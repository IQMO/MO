"""Read the one live window selected by this MO Shell instance."""
from __future__ import annotations

import os


SHELL_HOST_HWND_ENV = "MO_SHELL_HOST_HWND"
ATTACHED_WINDOW_PROPERTY = "MO_SHELL_ATTACHED_WINDOW_V1"


def current_shell_attachment_title() -> str:
    """Return the current attached window title without persisting HWND state."""
    raw_host = os.environ.get(SHELL_HOST_HWND_ENV, "").strip()
    if os.name != "nt" or not raw_host:
        return ""
    try:
        host = int(raw_host)
    except ValueError:
        return ""
    if host <= 0:
        return ""

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user32.GetPropW.restype = wintypes.HANDLE
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int

    if not user32.IsWindow(host):
        return ""
    target = int(user32.GetPropW(host, ATTACHED_WINDOW_PROPERTY) or 0)
    if target <= 0 or not user32.IsWindow(target):
        return ""
    length = min(512, max(0, int(user32.GetWindowTextLengthW(target))))
    if length <= 0:
        return ""
    title = ctypes.create_unicode_buffer(length + 1)
    if user32.GetWindowTextW(target, title, len(title)) <= 0:
        return ""
    return " ".join(title.value.split())[:240]


__all__ = [
    "ATTACHED_WINDOW_PROPERTY",
    "SHELL_HOST_HWND_ENV",
    "current_shell_attachment_title",
]
