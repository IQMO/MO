"""Durable, bounded device turn jobs with reconnect and truthful cancellation."""
from __future__ import annotations

import hashlib
import queue
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from core.state.sqlite import connect_state_db
from core.session.session import assistant_result_is_incomplete
from core.mail.intent import is_mail_sensitive_request

from .registry import DevicePrincipal


JOB_STATES = frozenset({"queued", "running", "cancelling", "completed", "cancelled", "failed", "interrupted"})
DEFAULT_JOB_TTL = 24 * 60 * 60
DEFAULT_MAX_JOBS_PER_DEVICE = 50
MAX_REPLY_CHARS = 40_000
MAX_CLIENT_REQUEST_ID_CHARS = 64
_MAIL_OMITTED = "[Mail turn omitted from saved job]"
# COMPAT(mail-job-placeholder-20260928): replaced-by _MAIL_OMITTED; remove-when no supported saved jobs contain the Gmail-specific placeholder.
_OLD_MAIL_OMITTED = "[Gmail turn omitted from saved job]"


def _mail_placeholder(value: str) -> bool:
    return value in {_MAIL_OMITTED, _OLD_MAIL_OMITTED}


class TurnJobError(RuntimeError):
    pass


class TurnJobConflict(TurnJobError):
    """A valid submission conflicts with existing device job state."""


class TurnJobRequestError(TurnJobError):
    """A submitted idempotency key is malformed."""


@dataclass(frozen=True)
class TurnJobSubmission:
    job: "TurnJob"
    created: bool


@dataclass(frozen=True)
class TurnJob:
    job_id: str
    device_id: str
    text: str
    status: str
    reply: str
    error: str
    created_at: float
    started_at: float
    completed_at: float
    expires_at: float
    cancel_requested: bool

    def public(self, *, include_text: bool = True) -> dict[str, Any]:
        data = {
            "job_id": self.job_id,
            "status": self.status,
            "reply": self.reply,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "cancel_requested": self.cancel_requested,
        }
        if include_text:
            data["text"] = self.text
        return data


