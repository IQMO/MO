"""Cross-process serialization for one transfer operation."""
from __future__ import annotations

import hashlib
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from core.runtime.lock import file_byte_lock
from core.state.paths import resolve_state_path


_TRANSFER_OPERATION_LOCKS = tuple(threading.RLock() for _ in range(32))


@contextmanager
def transfer_operation_lock(
    config: dict[str, Any] | None,
    *,
    identity: str,
) -> Iterator[None]:
    """Serialize one stable transfer identity across threads and processes."""
    digest = hashlib.sha256(
        str(identity).encode("utf-8", errors="replace")
    ).hexdigest()
    thread_lock = _TRANSFER_OPERATION_LOCKS[
        int(digest[:8], 16) % len(_TRANSFER_OPERATION_LOCKS)
    ]
    lock_path = Path(
        resolve_state_path(
            f"run/file-transfer-operation-{digest[:16]}.lock",
            config or {},
        )
    )
    with file_byte_lock(lock_path, thread_lock):
        yield
