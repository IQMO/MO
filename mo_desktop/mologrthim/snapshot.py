"""Read-only projection of the existing project roster and worker registry."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def build_snapshot(roles: Any, registry: Any, *, activity: str = "", summary: str = "",
                   session: Any = None, board: Any = None, events: Any = None,
                   learning: Any = None, binding: str = "") -> dict[str, Any]:
    """Project project-bound roles and their latest current-process assignments."""
    from core.runtime.backend_monitor import redact_monitor_text
    project_roles = [
        role for role in list(roles or [])
        if str(getattr(role, "project_root", "") or "").strip()
    ]
    recent = registry.recent(limit=50) if callable(getattr(registry, "recent", None)) else []
    latest: dict[tuple[Path, str], Any] = {}
    for record in recent:
        if getattr(record, "source", "") != "project-architect" or getattr(record, "kind", "") != "worker":
            continue
        role_id = str(getattr(record, "role", "") or "").casefold()
        project = str(getattr(record, "project_root", "") or "")
        if role_id and project:
            latest[(Path(project), role_id)] = record

    states = {
        "offered": ("Queued", "active"),
        "accepted": ("Queued", "active"),
        "running": ("Running", "active"),
        "completed": ("Report received · see orchestrator summary", "warn"),
        "blocked": ("Blocked", "error"),
        "cancelled": ("Stopped", "muted"),
        "paused": ("Paused", "warn"),
    }
    rows = []
    for role in sorted(project_roles, key=lambda item: str(getattr(item, "name", "")).casefold()):
        role_id = str(getattr(role, "role", "") or "")
        record = latest.get((Path(role.project_root), role_id.casefold()))
        raw_state = str(getattr(record, "state", "") or "") if record is not None else ""
        label, tone = states.get(raw_state, ("No recorded assignment", "muted"))
        rows.append({
            "role": role_id,
            "name": str(getattr(role, "name", "") or role_id),
            "focus": str(getattr(role, "description", "") or ""),
            "status": label,
            "tone": tone,
            "active": bool(record is not None and raw_state in {"offered", "accepted", "running"}),
            "state": raw_state,
            "assignment": redact_monitor_text(getattr(record, "objective", ""), 1200),
            "note": redact_monitor_text(getattr(record, "note", ""), 500),
            "report": redact_monitor_text(getattr(record, "result_report", "") or getattr(record, "result_summary", ""), 12000),
            "evidence": [redact_monitor_text(item, 240) for item in (getattr(record, "evidence", []) or [])[:12]],
        })
    from core.session.session import INTERNAL_CONTINUATION_KEY, is_runtime_owned_session_summary
    conversation = []
    for message in list(getattr(session, 'messages', []) or []):
        if (not isinstance(message, dict) or message.get('role') not in ('user', 'assistant')
                or message.get(INTERNAL_CONTINUATION_KEY) or is_runtime_owned_session_summary(message)):
            continue
        content = message.get('content')
        if isinstance(content, list):
            content = '\n'.join(str(part.get('text', '')) for part in content
                                if isinstance(part, dict) and part.get('type') == 'text')
        if isinstance(content, str) and content.strip():
            conversation.append({'role': message['role'], 'text': redact_monitor_text(content, len(content))})
    sid = str(getattr(session, 'session_id', '') or '')
    tasks = []
    if sid and str(getattr(board, 'session_id', '') or '') == sid:
        tasks = [{'id': str(t.id), 'title': redact_monitor_text(t.title, len(str(t.title or ''))),
                  'status': str(t.status), 'evidence': [redact_monitor_text(e, len(str(e or ''))) for e in t.evidence],
                  'blocker': redact_monitor_text(t.blocker, len(str(t.blocker or '')))} for t in board.tasks]
    # Hosts supply only events from this exact conversation and instance.
    receipts = [dict(event) for event in list(events or ())[-12:] if isinstance(event, dict)]
    return {
        "roles": rows,
        "activity": str(activity or "").strip()[:180],
        "summary": str(summary or "").strip()[:500],
        "active_count": sum(bool(row["active"]) for row in rows),
        "report_count": sum(row["status"].startswith("Report received") for row in rows),
        "conversation": conversation, "binding": str(binding),
        "connected": session is not None, "tasks": tasks, "events": receipts,
        "session_id": sid, "events_ready": events is not None,
        "learning": dict(learning) if isinstance(learning, dict) else None,
    }

def read_runtime_events(monitor, session_id, cache=None):
    """Bounded observation of one caller-owned monitor, never log discovery."""
    from core.diagnostics.surface_trace import _jsonl_rows
    from core.runtime.backend_monitor import redact_monitor_text
    if not session_id or monitor is None or not getattr(monitor, 'path', None):
        return []
    cache = cache if cache is not None else {}
    path = Path(monitor.path)
    try:
        stat = path.stat()
        key = (str(path), getattr(monitor, 'run_id', None), session_id, stat.st_mtime_ns, stat.st_size)
        if cache.get('key') == key:
            return cache['events']
        rows = _jsonl_rows(path, 128, max_bytes=65536)
    except OSError:
        return []
    labels = {'tool_call':'Tool dispatched', 'tool_result':'Tool result received',
              'worker_event':'Worker record updated', 'taskboard':'Taskboard updated',
              'memory_index':'Memory indexed', 'memory_embedding_index':'Embedding receipt',
              'learning_auto_promote':'Learning promotion recorded', 'runtime_phase':'Runtime activity',
              'session_compact':'Conversation compacted', 'context_handoff':'Context handoff recorded'}
    events = []
    for row in rows:
        payload = row.get('payload') or {}
        if (not isinstance(payload, dict) or payload.get('session_id') != session_id or row.get('type') not in labels
                or row.get('run_id') != getattr(monitor, 'run_id', None)):
            continue
        events.append({'id':f"{row['run_id']}:{row.get('seq', '')}", 'type':row['type'], 'label':labels[row['type']],
                       'detail':redact_monitor_text(str(payload), 1600), 'ts':row.get('ts')})
    cache.update(key=key, events=events[-12:])
    return cache['events']
