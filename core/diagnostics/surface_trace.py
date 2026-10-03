"""Joined, redacted, read-only diagnostics for MO conversation surfaces.

The runtime already owns the evidence. This module only joins bounded tails on
demand; it starts no service, writes no transcript, and never initializes a
missing database.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from ..runtime.backend_monitor import redact_monitor_text
from ..runtime.heartbeat import read_recent_heartbeats
from ..runtime.surface_identity import normalize_runtime_surface
from ..state.continuity_events import LOCAL_CONTINUITY_PATH, continuity_status_from_db
from ..state.everywhere_coordinator import read_coordinator_status
from ..state.paths import HEARTBEAT_LEDGER_PATH, SESSION_ROOT_DIR, resolve_state_path
from ..utils.jsonl_utils import read_recent_ledger_entries
from ..session.sessions import iter_conversation_session_paths, session_snapshot_path
from ..session.session import PRESENTATION_KEY
from ..tooling.sandbox import redact_sensitive_text


def build_surface_trace(
    config: dict[str, Any] | None = None,
    *,
    surface: str,
    session_slot: str = "",
    session_id: str = "",
    turn_id: str = "",
    live_session: Any = None,
    limit: int = 8,
) -> dict[str, Any]:
    """Join existing evidence for one surface without changing runtime state."""
    cfg = config if isinstance(config, dict) else {}
    wanted_surface = normalize_runtime_surface(surface)
    event_limit = max(1, min(int(limit or 8), 20))
    slot = _safe_slot(session_slot)
    if wanted_surface == "telegram" and not slot:
        slot = _latest_telegram_slot(cfg)
    session_path = _session_path(cfg, slot) if slot else None
    session = _live_session_dict(live_session, slot) or _load_json(session_path)
    loaded_session_id = str((session or {}).get("session_id") or "")
    if session_id and loaded_session_id != session_id:
        # Desktop archives already have a canonical exact-ID slot. Do not scan
        # unrelated transcripts or substitute today's conversation for that ID.
        archived_path = _session_path(cfg, f"{slot}-{session_id}") if slot else None
        archived = _load_json(archived_path)
        session = archived if str((archived or {}).get("session_id") or "") == session_id else None
        session_path = archived_path if session is not None else None
    session_id = session_id or loaded_session_id
    if wanted_surface == "mo_desktop" and not turn_id:
        # Default to the latest saved reply, not a mixture of this conversation's
        # historical processes. Missing metadata must not select an older reply.
        for message in reversed((session or {}).get("messages") or []):
            if isinstance(message, dict) and message.get("role") == "assistant" and not message.get("tool_calls"):
                presentation = message.get(PRESENTATION_KEY)
                if isinstance(presentation, dict) and presentation.get("session_id", session_id) == session_id:
                    turn_id = str(presentation.get("turn_id") or "")
                break

    provider_path = Path(resolve_state_path("logs/provider_audit.jsonl", cfg))
    tool_path = _tool_audit_path(cfg)
    heartbeat_path = Path(resolve_state_path(HEARTBEAT_LEDGER_PATH, cfg))
    provider_rows = [
        row for row in _jsonl_rows(provider_path, 800)
        if _surface_matches(row.get("surface"), wanted_surface)
        and _session_matches(row, session_id=session_id, slot=slot)
        and (not turn_id or str(row.get("turn_id") or row.get("user_turn_id") or "") == turn_id)
    ]
    tool_rows = [
        row for row in _jsonl_rows(tool_path, 1000)
        if _surface_matches(row.get("surface"), wanted_surface)
        and _session_matches(row, session_id=session_id, slot=slot)
        and (not turn_id or str(row.get("turn_id") or row.get("user_turn_id") or "") == turn_id)
    ]
    heartbeat_rows = [
        row for row in read_recent_heartbeats(limit=600, path=heartbeat_path)
        if _surface_matches(row.get("surface"), wanted_surface)
        and _session_matches(row, session_id=session_id, slot=slot)
        and (not turn_id or str(row.get("turn_id") or row.get("user_turn_id") or "") == turn_id)
    ]
    monitor_rows = []
    for row in _monitor_rows(cfg, scan_files=12, scan_lines=400):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if _surface_matches(payload.get("surface"), wanted_surface) and _session_matches(
            payload, session_id=session_id, slot=slot
        ) and (not turn_id or str(payload.get("turn_id") or payload.get("user_turn_id") or "") == turn_id):
            monitor_rows.append(row)

    return {
        "surface": wanted_surface,
        "requested_turn": {"session_id": session_id, "turn_id": turn_id},
        "display_slot": _display_slot(slot, wanted_surface),
        "sources": {
            "session": _source_info(session_path),
            "provider_audit": _source_info(provider_path),
            "tool_audit": _source_info(tool_path),
            "heartbeat": _source_info(heartbeat_path),
            "backend_monitor": {
                "exists": bool(monitor_rows),
                "sampled": len(monitor_rows),
            },
        },
        "session": _session_summary(session),
        "conversation_tail": _conversation_tail(session, event_limit, turn_id=turn_id),
        "provider_events": [_provider_summary(row) for row in provider_rows[-event_limit:]],
        "tool_events": [_tool_summary(row) for row in tool_rows[-event_limit:]],
        "blocked_tools": [_tool_summary(row) for row in tool_rows if row.get("blocked")][-event_limit:],
        "heartbeat_events": [_heartbeat_summary(row) for row in heartbeat_rows[-event_limit:]],
        "backend_events": [_monitor_summary(row) for row in monitor_rows[-event_limit:]],
        "completeness": _surface_completeness(
            session,
            provider_rows,
            tool_rows,
            heartbeat_rows,
            monitor_rows,
            wanted_surface,
        ),
    }


def build_telegram_trace_report(
    config: dict[str, Any] | None = None,
    *,
    session_slot: str = "",
    live_session: Any = None,
    limit: int = 8,
) -> str:
    cfg = config if isinstance(config, dict) else {}
    snapshot = build_surface_trace(
        cfg,
        surface="telegram",
        session_slot=session_slot,
        live_session=live_session,
        limit=limit,
    )
    report = render_surface_trace(snapshot, title="MO Telegram trace")
    remote = _remote_surface_continuity(cfg, "telegram")
    if not snapshot.get("session", {}).get("exists") and remote.get("count"):
        report += (
            "\n\nCross-device boundary\n"
            f"- this device has {remote['count']} bounded Telegram continuity outcome(s); "
            f"latest {_format_ts(remote.get('latest')) or 'unknown time'}\n"
            "- full Telegram transcript/provider/tool diagnostics stay on the serving device; "
            "run /telegram trace there, and use /everywhere trace here for delivery and binding evidence"
        )
    return report


def render_surface_trace(snapshot: dict[str, Any], *, title: str = "MO surface trace") -> str:
    """Render a compact report suitable for both terminal and Telegram."""
    sources = snapshot.get("sources") if isinstance(snapshot.get("sources"), dict) else {}
    session = snapshot.get("session") if isinstance(snapshot.get("session"), dict) else {}
    lines = [title, "", "Evidence sources"]
    requested = snapshot.get("requested_turn") or {}
    if requested.get("turn_id"):
        lines.insert(1, f"Requested session={_safe_text(requested.get('session_id'), 100)} turn={_safe_text(requested.get('turn_id'), 100)}")
    for key in ("session", "provider_audit", "tool_audit", "heartbeat", "backend_monitor"):
        info = sources.get(key) if isinstance(sources.get(key), dict) else {}
        state = "found" if info.get("exists") else "missing"
        sampled = f"; {info.get('sampled')} matching event(s)" if info.get("sampled") is not None else ""
        lines.append(f"- {key}: {state}{sampled}")

    lines.extend(["", "Session"])
    if session.get("exists"):
        lines.append(
            f"- slot: {snapshot.get('display_slot') or 'unknown'} | "
            f"session_id: {_safe_text(session.get('session_id'), 100) or 'unknown'} | "
            f"turns: {session.get('turn_count', 0)} | messages: {session.get('message_count', 0)}"
        )
        if session.get("saved_at"):
            lines.append(f"- saved: {session['saved_at']}")
        pending = session.get("pending_interrupted_work")
        if isinstance(pending, dict) and pending.get("exists"):
            lines.append(
                f"- pending interrupted work: {pending.get('reason') or 'unknown'} | "
                f"dropped {pending.get('dropped_messages', 0)} message(s)"
            )
    else:
        lines.append("- no saved session snapshot found")

    _append_section(lines, "Conversation tail", snapshot.get("conversation_tail"))
    _append_section(lines, "Turn lifecycle", snapshot.get("backend_events"))
    _append_section(lines, "Heartbeat events", snapshot.get("heartbeat_events"))
    _append_section(lines, "Provider events", snapshot.get("provider_events"))
    _append_section(lines, "Tool events", snapshot.get("tool_events"))
    _append_section(lines, "Blocked tools", snapshot.get("blocked_tools"), empty="none in sampled tool audit")
    completeness = snapshot.get("completeness") if isinstance(snapshot.get("completeness"), dict) else {}
    _append_section(lines, "Trace completeness", completeness.get("notes"), empty="no completeness note")
    return "\n".join(lines).rstrip()


def build_everywhere_trace_report(config: dict[str, Any] | None = None, *, limit: int = 8) -> str:
    """Render Everywhere delivery/binding/coordinator evidence, read-only."""
    cfg = config if isinstance(config, dict) else {}
    event_limit = max(1, min(int(limit or 8), 20))
    from ..state.everywhere_setup import render_everywhere_status

    status = render_everywhere_status(cfg)
    coordinator = read_coordinator_status(cfg)
    journal = _read_everywhere_journal(cfg, limit=event_limit)
    heartbeat_path = Path(resolve_state_path(HEARTBEAT_LEDGER_PATH, cfg))
    heartbeats = read_recent_heartbeats(limit=300, path=heartbeat_path)
    interactive = [
        row for row in heartbeats
        if str(row.get("event") or "") in {"turn_start", "turn_end"}
        or str(row.get("event") or "").startswith("auto_reply:")
    ][-event_limit:]

    lines = ["MO Everywhere trace", "", status, "", "Coordinator"]
    if coordinator:
        continuity = coordinator.get("continuity") if isinstance(coordinator.get("continuity"), dict) else {}
        profile = coordinator.get("profile") if isinstance(coordinator.get("profile"), dict) else {}
        lines.append(f"- sampled: {_format_ts(coordinator.get('created_at')) or 'unknown time'}")
        lines.append(
            f"- continuity: state={continuity.get('state', 'unknown')} "
            f"published={int(continuity.get('published') or 0)} received={int(continuity.get('received') or 0)} "
            f"pending={int(continuity.get('pending') or 0)}"
        )
        lines.append(
            f"- profile: state={profile.get('state', 'unknown')} "
            f"changed={bool(profile.get('changed'))} pulled={bool(profile.get('pulled'))} pushed={bool(profile.get('pushed'))}"
        )
    else:
        lines.append("- never run")

    lines.extend(["", "Continuity journal"])
    if journal.get("exists"):
        counts = journal.get("counts") if isinstance(journal.get("counts"), dict) else {}
        if counts:
            lines.append(
                f"- outbound={counts.get('outbound', 0)} inbound={counts.get('inbound', 0)} "
                f"pending={counts.get('pending', 0)} bindings={counts.get('bindings', 0)} cursors={counts.get('cursors', 0)}"
            )
            for binding in journal.get("bindings") or []:
                lines.append(f"- binding: {binding}")
            for event in journal.get("events") or []:
                lines.append(f"- {event}")
            if not journal.get("events"):
                lines.append("- no continuity events")
        else:
            lines.append(f"- {journal.get('state') or 'unreadable'}")
    else:
        lines.append(f"- {journal.get('state') or 'not initialized'}")

    _append_section(
        lines,
        "Recent conversational surfaces",
        [_heartbeat_summary(row) for row in interactive],
        empty="no conversational heartbeat events",
    )
    lines.extend([
        "",
        "Trace completeness",
        "- read-only snapshot; no coordinator cycle, profile sync, pairing, or database initialization was triggered",
    ])
    return "\n".join(lines).rstrip()


def _latest_telegram_slot(config: dict[str, Any]) -> str:
    db_path = Path(resolve_state_path("memory/surfaces/telegram.sqlite", config))
    if db_path.is_file():
        try:
            with _read_only_db(db_path) as db:
                row = db.execute(
                    "SELECT session_name FROM chat_sessions ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
            if row:
                slot = _safe_slot(row[0])
                if slot.startswith("telegram-"):
                    return slot
        except (OSError, sqlite3.Error, TypeError, ValueError):
            pass
    sessions_dir = Path(resolve_state_path(SESSION_ROOT_DIR, config))
    try:
        candidates = sorted(
            (path for path in iter_conversation_session_paths(sessions_dir) if path.name.startswith("telegram-")),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return _safe_slot(candidates[0].stem) if candidates else ""
    except OSError:
        return ""


def _read_everywhere_journal(config: dict[str, Any], *, limit: int) -> dict[str, Any]:
    path = Path(resolve_state_path(LOCAL_CONTINUITY_PATH, config))
    if not path.is_file():
        return {"exists": False, "state": "not initialized"}
    try:
        now = time.time()
        with _read_only_db(path) as db:
            journal_status = continuity_status_from_db(db, now=now)
            cursors = int(db.execute("SELECT COUNT(*) FROM continuity_cursor").fetchone()[0])
            binding_rows = db.execute(
                "SELECT surface,COUNT(*),MAX(updated_at) FROM continuity_binding GROUP BY surface ORDER BY surface"
            ).fetchall()
            rows = db.execute(
                """
                SELECT direction,source_surface,kind,status,created_at,delivered_at,intent,outcome
                FROM continuity_event WHERE expires_at>? ORDER BY created_at DESC,event_id DESC LIMIT ?
                """,
                (now, limit),
            ).fetchall()
        events = []
        for direction, surface, kind, status, created_at, delivered_at, intent, outcome in reversed(rows):
            delivery = "delivered" if delivered_at else "pending" if direction == "outbound" else "received"
            events.append(
                f"{_format_ts(created_at)} | {_safe_text(direction, 16)} {_safe_text(surface, 24)} "
                f"{_safe_text(kind, 20)}/{_safe_text(status, 20)} | {delivery} | "
                f"intent={_safe_text(intent, 100) or '-'} | outcome={_safe_text(outcome, 120) or '-'}"
            )
        return {
            "exists": True,
            "counts": {
                **journal_status,
                "cursors": cursors,
            },
            "events": events,
            "bindings": [
                f"{_safe_text(surface, 24) or 'unknown'} count={int(count)} updated={_format_ts(updated_at) or 'unknown'}"
                for surface, count, updated_at in binding_rows
            ],
        }
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        return {"exists": True, "state": f"unreadable ({type(exc).__name__})"}


def _remote_surface_continuity(config: dict[str, Any], surface: str) -> dict[str, Any]:
    """Return count/time only for remote outcomes already present locally."""
    path = Path(resolve_state_path(LOCAL_CONTINUITY_PATH, config))
    if not path.is_file():
        return {"count": 0, "latest": 0.0}
    try:
        with _read_only_db(path) as db:
            row = db.execute(
                """
                SELECT COUNT(*),MAX(created_at) FROM continuity_event
                WHERE direction='inbound' AND source_surface=? AND expires_at>?
                """,
                (normalize_runtime_surface(surface), time.time()),
            ).fetchone()
        return {"count": int(row[0] or 0), "latest": float(row[1] or 0.0)}
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return {"count": 0, "latest": 0.0}


def _read_only_db(path: Path) -> sqlite3.Connection:
    uri = path.resolve(strict=True).as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=1.0)
    connection.row_factory = sqlite3.Row
    return connection


def _session_path(config: dict[str, Any], slot: str) -> Path:
    return session_snapshot_path(resolve_state_path(SESSION_ROOT_DIR, config), _safe_slot(slot))


def _safe_slot(value: Any) -> str:
    return "".join(char for char in str(value or "") if char.isalnum() or char in "-_.")[:64]


def _display_slot(slot: str, surface: str) -> str:
    if not slot:
        return ""
    if surface == "telegram":
        digest = hashlib.sha256(slot.encode("utf-8", errors="replace")).hexdigest()[:10]
        return f"telegram-{digest}"
    return _safe_text(slot, 80)


def _live_session_dict(session: Any, slot: str) -> dict[str, Any] | None:
    if session is None:
        return None
    return {
        "name": slot,
        "session_id": str(getattr(session, "session_id", "") or ""),
        "turn_count": int(getattr(session, "turn_count", 0) or 0),
        "messages": list(getattr(session, "messages", []) or []),
        "saved_at": 0,
        "meta": getattr(session, "_loaded_meta", {}) if isinstance(getattr(session, "_loaded_meta", {}), dict) else {},
    }


def _load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _tool_audit_path(config: dict[str, Any]) -> Path:
    sandbox = config.get("sandbox") if isinstance(config.get("sandbox"), dict) else {}
    raw = str(sandbox.get("audit_log") or "").strip()
    if raw:
        candidate = Path(raw).expanduser()
        return candidate if candidate.is_absolute() else Path(resolve_state_path(raw, config))
    return Path(resolve_state_path("logs/tool_audit.jsonl", config))


def _monitor_rows(config: dict[str, Any], *, scan_files: int, scan_lines: int,
                  max_bytes: int = 512_000, session_id: str = "") -> list[dict[str, Any]]:
    from ..runtime.backend_monitor import get_monitor

    configured = str(os.environ.get("MO_BACKEND_MONITOR_DIR") or "").strip()
    monitor_dir = Path(configured) if configured else Path(resolve_state_path("logs/monitor", config))
    monitor = get_monitor()
    active = Path(monitor.path) if monitor is not None else None
    directories = {monitor_dir, active.parent} if active else {monitor_dir}
    # Traced terminal launches keep their monitor under the canonical trace
    # run. A Dashboard hosted by another process has no active-path pointer.
    directories.update(Path(resolve_state_path("logs/traces", config)).glob("*/monitor"))
    paths = {active} if active else set()
    for directory in directories:
        paths.update(directory.glob("backend_monitor-*.jsonl"))
    try:
        recent = sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    sampled = 0
    for path in recent:
        file_rows = _jsonl_rows(path, scan_lines, max_bytes=max_bytes)
        if session_id:
            file_rows = [row for row in file_rows if (row.get("payload") or {}).get("session_id") == session_id]
            # Other terminals and metadata-only session loads must not consume
            # the selected conversation's evidence-file budget.
            if not any(row.get("type") in {"turn_start", "turn_context", "provider_request"}
                       and (row.get("payload") or {}).get("turn_id") for row in file_rows):
                continue
        rows.extend(file_rows)
        sampled += 1
        if sampled >= scan_files:
            break
    return sorted(rows, key=lambda row: float(row.get("ts") or 0))


def build_check_evidence(config: dict[str, Any], *, session_id: str) -> dict[str, Any]:
    """Summarize recorded context, tools and checks for the latest sampled turn.

    This is on-demand historical inspection, not reusable verification proof.
    It never scans other conversations to substitute for missing evidence.
    """
    result: dict[str, Any] = {"state": "not observed", "checks": []}
    if not session_id:
        return result
    # Long tool turns can put their context receipt outside the normal trace
    # tail. This explicit inspection stays bounded, including across terminals.
    rows = _monitor_rows(config, scan_files=4, scan_lines=4000, max_bytes=4_000_000, session_id=session_id)
    latest = next((row for row in reversed(rows) if row.get("type") in {"turn_start", "turn_context", "provider_request"}
                   and (row.get("payload") or {}).get("turn_id")), None)
    turn = str(latest["payload"]["turn_id"]) if latest else ""
    if not turn:
        return result
    instance = str(latest["payload"].get("instance_id") or "")
    selected = [row for row in rows if (row.get("payload") or {}).get("turn_id") == turn
                and str(row["payload"].get("instance_id") or "") == instance]
    missing = "not recorded in sampled evidence"
    context = next((row["payload"] for row in reversed(selected) if row.get("type") == "turn_context"), {})
    result["checks"].extend([
        {"label": "Recorded turn", "value": f"session {_safe_text(session_id, 100)} · {_safe_text(turn, 100)} · instance {_safe_text(instance, 60) or 'not recorded'}"},
        {"label": "Latest evidence", "value": f"{_format_ts(selected[-1].get('ts')) or 'unknown time'} · local time · sampled records"},
    ])
    for label, key in (("Context supplied", "context_bridge_sources"), ("Context omitted", "context_bridge_omitted")):
        names = context.get(key)
        value = ", ".join(_safe_text(name, 60).replace("_", " ") for name in names) or "none recorded" if isinstance(names, list) else missing
        result["checks"].append({"label": label, "value": value})
    retrieved, delivered = context.get("memory_records_retrieved"), context.get("memory_records_delivered")
    recall = f"{delivered} of {retrieved} retrieved entries supplied" if isinstance(retrieved, int) and isinstance(delivered, int) else missing
    result["checks"].append({"label": "Memory recall", "value": recall})
    tool_counts: dict[tuple[str, str], int] = {}
    for row in selected:
        if row.get("type") != "tool_result":
            continue
        event = row["payload"]
        recorded_outcome = str(event.get("outcome") or "")
        outcome = {"success": "completed", "error": "failed", "blocked": "blocked", "rejected": "rejected"}.get(recorded_outcome)
        if outcome is None:
            outcome = ("outcome not recorded" if recorded_outcome else
                       "blocked" if event.get("blocked") else "failed" if event.get("error") else
                       "completed" if event.get("blocked") is False and event.get("error") is False else "outcome not recorded")
        key = (_safe_text(event.get("tool"), 60) or "unnamed tool", outcome)
        tool_counts[key] = tool_counts.get(key, 0) + 1
    result["checks"].append({"label": "Tool results", "value": "; ".join(
        f"{tool}: {count} {outcome}" for (tool, outcome), count in tool_counts.items()) or missing})
    labels = {
        "lsp_diagnostics": "LSP diagnostics", "affected_tests": "Affected tests",
        "security_check": "Security scan", "final_gates": "Final gates visited",
        "provider_retry": "Provider retries", "provider_fallback": "Provider fallbacks",
    }
    for event_type, label in labels.items():
        events = [row["payload"] for row in selected if row.get("type") == event_type]
        value = "not observed"
        if events:
            last = events[-1]
            if event_type == "lsp_diagnostics":
                counts: dict[str, int] = {}
                for event in events:
                    status = str(event.get("status") or "unknown")
                    counts[status] = counts.get(status, 0) + 1
                value = ", ".join(f"{_safe_text(status, 30)}: {count}" for status, count in counts.items()) + " (recorded checks, including rechecks)"
            elif event_type == "affected_tests":
                value = "passed" if last.get("passed") is True else "failed" if last.get("failed") else "incomplete"
                value += f" · {len(last.get('ran') or [])} named test file(s)"
            elif event_type == "final_gates":
                value = ", ".join(_safe_text(item, 60) for item in (last.get("fired") or [])[:16]) or "none recorded"
            else:
                value = f"{len(events)} event(s) recorded"
        result["checks"].append({"label": label, "value": value})
    result["state"] = "sampled from this conversation"
    return result


def _tail_lines(path: Path, limit: int, *, max_bytes: int = 512_000) -> list[str]:
    try:
        wanted = max(1, int(limit))
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            remaining = handle.tell()
            chunks: list[bytes] = []
            newline_count = 0
            while remaining > 0 and sum(len(chunk) for chunk in chunks) < max_bytes and newline_count <= wanted:
                size = min(64_000, remaining, max_bytes - sum(len(chunk) for chunk in chunks))
                remaining -= size
                handle.seek(remaining)
                chunk = handle.read(size)
                chunks.append(chunk)
                newline_count += chunk.count(b"\n")
        raw = b"".join(reversed(chunks)).decode("utf-8", errors="replace")
        return raw.splitlines()[-wanted:]
    except OSError:
        return []


def _jsonl_rows(path: Path, limit: int, *, max_bytes: int = 512_000) -> list[dict[str, Any]]:
    return read_recent_ledger_entries(_tail_lines(path, limit, max_bytes=max_bytes), limit)


def _surface_matches(value: Any, wanted: str) -> bool:
    return normalize_runtime_surface(str(value or "")) == wanted


def _session_matches(row: dict[str, Any], *, session_id: str, slot: str) -> bool:
    row_session = str(row.get("session_id") or "")
    row_slot = str(row.get("slot") or row.get("session_slot") or "")
    # A durable session id is stronger than a slot label.  When a trace targets
    # one known conversation, rows without either correlation field are not
    # evidence for it: accepting old process-level rows by surface alone can
    # make stale activity look current.  Fall back to the slot only when the row
    # actually carries one; otherwise fail closed and let completeness report
    # the missing evidence.
    if session_id:
        return row_session == session_id
    if slot:
        return bool(row_slot and row_slot == slot)
    return True


def _source_info(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"exists": False}
    try:
        exists = path.is_file()
        stat = path.stat() if exists else None
        return {
            "path": str(path),
            "exists": exists,
            "bytes": int(stat.st_size) if stat else 0,
            "updated": _format_ts(stat.st_mtime) if stat else "",
        }
    except OSError:
        return {"path": str(path), "exists": False}


def trace_source_info(path: str | Path | None) -> dict[str, Any]:
    """Public source metadata helper for specialized surface diagnostics."""
    return _source_info(Path(path) if path is not None else None)


def _session_summary(session: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(session, dict):
        return {"exists": False}
    meta = session.get("meta") if isinstance(session.get("meta"), dict) else {}
    pending = meta.get("pending_interrupted_work") if isinstance(meta.get("pending_interrupted_work"), dict) else {}
    return {
        "exists": True,
        "name": _safe_text(session.get("name"), 80),
        "session_id": _safe_text(session.get("session_id"), 120),
        "turn_count": int(session.get("turn_count") or 0),
        "message_count": len(session.get("messages") or []),
        "saved_at": _format_ts(session.get("saved_at")),
        "pending_interrupted_work": {
            "exists": bool(pending),
            "reason": _safe_text(pending.get("reason"), 100),
            "dropped_messages": int(pending.get("dropped_messages") or 0),
            "user": _safe_text(pending.get("user"), 220),
        },
    }


def _conversation_tail(session: dict[str, Any] | None, limit: int, *, turn_id: str = "") -> list[str]:
    if not isinstance(session, dict) or not isinstance(session.get("messages"), list):
        return []
    messages = session["messages"]
    if turn_id:
        matching = [index for index, message in enumerate(messages) if isinstance(message, dict)
                    and isinstance(message.get(PRESENTATION_KEY), dict)
                    and message[PRESENTATION_KEY].get("turn_id") == turn_id]
        if not matching:
            return []
        end = matching[-1] + 1
        start = matching[0]
        while start > 0 and messages[start].get("role") != "user":
            start -= 1
        messages = messages[start:end]
    out = []
    for message in messages[-limit:]:
        if not isinstance(message, dict):
            continue
        role = _safe_text(message.get("role"), 20) or "message"
        presentation = message.get(PRESENTATION_KEY)
        if isinstance(presentation, dict):
            anchor = [part for part in (
                _format_ts(presentation.get("started_at")),
                f"turn={_safe_text(presentation['turn_id'], 100)}" if presentation.get("turn_id") else "",
                f"instance={_safe_text(presentation['instance_id'], 80)}" if presentation.get("instance_id") else "",
            ) if part]
            if anchor:
                role += " [" + " | ".join(anchor) + "]"
        content = _flatten_content(message.get("content"))
        if not content and isinstance(message.get("tool_calls"), list):
            content = f"tool_calls={len(message['tool_calls'])}"
        out.append(f"{role}: {content or '[empty]'}")
    return out


def _provider_summary(row: dict[str, Any]) -> str:
    parts = [_format_ts(row.get("ts")), _safe_text(row.get("event"), 60) or "provider_event"]
    provider = f"{_safe_text(row.get('provider'), 50)}/{_safe_text(row.get('model'), 80)}".strip("/")
    if provider:
        parts.append(provider)
    if row.get("from_provider") or row.get("to_provider"):
        parts.append(
            f"{_safe_text(row.get('from_provider'), 40)}/{_safe_text(row.get('from_model'), 60)}"
            f" -> {_safe_text(row.get('to_provider'), 40)}/{_safe_text(row.get('to_model'), 60)}"
        )
    if row.get("ok") is not None:
        parts.append(f"ok={bool(row.get('ok'))}")
    if row.get("reason"):
        parts.append(f"reason={_safe_text(row.get('reason'), 100)}")
    return " | ".join(part for part in parts if part)


def _tool_summary(row: dict[str, Any]) -> str:
    parts = [_format_ts(row.get("ts")), _safe_text(row.get("tool"), 60) or "tool"]
    arguments = row.get("arguments") if isinstance(row.get("arguments"), dict) else {}
    if arguments:
        summary = ", ".join(f"{_safe_text(key, 30)}={_safe_text(arguments[key], 55)}" for key in sorted(arguments))
        parts.append(summary[:180])
    if row.get("blocked"):
        parts.append("blocked")
    if row.get("block_reason"):
        parts.append(_safe_text(row.get("block_reason"), 120))
    return " | ".join(part for part in parts if part)


def _heartbeat_summary(row: dict[str, Any]) -> str:
    task = row.get("taskboard") if isinstance(row.get("taskboard"), dict) else {}
    parts = [
        _format_ts(row.get("created_at")),
        _safe_text(row.get("surface"), 24),
        _safe_text(row.get("event"), 50) or "heartbeat",
        f"turns={int(row.get('turn_count') or 0)} messages={int(row.get('message_count') or 0)}",
    ]
    if task.get("open"):
        parts.append(f"board_open={int(task.get('open') or 0)}")
    return " | ".join(part for part in parts if part)


def _monitor_summary(row: dict[str, Any]) -> str:
    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
    parts = [_format_ts(row.get("ts")), _safe_text(row.get("type"), 50) or "event"]
    for key in ("status", "tool", "provider", "model", "error_type"):
        if payload.get(key) not in (None, ""):
            parts.append(f"{key}={_safe_text(payload.get(key), 70)}")
    if payload.get("error_excerpt") not in (None, ""):
        parts.append(f"error={_safe_text(payload.get('error_excerpt'), 220)}")
    if payload.get("duration_ms") is not None:
        parts.append(f"duration_ms={int(payload.get('duration_ms') or 0)}")
    return " | ".join(part for part in parts if part)


def _surface_completeness(
    session: dict[str, Any] | None,
    providers: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    heartbeats: list[dict[str, Any]],
    monitor: list[dict[str, Any]],
    surface: str,
) -> dict[str, Any]:
    notes = []
    if not session:
        notes.append(f"no saved {surface} session snapshot")
    else:
        pending = ((session.get("meta") or {}).get("pending_interrupted_work") or {})
        if isinstance(pending, dict) and pending:
            notes.append("conversation snapshot was cleaned after unfinished tool work; audit rows preserve the diagnostic trail")
    if not providers:
        notes.append(f"no sampled provider events for surface={surface}")
    if not tools:
        notes.append(f"no sampled tool events for surface={surface}")
    if not heartbeats:
        notes.append(f"no sampled heartbeat events for surface={surface}")
    if not monitor:
        notes.append(f"no sampled backend monitor events for surface={surface}")
    if not notes:
        notes.append(f"all expected evidence sources had sampled {surface} data")
    return {"notes": notes}


def _flatten_content(content: Any) -> str:
    if isinstance(content, str):
        return _safe_text(content, 220)
    if isinstance(content, list):
        parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif item.get("type") == "image":
                parts.append("[image]")
        return _safe_text(" ".join(parts), 220)
    return _safe_text(content, 220)


def _safe_text(value: Any, limit: int = 200) -> str:
    text = redact_sensitive_text(redact_monitor_text(value, max(limit * 2, 200)))
    text = " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split())
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _format_ts(value: Any) -> str:
    try:
        stamp = float(value or 0)
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp)) if stamp > 0 else ""
    except (TypeError, ValueError, OSError):
        return ""


def _append_section(lines: list[str], title: str, values: Any, *, empty: str = "none") -> None:
    lines.extend(["", title])
    if isinstance(values, list) and values:
        lines.extend(f"- {value}" for value in values)
    else:
        lines.append(f"- {empty}")
