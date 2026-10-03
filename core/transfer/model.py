"""Pure transfer models and configuration."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from core.state.attachments import safe_attachment_name
from core.utils.number_utils import as_bounded_int


DEFAULT_MAX_BYTES = 2 * 1024 * 1024 * 1024
TURN_CONTEXT_MAX_BYTES = 20 * 1024 * 1024
DEFAULT_CHUNK_BYTES = 8 * 1024 * 1024
DEFAULT_HUB_QUOTA_BYTES = 10 * 1024 * 1024 * 1024
DEFAULT_TTL_SECONDS = 24 * 60 * 60
DEFAULT_CONCURRENT_PER_DEVICE = 3
MIN_CHUNK_BYTES = 64 * 1024
MAX_CHUNK_BYTES = 16 * 1024 * 1024

ACTIVE_TRANSFER_STATES = frozenset(
    {"created", "uploading", "offered", "claimed", "delivered"}
)
TERMINAL_TRANSFER_STATES = frozenset(
    {"done", "failed", "cancelled", "expired"}
)
TRANSFER_STATES = ACTIVE_TRANSFER_STATES | TERMINAL_TRANSFER_STATES


class TransferError(RuntimeError):
    """Safe transfer-boundary error."""


class TransferNotFound(TransferError):
    pass


class TransferConflict(TransferError):
    pass


class TransferLimit(TransferError):
    pass


def safe_transfer_error(
    exc: Exception, *, retry_scheduled: bool = False
) -> str:
    """Return only errors whose owners guarantee path/credential-safe text."""
    module = type(exc).__module__
    if isinstance(exc, TransferError) or (
        module == "mo_everywhere.client"
        and type(exc).__name__ == "EverywhereClientError"
    ):
        text = str(exc or "").replace("\r", " ").replace("\n", " ").strip()
        return (text or "transfer failed")[:240]
    return (
        "transfer failed; retry is scheduled"
        if retry_scheduled
        else "transfer failed"
    )


@dataclass(frozen=True)
class TransferSettings:
    enabled: bool = False
    max_bytes: int = DEFAULT_MAX_BYTES
    chunk_bytes: int = DEFAULT_CHUNK_BYTES
    auto_accept: bool = True
    auto_accept_named_paths: bool = False
    hub_spool_quota_bytes: int = DEFAULT_HUB_QUOTA_BYTES
    hub_spool_ttl_seconds: int = DEFAULT_TTL_SECONDS
    concurrent_per_device: int = DEFAULT_CONCURRENT_PER_DEVICE

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> "TransferSettings":
        raw = (config or {}).get("file_transfer")
        block = raw if isinstance(raw, dict) else {}
        chunk_bytes = as_bounded_int(
            block.get("chunk_bytes"),
            DEFAULT_CHUNK_BYTES,
            MIN_CHUNK_BYTES,
            MAX_CHUNK_BYTES,
        )
        hub_spool_quota_bytes = as_bounded_int(
            block.get("hub_spool_quota_bytes"),
            DEFAULT_HUB_QUOTA_BYTES,
            MIN_CHUNK_BYTES,
            100 * 1024 * 1024 * 1024,
        )
        return cls(
            enabled=block.get("enabled") is True,
            max_bytes=as_bounded_int(
                block.get("max_bytes"), DEFAULT_MAX_BYTES, 1, DEFAULT_MAX_BYTES
            ),
            chunk_bytes=chunk_bytes,
            auto_accept=block.get("auto_accept", True) is True,
            auto_accept_named_paths=block.get("auto_accept_named_paths") is True,
            # A valid configured chunk must always fit in an otherwise-empty
            # spool. Keep the independent quota knob, but never manufacture a
            # configuration in which every first chunk is rejected.
            hub_spool_quota_bytes=max(chunk_bytes, hub_spool_quota_bytes),
            hub_spool_ttl_seconds=as_bounded_int(
                block.get("hub_spool_ttl_seconds"),
                DEFAULT_TTL_SECONDS,
                60,
                30 * 24 * 60 * 60,
            ),
            concurrent_per_device=as_bounded_int(
                block.get("concurrent_per_device"),
                DEFAULT_CONCURRENT_PER_DEVICE,
                1,
                16,
            ),
        )


@dataclass(frozen=True)
class TransferRecord:
    transfer_id: str
    client_request_id: str
    sender_device_id: str
    target_device_id: str
    source_surface: str
    purpose: str
    destination: str
    name: str
    size_bytes: int
    sha256: str
    chunk_bytes: int
    chunk_count: int
    uploaded_chunks: int
    uploaded_bytes: int
    state: str
    created_at: float
    updated_at: float
    expires_at: float
    path_hint: str = ""
    failure: str = ""
    saved_path: Path | None = None

    @property
    def progress(self) -> float:
        if self.size_bytes <= 0:
            return 0.0
        return min(1.0, self.uploaded_bytes / self.size_bytes)

    def public(self) -> dict[str, Any]:
        """Participant-safe representation; never exposes an actual saved path."""
        return {
            "transfer_id": self.transfer_id,
            "client_request_id": self.client_request_id,
            "sender_device_id": self.sender_device_id,
            "target_device_id": self.target_device_id,
            "source_surface": self.source_surface,
            "purpose": self.purpose,
            "destination": self.destination,
            "name": self.name,
            "bytes": self.size_bytes,
            "sha256": self.sha256,
            "chunk_bytes": self.chunk_bytes,
            "chunk_count": self.chunk_count,
            "uploaded_chunks": self.uploaded_chunks,
            "uploaded_bytes": self.uploaded_bytes,
            "progress": self.progress,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "path_hint": self.path_hint,
            "failure": self.failure,
        }


def validate_create(
    *,
    sender_device_id: Any,
    target_device_id: Any,
    source_surface: Any,
    purpose: Any,
    destination: Any,
    name: Any,
    size_bytes: Any,
    sha256: Any,
    path_hint: Any,
    settings: TransferSettings,
) -> dict[str, Any]:
    sender = strict_transfer_id_part(
        sender_device_id, label="sender device ID"
    )
    target = strict_transfer_id_part(
        target_device_id, label="target device ID"
    )
    surface = strict_transfer_id_part(
        source_surface, limit=40, label="source surface"
    )
    clean_purpose = str(purpose or "cargo").strip().lower()
    clean_destination = str(destination or "catalog").strip().lower()
    clean_name = safe_attachment_name(name)
    digest = clean_sha256(sha256)
    if not isinstance(size_bytes, int) or isinstance(size_bytes, bool):
        raise TransferError("bytes must be an integer")
    size = size_bytes
    if not sender or not target:
        raise TransferError("sender and target device IDs are required")
    if not surface:
        raise TransferError("source surface is required")
    if clean_purpose not in {"cargo", "turn_context"}:
        raise TransferError("purpose must be cargo or turn_context")
    if clean_destination not in {"catalog", "named_path"}:
        raise TransferError("destination must be catalog or named_path")
    if size < 1:
        raise TransferError("file is empty")
    size_limit = (
        TURN_CONTEXT_MAX_BYTES
        if clean_purpose == "turn_context"
        else settings.max_bytes
    )
    if size > size_limit:
        raise TransferLimit("file exceeds the configured transfer limit")
    if clean_purpose == "turn_context" and clean_destination != "catalog":
        raise TransferError("turn-context attachments require the shared catalog")
    hint = _path_hint(path_hint) if clean_destination == "named_path" else ""
    if clean_destination == "named_path" and not hint:
        raise TransferError("a named destination requires path_hint")
    return {
        "sender_device_id": sender,
        "target_device_id": target,
        "source_surface": surface,
        "purpose": clean_purpose,
        "destination": clean_destination,
        "name": clean_name,
        "size_bytes": size,
        "sha256": digest,
        "path_hint": hint,
        "chunk_count": int(math.ceil(size / settings.chunk_bytes)),
    }


def transfer_id_part(value: Any, *, limit: int = 80) -> str:
    return "".join(
        ch
        for ch in str(value or "").strip()
        if ch.isascii() and (ch.isalnum() or ch in "-_.")
    )[:limit]


def strict_transfer_id_part(
    value: Any,
    *,
    limit: int = 80,
    label: str = "identifier",
    allow_empty: bool = False,
) -> str:
    raw = str(value or "").strip()
    if not raw and allow_empty:
        return ""
    clean = transfer_id_part(raw, limit=limit)
    if not raw or clean != raw:
        raise TransferError(f"{label} is invalid")
    return clean


def clean_sha256(value: Any) -> str:
    clean = str(value or "").strip().lower()
    if len(clean) != 64 or any(ch not in "0123456789abcdef" for ch in clean):
        raise TransferError("sha256 must be a 64-character lowercase hex digest")
    return clean


def _path_hint(value: Any) -> str:
    raw = str(value or "").strip()
    if (
        not raw
        or len(raw) > 512
        or any(ord(character) < 32 or ord(character) == 127 for character in raw)
    ):
        return ""
    # It becomes target-side authority only through explicit confirmation or
    # the separate auto_accept_named_paths policy.
    return raw


def named_destination_path(
    value: str | Path,
    allowed_roots: Iterable[str | Path],
) -> Path:
    """Resolve one receiver-selected path inside its current filesystem roots."""
    requested = Path(value).expanduser()
    if not requested.is_absolute():
        raise TransferError(
            "named transfer destination must be an absolute path"
        )
    destination = requested.resolve(strict=False)
    roots = [
        Path(root).expanduser().resolve(strict=False)
        for root in allowed_roots
        if str(root or "").strip()
    ]
    if roots:
        for root in roots:
            try:
                destination.relative_to(root)
                return destination
            except ValueError:
                continue
        raise TransferError(
            "named transfer destination is outside the receiver's allowed roots"
        )
    return destination
