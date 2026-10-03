"""Transient, full-resolution screen selection for the Desktop cube gesture."""
from __future__ import annotations

from typing import Any, Callable

from mo_desktop.image_crop import CropSelection


class ScreenSelection:
    """Show the unchanged screen until a drag shades the selected pixels."""

    def __init__(self, root: Any, *, accent: str,
                 on_capture: Callable[[Any], None], on_close: Callable[[], None]) -> None:
        import ctypes
        import tkinter as tk
        from PIL import ImageGrab, ImageTk

        user32 = ctypes.windll.user32
        left = int(user32.GetSystemMetrics(76))  # SM_XVIRTUALSCREEN
        top = int(user32.GetSystemMetrics(77))   # SM_YVIRTUALSCREEN
        self._screen = ImageGrab.grab(all_screens=True).convert("RGB")
        width, height = self._screen.size
        self._on_capture = on_capture
        self._on_close = on_close
        self._selection = CropSelection()
        self._closed = False
        self._window = tk.Toplevel(root)
        try:
            self._window.withdraw()
            self._window.overrideredirect(True)
            self._window.attributes("-topmost", True)
            self._window.geometry(f"{width}x{height}+0+0")
            self._image = ImageTk.PhotoImage(self._screen, master=self._window)
            self._canvas = tk.Canvas(self._window, width=width, height=height,
                                     bd=0, highlightthickness=0, cursor="crosshair")
            self._canvas.pack(fill="both", expand=True)
            self._canvas.create_image(0, 0, image=self._image, anchor="nw")
            self._shade = self._canvas.create_rectangle(0, 0, 0, 0, fill="black",
                                                        stipple="gray50", outline=accent, width=2,
                                                        state="hidden")
            self._canvas.bind("<ButtonPress-1>", self._press)
            self._canvas.bind("<B1-Motion>", self._drag)
            self._canvas.bind("<ButtonRelease-1>", self._release)
            self._canvas.bind("<ButtonRelease-3>", lambda _event: self.close())
            self._window.bind("<Escape>", lambda _event: self.close())
            self._window.deiconify()
            user32.SetWindowPos(int(self._window.winfo_id()), -1, left, top, width, height, 0x0040)
            self._window.focus_force()
        except Exception:
            self._window.destroy()
            raise

    def _press(self, event: Any) -> None:
        self._selection.begin(event.x, event.y)

    def _drag(self, event: Any) -> None:
        self._selection.drag(event.x, event.y)
        rect = self._selection.rect_display(bounds=(0, 0, *self._screen.size))
        if rect is None:
            return
        self._canvas.coords(self._shade, *rect)
        self._canvas.itemconfigure(self._shade, state="normal")

    def _release(self, event: Any) -> None:
        if not self._selection.dragging:
            return
        self._selection.drag(event.x, event.y)
        self._selection.end()
        rect = self._selection.rect_display(bounds=(0, 0, *self._screen.size), min_size=3)
        if rect is None:
            self._canvas.itemconfigure(self._shade, state="hidden")
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
