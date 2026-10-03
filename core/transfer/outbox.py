"""Private sender outbox for retrying resumable file transfers."""
from __future__ import annotations

import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.state.attachments import safe_attachment_name
from core.state.sqlite import connect_state_db

from .model import (
    TransferError,
    TransferSettings,
    safe_transfer_error,
    strict_transfer_id_part,
)
from .storage import resolve_transfer_storage

OUTBOX_HEARTBEAT_SECONDS = 30.0
OUTBOX_SENDING_STALE_SECONDS = 300.0


@dataclass(frozen=True)
class OutboxRecord:
    outbox_id: str
    source_path: Path
    target_device_id: str
    target_label: str
    source_surface: str
    destination: str
    path_hint: str
    client_request_id: str
    hub_local: bool
    state: str
    attempts: int
    next_attempt_at: float
    transfer_id: str
    error: str
    created_at: float
    updated_at: float

    def public(self) -> dict[str, Any]:
        """Return sender-safe status without exposing the private source path."""
        return {
            "outbox_id": self.outbox_id,
            "transfer_id": self.transfer_id,
            "target_device_id": self.target_device_id,
            "target_label": self.target_label,
            "source_surface": self.source_surface,
            "destination": self.destination,
            "name": self.source_path.name,
            "state": self.state,
            "progress": 1.0 if self.state == "done" else 0.0,
            "failure": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "direction": "out",
        }


