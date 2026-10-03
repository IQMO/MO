"""Deterministic MO session closeout.

This is MO's built-in session closeout: a local truth refresh at real session
boundaries, not a public skill or another agent. It records clean/unresolved
state, continuity warnings, token economics, result-cap savings, taskboard
evidence, workers/goals, and workspace dirtiness as orientation only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..utils.atomic_write import atomic_write_text
from ..runtime.backend_monitor import redact_monitor_text
from ..runtime.subprocess_flags import apply_windows_hidden_process_flags
from ..context.coordination_state import goal_summary_lines, worker_summary_lines
from .handoff import context_pressure
from ..utils.number_utils import as_non_negative_int as _as_int
from ..state.paths import SESSION_CLOSEOUT_DIR, resolve_state_path
from ..tasking.task_board import read_recent_snapshots
from ..tasking.task_board_context import (
    compile_board_context,
    compile_board_context_from_snapshot,
    task_row_blocker,
    task_row_counts,
    task_row_evidence,
    task_row_status,
    task_row_title,
)

DEFAULT_MAX_CLOSEOUTS = 50


@dataclass(frozen=True)
class SessionCloseout:
    reason: str
    session_id: str
    slot: str
    turn_count: int
    message_count: int
    total_tokens: int
    input_tokens: int
    output_tokens: int
    result_cap_ops: int = 0
    result_cap_saved_chars: int = 0
    result_cap_saved_tokens_est: int = 0
    result_cap_last_pct: int = 0
    pressure: float = 0.0
    task_total: int = 0
    task_completed: int = 0
    task_open: int = 0
    task_blocked: int = 0
    unresolved: tuple[str, ...] = ()
    continuity_warnings: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    dirty_files: tuple[str, ...] = ()
    scratch_files: tuple[str, ...] = ()
    worker_state: tuple[str, ...] = ()
    goal_state: tuple[str, ...] = ()
    prt_summary: tuple[str, ...] = ()
    learning_delta: tuple[str, ...] = ()
    session_spine: tuple[str, ...] = ()
    clean: bool = True
    created_at: float = field(default_factory=time.time)

    def as_meta(self) -> dict[str, Any]:
        data = asdict(self)
        data["unresolved_count"] = len(self.unresolved)
        data["continuity_warning_count"] = len(self.continuity_warnings)
        data["dirty_count"] = len(self.dirty_files)
        data["scratch_count"] = len(self.scratch_files)
        return data


def build_session_closeout(agent: Any, *, reason: str = "session boundary") -> SessionCloseout:
    """Build a local evidence refresh from current runtime state."""
    session = getattr(agent, "session", None)
    messages = list(getattr(session, "messages", []) or [])
    token_log = [entry for entry in list(getattr(session, "token_log", []) or []) if isinstance(entry, dict)]
    input_tokens = sum(_as_int(entry.get("input_tokens", 0)) for entry in token_log)
    output_tokens = sum(_as_int(entry.get("output_tokens", 0)) for entry in token_log) or _as_int(getattr(session, "output_tokens", 0))
    total_tokens = sum(_as_int(entry.get("total_tokens", 0)) for entry in token_log) or _as_int(getattr(session, "total_tokens", 0)) or input_tokens + output_tokens

    pressure = _pressure(agent)
    task = _session_taskboard_state(agent)
    workers = _worker_state(agent)
    goal = _goal_state(agent)
    learning_delta = _learning_delta(agent)
    project_root = getattr(agent, "project_cwd", None)
    dirty = tuple(_git_dirty_lines(project_root))
    touched_dirty, unattributed_dirty, scratch = _session_workspace_state(
        agent, session, project_root, dirty, messages
    )
    unresolved = list(task["unresolved"])
    unresolved.extend(f"active/recent worker: {item}" for item in workers if _worker_line_open(item))
    unresolved.extend(f"goal: {item}" for item in goal if _goal_line_open(item))
    unresolved.extend(f"task scratch remains: {path}" for path in scratch)
    continuity_warnings: list[str] = []
    if touched_dirty:
        # A successful path-level edit does not attribute the remaining diff:
        # session changes may already be committed while foreign hunks remain.
        continuity_warnings.append(
            f"workspace has {len(touched_dirty)} uncommitted file(s) touched by this session; "
            "ownership of remaining changes is unproven"
        )
    if unattributed_dirty:
        continuity_warnings.append(
            f"workspace has {len(unattributed_dirty)} uncommitted file(s) not attributed to this session"
        )
    trimmed = _as_int(pressure.get("trimmed_messages_count", 0))
    if trimmed:
        continuity_warnings.append(
            f"{trimmed} older message(s) were trimmed before closeout; retained history may be incomplete"
        )

    prt_info = []
    registry = getattr(agent, "workers", None)
    if registry and hasattr(registry, "get_all"):
        for w in registry.get_all():
            if w.kind == "prt" and w.state == "completed":
                prt_info.append(f"PRT {w.id}: {w.result_summary}")

    # Session spine: conversation content for "where were we?" continuity.
    # Prefer the handoff document (if one was generated) — it already has
    # a cleanly formatted recent-dialogue section. Fall back to the last
    # few user/assistant messages from the live session.
    session_spine = _extract_session_spine(agent, session, messages)

    if callable(getattr(agent, "_tool_context_saved_chars", None)):
        saved_chars = _as_int(agent._tool_context_saved_chars())
    else:
        saved_chars = _as_int(getattr(agent, "result_cap_total_saved", 0))
    context_saving_ops = _as_int(getattr(agent, "result_cap_total_ops", 0))
    closeout = SessionCloseout(
        reason=redact_monitor_text(reason or "session boundary", 160),
        session_id=redact_monitor_text(str(getattr(session, "session_id", "") or ""), 120),
        slot=redact_monitor_text(str(getattr(getattr(agent, "_sessions", None), "current_name", "main") or "main"), 80),
        turn_count=_as_int(getattr(session, "turn_count", 0)),
        message_count=len(messages),
        total_tokens=total_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        result_cap_ops=context_saving_ops,
        result_cap_saved_chars=saved_chars,
        result_cap_saved_tokens_est=max(0, round(saved_chars / 4)),
        result_cap_last_pct=_as_int(getattr(agent, "result_cap_last_pct", 0)),
        pressure=float(pressure.get("pressure", 0.0) or 0.0),
        task_total=task["total"],
        task_completed=task["completed"],
        task_open=task["open"],
        task_blocked=task["blocked"],
        unresolved=tuple(redact_monitor_text(item, 240) for item in unresolved if str(item or "").strip()),
        continuity_warnings=tuple(
            redact_monitor_text(item, 240)
            for item in continuity_warnings
            if str(item or "").strip()
        ),
        evidence=tuple(task["evidence"][:12]),
        dirty_files=tuple(redact_monitor_text(item, 240) for item in touched_dirty[:30]),
        scratch_files=tuple(redact_monitor_text(item, 240) for item in scratch[:30]),
        worker_state=tuple(redact_monitor_text(item, 240) for item in workers[:8]),
        goal_state=tuple(redact_monitor_text(item, 240) for item in goal[:6]),
        prt_summary=tuple(redact_monitor_text(item, 240) for item in prt_info[:5]),
        learning_delta=tuple(redact_monitor_text(item, 240) for item in learning_delta[:8]),
        session_spine=tuple(redact_monitor_text(item, 320) for item in session_spine[:12]),
        clean=not unresolved,
    )
    _write_file_operations(agent, session)
    _expire_runtime_learning_suggestions(agent)
    return closeout


def render_session_closeout(closeout: SessionCloseout, *, path: str = "") -> str:
    """Render a compact report/status block."""
    lines = [
        "Session closeout:",
        f"  truth:   {'clean' if closeout.clean else 'unresolved'} ({len(closeout.unresolved)} unresolved)",
        f"  session: {closeout.turn_count} turns · {closeout.message_count} messages · slot {closeout.slot}",
        f"  tokens:  {closeout.total_tokens:,} total · in {closeout.input_tokens:,} / out {closeout.output_tokens:,}",
    ]
    if closeout.result_cap_ops:
        lines.append(f"  saved:   ~{closeout.result_cap_saved_tokens_est:,} tokens / {closeout.result_cap_saved_chars:,} chars via explicit result caps ({closeout.result_cap_ops} ops)")
    lines.append(f"  context: {closeout.pressure:.0%} pressure")
    if closeout.task_total:
        lines.append(f"  board:   {closeout.task_completed}/{closeout.task_total} done · open {closeout.task_open} · blocked {closeout.task_blocked}")
    if closeout.continuity_warnings:
        lines.append(f"  history: {len(closeout.continuity_warnings)} continuity warning(s)")
    if closeout.scratch_files:
        lines.append(f"  scratch: {len(closeout.scratch_files)} task-owned file(s) remain")
    if closeout.dirty_files:
        lines.append(f"  git:     {len(closeout.dirty_files)} dirty file(s)")
    if closeout.learning_delta:
        lines.append(f"  learned: {len(closeout.learning_delta)} item(s)")
    if path:
        lines.append(f"  saved:   {path}")
    return "\n".join(lines)


def render_session_closeout_markdown(closeout: SessionCloseout) -> str:
    """Render durable Markdown orientation."""
    created = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(closeout.created_at))
    lines = [
        "# MO Session Closeout",
        "",
        f"Created: {created}",
        f"Reason: {closeout.reason}",
        f"Session: `{closeout.session_id}` / slot `{closeout.slot}`",
        "",
        "## Truth",
        f"- Status: {'clean' if closeout.clean else 'unresolved'}",
        f"- Turns/messages: {closeout.turn_count}/{closeout.message_count}",
        f"- Context pressure: {closeout.pressure:.0%}",
        f"- Tokens: {closeout.total_tokens:,} total; input {closeout.input_tokens:,}; output {closeout.output_tokens:,}",
    ]
    if closeout.result_cap_ops:
        lines.append(f"- Tool context saved: ~{closeout.result_cap_saved_tokens_est:,} tokens / {closeout.result_cap_saved_chars:,} chars via explicit result caps ({closeout.result_cap_ops} ops, {closeout.result_cap_last_pct}% last cap)")
    lines.extend(["", "## Taskboard"])
    lines.append(f"- {closeout.task_completed}/{closeout.task_total} completed; open {closeout.task_open}; blocked {closeout.task_blocked}" if closeout.task_total else "- No active taskboard captured.")
    if closeout.evidence:
        lines.append("- Evidence:")
        lines.extend(f"  - {item}" for item in closeout.evidence)
    lines.extend(["", "## Clean vs unresolved"])
    if closeout.unresolved:
        lines.append("UNRESOLVED:")
        lines.extend(f"- {item}" for item in closeout.unresolved)
    else:
        lines.extend(["CLEAN:", "- No open taskboard items, active workers/goals, or task-owned scratch detected.", "- Workspace warnings are not a delivery verdict; clean does not prove a clean Git tree or task completion."])
    if closeout.continuity_warnings:
        lines.extend(["", "## Continuity warnings"])
        lines.extend(f"- {item}" for item in closeout.continuity_warnings)
    if closeout.session_spine:
        lines.extend(["", "## Conversation topic / recent spine"])
        lines.append("- Spine (for session continuity):")
        lines.extend(f"  - {item}" for item in closeout.session_spine)
    lines.extend(["", "## Workspace / workers / goals"])
    if closeout.dirty_files:
        lines.append("- Git dirty lines:")
        lines.extend(f"  - `{item}`" for item in closeout.dirty_files)
    if closeout.worker_state:
        lines.append("- Workers:")
        lines.extend(f"  - {item}" for item in closeout.worker_state)
    if closeout.goal_state:
        lines.append("- Goal:")
        lines.extend(f"  - {item}" for item in closeout.goal_state)
    if closeout.prt_summary:
        lines.append("- PRT Reviews:")
        lines.extend(f"  - {item}" for item in closeout.prt_summary)
    if closeout.learning_delta:
        lines.extend(["", "## Learning delta"])
        lines.extend(f"- {item}" for item in closeout.learning_delta)
    if not closeout.dirty_files and not closeout.worker_state and not closeout.goal_state and not closeout.prt_summary:
        lines.append("- No dirty workspace/worker/goal/PRT state captured.")
    lines.extend([
        "",
        "## Rules for next session",
        "- This closeout is orientation, not proof.",
        "- Reuse matching original tool/test receipts for an unchanged candidate; a closeout summary alone is not verification.",
        "- Refresh only missing evidence or checks invalidated by source/state changes; a new session or commit alone does not require rerunning tests.",
        "- Gateway/taskboard evidence remains the completion source of truth.",
    ])
    return redact_monitor_text("\n".join(lines).strip() + "\n", 40_000)


def write_session_closeout(
    closeout: SessionCloseout,
    *,
    root: str | Path = SESSION_CLOSEOUT_DIR,
    keep: int = DEFAULT_MAX_CLOSEOUTS,
) -> Path:
    out_dir = Path(resolve_state_path(root, getattr(closeout, "config", None)))
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(closeout.created_at))
    path = _unique_closeout_path(out_dir, stamp, _safe_slug(closeout.session_id or 'session'))
    atomic_write_text(path, render_session_closeout_markdown(closeout), encoding="utf-8")
    atomic_write_text(
        path.with_suffix(".json"),
        json.dumps(_closeout_sidecar_meta(closeout, path), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    prune_session_closeouts(out_dir, keep=keep)
    return path


def prune_session_closeouts(root: str | Path = SESSION_CLOSEOUT_DIR, *, keep: int = DEFAULT_MAX_CLOSEOUTS) -> tuple[Path, ...]:
    """Delete oldest closeout Markdown files beyond the retention cap.

    The default resolves through the private state home; a bare relative
    default must never delete under the process working directory.
    """
    try:
        keep_count = max(1, int(keep or DEFAULT_MAX_CLOSEOUTS))
    except (TypeError, ValueError):
        keep_count = DEFAULT_MAX_CLOSEOUTS
    root_path = Path(resolve_state_path(root))
    if not root_path.exists():
        return ()
    files = [path for path in root_path.glob("*.md") if path.is_file()]
    files.sort(key=lambda path: (_mtime(path), path.name), reverse=True)
    removed: list[Path] = []
    for path in files[keep_count:]:
        try:
            path.unlink()
            removed.append(path)
            sidecar = path.with_suffix(".json")
            if sidecar.is_file():
                sidecar.unlink()
        except OSError:
            continue
    return tuple(removed)


def _closeout_sidecar_meta(closeout: SessionCloseout, path: str | Path = "") -> dict[str, Any]:
    meta = closeout.as_meta()
    if path:
        meta["path"] = str(path)
    return _redact_json_strings(meta)


def closeout_meta(closeout: SessionCloseout, path: str | Path = "") -> dict[str, Any]:
    meta = closeout.as_meta()
    if path:
        meta["path"] = str(path)
    meta["unresolved_preview"] = list(closeout.unresolved[:6])
    meta["continuity_warning_preview"] = list(closeout.continuity_warnings[:3])
    for key in ("unresolved", "continuity_warnings", "evidence", "dirty_files", "worker_state", "goal_state"):
        meta.pop(key, None)
    return meta


def _memory_root(profile: Any) -> Path:
    profile_path = getattr(profile, "_path", None)
    return Path(profile_path).parent if profile_path else Path("memory")


def _pressure(agent: Any) -> dict[str, Any]:
    try:
        return context_pressure(agent)
    except Exception:
        return {"pressure": 0.0, "trimmed_messages_count": 0}


def _write_file_operations(agent: Any, session: Any) -> None:
    try:
        from ..tooling.file_operations import write_file_ops
        from ..state.paths import resolve_state_path

        cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
        write_file_ops(
            session_id=str(getattr(session, "session_id", "") or ""),
            run_id=str(getattr(getattr(agent, "_goal_plan", None), "run_id", "") or ""),
            since_ts=float(getattr(session, "created_at", 0.0) or 0.0),
            provider=str(getattr(agent, "provider_name", "") or ""),
            model=str(getattr(agent, "model", "") or ""),
            turn_count=_as_int(getattr(session, "turn_count", 0)),
            path=resolve_state_path("logs/file_operations.jsonl", cfg),
            audit_path=resolve_state_path("logs/tool_audit.jsonl", cfg),
        )
    except Exception:
        return


def _session_workspace_state(
    agent: Any,
    session: Any,
    project_root: str | Path | None,
    dirty_lines: tuple[str, ...],
    messages: list[Any],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Find recorded path touches and scratch, not ownership of remaining diffs."""
    session_id = str(getattr(session, "session_id", "") or "")
    if not project_root or not session_id:
        return (), dirty_lines, ()
    try:
        from ..tooling.file_operations import _read_tool_audit_files
        from ..tooling.scratch import unrequested_live_scratch_paths

        cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
        audit_path = Path(resolve_state_path("logs/tool_audit.jsonl", cfg))
        if not audit_path.is_file():
            return (), dirty_lines, ()
        _read, modified = _read_tool_audit_files(
            float(getattr(session, "created_at", 0.0) or 0.0),
            audit_path=audit_path,
            session_id=session_id,
        )
        root = Path(project_root).expanduser().resolve(strict=False)
        touched_paths: set[str] = set()
        for value in modified:
            candidate = Path(str(value or "")).expanduser()
            try:
                resolved = candidate.resolve(strict=False) if candidate.is_absolute() else (root / candidate).resolve(strict=False)
                touched_paths.add(os.path.normcase(str(resolved.relative_to(root))))
            except (OSError, ValueError):
                continue
        touched = tuple(
            line for line in dirty_lines
            if os.path.normcase(str(Path(str(line).split(": ", 1)[-1]))) in touched_paths
        )
        user_text = "\n".join(
            str(message.get("content") or "")
            for message in messages
            if isinstance(message, dict) and message.get("role") == "user"
        )
        scratch = unrequested_live_scratch_paths(project_root, modified, user_text=user_text)
        return touched, tuple(line for line in dirty_lines if line not in touched), scratch
    except Exception:
        return (), dirty_lines, ()


