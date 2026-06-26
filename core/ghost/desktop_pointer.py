"""Process-wide handle to the live desktop Ghost orb pointer.

When the desktop Ghost runs with its on-screen moon orb, that orb becomes MO's
pointer: ``point_on_screen`` drives the animated moon to the target instead of
spawning the one-shot overlay bubble — so MO points with its own moon, not the
bare Windows cursor (the clicky-style flying pointer).

This tiny registry lets the actuation tool (core/tools layer) reach the orb
(interface layer) without a hard import edge. It is ``None`` whenever the desktop
orb is not live, and callers then fall back to the subprocess overlay bubble.
"""
from __future__ import annotations

from typing import Callable

# Signature: (x, y, label, seconds) -> handled?
_POINTER: Callable[[int, int, str, float], bool] | None = None


def set_desktop_pointer(fn: Callable[[int, int, str, float], bool] | None) -> None:
    """Register (or clear, with ``None``) the live desktop orb pointer."""
    global _POINTER
    _POINTER = fn


def point_with_desktop_orb(x: int, y: int, label: str = "here", seconds: float = 4.0) -> bool:
    """Drive the live orb to ``(x, y)``. Returns True only if an orb handled it."""
    fn = _POINTER
    if fn is None:
        return False
    try:
        return bool(fn(int(x), int(y), str(label or "here"), float(seconds or 4.0)))
    except Exception:
        return False
