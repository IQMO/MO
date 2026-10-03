"""Bounded private conversation state for one MO Design artifact folder."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from core.state.attachments import (
    ATTACHMENT_CATEGORIES,
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS_PER_TURN,
    safe_attachment_name,
)
from core.utils.atomic_write import atomic_write_json
from .schema import DESIGN_EXTENSION


SESSION_VERSION = 1
SESSION_FILE = "session.json"
MAX_MESSAGES = 80
MAX_MESSAGE_CHARS = 4_000
MAX_SESSION_BYTES = 256_000
REQUEST_TIMEOUT_SECONDS = 15 * 60
_ROLES = frozenset({"mo", "user"})
_ROUTES = frozenset({"studio"})
# COMPAT(mo-design-request-kind-v1): replaced-by auto/repair request kinds; remove-when no supported pre-auto Studio build can leave a baseline/refine pending sidecar
_REQUEST_KINDS = frozenset({"auto", "repair", "baseline", "refine"})
_ORIGIN_INTENTS = frozenset({"current_state", "refinement", "new_concept"})
_LIFECYCLE_STATES = frozenset({"active", "completed"})
_UNCHANGED = object()
_LOCK = threading.RLock()


def _activity(phase: str, label: str, detail: str) -> dict[str, str]:
    return {
        "phase": str(phase or "ready").strip().lower()[:32],
        "label": str(label or "Ready").strip()[:80],
        "detail": str(detail or "").strip()[:240],
        "updated_at": _now(),
    }


def design_session_path(artifact_path: str | Path) -> Path:
    """Return the private sidecar next to one portable Design artifact."""
    artifact = Path(artifact_path).expanduser().resolve(strict=False)
    if artifact.suffix.casefold() != DESIGN_EXTENSION:
        raise ValueError(f"MO Design sessions require a {DESIGN_EXTENSION} artifact")
    return artifact.parent / SESSION_FILE


def initialize_design_session(
    artifact_path: str | Path,
    *,
    design_id: str,
    title: str,
    origin_intent: str = "",
) -> dict[str, Any]:
    """Create the initial local conversation without touching an existing one."""
    path = design_session_path(artifact_path)
    with _LOCK:
        if path.is_file():
            return load_design_session(artifact_path, design_id=design_id, title=title)
        session = _default_session(design_id, title, origin_intent=origin_intent)
        _write(path, session)
        return session


def load_design_session(
    artifact_path: str | Path,
    *,
    design_id: str,
    title: str,
) -> dict[str, Any]:
    """Load safe session state, falling back without blocking the artifact."""
    path = design_session_path(artifact_path)
    try:
        raw_bytes = path.read_bytes()
        if len(raw_bytes) > MAX_SESSION_BYTES:
            return _default_session(design_id, title)
        raw = json.loads(raw_bytes.decode("utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return _default_session(design_id, title)
    try:
        return _normalize_session(raw, design_id=design_id, title=title)
    except (TypeError, ValueError):
        return _default_session(design_id, title)


def complete_design_session(
    artifact_path: str | Path,
    *,
    design_id: str,
    title: str,
    revision: int,
) -> dict[str, Any]:
    """Mark one implemented Design revision complete after its receiving MO verifies the work."""
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("MO Design completed revision is invalid")
    path = design_session_path(artifact_path)
    with _LOCK:
        session = load_design_session(artifact_path, design_id=design_id, title=title)
        if session.get("pending") is not None:
            raise ValueError("MO Design cannot complete while a Design request is pending")
        now = _now()
        session["lifecycle"] = {
            "state": "completed",
            "revision": revision,
            "completed_at": now,
        }
        session["activity"] = _activity(
            "completed",
            "Completed",
            f"Implemented and verified from Design revision r{revision}.",
        )
        session["updated_at"] = now
        _write(path, session)
        return session


def reopen_design_session(
    artifact_path: str | Path,
    *,
    design_id: str,
    title: str,
) -> dict[str, Any]:
    """Reactivate a completed Design while retaining its resolved revision."""
    path = design_session_path(artifact_path)
    with _LOCK:
        session = load_design_session(artifact_path, design_id=design_id, title=title)
        lifecycle = session.get("lifecycle") or {}
        if lifecycle.get("state") != "completed":
            return session
        resolved_revision = int(lifecycle.get("revision") or 0)
        session["lifecycle"] = {
            "state": "active",
            "revision": resolved_revision,
            "completed_at": str(lifecycle.get("completed_at") or ""),
        }
        session["activity"] = _activity(
            "ready",
            "Ready",
            f"Resolved r{resolved_revision}; reopened for another Design refinement.",
        )
        session["updated_at"] = _now()
        _write(path, session)
        return session


def mark_design_baseline(
    artifact_path: str | Path,
    *,
    design_id: str,
    title: str,
    revision: int,
) -> dict[str, Any]:
    """Record the current-state revision that authorizes later project refinement."""
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ValueError("MO Design baseline revision is invalid")
    path = design_session_path(artifact_path)
    with _LOCK:
        session = load_design_session(artifact_path, design_id=design_id, title=title)
        session["baseline_revision"] = revision
        session["updated_at"] = _now()
        _write(path, session)
        return session


def append_design_messages(
    artifact_path: str | Path,
    *,
    design_id: str,
    title: str,
    messages: Iterable[tuple[Any, ...]],
    pending: dict[str, Any] | None | object = _UNCHANGED,
    activity: dict[str, Any] | object = _UNCHANGED,
) -> dict[str, Any]:
    """Append trusted UI messages and optionally replace pending work state."""
    path = design_session_path(artifact_path)
    with _LOCK:
        session = load_design_session(artifact_path, design_id=design_id, title=title)
        rows = list(session["messages"])
        for message in messages:
            if not isinstance(message, (list, tuple)) or len(message) not in {2, 3}:
                raise ValueError("MO Design message must contain role, text, and optional attachments")
            role, text = message[:2]
            attachments = _normalize_attachments(message[2] if len(message) == 3 else None)
            clean_role = str(role or "").strip().lower()
            clean_text = str(text or "").strip()
            if clean_role not in _ROLES:
                raise ValueError("MO Design message role must be mo or user")
            if attachments and clean_role != "user":
                raise ValueError("Only user Design messages may carry attachments")
            if not clean_text and not attachments:
                continue
            if len(clean_text) > MAX_MESSAGE_CHARS:
                raise ValueError("MO Design message is too large")
            row = {"role": clean_role, "text": clean_text, "created_at": _now()}
            if attachments:
                row["attachments"] = attachments
            rows.append(row)
        session["messages"] = rows[-MAX_MESSAGES:]
        if pending is not _UNCHANGED:
            session["pending"] = _normalize_pending(pending)
            if session["pending"]:
                session["last_attention"] = ""
        if activity is not _UNCHANGED:
            session["activity"] = _normalize_activity(activity, session["activity"])
        session["updated_at"] = _now()
        _write(path, session)
        return session


def begin_design_request(
    artifact_path: str | Path,
    *,
    design_id: str,
    title: str,
    feedback: str,
    acknowledgement: str,
    request_kind: str,
    route: str,
    command_id: str,
    base_revision: int,
    source_revision: int | None = None,
    attachments: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Persist one accepted visual-conversation or repair request."""
    pending = {
        "base_revision": int(base_revision),
        "source_revision": int(source_revision or base_revision),
        "request_kind": str(request_kind or "").strip().lower(),
        "route": str(route or "").strip().lower(),
        "command_id": str(command_id or "")[:128],
        "started_at": _now(),
    }
    with _LOCK:
        if load_design_session(artifact_path, design_id=design_id, title=title).get("pending"):
            raise ValueError("Wait for the current Design request to finish or stop it first.")
        return append_design_messages(
            artifact_path,
            design_id=design_id,
            title=title,
            messages=(("user", feedback, list(attachments or ())), ("mo", acknowledgement)),
            pending=pending,
            activity=_activity(
                "preparing",
                "Preparing request…",
                "Reading your message and the selected visual context.",
            ),
        )


