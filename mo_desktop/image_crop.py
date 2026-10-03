"""Reusable crop-selection geometry + overlay for MO Desktop surfaces.

A drag-rectangle crop, split so the correctness-critical part is pure and testable:

- ``CropSelection`` owns the rectangle state and the coordinate math — normalize a
  press→drag rectangle, clamp it to the shown image, and map it from *display* pixels to
  *source-image* pixels for ``core/imageedit.crop``. No tkinter, no PIL, no rendering.
- ``draw_crop_overlay`` dims outside the selection and draws its border + corner handles
  on a PIL canvas.

The panel (``reply_bubble``) owns the pointer bindings and the canvas and feeds this
module display-space coordinates; any other Desktop surface can reuse it the same way.
Coordinates are whatever space the caller works in (the panel uses supersampled canvas
px for drawing and window px for hit-mapping) — this module is unit-agnostic as long as
``disp_box`` and the pointer coords share one space.
"""
from __future__ import annotations

from typing import Any


class CropSelection:
    """A press→drag→release rectangle, in the caller's display coordinates."""

    def __init__(self) -> None:
        self._start: tuple[float, float] | None = None
        self._cur: tuple[float, float] | None = None
        self.dragging = False

    def begin(self, x: float, y: float) -> None:
        self._start = (float(x), float(y))
        self._cur = (float(x), float(y))
        self.dragging = True

    def drag(self, x: float, y: float) -> None:
        if self._start is None:
            return
        self._cur = (float(x), float(y))

    def end(self) -> None:
        self.dragging = False

    def clear(self) -> None:
        self._start = self._cur = None
        self.dragging = False

    def rect_display(self, bounds: tuple[float, float, float, float] | None = None,
                     min_size: float = 1.0) -> tuple[float, float, float, float] | None:
        """Normalized (x0, y0, x1, y1) with x0<x1, y0<y1, optionally clamped to ``bounds``
        (bx0, by0, bx1, by1). ``None`` when there is no selection of at least ``min_size``."""
        if self._start is None or self._cur is None:
            return None
        x0, x1 = sorted((self._start[0], self._cur[0]))
        y0, y1 = sorted((self._start[1], self._cur[1]))
        if bounds is not None:
            bx0, by0, bx1, by1 = bounds
            x0, x1 = max(bx0, min(x0, bx1)), max(bx0, min(x1, bx1))
            y0, y1 = max(by0, min(y0, by1)), max(by0, min(y1, by1))
        if (x1 - x0) < min_size or (y1 - y0) < min_size:
            return None
        return (x0, y0, x1, y1)

    def box_source(self, disp_box: tuple[float, float, float, float],
                   src_size: tuple[int, int]) -> tuple[int, int, int, int] | None:
        """Map the display rectangle to source pixels. ``disp_box`` is the shown image's
        placement/extent in display coords; ``src_size`` is (source_w, source_h). Returns
        (x, y, w, h) in source pixels, clamped to the image, or ``None`` if empty."""
        rect = self.rect_display(bounds=disp_box)
        if rect is None:
            return None
        dx0, dy0, dx1, dy1 = disp_box
        dw = max(1.0, float(dx1 - dx0))
        dh = max(1.0, float(dy1 - dy0))
        sw, sh = int(src_size[0]), int(src_size[1])
        sx0 = (rect[0] - dx0) / dw * sw
        sy0 = (rect[1] - dy0) / dh * sh
        sx1 = (rect[2] - dx0) / dw * sw
        sy1 = (rect[3] - dy0) / dh * sh
        x = int(max(0, min(round(sx0), sw)))
        y = int(max(0, min(round(sy0), sh)))
        w = int(min(round(sx1), sw) - x)
        h = int(min(round(sy1), sh) - y)
        if w < 1 or h < 1:
            return None
        return (x, y, w, h)


def draw_crop_overlay(img: Any, d: Any, disp_box: tuple[float, float, float, float],
                      rect: tuple[float, float, float, float] | None, *,
                      dim_rgba: tuple[int, int, int, int], line_rgb: tuple[int, int, int],
                      ss: int) -> None:
    """Dim the image (``disp_box``) outside the selection ``rect`` and draw the rect border
    + corner handles. All coords are in the canvas's pixel space. ``rect`` None -> no dim
    (the pre-drag prompt state). ``img`` is the RGBA canvas, ``d`` an ImageDraw on it."""
    if rect is None:
        return
    from PIL import Image, ImageDraw

    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ld = ImageDraw.Draw(layer)
    ld.rectangle([disp_box[0], disp_box[1], disp_box[2], disp_box[3]], fill=dim_rgba)
    ld.rectangle([rect[0], rect[1], rect[2], rect[3]], fill=(0, 0, 0, 0))  # clear the hole
    img.alpha_composite(layer)
    w = max(1, ss)
    d.rectangle([rect[0], rect[1], rect[2], rect[3]], outline=(*line_rgb, 255), width=w)
    hs = 3 * ss
    for hx, hy in ((rect[0], rect[1]), (rect[2], rect[1]),
                   (rect[0], rect[3]), (rect[2], rect[3])):
        d.rectangle([hx - hs, hy - hs, hx + hs, hy + hs], fill=(*line_rgb, 255))
