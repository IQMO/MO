"""Shared per-pixel-alpha layered-window helper (UpdateLayeredWindow / ULW_ALPHA).

Lets a borderless Tk ``Toplevel`` be painted directly from a PIL RGBA image, so a
surface can float on the desktop with SMOOTH anti-aliased edges, a soft shadow, and a
glow — the same rendering language as the 4-cube (``cube.py``). A binary chroma key or
``SetWindowRgn`` can't do soft edges; this can. Windows-only; ``available()`` is False
elsewhere so callers fall back to a plain window.
"""
from __future__ import annotations

from typing import Any
from functools import lru_cache

_WINAPI: Any = None


@lru_cache(maxsize=128)
def path_icon(path: str, index: int | None = None) -> Any:
    """Read a file/shortcut's actual shell icon through the borrowed-HICON renderer."""
    import os
    if os.name != "nt":
        return None
    import win32gui
    from win32com.shell import shell, shellcon
    if index is not None and index != -1:
        large, small = win32gui.ExtractIconEx(path, index, 1)
        handles = [*large, *small]
        try:
            return icon_image(handles[0]) if handles else None
        finally:
            for handle in handles:
                win32gui.DestroyIcon(handle)
    success, info = shell.SHGetFileInfo(path, 0, shellcon.SHGFI_ICON)
    if not success or not info[0]:
        return None
    try:
        return icon_image(info[0])
    finally:
        win32gui.DestroyIcon(info[0])


def icon_image(handle: int) -> Any:
    """Rasterize a borrowed Windows icon; its caller retains handle ownership."""
    from PIL import Image
    import win32gui
    import win32ui

    dc = win32ui.CreateDCFromHandle(win32gui.GetDC(0))
    memory = dc.CreateCompatibleDC()
    bitmap = win32ui.CreateBitmap()
    bitmap.CreateCompatibleBitmap(dc, 32, 32)
    old = memory.SelectObject(bitmap)
    try:
        passes = []
        for color in (0, 0xFFFFFF):
            memory.FillSolidRect((0, 0, 32, 32), color)
            win32gui.DrawIconEx(memory.GetSafeHdc(), 0, 0, handle, 32, 32, 0, None, 3)
            passes.append(Image.frombuffer("RGB", (32, 32), bitmap.GetBitmapBits(True), "raw", "BGRX", 0, 1))
        pixels = []
        for black, white in zip(passes[0].getdata(), passes[1].getdata()):
            alpha = max(0, min(255, 255 - max(w - b for b, w in zip(black, white))))
            pixels.append(tuple(min(255, round(v * 255 / alpha)) if alpha else 0 for v in black) + (alpha,))
        image = Image.new("RGBA", (32, 32))
        image.putdata(pixels)
        return image
    finally:
        memory.SelectObject(old)
        win32gui.DeleteObject(bitmap.GetHandle())
        memory.DeleteDC()
        win32gui.ReleaseDC(0, dc.GetSafeHdc())


