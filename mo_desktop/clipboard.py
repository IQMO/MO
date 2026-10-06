"""MO Desktop clipboard history: event-driven, memory only, private by default.

Windows tells one message-only listener window each time the clipboard changes
(``AddClipboardFormatListener``; no polling). Items live in this process only and
are never written to disk. Content an app marks as not for clipboard history
(password managers do) is never recorded, and an item that looks like a secret
(MO's redaction rules) is masked in the list and is never offered to MO. Nothing
reaches MO unless the operator picks "Ask MO" on an item.
"""
from __future__ import annotations

import ctypes
import hashlib
import io
import struct
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

WM_CLIPBOARDUPDATE = 0x031D
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
_MAX_TEXT_CHARS = 100_000
_MAX_IMAGE_PIXELS = 40_000_000
_THUMB_SIZE = (40, 26)
# Read a moment after Windows reports a change, once per burst: the app that just copied, and a
# paste right after it, get the clipboard first (an immediate read made MO Shell's Ctrl+V miss).
CAPTURE_DELAY_MS = 150
# Formats an app sets to keep its copy out of clipboard histories (Windows convention).
_EXCLUDE_FORMATS = ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore")
_HISTORY_FORMAT = "CanIncludeInClipboardHistory"


@dataclass
class ClipItem:
    kind: str                       # "text" | "image" | "files"
    text: str = ""                  # the text, or newline-joined file paths
    image: Any = None               # PIL image, memory only
    source: str = ""                # the app in front when it was copied
    masked: bool = False
    copied_at: float = field(default_factory=time.time)
    ident: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    digest: str = ""

    def __post_init__(self) -> None:
        if not self.digest:
            payload = self.image.tobytes() if self.image is not None else self.text.encode("utf-8", "replace")
            self.digest = hashlib.sha1(self.kind.encode() + b"\0" + payload).hexdigest()


def looks_secret(text: str) -> bool:
    from core.tooling.sandbox import redact_sensitive_text

    return bool(text) and redact_sensitive_text(text) != text


def _age(seconds: float) -> str:
    minutes = int(max(0.0, seconds) // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes} min ago"
    return f"{minutes // 60} h ago" if minutes < 2880 else f"{minutes // 1440} days ago"


def _foreground_app() -> str:
    try:
        from mo_desktop.overlay_compat import process_executable_name

        user32 = ctypes.windll.user32
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
        name = process_executable_name(int(pid.value))
        return name[:-4] if name.lower().endswith(".exe") else name
    except Exception:
        return ""


def _open_clipboard(win32clipboard: Any) -> bool:
    for _attempt in range(5):           # another app may hold it for a moment
        try:
            win32clipboard.OpenClipboard()
            return True
        except Exception:
            time.sleep(0.02)
    return False


def read_clipboard() -> ClipItem | None:
    """The clipboard's current content as one item, or None (empty, excluded or unreadable)."""
    import win32clipboard
    import win32con

    if not _open_clipboard(win32clipboard):
        return None
    image_only = False
    try:
        for name in _EXCLUDE_FORMATS:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.RegisterClipboardFormat(name)):
                return None
        history = win32clipboard.RegisterClipboardFormat(_HISTORY_FORMAT)
        if win32clipboard.IsClipboardFormatAvailable(history):
            data = win32clipboard.GetClipboardData(history)
            if isinstance(data, (bytes, bytearray)) and len(data) >= 4 and struct.unpack("<I", bytes(data[:4]))[0] == 0:
                return None
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_HDROP):
            paths = [str(path) for path in win32clipboard.GetClipboardData(win32con.CF_HDROP) or ()]
            return ClipItem("files", "\n".join(paths)) if paths else None
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            text = str(win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) or "")
            if not text.strip():
                return None
            text = text[:_MAX_TEXT_CHARS]
            return ClipItem("text", text, masked=looks_secret(text))
        image_only = bool(win32clipboard.IsClipboardFormatAvailable(win32con.CF_DIB))
    finally:
        win32clipboard.CloseClipboard()
    if not image_only:
        return None
    try:
        from PIL import ImageGrab

        image = ImageGrab.grabclipboard()
    except Exception:
        return None
    if image is None or not hasattr(image, "size") or image.size[0] * image.size[1] > _MAX_IMAGE_PIXELS:
        return None
    return ClipItem("image", image=image.convert("RGBA"))


def write_clipboard(item: ClipItem) -> bool:
    """Put an item back on the clipboard as its own kind."""
    import win32clipboard
    import win32con

    if item.kind == "image" and item.image is not None:
        buffer = io.BytesIO()
        item.image.convert("RGB").save(buffer, "BMP")
        fmt, data = win32con.CF_DIB, buffer.getvalue()[14:]      # a DIB is a BMP without its file header
    elif item.kind == "files":
        paths = [path for path in item.text.split("\n") if path]
        names = ("\0".join(paths) + "\0\0").encode("utf-16-le")
        fmt, data = win32con.CF_HDROP, struct.pack("<IiiII", 20, 0, 0, 0, 1) + names   # DROPFILES, wide names
    else:
        fmt, data = win32con.CF_UNICODETEXT, item.text
    if not _open_clipboard(win32clipboard):
        return False
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(fmt, data)
        return True
    except Exception:
        return False
    finally:
        win32clipboard.CloseClipboard()


