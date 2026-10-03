"""Transient, full-resolution screen selection for the Desktop cube gesture."""
from __future__ import annotations

from typing import Any, Callable

from mo_desktop.image_crop import CropSelection


class ScreenSelection:
    """Show the unchanged screen until a drag shades the selected pixels."""

    def __init__(self, *, post: Any, accent: str,
                 on_capture: Callable[[Any], None], on_close: Callable[[], None]) -> None:
        import ctypes
        from PIL import ImageGrab
        from mo_desktop.layered import NativeLayeredWindow

        user32 = ctypes.windll.user32
        left = int(user32.GetSystemMetrics(76))  # SM_XVIRTUALSCREEN
        top = int(user32.GetSystemMetrics(77))   # SM_YVIRTUALSCREEN
        self._screen = ImageGrab.grab(all_screens=True).convert("RGB")
        self._origin = (left, top)
        self._accent = accent
        self._last_rect = None
        self._on_capture = on_capture
        self._on_close = on_close
        self._selection = CropSelection()
        self._closed = False
        self._window = NativeLayeredWindow(title="MO screen selection", on_event=self._on_event, post=post)
        try:
            self._window.set_cursor("cross")
            self._paint(None)
            self._window.activate()
        except Exception:
            self._window.destroy()
            raise

    def _on_event(self, kind: str, event: Any) -> None:
        if kind == "close" or (kind == "key" and event.keysym == "Escape"):
            self.close()
        elif kind == "release" and event.num == 3:
            self.close()
        elif kind == "press" and event.num == 1:
            self._press(event)
        elif kind == "motion" and event.dragging:
            self._drag(event)
        elif kind == "release" and event.num == 1:
            self._release(event)

    def _paint(self, rect: Any) -> None:
        from PIL import Image, ImageDraw
        image = self._screen.convert("RGBA")
        if rect is not None:
            box = tuple(round(value) for value in rect)
            shade = Image.new("RGBA", (box[2]-box[0], box[3]-box[1]), (0, 0, 0, 128))
            image.alpha_composite(shade, box[:2])
            ImageDraw.Draw(image).rectangle(box, outline=self._accent, width=2)
        if not self._window.blit(image, *self._origin):
            raise RuntimeError(self._window.failure_detail())
        self._last_rect = rect

    def _press(self, event: Any) -> None:
        self._selection.begin(event.x, event.y)

    def _drag(self, event: Any) -> None:
        self._selection.drag(event.x, event.y)
        rect = self._selection.rect_display(bounds=(0, 0, *self._screen.size))
        if rect is None or rect == self._last_rect:
            return
        self._paint(rect)

    def _release(self, event: Any) -> None:
        if not self._selection.dragging:
            return
        self._selection.drag(event.x, event.y)
        self._selection.end()
        rect = self._selection.rect_display(bounds=(0, 0, *self._screen.size), min_size=3)
        if rect is None:
            self._paint(None)
            return
        box = tuple(int(round(value)) for value in rect)
        captured = self._screen.crop(box)
        self.close()
        self._on_capture(captured)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._window.destroy()
        self._on_close()
