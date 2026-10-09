"""MO — Session persistence: save, load, switch, list, resume, remove."""
from __future__ import annotations

import json
import hashlib
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
import traceback

from ..runtime.lock import file_byte_lock
from ..runtime.surface_identity import DESKTOP_SESSION_SLOT
from ..state.paths import SESSION_ROOT_DIR, resolve_state_path
from ..utils.atomic_write import atomic_write_json
from .session import (
    INTERNAL_CONTINUATION_KEY,
    PRESENTATION_KEY,
    Session,
    _bounded_skill_names,
    _bounded_skill_sources,
    _safe_float,
    _safe_int,
    is_runtime_owned_session_summary,
    restore_session_snapshot_fields,
)


def _emit_session_event(kind: str, **payload: Any) -> None:
    try:
        from ..runtime.backend_monitor import get_monitor
        monitor = get_monitor()
        if monitor:
            data = {"kind": kind}
            data.update(payload)
            monitor.emit("session_event", data)
    except Exception:
        traceback.print_exc()


SLOT_RETENTION_SECONDS = 14 * 24 * 3600  # prune auto-slots untouched > 14 days
HANDOFF_SNAPSHOT_KEEP = 30
PORTABLE_CONVERSATION_VERSION = 1
PORTABLE_CONVERSATION_ID_RE = re.compile(r"conv_[0-9a-f]{32}\Z")
PORTABLE_CONVERSATION_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
PORTABLE_CONVERSATION_LIMIT = 50
PORTABLE_TRANSCRIPT_MESSAGE_LIMIT = 100
PORTABLE_TRANSCRIPT_CHARS = 120_000
_RESERVED_PORTABLE_NAMES = frozenset({"main", DESKTOP_SESSION_SLOT})
_RESERVED_PORTABLE_PREFIXES = ("main-", "api-", "telegram-", "schedule-", "scheduler-", "desktop-", DESKTOP_SESSION_SLOT + "-")
_SESSION_THREAD_LOCKS = tuple(threading.RLock() for _ in range(32))


class PortableConversationError(RuntimeError):
    """Safe portable-conversation error suitable for a local/API response."""


class PortableConversationNotFound(PortableConversationError):
    pass


class PortableConversationConflict(PortableConversationError):
    pass


class PortableConversationValidationError(PortableConversationError):
    pass


@contextmanager
def _session_file_lock(sessions_dir: str | Path) -> Iterator[None]:
    """Serialize session catalog mutations across threads and MO processes."""
    root = Path(sessions_dir).resolve(strict=False)
    digest = hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()
    thread_lock = _SESSION_THREAD_LOCKS[int(digest[:8], 16) % len(_SESSION_THREAD_LOCKS)]
    lock_path = conversation_sessions_dir(root) / ".sessions.lock"
    with file_byte_lock(lock_path, thread_lock):
        yield


def conversation_sessions_dir(sessions_dir: str | Path) -> Path:
    return Path(sessions_dir) / "conversations"


def handoff_sessions_dir(sessions_dir: str | Path) -> Path:
    return Path(sessions_dir) / "history" / "handoffs"


def session_snapshot_path(sessions_dir: str | Path, name: str) -> Path:
    safe = "".join(c for c in str(name or "") if c.isalnum() or c in "-_.")[:64] or "session"
    return conversation_sessions_dir(sessions_dir) / f"{safe}.json"


def read_saved_user_message(reference: dict[str, Any], *, capture: bool = False) -> dict[str, Any]:
    """Read one explicitly cited user message without restoring a session.

    Message positions are not permanent IDs. A retained content hash may find
    a uniquely matching row after compaction, but never another conversation,
    an assistant answer, or a runtime summary. Initial capture may pin the ID
    of an explicitly named saved slot; later reads require that pinned ID.
    No transcript copy is persisted.
    """
    name = str(reference.get("session_name") or "")
    session_id = str(reference.get("session_id") or "")
    index = reference.get("message_index")
    expected = str(reference.get("content_sha256") or "")
    root = Path(resolve_state_path(SESSION_ROOT_DIR)).resolve()
    path = session_snapshot_path(root, name).resolve()
    if (not name or path.stem != name or path.parent != conversation_sessions_dir(root)
            or (not session_id and (not capture or expected)) or type(index) is not int or index < 0
            or (expected and not re.fullmatch(r"[0-9a-f]{64}", expected))):
        raise ValueError("A user-message reference needs an exact session name/ID and nonnegative message_index")
    data = SessionManager._read_path(path)
    if not data:
        return {"state": "source_unavailable"}
    if capture and not session_id:
        session_id = str(data.get("session_id") or "")
    if not session_id:
        return {"state": "session_unavailable"}
    if data.get("session_id") != session_id:
        return {"state": "session_changed"}
    messages = data.get("messages") or []
    candidates = []
    for position, message in enumerate(messages):
        if (not isinstance(message, dict) or message.get("role") != "user"
                or message.get(INTERNAL_CONTINUATION_KEY) or is_runtime_owned_session_summary(message)
                or not isinstance(message.get("content"), str)):
            continue
        digest = hashlib.sha256(message["content"].encode("utf-8")).hexdigest()
        if (expected and digest == expected) or (not expected and position == index):
            candidates.append((position, digest, message["content"]))
    if not candidates:
        return {"state": "message_unavailable_or_changed"}
    if len(candidates) != 1:
        return {"state": "ambiguous_message"}
    position, digest, content = candidates[0]
    from ..utils.text_safety import redact_secret_values

    safe_content = redact_secret_values(content)
    return {
        "state": "available", "content": safe_content[:12000],
        "content_truncated": len(safe_content) > 12000,
        "reference": {"kind": "user_message", "session_name": name, "session_id": session_id,
                      "message_index": position, "content_sha256": digest},
    }


