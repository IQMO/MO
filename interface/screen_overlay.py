"""On-screen MO companion overlay — a small labelled bubble at a screen point.

Draws a compact MO-branded bubble (cyan ``#00cccc`` glyph + label on a dark
card with a cyan border) anchored next to a screen coordinate, then auto-closes.
Paired with moving the real cursor to the point, this is MO's "pointing here"
(Guided mode). Deliberately a small always-on-top window with simple alpha — NOT
a fullscreen color-keyed overlay, which fights the Windows compositor and renders
black. Runs as its own short-lived process so the GUI never shares a thread with
the prompt_toolkit TUI:

    python -m interface.screen_overlay <x> <y> <label> <seconds>
"""
from __future__ import annotations

import sys

from interface.theming import skin_overlay_vars as _resolve_overlay_colors
_oc = _resolve_overlay_colors()
CYAN: str = _oc["CYAN"]
CARD: str = _oc["CARD"]
TEXT: str = _oc["TEXT"]
GLYPH = "◐"  # ◐ half-moon = MO mark


def show_pointer(x: int, y: int, label: str, seconds: float = 4.0) -> None:
    import tkinter as tk

    root = tk.Tk()
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

    # cyan border = outer frame; dark card = inner frame
    border = tk.Frame(root, bg=CYAN)
    border.pack()
    card = tk.Frame(border, bg=CARD)
    card.pack(padx=2, pady=2)
    tk.Label(card, text=GLYPH, fg=CYAN, bg=CARD, font=("Segoe UI", 16, "bold")).pack(side="left", padx=(10, 4), pady=8)
    tk.Label(card, text=text, fg=TEXT, bg=CARD, font=("Segoe UI", 11)).pack(side="left", padx=(2, 12), pady=8)

    root.update_idletasks()
    bw, bh = root.winfo_width(), root.winfo_height()
    # place the bubble next to the point without covering it; clamp on-screen
    px = int(x) + 24
    py = int(y) + 18
    if px + bw > sw:
        px = int(x) - bw - 24
    if py + bh > sh:
        py = int(y) - bh - 18
    px = max(0, min(px, sw - bw))
    py = max(0, min(py, sh - bh))
    root.geometry(f"+{px}+{py}")

    root.after(int(max(0.5, seconds) * 1000), root.destroy)
    root.mainloop()


if __name__ == "__main__":
    _x = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    _y = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    _label = sys.argv[3] if len(sys.argv) > 3 else "here"
    _secs = float(sys.argv[4]) if len(sys.argv) > 4 else 4.0
    show_pointer(_x, _y, _label, _secs)
