"""MO Desktop — Windows system-volume read/set (Core Audio via pycaw).

Lazy + graceful: the pycaw/comtypes import happens on first use and the whole
module no-ops (returns None) when unavailable, so the companion degrades cleanly
on non-Windows or without the optional dep. The endpoint is cached after the first
successful lookup. Call from the GUI thread (COM apartment) — the cube's scroll /
hover handlers do.
"""
from __future__ import annotations

from typing import Any, Optional

_ENDPOINT: Any = None
_TRIED = False


def available() -> bool:
    return _endpoint() is not None


def _endpoint() -> Any:
    global _ENDPOINT, _TRIED
    if _ENDPOINT is not None:
        return _ENDPOINT
    if _TRIED:
        return None
    _TRIED = True
    try:
        from pycaw.pycaw import AudioUtilities
        _ENDPOINT = AudioUtilities.GetSpeakers().EndpointVolume
    except Exception:
        _ENDPOINT = None
    return _ENDPOINT


def get_volume() -> Optional[float]:
    """Master volume as 0..1, or None if unavailable."""
    ev = _endpoint()
    if ev is None:
        return None
    try:
        return float(ev.GetMasterVolumeLevelScalar())
    except Exception:
        return None


def set_volume(scalar: float) -> Optional[float]:
    """Set master volume (clamped 0..1); return the value set, or None."""
    ev = _endpoint()
    if ev is None:
        return None
    try:
        s = max(0.0, min(1.0, float(scalar)))
        ev.SetMasterVolumeLevelScalar(s, None)
        return s
    except Exception:
        return None


def nudge_volume(delta: float) -> Optional[float]:
    """Change master volume by ``delta`` (e.g. +/-0.04 per scroll notch); return new value."""
    cur = get_volume()
    if cur is None:
        return None
    return set_volume(cur + delta)
