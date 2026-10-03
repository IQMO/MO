"""Environment parsing helpers shared by runtime modules."""

from __future__ import annotations

import os


def int_env(name: str, default: int) -> int:
    """Return an integer environment variable, falling back to default.

    Preserves the existing MO helper semantics used by pruning/cap code:
    unset, empty, or invalid values return ``default``; valid strings,
    including ``0`` or negative values, are returned as integers so callers
    can decide whether and how to clamp them.
    """
    try:
        return int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default
