"""Shared Tk label-card renderer for Desktop pointer and annotations."""
from __future__ import annotations

from typing import Any

from interface.desktop_ui import DESKTOP_TYPOGRAPHY as TYPOGRAPHY, DesktopVisualState
from interface.desktop_widgets import install_desktop_window_corners


def build_desktop_label_card(
    window: Any,
    text: str,
    visuals: DesktopVisualState,
    *,
    brand: bool = False,
    strong: bool = False,
) -> Any:
    """Build one compact label through the exact shared palette and geometry."""
    import tkinter as tk

    if not isinstance(visuals, DesktopVisualState):
        raise TypeError("Desktop label card requires DesktopVisualState")
    palette = visuals.palette
    padding = int(visuals.metrics.panel_padding)
    window.configure(bg=palette.border)
    shell = tk.Frame(window, bg=palette.border)
    shell.pack()
    content = tk.Frame(shell, bg=palette.card)
    content.pack(padx=1, pady=1)
    photo = None
    if brand:
        from PIL import ImageTk
        from interface.desktop_brand import make_four_cube_icon

        photo = ImageTk.PhotoImage(
            make_four_cube_icon(28, palette=palette),
            master=window,
        )
        tk.Label(content, image=photo, bg=palette.card, bd=0).pack(
            side="left", padx=(padding, max(2, padding // 2)), pady=padding,
        )
    tk.Label(
        content,
        text=str(text or ""),
        fg=palette.text,
        bg=palette.card,
        font=TYPOGRAPHY.body_weight(True) if strong else TYPOGRAPHY.callout,
    ).pack(
        side="left",
        padx=((padding if not brand else 0), padding),
        pady=padding,
    )
    install_desktop_window_corners(window)
    return photo


__all__ = ["build_desktop_label_card"]
