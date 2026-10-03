"""Resumable cross-surface file transfer.

The package is deliberately not imported from ``core.agent.agent``.  Surfaces
load it only when file transfer is enabled or a transfer command is used.
"""

from .model import (
    ACTIVE_TRANSFER_STATES,
    TERMINAL_TRANSFER_STATES,
    TURN_CONTEXT_MAX_BYTES,
    TransferConflict,
    TransferError,
    TransferLimit,
    TransferNotFound,
    TransferRecord,
    TransferSettings,
    safe_transfer_error,
)
from .service import HUB_TARGET_ID, TransferService
from .outbox import OutboxRecord, TransferOutbox

__all__ = [
    "ACTIVE_TRANSFER_STATES",
    "HUB_TARGET_ID",
    "OutboxRecord",
    "TERMINAL_TRANSFER_STATES",
    "TURN_CONTEXT_MAX_BYTES",
    "TransferConflict",
    "TransferError",
    "TransferLimit",
    "TransferNotFound",
    "TransferRecord",
    "TransferOutbox",
    "TransferService",
    "TransferSettings",
    "safe_transfer_error",
]
