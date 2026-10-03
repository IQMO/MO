"""Device-scoped pending attachment intake for MO Everywhere."""
from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from core.state.attachments import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS_PER_TURN,
    attachment_category,
    attachment_category_dir,
    attachment_home,
    safe_attachment_name,
)
from core.state.sqlite import connect_state_db
from core.utils.file_hash import file_sha256
from core.transfer import (
    HUB_TARGET_ID,
    TransferService,
)


MAX_PENDING_BYTES_PER_DEVICE = 50 * 1024 * 1024
PENDING_ATTACHMENT_TTL = 24 * 60 * 60


class AttachmentError(RuntimeError):
    """A safe attachment boundary error."""


class AttachmentNotFound(AttachmentError):
    pass


class AttachmentConflict(AttachmentError):
    pass


class AttachmentLimit(AttachmentError):
    pass


@dataclass(frozen=True)
class AttachmentUpload:
    attachment_id: str
    device_id: str
    name: str
    category: str
    temp_path: Path
    saved_path: Path


@dataclass(frozen=True)
class PendingAttachment:
    attachment_id: str
    device_id: str
    name: str
    category: str
    saved_path: Path
    size: int
    created_at: float
    expires_at: float
    state: str
    job_id: str

    def public(self) -> dict[str, Any]:
        return {
            "attachment_id": self.attachment_id,
            "name": self.name,
            "category": self.category,
            "bytes": self.size,
            "created_at": self.created_at,
            "expires_at": self.expires_at if self.state == "pending" else 0.0,
            "state": self.state,
        }