def _expire_runtime_learning_suggestions(agent: Any) -> None:
    """Expire stale review suggestions at the session boundary, best-effort."""
    try:
        from ..learning.proactive_learning import expire_stale_suggestions

        profile = getattr(agent, "profile", None)
        out = _memory_root(profile) / "learning" / "suggestions.jsonl"
        expire_stale_suggestions(path=out)
    except Exception:
        return


def _learning_delta(agent: Any) -> list[str]:
    session = getattr(agent, "session", None)
    since = float(getattr(session, "created_at", 0.0) or 0.0)
    profile = getattr(agent, "profile", None)
    profile_path = getattr(profile, "_path", None)
    if not since or not profile_path:
        return []
    path = Path(profile_path).parent / "profile" / "learning.md"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        text = ""
    rows: list[str] = []
    for ts, body in re.findall(r"^## (\S+T\S+Z)\s+—\s+profile learning$(.*?)(?=^## |\Z)", text, re.M | re.S):
        try:
            learned_at = time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
        except Exception:
            continue
        if learned_at >= since:
            cats = re.findall(r"^- ([\w-]+):", body, re.M)
            rows.append(f"{ts}: {', '.join(cats) if cats else 'profile learning'}")
    try:
        from ..learning.operator_messages import learning_receipts_since
        for item in learning_receipts_since(profile, since, config=getattr(agent, "config", {}) or {}):
            if item not in rows:
                rows.append(item)
    except Exception:
        pass
    return rows