def _winapi() -> Any:
    """Return pointer-width-safe Win32 bindings, initialized once on first use."""
    global _WINAPI
    if _WINAPI is not None:
        return _WINAPI

    import ctypes
    from ctypes import wintypes
    from types import SimpleNamespace

    class Point(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class Size(ctypes.Structure):
        _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]

    class BlendFunction(ctypes.Structure):
        _fields_ = [
            ("op", ctypes.c_byte),
            ("flags", ctypes.c_byte),
            ("alpha", ctypes.c_byte),
            ("format", ctypes.c_byte),
        ]

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int
    user32.UpdateLayeredWindow.argtypes = [
        wintypes.HWND,
        wintypes.HDC,
        ctypes.POINTER(Point),
        ctypes.POINTER(Size),
        wintypes.HDC,
        ctypes.POINTER(Point),
        wintypes.COLORREF,
        ctypes.POINTER(BlendFunction),
        wintypes.DWORD,
    ]
    user32.UpdateLayeredWindow.restype = wintypes.BOOL
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        ctypes.c_void_p,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.SetWindowPos.argtypes = [
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    user32.DispatchMessageW.restype = ctypes.c_ssize_t
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL
    gdi32.CreateDIBSection.argtypes = [
        wintypes.HDC,
        ctypes.c_void_p,
        wintypes.UINT,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.HANDLE,
        wintypes.DWORD,
    ]
    gdi32.CreateDIBSection.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL
    _WINAPI = SimpleNamespace(
        ctypes=ctypes,
        wintypes=wintypes,
        user32=user32,
        gdi32=gdi32,
        Point=Point,
        Size=Size,
        BlendFunction=BlendFunction,
    )
    return _WINAPI


def pump_native_window_messages() -> None:
    """Dispatch pending messages for native helper HWNDs on this thread."""
    import sys

    if sys.platform != "win32":
        return
    api = _winapi()
    message = api.wintypes.MSG()
    while api.user32.PeekMessageW(api.ctypes.byref(message), None, 0, 0, 0x0001):
        api.user32.TranslateMessage(api.ctypes.byref(message))
        api.user32.DispatchMessageW(api.ctypes.byref(message))


class LayeredWindow:
    """Wrap a Tk Toplevel as a per-pixel-alpha layered window painted from PIL images."""

    def __init__(self, win: Any, *, click_through: bool = False) -> None:
        self._win = win
        self._click_through = click_through
        self._ok = False
        self._last_error = "not initialized"
        self._dc = None
        self._bmp = None
        self._old = None
        self._ppv = None
        self._w = 0
        self._h = 0
        self._cached_hwnd = 0
        self._input_bounds = None
        self._input_procs: dict[int, tuple[Any, Any]] = {}
        self._setup()

    def available(self) -> bool:
        return self._ok

    def set_input_bounds(self, bounds: tuple[int, int, int, int]) -> None:
        """Keep the painted shadow passive without clipping its antialiased pixels."""
        import win32gui
        self._input_bounds = bounds
        for hwnd in {self._hwnd(), win32gui.GetAncestor(self._hwnd(), 2)}:
            if hwnd in self._input_procs:
                continue
            def install(handle: int) -> None:
                previous = None
                def dispatch(h: int, message: int, wparam: int, lparam: int) -> int:
                    if message == 0x0084:  # WM_NCHITTEST carries signed screen coordinates.
                        x, y = lparam & 0xffff, lparam >> 16 & 0xffff
                        x, y = x-65536 if x & 32768 else x, y-65536 if y & 32768 else y
                        left, top, right, bottom = self._input_bounds
                        if not (left <= x < right and top <= y < bottom):
                            return -1  # HTTRANSPARENT: the original cube remains interactive.
                    return win32gui.CallWindowProc(previous, h, message, wparam, lparam)
                previous = win32gui.SetWindowLong(handle, -4, dispatch)
                self._input_procs[handle] = (previous, dispatch)
            install(hwnd)

    def set_click_through(self, enabled: bool) -> bool:
        """Switch one existing layered surface between a passive hint and a control."""
        if not self._ok:
            return False
        try:
            api = _winapi()
            hwnd = self._hwnd()
            api.user32.GetWindowLongW.argtypes = [api.wintypes.HWND, api.ctypes.c_int]
            api.user32.GetWindowLongW.restype = api.ctypes.c_long
            api.user32.SetWindowLongW.argtypes = [api.wintypes.HWND, api.ctypes.c_int, api.ctypes.c_long]
            api.user32.SetWindowLongW.restype = api.ctypes.c_long
            style = api.user32.GetWindowLongW(hwnd, -20)
            transparent = 0x00000020
            new_style = style | transparent if enabled else style & ~transparent
            if new_style != style:
                api.ctypes.set_last_error(0)
                api.user32.SetWindowLongW(hwnd, -20, new_style)
                if api.ctypes.get_last_error():
                    return False
            self._click_through = bool(enabled)
            return True
        except Exception:
            return False

    def failure_detail(self) -> str:
        """Return a bounded diagnostic reason without exposing exception text."""
        return self._last_error or "unknown layered-window failure"

    def exclude_from_capture(self, enabled: bool) -> bool:
        """Keep a passive activity cue out of MO's own desktop captures."""
        if not self._ok:
            return False
        api = _winapi()
        method = api.user32.SetWindowDisplayAffinity
        method.argtypes = [api.wintypes.HWND, api.wintypes.DWORD]
        method.restype = api.wintypes.BOOL
        return bool(method(self._hwnd(), 0x11 if enabled else 0))

    def _fail(self, detail: str, *, disable: bool = False) -> bool:
        self._last_error = str(detail or "unknown layered-window failure")[:160]
        if disable:
            self._ok = False
        return False

    # ------------------------------------------------------------------
    def _hwnd(self) -> int:
        cached = int(getattr(self, "_cached_hwnd", 0) or 0)
        if cached:
            return cached
        try:
            hwnd = int(self._win.winfo_id())
            self._cached_hwnd = hwnd
            return hwnd
        except Exception:
            return 0

    def _setup(self) -> None:
        import sys
        if sys.platform != "win32":
            self._fail("layered windows require Windows")
            return
        try:
            from PIL import Image, ImageChops  # noqa: F401 (used by blit)
        except Exception as exc:
            self._fail(f"Pillow unavailable ({type(exc).__name__})")
            return
        try:
            from core.desktop.win32 import apply_layered_exstyle

            self._win.update_idletasks()
            hwnd = self._hwnd()
            if not hwnd:
                self._fail("native window handle unavailable")
                return
            self._ok = apply_layered_exstyle(hwnd, click_through=self._click_through)
            if self._ok:
                self._last_error = ""
            else:
                self._fail("layered window style was rejected")
        except Exception as exc:
            self._fail(f"layered window setup failed ({type(exc).__name__})", disable=True)

    def _alloc(self, w: int, h: int) -> bool:
        api = _winapi()
        ctypes = api.ctypes
        wintypes = api.wintypes
        g = api.gdi32
        u = api.user32
        self._free()

        class _BIH(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("a", wintypes.LONG),
                        ("b", wintypes.LONG), ("c", wintypes.DWORD), ("d", wintypes.DWORD)]

        class _BI(ctypes.Structure):
            _fields_ = [("h", _BIH), ("cols", wintypes.DWORD * 3)]

        screen = u.GetDC(0)
        if not screen:
            return self._fail("screen device context unavailable")
        memdc = g.CreateCompatibleDC(screen)
        u.ReleaseDC(0, screen)
        if not memdc:
            return self._fail("compatible device context unavailable")
        bi = _BI()
        bi.h.biSize = ctypes.sizeof(_BIH)
        bi.h.biWidth = w
        bi.h.biHeight = -h  # top-down
        bi.h.biPlanes = 1
        bi.h.biBitCount = 32
        ppv = ctypes.c_void_p()
        bmp = g.CreateDIBSection(memdc, ctypes.byref(bi), 0, ctypes.byref(ppv), None, 0)
        if not bmp or not ppv.value:
            g.DeleteDC(memdc)
            return self._fail("layered bitmap allocation failed")
        old = g.SelectObject(memdc, bmp)
        self._dc, self._bmp, self._old, self._ppv, self._w, self._h = memdc, bmp, old, ppv, w, h
        return True

    def _free(self) -> None:
        try:
            g = _winapi().gdi32
            if self._dc:
                if self._old:
                    g.SelectObject(self._dc, self._old)
                if self._bmp:
                    g.DeleteObject(self._bmp)
                g.DeleteDC(self._dc)
        except Exception:
            pass
        self._dc = self._bmp = self._old = self._ppv = None
        self._w = self._h = 0

    def _update_layered_opacity(self, opacity: int) -> bool:
        if not self._ok or not self._dc or self._w <= 0 or self._h <= 0:
            return self._fail("layered bitmap unavailable")
        try:
            api = _winapi()
            ctypes = api.ctypes
            size = api.Size(self._w, self._h)
            source = api.Point(0, 0)
            alpha = max(0, min(255, int(opacity)))
            blend = api.BlendFunction(0, 0, alpha, 1)
            updated = api.user32.UpdateLayeredWindow(
                self._hwnd(),
                None,
                None,
                ctypes.byref(size),
                self._dc,
                ctypes.byref(source),
                0,
                ctypes.byref(blend),
                0x02,
            )
            if not updated:
                return self._fail(
                    f"UpdateLayeredWindow failed (winerror={int(ctypes.get_last_error() or 0)})",
                    disable=True,
                )
            self._last_error = ""
            return True
        except Exception as exc:
            return self._fail(
                f"layered opacity update failed ({type(exc).__name__})",
                disable=True,
            )

    def set_opacity(self, opacity: int) -> bool:
        """Reuse the current bitmap while changing only its global alpha."""
        return self._update_layered_opacity(opacity)

    def blit(
        self,
        img: Any,
        x: int,
        y: int,
        *,
        premultiplied: bool = False,
        opacity: int = 255,
        position: bool = True,
    ) -> bool:
        """Paint PIL RGBA ``img`` at screen ``(x, y)``; the window resizes to match.

        Positions the window via Tk ``geometry`` and paints the content in place
        (``pptDst = NULL``) — the same order the cube uses, which repositions reliably.
        ``premultiplied=True`` means the caller already multiplied RGB by alpha (e.g. it
        resized in premultiplied space to avoid edge darkening), so only reorder to BGRA.
        """
        if not self._ok:
            if not self._last_error:
                self._last_error = "layered window unavailable"
            return False
        try:
            api = _winapi()
            ctypes = api.ctypes
            from PIL import Image, ImageChops
            w, h = img.size
            if (w, h) != (self._w, self._h):
                if not self._alloc(w, h):
                    return False
            r, g_, b, a = img.split()
            if premultiplied:
                bgra = Image.merge("RGBA", (b, g_, r, a)).tobytes()
            else:
                bgra = Image.merge("RGBA", (ImageChops.multiply(b, a), ImageChops.multiply(g_, a),
                                            ImageChops.multiply(r, a), a)).tobytes()
            ctypes.memmove(self._ppv, bgra, min(len(bgra), w * h * 4))
            if position:
                try:
                    self._win.geometry(f"{w}x{h}+{int(x)}+{int(y)}")
                except Exception:
                    pass

            return self._update_layered_opacity(opacity)
        except Exception as exc:
            return self._fail(f"layered blit failed ({type(exc).__name__})", disable=True)

    def destroy(self) -> None:
        if self._input_procs:
            import win32gui
            for hwnd, (previous, _dispatch) in self._input_procs.items():
                if win32gui.IsWindow(hwnd):
                    win32gui.SetWindowLong(hwnd, -4, previous)
            self._input_procs.clear()
        self._free()


class NativeLayeredWindow(LayeredWindow):
    """One native alpha surface: passive effects or an explicitly interactive panel."""

    _WS_POPUP = 0x80000000
    _WS_EX_TRANSPARENT = 0x00000020
    _WS_EX_TOOLWINDOW = 0x00000080
    _WS_EX_LAYERED = 0x00080000
    _WS_EX_NOACTIVATE = 0x08000000
    _SW_HIDE = 0
    _SWP_NOZORDER = 0x0004
    _SWP_NOACTIVATE = 0x0010
    _SWP_SHOWWINDOW = 0x0040

    def __init__(self, target_hwnd: int = 0, *, on_message: Any = None, post: Any = None,
                 title: str = "", activate: bool = True) -> None:
        api = _winapi()
        self._target_hwnd = int(target_hwnd)
        self._interactive = on_message is not None
        self._message_handler = on_message
        self._post = post
        self._window_proc: Any = None
        self._previous_proc: Any = None
        self._controls: dict[Any, Any] = {}
        self._control_procs: dict[Any, Any] = {}
        self._control_layout: Any = None
        self._invoke_control: Any = None
        self._text_input = 0
        self._text_callback = None
        self._text_proc = None
        exstyle = (
            self._WS_EX_LAYERED
            | self._WS_EX_TOOLWINDOW
        )
        if not self._interactive:
            exstyle |= self._WS_EX_TRANSPARENT
        if not self._interactive or not activate:
            exstyle |= self._WS_EX_NOACTIVATE
        hwnd = api.user32.CreateWindowExW(
            exstyle,
            "Static",
            title,
            self._WS_POPUP | (0x0100 if self._interactive else 0),  # SS_NOTIFY receives pointer input.
            0,
            0,
            1,
            1,
            None,
            None,
            None,
            None,
        )
        if not hwnd:
            raise RuntimeError("native layered effect window could not be created")
        self._native_hwnd = int(hwnd)

        class _NativeProxy:
            def __init__(self, value: int) -> None:
                self.value = value

            def winfo_id(self) -> int:
                return self.value

            def update_idletasks(self) -> None:
                return None

            def geometry(self, _value: str) -> None:
                return None

        super().__init__(_NativeProxy(self._native_hwnd), click_through=not self._interactive)
        if not self.available():
            api.user32.DestroyWindow(self._native_hwnd)
            self._native_hwnd = 0
            raise RuntimeError(self.failure_detail())
        if self._interactive:
            import win32gui
            def dispatch(hwnd: int, message: int, wparam: int, lparam: int) -> int:
                previous = self._previous_proc
                if message == 0x0111 and lparam == self._text_input and wparam >> 16 == 0x0300:
                    value = win32gui.GetWindowText(self._text_input)
                    if self._post is not None:
                        self._post(lambda: self._text_callback(value) if self._native_hwnd else None)
                    else:
                        self._text_callback(value)
                    return 0
                if message == 0x0111 and lparam and not (wparam >> 16):
                    key = next((key for key, value in self._controls.items() if value == lparam), None)
                    if key is not None:
                        if self._post is not None:
                            self._post(lambda: self._invoke_control(key))
                        else:
                            self._invoke_control(key)
                        return 0
                if message == 0x002B:  # Owner-drawn controls use the shared alpha bitmap.
                    return 1
                result = self._deliver_message(hwnd, message, wparam, lparam)
                if result is not None:
                    return int(result)
                return win32gui.CallWindowProc(previous, hwnd, message, wparam, lparam)
            self._window_proc = dispatch
            self._previous_proc = win32gui.SetWindowLong(self._native_hwnd, -4, dispatch)

    def _deliver_message(self, hwnd: int, message: int, wparam: int, lparam: int) -> int | None:
        if self._post is not None:
            if message in (0x0200, 0x0201, 0x0202, 0x0203, 0x0204, 0x0205, 0x020A, 0x0100, 0x0006):
                # Tcl releases the GIL around its Windows message pump. Native
                # callbacks must not re-enter Tk; the existing GUI queue owns work.
                self._post(lambda: self._message_handler(hwnd, message, wparam, lparam)
                           if self._native_hwnd else None)
                return 0
            return None
        return self._message_handler(hwnd, message, wparam, lparam)

    def set_controls(self, controls: dict[Any, tuple[str, tuple[int, int, int, int]]], invoke: Any) -> None:
        """Expose the painted hit targets as real, named Windows buttons for UIA.

        These controls own semantics only. The existing bitmap owns every pixel,
        and the existing surface handler owns mouse/keyboard behavior.
        """
        import win32gui
        layout = tuple(controls.items())
        self._invoke_control = invoke
        if layout == self._control_layout:
            return
        self._control_layout = layout
        for key in set(self._controls)-controls.keys():
            win32gui.DestroyWindow(self._controls.pop(key))
            self._control_procs.pop(key, None)
        for key, (label, box) in controls.items():
            if key not in self._controls:
                child = win32gui.CreateWindowEx(0, "Button", label, 0x5001000B,
                    *box, self._native_hwnd, len(self._controls)+100, 0, None)
                self._controls[key] = child
                def make_proc(child: int, key: Any) -> Any:
                    previous = None
                    def proc(hwnd: int, message: int, wparam: int, lparam: int) -> int:
                        if message == 0x00F5:  # BM_CLICK is UIA's named Invoke action.
                            if self._post is not None:
                                self._post(lambda: self._invoke_control(key) if self._native_hwnd else None)
                            else:
                                self._invoke_control(key)
                            return 0
                        if message in (0x0200, 0x0201, 0x0202, 0x0203, 0x0204, 0x0205, 0x0100):
                            if message != 0x0100:
                                sx, sy = win32gui.ClientToScreen(hwnd, (lparam & 0xffff, lparam >> 16 & 0xffff))
                                x, y = win32gui.ScreenToClient(self._native_hwnd, (sx, sy))
                                lparam = ((y & 0xffff) << 16) | (x & 0xffff)
                            result = self._deliver_message(self._native_hwnd, message, wparam, lparam)
                            if result is not None:
                                return int(result)
                        return win32gui.CallWindowProc(previous, hwnd, message, wparam, lparam)
                    previous = win32gui.SetWindowLong(child, -4, proc)
                    return proc
                self._control_procs[key] = make_proc(child, key)
            child = self._controls[key]
            win32gui.SetWindowText(child, label)
            win32gui.SetWindowPos(child, 0, *box, 0x0014)

    def text_input(self, box: tuple[int, int, int, int] | None, changed: Any) -> None:
        """Native EDIT owns text, selection, clipboard and IME; the card owns pixels."""
        import win32gui
        self._text_callback = changed
        if box is None:
            if self._text_input:
                win32gui.ShowWindow(self._text_input, 0)
            return
        if not self._text_input:
            child = win32gui.CreateWindowEx(self._WS_EX_LAYERED, "Edit", "", 0x50010080,
                *box, self._native_hwnd, 900, 0, None)
            self._text_input = child
            win32gui.SetLayeredWindowAttributes(child, 0, 0, 2)
            win32gui.SendMessage(child, 0x00C5, 200, 0)  # EM_LIMITTEXT
            previous = None
            def proc(hwnd: int, message: int, wparam: int, lparam: int) -> int:
                if message == 0x0100 and wparam in (13, 27, 38, 40):
                    self._deliver_message(self._native_hwnd, message, wparam, lparam)
                    return 0
                if message == 0x0102 and wparam in (13, 27):
                    return 0
                import win32api
                if message == 0x0100 and wparam == 65 and win32api.GetKeyState(17) < 0:
                    win32gui.SendMessage(hwnd, 0x00B1, 0, -1)
                    return 0
                return win32gui.CallWindowProc(previous, hwnd, message, wparam, lparam)
            previous = win32gui.SetWindowLong(child, -4, proc)
            self._text_proc = proc
        win32gui.SetWindowPos(self._text_input, 0, *box, 0x0054)

    def focus_text_input(self) -> None:
        import win32gui
        if self._text_input:
            foreground = win32gui.GetForegroundWindow()
            if foreground != self._native_hwnd:
                self._text_previous_hwnd = foreground
            win32gui.SetForegroundWindow(self._native_hwnd)
            win32gui.SetFocus(self._text_input)

    def blur_text_input(self) -> None:
        import win32gui
        previous = getattr(self, "_text_previous_hwnd", 0)
        if previous and win32gui.IsWindow(previous) and win32gui.GetForegroundWindow() == self._native_hwnd:
            win32gui.SetForegroundWindow(previous)

    def text_selection(self) -> tuple[int, int] | None:
        import win32gui
        if not self._text_input or win32gui.GetFocus() != self._text_input:
            return None
        selected = win32gui.SendMessage(self._text_input, 0x00B0, 0, 0)
        return selected & 0xffff, selected >> 16 & 0xffff

    def clear_text_input(self) -> None:
        import win32gui
        if self._text_input:
            win32gui.SetWindowText(self._text_input, "")

    def blit(
        self,
        img: Any,
        x: int,
        y: int,
        *,
        premultiplied: bool = False,
        opacity: int = 255,
        show: bool = True,
        position: bool = True,
    ) -> bool:
        if not self._native_hwnd:
            return False
        api = _winapi()
        width, height = img.size
        positioned = api.user32.SetWindowPos(
            self._native_hwnd,
            None,
            int(x),
            int(y),
            int(width),
            int(height),
            self._SWP_NOZORDER | self._SWP_NOACTIVATE,
        )
        if not positioned:
            return self._fail("native layered effect window could not be positioned")
        if not super().blit(
            img,
            x,
            y,
            premultiplied=premultiplied,
            opacity=opacity,
            position=False,
        ):
            return False
        if not show:
            self.hide()
            return True
        if self._interactive:
            return bool(api.user32.SetWindowPos(self._native_hwnd, -1, int(x), int(y), width, height,
                                               self._SWP_NOACTIVATE | self._SWP_SHOWWINDOW))
        return self.place_behind(x, y, width, height)

    def place_behind(self, x: int, y: int, width: int, height: int) -> bool:
        if not self._native_hwnd:
            return False
        api = _winapi()
        if self._target_hwnd and not api.user32.IsWindow(self._target_hwnd):
            return self._fail("native layered effect target is unavailable", disable=True)
        placed = api.user32.SetWindowPos(
            self._native_hwnd,
            self._target_hwnd or -1,
            int(x),
            int(y),
            max(1, int(width)),
            max(1, int(height)),
            self._SWP_NOACTIVATE | self._SWP_SHOWWINDOW,
        )
        return bool(placed) or self._fail("native layered effect z-order was rejected")

    def hide(self) -> None:
        if self._native_hwnd:
            _winapi().user32.ShowWindow(self._native_hwnd, self._SW_HIDE)

    def destroy(self) -> None:
        super().destroy()
        if self._native_hwnd:
            try:
                import win32gui
                win32gui.DestroyWindow(self._native_hwnd)
            finally:
                self._native_hwnd = 0
                self._controls.clear()
                self._control_procs.clear()
