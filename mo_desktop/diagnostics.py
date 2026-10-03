"""Joined, redacted diagnostics for MO Desktop runtime traces.

MO Desktop evidence is intentionally split by ownership:

- ``mo_desktop.log`` owns lifecycle and exception lines.
- ``mo-desktop.json`` owns the isolated desktop conversation snapshot.
- ``provider_audit.jsonl`` owns provider/model switches and handoffs.
- ``tool_audit.jsonl`` owns sandboxed actions and blocked tool calls.

This module reads those existing private-state files and renders one compact
operator-facing report. It does not create a second transcript or change task
truth.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.diagnostics.surface_trace import build_surface_trace, trace_source_info
from core.state.everywhere_coordinator import COORDINATOR_STATUS, read_coordinator_status
from core.state.paths import resolve_state_path
from core.tooling.sandbox import redact_sensitive_text
from mo_desktop.desktop_log import ready_metadata

from mo_desktop.companion_session import (
    MO_DESKTOP_SESSION_SLOT as DESKTOP_SESSION_SLOT,
)
_ISSUE_WORDS = (
    "error",
    "exception",
    "traceback",
    "failed",
    "fail",
    "timeout",
    "blocked",
    "denied",
    "abort",
    "crash",
    "warning",
    "cannot",
    "unable",
)


def build_mo_desktop_trace(
    config: dict[str, Any] | None = None, *, limit: int = 12, session_id: str = "", turn_id: str = "",
) -> dict[str, Any]:
    """Join a requested turn (or the latest saved reply) with bounded evidence."""
    cfg = config if isinstance(config, dict) else {}
    event_limit = max(1, min(int(limit or 12), 40))
    paths = _source_paths(cfg)
    desktop_log_lines = _tail_lines(paths["desktop_log"], 220)
    session_slot = DESKTOP_SESSION_SLOT
    shared = build_surface_trace(
        cfg,
        surface="mo_desktop",
        session_slot=session_slot,
        session_id=session_id,
        turn_id=turn_id,
        limit=event_limit,
    )
    desktop_log_issue_lines = _unrecovered_lifecycle_issues(
        _latest_lifecycle(desktop_log_lines)
    )
    snapshot = dict(shared)
    snapshot["resident"] = ready_metadata(cfg)
    snapshot["sources"] = {
        "desktop_log": trace_source_info(paths["desktop_log"]),
        "everywhere_status": trace_source_info(paths["everywhere_status"]),
        **(shared.get("sources") or {}),
    }
    snapshot["desktop_log_tail"] = [_clean_line(line) for line in desktop_log_lines[-event_limit:]]
    snapshot["desktop_log_issues"] = [_clean_line(line) for line in desktop_log_issue_lines[-event_limit:]]
    coordinator = read_coordinator_status(cfg)
    profile = coordinator.get("profile") if isinstance(coordinator.get("profile"), dict) else {}
    profile_state = str(profile.get("state") or "unknown")
    profile_detail = _safe_text(profile.get("detail"), 300)
    snapshot["everywhere_sync"] = {
        "state": profile_state,
        "detail": profile_detail,
        "checked_at": profile.get("checked_at") or coordinator.get("created_at"),
    }
    if profile_state in {"blocked", "error"}:
        issue = f"Everywhere profile sync {profile_state}"
        if profile_detail:
            issue += f": {profile_detail}"
        snapshot["desktop_log_issues"].append(issue)
        snapshot["desktop_log_issues"] = snapshot["desktop_log_issues"][-event_limit:]
    completeness = shared.get("completeness") if isinstance(shared.get("completeness"), dict) else {}
    notes = list(completeness.get("notes") or [])
    if not desktop_log_lines:
        notes.append("runtime lifecycle log is missing or empty")
    if snapshot["resident"].get("state") == "stale":
        notes.append("resident MO Desktop is running older source than the active checkout")
    snapshot["completeness"] = {"notes": notes}
    return snapshot


def build_mo_desktop_trace_report(
    config: dict[str, Any] | None = None, *, limit: int = 12, session_id: str = "", turn_id: str = "",
) -> str:
    """Render the joined MO Desktop trace as plain text."""
    return render_mo_desktop_trace(build_mo_desktop_trace(config, limit=limit, session_id=session_id, turn_id=turn_id))


def render_mo_desktop_trace(snapshot: dict[str, Any]) -> str:
    sources = snapshot.get("sources") if isinstance(snapshot.get("sources"), dict) else {}
    session = snapshot.get("session") if isinstance(snapshot.get("session"), dict) else {}
    resident = snapshot.get("resident") if isinstance(snapshot.get("resident"), dict) else {}
    lines = ["MO Desktop trace"]
    requested = snapshot.get("requested_turn") or {}
    if requested.get("turn_id"):
        lines.append(f"Selected session={_safe_text(requested.get('session_id'), 100)} turn={_safe_text(requested.get('turn_id'), 100)}")
    else:
        lines.append("Unpinned conversation history: reply turn metadata unavailable; snapshot save time is not turn time.")

    lines.append("")
    lines.append("Current resident (not necessarily the reported turn's process)")
    lines.append(f"- state: {resident.get('state') or 'unknown'}")
    if resident.get("pid"):
        lines.append(f"- pid: {resident['pid']}")
    if resident.get("started_at"):
        lines.append(f"- started: {_format_epoch(resident['started_at'])}")
    if resident.get("source_stamp"):
        lines.append(f"- resident source: {resident['source_stamp']}")
    if resident.get("checkout_stamp"):
        lines.append(f"- checkout source: {resident['checkout_stamp']}")

    lines.append("")
    lines.append("Sources")
    for key in ("desktop_log", "session", "provider_audit", "tool_audit", "everywhere_status"):
        info = sources.get(key) if isinstance(sources.get(key), dict) else {}
        status = "found" if info.get("exists") else "missing"
        details = []
        if info.get("updated"):
            details.append(str(info["updated"]))
        if info.get("bytes"):
            details.append(f"{info['bytes']} bytes")
        suffix = f" ({', '.join(details)})" if details else ""
        lines.append(f"- {key}: {status} | {info.get('path', '')}{suffix}")

    lines.append("")
    lines.append("Session")
    if session.get("exists"):
        lines.append(
            f"- slot: {session.get('name') or DESKTOP_SESSION_SLOT} | "
            f"session_id: {session.get('session_id') or 'unknown'} | "
            f"turns: {session.get('turn_count', 0)} | "
            f"messages: {session.get('message_count', 0)}"
        )
        if session.get("saved_at"):
            lines.append(f"- saved: {session['saved_at']}")
        pending = session.get("pending_interrupted_work")
        if isinstance(pending, dict) and pending.get("exists"):
            lines.append(
                f"- pending interrupted work: {pending.get('reason') or 'unknown'} | "
                f"dropped {pending.get('dropped_messages', 0)} message(s)"
            )
            if pending.get("user"):
                lines.append(f"- last interrupted request: {pending['user']}")
    else:
        lines.append("- no saved MO Desktop session snapshot found")

    _append_section(lines, "Conversation Tail", snapshot.get("conversation_tail"))
    _append_section(lines, "Current lifecycle log (not selected-turn evidence)", snapshot.get("desktop_log_tail"))
    _append_section(lines, "Current runtime issues", snapshot.get("desktop_log_issues"), empty="none in the sampled log tail")
    sync = snapshot.get("everywhere_sync") if isinstance(snapshot.get("everywhere_sync"), dict) else {}
    lines.append("")
    lines.append("Everywhere Profile Sync")
    lines.append(f"- state: {sync.get('state') or 'unknown'}")
    if sync.get("checked_at"):
        lines.append(f"- checked: {_format_epoch(sync['checked_at'])}")
    if sync.get("detail"):
        lines.append(f"- detail: {sync['detail']}")
    _append_section(lines, "Turn Lifecycle", snapshot.get("backend_events"))
    _append_section(lines, "Heartbeat Events", snapshot.get("heartbeat_events"))
    _append_section(lines, "Provider Events", snapshot.get("provider_events"))
    _append_section(lines, "Tool Events", snapshot.get("tool_events"))
    _append_section(lines, "Blocked Tools", snapshot.get("blocked_tools"), empty="none in the sampled tool audit")

    completeness = snapshot.get("completeness") if isinstance(snapshot.get("completeness"), dict) else {}
    lines.append("")
    lines.append("Trace Completeness")
    for item in completeness.get("notes", []) or []:
        lines.append(f"- {item}")
    return "\n".join(lines).rstrip()


def _source_paths(config: dict[str, Any]) -> dict[str, Path]:
    return {
        "desktop_log": Path(resolve_state_path("logs/mo_desktop.log", config)),
        "everywhere_status": Path(resolve_state_path(COORDINATOR_STATUS, config)),
    }


def _tail_lines(path: Path, limit: int) -> list[str]:
    try:
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, limit):]
    except Exception:
        return []


def _append_section(lines: list[str], title: str, values: Any, *, empty: str = "none") -> None:
    lines.append("")
    lines.append(title)
    if isinstance(values, list) and values:
        for value in values:
            lines.append(f"- {value}")
    else:
        lines.append(f"- {empty}")


def _looks_issue(line: str) -> bool:
    low = str(line or "").lower()
    return any(word in low for word in _ISSUE_WORDS)


def _unrecovered_lifecycle_issues(lines: list[str]) -> list[str]:
    """Return issue lines whose sampled lifecycle never recorded recovery.

    Live Control reconnects are explicit lifecycle pairs. Reporting every earlier
    transient ``unavailable`` after a later ``connected`` makes a healthy resident
    look broken, while an unavailable line after the last recovery remains visible.
    """
    issues: list[str] = []
    live_unavailable_indexes: list[int] = []
    for line in lines:
        low = str(line or "").lower()
        if (
            "mo live control host connected" in low
            or "mo live control host stopped" in low
        ):
            if live_unavailable_indexes:
                remove = set(live_unavailable_indexes)
                issues = [item for index, item in enumerate(issues) if index not in remove]
                live_unavailable_indexes = []
            continue
        if not _looks_issue(line):
            continue
        if "mo live control host unavailable" in low:
            live_unavailable_indexes.append(len(issues))
        issues.append(line)
    return issues


def _latest_lifecycle(lines: list[str]) -> list[str]:
    for index in range(len(lines) - 1, -1, -1):
        if "desktop entrypoint starting" in lines[index].lower():
            return lines[index:]
    return lines


def _clean_line(line: str) -> str:
    return _safe_text(line, 320)


def _safe_text(value: Any, limit: int = 200) -> str:
    text = redact_sensitive_text(str(value or "")).replace("\r", " ").replace("\n", " ")
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: max(0, limit - 3)] + "..."
    return text


def _format_epoch(value: Any) -> str:
    import time

    try:
        return time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(float(value)))
    except (TypeError, ValueError, OSError, OverflowError):
        return "unknown"
