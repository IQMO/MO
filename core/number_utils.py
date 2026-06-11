"""Small numeric coercion helpers shared by reporting/context modules."""

from __future__ import annotations

from typing import Any


def as_int(value: Any, default: int = 0) -> int:
    """Return ``value`` as ``int`` or ``default`` on blank/invalid input.

    This preserves the local helper semantics used by MO reporting code:
    ``None``, empty strings, and invalid values fall back to ``default``;
    valid falsey numerics such as ``0`` and ``False`` remain ``0``.
    """
    try:
        if value is None or value == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def as_non_negative_int(value: Any, default: int = 0) -> int:
    """Return ``value`` as an int clamped to zero or above."""
    return max(0, as_int(value, default))


def as_optional_int(value: Any) -> int | None:
    """Return ``value`` as ``int``; return ``None`` for blank/invalid input."""
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None