def update_design_activity(
    artifact_path: str | Path,
    *,
    design_id: str,
    title: str,
    command_id: str,
    phase: str,
    label: str,
    detail: str,
    message: str = "",
) -> dict[str, Any]:
    """Persist one truthful, bounded activity phase for the matching Studio request."""
    path = design_session_path(artifact_path)
    clean_command = str(command_id or "")[:128]
    with _LOCK:
        session = load_design_session(artifact_path, design_id=design_id, title=title)
        pending = session.get("pending")
        if not pending or str(pending.get("command_id") or "") != clean_command:
            return session
        next_activity = _activity(phase, label, detail)
        current = session.get("activity") if isinstance(session.get("activity"), dict) else {}
        if all(current.get(key) == next_activity.get(key) for key in ("phase", "label", "detail")):
            return session
        session["activity"] = next_activity
        if message:
            session["messages"] = (session["messages"] + [{
                "role": "mo", "text": str(message)[:MAX_MESSAGE_CHARS], "created_at": _now(),
            }])[-MAX_MESSAGES:]
        session["updated_at"] = _now()
        _write(path, session)
        return session


def advance_pending_revision(
    artifact_path: str | Path,
    *,
    design_id: str,
    title: str,
    revision: int,
) -> dict[str, Any]:
    """Prevent a non-visual artifact edit from completing pending Design work."""
    path = design_session_path(artifact_path)
    with _LOCK:
        session = load_design_session(artifact_path, design_id=design_id, title=title)
        pending = session.get("pending")
        if not pending:
            return session
        pending = dict(pending)
        pending["base_revision"] = int(revision)
        session["pending"] = _normalize_pending(pending)
        session["updated_at"] = _now()
        _write(path, session)
        return session


