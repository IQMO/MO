"""Telegram chat/thread to MO session mapping."""
from __future__ import annotations

import re
import secrets
import sqlite3
import time
from pathlib import Path

from ..state.paths import TELEGRAM_DB_PATH, resolve_state_path


class TelegramSessionStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(resolve_state_path(path or TELEGRAM_DB_PATH))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5.0)

    def _init_db(self) -> None:
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS chat_sessions("
                "chat_id TEXT PRIMARY KEY, session_name TEXT NOT NULL, "
                "updated_at REAL NOT NULL, handoff_key TEXT NOT NULL DEFAULT '', "
                "active_handoff_id TEXT NOT NULL DEFAULT '')"
            )
            columns = {
                str(row[1]) for row in db.execute("PRAGMA table_info(chat_sessions)")
            }
            if "handoff_key" not in columns:
                db.execute(
                    "ALTER TABLE chat_sessions ADD COLUMN handoff_key TEXT NOT NULL DEFAULT ''"
                )
            if "active_handoff_id" not in columns:
                db.execute(
                    "ALTER TABLE chat_sessions ADD COLUMN active_handoff_id "
                    "TEXT NOT NULL DEFAULT ''"
                )
            missing = db.execute(
                "SELECT chat_id FROM chat_sessions WHERE handoff_key=''"
            ).fetchall()
            for (chat_id,) in missing:
                db.execute(
                    "UPDATE chat_sessions SET handoff_key=? WHERE chat_id=?",
                    (secrets.token_hex(16), str(chat_id)),
                )
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS chat_sessions_handoff_key_idx "
                "ON chat_sessions(handoff_key)"
            )

    def get_or_create(self, chat_id: str) -> str:
        chat_id = str(chat_id)
        with self._connect() as db:
            row = db.execute("SELECT session_name FROM chat_sessions WHERE chat_id=?", (chat_id,)).fetchone()
            if row:
                db.execute("UPDATE chat_sessions SET updated_at=? WHERE chat_id=?", (time.time(), chat_id))
                return str(row[0])
            session = f"telegram-{_safe_session_part(chat_id)}"
            db.execute(
                "INSERT INTO chat_sessions(chat_id, session_name, updated_at, handoff_key) "
                "VALUES (?, ?, ?, ?)",
                (chat_id, session, time.time(), secrets.token_hex(16)),
            )
            return session

    def list_mappings(self, *, limit: int = 50) -> list[dict[str, str]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT chat_id, session_name, updated_at, handoff_key, active_handoff_id "
                "FROM chat_sessions "
                "ORDER BY updated_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [
            {
                "chat_id": str(chat_id),
                "session_name": str(session_name),
                "updated_at": str(updated_at),
                "handoff_key": str(handoff_key),
                "active_handoff_id": str(active_handoff_id),
            }
            for chat_id, session_name, updated_at, handoff_key, active_handoff_id in rows
        ]

    def handoff_target(self, handoff_key: str) -> dict[str, str] | None:
        """Resolve one opaque target key without exposing it as a chat identity."""
        key = _handoff_key(handoff_key)
        with self._connect() as db:
            row = db.execute(
                "SELECT chat_id, session_name, updated_at, handoff_key, active_handoff_id "
                "FROM chat_sessions WHERE handoff_key=?",
                (key,),
            ).fetchone()
        if not row:
            return None
        return {
            "chat_id": str(row[0]),
            "session_name": str(row[1]),
            "updated_at": str(row[2]),
            "handoff_key": str(row[3]),
            "active_handoff_id": str(row[4]),
        }

    def handoff_target_for_chat(self, chat_id: str) -> dict[str, str] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT chat_id, session_name, updated_at, handoff_key, active_handoff_id "
                "FROM chat_sessions WHERE chat_id=?",
                (str(chat_id),),
            ).fetchone()
        if not row:
            return None
        return {
            "chat_id": str(row[0]),
            "session_name": str(row[1]),
            "updated_at": str(row[2]),
            "handoff_key": str(row[3]),
            "active_handoff_id": str(row[4]),
        }

    def bind_handoff_target(
        self, handoff_key: str, session_name: str, handoff_id: str
    ) -> bool:
        key = _handoff_key(handoff_key)
        name = _session_name(session_name)
        active_handoff = _handoff_id(handoff_id)
        with self._connect() as db:
            changed = db.execute(
                "UPDATE chat_sessions SET session_name=?,active_handoff_id=?,updated_at=? "
                "WHERE handoff_key=?",
                (name, active_handoff, time.time(), key),
            ).rowcount
        return changed == 1

    def clear_active_handoff(self, handoff_key: str, handoff_id: str) -> bool:
        key = _handoff_key(handoff_key)
        active_handoff = _handoff_id(handoff_id)
        with self._connect() as db:
            changed = db.execute(
                "UPDATE chat_sessions SET active_handoff_id='' "
                "WHERE handoff_key=? AND active_handoff_id=?",
                (key, active_handoff),
            ).rowcount
        return changed == 1

    def count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0])


def _safe_session_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip("-.")
    return safe[:48] or "chat"


def _handoff_key(value: str) -> str:
    clean = str(value or "").strip().lower()
    if len(clean) != 32 or any(char not in "0123456789abcdef" for char in clean):
        raise ValueError("Telegram handoff target is invalid")
    return clean


def _session_name(value: str) -> str:
    clean = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", clean):
        raise ValueError("Telegram session name is invalid")
    return clean


def _handoff_id(value: str) -> str:
    clean = str(value or "").strip().lower()
    if len(clean) != 32 or any(char not in "0123456789abcdef" for char in clean):
        raise ValueError("Telegram conversation handoff is invalid")
    return clean
