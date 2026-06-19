"""MO — Session persistence: save, load, switch, list, resume, remove."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
import traceback

from .session import Session


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _emit_session_event(kind: str, **payload: Any) -> None:
    try:
        from ..backend_monitor import get_monitor
        monitor = get_monitor()
        if monitor:
            data = {"kind": kind}
            data.update(payload)
            monitor.emit("session_event", data)
    except Exception:
        traceback.print_exc()


class SessionManager:
    """Manages named sessions as JSON files under memory/sessions/."""

    def __init__(self, sessions_dir: str = "memory/sessions"):
        self.dir = Path(sessions_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._current_name: str = "main"

    def _path(self, name: str) -> Path:
        safe = "".join(c for c in str(name or "") if c.isalnum() or c in "-_.")[:64] or "session"
        return self.dir / f"{safe}.json"

    def save(self, name: str, session: Any, *, extra_meta: dict | None = None) -> str:
        """Save current session to disk."""
        name = str(name or self._current_name).strip() or "main"
        self._write_session(name, session, extra_meta=extra_meta)
        self._current_name = name
        _emit_session_event("save", name=name, session_id=str(getattr(session, "session_id", "") or ""), turns=int(getattr(session, "turn_count", 0) or 0), messages=len(getattr(session, "messages", []) or []))
        return f"Session saved: {name} ({session.turn_count} turns, {len(session.messages)} messages)"

    def save_snapshot(self, name: str, session: Any, *, extra_meta: dict | None = None) -> str:
        """Save a session snapshot without changing the current session slot."""
        name = str(name or "snapshot").strip() or "snapshot"
        self._write_session(name, session, extra_meta=extra_meta)
        _emit_session_event("save_snapshot", name=name, session_id=str(getattr(session, "session_id", "") or ""), turns=int(getattr(session, "turn_count", 0) or 0), messages=len(getattr(session, "messages", []) or []))
        return f"Session snapshot saved: {name} ({session.turn_count} turns, {len(session.messages)} messages)"

    def _write_session(self, name: str, session: Any, *, extra_meta: dict | None = None) -> None:
        cleaned_messages, clean_meta = self._clean_messages_with_meta(session.messages)
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
            "token_log": list(session.token_log or []),
            "compacted_messages_count": _safe_int(getattr(session, "compacted_messages_count", 0)),
            "last_compacted_at": _safe_float(getattr(session, "last_compacted_at", 0.0)),
            "created_at": _safe_float(getattr(session, "created_at", 0.0)),
            "saved_at": saved_at,
        }
        meta = dict(extra_meta or {})
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
        path = self._path(name)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    def load(self, name: str) -> dict | None:
        """Load a session from disk. Returns raw dict or None."""
        path = self._path(name)
        if not path.exists():
            _emit_session_event("load_missing", name=name)
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cleaned, meta = self._clean_messages_with_meta(data.get("messages", []) or [])
                data["messages"] = cleaned
                if meta.get("changed"):
                    data["_unfinished_tail_meta"] = meta
                _emit_session_event("load", name=name, session_id=str(data.get("session_id") or ""), turns=int(data.get("turn_count", 0) or 0), messages=len(cleaned), quarantined=bool(meta.get("changed")), dropped_messages=int(meta.get("dropped_messages") or 0))
            return data
        except (json.JSONDecodeError, OSError) as exc:
            _emit_session_event("load_error", name=name, error_type=type(exc).__name__)
            return None

    def switch(self, name: str, session: Any, *, extra_meta: dict | None = None) -> str:
        """Save current, then load target (or create new)."""
        self.save(self._current_name, session, extra_meta=extra_meta)
        data = self.load(name)
        if data:
            session.session_id = data.get("session_id", session.session_id)
            session.turn_count = data.get("turn_count", 0)
            session.messages = data.get("messages", [])
            session.total_tokens = data.get("total_tokens", 0)
            session.output_tokens = data.get("output_tokens", 0)
            session.input_tokens = _safe_int(data.get("input_tokens", 0))
            session.cache_hit_tokens = _safe_int(data.get("cache_hit_tokens", 0))
            session.cache_miss_tokens = _safe_int(data.get("cache_miss_tokens", 0))
            session.token_log = list(data.get("token_log", []) or [])
            session.compacted_messages_count = _safe_int(data.get("compacted_messages_count", 0))
            session.last_compacted_at = _safe_float(data.get("last_compacted_at", 0.0))
            # Restore the switched-in session's own birth time so age/learning-delta
            # windows key off it, not the in-memory object's creation time.
            restored_created = _safe_float(data.get("created_at", 0.0))
            if restored_created:
                session.created_at = restored_created
            session.sanitize_for_provider()
            session._loaded_meta = data.get("meta", {}) if isinstance(data.get("meta", {}), dict) else {}
            self._current_name = name
            _emit_session_event("switch", name=name, session_id=str(getattr(session, "session_id", "") or ""), turns=int(getattr(session, "turn_count", 0) or 0), messages=len(getattr(session, "messages", []) or []))
            preview = ""
            for m in session.messages:
                if m.get("role") == "user":
                    preview = m.get("content", "")[:60].replace("\n", " ")
                    break
            return (
                f"Switched to '{name}'\n"
                f"  {session.turn_count} turns, {len(session.messages)} messages\n"
                + (f"  First: \"{preview}\"\n" if preview else "")
            )
        # New session
        session.clear()
        session.session_id = f"mo-{int(time.time())}"
        session._loaded_meta = {}
        self._current_name = name
        self.save(name, session)
        _emit_session_event("create", name=name, session_id=str(getattr(session, "session_id", "") or ""))
        return f"Created new session: '{name}'"

    def remove(self, name: str) -> str:
        """Delete a saved session."""
        path = self._path(name)
        if not path.exists():
            return f"Session not found: {name}"
        path.unlink()
        if self._current_name == name:
            self._current_name = "main"
        _emit_session_event("remove", name=name)
        return f"Removed session: {name}"

    def list_sessions(self) -> list[dict]:
        """Return metadata for all saved sessions, newest first."""
        sessions = []
        for path in sorted(self.dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                sessions.append({
                    "name": data.get("name", path.stem),
                    "turns": data.get("turn_count", 0),
                    "messages": len(data.get("messages", [])),
                    "saved_at": data.get("saved_at", 0),
                    "current": data.get("name", path.stem) == self._current_name,
                })
            except (json.JSONDecodeError, OSError):
                continue
        return sessions

    def latest(self) -> str | None:
        """Return name of the most recently saved session."""
        sessions = self.list_sessions()
        return sessions[0]["name"] if sessions else None

    def render_list(self) -> str:
        """Render session list for display."""
        sessions = self.list_sessions()
        if not sessions:
            return "No saved sessions."
        lines = [f"{len(sessions)} sessions:"]
        for i, s in enumerate(sessions, 1):
            marker = " *" if s["current"] else "  "
            age = self._age_text(s["saved_at"])
            lines.append(f"{marker}[{i}] {s['name']} — {s['turns']} turns · {age}")
        lines.append("\nUse /session <name> or /session <number> to switch.")
        return "\n".join(lines)

    @property
    def current_name(self) -> str:
        return self._current_name

    @staticmethod
    def _clean_messages_with_meta(messages: list[dict]) -> tuple[list[dict], dict[str, Any]]:
        """Strip saved messages and return meta when an unfinished tail was removed."""
        cleaned = []
        for msg in messages or []:
            if isinstance(msg, dict):
                m = {k: v for k, v in msg.items() if k != "reasoning_content"}
                cleaned.append(m)
        cleaned, meta = Session.strip_unfinished_tool_tail(cleaned)
        if not meta.get("changed"):
            cleaned, meta = Session.strip_unanswered_user_tail(cleaned)
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
