"""Safe Ghost context builder.

Ghost is a side assistant/coordinator. This module gives it a concise, redacted
snapshot of MO state so it can answer about the current chat/workers without
seeing raw backend internals or secrets. Separate read-only scout context may be
provided by core.ghost_tool_context; neither surface owns task truth.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..backend_monitor import redact_monitor_text, tool_call_names
from ..graph.code_graph import build_code_graph_context, should_include_code_graph_context
from ..coordination_state import goal_summary_lines, worker_summary_lines
from ..tasking.task_board import read_recent_snapshots, status_marker
from ..tasking.task_board_context import compile_board_context, compile_board_context_from_snapshot


def build_ghost_context(
    agent: Any,
    gateway: Any | None = None,
    *,
    question: str = "",
    ui_state: dict[str, Any] | None = None,
    max_chars: int = 3200,
) -> str:
    """Return a safe, compact context block for Ghost side-chat.

    The block intentionally contains product-level state only: no raw system
    prompt, no provider traces, no secrets, and no private security internals.
    """
    ui_state = ui_state or {}
    status_question = _is_status_question(question)
    if status_question:
        max_chars = min(max_chars, 1800)
    sections: list[str] = []

    worker_state = _worker_state(agent, ui_state)
    if worker_state:
        sections.append("### Workers / routing state\n" + worker_state)

    session = getattr(agent, "session", None)
    session_id = str(getattr(session, "session_id", "") or "")
    board_text = _task_board_text(gateway, ui_state, session_id=session_id)
    if board_text:
        sections.append("### Current task board\n" + redact_monitor_text(board_text, 900))

    goal_text = _goal_text(agent)
    if goal_text:
        sections.append("### Current goal\n" + goal_text)

    if not status_question:
        profile_text = _profile_text(agent)
        if profile_text:
            sections.append("### Safe operator profile\n" + profile_text)

        session_text = _recent_session_text(agent)
        if session_text:
            sections.append("### Recent visible chat\n" + session_text)

        code_map_text = _code_map_text(question)
        if code_map_text:
            sections.append("### Private code map\n" + code_map_text)

    monitor_text = _recent_monitor_text(gateway)
    if monitor_text:
        sections.append("### Recent safe backend events\n" + monitor_text)

    routing_text = _routing_guidance(question, worker_state, status_question=status_question)
    sections.append("### Ghost routing guidance\n" + routing_text)

    text = "\n\n".join(section for section in sections if section.strip()).strip()
    text = redact_monitor_text(text, max_chars)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def _is_status_question(question: str) -> bool:
    q = str(question or "").lower()
    return bool(re.search(
        r"\b(what('?s| is)?\s+(mo\s+)?doing|what('?s| is)?\s+going\s+on|why\s+.*queu|since\s+when|how\s+far|status|stale|stuck)\b",
        q,
    ))


def _worker_state(agent: Any, ui_state: dict[str, Any]) -> str:
    lines: list[str] = []
    worker_rows = worker_summary_lines(agent, limit=6)
    if worker_rows:
        lines.append("Registered workers:\n" + "\n".join(worker_rows))
    main_busy = bool(ui_state.get("main_busy"))
    if main_busy:
        # Redact internal activity — Ghost must not leak "handoff continuation" etc.
        activity = str(ui_state.get("activity") or "working").strip()
        if "handoff" in activity.lower():
            lines.append("Main MO: busy (working)")
        elif "tooling" in activity.lower():
            lines.append("Main MO: busy (using tools)")
        elif "thinking" in activity.lower():
            lines.append("Main MO: busy (thinking)")
        else:
            lines.append("Main MO: busy (working)")
    else:
        lines.append("Main MO: idle")

    queued_count = int(ui_state.get("queued_count") or 0)
    if queued_count:
        lines.append(f"Queued user inputs: {queued_count}")

    goal_active = bool(ui_state.get("goal_worker_active") or getattr(agent, "_goal_active", False))
    if goal_active:
        stage = str(ui_state.get("goal_stage") or "running").strip() or "running"
        placement = "background" if ui_state.get("goal_backgrounded") else "foreground/visible"
        elapsed = str(ui_state.get("goal_elapsed") or "").strip()
        suffix = f" · {elapsed}" if elapsed else ""
        lines.append(f"Background MO worker/goal: active ({placement}, {stage}{suffix})")
    elif ui_state.get("goal_queued"):
        lines.append("Background MO worker/goal: queued")
    else:
        lines.append("Background MO worker/goal: idle")

    return "\n".join(lines)


def _task_board_text(gateway: Any | None, ui_state: dict[str, Any], *, session_id: str = "") -> str:
    board_text = str(ui_state.get("board_text") or "").strip()
    if board_text:
        return board_text
    board = getattr(gateway, "last_task_board", None) if gateway is not None else None
    if board:
        try:
            return compile_board_context(board, max_chars=900)["text"]
        except Exception:
            return ""
    recent = read_recent_snapshots(limit=1, session_id=session_id) if session_id else []
    if recent:
        return compile_board_context_from_snapshot(recent[-1], max_chars=900)["text"]
    return ""


def _task_board_snapshot_text(snapshot: dict[str, Any]) -> str:
    tasks = list(snapshot.get("tasks") or [])
    if not tasks:
        return ""
    completed = sum(1 for task in tasks if task.get("status") == "completed")
    open_count = sum(1 for task in tasks if task.get("status") in {"pending", "active", "blocked"})
    lines = [f"Last recorded task board: {len(tasks)} tasks ({completed} done, {open_count} open) · ledger {snapshot.get('event', 'updated')}/{snapshot.get('state', 'active')}"]
    for task in tasks[:8]:
        status = str(task.get("status") or "pending")
        marker = status_marker(status)
        suffix = f" — {task.get('blocker')}" if task.get("blocker") else ""
        lines.append(f"{marker} {task.get('title', 'task')}{suffix}")
    if len(tasks) > 8:
        lines.append(f"… {len(tasks) - 8} more task(s)")
    return "\n".join(lines)


def _goal_text(agent: Any) -> str:
    rows = goal_summary_lines(agent, limit=8)
    if not rows:
        return ""
    out: list[str] = []
    for row in rows:
        if row.startswith("objective: "):
            out.append("Objective: " + row.split(": ", 1)[1])
        elif row.startswith("state: "):
            out.append("State: " + row.split(": ", 1)[1])
        elif row.startswith("stop reason: "):
            out.append("Stop reason: " + row.split(": ", 1)[1])
        else:
            out.append("- " + row)
    return "\n".join(out)


def _profile_text(agent: Any) -> str:
    profile = getattr(agent, "profile", None)
    if not profile:
        return ""
    try:
        if hasattr(profile, "build_profile_context"):
            return redact_monitor_text(profile.build_profile_context(max_chars=900), 1000)
        if hasattr(profile, "render"):
            return redact_monitor_text(profile.render(), 700)
    except Exception:
        return ""
    return ""


def _recent_session_text(agent: Any) -> str:
    session = getattr(agent, "session", None)
    messages = list(getattr(session, "messages", []) or [])[-6:]
    lines: list[str] = []
    for msg in messages:
        role = str(msg.get("role") or "?")
        if role == "system":
            continue
        if role == "tool":
            content = msg.get("content") or ""
            lines.append(f"tool: [tool result chars={len(str(content))}]")
            continue
        if msg.get("tool_calls"):
            names = tool_call_names(msg.get("tool_calls"))
            lines.append(f"assistant: [tool calls: {', '.join(names)}]")
            continue
        content = redact_monitor_text(msg.get("content") or "", 240).replace("\n", " ")
        if content:
            lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _code_map_text(question: str) -> str:
    if not should_include_code_graph_context(question):
        return ""
    try:
        text = build_code_graph_context(question, max_chars=800, max_nodes=4)
    except Exception:
        return ""
    if not text:
        return ""
    return text.replace("### MO Internal Code Map - orientation only\n", "")


def _recent_monitor_text(gateway: Any | None) -> str:
    monitor = getattr(gateway, "monitor", None) if gateway is not None else None
    path = getattr(monitor, "path", None)
    if not path:
        return ""
    try:
        p = Path(path)
        raw_lines = p.read_text(encoding="utf-8", errors="replace").splitlines()[-14:]
    except Exception:
        return ""

    events: list[str] = []
    for raw in raw_lines:
        try:
            event = json.loads(raw)
        except Exception:
            continue
        etype = str(event.get("type") or "event")
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {"message": str(payload)}
        summary = _safe_event_summary(payload)
        if summary:
            events.append(f"{etype}: {summary}")
        else:
            events.append(etype)
    return "\n".join(events[-8:])


def _safe_event_summary(payload: dict[str, Any]) -> str:
    keys = ("message", "tool", "reason", "preview", "title", "status", "state", "error")
    parts: list[str] = []
    for key in keys:
        value = payload.get(key)
        if value:
            parts.append(f"{key}={redact_monitor_text(value, 180).replace(chr(10), ' ')}")
    return "; ".join(parts)[:360]


def _routing_guidance(question: str, worker_state: str, *, status_question: bool = False) -> str:
    q = str(question or "").lower()
    risky = bool(re.search(r"\b(commit|push|deploy|deployment|production|prod|server|secret|credential|token|destructive|delete|rm\s+-rf)\b", q))
    if status_question:
        return "\n".join([
            "Answer status questions from visible worker/task/goal state only.",
            "It is okay to mention busy, queued, running, or idle when the user directly asks about status.",
            "Do not infer hidden provider/tool details; say what is unknown if it is not visible.",
        ])
    lines = [
        "Ghost is the user's coordinator and sanity-checker; Ghost does not execute tools directly, but may receive an audited read-only tool scout.",
        "Keep your messages highly concise so they fit comfortably inside your small side-panel UI.",
        "If work needs edits/tests/final proof, suggest routing it through MO/Gateway. If the user already requested the work or asked you to route it, execute the route immediately without asking for YES/NO confirmation.",
        "Balance options internally: use main MO now when idle; queue when context-sensitive; suggest background only for independent safe work.",
        "Never mention internal state to the user — no 'handoff', 'continuation', 'context pressure', 'busy', 'queue'. Just say 'let me route this to MO' or 'MO can handle this'.",
        "Never claim a route has started unless the visible app confirms it.",
    ]
    if risky:
        lines.append("This user question mentions a high-risk boundary; recommend explicit approval and main MO/Gateway handling, not background work.")
    elif "Main MO: busy" in worker_state:
        lines.append("Main MO is busy; prefer queueing context-sensitive work, or suggest background work only if it is independent and safe.")
    return "\n".join(lines)
