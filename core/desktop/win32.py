"""Shared Win32 window helpers.

``ctypes`` is resolved on first use and every helper is a no-op off Windows, so importing
this module stays as cheap as the package promises.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000


@dataclass(frozen=True)
class NativeWindow:
    handle: int
    process_id: int
    title: str
    class_name: str
    bounds: tuple[int, int, int, int]


def top_level_windows() -> tuple[NativeWindow, ...]:
    """Return visible top-level HWND metadata without importing UI Automation."""
    if sys.platform != "win32":
        return ()
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.EnumWindows.argtypes = [
            ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM),
            wintypes.LPARAM,
        ]
        user32.EnumWindows.restype = wintypes.BOOL
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
        user32.GetWindowTextLengthW.restype = ctypes.c_int
        user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetWindowTextW.restype = ctypes.c_int
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.GetClassNameW.restype = ctypes.c_int
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL

        rows: list[NativeWindow] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def collect(hwnd: int, _lparam: int) -> bool:
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                rect = wintypes.RECT()
                if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    return True
                bounds = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
                if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
                    return True
                process_id = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
                title_length = max(0, int(user32.GetWindowTextLengthW(hwnd)))
                title_buffer = ctypes.create_unicode_buffer(title_length + 1)
                user32.GetWindowTextW(hwnd, title_buffer, len(title_buffer))
                class_buffer = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
                rows.append(NativeWindow(
                    handle=int(hwnd),
                    process_id=int(process_id.value),
                    title=str(title_buffer.value or ""),
                    class_name=str(class_buffer.value or ""),
                    bounds=bounds,
                ))
            except Exception:
                pass
            return True

        if not user32.EnumWindows(collect, 0):
            return ()
        return tuple(rows)
    except Exception:
        return ()


def window_process_id(handle: int) -> int:
    """Return the process that owns one window handle now, or 0 if it is gone."""
    if sys.platform != "win32" or int(handle or 0) <= 0:
        return 0
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindow.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        if not user32.IsWindow(int(handle)):
            return 0
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(int(handle), ctypes.byref(process_id))
        return int(process_id.value)
    except Exception:
        return 0


def foreground_window_handle() -> int:
    """Return the top-level foreground HWND, or zero when it is unavailable."""
    if sys.platform != "win32":
        return 0
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = int(user32.GetForegroundWindow() or 0)
        return int(user32.GetAncestor(hwnd, 2) or hwnd) if hwnd else 0
    except Exception:
        return 0


def apply_layered_exstyle(hwnd: int, *, click_through: bool = False) -> bool:
    """Mark a window layered + tool-window, optionally letting clicks pass through it.

    ``WS_EX_LAYERED`` is what allows per-pixel alpha (``UpdateLayeredWindow``) or a plain
    alpha level; ``WS_EX_TOOLWINDOW`` keeps the surface out of the taskbar and Alt-Tab.
    ``WS_EX_TRANSPARENT`` makes a passive surface ignore the mouse entirely — correct for an
    overlay that only shows something, wrong for one you can click.

    Returns False when the style could not be applied, so a caller can fall back.
    """
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(int(hwnd), GWL_EXSTYLE) | WS_EX_LAYERED | WS_EX_TOOLWINDOW
        if click_through:
            style |= WS_EX_TRANSPARENT
        user32.SetWindowLongW(int(hwnd), GWL_EXSTYLE, style)
        return True
    except Exception:
        return False


VK_LBUTTON = 0x01
VK_RBUTTON = 0x02


def request_power_action(action: str) -> None:
    """Execute an explicit local power choice; callers own confirmation and delay.

    Shutdown uses zero Windows timeout and never forces applications closed.
    A positive shutdown.exe timeout implies /f, so the UI owns its cancellable
    countdown and calls this boundary only when that countdown has elapsed.
    """
    if action not in {"sleep", "restart", "shutdown"}:
        raise ValueError("Unsupported power action")
    if sys.platform != "win32":
        raise OSError("Machine power controls require Windows")
    if action != "sleep":
        import subprocess
        from pathlib import Path
        import win32api
        from core.runtime.subprocess_flags import apply_windows_hidden_process_flags
        subprocess.run(
            [str(Path(win32api.GetSystemDirectory()) / "shutdown.exe"),
             "/r" if action == "restart" else "/s", "/t", "0"],
            **apply_windows_hidden_process_flags({"check": True, "timeout": 10,
                                                   "capture_output": True}),
        )
        return
    import ctypes
    import win32api
    import win32con
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(),
        win32con.TOKEN_ADJUST_PRIVILEGES | win32con.TOKEN_QUERY)
    previous = None
    try:
        privilege = win32security.LookupPrivilegeValue(None, win32security.SE_SHUTDOWN_NAME)
        previous = win32security.AdjustTokenPrivileges(token, False,
            [(privilege, win32con.SE_PRIVILEGE_ENABLED)])
        if win32api.GetLastError():
            raise OSError("Windows did not grant the sleep privilege")
        suspend = ctypes.WinDLL("PowrProf", use_last_error=True).SetSuspendState
        suspend.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ubyte]
        suspend.restype = ctypes.c_ubyte
        if not suspend(False, False, False):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if previous is not None:
            win32security.AdjustTokenPrivileges(token, False, previous)
        token.Close()


def minimize_window(handle: int) -> bool:
    """Minimize one native top-level window and verify the resulting iconic state."""
    if sys.platform != "win32" or int(handle or 0) <= 0:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.ShowWindow(int(handle), 6)  # SW_MINIMIZE; application remains in the taskbar.
        return bool(user32.IsIconic(int(handle)))
    except Exception:
        return False


def mouse_button_held(buttons: tuple[int, ...] = (VK_LBUTTON, VK_RBUTTON)) -> bool:
    """Is one of these mouse buttons down right now? ``GetAsyncKeyState``'s high bit is live state.

    The low bit latches a press since the last read, so it must not be used here: two callers
    polling would steal each other's latch.
    """
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        u32 = ctypes.windll.user32
        return any(u32.GetAsyncKeyState(vk) & 0x8000 for vk in buttons)
    except Exception:
        return False