class EverywhereAttachmentStore:
    """Persist bounded uploads and bind opaque IDs to one device turn."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        path: str | Path,
        transfer_service: TransferService | None = None,
    ):
        self.config = config or {}
        self.path = Path(path).expanduser().resolve(strict=False)
        self.transfer_service = transfer_service
        self._finish_lock = threading.RLock()
        self._initialize()

    def begin_upload(self, device_id: str, name: Any) -> AttachmentUpload:
        clean_device = _id(device_id)
        if not clean_device:
            raise AttachmentError("device is invalid")
        clean_name = safe_attachment_name(name)
        attachment_id = uuid.uuid4().hex
        category = attachment_category(clean_name)
        folder = attachment_category_dir(self.config, clean_name)
        suffix = Path(clean_name).suffix
        stem = Path(clean_name).stem or "attachment"
        saved = folder / f"{stem}-{attachment_id[:10]}{suffix}"
        temp = folder / f".upload-{attachment_id}.part"
        return AttachmentUpload(attachment_id, clean_device, clean_name, category, temp, saved)

    def finish_upload(self, upload: AttachmentUpload, size: int) -> PendingAttachment:
        bounded_size = int(size)
        if bounded_size < 1:
            self.abort_upload(upload)
            raise AttachmentError("attachment is empty")
        if bounded_size > MAX_ATTACHMENT_BYTES:
            self.abort_upload(upload)
            raise AttachmentLimit("attachment exceeds the 20 MiB limit")
        try:
            actual_size = upload.temp_path.stat().st_size
        except OSError:
            raise AttachmentError("attachment upload was not completed") from None
        if actual_size != bounded_size:
            self.abort_upload(upload)
            raise AttachmentError("attachment byte count changed during upload")
        now = time.time()
        expires_at = now + PENDING_ATTACHMENT_TTL
        expired: list[str] = []
        reserved = False
        try:
            with self._finish_lock:
                with self._connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    expired = self._prune_rows(db, now)
                    pending_bytes = int(db.execute(
                        "SELECT COALESCE(SUM(bytes),0) FROM everywhere_attachment "
                        "WHERE device_id=? AND state IN ('uploading','pending') "
                        "AND expires_at>?",
                        (upload.device_id, now),
                    ).fetchone()[0])
                    if pending_bytes + bounded_size > MAX_PENDING_BYTES_PER_DEVICE:
                        raise AttachmentLimit("pending attachments exceed the 50 MiB device limit")
                    db.execute(
                        """
                        INSERT INTO everywhere_attachment(
                          attachment_id,device_id,name,category,saved_path,bytes,
                          created_at,expires_at,state,job_id
                        ) VALUES(?,?,?,?,?,?,?,?, 'uploading','')
                        """,
                        (
                            upload.attachment_id,
                            upload.device_id,
                            upload.name,
                            upload.category,
                            "",
                            bounded_size,
                            now,
                            expires_at,
                        ),
                    )
                    reserved = True
                # Compatibility IDs and turn binding remain in this adapter, but
                # the bytes take the same resumable transfer spine as cargo.
                # Do not hold the shared Everywhere registry write lock while
                # hashing and copying up to 20 MiB through that separate owner.
                service = self.transfer_service or TransferService(self.config)
                delivered = service.send_local_file(
                    upload.temp_path,
                    sender_device_id=upload.device_id,
                    target_device_id=HUB_TARGET_ID,
                    source_surface="everywhere_attachment",
                    name=upload.saved_path.name,
                    purpose="turn_context",
                    client_request_id=f"attachment-{upload.attachment_id}",
                )
                if delivered.saved_path is None:
                    raise AttachmentError("attachment transfer was not delivered")
                # Hub custody is now durable. Preserve the uploading row until
                # its adapter update commits so startup recovery can reconnect
                # it after an SQLite/process interruption.
                reserved = False
                with self._connect() as db:
                    changed = db.execute(
                        """
                        UPDATE everywhere_attachment
                        SET saved_path=?,state='pending'
                        WHERE attachment_id=? AND device_id=? AND state='uploading'
                        """,
                        (
                            str(delivered.saved_path),
                            upload.attachment_id,
                            upload.device_id,
                        ),
                    ).rowcount
                if not changed:
                    with self._connect() as db:
                        current = db.execute(
                            "SELECT state,saved_path FROM everywhere_attachment "
                            "WHERE attachment_id=? AND device_id=?",
                            (upload.attachment_id, upload.device_id),
                        ).fetchone()
                    if (
                        not current
                        or str(current["state"]) != "pending"
                        or Path(str(current["saved_path"])).resolve(strict=False)
                        != delivered.saved_path.resolve(strict=False)
                    ):
                        raise AttachmentConflict(
                            "attachment upload reservation expired"
                        )
                # Adapter and transfer custody are already committed. Temp
                # cleanup is secondary and must not make the client retry a
                # successfully accepted attachment.
                self.abort_upload(upload)
        except Exception:
            if reserved:
                with self._connect() as db:
                    db.execute(
                        "DELETE FROM everywhere_attachment "
                        "WHERE attachment_id=? AND device_id=? AND state='uploading'",
                        (upload.attachment_id, upload.device_id),
                    )
            self.abort_upload(upload)
            raise
        finally:
            self._remove_paths(expired)
        return self.get(upload.attachment_id, upload.device_id)

    @staticmethod
    def abort_upload(upload: AttachmentUpload) -> None:
        try:
            upload.temp_path.unlink(missing_ok=True)
        except OSError:
            pass

    def get(self, attachment_id: str, device_id: str) -> PendingAttachment:
        now = time.time()
        with self._connect() as db:
            expired = self._prune_rows(db, now)
            row = db.execute(
                "SELECT * FROM everywhere_attachment WHERE attachment_id=? AND device_id=?",
                (_id(attachment_id), _id(device_id)),
            ).fetchone()
        self._remove_paths(expired)
        if not row:
            raise AttachmentNotFound("attachment was not found")
        return _from_row(row)

    def pending(self, device_id: str) -> list[PendingAttachment]:
        now = time.time()
        with self._connect() as db:
            expired = self._prune_rows(db, now)
            rows = db.execute(
                "SELECT * FROM everywhere_attachment WHERE device_id=? AND state='pending' "
                "AND expires_at>? ORDER BY created_at",
                (_id(device_id), now),
            ).fetchall()
        self._remove_paths(expired)
        return [_from_row(row) for row in rows]

    def delete_pending(self, attachment_id: str, device_id: str) -> bool:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT saved_path,state FROM everywhere_attachment WHERE attachment_id=? AND device_id=?",
                (_id(attachment_id), _id(device_id)),
            ).fetchone()
            if not row:
                raise AttachmentNotFound("attachment was not found")
            if str(row[1]) != "pending":
                raise AttachmentConflict("bound attachments cannot be deleted")
            db.execute("DELETE FROM everywhere_attachment WHERE attachment_id=?", (_id(attachment_id),))
        self._remove_paths([str(row[0])])
        return True

    def bind_to_job(
        self,
        device_id: str,
        job_id: str,
        attachment_ids: Iterable[str],
    ) -> list[PendingAttachment]:
        clean_ids = [_id(value) for value in attachment_ids]
        if any(not value for value in clean_ids) or len(clean_ids) != len(set(clean_ids)):
            raise AttachmentError("attachment_ids must contain unique opaque IDs")
        if len(clean_ids) > MAX_ATTACHMENTS_PER_TURN:
            raise AttachmentLimit("a turn accepts at most 8 attachments")
        clean_device, clean_job = _id(device_id), _id(job_id)
        now = time.time()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            expired = self._prune_rows(db, now)
            existing = [str(row[0]) for row in db.execute(
                "SELECT attachment_id FROM everywhere_job_attachment "
                "WHERE job_id=? AND device_id=? ORDER BY position",
                (clean_job, clean_device),
            ).fetchall()]
            if existing:
                if existing != clean_ids:
                    raise AttachmentConflict("turn request was already bound to different attachments")
                rows = [db.execute(
                    "SELECT * FROM everywhere_attachment WHERE attachment_id=? AND device_id=?",
                    (attachment_id, clean_device),
                ).fetchone() for attachment_id in existing]
                result = [_from_row(row) for row in rows if row is not None]
            else:
                result = []
                for position, attachment_id in enumerate(clean_ids):
                    row = db.execute(
                        "SELECT * FROM everywhere_attachment WHERE attachment_id=? AND device_id=?",
                        (attachment_id, clean_device),
                    ).fetchone()
                    if not row or str(row["state"]) != "pending" or float(row["expires_at"]) <= now:
                        raise AttachmentNotFound("pending attachment was not found")
                    db.execute(
                        "UPDATE everywhere_attachment SET state='bound',job_id=?,expires_at=0 "
                        "WHERE attachment_id=? AND device_id=? AND state='pending'",
                        (clean_job, attachment_id, clean_device),
                    )
                    db.execute(
                        "INSERT INTO everywhere_job_attachment(job_id,device_id,position,attachment_id) "
                        "VALUES(?,?,?,?)",
                        (clean_job, clean_device, position, attachment_id),
                    )
                    result.append(_from_row(row, state="bound", job_id=clean_job, expires_at=0.0))
        self._remove_paths(expired)
        return result

    def bound_paths(self, job_id: str, device_id: str) -> list[Path]:
        return [item.saved_path for item in self.bound(job_id, device_id)]

    def bound(self, job_id: str, device_id: str) -> list[PendingAttachment]:
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT a.* FROM everywhere_job_attachment b
                JOIN everywhere_attachment a ON a.attachment_id=b.attachment_id
                WHERE b.job_id=? AND b.device_id=? AND a.state='bound'
                ORDER BY b.position
                """,
                (_id(job_id), _id(device_id)),
            ).fetchall()
        root = attachment_home(self.config).resolve(strict=False)
        items: list[PendingAttachment] = []
        for row in rows:
            item = _from_row(row)
            path = item.saved_path
            try:
                path.relative_to(root)
            except ValueError:
                continue
            if path.is_file() and path.stat().st_size == item.size:
                items.append(item)
        return items

    def bound_file(
        self,
        job_id: str,
        attachment_id: str,
        device_id: str,
    ) -> PendingAttachment:
        clean_attachment = _id(attachment_id)
        item = next(
            (
                candidate
                for candidate in self.bound(job_id, device_id)
                if candidate.attachment_id == clean_attachment
            ),
            None,
        )
        if item is None:
            raise AttachmentNotFound("bound attachment was not found")
        return item

    def _initialize(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS everywhere_attachment(
                    attachment_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    saved_path TEXT NOT NULL,
                    bytes INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    state TEXT NOT NULL,
                    job_id TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS everywhere_attachment_device_idx
                    ON everywhere_attachment(device_id,state,created_at);
                CREATE TABLE IF NOT EXISTS everywhere_job_attachment(
                    job_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    attachment_id TEXT NOT NULL UNIQUE REFERENCES everywhere_attachment(attachment_id),
                    PRIMARY KEY(job_id,device_id,position)
                );
                """
            )

    def _prune_rows(self, db: sqlite3.Connection, now: float) -> list[str]:
        paths = self._recover_uploading_rows(db)
        rows = db.execute(
            "SELECT attachment_id,name,state,saved_path FROM everywhere_attachment "
            "WHERE state IN ('uploading','pending') AND expires_at<=?",
            (now,),
        ).fetchall()
        db.execute(
            "DELETE FROM everywhere_attachment "
            "WHERE state IN ('uploading','pending') AND expires_at<=?",
            (now,),
        )
        for row in rows:
            saved = str(row["saved_path"] or "").strip()
            if saved:
                paths.append(saved)
            if str(row["state"]) == "uploading":
                paths.append(str(self._upload_temp_path(
                    str(row["attachment_id"]),
                    str(row["name"]),
                )))
        return paths

    def _recover_uploading_rows(self, db: sqlite3.Connection) -> list[str]:
        """Reconnect a delivered transfer after adapter-process interruption."""
        rows = db.execute(
            "SELECT attachment_id,device_id,name,bytes "
            "FROM everywhere_attachment WHERE state='uploading'"
        ).fetchall()
        if not rows:
            return []
        service = self.transfer_service or TransferService(self.config)
        recovered_temps: list[str] = []
        catalog_root = attachment_home(self.config).resolve(strict=False)
        for row in rows:
            attachment_id = str(row["attachment_id"])
            device_id = str(row["device_id"])
            delivered = service.find_request(
                device_id,
                f"attachment-{attachment_id}",
            )
            delivered_bytes_valid = False
            if delivered is not None and delivered.saved_path is not None:
                try:
                    delivered_bytes_valid = (
                        delivered.saved_path.is_file()
                        and delivered.saved_path.stat().st_size
                        == delivered.size_bytes
                        and file_sha256(delivered.saved_path)
                        == delivered.sha256
                    )
                except OSError:
                    delivered_bytes_valid = False
            if (
                delivered is None
                or delivered.state != "done"
                or delivered.saved_path is None
                or delivered.sender_device_id != device_id
                or delivered.target_device_id != HUB_TARGET_ID
                or delivered.source_surface != "everywhere_attachment"
                or delivered.purpose != "turn_context"
                or delivered.destination != "catalog"
                or delivered.path_hint
                or delivered.name
                != self._expected_saved_name(attachment_id, str(row["name"]))
                or delivered.size_bytes != int(row["bytes"])
                or not delivered_bytes_valid
            ):
                continue
            saved = delivered.saved_path.resolve(strict=False)
            try:
                saved.relative_to(catalog_root)
            except ValueError:
                continue
            changed = db.execute(
                "UPDATE everywhere_attachment SET saved_path=?,state='pending' "
                "WHERE attachment_id=? AND device_id=? AND state='uploading'",
                (str(saved), attachment_id, device_id),
            ).rowcount
            if changed:
                recovered_temps.append(
                    str(self._upload_temp_path(attachment_id, str(row["name"])))
                )
        return recovered_temps

    def _upload_temp_path(self, attachment_id: str, name: str) -> Path:
        return (
            attachment_category_dir(self.config, name)
            / f".upload-{attachment_id}.part"
        )

    @staticmethod
    def _expected_saved_name(attachment_id: str, name: str) -> str:
        clean_name = safe_attachment_name(name)
        suffix = Path(clean_name).suffix
        stem = Path(clean_name).stem or "attachment"
        return f"{stem}-{attachment_id[:10]}{suffix}"

    def _remove_paths(self, paths: Iterable[str]) -> None:
        root = attachment_home(self.config).resolve(strict=False)
        for value in paths:
            try:
                path = Path(value).expanduser().resolve(strict=False)
                path.relative_to(root)
                path.unlink(missing_ok=True)
            except (OSError, ValueError):
                pass

    def _connect(self) -> sqlite3.Connection:
        return connect_state_db(self.path, foreign_keys=True)


def _from_row(
    row: sqlite3.Row,
    *,
    state: str | None = None,
    job_id: str | None = None,
    expires_at: float | None = None,
) -> PendingAttachment:
    return PendingAttachment(
        attachment_id=str(row["attachment_id"]),
        device_id=str(row["device_id"]),
        name=str(row["name"]),
        category=str(row["category"]),
        saved_path=Path(str(row["saved_path"])).expanduser().resolve(strict=False),
        size=int(row["bytes"]),
        created_at=float(row["created_at"]),
        expires_at=float(row["expires_at"] if expires_at is None else expires_at),
        state=str(row["state"] if state is None else state),
        job_id=str(row["job_id"] if job_id is None else job_id),
    )


def _id(value: Any) -> str:
    return "".join(
        ch for ch in str(value or "").strip()
        if ch.isascii() and (ch.isalnum() or ch in "-_.")
    )[:64]
