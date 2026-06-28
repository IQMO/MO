"""MO Handsoff: deterministic context continuation without destructive compaction."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
import traceback

from ..utils.atomic_write import atomic_write_text
from ..runtime.backend_monitor import redact_monitor_text, tool_call_names
from ..graph.code_graph import build_code_graph_context, should_include_code_graph_context
from ..context.coordination_state import goal_summary_lines, worker_summary_lines
from ..provider.provider_audit import LOG_PATH as PROVIDER_AUDIT_LOG_PATH
from ..tasking.task_board import read_recent_snapshots
from ..tasking.task_board_context import compile_board_context, compile_board_context_from_snapshot
from ..worker import extract_worker_paths
from ..utils.text_utils import cap_by_tokens, token_aware_truncation_enabled

HANDOFF_HEADER = "MO HANDSOFF CONTEXT"
MAX_HANDOFF_DOC_CHARS = 60_000
HANDOFF_FILE_KEEP = 30


def session_chars(session: Any, *, extra_context: str = "") -> int:
    messages = list(getattr(session, "messages", []) or [])
    total = sum(len(json.dumps(m, default=str, ensure_ascii=False)) for m in messages)
    if extra_context:
        total += len(str(extra_context))
    return total


def context_pressure(agent: Any, *, extra_context: str = "") -> dict[str, Any]:
    session = getattr(agent, "session", None)
    budget_chars = None
    try:
        budget_chars = agent._provider_context_max_chars()
    except Exception:
        budget_chars = None
    chars = session_chars(session, extra_context=extra_context)
    message_count = len(getattr(session, "messages", []) or [])
    max_history = int(getattr(session, "max_history", 50) or 50)
    char_ratio = (chars / budget_chars) if budget_chars else 0.0
    message_ratio = (message_count / max_history) if max_history else 0.0
    raw_pressure = max(char_ratio, message_ratio)
    pressure_source = "messages" if message_ratio >= char_ratio else "chars"
    created_at = float(getattr(session, "created_at", 0.0) or 0.0)
    age_seconds = max(0, int(time.time() - created_at)) if created_at else 0
    return {
        "chars": chars,
        "budget_chars": budget_chars or 0,
        "char_ratio": char_ratio,
        "message_count": message_count,
        "max_history": max_history,
        "message_ratio": message_ratio,
        "raw_pressure": raw_pressure,
        "pressure": min(1.0, raw_pressure),
        "pressure_source": pressure_source,
        "over_limit": raw_pressure > 1.0,
        "turn_count": int(getattr(session, "turn_count", 0) or 0),
        "session_age_seconds": age_seconds,
        "trimmed_messages_count": int(getattr(session, "trimmed_messages_count", 0) or 0),
        "last_trimmed_at": float(getattr(session, "last_trimmed_at", 0.0) or 0.0),
        "compacted_messages_count": int(getattr(session, "compacted_messages_count", 0) or 0),
        "last_compacted_at": float(getattr(session, "last_compacted_at", 0.0) or 0.0),
    }


def should_auto_handoff(agent: Any, *, extra_context: str = "") -> tuple[bool, dict[str, Any]]:
    metrics = context_pressure(agent, extra_context=extra_context)
    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    agent_cfg = cfg.get("agent", {}) if isinstance(cfg.get("agent", {}), dict) else {}
    base_threshold = float(agent_cfg.get("context_handoff_threshold", getattr(agent, "context_handoff_threshold", 0.70)) or 0.70)
    base_threshold = min(0.95, max(0.25, base_threshold))
    char_threshold = float(agent_cfg.get("context_handoff_char_threshold", base_threshold) or base_threshold)
    msg_threshold = float(agent_cfg.get("context_handoff_msg_threshold", base_threshold) or base_threshold)
    char_threshold = min(0.95, max(0.25, char_threshold))
    msg_threshold = min(0.95, max(0.25, msg_threshold))
    trimmed_threshold = int(agent_cfg.get("context_handoff_trimmed_threshold", 1) or 1)
    min_messages = int(agent_cfg.get("context_handoff_min_messages", 8) or 8)
    min_chars = int(agent_cfg.get("context_handoff_min_chars", 16_000) or 16_000)

    compress_ops = int(getattr(agent, 'compression_total_ops', 0) or 0) + int(getattr(agent, 'context_momentum_compression_ops', 0) or 0)
    if compress_ops >= 5:
        avg_pct = (
            int(getattr(agent, 'compression_total_saved', 0) or 0)
            + int(getattr(agent, 'context_momentum_compression_saved', 0) or 0)
        ) / max(1, compress_ops)
        if avg_pct > 200:
            adaptive_boost = min(0.08, 0.04 + (compress_ops / 100))
            char_threshold = min(0.92, char_threshold + adaptive_boost)
            msg_threshold = min(0.92, msg_threshold + adaptive_boost)

    max_history = int(metrics.get("max_history") or 50)
    message_count = int(metrics.get("message_count") or 0)
    trimmed = int(metrics.get("trimmed_messages_count") or 0)
    last_trimmed_at = float(metrics.get("last_trimmed_at") or 0.0)
    last_compacted_at = float(metrics.get("last_compacted_at") or 0.0)
    trimmed_uncovered = trimmed >= trimmed_threshold and (last_compacted_at <= 0.0 or last_trimmed_at > last_compacted_at)
    enough_state = message_count >= min_messages or int(metrics.get("chars") or 0) >= min_chars
    triggered = enough_state and (
        float(metrics.get("char_ratio") or 0.0) >= char_threshold
        or float(metrics.get("message_ratio") or 0.0) >= msg_threshold
        or message_count >= max(1, max_history - 5)
        or trimmed_uncovered
    )
    metrics["threshold"] = max(char_threshold, msg_threshold)
    metrics["char_threshold"] = char_threshold
    metrics["message_threshold"] = msg_threshold
    metrics["triggered"] = triggered
    # Record which dimension crossed first so the handoff reason/capsule is honest
    # about token pressure vs message-count wall (they are conflated under `pressure`).
    char_ratio = float(metrics.get("char_ratio") or 0.0)
    message_ratio = float(metrics.get("message_ratio") or 0.0)
    if not triggered:
        trigger_dimension = ""
    elif trimmed_uncovered:
        trigger_dimension = "trimmed-history"
    elif char_ratio >= char_threshold and char_ratio >= message_ratio:
        trigger_dimension = "token-budget"
    elif message_count >= max(1, max_history - 5) or message_ratio >= msg_threshold:
        trigger_dimension = "message-count"
    else:
        trigger_dimension = "token-budget"
    metrics["trigger_dimension"] = trigger_dimension
    return triggered, metrics


def build_handoff_document(agent: Any, *, focus: str = "", reason: str = "", latest_user: str = "") -> str:
    session = getattr(agent, "session", None)
    messages = list(getattr(session, "messages", []) or [])
    session_id = str(getattr(session, "session_id", "") or "")
    provider = str(getattr(agent, "provider_name", "") or "")
    model = str(getattr(agent, "model", "") or "")
    budget = int(getattr(agent, "context_budget_tokens", 0) or 0)
    budget_source = str(getattr(agent, "context_budget_source", "") or "")
    metrics = context_pressure(agent)
    latest_user_text = str(latest_user or "").strip()
    session_latest_user = _latest_role(messages, "user")
    objective = _objective_text(focus, session_latest_user, latest_user_text)
    changed = _git_status_lines()
    workers = _worker_summary(agent)
    goal = _goal_summary(agent)
    task_board = _active_task_board(agent)
    task_board_rows = _task_board_summary(task_board, session_id=session_id)
    tool_entries = _recent_tool_audit(agent)
    provider_events = _recent_provider_audit()
    graph_context = _code_graph_context_for_handoff(latest_user_text or focus or session_latest_user)
    prt_summaries = _prt_summary(agent)
    artifacts = _artifact_references(
        agent,
        changed=changed,
        workers=workers,
        goal=goal,
        task_board=task_board,
        tool_entries=tool_entries,
        graph_context=graph_context,
    )
    recent = _recent_dialogue(messages, exclude_latest_user=latest_user_text)
    evidence = _evidence_ledger(task_board, tool_entries)
    attempts = _attempt_ledger(task_board, tool_entries, messages, metrics)
    unknowns = _unknowns(changed, task_board, tool_entries, provider_events, graph_context, metrics)
    decisions = _decision_rows(messages, task_board, goal)
    operator = str(getattr(getattr(agent, "profile", None), "user_name", "") or "operator").strip()
    current_name = str(getattr(getattr(agent, "_sessions", None), "current_name", "main") or "main")

    lines = [
        f"# {HANDOFF_HEADER}",
        "",
        f"Created: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Reason: {redact_monitor_text(reason or 'context pressure handoff', 180)}",
        "",
        "## Session continuity",
        f"- Source session: {redact_monitor_text(session_id, 120)} / slot `{redact_monitor_text(current_name, 80)}`",
        f"- Turns/messages: {metrics['turn_count']} turn(s), {metrics['message_count']}/{metrics['max_history']} message(s), pressure {float(metrics['pressure']):.0%}",
        f"- Session age: {_duration(metrics.get('session_age_seconds', 0))}; created {_format_ts(getattr(session, 'created_at', 0.0))}",
        f"- Provider/model at handoff: {redact_monitor_text(provider, 80)} / {redact_monitor_text(model, 120)}",
        f"- Context budget: {budget:,} tokens ({redact_monitor_text(budget_source, 80)})" if budget else "- Context budget: unknown",
        f"- Previous handoffs in this agent: {int(getattr(agent, '_handoff_count', 0) or 0)}",
    ]
    if getattr(agent, "last_handoff_path", ""):
        lines.append(f"- Previous handoff file: `{redact_monitor_text(getattr(agent, 'last_handoff_path', ''), 240)}`")
    if int(metrics.get("trimmed_messages_count") or 0) > 0:
        lines.append(f"- WARNING: {metrics['trimmed_messages_count']} older message(s) were already trimmed before this handoff; treat missing history as unknown.")
    if int(metrics.get("compacted_messages_count") or 0) > 0:
        lines.append(f"- Momentum compacted {metrics['compacted_messages_count']} older message(s) before this handoff; compacted context is orientation only, not proof.")

    lines.extend([
        "",
        "## Current objective",
        f"- {redact_monitor_text(objective, 700)}",
        *_uncommitted_work_lines(changed),
        "",
        "## Operating rules for the continuation",
        "- This handoff is orientation, not proof. Re-read files and re-run verification before factual claims.",
        f"- Preserve {redact_monitor_text(operator, 80)} workflow: exact completion, no fake done, tests/evidence before claims.",
        "- Prefer concise catch-up: use this capsule to choose the next action, not to retell the whole conversation.",
    ])
    # Context-saving note: compression and fallback truncation both keep tool
    # output from bloating provider history; neither is proof.
    compress_ops = int(getattr(agent, 'compression_total_ops', 0) or 0) + int(getattr(agent, 'context_momentum_compression_ops', 0) or 0)
    trunc_ops = int(getattr(agent, 'truncation_total_ops', 0) or 0) + int(getattr(agent, 'context_momentum_truncation_ops', 0) or 0)
    if compress_ops > 0 or trunc_ops > 0:
        if callable(getattr(agent, '_tool_context_saved_chars', None)):
            saved_chars = int(agent._tool_context_saved_chars() or 0)
        else:
            saved_chars = (
                int(getattr(agent, 'compression_total_saved', 0) or 0)
                + int(getattr(agent, 'truncation_total_saved', 0) or 0)
                + int(getattr(agent, 'context_momentum_compression_saved', 0) or 0)
                + int(getattr(agent, 'context_momentum_truncation_saved', 0) or 0)
            )
        lines.append(f"- Context-saving momentum kept tool-result context lean ({compress_ops} compressed, {trunc_ops} truncated, {saved_chars:,} chars kept out of provider history). Re-run tools for exact details when needed.")
    lines.extend([
        "",
        "## Taskboard state",
    ])
    lines.extend(task_board_rows or ["- No current taskboard captured."])

    lines.extend([
        "",
        "## Verified evidence ledger",
        "- Only tool/taskboard-backed entries are listed here; re-run critical checks before final claims.",
    ])
    lines.extend(evidence or ["- No recent tool-backed evidence captured."])

    lines.extend([
        "",
        "## Decisions / constraints currently known",
    ])
    lines.extend(decisions or ["- No durable decision ledger captured; treat conversation-only decisions as unverified until checked."])

    lines.extend([
        "",
        "## References and graph",
    ])
    if artifacts:
        lines.extend(f"- `{path}`" for path in artifacts[:32])
    else:
        lines.append("- No concrete file references detected beyond the current workspace/session.")
    if graph_context:
        lines.extend(["", "### Code graph slice", *_indent_block(graph_context, prefix="> ")])
    else:
        lines.append("- No code graph slice generated for this handoff.")
    file_ops = _file_operation_lines(limit=30)
    if file_ops:
        lines.extend(["", "## Recent file operations (across recent sessions)", *file_ops])

    lines.extend([
        "",
        "## Active workers / goals / workspace",
    ])
    if changed:
        lines.extend(["- Git/workspace signals:", *[f"  - `{redact_monitor_text(item, 240)}`" for item in changed[:30]]])
    if workers:
        lines.extend(["- Worker/route state:", *[f"  - {redact_monitor_text(item, 240)}" for item in workers]])
    if goal:
        lines.extend(["- Goal state:", *[f"  - {redact_monitor_text(item, 240)}" for item in goal]])
    if prt_summaries:
        lines.extend(["- PRT Reviews:", *[f"  - {item}" for item in prt_summaries]])
    if provider_events:
        lines.extend(["- Recent provider/runtime audit:", *[f"  - {event}" for event in provider_events]])
    if not changed and not workers and not goal and not provider_events and not prt_summaries:
        lines.append("- No active workspace/worker/goal/provider/PRT signals captured.")

    lines.extend([
        "",
        "## Already tried / avoid repeating",
    ])
    lines.extend(attempts or ["- No blocked/failed attempts captured in current taskboard or audit logs."])

    lines.extend([
        "",
        "## Recent session spine",
    ])
    lines.extend(recent or ["- No recent dialogue captured."])

    lines.extend([
        "",
        "## Unknowns / must re-check",
    ])
    lines.extend(unknowns)

    lines.extend([
        "",
        "## Next exact step",
        f"- {_next_exact_step(latest_user_text, artifacts, task_board)}",
        "",
        "## Suggested MO surfaces",
        "- Use `/goal` only when autonomous completion is intended and auditor evidence can pass.",
        "- Use Ghost only for fast side-check/planning; Ghost does not own completion.",
        "- Use auditor/verification as final principle gate for completion claims.",
        "",
        "## Do not assume",
        "- Do not assume tests passed unless current tool evidence says so.",
        "- Do not assume old taskboard/session state is complete unless verified.",
        "- Do not expose backend terms or raw tool/provider payloads to the user.",
    ])
    text = "\n".join(lines).strip() + "\n"
    if token_aware_truncation_enabled():
        redacted = redact_monitor_text(text, len(text) + 1000)
        return cap_by_tokens(redacted, MAX_HANDOFF_DOC_CHARS, "[handoff truncated]")
    return redact_monitor_text(text, MAX_HANDOFF_DOC_CHARS)


def _file_operation_lines(*, limit: int = 30) -> list[str]:
    try:
        from ..tooling.file_operations import accumulated_files
        recent = accumulated_files(limit=limit)
    except Exception:
        return []
    if not recent:
        return []
    lines = [f"- {len(recent)} unique files tracked recently (best-effort)."]
    modified = sorted(((p, i) for p, i in recent.items() if int(i.get("modifies", 0) or 0) > 0), key=lambda item: -int(item[1].get("modifies", 0) or 0))
    for path, info in modified[:10]:
        lines.append(f"- {redact_monitor_text(path, 140)} ({int(info.get('modifies', 0) or 0)} writes, {int(info.get('reads', 0) or 0)} reads)")
    return lines


def _file_operation_refs(*, limit: int = 20) -> tuple[list[str], list[str]]:
    try:
        from ..tooling.file_operations import accumulated_files
        recent = accumulated_files(limit=limit)
    except Exception:
        return [], []
    read_files = [path for path, info in sorted(recent.items(), key=lambda item: -int(item[1].get("reads", 0) or 0)) if int(info.get("reads", 0) or 0) > 0]
    modified_files = [path for path, info in sorted(recent.items(), key=lambda item: -int(item[1].get("modifies", 0) or 0)) if int(info.get("modifies", 0) or 0) > 0]
    return read_files, modified_files


def _compact_health_lines(agent: Any) -> list[str]:
    lines: list[str] = []
    try:
        from ..diagnostics.system_health import build_health_report
        structural = build_health_report().graph.get("structural", {})
        if isinstance(structural, dict) and structural.get("nodes"):
            lines.append(f"- Graph: {structural.get('nodes')} nodes, {structural.get('edges')} edges, {structural.get('communities')} communities")
            gods = structural.get("god_nodes") or []
            if gods:
                lines.append("- Hottest files: " + ", ".join(str(item.get("name") or "?") for item in gods[:3]))
    except Exception:
        traceback.print_exc()
    try:
        from .session_closeout import _learning_delta
        delta = _learning_delta(agent)
        if delta:
            lines.append("- Learned this session: " + "; ".join(delta[:3]))
    except Exception:
        traceback.print_exc()
    return lines


def build_compact_summary(agent: Any, *, focus: str = "", reason: str = "", latest_user: str = "") -> str:
    """Build a compact pi-style summary for session seeding."""
    session = getattr(agent, "session", None)
    messages = list(getattr(session, "messages", []) or [])
    latest_user_text = str(latest_user or _latest_role(messages, "user") or "").strip()
    objective = _objective_text(focus, _latest_role(messages, "user"), latest_user_text)
    changed = _git_status_lines()
    workers = _worker_summary(agent)
    goal = _goal_summary(agent)
    task_board = _active_task_board(agent)
    decisions = _decision_rows(messages, task_board, goal)
    metrics = context_pressure(agent) if session else {}
    lines = [
        f"# {HANDOFF_HEADER} (compact)",
        f"Created: {time.strftime('%Y-%m-%d %H:%M:%S')} | Reason: {redact_monitor_text(reason or 'context handoff', 120)}",
        "",
        "## Goal",
        f"- {redact_monitor_text(objective, 500)}",
        "",
        "## Progress",
    ]
    lines.extend(f"- {item}" for item in (goal[:6] or ["No active goal captured."]))
    if changed:
        lines.extend(["", "## Workspace", *[f"- `{redact_monitor_text(item, 220)}`" for item in changed[:10]]])
    if workers:
        lines.extend(["", "## Active Workers", *[f"- {redact_monitor_text(item, 220)}" for item in workers[:8]]])
    if decisions:
        lines.extend(["", "## Key Decisions", *[f"- {item}" for item in decisions[:6]]])
    lines.extend([
        "",
        "## Critical Context",
        f"- Session: {metrics.get('turn_count', '?')} turns, {metrics.get('message_count', '?')} messages, pressure {float(metrics.get('pressure', 0.0) or 0.0):.0%}",
        f"- Provider/model: {redact_monitor_text(getattr(agent, 'provider_name', ''), 80)} / {redact_monitor_text(getattr(agent, 'model', ''), 100)}",
    ])
    if getattr(agent, "context_budget_tokens", 0):
        lines.append(f"- Context budget: {int(getattr(agent, 'context_budget_tokens', 0) or 0):,} tokens")
    lines.extend(_compact_health_lines(agent))
    file_ops = _file_operation_lines(limit=20)
    if file_ops:
        lines.extend(["", "## Recent File Operations", *file_ops[:12]])
    lines.extend(["", "## Next Steps", f"- Continue: {redact_monitor_text(objective, 400)}", "- Re-read files and re-run verification before factual claims."])
    read_files, modified_files = _file_operation_refs(limit=20)
    if read_files:
        lines.extend(["", "<read-files>", *read_files[:12], "</read-files>"])
    if modified_files:
        lines.extend(["", "<modified-files>", *modified_files[:12], "</modified-files>"])
    return redact_monitor_text("\n".join(lines).strip() + "\n", 40_000)


def write_handoff_document(document: str, *, prefix: str = "mo-handoff") -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = Path(tempfile.gettempdir()) / f"{prefix}-{stamp}-{os.getpid()}.md"
    atomic_write_text(path, redact_monitor_text(document, 80_000), encoding="utf-8")
    _cleanup_old_handoff_documents(prefix=prefix)
    return path


def _cleanup_old_handoff_documents(*, prefix: str = "mo-handoff", keep: int = HANDOFF_FILE_KEEP) -> None:
    """Prune old temp handoff capsules while keeping recent continuity evidence."""
    try:
        parent = Path(tempfile.gettempdir())
        files = sorted(parent.glob(f"{prefix}-*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[max(1, int(keep or HANDOFF_FILE_KEEP)):]:
            try:
                old.unlink()
            except OSError:
                pass
    except Exception:
        return


def recent_visible_report_messages(messages: list[dict], *, max_chars: int = 3000, keep_recent: int = 6) -> list[dict]:
    """Return the latest visible assistant reports and user messages worth preserving across handoff."""
    blocked_prefixes = (
        "[raw tool payload blocked]",
        "[taskboard incomplete]",
        "[verify retry]",
        "[verify blocked]",
        "[answer held by critique:",
        "reply 'prd'",
        'reply "prd"',
    )
    kept: list[dict] = []
    for msg in reversed(messages or []):
        if len(kept) >= keep_recent:
            break
        role = str(msg.get("role") or "")
        if msg.get("tool_calls"):
            continue
        if role == "tool":
            continue
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        lower = content.lower()
        if lower.startswith(blocked_prefixes):
            continue
        kept.append({"role": role, "content": redact_monitor_text(content, max_chars)})
    kept.reverse()
    return kept


def seed_session_from_handoff(session: Any, document: str, *, latest_user: str = "", visible_messages: list[dict] | None = None, compact: bool = False) -> None:
    if compact:
        seed = str(document or "")
    else:
        seed = (
            f"[{HANDOFF_HEADER}]\n"
            "You are continuing from an automatic MO context handoff. Treat this as orientation only; verify files/tests before claims.\n\n"
            f"{document}"
        )
    kept_visible = []
    for msg in visible_messages or []:
        role = str(msg.get("role") or "").strip()
        content = str(msg.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            kept_visible.append({"role": role, "content": redact_monitor_text(content, 8_000)})
    session.clear()
    session.session_id = f"mo-handoff-{int(time.time())}"
    session.created_at = time.time()
    # Store handoff as internal context so it never appears as a user message.
    session._handoff_context = redact_monitor_text(seed, 60_000)
    # Keep the last 6 visible messages (3 exchanges) for continuity
    session.messages = kept_visible[-6:]
    if latest_user.strip():
        session.messages.append({"role": "user", "content": redact_monitor_text(latest_user.strip(), 8_000)})
        session.turn_count = 1
    else:
        session.turn_count = 0


def _latest_role(messages: list[dict], role: str) -> str:
    for msg in reversed(messages):
        if msg.get("role") == role:
            return str(msg.get("content") or "").strip()
    return ""


def _objective_text(focus: str, session_latest_user: str, latest_user: str) -> str:
    focus_text = str(focus or "").strip()
    if latest_user and focus_text == latest_user:
        return "Continue the latest user request appended after this handoff seed."
    return focus_text or session_latest_user or "Continue current MO session."


def _recent_dialogue(messages: list[dict], *, limit: int = 12, exclude_latest_user: str = "") -> list[str]:
    rows: list[str] = []
    skip_index: int | None = None
    if exclude_latest_user.strip():
        needle = exclude_latest_user.strip()
        for idx in range(len(messages) - 1, -1, -1):
            msg = messages[idx]
            if msg.get("role") == "user" and str(msg.get("content") or "").strip() == needle:
                skip_index = idx
                break
    start = max(0, len(messages) - limit)
    for idx, msg in enumerate(messages[start:], start=start):
        if idx == skip_index:
            continue
        role = str(msg.get("role") or "").strip() or "message"
        if role == "tool":
            content = msg.get("content") or ""
            rows.append(f"- tool: [tool result chars={len(str(content))}]")
            continue
        if msg.get("tool_calls"):
            names = tool_call_names(msg.get("tool_calls"))
            rows.append(f"- {role}: [tool calls: {', '.join(names)}]")
            continue
        content = redact_monitor_text(str(msg.get("content") or "").strip(), 500)
        if not content:
            continue
        rows.append(f"- {role}: {content}")
    return rows


def _uncommitted_work_lines(changed: list[str]) -> list[str]:
    """Prominent ground-truth block: files this session changed but hasn't committed.

    Context compaction during a long turn can strip the tool-result history that
    records edits/tests, leaving the model to guess it "did nothing" and emit a
    false "[WORK BLOCKED] / no changes" capsule. Surfacing the uncommitted file
    list high in the handoff — with an explicit do-not-misreport rule — keeps the
    continuation honest even after the working memory is compacted away.
    """
    modified = [c for c in (changed or []) if not c.startswith("##")]
    if not modified:
        return []
    return [
        "",
        "## Uncommitted work already done this session (GROUND TRUTH — not recollection)",
        f"- {len(modified)} file(s) are modified and NOT yet committed. This work exists on disk; you did it:",
        *[f"  - `{redact_monitor_text(m, 200)}`" for m in modified[:40]],
        "- Do NOT report 'no changes', 'not started', or 'blocked' about this work. "
        "Run `git status` / `git diff` to re-orient, then continue or finish — never claim it away.",
    ]


def _git_status_lines() -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "status", "--short", "--branch"],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if proc.returncode == 0:
            return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    except Exception:
        return []
    return []


def _worker_summary(agent: Any) -> list[str]:
    return worker_summary_lines(agent, limit=8)


def _goal_summary(agent: Any) -> list[str]:
    return goal_summary_lines(agent, limit=8, include_evidence=True)


def _prt_summary(agent: Any) -> list[str]:
    registry = getattr(agent, "workers", None)
    if not registry or not hasattr(registry, "recent"):
        return []
    rows = []
    try:
        for w in registry.recent(limit=20):
            if getattr(w, "kind", "") == "prt":
                state = getattr(w, "state", "running")
                summary = getattr(w, "result_summary", "")
                rows.append(f"PRT {w.id} [{state}]: {redact_monitor_text(summary, 200)}")
    except Exception:
        traceback.print_exc()
    return rows[-5:]


def _active_task_board(agent: Any) -> Any | None:
    board = getattr(agent, "_active_task_board", None)
    if board:
        return board
    gateway = getattr(agent, "gateway", None)
    return getattr(gateway, "last_task_board", None) if gateway else None


def _task_board_summary(board: Any | None, *, session_id: str = "") -> list[str]:
    if not board:
        recent = read_recent_snapshots(limit=1, session_id=session_id) if session_id else []
        return _task_board_snapshot_summary(recent[-1]) if recent else []
    context = compile_board_context(board, max_tasks=10, max_evidence=4, max_chars=2400)
    return ["- " + line if idx == 0 else "  " + line for idx, line in enumerate(context["lines"])]


def _task_board_snapshot_summary(snapshot: dict[str, Any] | None) -> list[str]:
    if not snapshot:
        return []
    context = compile_board_context_from_snapshot(snapshot, max_tasks=10, max_evidence=4, max_chars=2400)
    return ["- " + line if idx == 0 else "  " + line for idx, line in enumerate(context["lines"])]


def _recent_tool_audit(agent: Any, *, limit: int = 14) -> list[dict[str, Any]]:
    cfg = getattr(agent, "sandbox_config", {}) if isinstance(getattr(agent, "sandbox_config", {}), dict) else {}
    audit_path = cfg.get("audit_log")
    if not audit_path:
        return []
    path = Path(str(audit_path))
    if not path.exists() or not path.is_file():
        return []
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
    except Exception:
        return []
    entries: list[dict[str, Any]] = []
    for raw in raw_lines:
        try:
            entry = json.loads(raw)
        except Exception:
            continue
        if not isinstance(entry, dict):
            continue
        safe_args = entry.get("arguments") if isinstance(entry.get("arguments"), dict) else {}
        entries.append({
            "ts": entry.get("ts"),
            "tool": redact_monitor_text(str(entry.get("tool") or ""), 80),
            "arguments": safe_args,
            "result_chars": int(entry.get("result_chars") or 0),
            "blocked": bool(entry.get("blocked")),
            "block_reason": redact_monitor_text(str(entry.get("block_reason") or ""), 220),
        })
    return entries[-limit:]


def _recent_provider_audit(*, limit: int = 8) -> list[str]:
    path = Path(PROVIDER_AUDIT_LOG_PATH)
    if not path.exists() or not path.is_file():
        return []
    try:
        raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:]
    except Exception:
        return []
    rows: list[str] = []
    interesting = {"provider_error", "provider_fallback", "model_switch", "context_handoff"}
    for raw in raw_lines:
        try:
            entry = json.loads(raw)
        except Exception:
            continue
        if not isinstance(entry, dict):
            continue
        event = str(entry.get("event") or "").strip()
        reason = str(entry.get("reason") or "").strip()
        ok = entry.get("ok")
        if event not in interesting and not reason and ok is not False:
            continue
        surface = str(entry.get("surface") or "").strip()
        provider = str(entry.get("provider") or "").strip()
        model = str(entry.get("model") or "").strip()
        route = f"{surface} " if surface else ""
        model_text = f" {provider}/{model}" if provider or model else ""
        reason_text = f" reason={reason}" if reason else ""
        ok_text = " ok=false" if ok is False else ""
        rows.append(redact_monitor_text(f"{route}{event}{model_text}{reason_text}{ok_text}".strip(), 320))
    return rows[-limit:]



def _safe_audit_arg_summary(arguments: dict[str, Any], *, limit: int = 500) -> str:
    if not arguments:
        return ""
    keys = (
        "path", "root", "workdir", "pattern", "file_glob", "command", "url", "query",
        "content_chars", "old_text_chars", "new_text_chars", "method",
    )
    picked = {str(key): arguments.get(key) for key in keys if arguments.get(key) is not None}
    if not picked:
        picked = {str(k): v for k, v in list(arguments.items())[:6]}
    return redact_monitor_text(json.dumps(picked, ensure_ascii=False, default=str), limit)


def _evidence_ledger(board: Any | None, tool_entries: list[dict[str, Any]]) -> list[str]:
    rows: list[str] = []
    if board:
        for task in list(getattr(board, "tasks", []) or [])[:10]:
            evidence = list(getattr(task, "evidence", []) or [])
            if not evidence:
                continue
            status = str(getattr(task, "status", "") or "pending")
            title = redact_monitor_text(getattr(task, "title", ""), 180)
            for item in evidence[:4]:
                rows.append(f"- task {getattr(task, 'id', '?')} [{status}] {title}: {redact_monitor_text(str(item), 220)}")
    for entry in tool_entries[-8:]:
        if entry.get("blocked"):
            continue
        summary = _safe_audit_arg_summary(entry.get("arguments") or {}, limit=360)
        tool = entry.get("tool") or "tool"
        suffix = f" args={summary}" if summary else ""
        rows.append(f"- recent tool {tool}:{suffix} result_chars={int(entry.get('result_chars') or 0)}")
    return rows[:18]


def _attempt_ledger(board: Any | None, tool_entries: list[dict[str, Any]], messages: list[dict], metrics: dict[str, Any]) -> list[str]:
    rows: list[str] = []
    if board:
        for task in list(getattr(board, "tasks", []) or [])[:10]:
            if getattr(task, "status", "") == "blocked":
                rows.append(
                    f"- blocked task {getattr(task, 'id', '?')}: {redact_monitor_text(getattr(task, 'title', ''), 180)} — {redact_monitor_text(getattr(task, 'blocker', ''), 220)}"
                )
    for entry in tool_entries[-10:]:
        if not entry.get("blocked"):
            continue
        summary = _safe_audit_arg_summary(entry.get("arguments") or {}, limit=300)
        detail = f" args={summary}" if summary else ""
        reason = entry.get("block_reason") or "blocked"
        rows.append(f"- blocked tool {entry.get('tool') or 'tool'}:{detail} — {redact_monitor_text(reason, 220)}")
    rows.extend(_user_corrections(messages))
    if int(metrics.get("trimmed_messages_count") or 0) > 0:
        rows.append("- prior history was trimmed; do not repeat from memory if evidence is missing—re-check files/logs instead.")
    return rows[:18]


def _user_corrections(messages: list[dict], *, limit: int = 4) -> list[str]:
    rows: list[str] = []
    patterns = ("don't", "do not", "wrong", "instead", "stop", "no ", "not ", "avoid", "must", "never")
    for msg in reversed(messages[-24:]):
        if msg.get("role") != "user":
            continue
        content = str(msg.get("content") or "").strip()
        lower = content.lower()
        if any(pat in lower for pat in patterns):
            rows.append(f"- recent user constraint/correction: {redact_monitor_text(content, 260)}")
        if len(rows) >= limit:
            break
    return list(reversed(rows))


def _decision_rows(messages: list[dict], board: Any | None, goal: list[str]) -> list[str]:
    rows: list[str] = []
    if board:
        title = str(getattr(board, "title", getattr(board, "template", "")) or "")
        if title:
            rows.append(f"- Taskboard in use: `{redact_monitor_text(title, 80)}`.")
        if any(getattr(task, "status", "") == "blocked" for task in getattr(board, "tasks", []) or []):
            rows.append("- Continue around blocked taskboard work only after verifying the blocker is still real.")
    if goal:
        rows.append("- Goal state exists; do not start a conflicting autonomous goal without checking it.")
    for msg in reversed(messages[-16:]):
        if msg.get("role") != "assistant":
            continue
        content = str(msg.get("content") or "")
        if re.search(r"\b(decided|decision|we will|plan is|next step is)\b", content, re.IGNORECASE):
            rows.append(f"- recent assistant decision note: {redact_monitor_text(content, 260)}")
        if len(rows) >= 6:
            break
    return rows[:6]


def _unknowns(
    changed: list[str],
    board: Any | None,
    tool_entries: list[dict[str, Any]],
    provider_events: list[str],
    graph_context: str,
    metrics: dict[str, Any],
) -> list[str]:
    rows = ["- Re-read files and re-run the smallest relevant verification before claiming completion."]
    change_count = max(0, len([line for line in changed if not line.startswith("##")]))
    if change_count:
        rows.append(f"- Workspace is dirty ({change_count} changed/untracked item(s)); inspect diffs before release/ready claims.")
    if board and any(getattr(task, "is_open", False) for task in getattr(board, "tasks", []) or []):
        rows.append("- Taskboard has open/blocked items; do not claim all done until they are resolved with evidence.")
    if not tool_entries:
        rows.append("- No recent tool audit entries were captured in this handoff; file/test facts may be stale.")
    if provider_events and any("provider_error" in event or "ok=false" in event for event in provider_events):
        rows.append("- Recent provider audit includes errors/fallback signals; do not assume provider stability.")
    if graph_context:
        rows.append("- Code graph/reference slice is orientation only and may be stale; open files before editing.")
    if int(metrics.get("trimmed_messages_count") or 0) > 0:
        rows.append("- Some prior messages were trimmed before handoff; missing conversation details are unknown.")
    return rows


_HANDOFF_GENERIC_GRAPH_TERMS = {
    "compact",
    "context",
    "continue",
    "handoff",
    "status",
    "summary",
    "test",
    "unit",
}


def _specific_handoff_graph_query(query: str) -> bool:
    words = [
        word
        for word in re.findall(r"[a-z0-9_./-]{3,}", str(query or "").lower())
        if word not in {"and", "for", "that", "the", "this", "with"}
    ]
    if len(words) < 2 and (not words or words[0] in _HANDOFF_GENERIC_GRAPH_TERMS):
        return False
    return True


def _code_graph_context_for_handoff(query: str) -> str:
    query = str(query or "").strip()
    if not query or not _specific_handoff_graph_query(query) or not should_include_code_graph_context(query):
        return ""
    try:
        graph_context = build_code_graph_context(query, max_chars=1200, max_nodes=6)
        try:
            from ..graph.structural_graph import build_structural_summary
            structural = build_structural_summary(query, max_chars=900)
        except Exception:
            structural = ""
        return "\n\n".join(part for part in (graph_context, structural) if part)
    except Exception:
        return ""


def _artifact_references(
    agent: Any | None = None,
    *,
    changed: list[str] | None = None,
    workers: list[str] | None = None,
    goal: list[str] | None = None,
    task_board: Any | None = None,
    tool_entries: list[dict[str, Any]] | None = None,
    graph_context: str = "",
) -> list[str]:
    candidates: list[str] = []
    static_names = [
        "README.md",
        "AGENTS.md",
        "MAP.md",
        "core/prompts/system.md",
        "config.example.yaml",
    ]
    candidates.extend(name for name in static_names if Path(name).exists())

    for line in changed or []:
        if line.startswith("##"):
            continue
        text = line[3:].strip() if len(line) > 3 else line.strip()
        if " -> " in text:
            candidates.extend(part.strip() for part in text.split(" -> "))
        elif text:
            candidates.append(text)

    if task_board:
        board_text = "\n".join(_task_board_summary(task_board))
        candidates.extend(_path_candidates_from_text(board_text))

    for entry in tool_entries or []:
        args = entry.get("arguments") if isinstance(entry.get("arguments"), dict) else {}
        for key in ("path", "root", "workdir", "file_glob"):
            value = str(args.get(key) or "").strip()
            if value:
                candidates.append(value)
        command = str(args.get("command") or "")
        if command:
            candidates.extend(_path_candidates_from_text(command))

    combined_worker_text = "\n".join((workers or []) + (goal or []))
    if combined_worker_text:
        try:
            candidates.extend(extract_worker_paths(combined_worker_text))
        except Exception:
            candidates.extend(_path_candidates_from_text(combined_worker_text))

    if graph_context:
        candidates.extend(_path_candidates_from_text(graph_context))
        graph_path = Path("memory/code_graph/knowledge-graph.json")
        if graph_path.exists():
            candidates.append(str(graph_path).replace("\\", "/"))
        structural_graph_path = Path("graphify-out/graph.json")
        if structural_graph_path.exists():
            candidates.append(str(structural_graph_path).replace("\\", "/"))

    return _unique_references(candidates)


def _path_candidates_from_text(text: str) -> list[str]:
    found: list[str] = []
    for match in re.finditer(r"`([^`]{1,220})`", str(text or "")):
        found.append(match.group(1).strip())
    pattern = r"(?<![\w.-])(?:[A-Za-z]:[\\/])?[\w.-]+(?:[\\/][\w .()@+-]+)+(?:\.[A-Za-z0-9]{1,8})?|(?<![\w.-])[\w.-]+\.(?:py|md|txt|ya?ml|json|toml|ini|html|css|js|ts|ps1|bat|sh)"
    for match in re.finditer(pattern, str(text or "")):
        found.append(match.group(0).strip())
    return found


def _unique_references(candidates: list[str], *, limit: int = 40) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        item = str(raw or "").strip().strip("'\"")
        item = item.replace("\\", "/")
        if not _reference_ok(item):
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        refs.append(redact_monitor_text(item, 240))
        if len(refs) >= limit:
            break
    return refs


def _reference_ok(item: str) -> bool:
    if not item or len(item) > 220:
        return False
    lower = item.lower()
    if any(secret in lower for secret in ("api_key", "apikey", "secret", "token", "password", ".env")):
        return False
    if lower.startswith(("http://", "https://")):
        return True
    if any(part in lower for part in ("__pycache__", ".pytest_cache", ".ruff_cache", "logs/")):
        return False
    if "\n" in item or "\r" in item or "\t" in item:
        return False
    if item.startswith("-"):
        return False
    return True


def _next_exact_step(latest_user: str, artifacts: list[str], board: Any | None) -> str:
    if latest_user.strip():
        return "Continue the appended latest user request, first checking the most relevant referenced file/test evidence instead of relying on this handoff."
    if board:
        for task in getattr(board, "tasks", []) or []:
            if getattr(task, "status", "") in {"active", "pending", "blocked"}:
                return f"Resume task {getattr(task, 'id', '?')}: {redact_monitor_text(getattr(task, 'title', ''), 220)}."
    if artifacts:
        return f"Open `{artifacts[0]}` and verify the current state before making the next claim."
    return "Run a fresh repo/session status check, then continue the user-facing request with evidence."


def _format_ts(value: Any) -> str:
    try:
        ts = float(value or 0.0)
    except Exception:
        ts = 0.0
    if ts <= 0:
        return "unknown"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _duration(seconds: Any) -> str:
    try:
        total = max(0, int(seconds or 0))
    except Exception:
        total = 0
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _indent_block(text: str, *, prefix: str) -> list[str]:
    return [prefix + redact_monitor_text(line, 900) for line in str(text or "").splitlines() if line.strip()]
