"""Typed, device-scoped MO Everywhere notification rules and delivery rows.

The store deliberately contains no expression language. Event owners publish
typed facts; a rule can match only an allowlisted fact and the requesting
device is the only principal allowed to read, acknowledge, or revoke it.
"""
from __future__ import annotations

import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.state.sqlite import connect_state_db


WORKER_FINISHED = "worker_finished"
WORKER_FAILED = "worker_failed"
WORKER_EVENTS = frozenset({WORKER_FINISHED, WORKER_FAILED})
SCHEDULE_FINISHED = "schedule_finished"
SCHEDULE_FAILED = "schedule_failed"
SCHEDULE_EVENTS = frozenset({SCHEDULE_FINISHED, SCHEDULE_FAILED})
RULE_TTL_SECONDS = 7 * 24 * 60 * 60
NOTICE_TTL_SECONDS = 24 * 60 * 60
MAX_RULES_PER_DEVICE = 50
MAX_NOTICES_PER_DEVICE = 50


class NotificationRuleError(RuntimeError):
    pass


class NotificationRuleConflict(NotificationRuleError):
    pass


@dataclass(frozen=True)
class NotificationRule:
    rule_id: str
    device_id: str
    event_owner: str
    event_type: str
    subject_id: str
    cadence: str
    active: bool
    created_at: float
    expires_at: float
    fired_at: float
    filter_value: str = ""
    comparison: str = ""
    threshold: float = 0.0
    last_value: float | None = None

    def public(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "event_owner": self.event_owner,
            "event_type": self.event_type,
            "subject_id": self.subject_id,
            "cadence": self.cadence,
            "active": self.active,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "fired_at": self.fired_at,
            "filter_value": self.filter_value,
        }


@dataclass(frozen=True)
class NotificationNotice:
    notification_id: str
    device_id: str
    rule_id: str
    event_owner: str
    event_type: str
    subject_id: str
    title: str
    detail: str
    created_at: float
    expires_at: float
    acknowledged_at: float

    def public(self) -> dict[str, Any]:
        return {
            "notification_id": self.notification_id,
            "rule_id": self.rule_id,
            "event_owner": self.event_owner,
            "event_type": self.event_type,
            "subject_id": self.subject_id,
            "title": self.title,
            "detail": self.detail,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
        }


