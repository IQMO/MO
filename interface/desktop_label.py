"""Shared, passive native cards for Desktop pointing and UIA annotations."""
from __future__ import annotations

from typing import Any

from interface.desktop_ui import DESKTOP_SPACING as SPACING, DesktopVisualState


def render_desktop_label_card(
    text: str, visuals: DesktopVisualState, *, brand: bool = False,
    strong: bool = False, max_width: int = 480,
) -> Any:
    """Render through Desktop's shared card, typography and premultiplied edges."""
    from PIL import ImageDraw
    from mo_desktop import card

    if not isinstance(visuals, DesktopVisualState):
        raise TypeError("Desktop label card requires DesktopVisualState")
    ss = card.SS
    padding = visuals.metrics.panel_padding
    mark = 28 if brand else 0
    gap = max(2, padding // 2) if brand else 0
    font = card.role_font("section" if strong else "callout")
    measure = ImageDraw.Draw(card.new_canvas(1, 1))
    available = max(1, max_width - padding * 2 - mark - gap)
    text = card.fit_text(measure, text, available * ss, font)
    bounds = measure.textbbox((0, 0), text, font=font)
    text_height = (bounds[3] - bounds[1] + ss - 1) // ss
    width = min(max_width, round(measure.textlength(text, font=font) / ss)
                + padding * 2 + mark + gap)
    height = max(mark, text_height) + padding * 2
    image = card.surface_canvas((width, height), visuals)
    if brand:
        from interface.desktop_brand import make_four_cube_icon

        image.alpha_composite(make_four_cube_icon(mark * ss, palette=visuals.palette),
                              (padding * ss, (height - mark) * ss // 2))
    ImageDraw.Draw(image).text(
        ((padding + mark + gap) * ss, (height * ss - bounds[3] + bounds[1]) // 2 - bounds[1]),
        text, font=font, fill=visuals.palette.text,
    )
    return card.finish(image)


def label_work_area(x: int, y: int) -> tuple[int, int, int, int]:
    """Use the target monitor, including monitors left of the primary screen."""
    import win32api

    return tuple(win32api.GetMonitorInfo(win32api.MonitorFromPoint((x, y), 2))["Work"])


def label_position(
    bounds: tuple[int, int, int, int], size: tuple[int, int],
    work_area: tuple[int, int, int, int], *, pointer: bool = False,
) -> tuple[int, int]:
    left, top, right, bottom = work_area
    x, y, _, anchor_bottom = bounds
    width, height = size
    if pointer:
        px, py = x + SPACING.page_wide, y + SPACING.expanded
        if px + width > right:
            px = x - width - SPACING.page_wide
        if py + height > bottom:
            py = y - height - SPACING.expanded
    else:
        px, py = x, y - height - SPACING.item
        if py < top:
            py = anchor_bottom + SPACING.item
    return (max(left, min(px, right - width)), max(top, min(py, bottom - height)))


def show_desktop_labels(
    labels: list[tuple[Any, int, int]], seconds: float, visuals: DesktopVisualState,
) -> None:
    """Paint once, wait for messages or expiry, and release every owned HWND."""
    import math
    import time
    import win32event
    import win32con
    from PIL import Image
    from interface.desktop_widgets import render_desktop_window_effect
    from mo_desktop.layered import NativeLayeredWindow, pump_native_window_messages

    if not labels:
        return
    windows = []
    try:
        for image, x, y in labels:
            if visuals.effects.style != "none" and visuals.effects.intensity > 0:
                effect, pad = render_desktop_window_effect(
                    *image.size, visuals.metrics.panel_corner_radius,
                    visuals.effects, visuals.token("_GLOW"),
                )
                content = Image.frombytes("RGBa", image.size, image.tobytes()).convert("RGBA")
                effect.alpha_composite(content, (pad, pad))
                image = Image.frombytes("RGBA", effect.size, effect.convert("RGBa").tobytes())
                x, y = x - pad, y - pad
            window = NativeLayeredWindow(activate=False, title="MO Desktop label")
            windows.append(window)
            if not window.blit(image, x, y, premultiplied=True):
                raise RuntimeError(window.failure_detail())
        deadline = time.monotonic() + max(0.5, seconds)
        while True:
            pump_native_window_messages()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            win32event.MsgWaitForMultipleObjects((), False, math.ceil(remaining * 1000),
                                                win32con.QS_ALLINPUT)
    finally:
        for window in reversed(windows):
            window.destroy()


__all__ = ["render_desktop_label_card", "label_work_area", "label_position", "show_desktop_labels"]