def iter_conversation_session_paths(sessions_dir: str | Path):
    return conversation_sessions_dir(sessions_dir).glob("*.json")


def iter_all_session_paths(sessions_dir: str | Path):
    root = Path(sessions_dir)
    yield from conversation_sessions_dir(root).glob("*.json")
    yield from handoff_sessions_dir(root).glob("*.json")


def portable_session_name(value: Any) -> str:
    """Validate one canonical user-visible portable session name."""
    name = str(value or "").strip()
    if not PORTABLE_CONVERSATION_NAME_RE.fullmatch(name):
        raise PortableConversationValidationError(
            "conversation name must contain 1-64 ASCII letters, numbers, dots, dashes, or underscores"
        )
    lowered = name.casefold()
    if lowered in _RESERVED_PORTABLE_NAMES or any(
        lowered.startswith(prefix) for prefix in _RESERVED_PORTABLE_PREFIXES
    ):
        raise PortableConversationValidationError("conversation name is reserved for an automatic MO session")
    return name


def session_matches_surface(name: str, *, surface: str, metadata_surface: str = "", session_id: str = "", portable: bool = False) -> bool:
    """Use the continuity owner's metadata-first rules for catalog admission."""
    from ..runtime.continuity import DESKTOP_SLOT_NAMESPACE, _surface_family
    from ..runtime.surface_identity import DESKTOP_SURFACES, normalize_runtime_surface

    requested = normalize_runtime_surface(surface)
    owner = _surface_family(name, surface=metadata_surface, session_id=session_id, portable=portable)
    if requested in DESKTOP_SURFACES:
        return owner == DESKTOP_SLOT_NAMESPACE and (name == DESKTOP_SLOT_NAMESPACE or name.startswith(DESKTOP_SLOT_NAMESPACE + "-"))
    if name.casefold() == DESKTOP_SLOT_NAMESPACE or name.casefold().startswith(DESKTOP_SLOT_NAMESPACE + "-"):
        return False
    return (portable and requested == "terminal") or owner == _surface_family(name, surface=requested)


def _portable_meta(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    meta = data.get("meta")
    raw = meta.get("portable") if isinstance(meta, dict) else None
    if not isinstance(raw, dict):
        return None
    conversation_id = str(raw.get("conversation_id") or "")
    revision = _safe_int(raw.get("revision"), -1)
    shared_at = _safe_float(raw.get("shared_at"), -1.0)
    updated_at = _safe_float(raw.get("updated_at"), -1.0)
    if (
        _safe_int(raw.get("version"), -1) != PORTABLE_CONVERSATION_VERSION
        or not PORTABLE_CONVERSATION_ID_RE.fullmatch(conversation_id)
        or revision < 1
        or shared_at < 0
        or updated_at < shared_at
    ):
        return None
    return {
        "version": PORTABLE_CONVERSATION_VERSION,
        "conversation_id": conversation_id,
        "revision": revision,
        "shared_at": shared_at,
        "updated_at": updated_at,
    }


def _portable_info(data: dict[str, Any]) -> dict[str, Any]:
    portable = _portable_meta(data)
    if portable is None:
        raise PortableConversationNotFound("portable conversation was not found")
    return {
        "conversation_id": portable["conversation_id"],
        "name": str(data.get("name") or "")[:64],
        "revision": portable["revision"],
        "turns": max(0, _safe_int(data.get("turn_count"))),
        "messages": _portable_message_stats(data)[0],
        "shared_at": portable["shared_at"],
        "updated_at": portable["updated_at"],
    }


def _plain_portable_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "").strip().lower()
        if kind in {"text", "input_text", "output_text"}:
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def _portable_message(raw: Any) -> dict[str, str] | None:
    if not isinstance(raw, dict) or is_runtime_owned_session_summary(raw):
        return None
    role = str(raw.get("role") or "")
    if role not in {"user", "assistant"} or raw.get("tool_calls"):
        return None
    text = _plain_portable_content(raw.get("content"))
    presentation = raw.get(PRESENTATION_KEY)
    if role == "user" and isinstance(presentation, dict):
        display_text = presentation.get("display_text")
        if isinstance(display_text, str):
            text = display_text.strip()
    return {"role": role, "text": text} if text else None


def _portable_message_stats(data: dict[str, Any]) -> tuple[int, int, bool]:
    """Count visible rows and projected characters without exposing private payloads."""
    count = 0
    projected_chars = 0
    clipped_message = False
    for raw in list(data.get("messages", []) or []):
        row = _portable_message(raw)
        if row is None:
            continue
        text_chars = len(row["text"])
        count += 1
        projected_chars += min(20_000, text_chars)
        clipped_message = clipped_message or text_chars > 20_000
    return count, projected_chars, clipped_message


