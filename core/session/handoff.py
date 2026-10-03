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
from ..runtime.subprocess_flags import apply_windows_hidden_process_flags
from ..context.coordination_state import goal_summary_lines, worker_summary_lines
from ..provider.provider_audit import LOG_PATH as PROVIDER_AUDIT_LOG_PATH
from ..tasking.task_board import read_recent_snapshots
from ..tasking.task_board_context import compile_board_context, compile_board_context_from_snapshot
from ..worker.registry import extract_worker_paths
from ..utils.text_utils import cap_by_tokens, chars_to_tokens, token_aware_truncation_enabled

HANDOFF_HEADER = "MO HANDOFF CONTEXT"
MAX_HANDOFF_DOC_CHARS = 60_000
HANDOFF_FILE_KEEP = 30


def _message_for_pressure(message: Any) -> Any:
    """Return a copy with image payload bytes replaced by a small marker.

    ``context_pressure`` measures textual conversation pressure, not HTTP body
    bytes. A screenshot data URI can be hundreds of thousands of characters even
    though it is a bounded vision input, so counting the base64 string as chat
    history forces false context handoffs before the vision model can consume it.
    """
    if not isinstance(message, dict):
        return message
    content = message.get("content")
    if not isinstance(content, list):
        return message
    changed = False
    parts: list[Any] = []
    for part in content:
        if not isinstance(part, dict):
            parts.append(part)
            continue
        ptype = str(part.get("type") or "").strip().lower()
        if ptype in {"image", "image_url", "input_image"}:
            marker = {"type": ptype or "image", "image_url": "[image omitted for context-pressure accounting]"}
            parts.append(marker)
            changed = True
        else:
            parts.append(part)
    if not changed:
        return message
    scrubbed = dict(message)
    scrubbed["content"] = parts
    return scrubbed


def session_chars(session: Any, *, extra_context: str = "") -> int:
    messages = list(getattr(session, "messages", []) or [])
    total = sum(
        len(json.dumps(_message_for_pressure(m), default=str, ensure_ascii=False))
        for m in messages
    )
    if extra_context:
        total += len(str(extra_context))
    return total


