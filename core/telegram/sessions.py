"""Telegram chat/thread to MO session mapping."""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path


class TelegramSessionStore:
    def __init__(self, path: str | Path = "memory/telegram.sqlite"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=5.0)

    def _init_db(self) -> None:
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS chat_sessions(chat_id TEXT PRIMARY KEY, session_name TEXT NOT NULL, updated_at REAL NOT NULL)"
            )

    def get_or_create(self, chat_id: str) -> str:
        chat_id = str(chat_id)
        with self._connect() as db:
            row = db.execute("SELECT session_name FROM chat_sessions WHERE chat_id=?", (chat_id,)).fetchone()
            if row:
                db.execute("UPDATE chat_sessions SET updated_at=? WHERE chat_id=?", (time.time(), chat_id))
                return str(row[0])
            session = f"telegram-{_safe_session_part(chat_id)}"
            db.execute("INSERT INTO chat_sessions(chat_id, session_name, updated_at) VALUES (?, ?, ?)", (chat_id, session, time.time()))
            return session

    def list_mappings(self, *, limit: int = 50) -> list[dict[str, str]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT chat_id, session_name, updated_at FROM chat_sessions ORDER BY updated_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [
            {"chat_id": str(chat_id), "session_name": str(session_name), "updated_at": str(updated_at)}
            for chat_id, session_name, updated_at in rows
        ]

    def count(self) -> int:
        with self._connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0])


def _safe_session_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip("-.")
    return safe[:48] or "chat"
