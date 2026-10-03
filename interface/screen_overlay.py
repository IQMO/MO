"""On-screen MO overlay — a small labelled bubble at a screen point.

Draws a compact MO-branded bubble using the active skin's brand glyph, card, and
border colours, anchored next to a screen coordinate, then auto-closes.
Paired with moving the real cursor to the point, this is MO's "pointing here"
(Guided mode). Deliberately a small always-on-top window with simple alpha — NOT
a fullscreen color-keyed overlay, which fights the Windows compositor and renders
black. Runs as its own short-lived process so the GUI never shares a thread with
the prompt_toolkit TUI:

    python -m interface.screen_overlay <x> <y> <label> <seconds>
"""
from __future__ import annotations

import sys

from interface.desktop_ui import DESKTOP_SPACING as SPACING


def show_pointer(x: int, y: int, label: str, seconds: float = 4.0) -> None:
    import tkinter as tk
    from interface.desktop_label import build_desktop_label_card
    from interface.desktop_widgets import reveal_desktop_window
    from mo_desktop.visuals import load_and_publish_desktop_visual_state

    visuals = load_and_publish_desktop_visual_state(None, refresh_skin=True)

    root = tk.Tk()
    root.withdraw()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    try:
        root.attributes("-alpha", 0.93)
    except Exception:
        pass

    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    text = (label or "").strip() or "here"
    if len(text) > 90:
        text = text[:87] + "..."

    brand_photo = build_desktop_label_card(root, text, visuals, brand=True)
    root._mo_brand_photo = brand_photo  # keep the Tk image alive for the overlay lifetime

    root.update_idletasks()
    bw, bh = root.winfo_width(), root.winfo_height()
    # place the bubble next to the point without covering it; clamp on-screen
    px = int(x) + SPACING.page_wide
    py = int(y) + SPACING.expanded
    if px + bw > sw:
        px = int(x) - bw - SPACING.page_wide
    if py + bh > sh:
        py = int(y) - bh - SPACING.expanded
    px = max(0, min(px, sw - bw))
    py = max(0, min(py, sh - bh))
    root.geometry(f"+{px}+{py}")
    reveal_desktop_window(root)

    root.after(int(max(0.5, seconds) * 1000), root.destroy)
    root.mainloop()


if __name__ == "__main__":
    _x = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    _y = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    _label = sys.argv[3] if len(sys.argv) > 3 else "here"
    _secs = float(sys.argv[4]) if len(sys.argv) > 4 else 4.0
    show_pointer(_x, _y, _label, _secs)
