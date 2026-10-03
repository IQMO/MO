"""Hub-owned bounded continuity event store.

The hub keeps prompt-safe orientation events, cursors, and finite retention in
the same private SQLite database as the device registry. It is not a transcript
store and never accepts tool output or arbitrary payload fields.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from core.state.continuity_events import (
    DEFAULT_MAX_AGE_SECONDS,
    ContinuityEvent,
    ContinuityEventError,
    continuity_event_from_row,
)
from core.state.paths import EVERYWHERE_HUB_DB_PATH, resolve_state_path
from core.state.sqlite import connect_state_db


DEFAULT_MAX_ROWS = 2_000
DEFAULT_MAX_ROWS_PER_THREAD = 100
DEFAULT_MAX_THREADS_PER_DEVICE = 50


class ContinuityHub:
    """Finite, cursor-addressable continuity ledger for trusted MO devices."""

    def __init__(self, config: dict[str, Any] | None = None, *, path: str | Path | None = None):
        self.config = config or {}
        configured = path or resolve_state_path(EVERYWHERE_HUB_DB_PATH, self.config)
        self.path = Path(configured).expanduser().resolve(strict=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_age_seconds, self.max_rows, self.max_rows_per_thread, self.max_threads_per_device = _limits(self.config)
        self._initialize()

    def publish(self, event: ContinuityEvent | dict[str, Any], *, source_device_id: str) -> ContinuityEvent:
        raw = event.as_dict(include_cursor=False) if isinstance(event, ContinuityEvent) else event
        clean = ContinuityEvent.from_dict(
            raw,
            source_device_id=source_device_id,
            max_age_seconds=self.max_age_seconds,
        )
        now = time.time()
        if clean.expires_at <= now:
            raise ContinuityEventError("continuity event is already expired")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT seq,source_device_id FROM continuity_hub_event WHERE event_id=?",
                (clean.event_id,),
            ).fetchone()
            if existing:
                if str(existing["source_device_id"]) != clean.source_device_id:
                    raise ContinuityEventError("continuity event id is already owned by another device")
                return replace(clean, remote_cursor=int(existing["seq"]))
            cursor = db.execute(
                """
                INSERT INTO continuity_hub_event(
                  event_id,thread_id,source_device_id,source_surface,source_slot,source_environment,
                  project_id,repo_id,commit_id,kind,status,intent,outcome,next_step,created_at,expires_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    clean.event_id,clean.thread_id,clean.source_device_id,clean.source_surface,
                    clean.source_slot,clean.source_environment,clean.project_id,clean.repo_id,
                    clean.commit_id,clean.kind,clean.status,clean.intent,clean.outcome,
                    clean.next_step,clean.created_at,clean.expires_at,
                ),
            ).lastrowid
            self._prune(db, now)
        return replace(clean, remote_cursor=int(cursor or 0))

    def list_events(self, *, after: int = 0, limit: int = 100, thread_id: str = "") -> list[ContinuityEvent]:
        now = time.time()
        clean_after = max(0, int(after))
        clean_limit = max(1, min(200, int(limit)))
        clean_thread = _opaque(thread_id, 64)
        with self._connect() as db:
            self._prune(db, now)
            if clean_thread:
                rows = db.execute(
                    "SELECT * FROM continuity_hub_event WHERE seq>? AND expires_at>? AND thread_id=? ORDER BY seq LIMIT ?",
                    (clean_after, now, clean_thread, clean_limit),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM continuity_hub_event WHERE seq>? AND expires_at>? ORDER BY seq LIMIT ?",
                    (clean_after, now, clean_limit),
                ).fetchall()
        return [
            continuity_event_from_row(
                row,
                cursor_column="seq",
                max_age_seconds=30 * 24 * 60 * 60,
            )
            for row in rows
        ]

    def thread_summaries(self, limit: int = 20) -> list[dict[str, Any]]:
        now = time.time()
        with self._connect() as db:
            self._prune(db, now)
            rows = db.execute(
                "SELECT * FROM continuity_hub_event WHERE expires_at>? ORDER BY seq DESC LIMIT ?",
                (now, max(20, min(2_000, int(limit) * self.max_rows_per_thread))),
            ).fetchall()
        summaries: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            thread_id = str(row["thread_id"])
            if thread_id in seen:
                continue
            seen.add(thread_id)
            summaries.append({
                "thread_id": thread_id,
                "project_id": str(row["project_id"]),
                "source_surface": str(row["source_surface"]),
                "status": str(row["status"]),
                "intent": str(row["intent"]),
                "outcome": str(row["outcome"]),
                "updated_at": float(row["created_at"]),
                "cursor": int(row["seq"]),
            })
            if len(summaries) >= max(1, min(100, int(limit))):
                break
        return summaries

    def status(self) -> dict[str, Any]:
        now = time.time()
        with self._connect() as db:
            self._prune(db, now)
            row = db.execute(
                "SELECT COUNT(*),COUNT(DISTINCT thread_id),COALESCE(MAX(seq),0) FROM continuity_hub_event WHERE expires_at>?",
                (now,),
            ).fetchone()
        return {
            "events": int(row[0]),
            "threads": int(row[1]),
            "cursor": int(row[2]),
            "max_rows": self.max_rows,
            "max_rows_per_thread": self.max_rows_per_thread,
            "max_threads_per_device": self.max_threads_per_device,
            "max_age_seconds": self.max_age_seconds,
        }

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS continuity_hub_event(
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    thread_id TEXT NOT NULL,
                    source_device_id TEXT NOT NULL,
                    source_surface TEXT NOT NULL,
                    source_slot TEXT NOT NULL,
                    source_environment TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    repo_id TEXT NOT NULL,
                    commit_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    next_step TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS continuity_hub_cursor_idx ON continuity_hub_event(seq,expires_at);
                CREATE INDEX IF NOT EXISTS continuity_hub_thread_idx ON continuity_hub_event(thread_id,seq);
                """
            )

    def _prune(self, db: sqlite3.Connection, now: float) -> None:
        db.execute("DELETE FROM continuity_hub_event WHERE expires_at<=?", (now,))
        db.execute(
            """
            DELETE FROM continuity_hub_event WHERE seq IN (
              SELECT seq FROM (
                SELECT seq,ROW_NUMBER() OVER(PARTITION BY thread_id ORDER BY seq DESC) AS rank_in_thread
                FROM continuity_hub_event
              ) WHERE rank_in_thread>?
            )
            """,
            (self.max_rows_per_thread,),
        )
        db.execute(
            """
            DELETE FROM continuity_hub_event WHERE EXISTS (
              SELECT 1 FROM (
                SELECT source_device_id,thread_id,
                       ROW_NUMBER() OVER(PARTITION BY source_device_id ORDER BY MAX(seq) DESC) AS thread_rank
                FROM continuity_hub_event
                GROUP BY source_device_id,thread_id
              ) ranked
              WHERE ranked.source_device_id=continuity_hub_event.source_device_id
                AND ranked.thread_id=continuity_hub_event.thread_id
                AND ranked.thread_rank>?
            )
            """,
            (self.max_threads_per_device,),
        )
        db.execute(
            "DELETE FROM continuity_hub_event WHERE seq IN (SELECT seq FROM continuity_hub_event ORDER BY seq DESC LIMIT -1 OFFSET ?)",
            (self.max_rows,),
        )

    def _connect(self) -> sqlite3.Connection:
        return connect_state_db(self.path)


def _limits(config: dict[str, Any]) -> tuple[float, int, int, int]:
    block = config.get("consistent_everywhere") if isinstance(config.get("consistent_everywhere"), dict) else {}
    continuity = block.get("continuity") if isinstance(block.get("continuity"), dict) else {}
    return (
        max(60.0, min(30 * 24 * 60 * 60, float(continuity.get("max_age_seconds", DEFAULT_MAX_AGE_SECONDS) or DEFAULT_MAX_AGE_SECONDS))),
        max(100, min(10_000, int(continuity.get("max_rows", DEFAULT_MAX_ROWS) or DEFAULT_MAX_ROWS))),
        max(10, min(500, int(continuity.get("max_rows_per_thread", DEFAULT_MAX_ROWS_PER_THREAD) or DEFAULT_MAX_ROWS_PER_THREAD))),
        max(5, min(500, int(continuity.get("max_threads_per_device", DEFAULT_MAX_THREADS_PER_DEVICE) or DEFAULT_MAX_THREADS_PER_DEVICE))),
    )


def _opaque(value: Any, limit: int) -> str:
    return "".join(ch for ch in str(value or "").strip() if ch.isalnum() or ch in "-_.")[:limit]
