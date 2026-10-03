"""On-screen MO overlay — a small labelled bubble at a screen point.

Draws a compact MO-branded bubble using the active skin's brand glyph, card, and
border colours, anchored next to a screen coordinate, then auto-closes.
Paired with moving the real cursor to the point, this is MO's "pointing here"
(Guided mode). Deliberately a small always-on-top window with per-pixel alpha — NOT
a fullscreen color-keyed overlay, which fights the Windows compositor and renders
black. Runs as its own short-lived process so the GUI never shares a thread with
the prompt_toolkit TUI:

    python -m interface.screen_overlay <x> <y> <label> <seconds>
"""
from __future__ import annotations

import sys

def show_pointer(x: int, y: int, label: str, seconds: float = 4.0) -> None:
    from interface.desktop_label import (
        label_position, label_work_area, render_desktop_label_card, show_desktop_labels,
    )
    from mo_desktop.visuals import load_and_publish_desktop_visual_state

    visuals = load_and_publish_desktop_visual_state(None, refresh_skin=True)
    text = (label or "").strip() or "here"
    if len(text) > 90:
        text = text[:87] + "..."
    x, y = int(x), int(y)
    work = label_work_area(x, y)
    image = render_desktop_label_card(text, visuals, brand=True, max_width=min(480, work[2]-work[0]))
    px, py = label_position((x, y, x, y), image.size, work, pointer=True)
    show_desktop_labels([(image, px, py)], seconds, visuals)


if __name__ == "__main__":
    _x = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    _y = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    _label = sys.argv[3] if len(sys.argv) > 3 else "here"
    _secs = float(sys.argv[4]) if len(sys.argv) > 4 else 4.0
    show_pointer(_x, _y, _label, _secs)
