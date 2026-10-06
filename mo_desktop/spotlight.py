"""MO's pointing spotlight: the target outlined, the rest of its screen dimmed, and a small
zoom beside a tiny control. One passive window: click-through, kept out of captures, above
other apps and below MO's own cubes and label (row 34, approved 2026-10-06)."""
from __future__ import annotations

from typing import Any, Callable

DIM_ALPHA = 110
GLOW_RADIUS = 6
LENS_SCALE = 3
TINY_TARGET = 48            # px: a control this small also gets the zoom lens


class Spotlight:
    def __init__(self, schedule: Callable[[float, Callable[[], None]], Any] | None = None) -> None:
        self._schedule = schedule
        self._layer: Any = None
        self._generation = 0

    @property
    def visible(self) -> bool:
        return self._layer is not None

    def show(self, box: tuple[int, int, int, int], *, accent: str, radius: int = 8,
             seconds: float | None = 4.0, zoom: bool = False) -> bool:
        """Outline ``box`` (screen x, y, width, height) for ``seconds``, or until ``hide()`` when
        ``seconds`` is None (held while MO asks "this one?"); a newer point replaces it."""
        from mo_desktop import brightness
        from mo_desktop.layered import NativeLayeredWindow

        x, y, w, h = (int(value) for value in box)
        display = brightness.display_at(x + w // 2, y + h // 2)
        if display is None or w <= 0 or h <= 0:
            return False
        rect = display[1]
        image = render(box, rect, accent=accent, radius=radius, lens=_grab(box) if zoom and max(w, h) <= TINY_TARGET else None)
        try:
            if self._layer is None:
                self._layer = NativeLayeredWindow(title="MO Desktop — Pointing")
                self._layer.exclude_from_capture(True)
            self._layer._target_hwnd = brightness.lowest_own_window(exclude=int(self._layer.hwnd))
            if not self._layer.blit(image, rect[0], rect[1]):
                self.hide()
                return False
            brightness.keep_in_top_band(self._layer, rect)
        except Exception:
            self.hide()
            return False
        self._generation += 1
        generation = self._generation
        if self._schedule is not None and seconds is not None:
            try:
                self._schedule(max(0.5, float(seconds or 4.0)) * 1000, lambda: self._expire(generation))
            except Exception:
                pass
        return True

    def _expire(self, generation: int) -> None:
        if generation == self._generation:
            self.hide()

    def hide(self) -> None:
        layer, self._layer = self._layer, None
        if layer is not None:
            try:
                layer.destroy()
            except Exception:
                pass


def _grab(box: tuple[int, int, int, int]) -> Any:
    """The target's pixels for the zoom (MO's own windows are kept out of captures)."""
    try:
        from PIL import ImageGrab

        x, y, w, h = box
        return ImageGrab.grab(bbox=(x, y, x + w, y + h), all_screens=True).convert("RGBA")
    except Exception:
        return None


def render(box: tuple[int, int, int, int], rect: tuple[int, int, int, int], *, accent: str,
           radius: int = 8, lens: Any = None) -> Any:
    """The spotlight image for the screen ``rect``: dim everywhere but ``box``, an accent outline
    with a soft glow around it, and ``lens`` (the target's pixels) enlarged beside it."""
    from PIL import Image, ImageColor, ImageDraw, ImageFilter

    left, top, right, bottom = rect
    width, height = right - left, bottom - top
    x, y, w, h = box
    rgb = ImageColor.getrgb(accent)
    image = Image.new("RGBA", (width, height), (0, 0, 0, DIM_ALPHA))
    hole = (x - left - 3, y - top - 3, x - left + w + 3, y - top + h + 3)
    ImageDraw.Draw(image).rounded_rectangle(hole, radius=radius, fill=(0, 0, 0, 0))
    pad = GLOW_RADIUS * 3                      # blur only the area around the target, not the screen
    area = (max(0, hole[0] - pad), max(0, hole[1] - pad), min(width, hole[2] + pad), min(height, hole[3] + pad))
    local = (hole[0] - area[0], hole[1] - area[1], hole[2] - area[0], hole[3] - area[1])
    glow = Image.new("RGBA", (area[2] - area[0], area[3] - area[1]), (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle(local, radius=radius, outline=(*rgb, 200), width=4)
    glow = glow.filter(ImageFilter.GaussianBlur(GLOW_RADIUS))
    ImageDraw.Draw(glow).rounded_rectangle(local, radius=radius, outline=(*rgb, 255), width=2)
    image.alpha_composite(glow, area[:2])
    if lens is not None:
        zoomed = lens.resize((lens.width * LENS_SCALE, lens.height * LENS_SCALE), Image.Resampling.LANCZOS)
        frame = Image.new("RGBA", (zoomed.width + 8, zoomed.height + 8), (0, 0, 0, 0))
        ImageDraw.Draw(frame).rounded_rectangle((0, 0, frame.width - 1, frame.height - 1), radius=radius,
                                                fill=(14, 18, 26, 255), outline=(*rgb, 255), width=2)
        frame.alpha_composite(zoomed, (4, 4))
        gap = 24
        lx = hole[2] + gap if hole[2] + gap + frame.width <= width else max(0, hole[0] - gap - frame.width)
        ly = max(0, min(height - frame.height, (hole[1] + hole[3]) // 2 - frame.height // 2))
        image.alpha_composite(frame, (lx, ly))
        anchor = hole[2] if lx > hole[2] else hole[0]
        edge = lx if lx > hole[2] else lx + frame.width
        ImageDraw.Draw(image).line((anchor, (hole[1] + hole[3]) // 2, edge, ly + frame.height // 2), fill=(*rgb, 200), width=1)
    return image
