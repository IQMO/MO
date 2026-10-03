"""Bounded device-local continuity journal for MO Everywhere.

Terminals only write small SQLite rows. They never perform networking or Git
work. One optional coordinator delivers the outbox and imports hub events.
The journal is thread-scoped so a globally latest completion cannot steal the
focus of another terminal or project.
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from ..runtime.backend_monitor import redact_monitor_text
from ..utils.text_safety import sanitize_unicode_text
from .paths import EVERYWHERE_DEVICE_DB_PATH, resolve_state_path
from .sqlite import connect_state_db


LOCAL_CONTINUITY_PATH = EVERYWHERE_DEVICE_DB_PATH
EVENT_VERSION = 1
EVENT_STATUSES = frozenset({"active", "paused", "completed", "cancelled", "failed"})
EVENT_KINDS = frozenset({"turn", "notice", "focus", "interrupt"})
CONTINUITY_SURFACES = frozenset({"terminal", "telegram", "mo_desktop", "companion", "api"})
_CONTINUITY_SURFACE_ALIASES = {
    "user": "terminal",
    "main": "terminal",
    "desktop": "mo_desktop",
}
MAX_INTENT_CHARS = 1_200
MAX_OUTCOME_CHARS = 2_400
MAX_NEXT_STEP_CHARS = 600
MAX_LOCAL_ROWS = 2_000
DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 60 * 60

# COMPAT(continuity-metadata-v0): replaced-by opaque source slots and current owner ids; remove-when supported rows are rewritten and the event TTL has elapsed
_LATEST_OTHER_EVENT_PER_THREAD_CTE = """
WITH ranked_event AS (
    SELECT e.*,e.rowid AS local_rowid,
           ROW_NUMBER() OVER (
               PARTITION BY e.thread_id
               ORDER BY e.created_at DESC,e.remote_cursor DESC,e.rowid DESC
           ) AS continuity_rank
    FROM continuity_event e
    WHERE e.expires_at>?
      AND NOT (e.source_surface=? AND e.source_slot IN (?,?))
),
latest_event AS (
    SELECT * FROM ranked_event WHERE continuity_rank=1
)
"""


def continuity_status_from_db(
    db: sqlite3.Connection,
    *,
    now: float | None = None,
) -> dict[str, int]:
    """Return the shared journal counters from an already-open database."""
    active_after = time.time() if now is None else float(now)
    counts = {
        str(row[0]): int(row[1])
        for row in db.execute(
            "SELECT direction,COUNT(*) FROM continuity_event WHERE expires_at>? GROUP BY direction",
            (active_after,),
        ).fetchall()
    }
    pending = int(
        db.execute(
            "SELECT COUNT(*) FROM continuity_event "
            "WHERE direction='outbound' AND delivered_at IS NULL AND expires_at>?",
            (active_after,),
        ).fetchone()[0]
    )
    bindings = int(db.execute("SELECT COUNT(*) FROM continuity_binding").fetchone()[0])
    return {
        "outbound": counts.get("outbound", 0),
        "inbound": counts.get("inbound", 0),
        "pending": pending,
        "bindings": bindings,
    }


class ContinuityEventError(ValueError):
    """A continuity event failed the bounded, prompt-safe contract."""


@dataclass(frozen=True)
class ContinuityEvent:
    version: int
    event_id: str
    thread_id: str
    source_device_id: str
    source_surface: str
    source_slot: str
    source_environment: str
    project_id: str
    repo_id: str
    commit_id: str
    kind: str
    status: str
    intent: str
    outcome: str
    next_step: str
    created_at: float
    expires_at: float
    remote_cursor: int = 0
    capability_note: str = ""

    def as_dict(self, *, include_cursor: bool = True) -> dict[str, Any]:
        data = asdict(self)
        data.pop("capability_note", None)
        if not include_cursor:
            data.pop("remote_cursor", None)
        return data

    @classmethod
    def from_dict(
        cls,
        raw: dict[str, Any],
        *,
        source_device_id: str = "",
        max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    ) -> "ContinuityEvent":
        if not isinstance(raw, dict) or int(raw.get("version") or 0) != EVENT_VERSION:
            raise ContinuityEventError("unsupported continuity event version")
        now = time.time()
        created_at = float(raw.get("created_at") or 0.0)
        if created_at <= 0 or created_at > now + 300:
            raise ContinuityEventError("invalid continuity event timestamp")
        configured_age = max(60.0, min(30 * 24 * 60 * 60, float(max_age_seconds or DEFAULT_MAX_AGE_SECONDS)))
        requested_expiry = float(raw.get("expires_at") or created_at + configured_age)
        expires_at = min(requested_expiry, created_at + configured_age)
        if expires_at <= created_at:
            raise ContinuityEventError("invalid continuity event expiry")

        event_id = _opaque_token(raw.get("event_id"), 64)
        thread_id = _opaque_token(raw.get("thread_id"), 64)
        device_id = _opaque_token(source_device_id or raw.get("source_device_id"), 64)
        source_surface = validate_continuity_source(raw.get("source_surface"))
        source_slot = _opaque_token(raw.get("source_slot"), 64)
        kind = str(raw.get("kind") or "turn").strip().lower()
        status = str(raw.get("status") or "completed").strip().lower()
        if not event_id or not thread_id or not device_id or not source_slot:
            raise ContinuityEventError("continuity event identifiers are required")
        if kind not in EVENT_KINDS or status not in EVENT_STATUSES:
            raise ContinuityEventError("invalid continuity event kind or status")
        intent = _bounded_text(raw.get("intent"), MAX_INTENT_CHARS)
        outcome = _bounded_text(raw.get("outcome"), MAX_OUTCOME_CHARS)
        next_step = _bounded_text(raw.get("next_step"), MAX_NEXT_STEP_CHARS)
        if kind == "turn" and (not intent or not outcome):
            raise ContinuityEventError("turn continuity requires bounded intent and outcome")
        return cls(
            version=EVENT_VERSION,
            event_id=event_id,
            thread_id=thread_id,
            source_device_id=device_id,
            source_surface=source_surface,
            source_slot=source_slot,
            source_environment=_enum_token(raw.get("source_environment"), {"workstation", "server", "mobile", "unknown"}, "unknown"),
            project_id=_opaque_token(raw.get("project_id"), 64),
            repo_id=_opaque_token(raw.get("repo_id"), 64),
            commit_id=_opaque_token(raw.get("commit_id"), 64),
            kind=kind,
            status=status,
            intent=intent,
            outcome=outcome,
            next_step=next_step,
            created_at=created_at,
            expires_at=expires_at,
            remote_cursor=max(0, int(raw.get("remote_cursor") or 0)),
        )


def new_event(
    *,
    source_device_id: str,
    source_surface: str,
    source_slot: str,
    thread_id: str,
    intent: str,
    outcome: str,
    project_id: str = "",
    repo_id: str = "",
    commit_id: str = "",
    source_environment: str = "unknown",
    kind: str = "turn",
    status: str = "completed",
    next_step: str = "",
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
) -> ContinuityEvent:
    now = time.time()
    raw = {
        "version": EVENT_VERSION,
        "event_id": secrets.token_hex(16),
        "thread_id": thread_id,
        "source_device_id": source_device_id,
        "source_surface": source_surface,
        "source_slot": source_slot,
        "source_environment": source_environment,
        "project_id": project_id,
        "repo_id": repo_id,
        "commit_id": commit_id,
        "kind": kind,
        "status": status,
        "intent": intent,
        "outcome": outcome,
        "next_step": next_step,
        "created_at": now,
        "expires_at": now + max(60.0, float(max_age_seconds or DEFAULT_MAX_AGE_SECONDS)),
    }
    return ContinuityEvent.from_dict(raw, max_age_seconds=max_age_seconds)


class LocalContinuityStore:
    """Concurrent local outbox/inbox, cursor, consumption, and focus bindings."""

    def __init__(self, config: dict[str, Any] | None = None, *, path: str | Path | None = None):
        self.config = config or {}
        configured = path or resolve_state_path(LOCAL_CONTINUITY_PATH, self.config)
        self.path = Path(configured).expanduser().resolve(strict=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def thread_for_surface(
        self,
        surface: str,
        slot: str,
        *,
        project_id: str,
        device_id: str,
    ) -> str:
        target = target_key(surface, slot)
        with self._connect() as db:
            row = db.execute("SELECT thread_id FROM continuity_binding WHERE target_key=?", (target,)).fetchone()
            if row and _opaque_token(row[0], 64):
                return str(row[0])
            thread_id = default_thread_id(
                surface,
                slot,
                project_id=project_id,
                device_id=device_id,
            )
            db.execute(
                "INSERT OR REPLACE INTO continuity_binding(target_key,surface,thread_id,project_id,updated_at) VALUES(?,?,?,?,?)",
                (target, normalize_continuity_target(surface), thread_id, _opaque_token(project_id, 64), time.time()),
            )
        return thread_id

    def bind(self, surface: str, slot: str, thread_id: str, *, project_id: str = "") -> None:
        clean_thread = _opaque_token(thread_id, 64)
        if not clean_thread:
            raise ContinuityEventError("thread id is required")
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO continuity_binding(target_key,surface,thread_id,project_id,updated_at) VALUES(?,?,?,?,?)",
                (target_key(surface, slot), normalize_continuity_target(surface), clean_thread, _opaque_token(project_id, 64), time.time()),
            )

    def binding(self, surface: str, slot: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT surface,thread_id,project_id,updated_at FROM continuity_binding WHERE target_key=?",
                (target_key(surface, slot),),
            ).fetchone()
        return dict(row) if row else None

    def append_outbound(self, event: ContinuityEvent) -> bool:
        return self._insert(event, direction="outbound")

    def ingest_inbound(self, events: Iterable[ContinuityEvent]) -> int:
        added = 0
        for event in events:
            added += int(self._insert(event, direction="inbound"))
        self.prune()
        return added

    def pending_outbound(self, limit: int = 50) -> list[ContinuityEvent]:
        now = time.time()
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT * FROM continuity_event
                WHERE direction='outbound' AND delivered_at IS NULL AND expires_at>?
                ORDER BY created_at,event_id LIMIT ?
                """,
                (now, max(1, min(200, int(limit)))),
            ).fetchall()
        return [
            continuity_event_from_row(row, cursor_column="remote_cursor")
            for row in rows
        ]

    def mark_delivered(self, event_id: str, remote_cursor: int) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE continuity_event SET delivered_at=?,remote_cursor=MAX(remote_cursor,?) WHERE event_id=?",
                (time.time(), max(0, int(remote_cursor)), _opaque_token(event_id, 64)),
            )

    def pending_for(
        self,
        *,
        target_surface: str,
        target_slot: str,
        project_id: str = "",
        repo_id: str = "",
        commit_id: str = "",
    ) -> ContinuityEvent | None:
        now = time.time()
        surface = normalize_continuity_target(target_surface)
        slot = opaque_id(surface, target_slot)
        raw_slot = _opaque_token(target_slot, 64)
        key = target_key(surface, target_slot)
        with self._connect() as db:
            binding = db.execute(
                "SELECT thread_id,project_id FROM continuity_binding WHERE target_key=?",
                (key,),
            ).fetchone()
            params: list[Any] = [now, surface, slot, raw_slot, key]
            thread_clause = ""
            if binding and _opaque_token(binding[0], 64):
                thread_clause = " AND e.thread_id=?"
                params.append(str(binding[0]))
            rows = db.execute(
                _LATEST_OTHER_EVENT_PER_THREAD_CTE + """
                SELECT e.* FROM latest_event e
                WHERE e.status IN ('completed','paused','cancelled','failed')
                  AND NOT EXISTS(
                    SELECT 1 FROM continuity_consumption c
                    WHERE c.target_key=? AND c.event_id=e.event_id
                  )
                """ + thread_clause + " ORDER BY e.created_at DESC,e.remote_cursor DESC,e.local_rowid DESC,e.event_id DESC LIMIT 50",
                tuple(params),
            ).fetchall()
        if not rows:
            return None
        if not binding:
            threads = {str(row["thread_id"]) for row in rows}
            if len(threads) != 1:
                return None
        event = continuity_event_from_row(
            rows[0], cursor_column="remote_cursor"
        )
        clean_project = _opaque_token(project_id, 64)
        clean_repo = _opaque_token(repo_id, 64)
        clean_commit = _opaque_token(commit_id, 64)
        same_repo = bool(clean_repo and event.repo_id and clean_repo == event.repo_id)
        if clean_project and event.project_id and clean_project != event.project_id and not same_repo:
            return replace(event, capability_note="The target is on a different project; orient only and do not claim local execution is available.")
        if clean_repo and event.repo_id and clean_repo != event.repo_id:
            return replace(event, capability_note="The target does not have the same repository identity; orient only and do not claim local execution is available.")
        if clean_commit and event.commit_id and clean_commit != event.commit_id:
            return replace(event, capability_note="The repository commit differs from the source surface; verify branch, working tree, and referenced files before acting.")
        return event

    def consume(self, event: ContinuityEvent, *, target_surface: str, target_slot: str) -> None:
        key = target_key(target_surface, target_slot)
        now = time.time()
        with self._connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO continuity_consumption(target_key,event_id,consumed_at) VALUES(?,?,?)",
                (key, event.event_id, now),
            )
            db.execute(
                "INSERT OR REPLACE INTO continuity_binding(target_key,surface,thread_id,project_id,updated_at) VALUES(?,?,?,?,?)",
                (key, normalize_continuity_target(target_surface), event.thread_id, event.project_id, now),
            )

    def thread_choices(self, *, target_surface: str, target_slot: str, limit: int = 8) -> list[dict[str, Any]]:
        key = target_key(target_surface, target_slot)
        surface = normalize_continuity_target(target_surface)
        slot = opaque_id(surface, target_slot)
        raw_slot = _opaque_token(target_slot, 64)
        now = time.time()
        with self._connect() as db:
            rows = db.execute(
                _LATEST_OTHER_EVENT_PER_THREAD_CTE + """
                SELECT e.thread_id,e.project_id,e.source_surface,e.source_slot,e.status,e.intent,e.outcome,e.created_at AS updated_at
                FROM latest_event e
                WHERE e.status IN ('completed','paused','cancelled','failed')
                  AND NOT EXISTS(
                    SELECT 1 FROM continuity_consumption c WHERE c.target_key=? AND c.event_id=e.event_id
                  )
                ORDER BY e.created_at DESC,e.remote_cursor DESC,e.local_rowid DESC,e.event_id DESC LIMIT 200
                """,
                (now, surface, slot, raw_slot, key),
            ).fetchall()
        choices: list[dict[str, Any]] = []
        seen: set[str] = set()
        wanted = max(1, min(20, int(limit)))
        for row in rows:
            thread_id = str(row["thread_id"])
            if thread_id in seen:
                continue
            seen.add(thread_id)
            choices.append(dict(row))
            if len(choices) >= wanted:
                break
        return choices

    def latest_inbound_rowid(self) -> int:
        with self._connect() as db:
            row = db.execute("SELECT COALESCE(MAX(rowid),0) FROM continuity_event WHERE direction='inbound'").fetchone()
        return max(0, int(row[0])) if row else 0

    def latest_rowid(self) -> int:
        with self._connect() as db:
            row = db.execute("SELECT COALESCE(MAX(rowid),0) FROM continuity_event").fetchone()
        return max(0, int(row[0])) if row else 0

    def inbound_after(self, rowid: int, *, limit: int = 20) -> list[tuple[int, ContinuityEvent]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT rowid AS local_rowid,* FROM continuity_event WHERE direction='inbound' AND rowid>? AND expires_at>? ORDER BY rowid LIMIT ?",
                (max(0, int(rowid)), time.time(), max(1, min(100, int(limit)))),
            ).fetchall()
        return [
            (
                int(row["local_rowid"]),
                continuity_event_from_row(row, cursor_column="remote_cursor"),
            )
            for row in rows
        ]

    def events_after(self, rowid: int, *, limit: int = 20) -> list[tuple[int, ContinuityEvent]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT rowid AS local_rowid,* FROM continuity_event WHERE rowid>? AND expires_at>? ORDER BY rowid LIMIT ?",
                (max(0, int(rowid)), time.time(), max(1, min(100, int(limit)))),
            ).fetchall()
        return [
            (
                int(row["local_rowid"]),
                continuity_event_from_row(row, cursor_column="remote_cursor"),
            )
            for row in rows
        ]

    def latest_for_thread(self, thread_id: str, *, source_surface: str = "") -> ContinuityEvent | None:
        clean_thread = _opaque_token(thread_id, 64)
        if not clean_thread:
            return None
        params: list[Any] = [clean_thread, time.time()]
        surface_clause = ""
        if source_surface:
            surface_clause = " AND source_surface=?"
            params.append(normalize_continuity_target(source_surface))
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM continuity_event WHERE thread_id=? AND expires_at>?" + surface_clause + " ORDER BY created_at DESC,event_id DESC LIMIT 1",
                tuple(params),
            ).fetchone()
        return (
            continuity_event_from_row(row, cursor_column="remote_cursor")
            if row
            else None
        )

    def cursor(self, hub_key: str) -> int:
        with self._connect() as db:
            row = db.execute("SELECT remote_cursor FROM continuity_cursor WHERE hub_key=?", (_opaque_token(hub_key, 120),)).fetchone()
        return max(0, int(row[0])) if row else 0

    def set_cursor(self, hub_key: str, remote_cursor: int) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO continuity_cursor(hub_key,remote_cursor,updated_at) VALUES(?,?,?)",
                (_opaque_token(hub_key, 120), max(0, int(remote_cursor)), time.time()),
            )

    def status(self) -> dict[str, Any]:
        with self._connect() as db:
            return continuity_status_from_db(db)

    def prune(self, *, max_rows: int = MAX_LOCAL_ROWS) -> None:
        now = time.time()
        keep = max(100, min(10_000, int(max_rows)))
        with self._connect() as db:
            db.execute("DELETE FROM continuity_event WHERE expires_at<=?", (now,))
            db.execute("DELETE FROM continuity_consumption WHERE event_id NOT IN (SELECT event_id FROM continuity_event)")
            db.execute(
                "DELETE FROM continuity_event WHERE event_id IN (SELECT event_id FROM continuity_event ORDER BY created_at DESC,rowid DESC LIMIT -1 OFFSET ?)",
                (keep,),
            )
            db.execute(
                "DELETE FROM continuity_binding WHERE target_key IN (SELECT target_key FROM continuity_binding ORDER BY updated_at DESC LIMIT -1 OFFSET 500)"
            )

    def _insert(self, event: ContinuityEvent, *, direction: str) -> bool:
        if direction not in {"outbound", "inbound"}:
            raise ContinuityEventError("invalid continuity direction")
        clean = ContinuityEvent.from_dict(event.as_dict())
        with self._connect() as db:
            changed = db.execute(
                """
                INSERT OR IGNORE INTO continuity_event(
                  event_id,direction,remote_cursor,thread_id,source_device_id,source_surface,source_slot,
                  source_environment,project_id,repo_id,commit_id,kind,status,intent,outcome,next_step,
                  created_at,expires_at,delivered_at,inserted_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?)
                """,
                (
                    clean.event_id,direction,clean.remote_cursor,clean.thread_id,clean.source_device_id,
                    clean.source_surface,clean.source_slot,clean.source_environment,clean.project_id,
                    clean.repo_id,clean.commit_id,clean.kind,clean.status,clean.intent,clean.outcome,
                    clean.next_step,clean.created_at,clean.expires_at,time.time(),
                ),
            ).rowcount
            if clean.remote_cursor:
                db.execute(
                    "UPDATE continuity_event SET remote_cursor=MAX(remote_cursor,?) WHERE event_id=?",
                    (clean.remote_cursor, clean.event_id),
                )
        return changed == 1

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS continuity_event(
                    event_id TEXT PRIMARY KEY,
                    direction TEXT NOT NULL,
                    remote_cursor INTEGER NOT NULL DEFAULT 0,
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
                    expires_at REAL NOT NULL,
                    delivered_at REAL,
                    inserted_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS continuity_event_pending_idx ON continuity_event(direction,delivered_at,created_at);
                CREATE INDEX IF NOT EXISTS continuity_event_thread_idx ON continuity_event(thread_id,created_at);
                CREATE TABLE IF NOT EXISTS continuity_consumption(
                    target_key TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    consumed_at REAL NOT NULL,
                    PRIMARY KEY(target_key,event_id)
                );
                CREATE TABLE IF NOT EXISTS continuity_binding(
                    target_key TEXT PRIMARY KEY,
                    surface TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS continuity_cursor(
                    hub_key TEXT PRIMARY KEY,
                    remote_cursor INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return connect_state_db(self.path)


def _canonical_continuity_surface(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _CONTINUITY_SURFACE_ALIASES.get(text, text)


def normalize_continuity_target(value: Any) -> str:
    """Coerce a trusted local target to the default terminal continuity lane."""
    surface = _canonical_continuity_surface(value)
    return surface if surface in CONTINUITY_SURFACES else "terminal"


def validate_continuity_source(value: Any) -> str:
    """Validate untrusted event input instead of silently relabeling it terminal."""
    surface = _canonical_continuity_surface(value)
    if surface not in CONTINUITY_SURFACES:
        raise ContinuityEventError("invalid continuity event source surface")
    return surface


def target_key(surface: str, slot: str) -> str:
    material = f"{normalize_continuity_target(surface)}\0{str(slot or '').strip()}"
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()[:32]


def default_thread_id(surface: str, slot: str, *, project_id: str, device_id: str) -> str:
    material = "\0".join((
        _opaque_token(device_id, 64),
        normalize_continuity_target(surface),
        _opaque_token(slot, 64),
        _opaque_token(project_id, 64),
    ))
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()[:32]


def opaque_id(namespace: str, value: Any, *, limit: int = 32) -> str:
    material = f"{str(namespace or '').strip()}\0{str(value or '').strip()}"
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()[: max(8, min(64, limit))]


def continuity_event_from_row(
    row: sqlite3.Row,
    *,
    cursor_column: str,
    max_age_seconds: float | None = None,
) -> ContinuityEvent:
    """Decode the shared continuity row shape with an explicit cursor owner."""
    raw = {
        "version": EVENT_VERSION,
        "event_id": row["event_id"],
        "thread_id": row["thread_id"],
        "source_device_id": row["source_device_id"],
        "source_surface": row["source_surface"],
        "source_slot": row["source_slot"],
        "source_environment": row["source_environment"],
        "project_id": row["project_id"],
        "repo_id": row["repo_id"],
        "commit_id": row["commit_id"],
        "kind": row["kind"],
        "status": row["status"],
        "intent": row["intent"],
        "outcome": row["outcome"],
        "next_step": row["next_step"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "remote_cursor": row[cursor_column],
    }
    if max_age_seconds is None:
        return ContinuityEvent.from_dict(raw)
    return ContinuityEvent.from_dict(raw, max_age_seconds=max_age_seconds)


def _bounded_text(value: Any, limit: int) -> str:
    text = sanitize_unicode_text(value).strip()
    text = redact_monitor_text(text, limit)
    return " ".join(text.replace("\x00", " ").split())[:limit]


def _opaque_token(value: Any, limit: int) -> str:
    return "".join(ch for ch in str(value or "").strip() if ch.isalnum() or ch in "-_.")[:limit]


def _enum_token(value: Any, allowed: set[str], default: str) -> str:
    token = str(value or "").strip().lower()
    return token if token in allowed else default
