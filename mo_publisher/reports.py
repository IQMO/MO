"""Bounded, independent publisher report custody and operator review."""

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


RETENTION_SECONDS = 30 * 24 * 60 * 60
MAX_BODY_BYTES = 256 * 1024
CATEGORIES = {"offensive", "unsafe", "harassment", "sexual", "other"}
OUTCOMES = {"action_required", "resolved", "no_action", "insufficient_context"}
TEXT_LIMITS = {"explanation": 1000, "app_version": 120, "build_id": 200, "locale": 40}
REQUIRED = {
    "schema_version", "report_id", "category", "explanation", "content_sha256",
    "content_included", "app_version", "version_code", "build_id", "distribution", "locale",
}


class ReportError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def configured_store():
    from core.state.paths import resolve_state_path

    state_home = Path(os.environ.get("MO_STATE_HOME", ""))
    if not state_home.is_absolute() or os.environ.get("MO_STATE_LOCAL"):
        raise RuntimeError("Publisher needs a dedicated absolute MO_STATE_HOME")
    return ReportStore(Path(resolve_state_path("memory/surfaces/publisher-reports.sqlite")))


def canonical_uuid(value: str) -> str:
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("Invalid report identifier")
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate field")
        result[key] = value
    return result


def validate_report(raw: bytes) -> dict:
    """Match Android schema 1; never echo rejected text or accept extra credentials."""
    if len(raw) > MAX_BODY_BYTES:
        raise ReportError(413, "Report is too large")
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        if not isinstance(data, dict) or type(data.get("content_included")) is not bool:
            raise ValueError()
        fields = REQUIRED | ({"assistant_response", "preceding_prompt"} if data["content_included"] else set())
        if set(data) != fields:
            raise ValueError()
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError()
        canonical_uuid(data["report_id"])
        if data["category"] not in CATEGORIES or data["distribution"] not in {"play", "direct"}:
            raise ValueError()
        if type(data["version_code"]) is not int or not 1 <= data["version_code"] <= 2_100_000_000:
            raise ValueError()
        if not isinstance(data["content_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", data["content_sha256"]):
            raise ValueError()
        limits = TEXT_LIMITS | ({"assistant_response": 16000, "preceding_prompt": 16000} if data["content_included"] else {})
        for key, maximum in limits.items():
            value = data[key]
            if not isinstance(value, str) or len(value.encode("utf-16-le")) // 2 > maximum:
                raise ValueError()
            if any(ord(ch) < 32 and ch not in "\n\r\t" for ch in value):
                raise ValueError()
        if data["content_included"]:
            response = data["assistant_response"]
            if not response.strip() or hashlib.sha256(response.encode("utf-8")).hexdigest() != data["content_sha256"]:
                raise ValueError()
        return data
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as exc:
        raise ReportError(422, "Invalid report") from exc


class ReportStore:
    """SQLite transactions acknowledge only durable writes. No IP or auth storage."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS reports (
                    report_id TEXT PRIMARY KEY, receipt TEXT UNIQUE NOT NULL,
                    created_at INTEGER NOT NULL, payload TEXT NOT NULL,
                    outcome TEXT NOT NULL DEFAULT 'pending', reviewed_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS report_created ON reports(created_at);
            """)
        path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA secure_delete=ON")
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def accept(self, data: dict, now: int | None = None) -> tuple[str, bool]:
        now = int(time.time()) if now is None else now
        payload = json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM reports WHERE created_at <= ?", (now - RETENTION_SECONDS,))
            old = db.execute("SELECT receipt,payload FROM reports WHERE report_id=?", (data["report_id"],)).fetchone()
            if old:
                if old["payload"] != payload:
                    raise ReportError(409, "Report identifier already used")
                return old["receipt"], False
            count = db.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
            recent = db.execute("SELECT COUNT(*) FROM reports WHERE created_at > ?", (now - 3600,)).fetchone()[0]
            if count >= 10000 or recent >= 500:
                raise ReportError(503, "Reporting is temporarily at capacity")
            receipt = str(uuid.uuid4())
            db.execute("INSERT INTO reports(report_id,receipt,created_at,payload) VALUES(?,?,?,?)",
                       (data["report_id"], receipt, now, payload))
        return receipt, True

    def purge(self, now: int | None = None) -> int:
        now = int(time.time()) if now is None else now
        with self.connect() as db:
            return db.execute("DELETE FROM reports WHERE created_at <= ?", (now - RETENTION_SECONDS,)).rowcount

    def pending(self) -> list[dict]:
        self.purge()
        with self.connect() as db:
            # No conversation, explanation, response hash, or receipt in routine output.
            rows = db.execute("SELECT report_id,created_at,outcome FROM reports WHERE outcome IN ('pending','action_required') ORDER BY created_at LIMIT 100")
            return [dict(row) for row in rows]

    def read(self, report_id: str) -> dict | None:
        self.purge()
        with self.connect() as db:
            row = db.execute("SELECT * FROM reports WHERE report_id=?", (canonical_uuid(report_id),)).fetchone()
            return dict(row) if row else None

    def review(self, report_id: str, outcome: str) -> bool:
        if outcome not in OUTCOMES:
            raise ValueError("Unsupported outcome")
        self.purge()
        with self.connect() as db:
            return bool(db.execute("UPDATE reports SET outcome=?,reviewed_at=? WHERE report_id=?",
                                   (outcome, int(time.time()), canonical_uuid(report_id))).rowcount)

    def delete(self, receipt: str) -> bool:
        with self.connect() as db:
            return bool(db.execute("DELETE FROM reports WHERE receipt=?", (canonical_uuid(receipt),)).rowcount)
