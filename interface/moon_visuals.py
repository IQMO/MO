"""Mathematical animations for the Moon Mode MO logo."""
from __future__ import annotations

import math
import re
import time
import threading
from typing import Any

# MO's "own-method" signal colour: gold. When MO runs its own features
# (extrathink, owner protocols) the activity lane + footer "MO" glow gold.
GOLD_RGB = (255, 180, 0)


def _pulse_glow(timestamp: float, base: tuple[int, int, int]) -> str:
    """Animated fg style: pulse a base colour brighter/back via its green channel."""
    r_base, g_base, b_base = base
    g = max(0, min(255, int(g_base + 40 * math.sin(timestamp * 2.0))))
    return f"fg:#{r_base:02x}{g:02x}{b_base:02x} bold"


def calculate_moon_glow(timestamp: float) -> str:
    """Animated fg for the MO logo — cycles the skin's base glow brighter."""
    from interface.theming import get_moon_glow_base
    return _pulse_glow(timestamp, get_moon_glow_base())


def calculate_gold_glow(timestamp: float) -> str:
    """Animated gold fg for the activity lane / footer while MO uses its own methods."""
    return _pulse_glow(timestamp, GOLD_RGB)


# ── extrathink shine ────────────────────────────────────────────────────────
# Vivid spectrum for the static in-history gradient. Colour only — every helper
# below keeps the exact glyphs/width of the word (one fragment per character, no
# inserted spacing, no bold toggling that could reflow width).
_SHINE_SPECTRUM = [
    (0xb8, 0x86, 0x0b),  # dark goldenrod
    (0xda, 0xa5, 0x20),  # goldenrod
    (0xff, 0xd7, 0x00),  # gold
    (0xff, 0xec, 0x8b),  # light gold
    (0xff, 0xd7, 0x00),  # gold
    (0xda, 0xa5, 0x20),  # goldenrod
]

_EXTRATHINK_RE = re.compile(r"\bextrathink\b", re.IGNORECASE)


def _lerp(a: int, b: int, t: float) -> int:
    return max(0, min(255, int(a + (b - a) * t)))


def gradient_fragments(word: str) -> list[tuple[str, str]]:
    """Static per-character gradient across the spectrum (no motion).

    Colour only: one ``fg:#rrggbb`` fragment per original character — same glyphs,
    same width, no spacing, no bold. Used for the committed (scrollback) copies.
    """
    n = len(word)
    if n == 0:
        return []
    out: list[tuple[str, str]] = []
    last = len(_SHINE_SPECTRUM) - 1
    for i, ch in enumerate(word):
        pos = (i / (n - 1) if n > 1 else 0.0) * last
        lo = int(pos)
        hi = min(lo + 1, last)
        t = pos - lo
        r = _lerp(_SHINE_SPECTRUM[lo][0], _SHINE_SPECTRUM[hi][0], t)
        g = _lerp(_SHINE_SPECTRUM[lo][1], _SHINE_SPECTRUM[hi][1], t)
        b = _lerp(_SHINE_SPECTRUM[lo][2], _SHINE_SPECTRUM[hi][2], t)
        out.append((f"fg:#{r:02x}{g:02x}{b:02x}", ch))
    return out


def shine_fragments(word: str, timestamp: float) -> list[tuple[str, str]]:
    """Per-frame traveling highlight: a bright spot sweeps L→R over a dim base.

    Colour/brightness only — constant weight, one fragment per character, no
    spacing change. Drives the live shimmer (typing + end-of-turn banner).
    """
    n = len(word)
    if n == 0:
        return []
    # Gold shimmer: deep gold base, peak to a bright pale gold (never pure white,
    # so it reads as gold throughout — MO's own-method colour).
    br, bg, bb = (170, 120, 10)
    pr, pg, pb = (255, 240, 150)
    speed = 3.0
    span = n + 2.0
    centre = (timestamp * speed) % span - 1.0  # sweeps in and out smoothly
    out: list[tuple[str, str]] = []
    for i, ch in enumerate(word):
        d = i - centre
        intensity = math.exp(-(d * d) / (2 * 1.4 * 1.4))  # gaussian bump ~1.4 wide
        r = _lerp(br, pr, intensity)
        g = _lerp(bg, pg, intensity)
        b = _lerp(bb, pb, intensity)
        out.append((f"fg:#{r:02x}{g:02x}{b:02x}", ch))
    return out


def gradient_line(text: str, base_style: str) -> list[tuple[str, str]]:
    """Style ``text`` with ``base_style``, replacing each ``extrathink`` run with a
    static gradient. Returns one fragment list suitable for ``_add_fragments_line``."""
    frags: list[tuple[str, str]] = []
    last = 0
    for m in _EXTRATHINK_RE.finditer(text):
        if m.start() > last:
            frags.append((base_style, text[last:m.start()]))
        frags.extend(gradient_fragments(text[m.start():m.end()]))
        last = m.end()
    if last < len(text):
        frags.append((base_style, text[last:]))
    return frags or [(base_style, text)]


def start_moon_animation_tick(agent: Any, app: Any):
    """
    Start a background thread that slowly invalidates the UI to animate the logo
    when moon mode is enabled.
    """
    stop = threading.Event()

    def tick():
        while not stop.is_set():
            time.sleep(0.016)  # ~60 FPS for smooth text glow
            if getattr(agent, "_moon_mode_active", False):
                if app:
                    app.invalidate()

    t = threading.Thread(target=tick, daemon=True, name="mo-moon-tick")
    t.start()
    return stop
