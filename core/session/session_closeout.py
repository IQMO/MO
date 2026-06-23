"""Deterministic MO session closeout.

This is MO's built-in SFF shape: a local truth refresh at real session
boundaries, not a public skill or another agent. It records clean/unresolved
state, token economics, compression savings, taskboard evidence, workers/goals,
and workspace dirtiness as orientation only.
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

from ..atomic_write import atomic_write_text
from ..backend_monitor import redact_monitor_text
from ..coordination_state import goal_summary_lines, worker_summary_lines
from .handoff import context_pressure
from ..number_utils import as_non_negative_int as _as_int
from ..path_defaults import resolve_state_path
from ..tasking.task_board import read_recent_snapshots
from ..tasking.task_board_context import compile_board_context, compile_board_context_from_snapshot

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
    compression_ops: int = 0
    compression_saved_chars: int = 0
    compression_saved_tokens_est: int = 0
    compression_last_pct: int = 0
    pressure: float = 0.0
    task_total: int = 0
    task_completed: int = 0
    task_open: int = 0
    task_blocked: int = 0
    unresolved: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    dirty_files: tuple[str, ...] = ()
    worker_state: tuple[str, ...] = ()
    goal_state: tuple[str, ...] = ()
    prt_summary: tuple[str, ...] = ()
    learning_delta: tuple[str, ...] = ()
    clean: bool = True
    created_at: float = field(default_factory=time.time)

    def as_meta(self) -> dict[str, Any]:
        data = asdict(self)
        data["unresolved_count"] = len(self.unresolved)
        data["dirty_count"] = len(self.dirty_files)
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
    task = _taskboard_state(agent)
    workers = _worker_state(agent)
    goal = _goal_state(agent)
    learning_delta = _learning_delta(agent)
    dirty = tuple(_git_dirty_lines(getattr(agent, "project_cwd", None)))
    unresolved = list(task["unresolved"])
    unresolved.extend(f"active/recent worker: {item}" for item in workers if _worker_line_open(item))
    unresolved.extend(f"goal: {item}" for item in goal if _goal_line_open(item))
    if dirty:
        unresolved.append(f"workspace has {len(dirty)} uncommitted file(s)")
    trimmed = _as_int(pressure.get("trimmed_messages_count", 0))
    if trimmed:
        unresolved.append(f"{trimmed} older message(s) were trimmed before closeout")
        
    prt_info = []
    registry = getattr(agent, "workers", None)
    if registry and hasattr(registry, "get_all"):
        for w in registry.get_all():
            if w.kind == "prt" and w.state == "completed":
                prt_info.append(f"PRT {w.id}: {w.result_summary}")

    if callable(getattr(agent, "_tool_context_saved_chars", None)):
        saved_chars = _as_int(agent._tool_context_saved_chars())
    else:
        saved_chars = _as_int(getattr(agent, "compression_total_saved", 0)) + _as_int(getattr(agent, "truncation_total_saved", 0))
    context_saving_ops = _as_int(getattr(agent, "compression_total_ops", 0)) + _as_int(getattr(agent, "truncation_total_ops", 0))
    closeout = SessionCloseout(
        reason=redact_monitor_text(reason or "session boundary", 160),
        session_id=redact_monitor_text(str(getattr(session, "session_id", "") or ""), 120),
        slot=redact_monitor_text(str(getattr(getattr(agent, "_sessions", None), "current_name", "main") or "main"), 80),
        turn_count=_as_int(getattr(session, "turn_count", 0)),
        message_count=len(messages),
        total_tokens=total_tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        compression_ops=context_saving_ops,
        compression_saved_chars=saved_chars,
        compression_saved_tokens_est=max(0, round(saved_chars / 4)),
        compression_last_pct=_as_int(getattr(agent, "compression_last_pct", 0)),
        pressure=float(pressure.get("pressure", 0.0) or 0.0),
        task_total=task["total"],
        task_completed=task["completed"],
        task_open=task["open"],
        task_blocked=task["blocked"],
        unresolved=tuple(redact_monitor_text(item, 240) for item in unresolved if str(item or "").strip()),
        evidence=tuple(task["evidence"][:12]),
        dirty_files=tuple(redact_monitor_text(item, 240) for item in dirty[:30]),
        worker_state=tuple(redact_monitor_text(item, 240) for item in workers[:8]),
        goal_state=tuple(redact_monitor_text(item, 240) for item in goal[:6]),
        prt_summary=tuple(redact_monitor_text(item, 240) for item in prt_info[:5]),
        learning_delta=tuple(redact_monitor_text(item, 240) for item in learning_delta[:8]),
        clean=not unresolved,
    )
    _write_file_operations(agent, session)
    _write_runtime_closeout_learning(agent, session, closeout)
    return closeout


def render_session_closeout(closeout: SessionCloseout, *, path: str = "") -> str:
    """Render a compact report/status block."""
    lines = [
        "Session closeout:",
        f"  truth:   {'clean' if closeout.clean else 'unresolved'} ({len(closeout.unresolved)} unresolved)",
        f"  session: {closeout.turn_count} turns · {closeout.message_count} messages · slot {closeout.slot}",
        f"  tokens:  {closeout.total_tokens:,} total · in {closeout.input_tokens:,} / out {closeout.output_tokens:,}",
    ]
    if closeout.compression_ops:
        lines.append(f"  saved:   ~{closeout.compression_saved_tokens_est:,} tokens / {closeout.compression_saved_chars:,} chars via tool compression/truncation ({closeout.compression_ops} ops)")
    lines.append(f"  context: {closeout.pressure:.0%} pressure")
    if closeout.task_total:
        lines.append(f"  board:   {closeout.task_completed}/{closeout.task_total} done · open {closeout.task_open} · blocked {closeout.task_blocked}")
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
    if closeout.compression_ops:
        lines.append(f"- Tool context saved: ~{closeout.compression_saved_tokens_est:,} tokens / {closeout.compression_saved_chars:,} chars via compression/truncation ({closeout.compression_ops} ops, {closeout.compression_last_pct}% last compression)")
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
        lines.extend(["CLEAN:", "- No open taskboard items, active workers/goals, dirty workspace lines, or trim-loss warnings detected."])
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
        "- Re-read files and re-run verification before factual claims.",
        "- Gateway/taskboard evidence remains the completion source of truth.",
    ])
    return redact_monitor_text("\n".join(lines).strip() + "\n", 40_000)


def write_session_closeout(
    closeout: SessionCloseout,
    *,
    root: str | Path = "memory/session_closeouts",
    keep: int = DEFAULT_MAX_CLOSEOUTS,
) -> Path:
    out_dir = Path(resolve_state_path(root, getattr(closeout, "config", None)))
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(closeout.created_at))
    path = _unique_closeout_path(out_dir, stamp, _safe_slug(closeout.session_id or 'session'))
    atomic_write_text(path, render_session_closeout_markdown(closeout), encoding="utf-8")
    prune_session_closeouts(out_dir, keep=keep)
    return path


def stage_session_closeout_feedback(
    profile: Any,
    closeout: SessionCloseout,
    *,
    closeout_path: str | Path = "",
) -> dict[str, Any]:
    """Stage inert workflow candidates for repeated closeout patterns only.

    Single-session unresolved state is noisy and remains archival. If the same
    closeout pattern appears in two consecutive closeouts, stage a candidate in
    the existing workflow-candidate pipeline; explicit operator approval is
    still required before it affects future turns.
    """
    patterns = _closeout_pattern_keys(closeout)
    memory_root = _memory_root(profile)
    observations_path = memory_root / "closeout_patterns.jsonl"
    previous = _last_closeout_observation(observations_path)
    repeated = sorted(set(patterns) & set(previous.get("patterns") or [])) if previous else []
    observation = {
        "session_id": closeout.session_id,
        "created_at": closeout.created_at,
        "patterns": patterns,
        "closeout_path": str(closeout_path or ""),
        "unresolved_count": len(closeout.unresolved),
        "dirty_count": len(closeout.dirty_files),
    }
    _append_jsonl(observations_path, observation)

    if not repeated or not profile:
        return {"staged": False, "patterns": patterns, "repeated": repeated, "reason": "no repeated closeout pattern"}

    staged: list[dict[str, Any]] = []
    try:
        from ..learning.workflow_learning import stage_workflow_source_candidate
    except Exception:
        return {"staged": False, "patterns": patterns, "repeated": repeated, "reason": "workflow learning unavailable"}

    for pattern in repeated:
        source = _closeout_candidate_source(pattern, closeout)
        result = stage_workflow_source_candidate(
            profile,
            source,
            source_label=f"session closeout repeated pattern: {pattern}",
            source_kind="session-closeout",
            request_text="stage repeated session closeout workflow learning",
        )
        if result.get("staged"):
            staged.append({"pattern": pattern, "id": result.get("id", ""), "duplicate": bool(result.get("duplicate"))})
    return {
        "staged": bool(staged),
        "patterns": patterns,
        "repeated": repeated,
        "candidates": staged,
        "path": str(memory_root / "workflow_candidates.jsonl"),
    }


def prune_session_closeouts(root: str | Path = "memory/session_closeouts", *, keep: int = DEFAULT_MAX_CLOSEOUTS) -> tuple[Path, ...]:
    """Delete oldest closeout Markdown files beyond the retention cap."""
    try:
        keep_count = max(1, int(keep or DEFAULT_MAX_CLOSEOUTS))
    except (TypeError, ValueError):
        keep_count = DEFAULT_MAX_CLOSEOUTS
    root_path = Path(root)
    if not root_path.exists():
        return ()
    files = [path for path in root_path.glob("*.md") if path.is_file()]
    files.sort(key=lambda path: (_mtime(path), path.name), reverse=True)
    removed: list[Path] = []
    for path in files[keep_count:]:
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            continue
    return tuple(removed)


def closeout_meta(closeout: SessionCloseout, path: str | Path = "") -> dict[str, Any]:
    meta = closeout.as_meta()
    if path:
        meta["path"] = str(path)
    meta["unresolved_preview"] = list(closeout.unresolved[:6])
    for key in ("unresolved", "evidence", "dirty_files", "worker_state", "goal_state"):
        meta.pop(key, None)
    return meta


def _closeout_pattern_keys(closeout: SessionCloseout) -> list[str]:
    patterns: list[str] = []
    unresolved_text = "\n".join(closeout.unresolved).lower()
    if closeout.dirty_files or "workspace has" in unresolved_text:
        patterns.append("dirty_workspace")
    if closeout.task_open or any("task " in item.lower() for item in closeout.unresolved):
        patterns.append("open_taskboard")
    if any("worker" in item.lower() for item in closeout.unresolved) or any(_worker_line_open(item) for item in closeout.worker_state):
        patterns.append("active_worker")
    if any("goal" in item.lower() for item in closeout.unresolved) or any(_goal_line_open(item) for item in closeout.goal_state):
        patterns.append("active_goal")
    if "trimmed" in unresolved_text:
        patterns.append("context_trimmed")
    return sorted(dict.fromkeys(patterns))


def _closeout_candidate_source(pattern: str, closeout: SessionCloseout) -> str:
    guidance = {
        "dirty_workspace": "Before ending or switching sessions, check git status and either commit, stash, or explicitly carry forward dirty workspace state.",
        "open_taskboard": "Before ending or switching sessions, keep open taskboard rows visible and report the next required action instead of implying completion.",
        "active_worker": "Before ending or switching sessions, report active workers and avoid starting conflicting follow-up work until their state is clear.",
        "active_goal": "Before ending or switching sessions, pause or summarize active goals with blocker/next-step context instead of treating them as done.",
        "context_trimmed": "Before ending or switching sessions after context trimming, write a handoff/closeout summary and re-verify facts next session.",
    }
    evidence = "; ".join(str(item) for item in closeout.unresolved[:4]) or f"pattern={pattern}"
    behavior = guidance.get(pattern, "Before ending or switching sessions, preserve unresolved state and re-verify before future claims.")
    return (
        "Repeated session closeout workflow candidate.\n"
        f"Pattern: {pattern}\n"
        f"Evidence: {evidence}\n"
        f"Do: {behavior}\n"
        "Avoid: do not treat closeout notes as proof and do not auto-promote this workflow without explicit operator approval."
    )


def _memory_root(profile: Any) -> Path:
    profile_path = getattr(profile, "_path", None)
    return Path(profile_path).parent if profile_path else Path("memory")


def _last_closeout_observation(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            return {}
        value = json.loads(lines[-1])
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass


def _pressure(agent: Any) -> dict[str, Any]:
    try:
        return context_pressure(agent)
    except Exception:
        return {"pressure": 0.0, "trimmed_messages_count": 0}


def _write_file_operations(agent: Any, session: Any) -> None:
    try:
        from ..file_operations import write_file_ops
        from ..path_defaults import resolve_state_path

        cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
        write_file_ops(
            session_id=str(getattr(session, "session_id", "") or ""),
            run_id=str(getattr(getattr(agent, "_goal_plan", None), "run_id", "") or ""),
            since_ts=float(getattr(session, "created_at", 0.0) or 0.0),
            provider=str(getattr(agent, "provider_name", "") or ""),
            model=str(getattr(agent, "model", "") or ""),
            turn_count=_as_int(getattr(session, "turn_count", 0)),
            path=resolve_state_path("memory/file_operations.jsonl", cfg),
            audit_path=resolve_state_path("logs/tool_audit.jsonl", cfg),
        )
    except Exception:
        return


def _write_runtime_closeout_learning(agent: Any, session: Any, closeout: SessionCloseout) -> None:
    """Analyze normal closeout metadata into inert suggestions, best-effort."""
    try:
        from ..learning.proactive_learning import write_learning_suggestions
        from ..learning.trace_learning import analyze_runtime_closeout

        cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
        audit_deltas = _audit_deltas_since(float(getattr(session, "created_at", 0.0) or 0.0), config=cfg)
        suggestions = analyze_runtime_closeout(closeout_meta(closeout), audit_deltas=audit_deltas)
        if not suggestions:
            return
        profile = getattr(agent, "profile", None)
        out = _memory_root(profile) / "learning_suggestions.jsonl"
        write_learning_suggestions(suggestions, path=out)
    except Exception:
        return


def _audit_deltas_since(since_ts: float, *, max_entries: int = 80, config: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    try:
        from ..path_defaults import resolve_state_path
        tool_path = Path(resolve_state_path("logs/tool_audit.jsonl", config or {}))
        provider_path = Path(resolve_state_path("logs/provider_audit.jsonl", config or {}))
    except Exception:
        tool_path = Path("logs/tool_audit.jsonl")
        provider_path = Path("logs/provider_audit.jsonl")
    return {
        "tool": _read_jsonl_since(tool_path, since_ts, max_entries=max_entries),
        "provider": _read_jsonl_since(provider_path, since_ts, max_entries=max_entries),
    }


def _read_jsonl_since(path: Path, since_ts: float, *, max_entries: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in reversed(path.read_text(encoding="utf-8", errors="replace").splitlines()):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            try:
                ts = float(row.get("ts") or 0.0)
            except (TypeError, ValueError):
                ts = 0.0
            if since_ts and ts < since_ts:
                break
            rows.append(row)
            if len(rows) >= max(1, int(max_entries or 80)):
                break
    except Exception:
        return []
    rows.reverse()
    return rows


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
        return []
    rows: list[str] = []
    for ts, body in re.findall(r"^## (\S+T\S+Z)\s+—\s+profile learning$(.*?)(?=^## |\Z)", text, re.M | re.S):
        try:
            learned_at = time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
        except Exception:
            continue
        if learned_at >= since:
            cats = re.findall(r"^- ([\w-]+):", body, re.M)
            rows.append(f"{ts}: {', '.join(cats) if cats else 'profile learning'}")
    return rows


def _taskboard_state(agent: Any) -> dict[str, Any]:
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
    unresolved: list[str] = []
    evidence: list[str] = []
    for task in tasks:
        title = str((getattr(task, "title", "") if board else task.get("title", "")) or "task").strip()
        status = str((getattr(task, "status", "") if board else task.get("status", "")) or "pending")
        if status in {"pending", "active", "blocked"}:
            blocker = str((getattr(task, "blocker", "") if board else task.get("blocker", "")) or "").strip()
            unresolved.append(f"task {status}: {title}" + (f" — {blocker}" if blocker else ""))
        evidence_items = getattr(task, "evidence", []) if board else task.get("evidence", [])
        for item in list(evidence_items or [])[:4]:
            clean = str(item or "").strip()
            if clean and clean not in evidence:
                evidence.append(clean[:240])
    return {
        "total": len(tasks),
        "completed": sum(1 for task in tasks if (getattr(task, "status", "") if board else task.get("status", "")) == "completed"),
        "open": sum(1 for task in tasks if (getattr(task, "status", "") if board else task.get("status", "")) in {"pending", "active", "blocked"}),
        "blocked": sum(1 for task in tasks if (getattr(task, "status", "") if board else task.get("status", "")) == "blocked"),
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
    try:
        workdir = Path(cwd).expanduser().resolve(strict=False) if cwd else Path(os.getcwd()).resolve(strict=False)
        proc = subprocess.run(["git", "status", "--short", "--branch"], cwd=str(workdir), capture_output=True, text=True, timeout=1.5)
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip() and not line.startswith("##")]
    if not include_untracked:
        # Untracked files ("??") are new artifacts, not incomplete work.
        # Only tracked modifications/deletions count as "dirty" for closeout.
        lines = [line for line in lines if not line.startswith("?")]
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