class TransferOutbox:
    """Keep sender custody and retry state outside any UI process."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        path: str | Path | None = None,
        spool_root: str | Path | None = None,
    ):
        self.config = config or {}
        self.settings = TransferSettings.from_config(self.config)
        self.path, transfer_spool = resolve_transfer_storage(
            self.config,
            path=path,
            spool_root=spool_root,
        )
        self.spool_root = transfer_spool / "outbox"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.spool_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def enqueue(
        self,
        source_path: str | Path,
        *,
        target_device_id: str,
        target_label: str,
        source_surface: str,
        hub_local: bool,
        destination: str = "catalog",
        path_hint: str = "",
    ) -> OutboxRecord:
        source = Path(source_path).expanduser().resolve(strict=True)
        if not source.is_file():
            raise TransferError("transfer source is not a file")
        target = strict_transfer_id_part(
            target_device_id, label="transfer target"
        )
        surface = strict_transfer_id_part(
            source_surface,
            limit=40,
            label="transfer source surface",
        )
        clean_destination = str(destination or "catalog").strip().lower()
        if clean_destination not in {"catalog", "named_path"}:
            raise TransferError("transfer destination is invalid")
        clean_hint = str(path_hint or "").strip()
        if (
            len(clean_hint) > 512
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in clean_hint
            )
        ):
            raise TransferError("transfer destination path is invalid")
        if clean_destination == "named_path" and not clean_hint:
            raise TransferError("named destination requires an explicit path")
        if clean_destination == "catalog":
            clean_hint = ""
        now = time.time()
        outbox_id = uuid.uuid4().hex
        request_id = uuid.uuid4().hex
        staged = self.spool_root / outbox_id / safe_attachment_name(source.name)
        before = source.stat()
        expected = int(before.st_size)
        if expected < 1 or expected > self.settings.max_bytes:
            raise TransferError("transfer source exceeds the configured file limit")
        with self._connect() as db:
            db.execute(
                """
                INSERT INTO file_transfer_outbox(
                  outbox_id,source_path,target_device_id,target_label,
                  source_surface,destination,path_hint,client_request_id,
                  hub_local,state,attempts,next_attempt_at,transfer_id,error,
                  created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,'staging',0,?,'','',?,?)
                """,
                (
                    outbox_id,
                    str(staged),
                    target,
                    str(target_label or "device")[:120],
                    surface,
                    clean_destination,
                    clean_hint,
                    request_id,
                    1 if hub_local else 0,
                    now,
                    now,
                    now,
                ),
            )
        try:
            staged.parent.mkdir(parents=True, exist_ok=False)
            copied = 0
            heartbeat = time.monotonic()
            with source.open("rb") as input_file, staged.open("xb") as output_file:
                while True:
                    block = input_file.read(1024 * 1024)
                    if not block:
                        break
                    copied += len(block)
                    if copied > self.settings.max_bytes:
                        raise TransferError(
                            "transfer source exceeds the configured file limit"
                        )
                    output_file.write(block)
                    if time.monotonic() - heartbeat >= 10.0:
                        with self._connect() as db:
                            db.execute(
                                "UPDATE file_transfer_outbox SET updated_at=? "
                                "WHERE outbox_id=? AND state='staging'",
                                (time.time(), outbox_id),
                            )
                        heartbeat = time.monotonic()
                output_file.flush()
                os.fsync(output_file.fileno())
            after = source.stat()
            if (
                copied != expected
                or int(after.st_size) != expected
                or int(after.st_mtime_ns) != int(before.st_mtime_ns)
            ):
                raise TransferError("transfer source changed while taking custody")
            with self._connect() as db:
                updated = db.execute(
                    "UPDATE file_transfer_outbox SET state='queued',"
                    "next_attempt_at=?,updated_at=? "
                    "WHERE outbox_id=? AND state='staging'",
                    (time.time(), time.time(), outbox_id),
                )
                if updated.rowcount != 1:
                    raise TransferError("sender custody staging was interrupted")
        except Exception:
            with self._connect() as db:
                db.execute(
                    "DELETE FROM file_transfer_outbox "
                    "WHERE outbox_id=? AND state='staging'",
                    (outbox_id,),
                )
            staged.unlink(missing_ok=True)
            try:
                staged.parent.rmdir()
            except OSError:
                pass
            raise
        return self.get(outbox_id)

    def send_now(
        self,
        source_path: str | Path,
        *,
        target_device_id: str,
        target_label: str,
        source_surface: str,
        hub_local: bool,
        destination: str = "catalog",
        path_hint: str = "",
        on_progress: Any = None,
    ) -> OutboxRecord:
        record = self.enqueue(
            source_path,
            target_device_id=target_device_id,
            target_label=target_label,
            source_surface=source_surface,
            hub_local=hub_local,
            destination=destination,
            path_hint=path_hint,
        )
        return self.process(record.outbox_id, on_progress=on_progress)

    def process(
        self, outbox_id: str, *, on_progress: Any = None
    ) -> OutboxRecord:
        clean_id = strict_transfer_id_part(
            outbox_id, label="transfer outbox ID"
        )
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM file_transfer_outbox WHERE outbox_id=?",
                (clean_id,),
            ).fetchone()
            if not row:
                raise TransferError("transfer outbox item was not found")
            if str(row["state"]) == "done":
                record = self._record(row)
                self._remove_staged_source(record)
                return record
            if str(row["state"]) == "cancelled":
                raise TransferError("transfer outbox item was cancelled")
            if str(row["state"]) == "staging":
                raise TransferError("sender custody is still being staged")
            if (
                str(row["state"]) == "sending"
                and float(row["updated_at"])
                > now - OUTBOX_SENDING_STALE_SECONDS
            ):
                return self._record(row)
            db.execute(
                "UPDATE file_transfer_outbox SET state='sending',attempts=attempts+1,"
                "updated_at=?,error='' WHERE outbox_id=?",
                (now, clean_id),
            )
        record = self.get(clean_id)
        last_heartbeat = now
        heartbeat_stop = threading.Event()

        def keep_lease_alive() -> None:
            while not heartbeat_stop.wait(OUTBOX_HEARTBEAT_SECONDS):
                try:
                    with self._connect() as db:
                        db.execute(
                            "UPDATE file_transfer_outbox SET updated_at=? "
                            "WHERE outbox_id=? AND state='sending'",
                            (time.time(), clean_id),
                        )
                except sqlite3.Error:
                    continue

        heartbeat_thread = threading.Thread(
            target=keep_lease_alive,
            name=f"mo-transfer-outbox-{clean_id[:8]}",
            daemon=True,
        )
        heartbeat_thread.start()

        def stop_heartbeat() -> None:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=1.0)

        def report_progress(current: int, total: int) -> None:
            nonlocal last_heartbeat
            stamp = time.time()
            if stamp - last_heartbeat >= 10.0:
                with self._connect() as db:
                    db.execute(
                        "UPDATE file_transfer_outbox SET updated_at=? "
                        "WHERE outbox_id=? AND state='sending'",
                        (stamp, clean_id),
                    )
                last_heartbeat = stamp
            if callable(on_progress):
                on_progress(current, total)

        try:
            if record.hub_local:
                from .service import HUB_TARGET_ID, TransferService

                if record.target_device_id != HUB_TARGET_ID:
                    from mo_everywhere.registry import DeviceRegistry

                    eligible = {
                        str(item["device_id"])
                        for item in DeviceRegistry(self.config).list_devices()
                        if (
                            item.get("revoked_at") is None
                            and item.get("capability") == "control"
                            and "file_transfer" in set(item.get("scopes") or ())
                        )
                    }
                    if record.target_device_id not in eligible:
                        raise TransferError(
                            "transfer target is no longer available"
                        )
                result: Any = TransferService(self.config).send_local_file(
                    record.source_path,
                    sender_device_id="hub",
                    target_device_id=record.target_device_id,
                    source_surface=record.source_surface,
                    destination=record.destination,
                    path_hint=record.path_hint,
                    client_request_id=record.client_request_id,
                    on_progress=report_progress,
                )
                transfer_id = result.transfer_id
            else:
                from mo_everywhere.client import TransferClient

                result = TransferClient(self.config).send_file(
                    record.source_path,
                    target_device_id=record.target_device_id,
                    source_surface=record.source_surface,
                    destination=record.destination,
                    path_hint=record.path_hint,
                    client_request_id=record.client_request_id,
                    on_progress=report_progress,
                )
                transfer_id = str(result.get("transfer_id") or "")
        except Exception as exc:
            stop_heartbeat()
            raw_error = str(exc or "").strip()
            terminal_request = raw_error in {
                "transfer request is already failed",
                "transfer request is already cancelled",
                "transfer request is already expired",
            }
            exhausted = record.attempts >= 8 or terminal_request
            delay = min(300.0, 2.0 ** min(8, max(1, record.attempts)))
            with self._connect() as db:
                db.execute(
                    "UPDATE file_transfer_outbox SET state=?,"
                    "next_attempt_at=?,updated_at=?,error=? WHERE outbox_id=?",
                    (
                        "failed" if exhausted else "queued",
                        0 if exhausted else time.time() + delay,
                        time.time(),
                        safe_transfer_error(exc, retry_scheduled=not exhausted),
                        clean_id,
                    ),
                )
            return self.get(clean_id)
        stop_heartbeat()
        with self._connect() as db:
            db.execute(
                "UPDATE file_transfer_outbox SET state='done',next_attempt_at=0,"
                "updated_at=?,transfer_id=?,error='' WHERE outbox_id=?",
                (time.time(), transfer_id, clean_id),
            )
        self._remove_staged_source(record)
        return self.get(clean_id)

    def process_due(self, *, limit: int = 3) -> list[OutboxRecord]:
        now = time.time()
        retired: list[OutboxRecord] = []
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            retired = [
                self._record(row)
                for row in db.execute(
                    """
                    SELECT * FROM file_transfer_outbox
                    WHERE (state='done' AND updated_at<?)
                       OR (state='cancelled' AND updated_at<?)
                       OR (state='failed' AND updated_at<?)
                       OR (state='staging' AND updated_at<?)
                    """,
                    (
                        now - 7 * 24 * 60 * 60,
                        now - 7 * 24 * 60 * 60,
                        now - 30 * 24 * 60 * 60,
                        now - 300.0,
                    ),
                ).fetchall()
            ]
            db.execute(
                """
                DELETE FROM file_transfer_outbox
                WHERE (state='done' AND updated_at<?)
                   OR (state='cancelled' AND updated_at<?)
                   OR (state='failed' AND updated_at<?)
                   OR (state='staging' AND updated_at<?)
                """,
                (
                    now - 7 * 24 * 60 * 60,
                    now - 7 * 24 * 60 * 60,
                    now - 30 * 24 * 60 * 60,
                    now - 300.0,
                ),
            )
            rows = db.execute(
                """
                SELECT outbox_id FROM file_transfer_outbox
                WHERE (
                  state='queued' AND next_attempt_at<=?
                ) OR (
                  state='sending' AND updated_at<=?
                )
                ORDER BY created_at
                LIMIT ?
                """,
                (
                    now,
                    now - OUTBOX_SENDING_STALE_SECONDS,
                    max(1, min(20, int(limit))),
                ),
            ).fetchall()
        for record in retired:
            self._remove_staged_source(record)
        return [self.process(str(row["outbox_id"])) for row in rows]

    def list_pending(self, *, limit: int = 20) -> list[OutboxRecord]:
        """List sender-owned work not represented by the hub transfer catalog."""
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT * FROM file_transfer_outbox
                WHERE state!='done'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (max(1, min(100, int(limit))),),
            ).fetchall()
        return [self._record(row) for row in rows]

    def clear_terminal_history(self) -> int:
        """Discard terminal sender rows without touching queued or active custody."""
        terminal = ("done", "failed", "cancelled")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                """
                SELECT * FROM file_transfer_outbox
                WHERE state IN (?,?,?)
                """,
                terminal,
            ).fetchall()
            records = [self._record(row) for row in rows]
            db.execute(
                """
                DELETE FROM file_transfer_outbox
                WHERE state IN (?,?,?)
                """,
                terminal,
            )
        for record in records:
            self._remove_staged_source(record)
        return len(records)

    def retry(self, outbox_id: str) -> OutboxRecord:
        clean_id = strict_transfer_id_part(
            outbox_id, label="transfer outbox ID"
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT state,source_path,error,client_request_id "
                "FROM file_transfer_outbox WHERE outbox_id=?",
                (clean_id,),
            ).fetchone()
            if not row:
                raise TransferError("transfer outbox item was not found")
            if str(row["state"]) == "done":
                return self.get(clean_id)
            if str(row["state"]) == "cancelled":
                raise TransferError("cancelled sender custody cannot be retried")
            if str(row["state"]) == "staging":
                raise TransferError("sender custody is still being staged")
            if not Path(str(row["source_path"])).is_file():
                raise TransferError("sender custody bytes are unavailable")
            terminal_request = str(row["error"] or "") in {
                "transfer request is already failed",
                "transfer request is already cancelled",
                "transfer request is already expired",
            }
            db.execute(
                "UPDATE file_transfer_outbox SET state='queued',attempts=0,"
                "client_request_id=?,transfer_id='',next_attempt_at=?,updated_at=?,"
                "error='' WHERE outbox_id=?",
                (
                    uuid.uuid4().hex
                    if terminal_request
                    else str(row["client_request_id"]),
                    time.time(),
                    time.time(),
                    clean_id,
                ),
            )
        return self.process(clean_id)

    def cancel(self, outbox_id: str) -> OutboxRecord:
        clean_id = strict_transfer_id_part(
            outbox_id, label="transfer outbox ID"
        )
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM file_transfer_outbox WHERE outbox_id=?",
                (clean_id,),
            ).fetchone()
            if not row:
                raise TransferError("transfer outbox item was not found")
            record = self._record(row)
            if record.state == "done":
                return record
            if record.state in {"sending", "staging"}:
                raise TransferError("active sender upload cannot be cancelled here")
            db.execute(
                "UPDATE file_transfer_outbox SET state='cancelled',"
                "next_attempt_at=0,updated_at=?,error='' WHERE outbox_id=?",
                (time.time(), clean_id),
            )
        self._remove_staged_source(record)
        return self.get(clean_id)

    def get(self, outbox_id: str) -> OutboxRecord:
        clean_id = strict_transfer_id_part(
            outbox_id, label="transfer outbox ID"
        )
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM file_transfer_outbox WHERE outbox_id=?",
                (clean_id,),
            ).fetchone()
        if not row:
            raise TransferError("transfer outbox item was not found")
        return self._record(row)

    def _remove_staged_source(self, record: OutboxRecord) -> None:
        source = record.source_path.resolve(strict=False)
        try:
            source.relative_to(self.spool_root.resolve(strict=False))
        except ValueError:
            # Compatibility rows may point at an operator-owned source. Never
            # delete a path that this outbox did not stage.
            return
        source.unlink(missing_ok=True)
        try:
            source.parent.rmdir()
        except OSError:
            pass

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS file_transfer_outbox(
                  outbox_id TEXT PRIMARY KEY,
                  source_path TEXT NOT NULL,
                  target_device_id TEXT NOT NULL,
                  target_label TEXT NOT NULL,
                  source_surface TEXT NOT NULL,
                  destination TEXT NOT NULL,
                  path_hint TEXT NOT NULL,
                  client_request_id TEXT NOT NULL UNIQUE,
                  hub_local INTEGER NOT NULL,
                  state TEXT NOT NULL,
                  attempts INTEGER NOT NULL,
                  next_attempt_at REAL NOT NULL,
                  transfer_id TEXT NOT NULL,
                  error TEXT NOT NULL,
                  created_at REAL NOT NULL,
                  updated_at REAL NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return connect_state_db(self.path)

    @staticmethod
    def _record(row: sqlite3.Row) -> OutboxRecord:
        return OutboxRecord(
            outbox_id=str(row["outbox_id"]),
            source_path=Path(str(row["source_path"])).resolve(strict=False),
            target_device_id=str(row["target_device_id"]),
            target_label=str(row["target_label"]),
            source_surface=str(row["source_surface"]),
            destination=str(row["destination"]),
            path_hint=str(row["path_hint"]),
            client_request_id=str(row["client_request_id"]),
            hub_local=bool(row["hub_local"]),
            state=str(row["state"]),
            attempts=int(row["attempts"]),
            next_attempt_at=float(row["next_attempt_at"]),
            transfer_id=str(row["transfer_id"]),
            error=str(row["error"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )
