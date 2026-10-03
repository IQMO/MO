"""Small multi-label desktop annotation overlay.

Avoids a full-screen transparent window on Windows. Each label is its own small
topmost card near the UIA element bounds, so DWM compositing stays predictable.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from interface.desktop_ui import DESKTOP_SPACING as SPACING


def show_annotations(items: list[dict[str, Any]], seconds: float = 5.0) -> None:
    import tkinter as tk
    from interface.desktop_label import build_desktop_label_card
    from interface.desktop_widgets import reveal_desktop_window
    from mo_desktop.visuals import load_and_publish_desktop_visual_state

    visuals = load_and_publish_desktop_visual_state(None, refresh_skin=True)

    root = tk.Tk()
    root.withdraw()
    wins: list[Any] = []
    screen_w = root.winfo_screenwidth()
    screen_h = root.winfo_screenheight()
    for item in items[:12]:
        try:
            left, top, _right, bottom = [int(v) for v in item.get("bounds", [0, 0, 0, 0])[:4]]
        except Exception:
            left = top = bottom = 0
        label = f"{item.get('ref', '?')}  {item.get('label') or item.get('role') or 'element'}"
        if len(label) > 70:
            label = label[:67] + "..."
        win = tk.Toplevel(root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.94)
        except Exception:
            pass
        build_desktop_label_card(win, label, visuals, strong=True)
        win.update_idletasks()
        width, height = win.winfo_width(), win.winfo_height()
        x = max(0, min(left, max(0, screen_w - width)))
        y = max(
            0,
            min(
                top - height - SPACING.item if top - height > 0 else bottom + SPACING.item,
                max(0, screen_h - height),
            ),
        )
        win.geometry(f"+{x}+{y}")
        reveal_desktop_window(win)
        _make_click_through(win)
        wins.append(win)
    root.after(int(max(0.5, seconds) * 1000), root.destroy)
    root.mainloop()


def _make_click_through(win: Any) -> None:
    """An annotation card only shows a label; the mouse must reach what it points at."""
    try:
        from core.desktop.win32 import apply_layered_exstyle

        apply_layered_exstyle(int(win.winfo_id()), click_through=True)
    except Exception:
        pass


if __name__ == "__main__":
    raw = sys.argv[1] if len(sys.argv) > 1 else "[]"
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    try:
        data = json.loads(raw)
    except Exception:
        data = []
    show_annotations(data if isinstance(data, list) else [], secs)
