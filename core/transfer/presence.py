"""Exactly-once transfer notices routed by real human surface activity."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from core.runtime.heartbeat import (
    HEARTBEAT_LEDGER_MAX_LINES,
    read_recent_heartbeats,
)
from core.runtime.surface_identity import normalize_runtime_surface
from core.runtime.instance import get_instance_id
from core.runtime.lock import _pid_alive
from core.state.attachments import safe_attachment_name
from core.state.paths import FILE_TRANSFER_DB_PATH, HEARTBEAT_LEDGER_PATH, resolve_state_path
from core.state.sqlite import connect_state_db


HUMAN_SURFACES = frozenset({"terminal", "telegram", "mo_desktop", "companion"})
CONVERSATIONAL_EVENTS = frozenset({"turn_start", "turn_end"})
CLAIMED_NOTICE_RETENTION_SECONDS = 7 * 24 * 60 * 60
MAX_CLAIMED_NOTICE_ROWS = 10_000


def _latest_human_target(
    config: dict[str, Any] | None = None,
) -> tuple[str, str, int, str]:
    """Return the latest conversational surface whose owning process is alive."""
    heartbeat_path = resolve_state_path(HEARTBEAT_LEDGER_PATH, config or {})
    liveness: dict[int, bool] = {}
    # Worker and periodic pulses can legitimately outnumber conversational
    # events. Search the entire bounded heartbeat ledger so they can never
    # crowd a still-live human surface out of notification routing.
    for item in reversed(
        read_recent_heartbeats(limit=HEARTBEAT_LEDGER_MAX_LINES, path=heartbeat_path)
    ):
        surface = normalize_runtime_surface(str(item.get("surface") or ""))
        event = str(item.get("event") or "").strip().lower()
        if surface not in HUMAN_SURFACES:
            continue
        if event not in CONVERSATIONAL_EVENTS and not event.startswith("auto_reply:"):
            continue
        try:
            pid = int(item.get("pid") or 0)
        except (TypeError, ValueError):
            pid = 0
        if pid <= 0:
            continue
        if pid not in liveness:
            liveness[pid] = _pid_alive(pid)
        if not liveness[pid]:
            continue
        extra = item.get("extra") if isinstance(item.get("extra"), dict) else {}
        target_key = (
            str(extra.get("chat_id") or "")[:120]
            if surface == "telegram"
            else ""
        )
        return surface, str(item.get("instance_id") or "")[:120], pid, target_key
    return "", "", 0, ""


def latest_human_surface(
    config: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Return the latest genuine running human surface, never a worker pulse."""
    surface, instance_id, _pid, _target_key = _latest_human_target(config)
    return surface, instance_id


def queue_transfer_notice(
    config: dict[str, Any] | None,
    *,
    transfer_id: str,
    name: str,
    size_bytes: int,
) -> None:
    surface, instance_id, target_pid, target_key = _latest_human_target(config)
    now = time.time()
    with _connect(config) as db:
        _initialize(db)
        _prune_claimed(db, now=now)
        db.execute(
            """
            INSERT OR IGNORE INTO file_transfer_notice(
              transfer_id,name,size_bytes,target_surface,target_instance,target_pid,
              target_key,created_at,claimed_at,claimed_surface,claimed_instance
            ) VALUES(?,?,?,?,?,?,?,?,0,'','')
            """,
            (
                str(transfer_id)[:80],
                safe_attachment_name(name),
                max(0, int(size_bytes)),
                surface,
                instance_id,
                target_pid,
                target_key,
                now,
            ),
        )
        transfer_columns = {
            str(row["name"])
            for row in db.execute("PRAGMA table_info(file_transfer)").fetchall()
        }
        if "notice_queued" in transfer_columns:
            db.execute(
                "UPDATE file_transfer SET notice_queued=1 WHERE transfer_id=?",
                (str(transfer_id)[:80],),
            )


