"""One font cache for every MO Desktop surface.

The cube loaded its label font with ``ImageFont.truetype(...)`` on every label render, uncached,
while the reply panel cached per supersample. Sharing the panel's *loader* would have silently
changed the cube's typeface, because the panel prefers Segoe UI Semibold and the cube uses regular
Segoe UI. So the cache is shared and the preference list stays the caller's.

Import stays light: PIL is resolved on first use.
"""
from __future__ import annotations

from typing import Any

_CACHE: dict[tuple[tuple[str, ...], int], Any] = {}


def load_font(names: tuple[str, ...], size: int) -> Any:
    """First font in ``names`` that loads at ``size``, cached. PIL's default if none do."""
    key = (tuple(names), int(size))
    font = _CACHE.get(key)
    if font is not None:
        return font

    from PIL import ImageFont

    for name in names:
        try:
            font = ImageFont.truetype(name, int(size))
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    _CACHE[key] = font
    return font


def clear_cache() -> None:
    """Drop every cached face. For tests and for a font-set change at runtime."""
    _CACHE.clear()