def context_pressure(agent: Any, *, extra_context: str = "", tools: list[dict] | None = None) -> dict[str, Any]:
    session = getattr(agent, "session", None)
    budget_chars = None
    try:
        budget_chars = agent._provider_context_max_chars()
    except Exception:
        budget_chars = None
    # Measure the textual provider projection, not private replay metadata or
    # base64 transport bytes. Add reported reasoning usage only for retained,
    # compatible native replay; visible text remains an estimate.
    if callable(getattr(session, "get_messages", None)):
        requires_reasoning = getattr(agent, "_provider_requires_reasoning_content", None)
        messages = session.get_messages(
            extra_context=extra_context,
            include_reasoning_content=bool(requires_reasoning()) if callable(requires_reasoning) else False,
        )
        parts = [json.dumps(_message_for_pressure(m), default=str, ensure_ascii=False) for m in messages]
    else:
        parts = [
            json.dumps(_message_for_pressure(m), default=str, ensure_ascii=False)
            for m in list(getattr(session, "messages", []) or [])
        ]
        if extra_context:
            parts.append(str(extra_context))
    tool_text = json.dumps(tools, default=str, ensure_ascii=False) if tools else ""
    chars = sum(len(part) for part in parts) + len(tool_text)
    tool_chars = len(tool_text)
    estimated_tokens = sum(max(1, chars_to_tokens(part)) for part in parts if part)
    tool_tokens = max(1, chars_to_tokens(tool_text)) if tool_text else 0
    estimated_tokens += tool_tokens
    replay_tokens = 0
    requires_replay = getattr(agent, "_provider_requires_responses_state", None)
    if callable(requires_replay) and requires_replay():
        from .session import replayed_reasoning_tokens
        replay_tokens = replayed_reasoning_tokens(
            list(getattr(session, "messages", []) or []), str(getattr(agent, "model", "") or ""),
        )
        estimated_tokens += replay_tokens
    budget_tokens = (
        max(1, int(budget_chars) // 4)
        if budget_chars
        else int(getattr(agent, "context_budget_tokens", 0) or 0)
    )
    if not budget_chars and budget_tokens > 0:
        budget_chars = budget_tokens * 4
    message_count = len(getattr(session, "messages", []) or [])
    max_history = int(getattr(session, "max_history", 500) or 500)
    char_ratio = (chars / budget_chars) if budget_chars else 0.0
    token_ratio = (estimated_tokens / budget_tokens) if budget_tokens else 0.0
    message_ratio = (message_count / max_history) if max_history else 0.0
    # Context pressure means provider-window pressure. Message retention is a
    # separate safety ceiling and remains only the fallback when no model budget exists.
    raw_pressure = token_ratio if budget_tokens else message_ratio
    pressure_source = "tokens-estimate" if budget_tokens else "messages-fallback"
    created_at = float(getattr(session, "created_at", 0.0) or 0.0)
    age_seconds = max(0, int(time.time() - created_at)) if created_at else 0
    return {
        "chars": chars,
        "tool_chars": tool_chars,
        "budget_chars": budget_chars or 0,
        "char_ratio": char_ratio,
        "estimated_tokens": estimated_tokens,
        "tool_tokens": tool_tokens,
        "replayed_reasoning_tokens": replay_tokens,
        "budget_tokens": budget_tokens,
        "token_ratio": token_ratio,
        "message_count": message_count,
        "max_history": max_history,
        "message_ratio": message_ratio,
        "retention_ratio": message_ratio,
        "retention_over_limit": message_ratio > 1.0,
        "raw_pressure": raw_pressure,
        "pressure": min(1.0, raw_pressure),
        "pressure_source": pressure_source,
        "over_limit": token_ratio > 1.0 if budget_tokens else message_ratio > 1.0,
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
    provider_threshold = float(agent_cfg.get("context_handoff_char_threshold", base_threshold) or base_threshold)
    msg_threshold = float(agent_cfg.get("context_handoff_msg_threshold", base_threshold) or base_threshold)
    provider_threshold = min(0.95, max(0.25, provider_threshold))
    msg_threshold = min(0.95, max(0.25, msg_threshold))
    trimmed_threshold = int(agent_cfg.get("context_handoff_trimmed_threshold", 1) or 1)
    min_messages = int(agent_cfg.get("context_handoff_min_messages", 8) or 8)
    min_chars = int(agent_cfg.get("context_handoff_min_chars", 16_000) or 16_000)

    message_count = int(metrics.get("message_count") or 0)
    trimmed = int(metrics.get("trimmed_messages_count") or 0)
    last_trimmed_at = float(metrics.get("last_trimmed_at") or 0.0)
    last_compacted_at = float(metrics.get("last_compacted_at") or 0.0)
    trimmed_uncovered = trimmed >= trimmed_threshold and (last_compacted_at <= 0.0 or last_trimmed_at > last_compacted_at)
    estimated_tokens = int(metrics.get("estimated_tokens") or 0)
    provider_ratio = float(metrics.get("token_ratio") or metrics.get("char_ratio") or 0.0)
    enough_state = message_count >= min_messages or max(int(metrics.get("chars") or 0), estimated_tokens * 4) >= min_chars
    triggered = enough_state and (provider_ratio >= provider_threshold or trimmed_uncovered)
    metrics["threshold"] = provider_threshold
    metrics["char_threshold"] = provider_threshold  # compatibility key for saved monitors/config
    metrics["message_threshold"] = msg_threshold
    metrics["triggered"] = triggered
    if not triggered:
        trigger_dimension = ""
    elif trimmed_uncovered:
        trigger_dimension = "trimmed-history"
    else:
        trigger_dimension = "token-budget"
    metrics["trigger_dimension"] = trigger_dimension
    return triggered, metrics


def format_handoff_reason(metrics: dict[str, Any], *, prefix: str = "") -> str:
    """Describe the actual trigger without presenting an estimate as provider usage."""
    lead = f"{str(prefix).strip()} " if str(prefix).strip() else ""
    dimension = str(metrics.get("trigger_dimension") or "unknown")
    messages = f"{metrics.get('message_count')}/{metrics.get('max_history')}"
    tokens = f"{metrics.get('estimated_tokens')}/{metrics.get('budget_tokens')} estimated tokens"
    chars = f"{metrics.get('chars')}/{metrics.get('budget_chars')} chars"
    context_ratio = float(metrics.get("token_ratio") or metrics.get("char_ratio") or 0.0)
    if dimension == "trimmed-history":
        return (
            f"{lead}retention limit [trimmed-history]; messages {messages}; "
            f"provider context {context_ratio:.0%} ({tokens}; {chars})"
        )
    return (
        f"{lead}context pressure {float(metrics.get('pressure') or 0.0):.0%} "
        f"[{dimension}]; messages {messages}; {tokens}; {chars}"
    )


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
    changed = _git_status_lines()
    workers = _worker_summary(agent)
    goal = _goal_summary(agent)
    task_board = _active_task_board(agent)
    task_board_rows = _task_board_summary(task_board, session_id=session_id)
    prior_objective = _task_board_objective(task_board, session_id=session_id) or _previous_user_objective(messages)
    objective = _objective_text(
        focus, session_latest_user, latest_user_text,
        getattr(agent, "_current_user_input", ""), prior_objective,
    )
    tool_entries = _recent_tool_audit(agent)
    provider_events = _recent_provider_audit(agent)
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
        f"- Turns/messages: {metrics['turn_count']} turn(s), {metrics['message_count']}/{metrics['max_history']} retained message(s)",
        f"- Provider context pressure: {float(metrics['pressure']):.0%} ({metrics['estimated_tokens']}/{metrics['budget_tokens']} estimated tokens; {metrics['chars']}/{metrics['budget_chars']} chars)",
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
        *_uncommitted_work_lines(changed, tool_entries=tool_entries),
        "",
        "## Operating rules for the continuation",
        "- This handoff is orientation, not proof. Check evidence freshness; reuse completed verification for an unchanged candidate.",
        f"- Preserve {redact_monitor_text(operator, 80)} workflow: exact completion, no fake done, tests/evidence before claims.",
        "- Prefer concise catch-up: use this capsule to choose the next action, not to retell the whole conversation.",
    ])
    # Result caps keep pathological fresh output from bloating provider history;
    # the marker is explicit and the retained excerpt is not proof of omitted text.
    cap_ops = int(getattr(agent, 'result_cap_total_ops', 0) or 0) + int(getattr(agent, 'carried_result_cap_ops', 0) or 0)
    if cap_ops > 0:
        if callable(getattr(agent, '_tool_context_saved_chars', None)):
            saved_chars = int(agent._tool_context_saved_chars() or 0)
        else:
            saved_chars = (
                int(getattr(agent, 'result_cap_total_saved', 0) or 0)
                + int(getattr(agent, 'carried_result_cap_saved', 0) or 0)
            )
        lines.append(f"- Explicit result caps kept tool context bounded ({cap_ops} caps, {saved_chars:,} chars omitted with markers). Re-run tools for exact omitted details when needed.")
    lines.extend([
        "",
        "## Taskboard state",
    ])
    lines.extend(task_board_rows or ["- No current taskboard captured."])

    lines.extend([
        "",
        "## Verified evidence ledger",
        "- Tool/taskboard-backed entries follow; check candidate identity and freshness before reusing them.",
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
    file_ops = _file_operation_lines(tool_entries=tool_entries)
    if file_ops:
        lines.extend(["", "## Recent file operations (this session/turn)", *file_ops])

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
        f"- {_next_exact_step(latest_user_text, task_board)}",
        "",
        "## Suggested MO surfaces",
        "- Use `/goal` only when autonomous completion is intended and auditor evidence can pass.",
        "- Use MO Desktop only as a separate companion; terminal MO owns completion.",
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


def _file_operation_lines(*, tool_entries: list[dict]) -> list[str]:
    read_files, modified_files = _file_operation_refs(tool_entries=tool_entries)
    return [f"- Read: {redact_monitor_text(path, 240)}" for path in read_files[:10]] + [
        f"- Modified: {redact_monitor_text(path, 240)}" for path in modified_files[:10]
    ]


def _file_operation_refs(*, tool_entries: list[dict]) -> tuple[list[str], list[str]]:
    from ..tooling.tool_constants import FILE_MUTATION_TOOLS

    read_files: set[str] = set()
    modified_files: set[str] = set()
    for entry in tool_entries:
        if entry.get("blocked") or entry.get("error"):
            continue
        args = entry.get("arguments") or {}
        path = str(args.get("path") or args.get("file_path") or "")
        if path and entry.get("tool") == "read_file":
            read_files.add(path)
        if path and entry.get("tool") in FILE_MUTATION_TOOLS:
            modified_files.add(path)
    return sorted(read_files), sorted(modified_files)


def _compact_health_lines(agent: Any) -> list[str]:
    lines: list[str] = []
    try:
        from ..diagnostics.system_health import build_health_report
        structural = build_health_report(
            str(getattr(agent, "runtime_home", ".") or "."),
            project_root=str(getattr(agent, "project_cwd", ".") or "."),
        ).graph
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
    session_id = str(getattr(session, "session_id", "") or "")
    latest_user_text = str(latest_user or "").strip()
    changed = _git_status_lines()
    workers = _worker_summary(agent)
    goal = _goal_summary(agent)
    task_board = _active_task_board(agent)
    task_board_rows = _task_board_summary(task_board, session_id=session_id)
    prior_objective = _task_board_objective(task_board, session_id=session_id) or _previous_user_objective(messages)
    objective = _objective_text(
        focus, _latest_role(messages, "user"), latest_user_text,
        getattr(agent, "_current_user_input", ""), prior_objective,
    )
    decisions = _decision_rows(messages, task_board, goal)
    metrics = context_pressure(agent) if session else {}
    tool_entries = _recent_tool_audit(agent)
    attempts = _attempt_ledger(task_board, tool_entries, messages, metrics)
    recent = _recent_dialogue(messages, limit=10, exclude_latest_user=latest_user_text)
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
    if task_board_rows:
        lines.extend(["", "## Taskboard at handoff", *task_board_rows])
    lines.extend([
        "",
        "## Continuation Contract",
        "- Continue the active work from this capsule; do not restart the original request or redo completed discovery.",
        "- Apply later user corrections, restrictions, cancellation, or replacement of the goal before choosing the next action. A recorded taskboard row never overrides them.",
    ])
    if changed:
        lines.extend(["", "## Workspace", *[f"- `{redact_monitor_text(item, 220)}`" for item in changed[:10]]])
    if workers:
        lines.extend(["", "## Active Workers", *[f"- {redact_monitor_text(item, 220)}" for item in workers[:8]]])
    if decisions:
        lines.extend(["", "## Key Decisions", *[f"- {item}" for item in decisions[:6]]])
    lines.extend([
        "",
        "## Critical Context",
        f"- Session: {metrics.get('turn_count', '?')} turns, {metrics.get('message_count', '?')}/{metrics.get('max_history', '?')} retained messages",
        f"- Provider context pressure: {float(metrics.get('pressure', 0.0) or 0.0):.0%}",
        f"- Provider/model: {redact_monitor_text(getattr(agent, 'provider_name', ''), 80)} / {redact_monitor_text(getattr(agent, 'model', ''), 100)}",
    ])
    total_tokens = max(0, int(getattr(session, "total_tokens", 0) or 0))
    if total_tokens:
        lines.append(
            f"- Recorded provider usage before handoff: {total_tokens:,} total request tokens "
            f"({max(0, int(getattr(session, 'input_tokens', 0) or 0)):,} input; "
            f"{max(0, int(getattr(session, 'output_tokens', 0) or 0)):,} output; "
            f"{len(list(getattr(session, 'token_log', []) or []))} usage receipts). "
            "This is cumulative request accounting, not unique context size."
        )
    if getattr(agent, "context_budget_tokens", 0):
        lines.append(f"- Context budget: {int(getattr(agent, 'context_budget_tokens', 0) or 0):,} tokens")
    lines.extend(_compact_health_lines(agent))
    file_ops = _file_operation_lines(tool_entries=tool_entries)
    if file_ops:
        lines.extend(["", "## Recent File Operations", *file_ops[:12]])
    if attempts:
        lines.extend(["", "## Already Tried / Avoid Repeating", *attempts[:6]])
    if recent:
        lines.extend(["", "## Recent Session Spine", *recent[:10]])
    lines.extend([
        "",
        "## Next Steps",
        f"- {_next_exact_step(latest_user, task_board)}",
        f"- Continue: {redact_monitor_text(objective, 400)}",
        "- Reuse completed verification for an unchanged candidate; recheck changed or stale evidence before claims.",
    ])
    read_files, modified_files = _file_operation_refs(tool_entries=tool_entries)
    if read_files:
        lines.extend(["", "<read-files>", *read_files[:12], "</read-files>"])
    if modified_files:
        lines.extend(["", "<modified-files>", *modified_files[:12], "</modified-files>"])
    return redact_monitor_text("\n".join(lines).strip() + "\n", 40_000)


def write_handoff_document(document: str, *, prefix: str = "mo-handoff") -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = Path(tempfile.gettempdir()) / f"{prefix}-{stamp}-{os.getpid()}-{time.time_ns()}.md"
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


def recent_visible_report_messages(messages: list[dict], *, max_chars: int = 3000, keep_recent: int = 6, active_user: str = "") -> list[dict]:
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
    active_start = next((i for i in range(len(messages or []) - 1, -1, -1) if active_user and messages[i].get("role") == "user" and str(messages[i].get("content") or "").strip() == active_user.strip()), len(messages or []))
    # Progress chatter must not displace the original request or the operator's
    # later corrections. Bound reports separately from retained user turns.
    user_indices = [i for i, msg in enumerate(messages or []) if msg.get("role") == "user"]
    retained_users = set(user_indices[:1] + user_indices[-keep_recent:])
    kept: list[dict] = []
    reports = 0
    for index in range(len(messages or []) - 1, -1, -1):
        msg = messages[index]
        role = str(msg.get("role") or "")
        if role == "user" and index not in retained_users and index < active_start:
            continue
        if role == "assistant" and reports >= keep_recent:
            continue
        if role not in {"user", "assistant"}:
            continue
        content = str(msg.get("content") or "")
        if role != "user":
            content = content.strip()
        if not content.strip():
            continue
        lower = content.lower()
        if lower.startswith(blocked_prefixes):
            continue
        kept.append({"role": role, "content": redact_monitor_text(content, len(content) + 1 if role == "user" else max_chars)})
        reports += role == "assistant"
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
        content = str(msg.get("content") or "")
        if role != "user":
            content = content.strip()
        if role in {"user", "assistant"} and content:
            kept_visible.append({"role": role, "content": redact_monitor_text(content, len(content) + 1 if role == "user" else 8_000)})
    session.clear()
    session.session_id = f"mo-handoff-{time.time_ns()}"
    session.created_at = time.time()
    # One canonical history preserves handoff context across calls and save/resume.
    # System context stays outside the visible conversation.
    session.messages = [{"role": "system", "content": redact_monitor_text(seed, 60_000)}, *kept_visible]
    latest = redact_monitor_text(latest_user, len(latest_user) + 1) if latest_user.strip() else ""
    already_last = bool(
        latest
        and session.messages
        and session.messages[-1].get("role") == "user"
        and str(session.messages[-1].get("content") or "").strip() == latest
    )
    if latest and not already_last:
        session.messages.append({"role": "user", "content": latest})
    session.turn_count = sum(message.get("role") == "user" for message in session.messages)


def _latest_role(messages: list[dict], role: str) -> str:
    for msg in reversed(messages):
        if msg.get("role") == role:
            return str(msg.get("content") or "").strip()
    return ""


def _objective_text(
    focus: str,
    session_latest_user: str,
    latest_user: str,
    active_user: str = "",
    prior_objective: str = "",
) -> str:
    """Resolve a continuation phrase to the substantive work it refers to."""
    from ..runtime.work_signals import looks_like_contextual_followup

    candidates = [focus, latest_user, active_user, session_latest_user]
    for value in candidates:
        text = str(value or "").strip()
        if text and not looks_like_contextual_followup(text):
            return text
    prior = str(prior_objective or "").strip()
    if prior:
        return prior
    return next((str(value).strip() for value in candidates if str(value or "").strip()), "Continue current MO session.")


def _previous_user_objective(messages: list[dict]) -> str:
    """Return the latest user turn that can stand without prior conversation."""
    from ..runtime.work_signals import looks_like_contextual_followup

    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        text = str(message.get("content") or "").strip()
        if text and not looks_like_contextual_followup(text):
            return text
    return ""


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
        content = redact_monitor_text(str(msg.get("content") or "").strip(), 500)
        if msg.get("tool_calls"):
            names = tool_call_names(msg.get("tool_calls"))
            tool_note = f"[tool calls: {', '.join(names)}]"
            rows.append(f"- {role}: {content} {tool_note}" if content else f"- {role}: {tool_note}")
            continue
        if not content:
            continue
        rows.append(f"- {role}: {content}")
    return rows


def _status_path(status_line: str) -> str:
    """Return a path from either exact or legacy-stripped short Git status."""
    text = str(status_line or "").rstrip()
    if not text or text.startswith("##"):
        return ""
    if len(text) >= 3 and text[2] == " ":
        return text[3:].strip()
    if len(text) >= 2 and text[1] == " ":
        return text[2:].strip()
    return text.strip()


def _workspace_path_key(path: str) -> str:
    text = str(path or "").strip().strip('"')
    if not text:
        return ""
    try:
        value = Path(text)
        if not value.is_absolute():
            value = Path.cwd() / value
        return os.path.normcase(os.path.normpath(str(value)))
    except (OSError, ValueError):
        return os.path.normcase(os.path.normpath(text))


def _uncommitted_work_lines(changed: list[str], *, tool_entries: list[dict]) -> list[str]:
    """Separate session-attributed mutations from shared-worktree state."""
    modified = [c for c in (changed or []) if c and not c.startswith("##")]
    if not modified:
        return []
    _read_files, recorded_mutations = _file_operation_refs(tool_entries=tool_entries)
    attributed_keys = {_workspace_path_key(path) for path in recorded_mutations}
    attributed: list[str] = []
    existing: list[str] = []
    for status_line in modified:
        path_text = _status_path(status_line)
        paths = [part.strip() for part in path_text.split(" -> ") if part.strip()]
        row = attributed if any(_workspace_path_key(path) in attributed_keys for path in paths) else existing
        row.append(status_line)

    lines = [
        "",
        "## Workspace changes at handoff",
    ]
    if attributed:
        lines.extend([
            f"- {len(attributed)} change(s) match successful mutation-tool evidence from this session:",
            *[f"  - `{redact_monitor_text(item, 200)}`" for item in attributed[:40]],
            "- Reuse this recorded work after checking its current diff; do not repeat it from recollection.",
        ])
    if existing:
        lines.extend([
            f"- {len(existing)} other workspace change(s) have no mutation-tool attribution to this session:",
            *[f"  - `{redact_monitor_text(item, 200)}`" for item in existing[:40]],
            "- Preserve these changes and do not claim them as this session's work or completion evidence.",
        ])
    return lines


def _git_status_lines() -> list[str]:
    try:
        kwargs = {
            "cwd": os.getcwd(),
            "capture_output": True,
            "text": True,
            "timeout": 1.5,
        }
        apply_windows_hidden_process_flags(kwargs)
        proc = subprocess.run(
            ["git", "status", "--short", "--branch"],
            **kwargs,
        )
        if proc.returncode == 0:
            return [line.rstrip() for line in proc.stdout.splitlines() if line.strip()]
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


def _task_board_objective(board: Any | None, *, session_id: str = "") -> str:
    if board:
        return str(getattr(board, "objective", "") or "").strip()
    recent = read_recent_snapshots(limit=1, session_id=session_id) if session_id else []
    return str(recent[-1].get("objective") or "").strip() if recent else ""


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
        if not isinstance(entry, dict) or not _audit_belongs_to_session(agent, entry):
            continue
        safe_args = entry.get("arguments") if isinstance(entry.get("arguments"), dict) else {}
        entries.append({
            "ts": entry.get("ts"),
            "tool": redact_monitor_text(str(entry.get("tool") or ""), 80),
            "arguments": safe_args,
            "result_chars": int(entry.get("result_chars") or 0),
            "blocked": bool(entry.get("blocked")),
            "error": bool(entry.get("error")),
            "block_reason": redact_monitor_text(str(entry.get("block_reason") or ""), 220),
        })
    return entries[-limit:]


def _audit_belongs_to_session(agent: Any, entry: dict[str, Any]) -> bool:
    from ..runtime.backend_monitor import current_monitor_context

    session_id = str(getattr(getattr(agent, "session", None), "session_id", "") or "")
    turn_id = str(current_monitor_context().get("turn_id") or "")
    return bool(
        (session_id and entry.get("session_id") == session_id)
        or (turn_id and entry.get("turn_id") == turn_id)
    )


def _recent_provider_audit(agent: Any, *, limit: int = 8) -> list[str]:
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
        if not isinstance(entry, dict) or not _audit_belongs_to_session(agent, entry):
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
    rows = ["- Check evidence freshness; reuse unchanged-candidate verification and rerun only the changed boundary before claiming completion."]
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
    if not query or not _specific_handoff_graph_query(query):
        return ""
    try:
        from ..graph.structural_graph import build_project_orientation, should_include_code_graph_context

        if not should_include_code_graph_context(query):
            return ""
        orientation = build_project_orientation(query, max_chars=1200, max_nodes=6, build_if_missing=False)
        try:
            from ..graph.structural_graph import build_structural_summary
            structural = build_structural_summary(query, max_chars=900)
        except Exception:
            structural = ""
        return "\n\n".join(part for part in (*orientation.values(), structural) if part)
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
        text = _status_path(line)
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
    return not item.startswith("-")


def _next_exact_step(latest_user: str, board: Any | None) -> str:
    if latest_user.strip():
        return "Continue the appended latest user request using the retained evidence and its current restrictions."
    if board:
        for task in getattr(board, "tasks", []) or []:
            if getattr(task, "status", "") in {"active", "pending", "blocked"}:
                return f"Recorded unfinished task {getattr(task, 'id', '?')}: {redact_monitor_text(getattr(task, 'title', ''), 220)}. Apply subsequent user instructions before continuing it."
    return "Continue the authorized work using retained evidence; inspect new sources only where the next decision requires them."


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
