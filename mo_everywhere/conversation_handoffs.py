"""Bounded cross-surface requests for opening one portable conversation.

The canonical transcript remains owned by ``SessionManager``.  This ledger
stores only an opaque conversation ID, its expected revision, exact principals,
and a short lifecycle record.
"""
from __future__ import annotations

import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.state.sqlite import connect_state_db


HANDOFF_TTL_SECONDS = 10 * 60
HANDOFF_LIMIT = 50
HANDOFF_STATES = frozenset(
    {"requested", "received", "ready", "running", "refused", "expired", "failed"}
)
TERMINAL_HANDOFF_STATES = frozenset({"running", "refused", "expired", "failed"})
# States a device/adapter may report in an acknowledgement: everything except
# the hub-owned "requested" start state and the hub-expired terminal.
ACK_HANDOFF_STATES = frozenset({"received", "ready", "running", "refused", "failed"})
_ACK_TRANSITIONS = {
    "requested": frozenset({"received", "ready", "refused", "failed"}),
    "received": frozenset({"ready", "refused", "failed"}),
    "ready": frozenset({"running", "failed"}),
}
_DEVICE_ID_RE = re.compile(r"[0-9a-f]{32}")
_CONVERSATION_ID_RE = re.compile(r"conv_[0-9a-f]{32}")
_REQUEST_KEY_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_HANDOFF_ID_RE = re.compile(r"[0-9a-f]{32}")
_TARGET_KEY_RE = re.compile(
    r"(?:device:[0-9a-f]{32}|terminal:hub|terminal:desktop:[0-9a-f]{32}|telegram:[0-9a-f]{32})"
)
_TARGET_KINDS = frozenset({"android", "terminal", "telegram"})


class ConversationHandoffError(RuntimeError):
    pass


class ConversationHandoffConflict(ConversationHandoffError):
    pass


class ConversationHandoffNotFound(ConversationHandoffError):
    pass


@dataclass(frozen=True)
class ConversationHandoff:
    handoff_id: str
    request_key: str
    conversation_id: str
    expected_revision: int
    source_device_id: str
    source_label: str
    target_device_id: str
    target_key: str
    target_kind: str
    target_label: str
    mode: str
    state: str
    reason: str
    created_at: float
    updated_at: float
    expires_at: float

    def public(self, *, viewer_device_id: str) -> dict[str, Any]:
        viewer = _device_id(viewer_device_id)
        if viewer != self.source_device_id and viewer != self.target_device_id:
            raise ConversationHandoffNotFound("conversation handoff was not found")
        return {
            "handoff_id": self.handoff_id,
            "request_id": self.request_key,
            "conversation_id": self.conversation_id,
            "expected_revision": self.expected_revision,
            "direction": "outgoing" if viewer == self.source_device_id else "incoming",
            "peer_label": self.target_label if viewer == self.source_device_id else self.source_label,
            "mode": self.mode,
            "state": self.state,
            "reason": self.reason,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": 0.0 if self.state in TERMINAL_HANDOFF_STATES else self.expires_at,
        }