def _extract_session_spine(agent: Any, session: Any, messages: list[dict]) -> list[str]:
    """Extract the last ~12 user/assistant messages as a session spine.

    Prefers the handoff document's "Recent session spine" section when a
    handoff was generated during this session — it already has cleanly
    redacted and formatted dialogue lines.  Falls back to extracting from
    the live session messages.
    """
    handoff_path = getattr(agent, "last_handoff_path", "")
    if handoff_path:
        try:
            handoff_text = Path(handoff_path).read_text(encoding="utf-8", errors="replace")
        except Exception:
            handoff_text = ""
        if handoff_text:
            rows = _spine_from_handoff_document(handoff_text)
            if rows:
                return rows
    return _spine_from_session_messages(messages)


def _spine_from_handoff_document(handoff_text: str) -> list[str]:
    """Parse the "## Recent session spine" section from a handoff document."""
    in_spine = False
    rows: list[str] = []
    for line in handoff_text.splitlines():
        stripped = line.strip()
        if stripped == "## Recent session spine":
            in_spine = True
            continue
        if in_spine:
            if stripped.startswith("## ") or stripped.startswith("# "):
                break
            if stripped.startswith("- "):
                rows.append(stripped[2:].strip())
    return rows[:12]


def _spine_from_session_messages(messages: list[dict], *, limit: int = 12) -> list[str]:
    """Extract the last N user/assistant messages, skipping tool calls/results."""
    rows: list[str] = []
    for msg in reversed(messages or []):
        if len(rows) >= limit:
            break
        role = str(msg.get("role") or "").strip()
        if role == "tool" or msg.get("tool_calls"):
            continue
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        preview = content.replace("\n", " ")[:200]
        rows.append(f"{role}: {preview}")
    rows.reverse()
    return rows


