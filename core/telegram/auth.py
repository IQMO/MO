"""Telegram pairing/allowlist store."""
from __future__ import annotations

import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PairingCode:
    code: str
    sender_id: str
    expires_at: float


class TelegramAuthStore:
    def __init__(self, path: str | Path = "memory/telegram.sqlite", *, ttl_seconds: int = 3600):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0)
        return conn

    def _init_db(self) -> None:
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS allowed(sender_id TEXT PRIMARY KEY, approved_at REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS pending(code TEXT PRIMARY KEY, sender_id TEXT NOT NULL, expires_at REAL NOT NULL)")

    def is_allowed(self, sender_id: str) -> bool:
        with self._connect() as db:
            row = db.execute("SELECT 1 FROM allowed WHERE sender_id=?", (str(sender_id),)).fetchone()
        return bool(row)

    def create_pairing(self, sender_id: str) -> PairingCode:
        code = secrets.token_urlsafe(6).replace("-", "")[:8].upper()
        expires = time.time() + self.ttl_seconds
        with self._connect() as db:
            db.execute("INSERT OR REPLACE INTO pending(code, sender_id, expires_at) VALUES (?, ?, ?)", (code, str(sender_id), expires))
        return PairingCode(code, str(sender_id), expires)

    def approve(self, code: str) -> bool:
        now = time.time()
        normalized = str(code or "").upper()
        with self._connect() as db:
            row = db.execute("SELECT sender_id, expires_at FROM pending WHERE code=?", (normalized,)).fetchone()
            if not row:
                return False
            sender_id, expires_at = row
            if float(expires_at) < now:
                db.execute("DELETE FROM pending WHERE code=?", (normalized,))
                return False
            db.execute("INSERT OR REPLACE INTO allowed(sender_id, approved_at) VALUES (?, ?)", (sender_id, now))
            db.execute("DELETE FROM pending WHERE code=?", (normalized,))
        return True

    def counts(self) -> tuple[int, int]:
        with self._connect() as db:
            allowed = int(db.execute("SELECT COUNT(*) FROM allowed").fetchone()[0])
            pending = int(db.execute("SELECT COUNT(*) FROM pending WHERE expires_at>=?", (time.time(),)).fetchone()[0])
        return allowed, pending