class NotificationRuleStore:
    """Durable typed rules sharing the Everywhere registry SQLite file."""

    def __init__(self, *, path: str | Path):
        self.path = Path(path).expanduser().resolve(strict=False)
        self._initialize()

    def ensure_worker_rule(
        self,
        device_id: str,
        worker_id: str,
        event_type: str,
    ) -> tuple[NotificationRule, bool]:
        device = _identifier(device_id, 160)
        subject = _identifier(worker_id, 80)
        event = str(event_type or "").strip().lower()
        if event not in WORKER_EVENTS:
            raise NotificationRuleError("worker notification event is unsupported")
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._prune(db, now)
            existing = db.execute(
                "SELECT * FROM everywhere_notification_rule "
                "WHERE device_id=? AND event_owner='worker' AND subject_id=? LIMIT 1",
                (device, subject),
            ).fetchone()
            if existing:
                rule = _rule_from_row(existing)
                if rule.event_type != event:
                    raise NotificationRuleConflict(
                        "client_request_id was already used with a different notification rule"
                    )
                return rule, False
            rule_id = secrets.token_hex(16)
            db.execute(
                """
                INSERT INTO everywhere_notification_rule(
                  rule_id,device_id,event_owner,event_type,subject_id,cadence,
                  active,created_at,expires_at,fired_at
                ) VALUES(?,?,'worker',?,?,'once',1,?,?,0)
                """,
                (rule_id, device, event, subject, now, now + RULE_TTL_SECONDS),
            )
            self._prune(db, now)
            row = db.execute(
                "SELECT * FROM everywhere_notification_rule WHERE rule_id=?",
                (rule_id,),
            ).fetchone()
        return _rule_from_row(row), True

    def remove_worker_rule(self, device_id: str, worker_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "DELETE FROM everywhere_notification_rule "
                "WHERE device_id=? AND event_owner='worker' AND subject_id=?",
                (_identifier(device_id, 160), _identifier(worker_id, 80)),
            )

    def ensure_scheduler_rule(
        self,
        device_id: str,
        job_id: str,
        event_type: str,
    ) -> tuple[NotificationRule, bool]:
        device = _identifier(device_id, 160)
        subject = _identifier(job_id, 80)
        event = str(event_type or "").strip().lower()
        if event not in SCHEDULE_EVENTS:
            raise NotificationRuleError("scheduled-task notification event is unsupported")
        return self._ensure_rule(
            device,
            "scheduler",
            event,
            subject,
            cadence="every",
        )

    def scheduler_rule(self, device_id: str, job_id: str) -> NotificationRule | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM everywhere_notification_rule WHERE device_id=? "
                "AND event_owner='scheduler' AND subject_id=? LIMIT 1",
                (_identifier(device_id, 160), _identifier(job_id, 80)),
            ).fetchone()
        return _rule_from_row(row) if row else None

    def remove_scheduler_rule(self, device_id: str, job_id: str) -> None:
        with self._connect() as db:
            db.execute(
                "DELETE FROM everywhere_notification_rule WHERE device_id=? "
                "AND event_owner='scheduler' AND subject_id=?",
                (_identifier(device_id, 160), _identifier(job_id, 80)),
            )

    def _ensure_rule(
        self,
        device_id: str,
        event_owner: str,
        event_type: str,
        subject_id: str,
        *,
        cadence: str,
        filter_value: str = "",
        comparison: str = "",
        threshold: float = 0.0,
    ) -> tuple[NotificationRule, bool]:
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._prune(db, now)
            row = db.execute(
                "SELECT * FROM everywhere_notification_rule WHERE device_id=? "
                "AND event_owner=? AND event_type=? AND subject_id=? "
                "AND filter_value=? AND comparison=? AND threshold=? LIMIT 1",
                (
                    device_id,
                    event_owner,
                    event_type,
                    subject_id,
                    filter_value,
                    comparison,
                    threshold,
                ),
            ).fetchone()
            if row:
                return _rule_from_row(row), False
            rule_id = secrets.token_hex(16)
            db.execute(
                """
                INSERT INTO everywhere_notification_rule(
                  rule_id,device_id,event_owner,event_type,subject_id,cadence,
                  active,created_at,expires_at,fired_at,filter_value,comparison,
                  threshold,last_value
                ) VALUES(?,?,?,?,?,?,1,?,?,0,?,?,?,NULL)
                """,
                (
                    rule_id,
                    device_id,
                    event_owner,
                    event_type,
                    subject_id,
                    cadence,
                    now,
                    now + RULE_TTL_SECONDS,
                    filter_value,
                    comparison,
                    threshold,
                ),
            )
            self._prune(db, now)
            row = db.execute(
                "SELECT * FROM everywhere_notification_rule WHERE rule_id=?",
                (rule_id,),
            ).fetchone()
        return _rule_from_row(row), True

    def worker_rule(self, device_id: str, worker_id: str) -> NotificationRule | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM everywhere_notification_rule "
                "WHERE device_id=? AND event_owner='worker' AND subject_id=? LIMIT 1",
                (_identifier(device_id, 160), _identifier(worker_id, 80)),
            ).fetchone()
        return _rule_from_row(row) if row else None

    def record_worker(self, record: Any) -> list[NotificationNotice]:
        worker_id = _identifier(getattr(record, "id", ""), 80)
        state = str(getattr(record, "state", "") or "").strip().lower()
        if state not in {"completed", "blocked", "cancelled", "paused"}:
            return []
        now = time.time()
        notices: list[NotificationNotice] = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._prune(db, now)
            rows = db.execute(
                "SELECT * FROM everywhere_notification_rule "
                "WHERE event_owner='worker' AND subject_id=? AND active=1 AND expires_at>?",
                (worker_id, now),
            ).fetchall()
            for row in rows:
                rule = _rule_from_row(row)
                matched = rule.event_type == WORKER_FINISHED or (
                    rule.event_type == WORKER_FAILED and state != "completed"
                )
                db.execute(
                    "UPDATE everywhere_notification_rule SET active=0,fired_at=? WHERE rule_id=?",
                    (now if matched else 0.0, rule.rule_id),
                )
                if not matched:
                    continue
                title = "Background task complete" if state == "completed" else "Background task needs attention"
                detail = "MO finished the requested phone task." if state == "completed" else f"The phone task ended as {state}."
                notices.append(self._insert_notice(db, rule, title, detail, now))
            self._prune(db, now)
        return notices

    def record_scheduler(self, run: Any) -> list[NotificationNotice]:
        job_id = _identifier(getattr(run, "job_id", ""), 80)
        status = str(getattr(run, "status", "") or "").strip().lower()
        if status not in {"ok", "error"}:
            return []
        now = time.time()
        notices: list[NotificationNotice] = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._prune(db, now)
            rows = db.execute(
                "SELECT * FROM everywhere_notification_rule WHERE event_owner='scheduler' "
                "AND subject_id=? AND active=1 AND expires_at>?",
                (job_id, now),
            ).fetchall()
            for row in rows:
                rule = _rule_from_row(row)
                matched = rule.event_type == SCHEDULE_FINISHED or status == "error"
                if not matched:
                    continue
                db.execute(
                    "UPDATE everywhere_notification_rule SET fired_at=? WHERE rule_id=?",
                    (now, rule.rule_id),
                )
                title = "Scheduled task complete" if status == "ok" else "Scheduled task needs attention"
                detail = "MO finished a scheduled task." if status == "ok" else "A scheduled task ended with a problem."
                notices.append(self._insert_notice(db, rule, title, detail, now))
            self._prune(db, now)
        return notices

    @staticmethod
    def _insert_notice(
        db: sqlite3.Connection,
        rule: NotificationRule,
        title: str,
        detail: str,
        now: float,
    ) -> NotificationNotice:
        notification_id = secrets.token_hex(16)
        db.execute(
            """
            INSERT INTO everywhere_notification(
              notification_id,device_id,rule_id,event_owner,event_type,
              subject_id,title,detail,created_at,expires_at,acknowledged_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,0)
            """,
            (
                notification_id,
                rule.device_id,
                rule.rule_id,
                rule.event_owner,
                rule.event_type,
                rule.subject_id,
                title[:80],
                detail[:160],
                now,
                now + NOTICE_TTL_SECONDS,
            ),
        )
        row = db.execute(
            "SELECT * FROM everywhere_notification WHERE notification_id=?",
            (notification_id,),
        ).fetchone()
        return _notice_from_row(row)

    def rules(self, device_id: str, *, limit: int = 20) -> list[NotificationRule]:
        now = time.time()
        with self._connect() as db:
            self._prune(db, now)
            rows = db.execute(
                "SELECT * FROM everywhere_notification_rule WHERE device_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (_identifier(device_id, 160), max(1, min(50, int(limit)))),
            ).fetchall()
        return [_rule_from_row(row) for row in rows]

    def pending(self, device_id: str, *, limit: int = 8) -> list[NotificationNotice]:
        now = time.time()
        with self._connect() as db:
            self._prune(db, now)
            rows = db.execute(
                "SELECT * FROM everywhere_notification WHERE device_id=? "
                "AND acknowledged_at=0 AND expires_at>? ORDER BY created_at ASC LIMIT ?",
                (_identifier(device_id, 160), now, max(1, min(8, int(limit)))),
            ).fetchall()
        return [_notice_from_row(row) for row in rows]

    def acknowledge(self, device_id: str, notification_id: str) -> bool:
        now = time.time()
        with self._connect() as db:
            changed = db.execute(
                "UPDATE everywhere_notification SET acknowledged_at=? "
                "WHERE notification_id=? AND device_id=? AND acknowledged_at=0 AND expires_at>?",
                (
                    now,
                    _identifier(notification_id, 64),
                    _identifier(device_id, 160),
                    now,
                ),
            ).rowcount
        return bool(changed)

    def revoke(self, device_id: str, rule_id: str) -> bool:
        with self._connect() as db:
            changed = db.execute(
                "UPDATE everywhere_notification_rule SET active=0 "
                "WHERE rule_id=? AND device_id=? AND active=1",
                (_identifier(rule_id, 64), _identifier(device_id, 160)),
            ).rowcount
        return bool(changed)

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS everywhere_notification_rule(
                  rule_id TEXT PRIMARY KEY,
                  device_id TEXT NOT NULL,
                  event_owner TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  subject_id TEXT NOT NULL,
                  cadence TEXT NOT NULL,
                  active INTEGER NOT NULL,
                  created_at REAL NOT NULL,
                  expires_at REAL NOT NULL,
                  fired_at REAL NOT NULL,
                  filter_value TEXT NOT NULL DEFAULT '',
                  comparison TEXT NOT NULL DEFAULT '',
                  threshold REAL NOT NULL DEFAULT 0,
                  last_value REAL
                );
                CREATE INDEX IF NOT EXISTS everywhere_notification_rule_device_idx
                  ON everywhere_notification_rule(device_id,created_at);
                CREATE TABLE IF NOT EXISTS everywhere_notification(
                  notification_id TEXT PRIMARY KEY,
                  device_id TEXT NOT NULL,
                  rule_id TEXT NOT NULL,
                  event_owner TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  subject_id TEXT NOT NULL,
                  title TEXT NOT NULL,
                  detail TEXT NOT NULL,
                  created_at REAL NOT NULL,
                  expires_at REAL NOT NULL,
                  acknowledged_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS everywhere_notification_device_idx
                  ON everywhere_notification(device_id,created_at);
                """
            )
            self._ensure_rule_columns(db)
            db.execute("DROP INDEX IF EXISTS everywhere_notification_rule_subject_idx")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS everywhere_notification_rule_match_idx "
                "ON everywhere_notification_rule("
                "device_id,event_owner,event_type,subject_id,filter_value,comparison,threshold)"
            )
            self._prune(db, time.time())

    @staticmethod
    def _ensure_rule_columns(db: sqlite3.Connection) -> None:
        columns = {
            str(row[1]) for row in db.execute("PRAGMA table_info(everywhere_notification_rule)")
        }
        additions = {
            "filter_value": "TEXT NOT NULL DEFAULT ''",
            "comparison": "TEXT NOT NULL DEFAULT ''",
            "threshold": "REAL NOT NULL DEFAULT 0",
            "last_value": "REAL",
        }
        for name, declaration in additions.items():
            if name not in columns:
                db.execute(
                    f"ALTER TABLE everywhere_notification_rule ADD COLUMN {name} {declaration}"
                )

    def _prune(self, db: sqlite3.Connection, now: float) -> None:
        db.execute("DELETE FROM everywhere_notification WHERE expires_at<=?", (now,))
        db.execute("DELETE FROM everywhere_notification_rule WHERE expires_at<=?", (now,))
        db.execute(
            """
            DELETE FROM everywhere_notification_rule WHERE rule_id IN (
              SELECT rule_id FROM (
                SELECT rule_id,ROW_NUMBER() OVER(PARTITION BY device_id ORDER BY created_at DESC) AS row_rank
                FROM everywhere_notification_rule
              ) WHERE row_rank>?
            )
            """,
            (MAX_RULES_PER_DEVICE,),
        )
        db.execute(
            """
            DELETE FROM everywhere_notification WHERE notification_id IN (
              SELECT notification_id FROM (
                SELECT notification_id,ROW_NUMBER() OVER(PARTITION BY device_id ORDER BY created_at DESC) AS row_rank
                FROM everywhere_notification
              ) WHERE row_rank>?
            )
            """,
            (MAX_NOTICES_PER_DEVICE,),
        )

    def _connect(self) -> sqlite3.Connection:
        return connect_state_db(self.path)


def _identifier(value: Any, maximum: int) -> str:
    clean = str(value or "").strip()
    if not clean or len(clean) > maximum or any(ord(ch) < 0x21 or ord(ch) > 0x7E for ch in clean):
        raise NotificationRuleError("notification identifier is invalid")
    return clean


def _rule_from_row(row: sqlite3.Row) -> NotificationRule:
    return NotificationRule(
        rule_id=str(row["rule_id"]),
        device_id=str(row["device_id"]),
        event_owner=str(row["event_owner"]),
        event_type=str(row["event_type"]),
        subject_id=str(row["subject_id"]),
        cadence=str(row["cadence"]),
        active=bool(row["active"]),
        created_at=float(row["created_at"]),
        expires_at=float(row["expires_at"]),
        fired_at=float(row["fired_at"]),
        filter_value=str(row["filter_value"]),
        comparison=str(row["comparison"]),
        threshold=float(row["threshold"]),
        last_value=float(row["last_value"]) if row["last_value"] is not None else None,
    )


def _notice_from_row(row: sqlite3.Row) -> NotificationNotice:
    return NotificationNotice(
        notification_id=str(row["notification_id"]),
        device_id=str(row["device_id"]),
        rule_id=str(row["rule_id"]),
        event_owner=str(row["event_owner"]),
        event_type=str(row["event_type"]),
        subject_id=str(row["subject_id"]),
        title=str(row["title"]),
        detail=str(row["detail"]),
        created_at=float(row["created_at"]),
        expires_at=float(row["expires_at"]),
        acknowledged_at=float(row["acknowledged_at"]),
    )
