"""SQLite-backed resumable transfer service and local catalog delivery."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from core.state.attachments import (
    attachment_home,
    record_attachment,
    unique_attachment_path,
)
from core.state.paths import default_project_roots
from core.state.sqlite import connect_state_db
from core.utils.atomic_write import atomic_create_bytes
from core.utils.file_hash import file_sha256

from .locking import transfer_operation_lock
from .model import (
    ACTIVE_TRANSFER_STATES,
    TERMINAL_TRANSFER_STATES,
    TransferConflict,
    TransferError,
    TransferLimit,
    TransferNotFound,
    TransferRecord,
    TransferSettings,
    clean_sha256,
    named_destination_path,
    strict_transfer_id_part,
    transfer_id_part,
    validate_create,
)
from .storage import resolve_transfer_storage


HUB_TARGET_ID = "hub"
TERMINAL_HISTORY_SECONDS = 30 * 24 * 60 * 60
MAX_TERMINAL_HISTORY_ROWS = 10_000


class _TransferIntegrityError(TransferConflict):
    """Private marker for verified hub-custody bytes that changed or vanished."""


class TransferService:
    """One store-and-forward owner shared by hub routes and local surfaces."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        path: str | Path | None = None,
        spool_root: str | Path | None = None,
    ):
        self.config = config or {}
        self.settings = TransferSettings.from_config(self.config)
        self.path, self.spool_root = resolve_transfer_storage(
            self.config,
            path=path,
            spool_root=spool_root,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.spool_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def create(
        self,
        *,
        sender_device_id: Any,
        target_device_id: Any,
        source_surface: Any,
        name: Any,
        size_bytes: Any,
        sha256: Any,
        purpose: Any = "cargo",
        destination: Any = "catalog",
        path_hint: Any = "",
        client_request_id: Any = "",
    ) -> TransferRecord:
        values = validate_create(
            sender_device_id=sender_device_id,
            target_device_id=target_device_id,
            source_surface=source_surface,
            purpose=purpose,
            destination=destination,
            name=name,
            size_bytes=size_bytes,
            sha256=sha256,
            path_hint=path_hint,
            settings=self.settings,
        )
        raw_request_id = str(client_request_id or "").strip()
        request_id = transfer_id_part(raw_request_id, limit=96)
        if raw_request_id and request_id != raw_request_id:
            raise TransferError("client_request_id is invalid")
        now = time.time()
        self.expire(now=now)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if request_id:
                prior = db.execute(
                    "SELECT * FROM file_transfer WHERE sender_device_id=? "
                    "AND client_request_id=?",
                    (values["sender_device_id"], request_id),
                ).fetchone()
                if prior:
                    record = self._record(db, prior)
                    if _same_request(record, values):
                        return record
                    raise TransferConflict(
                        "client_request_id already belongs to a different transfer"
                    )
            active = int(
                db.execute(
                    "SELECT COUNT(*) FROM file_transfer WHERE sender_device_id=? "
                    "AND state IN ('created','uploading')",
                    (values["sender_device_id"],),
                ).fetchone()[0]
            )
            if active >= self.settings.concurrent_per_device:
                raise TransferLimit("too many active transfers for this sender")
            transfer_id = uuid.uuid4().hex
            db.execute(
                """
                INSERT INTO file_transfer(
                  transfer_id,client_request_id,sender_device_id,target_device_id,
                  source_surface,purpose,destination,name,size_bytes,sha256,
                  chunk_bytes,chunk_count,state,created_at,updated_at,expires_at,
                  path_hint,failure,saved_path
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'','')
                """,
                (
                    transfer_id,
                    request_id,
                    values["sender_device_id"],
                    values["target_device_id"],
                    values["source_surface"],
                    values["purpose"],
                    values["destination"],
                    values["name"],
                    values["size_bytes"],
                    values["sha256"],
                    self.settings.chunk_bytes,
                    values["chunk_count"],
                    "created",
                    now,
                    now,
                    now + self.settings.hub_spool_ttl_seconds,
                    values["path_hint"],
                ),
            )
        return self.get(transfer_id, values["sender_device_id"])

    def put_chunk(
        self,
        transfer_id: Any,
        actor_device_id: Any,
        index: Any,
        content: bytes,
        *,
        sha256: Any,
    ) -> TransferRecord:
        transfer = self.get(transfer_id, actor_device_id)
        self._require_sender(transfer, actor_device_id)
        if transfer.state not in {"created", "uploading"}:
            raise TransferConflict("transfer no longer accepts chunks")
        if not isinstance(index, int) or isinstance(index, bool):
            raise TransferError("chunk index must be an integer")
        chunk_index = index
        if chunk_index < 0 or chunk_index >= transfer.chunk_count:
            raise TransferError("chunk index is outside the transfer")
        expected = transfer.chunk_bytes
        if chunk_index == transfer.chunk_count - 1:
            expected = transfer.size_bytes - (chunk_index * transfer.chunk_bytes)
        if len(content) != expected:
            raise TransferError("chunk byte count does not match the transfer")
        digest = clean_sha256(sha256)
        if hashlib.sha256(content).hexdigest() != digest:
            raise TransferConflict("chunk digest mismatch")
        folder = self._transfer_dir(transfer.transfer_id)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{chunk_index:08d}.chunk"
        created_target = False
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                current = db.execute(
                    "SELECT state FROM file_transfer WHERE transfer_id=?",
                    (transfer.transfer_id,),
                ).fetchone()
                if not current or str(current["state"]) not in {"created", "uploading"}:
                    raise TransferConflict("transfer no longer accepts chunks")
                prior = db.execute(
                    "SELECT bytes,sha256 FROM file_transfer_chunk "
                    "WHERE transfer_id=? AND chunk_index=?",
                    (transfer.transfer_id, chunk_index),
                ).fetchone()
                if prior:
                    if (
                        int(prior["bytes"]) == len(content)
                        and str(prior["sha256"]) == digest
                        and target.is_file()
                        and target.stat().st_size == len(content)
                        and file_sha256(target) == digest
                    ):
                        return self.get(transfer.transfer_id, actor_device_id)
                    raise TransferConflict(
                        "chunk index already contains different bytes"
                    )
                spooled = int(
                    db.execute(
                        "SELECT COALESCE(SUM(c.bytes),0) "
                        "FROM file_transfer_chunk c "
                        "JOIN file_transfer t ON t.transfer_id=c.transfer_id "
                        "WHERE t.state NOT IN "
                        "('done','failed','cancelled','expired')"
                    ).fetchone()[0]
                )
                if (
                    spooled + len(content)
                    > self.settings.hub_spool_quota_bytes
                ):
                    raise TransferLimit("hub transfer spool quota is full")
                try:
                    atomic_create_bytes(target, content)
                    created_target = True
                except FileExistsError:
                    try:
                        existing_matches = (
                            target.stat().st_size == len(content)
                            and file_sha256(target) == digest
                        )
                    except OSError:
                        existing_matches = False
                    if not existing_matches:
                        raise TransferConflict(
                            "chunk index already contains different bytes"
                        ) from None
                db.execute(
                    "INSERT INTO file_transfer_chunk("
                    "transfer_id,chunk_index,bytes,sha256,uploaded_at"
                    ") VALUES(?,?,?,?,?)",
                    (
                        transfer.transfer_id,
                        chunk_index,
                        len(content),
                        digest,
                        time.time(),
                    ),
                )
                db.execute(
                    "UPDATE file_transfer SET state='uploading',updated_at=? "
                    "WHERE transfer_id=?",
                    (time.time(), transfer.transfer_id),
                )
        except Exception:
            if created_target:
                target.unlink(missing_ok=True)
            raise
        return self.get(transfer.transfer_id, actor_device_id)

    def complete(self, transfer_id: Any, actor_device_id: Any) -> TransferRecord:
        transfer = self.get(transfer_id, actor_device_id)
        self._require_sender(transfer, actor_device_id)
        if transfer.state == "done":
            self._remove_spool(transfer.transfer_id)
            if transfer.target_device_id == HUB_TARGET_ID:
                self._finish_hub_delivery(transfer)
            return self.get(transfer.transfer_id, actor_device_id)
        if transfer.state in {"offered", "claimed", "delivered"}:
            return transfer
        if transfer.state not in {"created", "uploading"}:
            raise TransferConflict("transfer cannot be completed")
        chunks = self._chunks(transfer.transfer_id)
        if len(chunks) != transfer.chunk_count:
            raise TransferConflict("transfer has missing chunks")
        digest = hashlib.sha256()
        total = 0
        try:
            for row in chunks:
                content = self._verified_chunk_bytes(transfer, row)
                digest.update(content)
                total += len(content)
        except _TransferIntegrityError:
            self._fail(transfer.transfer_id, "whole-file digest mismatch")
            raise TransferConflict("whole-file digest mismatch") from None
        if total != transfer.size_bytes or digest.hexdigest() != transfer.sha256:
            self._fail(transfer.transfer_id, "whole-file digest mismatch")
            raise TransferConflict("whole-file digest mismatch")
        already_complete = False
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT state FROM file_transfer WHERE transfer_id=?",
                (transfer.transfer_id,),
            ).fetchone()
            state = str(current["state"]) if current else ""
            if state not in {"created", "uploading"}:
                if state in {"offered", "claimed", "delivered", "done"}:
                    already_complete = True
                else:
                    raise TransferConflict("transfer cannot be completed")
            else:
                db.execute(
                    "UPDATE file_transfer SET state='offered',updated_at=? "
                    "WHERE transfer_id=? AND state IN ('created','uploading')",
                    (time.time(), transfer.transfer_id),
                )
        transfer = self.get(transfer.transfer_id, actor_device_id)
        if already_complete:
            return transfer
        if (
            transfer.target_device_id == HUB_TARGET_ID
            and (
                transfer.purpose == "turn_context"
                or (
                    self.settings.auto_accept
                    and (
                        transfer.destination == "catalog"
                        or self.settings.auto_accept_named_paths
                    )
                )
            )
        ):
            return self.receive_local(
                transfer.transfer_id,
                HUB_TARGET_ID,
                destination_path=(
                    transfer.path_hint
                    if transfer.destination == "named_path"
                    else None
                ),
            )
        return self.get(transfer.transfer_id, actor_device_id)

    def accept(self, transfer_id: Any, actor_device_id: Any) -> TransferRecord:
        clean_id = strict_transfer_id_part(
            transfer_id, label="transfer ID"
        )
        with transfer_operation_lock(
            self.config,
            identity=f"{self.path}\0receive\0{clean_id}",
        ):
            return self._accept(clean_id, actor_device_id)

    def _accept(
        self, transfer_id: str, actor_device_id: Any
    ) -> TransferRecord:
        transfer = self.get(transfer_id, actor_device_id)
        self._require_target(transfer, actor_device_id)
        if transfer.state == "claimed":
            return transfer
        if transfer.state != "offered":
            raise TransferConflict("transfer is not ready to accept")
        if transfer.destination == "named_path" and not self.settings.auto_accept_named_paths:
            # Calling this endpoint is the target's explicit confirmation.
            pass
        now = time.time()
        with self._connect() as db:
            changed = db.execute(
                "UPDATE file_transfer SET state='claimed',updated_at=?,expires_at=? "
                "WHERE transfer_id=? AND state='offered'",
                (
                    now,
                    now + self.settings.hub_spool_ttl_seconds,
                    transfer.transfer_id,
                ),
            ).rowcount
        current = self.get(transfer.transfer_id, actor_device_id)
        if changed or current.state == "claimed":
            return current
        raise TransferConflict("transfer is not ready to accept")

    def receive_local(
        self,
        transfer_id: Any,
        actor_device_id: Any,
        *,
        destination_path: str | Path | None = None,
        allowed_roots: Iterable[str | Path] | None = None,
    ) -> TransferRecord:
        """Confirm and durably receive a transfer whose target is this process."""
        clean_id = strict_transfer_id_part(
            transfer_id, label="transfer ID"
        )
        receiver_roots = list(
            default_project_roots(self.config)
            if allowed_roots is None
            else allowed_roots
        )
        with transfer_operation_lock(
            self.config,
            identity=f"{self.path}\0receive\0{clean_id}",
        ):
            return self._receive_local(
                clean_id,
                actor_device_id,
                destination_path=destination_path,
                allowed_roots=receiver_roots,
            )

    def _receive_local(
        self,
        transfer_id: str,
        actor_device_id: Any,
        *,
        destination_path: str | Path | None = None,
        allowed_roots: Iterable[str | Path],
    ) -> TransferRecord:
        transfer = self.get(transfer_id, actor_device_id)
        self._require_target(transfer, actor_device_id)
        if transfer.destination == "catalog" and destination_path is not None:
            raise TransferConflict(
                "catalog transfer does not accept a named destination"
            )
        if transfer.state == "done":
            if transfer.target_device_id == HUB_TARGET_ID:
                self._finish_hub_delivery(transfer)
            return self.get(transfer.transfer_id, actor_device_id)
        if transfer.state == "offered":
            transfer = self._accept(transfer.transfer_id, actor_device_id)
        if transfer.state not in {"claimed", "delivered"}:
            raise TransferConflict("transfer is not ready to receive")
        if transfer.state == "claimed":
            now = time.time()
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "UPDATE file_transfer SET state='delivered',updated_at=?,expires_at=? "
                    "WHERE transfer_id=? AND state='claimed'",
                    (
                        now,
                        now + self.settings.hub_spool_ttl_seconds,
                        transfer.transfer_id,
                    ),
                )
            transfer = self.get(transfer.transfer_id, actor_device_id)
            if transfer.state != "delivered":
                raise TransferConflict("transfer is not ready to receive")
        if transfer.destination == "catalog":
            saved = self._reserve_catalog_destination(transfer)
            transfer = self.get(transfer.transfer_id, actor_device_id)
            try:
                saved = self._deliver_to_catalog(transfer, saved)
            except _TransferIntegrityError as exc:
                self._fail_integrity(transfer, str(exc))
                raise
            with self._connect() as db:
                changed = db.execute(
                    "UPDATE file_transfer SET state='done',saved_path=?,"
                    "updated_at=?,expires_at=0 WHERE transfer_id=? "
                    "AND state='delivered'",
                    (str(saved), time.time(), transfer.transfer_id),
                ).rowcount
            if not changed:
                raise TransferConflict("transfer is not ready to receive")
            self._finish_hub_delivery(transfer)
            return self.get(transfer.transfer_id, actor_device_id)
        requested = str(destination_path or "").strip()
        if not requested and self.settings.auto_accept_named_paths:
            requested = str(transfer.path_hint or "").strip()
        if not requested:
            raise TransferConflict("named-path receipt requires an explicit path")
        saved = self._reserve_named_destination(
            transfer, requested, allowed_roots=allowed_roots
        )
        try:
            self._deliver_to_named_path(transfer, saved)
        except _TransferIntegrityError as exc:
            self._fail_integrity(transfer, str(exc))
            raise
        with self._connect() as db:
            changed = db.execute(
                "UPDATE file_transfer SET state='delivered',saved_path=?,"
                "updated_at=? WHERE transfer_id=? AND state='delivered'",
                (str(saved), time.time(), transfer.transfer_id),
            ).rowcount
        if not changed:
            raise TransferConflict("transfer is not ready to receive")
        self._queue_notice_once(transfer)
        return self._receipt(
            transfer.transfer_id,
            actor_device_id,
            sha256=transfer.sha256,
        )

    def read_chunk(
        self, transfer_id: Any, actor_device_id: Any, index: Any
    ) -> tuple[TransferRecord, bytes, str]:
        clean_id = strict_transfer_id_part(
            transfer_id, label="transfer ID"
        )
        with transfer_operation_lock(
            self.config,
            identity=f"{self.path}\0receive\0{clean_id}",
        ):
            return self._read_chunk(clean_id, actor_device_id, index)

    def _read_chunk(
        self, transfer_id: str, actor_device_id: Any, index: Any
    ) -> tuple[TransferRecord, bytes, str]:
        transfer = self.get(transfer_id, actor_device_id)
        self._require_target(transfer, actor_device_id)
        if transfer.state == "offered" and self.settings.auto_accept and (
            transfer.destination == "catalog" or self.settings.auto_accept_named_paths
        ):
            transfer = self._accept(transfer.transfer_id, actor_device_id)
        if transfer.state not in {"claimed", "delivered"}:
            raise TransferConflict("transfer must be accepted before download")
        if not isinstance(index, int) or isinstance(index, bool):
            raise TransferError("chunk index must be an integer")
        chunk_index = index
        with self._connect() as db:
            row = db.execute(
                "SELECT chunk_index,bytes,sha256 FROM file_transfer_chunk "
                "WHERE transfer_id=? AND chunk_index=?",
                (transfer.transfer_id, chunk_index),
            ).fetchone()
        if not row:
            raise TransferNotFound("transfer chunk was not found")
        try:
            content = self._verified_chunk_bytes(transfer, row)
        except _TransferIntegrityError as exc:
            self._fail_integrity(transfer, str(exc))
            raise
        now = time.time()
        if transfer.state == "claimed" and chunk_index == transfer.chunk_count - 1:
            with self._connect() as db:
                db.execute(
                    "UPDATE file_transfer SET state='delivered',updated_at=?,expires_at=? "
                    "WHERE transfer_id=? AND state='claimed'",
                    (
                        now,
                        now + self.settings.hub_spool_ttl_seconds,
                        transfer.transfer_id,
                    ),
                )
            transfer = self.get(transfer.transfer_id, actor_device_id)
        elif transfer.state in {"claimed", "delivered"}:
            with self._connect() as db:
                db.execute(
                    "UPDATE file_transfer SET updated_at=?,expires_at=? "
                    "WHERE transfer_id=? AND state IN ('claimed','delivered')",
                    (
                        now,
                        now + self.settings.hub_spool_ttl_seconds,
                        transfer.transfer_id,
                    ),
                )
        return transfer, content, str(row["sha256"])

    def receipt(
        self,
        transfer_id: Any,
        actor_device_id: Any,
        *,
        sha256: Any,
    ) -> TransferRecord:
        clean_id = strict_transfer_id_part(
            transfer_id, label="transfer ID"
        )
        with transfer_operation_lock(
            self.config,
            identity=f"{self.path}\0receive\0{clean_id}",
        ):
            return self._receipt(
                clean_id,
                actor_device_id,
                sha256=sha256,
            )

    def _receipt(
        self,
        transfer_id: str,
        actor_device_id: Any,
        *,
        sha256: Any,
    ) -> TransferRecord:
        transfer = self.get(transfer_id, actor_device_id)
        self._require_target(transfer, actor_device_id)
        if transfer.state == "done":
            self._remove_spool(transfer.transfer_id)
            return transfer
        if transfer.state not in {"claimed", "delivered"}:
            raise TransferConflict("transfer is not awaiting a receipt")
        if clean_sha256(sha256) != transfer.sha256:
            raise TransferConflict("receipt digest mismatch")
        already_done = False
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT state FROM file_transfer WHERE transfer_id=?",
                (transfer.transfer_id,),
            ).fetchone()
            state = str(current["state"]) if current else ""
            if state == "done":
                already_done = True
            elif state not in {"claimed", "delivered"}:
                raise TransferConflict("transfer is not awaiting a receipt")
            else:
                db.execute(
                    "UPDATE file_transfer SET state='done',updated_at=?,expires_at=0 "
                    "WHERE transfer_id=? AND state IN ('claimed','delivered')",
                    (time.time(), transfer.transfer_id),
                )
        if already_done:
            return self.get(transfer.transfer_id, actor_device_id)
        self._remove_spool(transfer.transfer_id)
        return self.get(transfer.transfer_id, actor_device_id)

    def cancel(self, transfer_id: Any, actor_device_id: Any) -> TransferRecord:
        clean_id = strict_transfer_id_part(
            transfer_id, label="transfer ID"
        )
        with transfer_operation_lock(
            self.config,
            identity=f"{self.path}\0receive\0{clean_id}",
        ):
            return self._cancel(clean_id, actor_device_id)

    def _cancel(
        self,
        transfer_id: str,
        actor_device_id: Any,
    ) -> TransferRecord:
        actor = strict_transfer_id_part(
            actor_device_id, label="device ID"
        )
        terminal_noop = False
        sender_noop = False
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM file_transfer WHERE transfer_id=?",
                (transfer_id,),
            ).fetchone()
            if not row:
                raise TransferNotFound("transfer was not found")
            transfer = self._record(db, row)
            if actor not in {
                transfer.sender_device_id,
                transfer.target_device_id,
            }:
                raise TransferNotFound("transfer was not found")
            if transfer.state in TERMINAL_TRANSFER_STATES:
                terminal_noop = True
            elif (
                actor == transfer.sender_device_id
                and transfer.state in {"claimed", "delivered"}
            ):
                sender_noop = True
            else:
                db.execute(
                    "UPDATE file_transfer SET state='cancelled',updated_at=?,expires_at=0 "
                    "WHERE transfer_id=? AND state NOT IN "
                    "('done','failed','cancelled','expired')",
                    (time.time(), transfer.transfer_id),
                )
        if sender_noop:
            return transfer
        self._remove_empty_reservation(transfer)
        self._remove_spool(transfer.transfer_id)
        if terminal_noop:
            return transfer
        return self.get(transfer.transfer_id, actor)

    def get(self, transfer_id: Any, actor_device_id: Any = "") -> TransferRecord:
        clean_id = strict_transfer_id_part(
            transfer_id, label="transfer ID"
        )
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM file_transfer WHERE transfer_id=?", (clean_id,)
            ).fetchone()
            if not row:
                raise TransferNotFound("transfer was not found")
            record = self._record(db, row)
        actor = strict_transfer_id_part(
            actor_device_id,
            label="device ID",
            allow_empty=True,
        )
        if actor and actor not in {
            record.sender_device_id,
            record.target_device_id,
        }:
            raise TransferNotFound("transfer was not found")
        return record

    def find_request(
        self,
        sender_device_id: Any,
        client_request_id: Any,
    ) -> TransferRecord | None:
        """Find one sender-owned idempotent request for adapter recovery."""
        sender = transfer_id_part(sender_device_id)
        request_id = transfer_id_part(client_request_id, limit=96)
        if not sender or not request_id:
            return None
        with self._connect() as db:
            row = db.execute(
                "SELECT * FROM file_transfer WHERE sender_device_id=? "
                "AND client_request_id=?",
                (sender, request_id),
            ).fetchone()
            return self._record(db, row) if row else None

    def list_for(
        self,
        actor_device_id: Any,
        *,
        direction: str = "all",
        states: Iterable[str] | None = None,
        purposes: Iterable[str] | None = None,
        limit: int = 100,
    ) -> list[TransferRecord]:
        actor = strict_transfer_id_part(
            actor_device_id, label="device ID"
        )
        clean_direction = str(direction or "all").strip().lower()
        if clean_direction == "incoming":
            where, args = "target_device_id=?", [actor]
        elif clean_direction == "outgoing":
            where, args = "sender_device_id=?", [actor]
        elif clean_direction == "all":
            where, args = "(sender_device_id=? OR target_device_id=?)", [actor, actor]
        else:
            raise TransferError("direction must be incoming, outgoing, or all")
        clean_states = [
            str(value).strip().lower()
            for value in (states or ())
            if str(value).strip().lower() in ACTIVE_TRANSFER_STATES | TERMINAL_TRANSFER_STATES
        ]
        if clean_states:
            where += " AND state IN (" + ",".join("?" for _ in clean_states) + ")"
            args.extend(clean_states)
        clean_purposes = [
            str(value).strip().lower()
            for value in (purposes or ())
            if str(value).strip().lower() in {"cargo", "turn_context"}
        ]
        if clean_purposes:
            where += " AND purpose IN (" + ",".join("?" for _ in clean_purposes) + ")"
            args.extend(clean_purposes)
        where += (
            " AND transfer_id NOT IN ("
            "SELECT transfer_id FROM file_transfer_hidden WHERE device_id=?"
            ")"
        )
        args.append(actor)
        args.append(max(1, min(200, int(limit))))
        with self._connect() as db:
            rows = db.execute(
                f"SELECT * FROM file_transfer WHERE {where} "
                "ORDER BY updated_at DESC LIMIT ?",
                args,
            ).fetchall()
            return [self._record(db, row) for row in rows]

    def clear_terminal_history(self, actor_device_id: Any) -> int:
        """Hide only this device's completed rows; active custody is untouched."""
        actor = strict_transfer_id_part(
            actor_device_id, label="device ID"
        )
        terminal = sorted(TERMINAL_TRANSFER_STATES)
        placeholders = ",".join("?" for _ in terminal)
        with self._connect() as db:
            before = db.total_changes
            db.execute(
                f"""
                INSERT OR IGNORE INTO file_transfer_hidden(
                  device_id,transfer_id,hidden_at
                )
                SELECT ?,transfer_id,?
                FROM file_transfer
                WHERE (sender_device_id=? OR target_device_id=?)
                  AND state IN ({placeholders})
                """,
                [actor, time.time(), actor, actor, *terminal],
            )
            return db.total_changes - before

    def received_ranges(self, transfer_id: Any, actor_device_id: Any) -> list[list[int]]:
        """Return compact inclusive chunk ranges for precise sender resume."""
        transfer = self.get(transfer_id, actor_device_id)
        self._require_sender(transfer, actor_device_id)
        indices = [
            int(row["chunk_index"]) for row in self._chunks(transfer.transfer_id)
        ]
        if not indices:
            return []
        ranges: list[list[int]] = []
        start = end = indices[0]
        for index in indices[1:]:
            if index == end + 1:
                end = index
            else:
                ranges.append([start, end])
                start = end = index
        ranges.append([start, end])
        return ranges

    def send_local_file(
        self,
        path: str | Path,
        *,
        sender_device_id: str,
        target_device_id: str,
        source_surface: str,
        name: str = "",
        purpose: str = "cargo",
        destination: str = "catalog",
        path_hint: str = "",
        client_request_id: str = "",
        on_progress: Any = None,
    ) -> TransferRecord:
        source = Path(path).expanduser().resolve(strict=True)
        if not source.is_file():
            raise TransferError("source is not a file")
        size = source.stat().st_size
        if size < 1:
            raise TransferError("source is empty")
        if size > self.settings.max_bytes:
            raise TransferLimit("source exceeds the configured transfer limit")
        whole = file_sha256(source)
        transfer = self.create(
            sender_device_id=sender_device_id,
            target_device_id=target_device_id,
            source_surface=source_surface,
            name=name or source.name,
            size_bytes=size,
            sha256=whole,
            purpose=purpose,
            destination=destination,
            path_hint=path_hint,
            client_request_id=client_request_id,
        )
        if transfer.state in {"offered", "claimed", "delivered", "done"}:
            return transfer
        if transfer.state in TERMINAL_TRANSFER_STATES:
            raise TransferConflict(
                f"transfer request is already {transfer.state}"
            )
        uploaded = {
            int(row["chunk_index"])
            for row in self._chunks(transfer.transfer_id)
        }
        with source.open("rb") as handle:
            for index in range(transfer.chunk_count):
                content = handle.read(transfer.chunk_bytes)
                if index not in uploaded:
                    self.put_chunk(
                        transfer.transfer_id,
                        sender_device_id,
                        index,
                        content,
                        sha256=hashlib.sha256(content).hexdigest(),
                    )
                if callable(on_progress):
                    on_progress(
                        min(size, (index + 1) * transfer.chunk_bytes),
                        size,
                    )
        return self.complete(transfer.transfer_id, sender_device_id)

    def expire(self, *, now: float | None = None) -> int:
        stamp = float(now if now is not None else time.time())
        with self._connect() as db:
            rows = db.execute(
                "SELECT transfer_id,target_device_id,destination,saved_path "
                "FROM file_transfer WHERE expires_at>0 "
                "AND expires_at<=? AND state NOT IN ('done','failed','cancelled','expired')",
                (stamp,),
            ).fetchall()
        expired: list[sqlite3.Row] = []
        for row in rows:
            transfer_id = str(row["transfer_id"])
            with transfer_operation_lock(
                self.config,
                identity=f"{self.path}\0receive\0{transfer_id}",
            ):
                with self._connect() as db:
                    changed = db.execute(
                        "UPDATE file_transfer SET state='expired',updated_at=?,"
                        "expires_at=0 WHERE transfer_id=? AND expires_at>0 "
                        "AND expires_at<=? AND state NOT IN "
                        "('done','failed','cancelled','expired')",
                        (stamp, transfer_id, stamp),
                    ).rowcount
                if changed:
                    expired.append(row)
                    self._remove_empty_reservation_fields(
                        target_device_id=str(row["target_device_id"]),
                        destination=str(row["destination"]),
                        saved_path=str(row["saved_path"] or ""),
                    )
                    self._remove_spool(transfer_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._prune_terminal_history(db, now=stamp)
        return len(expired)

    def _reserve_catalog_destination(self, transfer: TransferRecord) -> Path:
        if transfer.saved_path is not None:
            return self._validated_catalog_destination(transfer.saved_path)
        created: Path | None = None
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT saved_path FROM file_transfer WHERE transfer_id=?",
                    (transfer.transfer_id,),
                ).fetchone()
                if not row:
                    raise TransferNotFound("transfer was not found")
                saved = str(row["saved_path"] or "").strip()
                if saved:
                    return self._validated_catalog_destination(Path(saved))
                while True:
                    destination = unique_attachment_path(
                        self.config, transfer.name
                    )
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        with destination.open("xb"):
                            pass
                    except FileExistsError:
                        continue
                    created = destination
                    break
                db.execute(
                    "UPDATE file_transfer SET saved_path=?,updated_at=? "
                    "WHERE transfer_id=? AND saved_path=''",
                    (str(destination), time.time(), transfer.transfer_id),
                )
            return destination.resolve(strict=False)
        except Exception:
            if created is not None:
                created.unlink(missing_ok=True)
            raise

    def _reserve_named_destination(
        self,
        transfer: TransferRecord,
        destination_path: str | Path,
        *,
        allowed_roots: Iterable[str | Path],
    ) -> Path:
        try:
            destination = named_destination_path(
                destination_path, allowed_roots
            )
        except TransferError as exc:
            raise TransferConflict(str(exc)) from None
        created = False
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT saved_path FROM file_transfer WHERE transfer_id=?",
                    (transfer.transfer_id,),
                ).fetchone()
                if not row:
                    raise TransferNotFound("transfer was not found")
                saved = str(row["saved_path"] or "").strip()
                if saved:
                    prior = Path(saved).resolve(strict=False)
                    if prior != destination:
                        raise TransferConflict(
                            "transfer already reserved a different destination"
                        )
                    return prior
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with destination.open("xb"):
                        pass
                except FileExistsError:
                    raise TransferConflict(
                        "transfer destination already exists"
                    ) from None
                created = True
                db.execute(
                    "UPDATE file_transfer SET saved_path=?,updated_at=? "
                    "WHERE transfer_id=? AND saved_path=''",
                    (str(destination), time.time(), transfer.transfer_id),
                )
            return destination
        except Exception:
            if created:
                destination.unlink(missing_ok=True)
            raise

    def _deliver_to_named_path(
        self, transfer: TransferRecord, destination: Path
    ) -> None:
        if destination.is_file() and (
            destination.stat().st_size == transfer.size_bytes
            and file_sha256(destination) == transfer.sha256
        ):
            return
        if destination.exists() and (
            not destination.is_file() or destination.stat().st_size != 0
        ):
            raise TransferConflict("reserved transfer destination was changed")
        staged = destination.with_name(
            f".{destination.name}.{transfer.transfer_id}.part"
        )
        staged.unlink(missing_ok=True)
        self._assemble_verified_transfer(
            transfer,
            staged=staged,
            destination=destination,
            integrity_error="transfer whole-file digest mismatch",
        )

    def _validated_catalog_destination(self, path: Path) -> Path:
        destination = path.expanduser().resolve(strict=False)
        root = attachment_home(self.config).resolve(strict=False)
        try:
            destination.relative_to(root)
        except ValueError:
            raise TransferConflict("saved catalog destination is invalid") from None
        return destination

    def _deliver_to_catalog(
        self, transfer: TransferRecord, destination: Path
    ) -> Path:
        destination = self._validated_catalog_destination(destination)
        if destination.is_file() and (
            destination.stat().st_size == transfer.size_bytes
            and file_sha256(destination) == transfer.sha256
        ):
            record_attachment(
                self.config,
                destination,
                origin=f"file_transfer:{transfer.source_surface}",
                attachment_id=transfer.transfer_id,
            )
            return destination
        if destination.exists() and (
            not destination.is_file() or destination.stat().st_size != 0
        ):
            raise TransferConflict("reserved catalog destination was changed")
        staged = destination.with_name(
            f".{destination.name}.{transfer.transfer_id}.{uuid.uuid4().hex}.part"
        )
        self._assemble_verified_transfer(
            transfer,
            staged=staged,
            destination=destination,
            integrity_error="transfer bytes changed before catalog installation",
        )
        record_attachment(
            self.config,
            destination,
            origin=f"file_transfer:{transfer.source_surface}",
            attachment_id=transfer.transfer_id,
        )
        return destination.resolve(strict=False)

    def _assemble_verified_transfer(
        self,
        transfer: TransferRecord,
        *,
        staged: Path,
        destination: Path,
        integrity_error: str,
    ) -> None:
        """Assemble verified chunks durably, then atomically install the file."""
        try:
            digest = hashlib.sha256()
            size = 0
            with staged.open("xb") as output:
                for row in self._chunks(transfer.transfer_id):
                    content = self._verified_chunk_bytes(transfer, row)
                    output.write(content)
                    digest.update(content)
                    size += len(content)
                output.flush()
                os.fsync(output.fileno())
            if size != transfer.size_bytes or digest.hexdigest() != transfer.sha256:
                raise _TransferIntegrityError(integrity_error)
            os.replace(staged, destination)
        except Exception:
            staged.unlink(missing_ok=True)
            raise

    def _finish_hub_delivery(self, transfer: TransferRecord) -> None:
        self._remove_spool(transfer.transfer_id)
        if transfer.purpose == "turn_context":
            return
        self._queue_notice_once(transfer)

    def _queue_notice_once(self, transfer: TransferRecord) -> None:
        with self._connect() as db:
            row = db.execute(
                "SELECT notice_queued FROM file_transfer WHERE transfer_id=?",
                (transfer.transfer_id,),
            ).fetchone()
        if row and int(row["notice_queued"]):
            return
        from .presence import queue_transfer_notice

        try:
            queue_transfer_notice(
                self.config,
                transfer_id=transfer.transfer_id,
                name=transfer.name,
                size_bytes=transfer.size_bytes,
            )
        except Exception:
            # Catalog custody is already durable. A secondary notice failure
            # must not make the sender retry completed bytes.
            pass

    def _record(self, db: sqlite3.Connection, row: sqlite3.Row) -> TransferRecord:
        progress = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(bytes),0) FROM file_transfer_chunk "
            "WHERE transfer_id=?",
            (str(row["transfer_id"]),),
        ).fetchone()
        saved = str(row["saved_path"] or "").strip()
        return TransferRecord(
            transfer_id=str(row["transfer_id"]),
            client_request_id=str(row["client_request_id"]),
            sender_device_id=str(row["sender_device_id"]),
            target_device_id=str(row["target_device_id"]),
            source_surface=str(row["source_surface"]),
            purpose=str(row["purpose"]),
            destination=str(row["destination"]),
            name=str(row["name"]),
            size_bytes=int(row["size_bytes"]),
            sha256=str(row["sha256"]),
            chunk_bytes=int(row["chunk_bytes"]),
            chunk_count=int(row["chunk_count"]),
            uploaded_chunks=int(progress[0]),
            uploaded_bytes=int(progress[1]),
            state=str(row["state"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            expires_at=float(row["expires_at"]),
            path_hint=str(row["path_hint"]),
            failure=str(row["failure"]),
            saved_path=Path(saved).resolve(strict=False) if saved else None,
        )

    def _chunks(self, transfer_id: str) -> list[sqlite3.Row]:
        with self._connect() as db:
            return db.execute(
                "SELECT chunk_index,bytes,sha256 FROM file_transfer_chunk "
                "WHERE transfer_id=? ORDER BY chunk_index",
                (transfer_id,),
            ).fetchall()

    def _verified_chunk_bytes(
        self,
        transfer: TransferRecord,
        row: sqlite3.Row,
    ) -> bytes:
        path = self._chunk_path(
            transfer.transfer_id, int(row["chunk_index"])
        )
        try:
            content = path.read_bytes()
        except OSError:
            raise _TransferIntegrityError(
                "transfer chunk bytes are unavailable"
            ) from None
        digest = str(row["sha256"])
        if (
            len(content) != int(row["bytes"])
            or hashlib.sha256(content).hexdigest() != digest
        ):
            raise _TransferIntegrityError(
                "transfer chunk integrity changed"
            )
        return content

    def _fail_integrity(
        self,
        transfer: TransferRecord,
        failure: str,
    ) -> None:
        with self._connect() as db:
            changed = db.execute(
                "UPDATE file_transfer SET state='failed',failure=?,updated_at=?,"
                "expires_at=0 WHERE transfer_id=? AND state IN "
                "('created','uploading','offered','claimed','delivered')",
                (str(failure)[:160], time.time(), transfer.transfer_id),
            ).rowcount
        if changed:
            self._remove_empty_reservation(transfer)
            self._remove_spool(transfer.transfer_id)

    def _fail(self, transfer_id: str, failure: str) -> None:
        with self._connect() as db:
            changed = db.execute(
                "UPDATE file_transfer SET state='failed',failure=?,updated_at=?,"
                "expires_at=0 WHERE transfer_id=? "
                "AND state IN ('created','uploading')",
                (str(failure)[:160], time.time(), transfer_id),
            ).rowcount
        if changed:
            self._remove_spool(transfer_id)

    def _transfer_dir(self, transfer_id: str) -> Path:
        return self.spool_root / transfer_id_part(transfer_id)

    def _chunk_path(self, transfer_id: str, index: int) -> Path:
        return self._transfer_dir(transfer_id) / f"{index:08d}.chunk"

    def _remove_spool(self, transfer_id: str) -> None:
        folder = self._transfer_dir(transfer_id)
        if not folder.is_dir():
            return
        for item in folder.iterdir():
            if item.is_file():
                item.unlink(missing_ok=True)
        try:
            folder.rmdir()
        except OSError:
            pass

    def _remove_empty_reservation(self, transfer: TransferRecord) -> None:
        self._remove_empty_reservation_fields(
            target_device_id=transfer.target_device_id,
            destination=transfer.destination,
            saved_path=str(transfer.saved_path or ""),
        )

    def _remove_empty_reservation_fields(
        self,
        *,
        target_device_id: str,
        destination: str,
        saved_path: str,
    ) -> None:
        """Remove only a zero-byte placeholder reserved by local hub receipt."""
        if target_device_id != HUB_TARGET_ID or not saved_path:
            return
        path = Path(saved_path).expanduser().resolve(strict=False)
        if destination == "catalog":
            try:
                path.relative_to(attachment_home(self.config).resolve(strict=False))
            except ValueError:
                return
        try:
            if path.is_file() and path.stat().st_size == 0:
                path.unlink()
        except OSError:
            pass

    @staticmethod
    def _prune_terminal_history(
        db: sqlite3.Connection, *, now: float
    ) -> None:
        terminal = "('done','failed','cancelled','expired')"
        db.execute(
            f"DELETE FROM file_transfer WHERE state IN {terminal} AND updated_at<?",
            (now - TERMINAL_HISTORY_SECONDS,),
        )
        db.execute(
            f"""
            DELETE FROM file_transfer
            WHERE state IN {terminal}
              AND transfer_id NOT IN (
                SELECT transfer_id FROM file_transfer
                WHERE state IN {terminal}
                ORDER BY updated_at DESC,transfer_id DESC
                LIMIT ?
              )
            """,
            (MAX_TERMINAL_HISTORY_ROWS,),
        )

    @staticmethod
    def _require_sender(transfer: TransferRecord, actor: Any) -> None:
        if transfer.sender_device_id != transfer_id_part(actor):
            raise TransferNotFound("transfer was not found")

    @staticmethod
    def _require_target(transfer: TransferRecord, actor: Any) -> None:
        if transfer.target_device_id != transfer_id_part(actor):
            raise TransferNotFound("transfer was not found")

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS file_transfer(
                    transfer_id TEXT PRIMARY KEY,
                    client_request_id TEXT NOT NULL DEFAULT '',
                    sender_device_id TEXT NOT NULL,
                    target_device_id TEXT NOT NULL,
                    source_surface TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    name TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    chunk_bytes INTEGER NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    path_hint TEXT NOT NULL DEFAULT '',
                    failure TEXT NOT NULL DEFAULT '',
                    saved_path TEXT NOT NULL DEFAULT '',
                    notice_queued INTEGER NOT NULL DEFAULT 0
                );
                CREATE UNIQUE INDEX IF NOT EXISTS file_transfer_request_idx
                    ON file_transfer(sender_device_id,client_request_id)
                    WHERE client_request_id<>'';
                CREATE INDEX IF NOT EXISTS file_transfer_sender_idx
                    ON file_transfer(sender_device_id,state,updated_at);
                CREATE INDEX IF NOT EXISTS file_transfer_target_idx
                    ON file_transfer(target_device_id,state,updated_at);
                CREATE TABLE IF NOT EXISTS file_transfer_chunk(
                    transfer_id TEXT NOT NULL REFERENCES file_transfer(transfer_id)
                        ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    uploaded_at REAL NOT NULL,
                    PRIMARY KEY(transfer_id,chunk_index)
                );
                CREATE TABLE IF NOT EXISTS file_transfer_hidden(
                    device_id TEXT NOT NULL,
                    transfer_id TEXT NOT NULL REFERENCES file_transfer(transfer_id)
                        ON DELETE CASCADE,
                    hidden_at REAL NOT NULL,
                    PRIMARY KEY(device_id,transfer_id)
                );
                """
            )
            columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(file_transfer)").fetchall()
            }
            if "notice_queued" not in columns:
                db.execute(
                    "ALTER TABLE file_transfer "
                    "ADD COLUMN notice_queued INTEGER NOT NULL DEFAULT 0"
                )
            self._prune_terminal_history(db, now=time.time())
            active_ids = {
                str(row["transfer_id"])
                for row in db.execute(
                    "SELECT transfer_id FROM file_transfer "
                    "WHERE state NOT IN ('done','failed','cancelled','expired')"
                ).fetchall()
            }
        self._recover_spool(active_ids)
        self._recover_hub_notices()

    def _recover_hub_notices(self) -> None:
        """Requeue a hub-local completion interrupted before notice commit."""
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT * FROM file_transfer
                WHERE state='done'
                  AND target_device_id=?
                  AND purpose='cargo'
                  AND saved_path<>''
                  AND notice_queued=0
                ORDER BY updated_at,transfer_id
                LIMIT 200
                """,
                (HUB_TARGET_ID,),
            ).fetchall()
            records = [self._record(db, row) for row in rows]
        for record in records:
            self._queue_notice_once(record)

    def _recover_spool(self, active_ids: set[str]) -> None:
        """Remove only orphaned/terminal chunk folders after interrupted cleanup."""
        try:
            folders = list(self.spool_root.iterdir())
        except OSError:
            return
        for folder in folders:
            if (
                not folder.is_dir()
                or folder.name == "outbox"
                or transfer_id_part(folder.name) != folder.name
                or folder.name in active_ids
            ):
                continue
            self._remove_spool(folder.name)

    def _connect(self) -> sqlite3.Connection:
        return connect_state_db(self.path, foreign_keys=True)


def _same_request(record: TransferRecord, values: dict[str, Any]) -> bool:
    return (
        record.target_device_id == values["target_device_id"]
        and record.source_surface == values["source_surface"]
        and record.purpose == values["purpose"]
        and record.destination == values["destination"]
        and record.name == values["name"]
        and record.size_bytes == values["size_bytes"]
        and record.sha256 == values["sha256"]
        and record.path_hint == values["path_hint"]
    )