def _session_taskboard_state(agent: Any) -> dict[str, Any]:
    gateway = getattr(agent, "gateway", None)
    board = getattr(gateway, "last_task_board", None) or getattr(agent, "_active_task_board", None)
    snapshot = None
    if not board:
        session = getattr(agent, "session", None)
        session_id = str(getattr(session, "session_id", "") or "")
        recent = read_recent_snapshots(limit=1, session_id=session_id) if session_id else []
        snapshot = recent[-1] if recent else None
    if not board and not snapshot:
        return {"total": 0, "completed": 0, "open": 0, "blocked": 0, "unresolved": [], "evidence": [], "context": ""}
    context = compile_board_context(board, max_tasks=10, max_evidence=4, max_chars=1800) if board else compile_board_context_from_snapshot(snapshot, max_tasks=10, max_evidence=4, max_chars=1800)
    tasks = list(getattr(board, "tasks", []) or []) if board else list(snapshot.get("tasks") or [])
    counts = task_row_counts(tasks)
    unresolved: list[str] = []
    evidence: list[str] = []
    for task in tasks:
        title = task_row_title(task)
        status = task_row_status(task)
        if status in {"pending", "active", "blocked"}:
            blocker = task_row_blocker(task)
            unresolved.append(f"task {status}: {title}" + (f" — {blocker}" if blocker else ""))
        for item in task_row_evidence(task)[:4]:
            clean = str(item or "").strip()
            if clean and clean not in evidence:
                evidence.append(clean[:240])
    return {
        "total": int(context.get("total", counts["total"]) or 0),
        "completed": int(context.get("completed", counts["completed"]) or 0),
        "open": int(context.get("open", counts["open"]) or 0),
        "blocked": counts["blocked"],
        "unresolved": unresolved,
        "evidence": evidence,
        "context": context.get("text", ""),
    }



def _worker_state(agent: Any) -> list[str]:
    return worker_summary_lines(agent, limit=8)


def _goal_state(agent: Any) -> list[str]:
    rows = goal_summary_lines(agent, limit=6)
    if getattr(agent, "_goal_active", False) and rows:
        return ["active goal running", *rows]
    return rows


def _git_dirty_lines(cwd: str | Path | None = None, *, include_untracked: bool = False) -> list[str]:
    """Return content-meaningful Git changes, ignoring metadata-only status noise."""
    try:
        workdir = Path(cwd).expanduser().resolve(strict=False) if cwd else Path(os.getcwd()).resolve(strict=False)

        def changed_paths(args: list[str]) -> list[str]:
            kwargs = {
                "cwd": str(workdir),
                "capture_output": True,
                "text": True,
                "encoding": "utf-8",
                "errors": "replace",
                "timeout": 1.5,
            }
            apply_windows_hidden_process_flags(kwargs)
            proc = subprocess.run(args, **kwargs)
            if proc.returncode != 0:
                raise RuntimeError("git change query failed")
            return [path for path in proc.stdout.split("\0") if path]

        tracked = changed_paths([
            "git", "diff", "--name-only", "-z", "--no-ext-diff",
            "--ignore-submodules=none", "--",
        ])
        tracked.extend(changed_paths([
            "git", "diff", "--cached", "--name-only", "-z", "--no-ext-diff",
            "--ignore-submodules=none", "--",
        ]))
        untracked = changed_paths([
            "git", "ls-files", "--others", "--exclude-standard", "-z",
        ]) if include_untracked else []
    except Exception:
        return []
    lines = [f"tracked: {path}" for path in dict.fromkeys(tracked)]
    lines.extend(f"untracked: {path}" for path in dict.fromkeys(untracked))
    return lines


def _worker_line_open(line: str) -> bool:
    text = str(line or "").lower()
    return any(marker in text for marker in ("accepted", "running", "offered")) and not any(marker in text for marker in ("completed", "blocked", "cancelled", "paused"))


def _goal_line_open(line: str) -> bool:
    text = str(line or "").lower()
    return "active" in text or "running" in text or "pending" in text


def _unique_closeout_path(out_dir: Path, stamp: str, safe_session: str) -> Path:
    base = out_dir / f"{stamp}-{safe_session}.md"
    if not base.exists():
        return base
    for idx in range(2, 100):
        candidate = out_dir / f"{stamp}-{safe_session}-{idx}.md"
        if not candidate.exists():
            return candidate
    return out_dir / f"{stamp}-{safe_session}-{os.getpid()}.md"


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _safe_slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(value or "")).strip("-.")[:80]
    return slug or "session"


def read_latest_closeout_meta(root: str | Path = SESSION_CLOSEOUT_DIR, max_age_hours: float = 720.0) -> dict[str, Any]:
    """Return normalized metadata from the latest fresh structured closeout."""
    root_path = Path(resolve_state_path(root))
    if not root_path.exists():
        return {}
    files = [path for path in root_path.glob("*.md") if path.is_file()]
    if not files:
        return {}
    files.sort(key=lambda path: (_mtime(path), path.name), reverse=True)
    latest = files[0]
    age_hours = (time.time() - _mtime(latest)) / 3600.0
    if age_hours > max_age_hours:
        return {}

    meta = _read_closeout_sidecar(latest)
    if not meta:
        return {}
    return _normalize_closeout_meta(meta, latest, age_hours=age_hours)