class TurnJobStore:
    def __init__(self, config: dict[str, Any] | None = None, *, path: str | Path):
        self.config = config or {}
        self.path = Path(path).expanduser().resolve(strict=False)
        self.ttl_seconds, self.max_per_device = _limits(self.config)
        self._mail_lock = threading.RLock()
        self._live_mail: dict[str, tuple[str, str, float]] = {}
        self._initialize()

    def create(
        self,
        principal: DevicePrincipal,
        text: str,
        *,
        client_request_id: str | None = None,
        request_context: str = "",
    ) -> TurnJob:
        return self.create_submission(
            principal,
            text,
            client_request_id=client_request_id,
            request_context=request_context,
        ).job

    def create_submission(
        self,
        principal: DevicePrincipal,
        text: str,
        *,
        client_request_id: str | None = None,
        request_context: str = "",
    ) -> TurnJobSubmission:
        clean_text = str(text or "").strip()
        if not clean_text or len(clean_text) > 20_000:
            raise TurnJobError("turn text is required")
        request_key = _client_request_key(client_request_id)
        request_fingerprint = _request_fingerprint(clean_text, request_context) if request_key else ""
        now = time.time()
        job_id = secrets.token_hex(16)
        mail_turn = is_mail_sensitive_request(clean_text, include_approval=True)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._prune(db, now)
            if request_key:
                existing = db.execute(
                    "SELECT * FROM everywhere_turn_job WHERE device_id=? AND client_request_key=? LIMIT 1",
                    (principal.device_id, request_key),
                ).fetchone()
                if existing:
                    if str(existing["request_fingerprint"] or "") != request_fingerprint:
                        raise TurnJobConflict(
                            "client_request_id was already used with different text, attachments, or conversation"
                        )
                    return TurnJobSubmission(self._with_live_mail(_from_row(existing)), False)
            active = db.execute(
                "SELECT 1 FROM everywhere_turn_job WHERE device_id=? AND status IN ('queued','running','cancelling') AND expires_at>? LIMIT 1",
                (principal.device_id, now),
            ).fetchone()
            if active:
                raise TurnJobConflict("device already has an active turn job")
            db.execute(
                """
                INSERT INTO everywhere_turn_job(
                  job_id,device_id,text,status,reply,error,created_at,started_at,completed_at,expires_at,
                  cancel_requested,client_request_key,request_fingerprint
                ) VALUES(?,?,?,'queued','','',?,0,0,?,0,?,?)
                """,
                (
                    job_id,
                    principal.device_id,
                    _MAIL_OMITTED if mail_turn else clean_text,
                    now,
                    now + self.ttl_seconds,
                    request_key,
                    request_fingerprint,
                ),
            )
        if mail_turn:
            with self._mail_lock:
                self._live_mail[job_id] = (clean_text, "", now + self.ttl_seconds)
        return TurnJobSubmission(self.get(job_id, principal.device_id), True)

    def get(self, job_id: str, device_id: str) -> TurnJob:
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM everywhere_turn_job WHERE job_id=? AND device_id=? AND expires_at>?",
                (_id(job_id), _id(device_id), time.time()),
            ).fetchone()
        if not row:
            raise TurnJobError("turn job was not found")
        return self._with_live_mail(_from_row(row))

    def history(self, device_id: str, limit: int = 20) -> list[TurnJob]:
        now = time.time()
        with self._connect() as db:
            self._prune(db, now)
            rows = db.execute(
                "SELECT * FROM everywhere_turn_job WHERE device_id=? AND expires_at>? ORDER BY created_at DESC LIMIT ?",
                (_id(device_id), now, max(1, min(50, int(limit)))),
            ).fetchall()
        return [self._with_live_mail(_from_row(row)) for row in rows]

    def start(self, job_id: str, device_id: str) -> TurnJob | None:
        now = time.time()
        with self._connect() as db:
            changed = db.execute(
                """
                UPDATE everywhere_turn_job SET status='running',started_at=?
                WHERE job_id=? AND device_id=? AND status='queued' AND cancel_requested=0 AND expires_at>?
                """,
                (now, _id(job_id), _id(device_id), now),
            ).rowcount
        return self.get(job_id, device_id) if changed else None

    def complete(
        self,
        job_id: str,
        device_id: str,
        reply: str,
        *,
        before_commit: Callable[[bool], None] | None = None,
    ) -> TurnJob:
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT cancel_requested,text FROM everywhere_turn_job WHERE job_id=? AND device_id=?",
                (_id(job_id), _id(device_id)),
            ).fetchone()
            cancelled = bool(row and row[0])
            mail_turn = bool(row and _mail_placeholder(row["text"]))
            if before_commit is not None:
                before_commit(cancelled)
            failed = not cancelled and assistant_result_is_incomplete(reply)
            db.execute(
                """
                UPDATE everywhere_turn_job
                SET status=?,reply=?,error=?,completed_at=?
                WHERE job_id=? AND device_id=?
                """,
                (
                    "cancelled" if cancelled else "failed" if failed else "completed",
                    "" if cancelled or failed else _MAIL_OMITTED if mail_turn else str(reply or "")[:MAX_REPLY_CHARS],
                    ("Mail turn failed" if mail_turn else str(reply or "")[:200]) if failed else "",
                    now,
                    _id(job_id),
                    _id(device_id),
                ),
            )
        if mail_turn and not cancelled and not failed:
            with self._mail_lock:
                live = self._live_mail.get(job_id)
                if live:
                    self._live_mail[job_id] = (live[0], str(reply or "")[:MAX_REPLY_CHARS], live[2])
        return self.get(job_id, device_id)

    def fail(self, job_id: str, device_id: str, error: str = "MO turn failed") -> TurnJob:
        now = time.time()
        with self._connect() as db:
            row = db.execute(
                "SELECT cancel_requested,text FROM everywhere_turn_job WHERE job_id=? AND device_id=?",
                (_id(job_id), _id(device_id)),
            ).fetchone()
            cancelled = bool(row and row[0])
            db.execute(
                "UPDATE everywhere_turn_job SET status=?,reply='',error=?,completed_at=? WHERE job_id=? AND device_id=?",
                ("cancelled" if cancelled else "failed", "" if cancelled else "Mail turn failed" if row and _mail_placeholder(row["text"]) else str(error or "MO turn failed")[:200], now, _id(job_id), _id(device_id)),
            )
        return self.get(job_id, device_id)

    def interrupt(self, job_id: str, device_id: str) -> TurnJob:
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT cancel_requested FROM everywhere_turn_job WHERE job_id=? AND device_id=?",
                (_id(job_id), _id(device_id)),
            ).fetchone()
            cancelled = bool(row and row[0])
            db.execute(
                "UPDATE everywhere_turn_job SET status=?,reply='',error=?,completed_at=? WHERE job_id=? AND device_id=?",
                (
                    "cancelled" if cancelled else "interrupted",
                    "" if cancelled else "Hub stopped before completion",
                    now,
                    _id(job_id),
                    _id(device_id),
                ),
            )
        return self.get(job_id, device_id)

    def cancel(self, job_id: str, device_id: str) -> TurnJob:
        now = time.time()
        with self._connect() as db:
            row = db.execute(
                "SELECT status FROM everywhere_turn_job WHERE job_id=? AND device_id=? AND expires_at>?",
                (_id(job_id), _id(device_id), now),
            ).fetchone()
            if not row:
                raise TurnJobError("turn job was not found")
            status = str(row[0])
            if status == "queued":
                db.execute(
                    "UPDATE everywhere_turn_job SET status='cancelled',cancel_requested=1,completed_at=? WHERE job_id=?",
                    (now, _id(job_id)),
                )
            elif status in {"running", "cancelling"}:
                db.execute(
                    "UPDATE everywhere_turn_job SET status='cancelling',cancel_requested=1 WHERE job_id=?",
                    (_id(job_id),),
                )
        return self.get(job_id, device_id)

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS everywhere_turn_job(
                    job_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reply TEXT NOT NULL,
                    error TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    started_at REAL NOT NULL,
                    completed_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    client_request_key TEXT,
                    request_fingerprint TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS everywhere_turn_job_device_idx ON everywhere_turn_job(device_id,created_at);
                """
            )
            columns = {str(row[1]) for row in db.execute("PRAGMA table_info(everywhere_turn_job)").fetchall()}
            if "client_request_key" not in columns:
                db.execute("ALTER TABLE everywhere_turn_job ADD COLUMN client_request_key TEXT")
            if "request_fingerprint" not in columns:
                db.execute("ALTER TABLE everywhere_turn_job ADD COLUMN request_fingerprint TEXT NOT NULL DEFAULT ''")
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS everywhere_turn_job_request_idx "
                "ON everywhere_turn_job(device_id,client_request_key) WHERE client_request_key IS NOT NULL"
            )
            now = time.time()
            db.execute(
                "UPDATE everywhere_turn_job SET status='interrupted',error='Hub restarted before completion',completed_at=? WHERE status IN ('queued','running','cancelling')",
                (now,),
            )
            self._prune(db, now)

    def _prune(self, db: sqlite3.Connection, now: float) -> None:
        db.execute("DELETE FROM everywhere_turn_job WHERE expires_at<=?", (now,))
        db.execute(
            """
            DELETE FROM everywhere_turn_job WHERE job_id IN (
              SELECT job_id FROM (
                SELECT job_id,ROW_NUMBER() OVER(PARTITION BY device_id ORDER BY created_at DESC) AS rank_for_device
                FROM everywhere_turn_job
              ) WHERE rank_for_device>?
            )
            """,
            (self.max_per_device,),
        )

    def _connect(self) -> sqlite3.Connection:
        return connect_state_db(self.path)

    def _with_live_mail(self, job: TurnJob) -> TurnJob:
        if not _mail_placeholder(job.text):
            return job
        self.prune_live_mail()
        with self._mail_lock:
            live = self._live_mail.get(job.job_id)
        if live is None:
            return job
        return replace(job, text=live[0], reply=live[1] if _mail_placeholder(job.reply) else job.reply)

    def prune_live_mail(self) -> None:
        now = time.time()
        with self._mail_lock:
            self._live_mail = {key: value for key, value in self._live_mail.items() if value[2] > now}


class TurnJobRunner:
    """One bounded worker; cancellation is cooperative and reported honestly."""

    def __init__(
        self,
        store: TurnJobStore,
        run_turn: Callable[[DevicePrincipal, str, str, threading.Event], str],
        *,
        on_start: Callable[[TurnJob], None] | None = None,
        on_finish: Callable[[TurnJob], None] | None = None,
        on_before_complete: Callable[[str, bool], None] | None = None,
    ):
        self.store = store
        self.run_turn = run_turn
        self.on_start = on_start
        self.on_finish = on_finish
        self.on_before_complete = on_before_complete
        self._stop = threading.Event()
        self._cancel_lock = threading.Lock()
        self._cancel_events: dict[tuple[str, str], threading.Event] = {}
        self._queue: queue.Queue[tuple[str, DevicePrincipal] | None] = queue.Queue(maxsize=100)
        self._thread = threading.Thread(target=self._loop, name="mo-everywhere-turns", daemon=True)
        self._thread.start()

    def submit(
        self,
        principal: DevicePrincipal,
        text: str,
        *,
        client_request_id: str | None = None,
        request_context: str = "",
        before_enqueue: Callable[[TurnJob], None] | None = None,
    ) -> TurnJob:
        if self._stop.is_set():
            raise TurnJobError("turn job runner is stopped")
        submission = self.store.create_submission(
            principal,
            text,
            client_request_id=client_request_id,
            request_context=request_context,
        )
        job = submission.job
        if before_enqueue is not None:
            try:
                before_enqueue(job)
            except Exception:
                if submission.created:
                    self.store.fail(job.job_id, principal.device_id, "Attachment binding failed")
                raise
        if not submission.created:
            return job
        try:
            self._queue.put_nowait((job.job_id, principal))
        except queue.Full:
            return self.store.fail(job.job_id, principal.device_id, "MO turn queue is full")
        return job

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        with self._cancel_lock:
            cancel_events = tuple(self._cancel_events.values())
        for cancel_event in cancel_events:
            cancel_event.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread.is_alive():
            self._thread.join(timeout=max(0.0, timeout))

    def cancel(self, job_id: str, device_id: str) -> TurnJob:
        """Cancel one owned job and wake its exact in-flight provider call."""
        job = self.store.cancel(job_id, device_id)
        if job.cancel_requested:
            with self._cancel_lock:
                cancel_event = self._cancel_events.get((job.job_id, job.device_id))
            if cancel_event is not None:
                cancel_event.set()
        return job

    def _loop(self) -> None:
        while True:
            if self._stop.is_set():
                return
            try:
                item = self._queue.get(timeout=60)
            except queue.Empty:
                self.store.prune_live_mail()
                continue
            if item is None:
                return
            job_id, principal = item
            cancel_key = (job_id, principal.device_id)
            cancel_event = threading.Event()
            with self._cancel_lock:
                self._cancel_events[cancel_key] = cancel_event
            try:
                job = self.store.start(job_id, principal.device_id)
                if job is None:
                    continue
                if self.on_start:
                    try:
                        self.on_start(job)
                    except Exception:
                        pass
                try:
                    reply = self.run_turn(principal, job.text, job.job_id, cancel_event)
                    if self._stop.is_set():
                        finished = self.store.interrupt(job_id, principal.device_id)
                    else:
                        callback = None
                        if self.on_before_complete is not None:
                            callback = lambda cancelled: self.on_before_complete(job_id, cancelled)
                        finished = self.store.complete(
                            job_id,
                            principal.device_id,
                            reply,
                            before_commit=callback,
                        )
                except TurnJobConflict as exc:
                    finished = (
                        self.store.interrupt(job_id, principal.device_id)
                        if self._stop.is_set()
                        else self.store.fail(job_id, principal.device_id, str(exc))
                    )
                except Exception:
                    finished = (
                        self.store.interrupt(job_id, principal.device_id)
                        if self._stop.is_set()
                        else self.store.fail(job_id, principal.device_id)
                    )
                if self.on_finish:
                    try:
                        self.on_finish(finished)
                    except Exception:
                        pass
            finally:
                with self._cancel_lock:
                    self._cancel_events.pop(cancel_key, None)


def _from_row(row: sqlite3.Row) -> TurnJob:
    status = str(row["status"])
    if status not in JOB_STATES:
        status = "failed"
    return TurnJob(
        job_id=str(row["job_id"]),
        device_id=str(row["device_id"]),
        text=str(row["text"]),
        status=status,
        reply=str(row["reply"]),
        error=str(row["error"]),
        created_at=float(row["created_at"]),
        started_at=float(row["started_at"]),
        completed_at=float(row["completed_at"]),
        expires_at=float(row["expires_at"]),
        cancel_requested=bool(row["cancel_requested"]),
    )


def _limits(config: dict[str, Any]) -> tuple[float, int]:
    block = config.get("consistent_everywhere") if isinstance(config.get("consistent_everywhere"), dict) else {}
    api = block.get("api") if isinstance(block.get("api"), dict) else {}
    return (
        max(300.0, min(7 * 24 * 60 * 60, float(api.get("job_ttl_seconds", DEFAULT_JOB_TTL) or DEFAULT_JOB_TTL))),
        max(5, min(200, int(api.get("max_jobs_per_device", DEFAULT_MAX_JOBS_PER_DEVICE) or DEFAULT_MAX_JOBS_PER_DEVICE))),
    )


def _id(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip() if ch.isalnum() or ch in "-_.")[:64]


def _client_request_key(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TurnJobRequestError("client_request_id must be a string")
    clean = value.strip()
    if not (1 <= len(clean) <= MAX_CLIENT_REQUEST_ID_CHARS):
        raise TurnJobRequestError(
            f"client_request_id must contain 1-{MAX_CLIENT_REQUEST_ID_CHARS} characters"
        )
    if any(not (ch.isascii() and (ch.isalnum() or ch in "-_.")) for ch in clean):
        raise TurnJobRequestError("client_request_id contains unsupported characters")
    return _fingerprint(clean)


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _request_fingerprint(text: str, context: str) -> str:
    clean_context = str(context or "")
    if not clean_context:
        return _fingerprint(text)
    text_bytes = text.encode("utf-8")
    context_bytes = clean_context.encode("utf-8")
    digest = hashlib.sha256()
    digest.update(len(text_bytes).to_bytes(8, "big"))
    digest.update(text_bytes)
    digest.update(len(context_bytes).to_bytes(8, "big"))
    digest.update(context_bytes)
    return digest.hexdigest()
