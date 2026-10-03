"""Canonical private-state locations shared by transfer stores."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.state.paths import (
    FILE_TRANSFER_DB_PATH,
    FILE_TRANSFER_SPOOL_DIR,
    resolve_state_path,
)


def resolve_transfer_storage(
    config: dict[str, Any],
    *,
    path: str | Path | None = None,
    spool_root: str | Path | None = None,
) -> tuple[Path, Path]:
    """Resolve the shared transfer database and base spool without creating them."""
    database = Path(
        path or resolve_state_path(FILE_TRANSFER_DB_PATH, config)
    ).expanduser().resolve(strict=False)
    spool = Path(
        spool_root or resolve_state_path(FILE_TRANSFER_SPOOL_DIR, config)
    ).expanduser().resolve(strict=False)
    return database, spool