def _read_closeout_sidecar(markdown_path: Path) -> dict[str, Any]:
    sidecar = markdown_path.with_suffix(".json")
    if not sidecar.is_file():
        return {}
    try:
        value = json.loads(sidecar.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _normalize_closeout_meta(meta: dict[str, Any], path: Path, *, age_hours: float) -> dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    data = dict(meta)
    data["path"] = str(path)
    data["age_hours"] = round(age_hours, 2)
    data["reason"] = str(data.get("reason") or "")
    data["turn_count"] = _as_int(data.get("turn_count"))
    data["message_count"] = _as_int(data.get("message_count"))
    if "clean" in data:
        clean = bool(data.get("clean"))
    else:
        status_text = str(data.get("status") or "").lower()
        clean = "clean" in status_text and "unresolved" not in status_text
    data["clean"] = clean
    data["status"] = "clean" if clean else "unresolved"
    unresolved = _string_list(data.get("unresolved"))
    unresolved_preview = _string_list(data.get("unresolved_preview")) or unresolved[:6]
    data["unresolved"] = unresolved
    data["unresolved_preview"] = unresolved_preview
    data["unresolved_count"] = _as_int(data.get("unresolved_count")) or len(unresolved)
    continuity_warnings = _string_list(data.get("continuity_warnings"))
    continuity_warning_preview = (
        _string_list(data.get("continuity_warning_preview"))
        or continuity_warnings[:3]
    )
    data["continuity_warnings"] = continuity_warnings
    data["continuity_warning_preview"] = continuity_warning_preview
    data["continuity_warning_count"] = (
        _as_int(data.get("continuity_warning_count")) or len(continuity_warnings)
    )
    dirty_files = _string_list(data.get("dirty_files"))
    data["dirty_files"] = dirty_files
    data["dirty_count"] = _as_int(data.get("dirty_count")) or len(dirty_files)
    spine = _string_list(data.get("session_spine"))
    data["session_spine"] = spine
    data["topic"] = str(data.get("topic") or _topic_from_spine(spine))
    data["terminal_marker"] = str(data.get("terminal_marker") or _terminal_marker_from_spine(spine))
    return data


_TOPIC_MIN_CHARS = 24


def _topic_index_from_spine(spine: list[str]) -> int:
    """Index of the user line that best identifies what the session was about.

    Taking the last user line can label a whole session by an incidental final
    aside, which the next session then quotes back as its topic. Prefer the
    longest substantive user line and keep the last one as fallback so a
    session of only short replies still gets a topic.
    """
    best_idx = -1
    best_len = 0
    last_idx = -1
    for idx, entry in enumerate(spine):
        if not entry.lower().startswith("user:"):
            continue
        last_idx = idx
        length = len(entry[5:].strip())
        if length >= _TOPIC_MIN_CHARS and length > best_len:
            best_idx, best_len = idx, length
    return best_idx if best_idx >= 0 else last_idx


def _topic_from_spine(spine: list[str]) -> str:
    topic_idx = _topic_index_from_spine(spine)
    if topic_idx < 0:
        return ""
    return spine[topic_idx][5:].strip()


def topic_from_messages(messages: Any) -> str:
    """Return the session subject from raw conversation messages.

    A forced terminal close may not run ``atexit`` or write a closeout.
    Continuity can recover a subject from the last successfully saved
    conversation snapshot, which may predate the interruption. Shares
    ``_topic_index_from_spine`` so a recovered topic and a recorded one are
    chosen by one rule.
    """
    spine: list[str] = []
    for message in list(messages or []):
        if not isinstance(message, dict) or str(message.get("role") or "") != "user":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        text = " ".join(content.split())
        if text:
            spine.append(f"user: {text}")
    return _topic_from_spine(spine)


def _terminal_marker_from_spine(spine: list[str]) -> str:
    for entry in spine:
        marker_match = re.search(r"\[([A-Z_]+ (?:COMPLETE|BLOCKED))\]", entry)
        if marker_match:
            return marker_match.group(1)
    return ""


def _string_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _redact_json_strings(value: Any) -> Any:
    if isinstance(value, str):
        return redact_monitor_text(value, 40_000)
    if isinstance(value, dict):
        return {str(key): _redact_json_strings(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_json_strings(item) for item in value]
    return value
