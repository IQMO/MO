"""A passive computer-use cue, driven only by the resident cube's existing clock."""
from __future__ import annotations

import math
from typing import Any

from mo_desktop.cube_motion import _ease_out


class ComputerActivityOverlay:
    """Cached bottom glow; no input, polling, thread, or idle window of its own."""

    def __init__(self, cube: Any) -> None:
        self.cube = cube
        self._surface = None
        self._started = 0.0
        self._amount = 0.0
        self._from = 0.0
        self._target = 0.0
        self._visuals = None
        self._placement = None

    def set_active(self, active: bool, now: float) -> None:
        target = float(active)
        if target != self._target:
            self._from, self._target, self._started = self._amount, target, now

    def needs_active_frames(self, now: float) -> bool:
        return self._amount != self._target and now-self._started < .24

    def tick(self, now: float) -> None:
        import win32api
        from mo_desktop.focus_native import native_handle
        from mo_desktop.layered import NativeLayeredWindow
        from interface.desktop_ui import active_desktop_visual_state

        phase = min(1.0, max(0.0, (now-self._started)/.24))
        self._amount = self._from+(self._target-self._from)*_ease_out(phase)
        if not self._amount and not self._target:
            self.destroy()
            return
        monitor = win32api.GetMonitorInfo(win32api.MonitorFromWindow(native_handle(self.cube._win), 2))["Monitor"]
        left, top, right, bottom = monitor
        placement = ((left+right)//2-220, bottom-56)
        visuals = active_desktop_visual_state()
        if self._surface is None:
            self._surface = NativeLayeredWindow(title="MO Desktop — Computer activity")
            self._surface.exclude_from_capture(True)
        if placement != self._placement or visuals != self._visuals:
            self._surface.blit(self._paint(visuals), *placement, premultiplied=True,
                               opacity=round(self._amount*255))
            self._placement, self._visuals = placement, visuals
        # Alpha-only updates reuse the native bitmap; the glow never redraws at 60 Hz.
        self._surface.set_opacity(round(self._amount*(.90+.10*math.sin(now*2))*255))

    @staticmethod
    def _paint(visuals: Any) -> Any:
        from PIL import Image, ImageColor, ImageDraw, ImageFilter
        from mo_desktop import card
        from mo_desktop.fonts import load_font
        from interface.desktop_ui import DESKTOP_TYPOGRAPHY

        ss = card.SS
        image = card.new_canvas(440*ss, 56*ss)
        glow = Image.new("RGBA", image.size)
        color = ImageColor.getrgb(visuals.palette.accent)
        draw = ImageDraw.Draw(glow)
        draw.ellipse((68*ss, 40*ss, 372*ss, 70*ss), fill=(*color, 135))
        image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(10*ss)))
        draw = ImageDraw.Draw(image)
        radius = visuals.metrics.panel_corner_radius*ss
        draw.rounded_rectangle((103*ss, 7*ss, 337*ss, 38*ss), radius=radius,
            fill=(*ImageColor.getrgb(visuals.palette.card), 245),
            outline=(*color, 65), width=ss)
        draw.ellipse((117*ss, 20*ss, 122*ss, 25*ss), fill=(*color, 255))
        font = load_font(("segoeui.ttf", "arial.ttf"), round(DESKTOP_TYPOGRAPHY.small[1]*4/3*ss))
        draw.text((229*ss, 23*ss), "MO is using your computer", fill=visuals.palette.text, font=font, anchor="mm")
        return card.finish(image)

    def destroy(self) -> None:
        if self._surface is not None:
            self._surface.destroy()
            self._surface = None
        self._placement = self._visuals = None
