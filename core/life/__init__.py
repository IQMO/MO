"""Private, operator-confirmed life commitments.

Mail search remains the account owner's job; profile facts, taskboards, and
scheduled work keep their separate meanings.
"""
from __future__ import annotations

from typing import Any


def bounded_text(value: Any, *, label: str, limit: int, required: bool = False) -> str:
    """Normalize and bound one operator-supplied Life text field."""
    result = " ".join(str(value or "").split())
    if required and not result:
        raise ValueError(f"{label} is required")
    if len(result) > limit:
        raise ValueError(f"{label} is too long (maximum {limit} characters)")
    return result