def claim_transfer_notices(
    config: dict[str, Any] | None,
    *,
    surface: str,
    instance_id: str = "",
    limit: int = 3,
    include_unassigned: bool = True,
    require_target_key: bool = False,
) -> list[dict[str, Any]]:
    """Atomically consume notices assigned here, or unassigned held notices."""
    clean_surface = normalize_runtime_surface(surface)
    if clean_surface not in HUMAN_SURFACES:
        return []
    clean_instance = str(instance_id or get_instance_id())[:120]
    held_target = (
        _latest_human_target(config)
        if include_unassigned
        else ("", "", 0, "")
    )
    with _connect(config) as db:
        _initialize(db)
        db.execute("BEGIN IMMEDIATE")
        _prune_claimed(db, now=time.time())
        assigned = db.execute(
            """
            SELECT transfer_id,target_pid
            FROM file_transfer_notice
            WHERE claimed_at=0 AND target_surface<>'' AND target_pid>0
            """
        ).fetchall()
        dead_ids = [
            str(row["transfer_id"])
            for row in assigned
            if not _pid_alive(int(row["target_pid"]))
        ]
        for transfer_id in dead_ids:
            db.execute(
                """
                UPDATE file_transfer_notice
                SET target_surface='',target_instance='',target_pid=0,target_key=''
                WHERE transfer_id=? AND claimed_at=0
                """,
                (transfer_id,),
            )
        if held_target[0] and held_target[2] > 0:
            db.execute(
                """
                UPDATE file_transfer_notice
                SET target_surface=?,target_instance=?,target_pid=?,target_key=?
                WHERE claimed_at=0 AND target_surface=''
                """,
                held_target,
            )
        # If no human surface is live, held notices stay unassigned. A later
        # caller first re-runs real heartbeat selection rather than winning by
        # poll timing.
        unassigned_clause = "0"
        key_clause = " AND target_key<>''" if require_target_key else ""
        rows = db.execute(
            f"""
            SELECT transfer_id,name,size_bytes,target_key,created_at
            FROM file_transfer_notice
            WHERE claimed_at=0 AND (
              {unassigned_clause}
              OR (
                target_surface=?
                AND (target_instance='' OR target_instance=?)
              )
            ){key_clause}
            ORDER BY created_at,transfer_id
            LIMIT ?
            """,
            (clean_surface, clean_instance, max(1, min(20, int(limit)))),
        ).fetchall()
        now = time.time()
        for row in rows:
            db.execute(
                """
                UPDATE file_transfer_notice
                SET claimed_at=?,claimed_surface=?,claimed_instance=?
                WHERE transfer_id=? AND claimed_at=0
                """,
                (now, clean_surface, clean_instance, str(row["transfer_id"])),
            )
    return [dict(row) for row in rows]


def _connect(config: dict[str, Any] | None) -> sqlite3.Connection:
    path = Path(resolve_state_path(FILE_TRANSFER_DB_PATH, config or {}))
    path.parent.mkdir(parents=True, exist_ok=True)
    return connect_state_db(path)


def _initialize(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS file_transfer_notice(
            transfer_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            target_surface TEXT NOT NULL,
            target_instance TEXT NOT NULL,
            target_pid INTEGER NOT NULL DEFAULT 0,
            target_key TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            claimed_at REAL NOT NULL,
            claimed_surface TEXT NOT NULL,
            claimed_instance TEXT NOT NULL
        )
        """
    )
    columns = {
        str(row["name"])
        for row in db.execute("PRAGMA table_info(file_transfer_notice)").fetchall()
    }
    if "target_pid" not in columns:
        db.execute(
            "ALTER TABLE file_transfer_notice "
            "ADD COLUMN target_pid INTEGER NOT NULL DEFAULT 0"
        )
    if "target_key" not in columns:
        db.execute(
            "ALTER TABLE file_transfer_notice "
            "ADD COLUMN target_key TEXT NOT NULL DEFAULT ''"
        )


def _prune_claimed(db: sqlite3.Connection, *, now: float) -> None:
    db.execute(
        "DELETE FROM file_transfer_notice WHERE claimed_at>0 AND claimed_at<?",
        (now - CLAIMED_NOTICE_RETENTION_SECONDS,),
    )
    db.execute(
        """
        DELETE FROM file_transfer_notice
        WHERE claimed_at>0
          AND transfer_id NOT IN (
            SELECT transfer_id FROM file_transfer_notice
            WHERE claimed_at>0
            ORDER BY claimed_at DESC,transfer_id DESC
            LIMIT ?
          )
        """,
        (MAX_CLAIMED_NOTICE_ROWS,),
    )
