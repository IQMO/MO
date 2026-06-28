"""Deterministic current-work continuity for MO.

Continuity questions such as "what were we busy with?" must not be answered
from episodic memory alone. This module builds a small runtime snapshot from
local ledgers so the model can answer from current state first, then use memory
only as older orientation.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from ..state.paths import resolve_state_path
from ..tasking.task_board import read_recent_snapshots
from .heartbeat import read_recent_heartbeats
from .instance import get_instance_id


_CONTINUITY_RE = re.compile(
    r"(?i)\b("
    r"what\s+(?:were|are)\s+we\s+(?:busy\s+with|working\s+on|doing)"
    r"|where\s+(?:were|are)\s+we"
    r"|what\s+(?:was|is)\s+(?:the\s+)?(?:last|current)\s+(?:thing|task|work)"
    r"|what\s+did\s+we\s+(?:do|finish|work\s+on)"
    r"|current\s+(?:work|task|state)"
    r"|work\s+in\s+flight"
    r"|active\s+(?:work|task|taskboard)"
    r"|what\s+now"
    r"|continue\s+(?:where\s+we\s+left|from\s+last)"
    r"|resume\s+(?:last|previous|work)"
    r")\b"
)

_NEGATIVE_CONTINUITY_RE = re.compile(
    r"(?i)\b("
    r"nothing\s+(?:active|open|in\s+flight)"
    r"|no\s+(?:taskboard|work\s+in\s+flight|active\s+work|open\s+work|active\s+task)"
    r"|last\s+session\s+was\s+clean"
    r"|repo\s+is\s+clean"
    r")\b"
)


def looks_like_continuity_question(user_input: str) -> bool:
    """Return True for questions that ask MO to recover current/last work."""
    return bool(_CONTINUITY_RE.search(str(user_input or "")))


def build_current_work_snapshot(agent: Any = None) -> dict[str, Any]:
    """Build a fast, local, deterministic snapshot of current work state."""
    session = getattr(agent, "session", None)
    gateway = getattr(agent, "gateway", None)
    session_id = str(getattr(session, "session_id", "") or "")
    slot = str(getattr(getattr(agent, "_sessions", None), "current_name", "") or "")
    live_board = _live_board(agent, gateway=gateway, session_id=session_id)
    latest_board = _latest_taskboard_snapshot(session_id=session_id)
    latest_closeout = _latest_closeout()
    latest_trace = _latest_trace_summary()
    git = _git_summary(getattr(agent, "project_cwd", None) or os.getcwd())
    heartbeat = _latest_heartbeat()
    messages = list(getattr(session, "messages", []) or [])
    latest_user = _latest_role(messages, "user")
    recent_activity = bool(
        latest_user
        or _board_open_count(live_board) > 0
        or int((latest_board or {}).get("open") or 0) > 0
        or str((latest_closeout or {}).get("topic") or "").strip()
        or str((latest_closeout or {}).get("terminal_marker") or "").strip()
        or str((latest_trace or {}).get("name") or "").strip()
    )
    return {
        "current_session": {
            "session_id": session_id,
            "slot": slot,
            "turn_count": int(getattr(session, "turn_count", 0) or 0),
            "message_count": len(messages),
            "latest_user": latest_user,
        },
        "heartbeat": heartbeat,
        "live_taskboard": _taskboard_summary(live_board),
        "latest_taskboard": latest_board,
        "latest_closeout": latest_closeout,
        "latest_trace": latest_trace,
        "git": git,
        "recent_activity": recent_activity,
    }


def render_current_work_snapshot(snapshot: dict[str, Any] | None = None) -> str:
    """Render provider-facing continuity context."""
    snap = snapshot or build_current_work_snapshot()
    session = snap.get("current_session") or {}
    heartbeat = snap.get("heartbeat") or {}
    live_board = snap.get("live_taskboard") or {}
    latest_board = snap.get("latest_taskboard") or {}
    closeout = snap.get("latest_closeout") or {}
    trace = snap.get("latest_trace") or {}
    git = snap.get("git") or {}
    lines = [
        "### Current Work Snapshot - runtime truth for continuity questions",
        "Use this before episodic memory. Memory recall is older orientation, not current work truth.",
        "Do not answer 'nothing active' unless the live taskboard, latest closeout, heartbeat, and git lines below support it.",
        f"- Current session: slot {session.get('slot') or '?'}; turns {session.get('turn_count', 0)}; messages {session.get('message_count', 0)}.",
    ]
    if session.get("latest_user"):
        lines.append(f"- Latest user in this session: {_clip(session.get('latest_user'), 180)}")
    if heartbeat:
        lines.append(
            f"- Latest heartbeat: event {heartbeat.get('event') or '?'}; slot {heartbeat.get('slot') or '?'}; "
            f"turns {heartbeat.get('turn_count', 0)}; taskboard open {heartbeat.get('taskboard_open', 0)}."
        )
    lines.append(
        f"- Live taskboard: {live_board.get('state', 'none')}; total {live_board.get('total', 0)}, "
        f"open {live_board.get('open', 0)}, completed {live_board.get('completed', 0)}."
    )
    if latest_board:
        lines.append(
            f"- Latest taskboard ledger: {latest_board.get('state') or latest_board.get('event') or '?'}; "
            f"open {latest_board.get('open', 0)}; "
            f"title {_clip(latest_board.get('title') or latest_board.get('objective') or '', 140)}."
        )
    if closeout:
        closeout_line = (
            f"- Latest closeout: {closeout.get('reason') or '?'}; "
            f"{closeout.get('status') or '?'}; turns/messages "
            f"{closeout.get('turn_count', 0)}/{closeout.get('message_count', 0)}"
        )
        if closeout.get("topic"):
            closeout_line += f"; topic {_clip(closeout.get('topic'), 160)}"
        if closeout.get("terminal_marker"):
            closeout_line += f"; marker {closeout.get('terminal_marker')}"
        lines.append(closeout_line + ".")
        if int(closeout.get("turn_count") or 0) == 0 and int(closeout.get("message_count") or 0) > 0:
            lines.append("- Note: a 0-turn runtime closeout with messages is not an empty/no-work session.")
    if trace:
        validation = trace.get("validation") or ""
        suffix = f"; validation {validation}" if validation else ""
        lines.append(f"- Latest trace: {trace.get('name')}{suffix}.")
    if git:
        dirty = int(git.get("dirty_count") or 0)
        lines.append(f"- Git: branch {git.get('branch') or '?'}; dirty lines {dirty}.")
    lines.append("Answer continuity by naming open work first, then latest completed/closed work. Mention older memory only after these runtime facts.")
    return "\n".join(lines)


def render_current_work_status(agent: Any = None) -> str:
    """Human-facing /now output."""
    snap = build_current_work_snapshot(agent)
    session = snap.get("current_session") or {}
    heartbeat = snap.get("heartbeat") or {}
    live_board = snap.get("live_taskboard") or {}
    latest_board = snap.get("latest_taskboard") or {}
    closeout = snap.get("latest_closeout") or {}
    trace = snap.get("latest_trace") or {}
    git = snap.get("git") or {}
    lines = [
        "Current work snapshot:",
        f"  session: slot {session.get('slot') or '?'}; turns {session.get('turn_count', 0)}; messages {session.get('message_count', 0)}",
    ]
    if session.get("latest_user"):
        lines.append(f"  latest user: {_clip(session.get('latest_user'), 160)}")
    if heartbeat:
        lines.append(
            f"  heartbeat: {heartbeat.get('event') or '?'}; slot {heartbeat.get('slot') or '?'}; "
            f"taskboard open {heartbeat.get('taskboard_open', 0)}"
        )
    lines.append(
        f"  live board: {live_board.get('state', 'none')}; open {live_board.get('open', 0)}/{live_board.get('total', 0)}"
    )
    if latest_board:
        lines.append(
            f"  latest board ledger: open {latest_board.get('open', 0)}/{latest_board.get('total', 0)}; "
            f"{_clip(latest_board.get('title') or latest_board.get('objective') or '', 120)}"
        )
    if closeout:
        status = str(closeout.get("status") or "?")
        if status.lower() == "clean":
            status = "no unresolved markers"
        closeout_line = (
            f"  latest closeout: {closeout.get('reason') or '?'}; {status}; "
            f"turns/messages {closeout.get('turn_count', 0)}/{closeout.get('message_count', 0)}"
        )
        if closeout.get("topic"):
            closeout_line += f"; topic {_clip(closeout.get('topic'), 140)}"
        if closeout.get("terminal_marker"):
            closeout_line += f"; marker {closeout.get('terminal_marker')}"
        lines.append(closeout_line)
        if int(closeout.get("turn_count") or 0) == 0 and int(closeout.get("message_count") or 0) > 0:
            lines.append("  note: 0-turn closeouts with messages are recent activity, not empty history")
    if trace:
        validation = trace.get("validation") or ""
        suffix = f"; validation {validation}" if validation else ""
        lines.append(f"  latest trace: {trace.get('name')}{suffix}")
    if git:
        lines.append(f"  git: branch {git.get('branch') or '?'}; dirty lines {int(git.get('dirty_count') or 0)}")
    open_count = int(live_board.get("open") or 0) + int(latest_board.get("open") or 0)
    if open_count:
        lines.append(f"  verdict: {open_count} open runtime task row(s) visible")
    elif closeout or heartbeat or trace:
        lines.append("  verdict: no open taskboard row visible; recent runtime activity exists")
    else:
        lines.append("  verdict: no recent runtime activity found")
    return "\n".join(lines)


def continuity_gate_instruction(user_input: str, final_text: str, snapshot: dict[str, Any] | None) -> str | None:
    """Return a corrective instruction when a continuity answer skipped runtime truth."""
    if not looks_like_continuity_question(user_input):
        return None
    if not snapshot:
        return (
            "You are answering a continuity/current-work question without the Current Work Snapshot. "
            "Use runtime continuity state first: current session, heartbeat, taskboard ledger, latest closeout/session index, latest trace, and git. "
            "Then answer again; use episodic memory only as older orientation."
        )
    text = str(final_text or "")
    closeout = snapshot.get("latest_closeout") or {}
    live_board = snapshot.get("live_taskboard") or {}
    latest_board = snapshot.get("latest_taskboard") or {}
    topic = str(closeout.get("topic") or "").strip()
    marker = str(closeout.get("terminal_marker") or "").strip()
    turn_count = int(closeout.get("turn_count") or 0)
    message_count = int(closeout.get("message_count") or 0)
    open_count = int(live_board.get("open") or 0) + int(latest_board.get("open") or 0)

    if _NEGATIVE_CONTINUITY_RE.search(text):
        if open_count > 0:
            return _continuity_retry_text("You said there was no active work, but runtime taskboard state has open work.", snapshot)
        if topic and not _topic_words_present(text, topic):
            return _continuity_retry_text("You made a no-active-work claim without naming the latest runtime closeout topic.", snapshot)
        if marker and marker.lower() not in text.lower():
            return _continuity_retry_text("You made a no-active-work claim without naming the latest terminal closeout marker.", snapshot)
        if turn_count == 0 and message_count > 0 and re.search(r"(?i)\b0\s+turns\b", text):
            return _continuity_retry_text("You treated a 0-turn runtime closeout with messages as empty work.", snapshot)
    if topic and "recalled" in text.lower() and not _topic_words_present(text, topic):
        return _continuity_retry_text("You cited recalled older interactions but skipped the latest runtime closeout topic.", snapshot)
    return None


def _continuity_retry_text(reason: str, snapshot: dict[str, Any]) -> str:
    return (
        f"{reason} Correct the answer using this runtime snapshot first:\n\n"
        f"{render_current_work_snapshot(snapshot)}\n\n"
        "Answer briefly. Separate 'open now' from 'latest completed/closed work'. "
        "Do not use older recalled memory as the lead unless the snapshot has no relevant current/recent work."
    )


def _live_board(agent: Any, *, gateway: Any = None, session_id: str = "") -> Any | None:
    for board in (getattr(gateway, "last_task_board", None), getattr(agent, "_active_task_board", None)):
        if board is None:
            continue
        board_session = str(getattr(board, "session_id", "") or "")
        if session_id and board_session and board_session != session_id:
            continue
        return board
    return None


def _taskboard_summary(board: Any | None) -> dict[str, Any]:
    if board is None:
        return {"state": "none", "total": 0, "open": 0, "completed": 0}
    tasks = list(getattr(board, "tasks", []) or [])
    open_count = _board_open_count(board)
    return {
        "state": "active" if open_count else "completed" if tasks else "empty",
        "title": str(getattr(board, "title", "") or ""),
        "session_id": str(getattr(board, "session_id", "") or ""),
        "total": len(tasks),
        "open": open_count,
        "completed": sum(1 for task in tasks if getattr(task, "status", "") == "completed"),
    }


def _board_open_count(board: Any | None) -> int:
    if board is None:
        return 0
    try:
        return int(board.open_count())
    except Exception:
        return sum(1 for task in list(getattr(board, "tasks", []) or []) if getattr(task, "status", "") in {"pending", "active", "blocked"})


def _latest_taskboard_snapshot(*, session_id: str = "") -> dict[str, Any]:
    try:
        recent = read_recent_snapshots(limit=12, session_id=session_id) if session_id else []
        if not recent:
            recent = read_recent_snapshots(limit=12)
    except Exception:
        return {}
    if not recent:
        return {}
    candidates = recent
    open_items = [item for item in candidates if _snapshot_open_count(item) > 0]
    chosen = open_items[-1] if open_items else candidates[-1]
    tasks = list(chosen.get("tasks") or [])
    return {
        "event": str(chosen.get("event") or ""),
        "state": str(chosen.get("state") or ""),
        "session_id": str(chosen.get("session_id") or ""),
        "title": str(chosen.get("title") or ""),
        "objective": str(chosen.get("objective") or ""),
        "total": len(tasks),
        "open": _snapshot_open_count(chosen),
        "completed": sum(1 for task in tasks if isinstance(task, dict) and task.get("status") == "completed"),
    }


def _snapshot_open_count(item: dict[str, Any]) -> int:
    return sum(
        1
        for task in list(item.get("tasks") or [])
        if isinstance(task, dict) and task.get("status") in {"pending", "active", "blocked"}
    )


def _latest_heartbeat() -> dict[str, Any]:
    try:
        rows = read_recent_heartbeats(limit=1, instance_id=get_instance_id())
    except Exception:
        rows = []
    if not rows:
        return {}
    item = rows[-1]
    taskboard = item.get("taskboard") if isinstance(item.get("taskboard"), dict) else {}
    return {
        "event": str(item.get("event") or ""),
        "surface": str(item.get("surface") or ""),
        "session_id": str(item.get("session_id") or ""),
        "slot": str(item.get("slot") or ""),
        "turn_count": int(item.get("turn_count") or 0),
        "message_count": int(item.get("message_count") or 0),
        "taskboard_open": int(taskboard.get("open") or 0),
    }


def _latest_closeout() -> dict[str, Any]:
    root = Path(resolve_state_path("memory/session_closeouts"))
    if not root.exists():
        return {}
    files = sorted((path for path in root.glob("*.md") if path.is_file()), key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    if not files:
        return {}
    path = files[0]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return {}
    return _parse_closeout_markdown(text, path)


def _parse_closeout_markdown(text: str, path: Path) -> dict[str, Any]:
    reason = ""
    status = ""
    turn_count = 0
    message_count = 0
    topic = ""
    terminal_marker = ""
    spine: list[str] = []
    in_spine = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Reason: "):
            reason = stripped[8:].strip()
        elif stripped.startswith("- Status: "):
            status = stripped[10:].strip()
        elif stripped.startswith("- Turns/messages: "):
            parts = stripped[17:].split("/", 1)
            try:
                turn_count = int(parts[0].strip())
            except Exception:
                turn_count = 0
            try:
                message_count = int(parts[1].strip()) if len(parts) > 1 else 0
            except Exception:
                message_count = 0
        elif stripped == "## Conversation topic / recent spine":
            in_spine = True
            continue
        elif stripped.startswith("## "):
            in_spine = False
        if in_spine and stripped.startswith("- "):
            entry = stripped[2:].strip()
            if entry and not entry.lower().startswith("spine "):
                spine.append(entry)
    for entry in spine:
        if entry.lower().startswith("user:"):
            topic = entry[5:].strip()
    for entry in spine:
        marker_match = re.search(r"\[([A-Z_]+ (?:COMPLETE|BLOCKED))\]", entry)
        if marker_match:
            terminal_marker = marker_match.group(1)
            break
    return {
        "path": str(path),
        "age_hours": round((time.time() - path.stat().st_mtime) / 3600.0, 2),
        "reason": reason,
        "status": status,
        "turn_count": turn_count,
        "message_count": message_count,
        "topic": topic,
        "terminal_marker": terminal_marker,
    }


def _latest_trace_summary() -> dict[str, Any]:
    root = Path(resolve_state_path("memory/traces"))
    if not root.exists():
        return {}
    files = sorted((path for path in root.glob("*.trace") if path.is_file()), key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    if not files:
        return {}
    path = files[0]
    out = {"name": path.stem, "age_hours": round((time.time() - path.stat().st_mtime) / 3600.0, 2)}
    try:
        if path.stat().st_size <= 15_000_000:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            report = data.get("validation") if isinstance(data, dict) else None
            if isinstance(report, list):
                failed = [_public_trace_label(str(row.get("name") or "")) for row in report if isinstance(row, dict) and not row.get("passed")]
                out["validation"] = "clean" if not failed else "failed: " + ", ".join(failed[:4])
    except Exception:
        pass
    return out


def _git_summary(cwd: str) -> dict[str, Any]:
    try:
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=3,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "status", "--short"],
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=3,
        ).stdout.splitlines()
        return {"branch": branch, "dirty_count": len([line for line in status if line.strip()])}
    except Exception:
        return {}


def _latest_role(messages: list[dict[str, Any]], role: str) -> str:
    for item in reversed(messages):
        if item.get("role") == role:
            return _clip(str(item.get("content") or ""), 300)
    return ""


def _public_trace_label(value: str) -> str:
    return re.sub(r"(?i)\bsession\s+clean\b", "Session state", str(value or "")).strip()


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\r", "\n").split())
    return text[: max(0, limit - 1)].rstrip() + ("..." if len(text) > limit else "")


def _topic_words_present(text: str, topic: str) -> bool:
    final_words = {word.lower() for word in re.findall(r"[A-Za-z0-9_]{4,}", text)}
    topic_words = [word.lower() for word in re.findall(r"[A-Za-z0-9_]{4,}", topic)]
    if not topic_words:
        return True
    important = [word for word in topic_words if word not in {"start", "what", "were", "with", "this", "that"}]
    important = important or topic_words
    return any(word in final_words for word in important[:5])