class ConversationHandoffStore:
    """SQLite lifecycle ledger sharing the Everywhere hub database."""

    def __init__(self, *, path: str | Path):
        self.path = Path(path).expanduser().resolve(strict=False)
        self._initialize()

    def create(
        self,
        *,
        source_device_id: str,
        source_label: str,
        target_key: str,
        target_kind: str,
        target_label: str,
        target_device_id: str,
        conversation_id: str,
        expected_revision: int,
        mode: str,
        request_key: str,
    ) -> ConversationHandoff:
        source = _device_id(source_device_id)
        target = _optional_device_id(target_device_id)
        target_ref = _target_key(target_key)
        kind = _target_kind(target_kind)
        if kind == "android" and target_ref != f"device:{target}":
            raise ConversationHandoffError("device target identity is inconsistent")
        if kind == "terminal" and (
            target
            or not (
                target_ref == "terminal:hub"
                or target_ref.startswith("terminal:desktop:")
            )
        ):
            raise ConversationHandoffError("terminal target identity is inconsistent")
        if kind == "telegram" and (target or not target_ref.startswith("telegram:")):
            raise ConversationHandoffError("Telegram target identity is inconsistent")
        if target and source == target:
            raise ConversationHandoffError("source and target devices must differ")
        conversation = _conversation_id(conversation_id)
        revision = _revision(expected_revision)
        clean_mode = str(mode or "").strip().lower()
        if clean_mode not in {"confirm", "auto"}:
            raise ConversationHandoffError("handoff mode must be confirm or auto")
        key = str(request_key or "").strip()
        if not _REQUEST_KEY_RE.fullmatch(key):
            raise ConversationHandoffError("request_id is invalid")
        source_name = _label(source_label)
        target_name = _label(target_label)
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db, now)
            existing = db.execute(
                "SELECT * FROM everywhere_conversation_handoff "
                "WHERE source_device_id=? AND request_key=?",
                (source, key),
            ).fetchone()
            if existing:
                record = _from_row(existing)
                if (
                    record.target_device_id != target
                    or record.target_key != target_ref
                    or record.target_kind != kind
                    or record.conversation_id != conversation
                    or record.expected_revision != revision
                    or record.mode != clean_mode
                ):
                    raise ConversationHandoffConflict(
                        "request_id was already used for a different conversation handoff"
                    )
                return record
            handoff_id = secrets.token_hex(16)
            db.execute(
                """
                INSERT INTO everywhere_conversation_handoff(
                  handoff_id,request_key,conversation_id,expected_revision,
                  source_device_id,source_label,target_device_id,target_key,target_kind,target_label,
                  mode,state,reason,created_at,updated_at,expires_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,'requested','',?,?,?)
                """,
                (
                    handoff_id,
                    key,
                    conversation,
                    revision,
                    source,
                    source_name,
                    target,
                    target_ref,
                    kind,
                    target_name,
                    clean_mode,
                    now,
                    now,
                    now + HANDOFF_TTL_SECONDS,
                ),
            )
            self._prune(db)
        return self.get(handoff_id, source)

    def list_for(self, device_id: str, *, limit: int = HANDOFF_LIMIT) -> list[ConversationHandoff]:
        device = _device_id(device_id)
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db, now)
            rows = db.execute(
                """
                SELECT * FROM everywhere_conversation_handoff
                WHERE source_device_id=? OR target_device_id=?
                ORDER BY created_at DESC LIMIT ?
                """,
                (device, device, max(1, min(HANDOFF_LIMIT, int(limit)))),
            ).fetchall()
        return [_from_row(row) for row in rows]

    def get(self, handoff_id: str, device_id: str) -> ConversationHandoff:
        clean_handoff = _handoff_id(handoff_id)
        device = _device_id(device_id)
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db, now)
            row = db.execute(
                """
                SELECT * FROM everywhere_conversation_handoff
                WHERE handoff_id=? AND (source_device_id=? OR target_device_id=?)
                """,
                (clean_handoff, device, device),
            ).fetchone()
        if not row:
            raise ConversationHandoffNotFound("conversation handoff was not found")
        return _from_row(row)

    def acknowledge(
        self,
        handoff_id: str,
        *,
        target_device_id: str,
        state: str,
        reason: str = "",
    ) -> ConversationHandoff:
        clean_handoff = _handoff_id(handoff_id)
        target = _device_id(target_device_id)
        clean_state = str(state or "").strip().lower()
        if clean_state not in ACK_HANDOFF_STATES:
            raise ConversationHandoffError("handoff acknowledgement state is invalid")
        clean_reason = _reason(reason)
        current, changed = self._advance_acknowledgement(
            clean_handoff,
            target=target,
            system_target=False,
            state=clean_state,
            reason=clean_reason,
        )
        if not changed:
            return current
        return self.get(clean_handoff, target)

    def acknowledge_system(
        self,
        handoff_id: str,
        *,
        target_key: str,
        state: str,
        reason: str = "",
    ) -> ConversationHandoff:
        """Advance one fixed non-device adapter without impersonating a device."""
        clean_handoff = _handoff_id(handoff_id)
        target_ref = _target_key(target_key)
        clean_state = str(state or "").strip().lower()
        if clean_state not in ACK_HANDOFF_STATES:
            raise ConversationHandoffError("handoff acknowledgement state is invalid")
        clean_reason = _reason(reason)
        current, changed = self._advance_acknowledgement(
            clean_handoff,
            target=target_ref,
            system_target=True,
            state=clean_state,
            reason=clean_reason,
        )
        if not changed:
            return current
        return self.get(clean_handoff, current.source_device_id)

    def _advance_acknowledgement(
        self,
        handoff_id: str,
        *,
        target: str,
        system_target: bool,
        state: str,
        reason: str,
    ) -> tuple[ConversationHandoff, bool]:
        """Apply the shared acknowledgement transition under one write lock."""
        now = time.time()
        target_column = "target_key" if system_target else "target_device_id"
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db, now)
            row = db.execute(
                f"SELECT * FROM everywhere_conversation_handoff WHERE handoff_id=? AND {target_column}=?",
                (handoff_id, target),
            ).fetchone()
            if not row:
                raise ConversationHandoffNotFound("conversation handoff was not found")
            current = _from_row(row)
            if system_target and current.target_device_id:
                raise ConversationHandoffNotFound("conversation handoff was not found")
            if current.state == state:
                return current, False
            if state not in _ACK_TRANSITIONS.get(current.state, frozenset()):
                raise ConversationHandoffConflict("conversation handoff state cannot transition")
            db.execute(
                "UPDATE everywhere_conversation_handoff SET state=?,reason=?,updated_at=? WHERE handoff_id=?",
                (state, reason, now, handoff_id),
            )
        return current, True

    def find_system_for_target(
        self,
        target_key: str,
        handoff_reference: str = "",
        *,
        states: frozenset[str] | set[str] | tuple[str, ...] = (
            "requested",
            "received",
            "ready",
        ),
    ) -> ConversationHandoff | None:
        """Resolve one active non-device request for its exact opaque target."""
        target_ref = _target_key(target_key)
        reference = str(handoff_reference or "").strip().lower()
        if reference and (
            not 8 <= len(reference) <= 32
            or any(char not in "0123456789abcdef" for char in reference)
        ):
            raise ConversationHandoffError("conversation handoff reference is invalid")
        allowed_states = tuple(
            state for state in (str(item).strip().lower() for item in states)
            if state in HANDOFF_STATES
        )
        if not allowed_states:
            return None
        placeholders = ",".join("?" for _ in allowed_states)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db, time.time())
            rows = db.execute(
                "SELECT * FROM everywhere_conversation_handoff "
                f"WHERE target_key=? AND target_device_id='' AND state IN ({placeholders}) "
                "ORDER BY created_at DESC LIMIT 8",
                (target_ref, *allowed_states),
            ).fetchall()
        matches = [
            _from_row(row) for row in rows
            if not reference or str(row["handoff_id"]).startswith(reference)
        ]
        if len(matches) > 1 and reference:
            raise ConversationHandoffConflict("conversation handoff reference is ambiguous")
        return matches[0] if matches else None

    def cancel(self, handoff_id: str, *, source_device_id: str) -> ConversationHandoff:
        clean_handoff = _handoff_id(handoff_id)
        source = _device_id(source_device_id)
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire(db, now)
            row = db.execute(
                "SELECT * FROM everywhere_conversation_handoff WHERE handoff_id=? AND source_device_id=?",
                (clean_handoff, source),
            ).fetchone()
            if not row:
                raise ConversationHandoffNotFound("conversation handoff was not found")
            current = _from_row(row)
            if current.state not in {"requested", "received"}:
                raise ConversationHandoffConflict("conversation handoff can no longer be cancelled")
            db.execute(
                "UPDATE everywhere_conversation_handoff SET state='refused',reason=?,updated_at=? WHERE handoff_id=?",
                ("Cancelled by the requesting device.", now, clean_handoff),
            )
        return self.get(clean_handoff, source)

    def fail_unavailable(self, handoff_id: str, *, reason: str) -> None:
        clean_handoff = _handoff_id(handoff_id)
        now = time.time()
        with self._connect() as db:
            db.execute(
                """
                UPDATE everywhere_conversation_handoff
                SET state='failed',reason=?,updated_at=?
                WHERE handoff_id=? AND state IN ('requested','received','ready')
                """,
                (_reason(reason), now, clean_handoff),
            )

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS everywhere_conversation_handoff(
                    handoff_id TEXT PRIMARY KEY,
                    request_key TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    expected_revision INTEGER NOT NULL,
                    source_device_id TEXT NOT NULL,
                    source_label TEXT NOT NULL,
                    target_device_id TEXT NOT NULL,
                    target_key TEXT NOT NULL,
                    target_kind TEXT NOT NULL,
                    target_label TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    state TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    UNIQUE(source_device_id,request_key)
                );
                CREATE INDEX IF NOT EXISTS everywhere_conversation_handoff_party_idx
                    ON everywhere_conversation_handoff(source_device_id,target_device_id,created_at);
                """
            )
            columns = {
                str(row[1])
                for row in db.execute("PRAGMA table_info(everywhere_conversation_handoff)")
            }
            if "target_key" not in columns:
                db.execute(
                    "ALTER TABLE everywhere_conversation_handoff "
                    "ADD COLUMN target_key TEXT NOT NULL DEFAULT ''"
                )
            if "target_kind" not in columns:
                db.execute(
                    "ALTER TABLE everywhere_conversation_handoff "
                    "ADD COLUMN target_kind TEXT NOT NULL DEFAULT 'android'"
                )
            db.execute(
                "UPDATE everywhere_conversation_handoff "
                "SET target_key='device:' || target_device_id WHERE target_key=''"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS everywhere_conversation_handoff_target_idx "
                "ON everywhere_conversation_handoff(target_key,created_at)"
            )

    @staticmethod
    def _expire(db: sqlite3.Connection, now: float) -> None:
        db.execute(
            """
            UPDATE everywhere_conversation_handoff
            SET state='expired',reason='The request expired before the target was ready.',updated_at=?
            WHERE state IN ('requested','received','ready') AND expires_at<=?
            """,
            (now, now),
        )

    @staticmethod
    def _prune(db: sqlite3.Connection) -> None:
        db.execute(
            """
            DELETE FROM everywhere_conversation_handoff WHERE handoff_id IN (
              SELECT handoff_id FROM everywhere_conversation_handoff
              ORDER BY created_at DESC LIMIT -1 OFFSET 500
            )
            """
        )

    def _connect(self) -> sqlite3.Connection:
        return connect_state_db(self.path)


def _from_row(row: sqlite3.Row) -> ConversationHandoff:
    return ConversationHandoff(
        handoff_id=str(row["handoff_id"]),
        request_key=str(row["request_key"]),
        conversation_id=str(row["conversation_id"]),
        expected_revision=int(row["expected_revision"]),
        source_device_id=str(row["source_device_id"]),
        source_label=str(row["source_label"]),
        target_device_id=str(row["target_device_id"]),
        target_key=str(row["target_key"]),
        target_kind=str(row["target_kind"]),
        target_label=str(row["target_label"]),
        mode=str(row["mode"]),
        state=str(row["state"]),
        reason=str(row["reason"]),
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
        expires_at=float(row["expires_at"]),
    )


def _device_id(value: Any) -> str:
    clean = str(value or "").strip().lower()
    if not _DEVICE_ID_RE.fullmatch(clean):
        raise ConversationHandoffError("device is invalid")
    return clean


def _optional_device_id(value: Any) -> str:
    clean = str(value or "").strip().lower()
    return _device_id(clean) if clean else ""


def _target_key(value: Any) -> str:
    clean = str(value or "").strip().lower()
    if not _TARGET_KEY_RE.fullmatch(clean):
        raise ConversationHandoffError("handoff target is invalid")
    return clean


def _target_kind(value: Any) -> str:
    clean = str(value or "").strip().lower()
    if clean not in _TARGET_KINDS:
        raise ConversationHandoffError("handoff target kind is invalid")
    return clean


def _handoff_id(value: Any) -> str:
    clean = str(value or "").strip().lower()
    if not _HANDOFF_ID_RE.fullmatch(clean):
        raise ConversationHandoffNotFound("conversation handoff was not found")
    return clean


def _conversation_id(value: Any) -> str:
    clean = str(value or "").strip().lower()
    if not _CONVERSATION_ID_RE.fullmatch(clean):
        raise ConversationHandoffError("conversation is invalid")
    return clean


def _revision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ConversationHandoffError("expected_revision must be a positive integer")
    return value


def _label(value: Any) -> str:
    clean = " ".join(str(value or "").split()).strip()
    if not clean or len(clean) > 80 or any(ord(char) < 32 for char in clean):
        raise ConversationHandoffError("device label is invalid")
    return clean


def _reason(value: Any) -> str:
    clean = " ".join(str(value or "").split()).strip()
    if len(clean) > 200 or any(ord(char) < 32 for char in clean):
        raise ConversationHandoffError("handoff reason is invalid")
    return clean
