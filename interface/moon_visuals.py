"""Mathematical animations for the Moon Mode MO logo."""
from __future__ import annotations

import math
import re
import time
import threading
from typing import Any

def calculate_moon_glow(timestamp: float) -> str:
    """
    Generate an animated foreground color style for the MO logo.
    Cycles between the skin's base glow and a brighter pulse.
    """
    from interface.theming import get_moon_glow_base
    r_base, g_base, b_base = get_moon_glow_base()
    speed = 2.0

    # Pulse the green channel to go between base and brighter
    g_val = g_base + 40 * math.sin(timestamp * speed)
    g = max(0, min(255, int(g_val)))

    hex_color = f"#{r_base:02x}{g:02x}{b_base:02x}"
    # Return as foreground color and bold
    return f"fg:{hex_color} bold"


# ── extrathink shine ────────────────────────────────────────────────────────
# Vivid spectrum for the static in-history gradient. Colour only — every helper
# below keeps the exact glyphs/width of the word (one fragment per character, no
# inserted spacing, no bold toggling that could reflow width).
_SHINE_SPECTRUM = [
    (0x8b, 0xe9, 0xfd),  # cyan
    (0x50, 0xfa, 0x7b),  # green
    (0xf1, 0xfa, 0x8c),  # yellow
    (0xff, 0xb8, 0x6c),  # orange
    (0xff, 0x79, 0xc6),  # pink
    (0xbd, 0x93, 0xf9),  # purple
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
    from interface.theming import get_moon_glow_base
    br, bg, bb = get_moon_glow_base()
    speed = 3.0
    span = n + 2.0
    centre = (timestamp * speed) % span - 1.0  # sweeps in and out smoothly
    out: list[tuple[str, str]] = []
    for i, ch in enumerate(word):
        d = i - centre
        intensity = math.exp(-(d * d) / (2 * 1.4 * 1.4))  # gaussian bump ~1.4 wide
        r = _lerp(br, 255, intensity)
        g = _lerp(bg, 255, intensity)
        b = _lerp(bb, 255, intensity)
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
            time.sleep(0.1)  # ~10 FPS for smooth text glow
            if getattr(agent, "_moon_mode_active", False):
                if app:
                    app.invalidate()

    t = threading.Thread(target=tick, daemon=True, name="mo-moon-tick")
    t.start()
    return stop