def complete_design_request(
    artifact_path: str | Path,
    *,
    design_id: str,
    title: str,
    revision: int,
    command_id: str = "",
    message: str = "",
    activity_label: str = "",
    activity_detail: str = "",
) -> dict[str, Any]:
    """Resolve pending Design work after the broker proves worker completion."""
    path = design_session_path(artifact_path)
    with _LOCK:
        session = load_design_session(artifact_path, design_id=design_id, title=title)
        pending = session.get("pending")
        current_revision = int(revision)
        if not pending or current_revision <= int(pending.get("base_revision") or 0):
            return session
        if command_id and pending.get("command_id") != command_id:
            return session
        rows = list(session["messages"])
        request_kind = str(pending.get("request_kind") or "auto")
        fallback_message = {
            "auto": (
                f"Design updated to r{current_revision}. Review it above; "
                "continue here until you are ready for Handoff."
            ),
            "baseline": (
                f"Current surface mapped to r{current_revision}. Review the baseline above; "
                "no visual changes were proposed."
            ),
            "repair": (
                f"Preview repaired at r{current_revision}. Test it again above before Handoff."
            ),
            "refine": (
                f"Visual updated to r{current_revision}. Review it above; "
                "we can refine it again before any Handoff."
            ),
        }[request_kind]
        clean_message = str(message or "").strip()[:MAX_MESSAGE_CHARS] or fallback_message
        rows.append({
            "role": "mo",
            "text": clean_message,
            "created_at": _now(),
        })
        session["messages"] = rows[-MAX_MESSAGES:]
        session["pending"] = None
        session["last_completed_revision"] = current_revision
        session["last_attention"] = ""
        session["activity"] = _activity(
            "delivered",
            str(activity_label or "").strip()[:160] or f"Visual updated · r{current_revision}",
            str(activity_detail or "").strip()[:600]
            or "Review the saved concept, keep refining, or use final Handoff when approved.",
        )
        session["updated_at"] = _now()
        _write(path, session)
        return session


def complete_design_response(
    artifact_path: str | Path,
    *,
    design_id: str,
    title: str,
    command_id: str,
    message: str,
    stopped: bool = False,
) -> dict[str, Any]:
    """Finish matching Design work without accepting a new visual revision."""
    path = design_session_path(artifact_path)
    clean_command = str(command_id or "")[:128]
    clean_message = str(message or "").strip()[:MAX_MESSAGE_CHARS]
    if not clean_message:
        raise ValueError("MO Design response is empty")
    with _LOCK:
        session = load_design_session(artifact_path, design_id=design_id, title=title)
        pending = session.get("pending")
        if not pending or str(pending.get("command_id") or "") != clean_command:
            return session
        rows = list(session["messages"])
        rows.append({"role": "mo", "text": clean_message, "created_at": _now()})
        session["messages"] = rows[-MAX_MESSAGES:]
        session["pending"] = None
        session["last_attention"] = ""
        session["activity"] = _activity(
            "stopped" if stopped else "answered",
            "Stopped" if stopped else "Answered",
            clean_message if stopped else "No visual revision was created. Continue the conversation or request a change.",
        )
        session["updated_at"] = _now()
        _write(path, session)
        return session