def _portable_messages(
    data: dict[str, Any],
    *,
    limit: int = PORTABLE_TRANSCRIPT_MESSAGE_LIMIT,
    max_chars: int = PORTABLE_TRANSCRIPT_CHARS,
) -> list[dict[str, str]]:
    """Return bounded visible text only; tools, system policy, and media stay private."""
    rows: list[dict[str, str]] = []
    remaining = max(1, min(PORTABLE_TRANSCRIPT_CHARS, int(max_chars or 1)))
    bounded_limit = max(1, min(PORTABLE_TRANSCRIPT_MESSAGE_LIMIT, int(limit or 1)))
    for raw in reversed(list(data.get("messages", []) or [])):
        if len(rows) >= bounded_limit or remaining <= 0:
            break
        row = _portable_message(raw)
        if row is None:
            continue
        text = row["text"]
        text = text[: min(20_000, remaining)]
        rows.append({"role": row["role"], "text": text})
        remaining -= len(text)
    rows.reverse()
    return rows


class SessionManager:
    """Manages user-visible snapshots under ``sessions/conversations``.

    Automatic pre-handoff recovery snapshots live separately under
    ``sessions/history/handoffs`` and never appear in ``/session`` lists.
    """

    def __init__(self, sessions_dir: str = SESSION_ROOT_DIR, *, default_name: str = "main"):
        # Absolute callers pass through unchanged; the bare relative default
        # resolves under the private state home instead of the process cwd.
        self.dir = Path(resolve_state_path(sessions_dir))
        self.dir.mkdir(parents=True, exist_ok=True)
        conversation_sessions_dir(self.dir).mkdir(parents=True, exist_ok=True)
        self._current_name: str = str(default_name or "main").strip() or "main"
        self._prune_stale_slots()

    def _prune_stale_slots(self, *, retention_seconds: float = SLOT_RETENTION_SECONDS) -> None:
        """Delete auto-generated per-instance slot files (and their pre-handoff
        backups) untouched past the retention window, so they don't accumulate
        forever (a fresh terminal never loads them in multi-instance mode). The
        current slot and user-named sessions are always kept; failures never
        break startup.
        """
        try:
            now = time.time()
            current = self._path(self._current_name).name
            for f in iter_conversation_session_paths(self.dir):
                if f.name == current:
                    continue
                # Only automatic per-instance slots; named sessions stay durable.
                if not f.name.startswith("main-"):
                    continue
                try:
                    if now - f.stat().st_mtime > retention_seconds:
                        f.unlink()
                except Exception:
                    continue
            for f in handoff_sessions_dir(self.dir).glob("*.json"):
                try:
                    if now - f.stat().st_mtime > retention_seconds:
                        f.unlink()
                except Exception:
                    continue
        except Exception:
            return

    def _path(self, name: str) -> Path:
        return session_snapshot_path(self.dir, name)

    def _snapshot_path(self, name: str) -> Path:
        safe = "".join(c for c in str(name or "") if c.isalnum() or c in "-_.")[:120] or "snapshot"
        return handoff_sessions_dir(self.dir) / f"{safe}.json"

    def save(self, name: str, session: Any, *, extra_meta: dict | None = None) -> str:
        """Save current session to disk."""
        name = str(name or self._current_name).strip() or "main"
        internal_history = "-pre-handoff-" in name
        loaded_name = str(getattr(session, "_loaded_session_name", "") or "")
        conversation_id = str(getattr(session, "_portable_conversation_id", "") or "")
        if not internal_history and conversation_id and loaded_name == name:
            data = self._save_portable_snapshot(
                conversation_id,
                session,
                expected_revision=_safe_int(
                    getattr(session, "_portable_conversation_revision", 0)
                ),
                extra_meta=extra_meta,
            )
            name = str(data.get("name") or name)
        else:
            data = self._write_session(name, session, extra_meta=extra_meta, snapshot=internal_history)
        self._current_name = name
        saved_messages = len(data.get("messages", []) or [])
        live_messages = len(getattr(session, "messages", []) or [])
        clean_meta = data.get("_clean_meta", {}) if isinstance(data.get("_clean_meta", {}), dict) else {}
        _emit_session_event(
            "save",
            name=name,
            session_id=str(getattr(session, "session_id", "") or ""),
            turns=int(getattr(session, "turn_count", 0) or 0),
            messages=saved_messages,
            saved_messages=saved_messages,
            live_messages=live_messages,
            quarantined=bool(clean_meta.get("changed")),
            dropped_messages=int(clean_meta.get("dropped_messages") or 0),
        )
        return f"Session saved: {name} ({session.turn_count} turns, {saved_messages} messages)"

    def save_snapshot(self, name: str, session: Any, *, extra_meta: dict | None = None) -> str:
        """Save a session snapshot without changing the current session slot."""
        name = str(name or "snapshot").strip() or "snapshot"
        loaded_name = str(getattr(session, "_loaded_session_name", "") or "")
        conversation_id = str(getattr(session, "_portable_conversation_id", "") or "")
        if "-pre-handoff-" not in name and conversation_id and loaded_name == name:
            data = self._save_portable_snapshot(
                conversation_id,
                session,
                expected_revision=_safe_int(
                    getattr(session, "_portable_conversation_revision", 0)
                ),
                extra_meta=extra_meta,
            )
            name = str(data.get("name") or name)
        else:
            data = self._write_session(
                name,
                session,
                extra_meta=extra_meta,
                snapshot="-pre-handoff-" in name,
            )
        saved_messages = len(data.get("messages", []) or [])
        live_messages = len(getattr(session, "messages", []) or [])
        clean_meta = data.get("_clean_meta", {}) if isinstance(data.get("_clean_meta", {}), dict) else {}
        _emit_session_event(
            "save_snapshot",
            name=name,
            session_id=str(getattr(session, "session_id", "") or ""),
            turns=int(getattr(session, "turn_count", 0) or 0),
            messages=saved_messages,
            saved_messages=saved_messages,
            live_messages=live_messages,
            quarantined=bool(clean_meta.get("changed")),
            dropped_messages=int(clean_meta.get("dropped_messages") or 0),
        )
        return f"Session snapshot saved: {name} ({session.turn_count} turns, {saved_messages} messages)"

    def prune_handoff_snapshots(self, base_name: str, *, keep: int = HANDOFF_SNAPSHOT_KEEP) -> int:
        """Cap '<base>-pre-handoff-*.json' snapshots at *keep* most-recent.

        These are written every context handoff and are historical only (the
        active session is its own file). Unpruned they accumulate unbounded
        (hundreds of files), which makes 'the last session' ambiguous and feeds
        drift. Mirrors prune_session_closeouts. Returns the count removed.
        """
        try:
            base = "".join(c for c in str(base_name or "main") if c.isalnum() or c in "-_.")[:64] or "main"
            snaps = sorted(handoff_sessions_dir(self.dir).glob(f"{base}-pre-handoff-*.json"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            removed = 0
            for old in snaps[max(0, keep):]:
                try:
                    old.unlink()
                    removed += 1
                except Exception:
                    continue
            return removed
        except Exception:
            return 0

    def _session_data(
        self,
        name: str,
        session: Any,
        *,
        extra_meta: dict | None = None,
        portable: dict[str, Any] | None = None,
        preserve_messages: bool = False,
    ) -> dict[str, Any]:
        cleaned_messages, clean_meta = (
            (list(session.messages), {}) if preserve_messages else self._clean_messages_with_meta(session.messages)
        )
        mail_turn = getattr(session, "_mail_sensitive_turn", False) is True
        if mail_turn:
            cleaned_messages = session.mail_safe_messages(cleaned_messages)
        if mail_turn and clean_meta.get("user"):
            clean_meta["user"] = "[Mail turn omitted from saved conversation]"
        saved_at = time.time()
        data = {
            "name": name,
            "session_id": session.session_id,
            "turn_count": session.turn_count,
            "messages": cleaned_messages,
            "total_tokens": session.total_tokens,
            "output_tokens": session.output_tokens,
            "input_tokens": _safe_int(getattr(session, "input_tokens", 0)),
            "cache_hit_tokens": _safe_int(getattr(session, "cache_hit_tokens", 0)),
            "cache_miss_tokens": _safe_int(getattr(session, "cache_miss_tokens", 0)),
            "cache_write_tokens": _safe_int(getattr(session, "cache_write_tokens", 0)),
            "token_log": list(session.token_log or []),
            "compacted_messages_count": _safe_int(getattr(session, "compacted_messages_count", 0)),
            "last_compacted_at": _safe_float(getattr(session, "last_compacted_at", 0.0)),
            "created_at": _safe_float(getattr(session, "created_at", 0.0)),
            "pending_learning_skill_sources": list(_bounded_skill_sources(
                getattr(session, "_pending_learning_skill_sources", ())
            )),
            "turn_selected_learning_skill_sources": list(_bounded_skill_sources(
                getattr(session, "_turn_selected_learning_skill_sources", ())
            )),
            "turn_selected_skill_names": list(_bounded_skill_names(
                getattr(session, "_turn_selected_skill_names", ())
            )),
            "turn_selected_skill_turn_count": max(
                0, _safe_int(getattr(session, "_turn_selected_skill_turn_count", 0))
            ),
            "pending_skill_import_context": str(
                getattr(session, "_pending_skill_import_context", "") or ""
            )[:2400],
            "saved_at": saved_at,
        }
        meta = dict(extra_meta or {})
        if portable is not None:
            meta["portable"] = dict(portable)
        if clean_meta.get("changed") and str(clean_meta.get("user") or "").strip() and "pending_interrupted_work" not in meta:
            meta["pending_interrupted_work"] = {
                "changed": True,
                "reason": str(clean_meta.get("reason") or "paused_work")[:120],
                "user": str(clean_meta.get("user") or "")[:500],
                "dropped_messages": int(clean_meta.get("dropped_messages") or 0),
                "saved_at": saved_at,
            }
        if meta:
            data["meta"] = meta
        data["_clean_meta"] = dict(clean_meta)
        return data

    def _write_session(self, name: str, session: Any, *, extra_meta: dict | None = None, snapshot: bool = False) -> dict[str, Any]:
        path = self._snapshot_path(name) if snapshot else self._path(name)
        with _session_file_lock(self.dir):
            existing = self._read_path(path)
            self._check_surface(name, existing, str((extra_meta or {}).get("surface") or getattr(session, "surface", "") or "terminal"))
            if not snapshot and _portable_meta(existing) is not None:
                raise PortableConversationConflict(
                    "portable conversation changed; reload it before saving"
                )
            data = self._session_data(name, session, extra_meta=extra_meta, preserve_messages=snapshot)
            self._write_path(path, data)
        if not snapshot:
            self._clear_portable_binding(session, name=name)
        return data

    @staticmethod
    def _check_surface(name: str, data: dict | None, surface: str) -> None:
        meta = (data or {}).get("meta") or {}
        declared_surface = str(meta.get("surface") or "")
        if data is None and name.casefold() not in _RESERVED_PORTABLE_NAMES and not name.casefold().startswith(_RESERVED_PORTABLE_PREFIXES):
            declared_surface = surface
        if not session_matches_surface(
            name, surface=surface, metadata_surface=declared_surface,
            session_id=str((data or {}).get("session_id") or ""), portable=_portable_meta(data) is not None,
        ):
            raise PortableConversationConflict("this session belongs to another MO surface; use that surface's history")

    @staticmethod
    def _read_path(path: Path) -> dict[str, Any] | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _write_path(path: Path, data: dict[str, Any]) -> None:
        stored = {key: value for key, value in data.items() if key != "_clean_meta"}
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, stored, indent=None, ensure_ascii=False, default=str)

    @staticmethod
    def _clear_portable_binding(session: Any, *, name: str = "") -> None:
        session._loaded_session_name = str(name or "")
        session._portable_conversation_id = ""
        session._portable_conversation_revision = 0

    def unbind_portable_session(self, session: Any, *, name: str = "") -> None:
        self._clear_portable_binding(session, name=name)

    @staticmethod
    def bind_loaded_session(session: Any, name: str, data: dict[str, Any]) -> None:
        """Attach optimistic portable revision state to one hydrated Session."""
        session._loaded_session_name = str(name or "")
        session._loaded_meta = (
            dict(data.get("meta") or {}) if isinstance(data.get("meta"), dict) else {}
        )
        portable = _portable_meta(data)
        session._portable_conversation_id = (
            str(portable["conversation_id"]) if portable is not None else ""
        )
        session._portable_conversation_revision = (
            int(portable["revision"]) if portable is not None else 0
        )

    def _find_portable_locked(
        self,
        conversation_id: str,
    ) -> tuple[Path, dict[str, Any], dict[str, Any]]:
        clean_id = str(conversation_id or "").strip()
        if not PORTABLE_CONVERSATION_ID_RE.fullmatch(clean_id):
            raise PortableConversationNotFound("portable conversation was not found")
        matches: list[tuple[Path, dict[str, Any], dict[str, Any]]] = []
        for path in iter_conversation_session_paths(self.dir):
            data = self._read_path(path)
            portable = _portable_meta(data)
            if data is not None and portable is not None and portable["conversation_id"] == clean_id:
                matches.append((path, data, portable))
        if not matches:
            raise PortableConversationNotFound("portable conversation was not found")
        if len(matches) != 1:
            raise PortableConversationConflict("portable conversation catalog is inconsistent")
        return matches[0]

    @staticmethod
    def _check_portable_revision(portable: dict[str, Any], expected_revision: Any) -> int:
        expected = _safe_int(expected_revision, -1)
        if expected < 1:
            raise PortableConversationValidationError("expected_revision must be a positive integer")
        if int(portable["revision"]) != expected:
            raise PortableConversationConflict(
                "portable conversation changed; reload it before continuing"
            )
        return expected

    def create_portable(self, name: str, session: Any) -> dict[str, Any]:
        """Create a new explicitly shared named session without a second transcript owner."""
        clean_name = portable_session_name(name)
        path = self._path(clean_name)
        now = time.time()
        portable = {
            "version": PORTABLE_CONVERSATION_VERSION,
            "conversation_id": "conv_" + secrets.token_hex(16),
            "revision": 1,
            "shared_at": now,
            "updated_at": now,
        }
        with _session_file_lock(self.dir):
            if path.exists():
                raise PortableConversationConflict("a session with that name already exists")
            data = self._session_data(clean_name, session, portable=portable)
            self._write_path(path, data)
        self.bind_loaded_session(session, clean_name, data)
        _emit_session_event("portable_create", name=clean_name, conversation_id=portable["conversation_id"])
        return _portable_info(data)

    def share_portable(self, name: str) -> dict[str, Any]:
        """Make one existing named session visible to explicitly scoped devices."""
        clean_name = portable_session_name(name)
        path = self._path(clean_name)
        with _session_file_lock(self.dir):
            data = self._read_path(path)
            if data is None:
                raise PortableConversationNotFound("session was not found")
            self._check_surface(clean_name, data, "terminal")
            existing = _portable_meta(data)
            if existing is None:
                now = time.time()
                existing = {
                    "version": PORTABLE_CONVERSATION_VERSION,
                    "conversation_id": "conv_" + secrets.token_hex(16),
                    "revision": 1,
                    "shared_at": now,
                    "updated_at": now,
                }
                meta = dict(data.get("meta") or {}) if isinstance(data.get("meta"), dict) else {}
                meta["portable"] = existing
                data["meta"] = meta
                self._write_path(path, data)
        _emit_session_event("portable_share", name=clean_name, conversation_id=existing["conversation_id"])
        return _portable_info(data)

    def bind_portable_session(self, session: Any, name: str, info: dict[str, Any]) -> None:
        session._loaded_session_name = str(name or "")
        session._portable_conversation_id = str(info.get("conversation_id") or "")
        session._portable_conversation_revision = _safe_int(info.get("revision"), 0)

    def list_portable(self, *, limit: int = PORTABLE_CONVERSATION_LIMIT) -> list[dict[str, Any]]:
        bounded = max(1, min(PORTABLE_CONVERSATION_LIMIT, int(limit or 1)))
        rows: list[dict[str, Any]] = []
        with _session_file_lock(self.dir):
            for path in iter_conversation_session_paths(self.dir):
                data = self._read_path(path)
                if data is None or _portable_meta(data) is None:
                    continue
                rows.append(_portable_info(data))
        return sorted(rows, key=lambda row: float(row["updated_at"]), reverse=True)[:bounded]

    def load_portable(
        self,
        conversation_id: str,
        *,
        expected_revision: int | None = None,
    ) -> tuple[str, dict[str, Any]]:
        with _session_file_lock(self.dir):
            _path, data, portable = self._find_portable_locked(conversation_id)
            if expected_revision is not None:
                self._check_portable_revision(portable, expected_revision)
            return str(data.get("name") or _path.stem), data

    def get_portable(
        self,
        conversation_id: str,
        *,
        limit: int = PORTABLE_TRANSCRIPT_MESSAGE_LIMIT,
    ) -> dict[str, Any]:
        with _session_file_lock(self.dir):
            _path, data, _portable = self._find_portable_locked(conversation_id)
            result = _portable_info(data)
            result["transcript"] = _portable_messages(data, limit=limit)
            message_count, projected_chars, clipped_message = _portable_message_stats(data)
            result["truncated"] = (
                clipped_message
                or projected_chars > PORTABLE_TRANSCRIPT_CHARS
                or message_count > len(result["transcript"])
            )
            return result

    def _save_portable_snapshot(
        self,
        conversation_id: str,
        session: Any,
        *,
        expected_revision: int,
        extra_meta: dict | None = None,
    ) -> dict[str, Any]:
        with _session_file_lock(self.dir):
            path, existing, portable = self._find_portable_locked(conversation_id)
            self._check_portable_revision(portable, expected_revision)
            updated = dict(portable)
            updated["revision"] = int(portable["revision"]) + 1
            updated["updated_at"] = time.time()
            meta = dict(existing.get("meta") or {}) if isinstance(existing.get("meta"), dict) else {}
            # Interrupted work describes this exact snapshot.  Do not carry a
            # previous checkpoint into a later clean portable revision; an
            # explicit current checkpoint (or _session_data's unfinished-tail
            # quarantine) will add it back below when it is still applicable.
            meta.pop("pending_interrupted_work", None)
            meta.update(dict(extra_meta or {}))
            meta.pop("portable", None)
            data = self._session_data(
                str(existing.get("name") or path.stem),
                session,
                extra_meta=meta,
                portable=updated,
            )
            self._write_path(path, data)
        self.bind_loaded_session(session, str(data.get("name") or path.stem), data)
        return data

    def rename_portable(
        self,
        conversation_id: str,
        name: str,
        *,
        expected_revision: int,
    ) -> dict[str, Any]:
        clean_name = portable_session_name(name)
        with _session_file_lock(self.dir):
            path, data, portable = self._find_portable_locked(conversation_id)
            self._check_portable_revision(portable, expected_revision)
            old_name = str(data.get("name") or path.stem)
            if clean_name == old_name:
                return _portable_info(data)
            destination = self._path(clean_name)
            if destination.exists() and destination != path:
                raise PortableConversationConflict("a session with that name already exists")
            updated = dict(portable)
            updated["revision"] = int(portable["revision"]) + 1
            updated["updated_at"] = time.time()
            meta = dict(data.get("meta") or {}) if isinstance(data.get("meta"), dict) else {}
            meta["portable"] = updated
            data["name"] = clean_name
            data["meta"] = meta
            self._write_path(path, data)
            if destination != path:
                os.replace(path, destination)
            if self._current_name == old_name:
                self._current_name = clean_name
        _emit_session_event("portable_rename", name=clean_name, conversation_id=conversation_id)
        return _portable_info(data)

    def unshare_portable(
        self,
        conversation_id: str,
        *,
        expected_revision: int,
    ) -> str:
        with _session_file_lock(self.dir):
            path, data, portable = self._find_portable_locked(conversation_id)
            self._check_portable_revision(portable, expected_revision)
            name = str(data.get("name") or path.stem)
            meta = dict(data.get("meta") or {}) if isinstance(data.get("meta"), dict) else {}
            meta.pop("portable", None)
            # Unsharing returns this explicitly named conversation to Terminal,
            # not to whichever scoped device last wrote its portable revision.
            meta["surface"] = "terminal"
            data["meta"] = meta
            self._write_path(path, data)
        _emit_session_event("portable_unshare", name=name, conversation_id=conversation_id)
        return name

    def remove_portable(
        self,
        conversation_id: str,
        *,
        expected_revision: int,
    ) -> str:
        with _session_file_lock(self.dir):
            path, data, portable = self._find_portable_locked(conversation_id)
            self._check_portable_revision(portable, expected_revision)
            name = str(data.get("name") or path.stem)
            path.unlink()
            if self._current_name == name:
                self._current_name = "main"
        _emit_session_event("portable_remove", name=name, conversation_id=conversation_id)
        return name

    def load(self, name: str, *, emit_event: bool = True) -> dict | None:
        """Load a session from disk. Returns raw dict or None.

        Catalog and preview readers may disable runtime activity events. User-
        visible restores and switches keep the default event emission.
        """
        path = self._path(name)
        if not path.exists():
            if emit_event:
                _emit_session_event("load_missing", name=name)
            return None
        try:
            data = self._read_path(path)
            if isinstance(data, dict):
                cleaned, meta = self._clean_messages_with_meta(data.get("messages", []) or [])
                data["messages"] = cleaned
                if meta.get("changed"):
                    data["_unfinished_tail_meta"] = meta
                if emit_event:
                    _emit_session_event("load", name=name, session_id=str(data.get("session_id") or ""), turns=int(data.get("turn_count", 0) or 0), messages=len(cleaned), quarantined=bool(meta.get("changed")), dropped_messages=int(meta.get("dropped_messages") or 0))
            return data
        except (json.JSONDecodeError, OSError) as exc:
            if emit_event:
                _emit_session_event("load_error", name=name, error_type=type(exc).__name__)
            return None

    def switch(
        self,
        name: str,
        session: Any,
        *,
        extra_meta: dict | None = None,
        save_current: bool = True,
    ) -> str:
        """Optionally save current, then load target (or create new).

        A caller may suppress the save when the live session is empty. This is
        required by ``/new`` -> ``/resume``: the previous conversation is already
        preserved in the current named slot, and saving the new empty object over
        that slot would destroy the snapshot the operator asked to resume.
        """
        data = self.load(name, emit_event=False)
        self._check_surface(name, data, str((extra_meta or {}).get("surface") or "terminal"))
        if save_current:
            self.save(self._current_name, session, extra_meta=extra_meta)
        data = self.load(name)
        if data:
            restore_session_snapshot_fields(session, data)
            session.sanitize_for_provider()
            self.bind_loaded_session(session, name, data)
            self._current_name = name
            _emit_session_event("switch", name=name, session_id=str(getattr(session, "session_id", "") or ""), turns=int(getattr(session, "turn_count", 0) or 0), messages=len(getattr(session, "messages", []) or []))
            preview = self._first_user_preview(session.messages, limit=60)
            return (
                f"Switched to '{name}'\n"
                f"  {session.turn_count} turns, {len(session.messages)} messages\n"
                + (f"  First: \"{preview}\"\n" if preview else "")
            )
        # New session
        session.clear()
        session.session_id = f"mo-{int(time.time())}"
        self._clear_portable_binding(session, name=name)
        session._loaded_meta = {}
        self._current_name = name
        self.save(name, session, extra_meta=extra_meta)
        _emit_session_event("create", name=name, session_id=str(getattr(session, "session_id", "") or ""))
        return f"Created new session: '{name}'"

    def remove(self, name: str, *, surface: str = "terminal") -> str:
        """Delete a saved session."""
        path = self._path(name)
        with _session_file_lock(self.dir):
            if not path.exists():
                return f"Session not found: {name}"
            self._check_surface(name, self._read_path(path), surface)
            path.unlink()
            if self._current_name == name:
                self._current_name = "main"
        _emit_session_event("remove", name=name)
        return f"Removed session: {name}"

    def list_sessions(self, *, include_topics: bool = False, surface: str = "") -> list[dict]:
        """Return metadata for all saved sessions, newest first."""
        if include_topics:
            from .session_closeout import topic_from_messages

        sessions = []
        for path in sorted(iter_conversation_session_paths(self.dir), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
                row = {
                    "name": data.get("name", path.stem),
                    "session_id": str(data.get("session_id") or ""),
                    "surface": str(meta.get("surface") or ""),
                    "turns": data.get("turn_count", 0),
                    "messages": len(data.get("messages", [])),
                    "saved_at": data.get("saved_at", 0),
                    "current": data.get("name", path.stem) == self._current_name,
                    "portable": _portable_meta(data) is not None,
                    "preview": self._first_user_preview(data.get("messages", [])),
                }
                row["age"] = self._age_text(row["saved_at"])
                if surface and not session_matches_surface(
                    str(row["name"]), surface=surface, metadata_surface=row["surface"],
                    session_id=row["session_id"], portable=row["portable"],
                ):
                    continue
                if include_topics:
                    row["topic"] = topic_from_messages(data.get("messages", []))
                sessions.append(row)
            except (json.JSONDecodeError, OSError):
                continue
        return sessions

    def usage_activity(self, *, days: int = 182, now: float | None = None) -> dict:
        """Numeric-only activity from retained local snapshots, not lifetime usage.

        Saved aliases and copied goal/session token rows are deduplicated by the
        original provider receipt. No transcript or second usage store is exposed.
        """
        from datetime import datetime, timedelta

        today = datetime.fromtimestamp(time.time() if now is None else now).date()
        days = max(7, min(int(days), 366))
        start = today - timedelta(days=days - 1)
        totals = {(start + timedelta(days=i)).isoformat(): 0 for i in range(days)}
        seen = set()
        skipped = 0
        measured = 0
        budget = 64 * 1024 * 1024
        paths = sorted(iter_conversation_session_paths(self.dir), key=lambda p: p.stat().st_mtime, reverse=True)
        cache = getattr(self, "_usage_snapshot_cache", {})
        current_cache = {}
        for index, path in enumerate(paths):
            try:
                stat = path.stat()
                if index >= 256 or stat.st_size > min(budget, 8 * 1024 * 1024):
                    skipped += 1
                    continue
                budget -= stat.st_size
                key = (str(path), stat.st_mtime_ns, stat.st_size)
                if key in cache:
                    receipts = cache[key]
                else:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    meta = data.get("meta") or {}
                    if not any(session_matches_surface(str(data.get("name", path.stem)), surface=surface,
                               metadata_surface=str(meta.get("surface") or ""),
                               session_id=str(data.get("session_id") or ""), portable=_portable_meta(data) is not None)
                               for surface in ("terminal", "desktop")):
                        continue
                    receipts = []
                    for row in data.get("token_log", []):
                        if not isinstance(row, dict) or row.get("source") != "provider_usage":
                            continue
                        stamp = float(row.get("ts") or 0)
                        count = max(0, int(row.get("total_tokens") or 0))
                        if stamp > 0:
                            receipts.append((stamp, str(row.get("provider") or ""), str(row.get("model") or ""),
                                             int(row.get("input_tokens") or 0), int(row.get("output_tokens") or 0), count))
                current_cache[key] = receipts
                for receipt in receipts:
                    if receipt in seen:
                        continue
                    seen.add(receipt)
                    day = datetime.fromtimestamp(receipt[0]).date().isoformat()
                    if day in totals:
                        totals[day] += receipt[-1]
                        measured += 1
            except (OSError, ValueError, TypeError, OverflowError, AttributeError):
                skipped += 1
        self._usage_snapshot_cache = current_cache
        longest = streak = 0
        for count in totals.values():
            streak = streak + 1 if count > 0 else 0
            longest = max(longest, streak)
        current = 0
        day = today if totals[today.isoformat()] else today - timedelta(days=1)
        while totals.get(day.isoformat(), 0) > 0:
            current += 1
            day -= timedelta(days=1)
        return {"available": bool(measured), "days": [{"date": day, "tokens": value} for day, value in totals.items()],
                "total_tokens": sum(totals.values()), "peak_day": max(totals.values()),
                "current_streak": current, "longest_streak": longest, "skipped_snapshots": skipped,
                "scope": "Retained local sessions · last " + str(days) + " days · not lifetime usage"}

    def latest(self) -> str | None:
        """Return name of the most recently saved session."""
        sessions = self.list_sessions()
        return sessions[0]["name"] if sessions else None

    def render_list(self, *, surface: str = "") -> str:
        """Render session list for display."""
        sessions = self.list_sessions(surface=surface)
        if not sessions:
            return "No saved sessions."
        lines = [f"{len(sessions)} sessions:"]
        for i, s in enumerate(sessions, 1):
            marker = " *" if s["current"] else "  "
            portable = " · portable" if s.get("portable") else ""
            lines.append(f"{marker}[{i}] {s['name']} — {s['turns']} turns · {s['age']}{portable}")
        lines.append(
            "\nUse /session <name> or /session <number> to switch. "
            "Use /session share <name> to make only that named conversation portable."
        )
        return "\n".join(lines)

    @property
    def current_name(self) -> str:
        return self._current_name

    @staticmethod
    def _first_user_preview(messages: Any, *, limit: int = 120) -> str:
        """Return one bounded first-message label for session selection."""
        for message in messages or []:
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                preview = " ".join(content.split())
                if preview:
                    return preview[: max(1, int(limit))]
        return ""

    @staticmethod
    def _clean_messages_with_meta(messages: list[dict]) -> tuple[list[dict], dict[str, Any]]:
        """Normalize saved messages, preserving interrupted tool evidence."""
        cleaned = []
        for msg in messages or []:
            if isinstance(msg, dict):
                if msg.get(INTERNAL_CONTINUATION_KEY) is True:
                    continue
                m = {k: v for k, v in msg.items() if k != "reasoning_content"}
                if is_runtime_owned_session_summary(m):
                    m["role"] = "system"
                cleaned.append(m)
        cleaned, meta = Session.close_unfinished_tool_tail(cleaned)
        if not meta.get("changed"):
            cleaned, meta = Session.close_unanswered_user_tail(cleaned)
        return cleaned, meta

    @staticmethod
    def _age_text(ts: float) -> str:
        if not ts:
            return "unknown"
        age = time.time() - ts
        if age < 60:
            return "just now"
        if age < 3600:
            return f"{int(age / 60)}m ago"
        if age < 86400:
            return f"{int(age / 3600)}h ago"
        return f"{int(age / 86400)}d ago"