class ClipboardHistory:
    """The newest-first history; start() must run on the thread that pumps window messages."""

    def __init__(self, *, limit: int = DEFAULT_LIMIT, on_change: Callable[[], None] | None = None,
                 reader: Callable[[], ClipItem | None] = read_clipboard,
                 writer: Callable[[ClipItem], bool] = write_clipboard,
                 schedule: Callable[[float, Callable[[], None]], Any] | None = None) -> None:
        self._items: list[ClipItem] = []
        self._schedule = schedule
        self._capture_pending = False
        self._lock = threading.Lock()
        self.limit = max(0, min(MAX_LIMIT, int(limit)))
        self._on_change = on_change
        self._reader = reader
        self._writer = writer
        self._hwnd = 0
        self._wndproc: Any = None
        self._class_name = ""

    @property
    def listening(self) -> bool:
        return bool(self._hwnd)

    def start(self) -> bool:
        if self._hwnd or not self.limit or sys.platform != "win32":
            return bool(self._hwnd)
        import win32api
        import win32gui

        def wndproc(hwnd: int, message: int, wparam: int, lparam: int) -> int:
            if message == WM_CLIPBOARDUPDATE:
                self._on_update()
                return 0
            return win32gui.DefWindowProc(hwnd, message, wparam, lparam)

        self._class_name = f"MOClipboardListener{uuid.uuid4().hex[:8]}"
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = wndproc
        wc.lpszClassName = self._class_name
        wc.hInstance = win32api.GetModuleHandle(None)
        try:
            win32gui.RegisterClass(wc)
            hwnd = win32gui.CreateWindowEx(0, self._class_name, "MO clipboard", 0, 0, 0, 0, 0,
                                           -3, 0, wc.hInstance, None)          # HWND_MESSAGE: message-only
        except Exception:
            return False
        if not ctypes.windll.user32.AddClipboardFormatListener(hwnd):
            win32gui.DestroyWindow(hwnd)
            return False
        self._wndproc, self._hwnd = wndproc, int(hwnd)
        return True

    def stop(self) -> None:
        hwnd, self._hwnd = self._hwnd, 0
        if not hwnd:
            return
        try:
            import win32api
            import win32gui

            ctypes.windll.user32.RemoveClipboardFormatListener(hwnd)
            win32gui.DestroyWindow(hwnd)
            win32gui.UnregisterClass(self._class_name, win32api.GetModuleHandle(None))
        except Exception:
            pass
        self._wndproc = None

    def set_limit(self, limit: int) -> bool:
        """Settings: a new size; 0 stops listening and forgets everything."""
        self.limit = max(0, min(MAX_LIMIT, int(limit)))
        if not self.limit:
            self.stop()
            self.clear()
            return True
        with self._lock:
            del self._items[self.limit:]
        return self.start()

    def _on_update(self) -> None:
        """Windows reported a change: read it CAPTURE_DELAY_MS later, once for a burst of changes."""
        if self._schedule is None:
            self.capture()
            return
        if self._capture_pending:
            return
        self._capture_pending = True

        def run() -> None:
            self._capture_pending = False
            self.capture()

        try:
            self._schedule(CAPTURE_DELAY_MS, run)
        except Exception:
            run()

    def capture(self) -> ClipItem | None:
        try:
            item = self._reader()
        except Exception:
            item = None
        if item is None:
            return None
        if not item.source:
            item.source = _foreground_app()
        self.add(item)
        return item

    def add(self, item: ClipItem) -> None:
        """Newest first; copying something already listed moves it to the top."""
        if not self.limit:
            return
        with self._lock:
            self._items = [existing for existing in self._items if existing.digest != item.digest]
            self._items.insert(0, item)
            del self._items[self.limit:]
        self._changed()

    def items(self) -> list[ClipItem]:
        with self._lock:
            return list(self._items)

    def get(self, ident: str) -> ClipItem | None:
        return next((item for item in self.items() if item.ident == ident), None)

    def restore(self, ident: str) -> bool:
        item = self.get(ident)
        return bool(item is not None and self._writer(item))

    def remove(self, ident: str) -> bool:
        with self._lock:
            before = len(self._items)
            self._items = [item for item in self._items if item.ident != ident]
            removed = len(self._items) != before
        if removed:
            self._changed()
        return removed

    def clear(self) -> None:
        with self._lock:
            self._items = []
        self._changed()

    def view_rows(self, now: float | None = None) -> list[dict[str, Any]]:
        """Rows for the panel's list view: what it is, where from, how long ago."""
        moment = time.time() if now is None else now
        rows = []
        for index, item in enumerate(self.items()):
            age = _age(moment - item.copied_at)
            source = f" · {item.source}" if item.source else ""
            if item.masked:
                title, detail = "•••••••• (looks like a secret, hidden)", f"Text · masked · never sent to MO · {age}"
            elif item.kind == "image":
                width, height = item.image.size
                title, detail = "Image", f"Image · {width} × {height}{source} · {age}"
            elif item.kind == "files":
                paths = item.text.split("\n")
                title = paths[0] if len(paths) == 1 else f"{paths[0]}  +{len(paths) - 1} more"
                detail = f"{'File' if len(paths) == 1 else 'Files'} · {len(paths)} path{'s' if len(paths) > 1 else ''}{source} · {age}"
            else:
                title, detail = " ".join(item.text.split())[:200], f"Text{source} · {age}"
            row = {"name": item.ident, "title": title, "detail": detail, "current": index == 0,
                   "actions": ("remove",) if item.masked else ("ask", "remove")}
            if item.kind == "image":
                thumb = item.image.copy()
                thumb.thumbnail(_THUMB_SIZE)
                row["thumbnail"] = thumb
            rows.append(row)
        return rows

    def _changed(self) -> None:
        callback = self._on_change
        if callable(callback):
            try:
                callback()
            except Exception:
                pass
