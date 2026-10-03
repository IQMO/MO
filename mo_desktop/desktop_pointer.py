"""Process-wide handles to the live MO Desktop companion.

When MO Desktop runs with its on-screen cube companion, that companion becomes
MO's pointer: ``point_on_screen`` drives the animated cube to the target instead
of spawning the one-shot overlay bubble.

This tiny registry lets the actuation tool reach the live companion without
importing the companion GUI. It is ``None`` whenever the desktop pointer is not
live, and callers then fall back to the subprocess overlay bubble.
"""
from __future__ import annotations

from typing import Callable

# Signature: (x, y, label, seconds) -> handled?
_POINTER: Callable[[int, int, str, float], bool] | None = None


def set_desktop_pointer(fn: Callable[[int, int, str, float], bool] | None) -> None:
    """Register (or clear, with ``None``) the live desktop pointer."""
    global _POINTER
    _POINTER = fn


def point_with_desktop_cube(x: int, y: int, label: str = "here", seconds: float = 4.0) -> bool:
    """Drive the live desktop pointer (the cube companion) to ``(x, y)``."""
    fn = _POINTER
    if fn is None:
        return False
    try:
        return bool(fn(int(x), int(y), str(label or "here"), float(seconds or 4.0)))
    except Exception:
        return False


# Signature: () -> a short line describing what MO terminal is working on, or "" when there is none.
_SYNC: Callable[[], str] | None = None


def set_desktop_sync(fn: Callable[[], str] | None) -> None:
    """Register (or clear, with ``None``) the live desktop's terminal-sync handoff."""
    global _SYNC
    _SYNC = fn


def sync_with_desktop() -> str | None:
    """Run the desktop's lock -> transfer -> announce handoff. None when MO Desktop is not live."""
    fn = _SYNC
    if fn is None:
        return None
    try:
        return str(fn() or "")
    except Exception:
        return None