def fail_design_request(
    artifact_path: str | Path,
    *,
    design_id: str,
    title: str,
    command_id: str,
    message: str,
) -> dict[str, Any]:
    """End matching Design work that produced no visual revision."""
    path = design_session_path(artifact_path)
    clean_command = str(command_id or "")[:128]
    clean_message = str(message or "").strip()[:MAX_MESSAGE_CHARS]
    with _LOCK:
        session = load_design_session(artifact_path, design_id=design_id, title=title)
        pending = session.get("pending")
        if not pending or str(pending.get("command_id") or "") != clean_command:
            return session
        if clean_message:
            rows = list(session["messages"])
            rows.append({"role": "mo", "text": clean_message, "created_at": _now()})
            session["messages"] = rows[-MAX_MESSAGES:]
        session["pending"] = None
        session["last_attention"] = clean_message
        session["activity"] = _activity("attention", "Needs attention", clean_message)
        session["updated_at"] = _now()
        _write(path, session)
        return session


def pending_request_is_stale(pending: Any, *, now: datetime | None = None) -> bool:
    """Return whether a Studio request exceeded the bounded recovery window."""
    if not isinstance(pending, dict):
        return False
    started = str(pending.get("started_at") or "").strip()
    if not started:
        return False
    try:
        parsed = datetime.fromisoformat(started.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    return (current - parsed.astimezone(timezone.utc)).total_seconds() > REQUEST_TIMEOUT_SECONDS


def _default_session(
    design_id: str,
    title: str,
    *,
    origin_intent: str = "",
) -> dict[str, Any]:
    now = _now()
    clean_title = str(title or "Untitled Design").strip() or "Untitled Design"
    return {
        "version": SESSION_VERSION,
        "design_id": str(design_id or "").strip(),
        "origin_intent": _normalize_origin_intent(origin_intent),
        "baseline_revision": 0,
        "messages": [{
            "role": "mo",
            "text": (
                f"Design session loaded: {clean_title}. "
                "Ask MO to load a current surface or refine a visual whenever you’re ready."
            ),
            "created_at": now,
        }],
        "pending": None,
        "activity": _activity(
            "ready",
            "Ready",
            "Ask for suggestions, compare options, load a surface, or request a visual change.",
        ),
        "last_completed_revision": 0,
        "last_attention": "",
        "lifecycle": {
            "state": "active",
            "revision": 0,
            "completed_at": "",
        },
        "updated_at": now,
    }


def _normalize_session(raw: Any, *, design_id: str, title: str) -> dict[str, Any]:
    fallback = _default_session(design_id, title)
    if not isinstance(raw, dict) or raw.get("version") != SESSION_VERSION:
        return fallback
    if str(raw.get("design_id") or "") != str(design_id or ""):
        return fallback
    messages: list[dict[str, Any]] = []
    for row in raw.get("messages") if isinstance(raw.get("messages"), list) else ():
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "").strip().lower()
        text = str(row.get("text") or "").strip()
        try:
            attachments = _normalize_attachments(row.get("attachments"))
        except ValueError:
            attachments = []
        if role != "user":
            attachments = []
        if role not in _ROLES or (not text and not attachments) or len(text) > MAX_MESSAGE_CHARS:
            continue
        message = {
            "role": role,
            "text": text,
            "created_at": str(row.get("created_at") or "")[:64],
        }
        if role == "user" and attachments:
            message["attachments"] = attachments
        messages.append(message)
    fallback["messages"] = (messages or fallback["messages"])[-MAX_MESSAGES:]
    fallback["origin_intent"] = _normalize_origin_intent(raw.get("origin_intent"))
    baseline = raw.get("baseline_revision", 0)
    fallback["baseline_revision"] = (
        int(baseline) if isinstance(baseline, int) and not isinstance(baseline, bool) and baseline >= 0 else 0
    )
    fallback["pending"] = _normalize_pending(raw.get("pending"))
    fallback["activity"] = _normalize_activity(raw.get("activity"), fallback["activity"])
    completed = raw.get("last_completed_revision", 0)
    fallback["last_completed_revision"] = (
        int(completed) if isinstance(completed, int) and not isinstance(completed, bool) and completed >= 0 else 0
    )
    fallback["last_attention"] = str(raw.get("last_attention") or "")[:MAX_MESSAGE_CHARS]
    fallback["lifecycle"] = _normalize_lifecycle(raw.get("lifecycle"))
    fallback["updated_at"] = str(raw.get("updated_at") or fallback["updated_at"])[:64]
    return fallback


