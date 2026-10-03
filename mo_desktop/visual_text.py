"""Pure text helpers shared by MO Desktop's PIL renderers.

This module deliberately has no Tk, PIL, or surface imports.  The cube label and
the interactive panel both need the same conservative inline-emphasis handling
and optional RTL shaping; neither renderer should depend on the other's private
methods to get it.
"""
from __future__ import annotations


def shape_line(text: str) -> tuple[str, bool]:
    """Return a PIL-ready line and whether RTL shaping was applied.

    Arabic/Hebrew remains unchanged when the optional shaping dependencies are
    unavailable.  That is preferable to making the Desktop import depend on
    optional typography packages.
    """
    value = str(text or "")
    if not any("\u0590" <= char <= "\u08ff" for char in value):
        return value, False
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        return get_display(arabic_reshaper.reshape(value)), True
    except Exception:
        return value, False


def parse_inline_emphasis(text: str) -> list[tuple[str, bool]]:
    """Parse only matched ``**bold**`` and ``__bold__`` spans.

    Unmatched markers remain literal so Desktop never silently deletes user or
    provider text while turning a reply into visual spans.
    """
    raw = str(text or "")
    spans: list[tuple[str, bool]] = []
    index = 0
    while index < len(raw):
        marker = "**" if raw.startswith("**", index) else "__" if raw.startswith("__", index) else None
        if marker:
            end = raw.find(marker, index + 2)
            if end > index + 2:
                spans.append((raw[index + 2:end], True))
                index = end + 2
                continue
        positions = [
            position
            for position in (raw.find("**", index + 1), raw.find("__", index + 1))
            if position != -1
        ]
        end = min(positions) if positions else len(raw)
        spans.append((raw[index:end], False))
        index = end
    return spans or [("", False)]


def plain_spans(spans: list[tuple[str, bool]]) -> str:
    """Return the literal text represented by parsed emphasis spans."""
    return "".join(text for text, _bold in spans)
