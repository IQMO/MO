"""Terminal colour effects for MO activity and trigger words."""
from __future__ import annotations

import math
import re


def _pulse_glow(timestamp: float, base: tuple[int, int, int]) -> str:
    """Pulse a base foreground colour through its green channel."""
    r_base, g_base, b_base = base
    green = max(0, min(255, int(g_base + 40 * math.sin(timestamp * 2.0))))
    return f"fg:#{r_base:02x}{green:02x}{b_base:02x} bold"


def calculate_method_glow(timestamp: float) -> str:
    """Return the active skin's warm pulse for distinctive MO methods."""
    from interface.theming import get_method_glow_base

    return _pulse_glow(timestamp, get_method_glow_base())


# White gradient for committed and composer ``extrathink``/``mapthis`` text.
# Every helper preserves the exact glyphs and width of the original word.
_SHINE_SPECTRUM = [
    (0x8a, 0x92, 0xa0),
    (0xc4, 0xcc, 0xd6),
    (0xf4, 0xf7, 0xfb),
    (0xff, 0xff, 0xff),
    (0xf4, 0xf7, 0xfb),
    (0xc4, 0xcc, 0xd6),
]

_SHINE_TRIGGER_RE = re.compile(r"\b(?:extrathink|mapthis)\b", re.IGNORECASE)


def has_shine_trigger(text: str) -> bool:
    """Return whether text contains a word that receives the white shine."""
    return bool(_SHINE_TRIGGER_RE.search(str(text or "")))


def shine_trigger_matches(text: str):
    """Return regex matches for white-shine trigger words."""
    return _SHINE_TRIGGER_RE.finditer(str(text or ""))


def _lerp(a: int, b: int, amount: float) -> int:
    return max(0, min(255, int(a + (b - a) * amount)))


def gradient_fragments(word: str) -> list[tuple[str, str]]:
    """Return a static per-character white gradient without changing width."""
    size = len(word)
    if size == 0:
        return []
    fragments: list[tuple[str, str]] = []
    last = len(_SHINE_SPECTRUM) - 1
    for index, char in enumerate(word):
        position = (index / (size - 1) if size > 1 else 0.0) * last
        lower = int(position)
        upper = min(lower + 1, last)
        amount = position - lower
        red = _lerp(_SHINE_SPECTRUM[lower][0], _SHINE_SPECTRUM[upper][0], amount)
        green = _lerp(_SHINE_SPECTRUM[lower][1], _SHINE_SPECTRUM[upper][1], amount)
        blue = _lerp(_SHINE_SPECTRUM[lower][2], _SHINE_SPECTRUM[upper][2], amount)
        fragments.append((f"bold fg:#{red:02x}{green:02x}{blue:02x}", char))
    return fragments


def gradient_line(text: str, base_style: str) -> list[tuple[str, str]]:
    """Apply the static gradient to trigger words inside one styled line."""
    fragments: list[tuple[str, str]] = []
    last = 0
    for match in shine_trigger_matches(text):
        if match.start() > last:
            fragments.append((base_style, text[last:match.start()]))
        fragments.extend(gradient_fragments(text[match.start():match.end()]))
        last = match.end()
    if last < len(text):
        fragments.append((base_style, text[last:]))
    return fragments or [(base_style, text)]