def _normalize_origin_intent(value: Any) -> str:
    intent = str(value or "").strip().lower()
    return intent if intent in _ORIGIN_INTENTS else ""


def _normalize_lifecycle(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"state": "active", "revision": 0, "completed_at": ""}
    state = str(value.get("state") or "active").strip().lower()
    revision = value.get("revision", 0)
    completed_at = str(value.get("completed_at") or "")[:64]
    if state not in _LIFECYCLE_STATES:
        state = "active"
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        revision = 0
    if state == "completed" and revision < 1:
        return {"state": "active", "revision": 0, "completed_at": ""}
    if revision < 1:
        completed_at = ""
    return {"state": state, "revision": revision, "completed_at": completed_at}


def _normalize_activity(value: Any, fallback: dict[str, str]) -> dict[str, str]:
    if not isinstance(value, dict):
        return fallback
    phase = str(value.get("phase") or "").strip().lower()
    label = str(value.get("label") or "").strip()
    if not phase or not label:
        return fallback
    return {
        "phase": phase[:32],
        "label": label[:80],
        "detail": str(value.get("detail") or "").strip()[:240],
        "updated_at": str(value.get("updated_at") or "")[:64],
    }


def _normalize_pending(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("MO Design pending state must be an object or null")
    base = value.get("base_revision")
    source = value.get("source_revision", base)
    route = str(value.get("route") or "").strip().lower()
    request_kind = str(value.get("request_kind") or "auto").strip().lower()
    command_id = str(value.get("command_id") or "")
    if not isinstance(base, int) or isinstance(base, bool) or base < 1:
        raise ValueError("MO Design pending revision is invalid")
    if not isinstance(source, int) or isinstance(source, bool) or source < 1:
        raise ValueError("MO Design pending source revision is invalid")
    if route not in _ROUTES:
        raise ValueError("MO Design pending route is invalid")
    if request_kind not in _REQUEST_KINDS:
        raise ValueError("MO Design request kind is invalid")
    if len(command_id) > 128:
        raise ValueError("MO Design command id is too large")
    return {
        "base_revision": base,
        "source_revision": source,
        "request_kind": request_kind,
        "route": route,
        "command_id": command_id,
        "started_at": str(value.get("started_at") or "")[:64],
    }


def _normalize_attachments(value: Any) -> list[dict[str, Any]]:
    if value in (None, ()):
        return []
    if not isinstance(value, (list, tuple)):
        raise ValueError("MO Design attachments must be a list")
    if len(value) > MAX_ATTACHMENTS_PER_TURN:
        raise ValueError("MO Design accepts at most 8 attachments per message")
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("MO Design attachment metadata is invalid")
        attachment_id = str(item.get("id") or item.get("attachment_id") or "").strip()
        if (
            not attachment_id
            or len(attachment_id) > 128
            or any(char in attachment_id for char in "/\\")
            or attachment_id in seen
        ):
            raise ValueError("MO Design attachment id is invalid")
        name = safe_attachment_name(item.get("name"))
        category = str(item.get("category") or "").strip().lower()
        size = item.get("bytes")
        if category not in ATTACHMENT_CATEGORIES:
            raise ValueError("MO Design attachment category is invalid")
        if not isinstance(size, int) or isinstance(size, bool) or size < 1 or size > MAX_ATTACHMENT_BYTES:
            raise ValueError("MO Design attachment size is invalid")
        seen.add(attachment_id)
        rows.append({
            "id": attachment_id,
            "name": name,
            "category": category,
            "bytes": size,
        })
    return rows


def _write(path: Path, session: dict[str, Any]) -> None:
    encoded = json.dumps(session, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    while len(encoded) > MAX_SESSION_BYTES and len(session["messages"]) > 1:
        session["messages"].pop(0)
        encoded = json.dumps(session, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_SESSION_BYTES:
        raise ValueError("MO Design session is too large")
    atomic_write_json(path, session, ensure_ascii=False, separators=(",", ":"))


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
