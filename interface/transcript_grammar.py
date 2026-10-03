"""Single source of the transcript line grammar.

Every producer routes its left edge through here so the transcript reads as one
system: your messages and MO's answers sit at the outer edge, and everything MO
*does* during a turn (tools, reasoning, notices) nests on a rail beneath the ask.
Producers pass a *kind*, never a raw marker, so gutters cannot drift apart.

Colours reuse existing skin tokens (see ``interface/theming.py``) — this owns
structure (gutter glyph + indent + turn spacing), not colour.
"""
from __future__ import annotations

Fragment = tuple[str, str]

# Gutter is 2 columns (glyph + space) so content always starts at column 2,
# the same column the wrap-continuation indent already uses.
GUTTERS: dict[str, Fragment] = {
    "user":   ("class:mo-marker", "❯ "),   # your message (heavy chevron)
    "mo":     ("class:mo-marker", "› "),   # MO's answer (light chevron, brand colour)
    "system": ("class:dim", "› "),          # slash output, queue/steer, session (dim)
    "cont":   ("", "  "),                    # continuation of the line above
}

# tool / reasoning / notice are a turn's chrome — they hang off one rail so they
# read as subordinate to the answer instead of as peer lines.
RAIL: Fragment = ("class:separator", "│ ")
_RAIL_KINDS = frozenset({"tool", "reasoning", "notice"})

# A blank line breathes before each of these so turns are visually separated.
TURN_KINDS = frozenset({"user", "mo"})


def gutter_for(kind: str) -> Fragment | None:
    """Return the gutter fragment for *kind*, or ``None`` for a full-bleed block."""
    if kind in _RAIL_KINDS:
        return RAIL
    return GUTTERS.get(kind)


def line_for(kind: str, fragments: list[Fragment]) -> list[Fragment]:
    """Prefix *fragments* with the gutter for *kind* (no-op for gutter-less kinds)."""
    gutter = gutter_for(kind)
    return ([gutter] + list(fragments)) if gutter else list(fragments)
