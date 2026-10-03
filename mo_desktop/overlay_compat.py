"""Bounded compatibility for approved third-party always-on-top overlays.

MO's native layered surfaces already live in Windows' topmost band. Some dimmers and
accessibility overlays periodically move themselves to the front of that same band,
placing the cube underneath even though both windows are topmost. This module listens
for external window reorders and restores MO as one non-activating batch only when an
exact, user-configured executable moves above it.

The allowlist is empty by default. Imports stay light and every Win32 binding is deferred
until the first configured check.
"""
from __future__ import annotations

from dataclasses import dataclass
import ntpath
import os
import sys
import time
from types import SimpleNamespace
from typing import Any, Callable, Iterable

from mo_desktop.settings import normalize_keep_above_apps


_FALLBACK_CHECK_SECONDS = 2.0
_PROCESS_NAME_CACHE_SECONDS = 5.0
_WS_EX_TOPMOST = 0x00000008
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_NOOWNERZORDER = 0x0200
_HWND_TOPMOST = -1
_GA_ROOT = 2
_EVENT_OBJECT_SHOW = 0x8002
_EVENT_OBJECT_REORDER = 0x8004
_OBJID_WINDOW = 0
_WINEVENT_OUTOFCONTEXT = 0x0000
_WINEVENT_SKIPOWNPROCESS = 0x0002
_WINAPI: Any = None


@dataclass(frozen=True)
class WindowOrderEntry:
    hwnd: int
    pid: int
    topmost: bool


def _winapi() -> Any:
    global _WINAPI
    if _WINAPI is not None:
        return _WINAPI

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    event_proc = ctypes.WINFUNCTYPE(
        None,
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.HWND,
        wintypes.LONG,
        wintypes.LONG,
        wintypes.DWORD,
        wintypes.DWORD,
    )

    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.SetWindowPos.restype = wintypes.BOOL
    user32.BeginDeferWindowPos.argtypes = [ctypes.c_int]
    user32.BeginDeferWindowPos.restype = wintypes.HANDLE
    user32.DeferWindowPos.argtypes = [
        wintypes.HANDLE, wintypes.HWND, wintypes.HWND,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    user32.DeferWindowPos.restype = wintypes.HANDLE
    user32.EndDeferWindowPos.argtypes = [wintypes.HANDLE]
    user32.EndDeferWindowPos.restype = wintypes.BOOL
    user32.SetWinEventHook.argtypes = [
        wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, event_proc,
        wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
    ]
    user32.SetWinEventHook.restype = wintypes.HANDLE
    user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
    user32.UnhookWinEvent.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    _WINAPI = SimpleNamespace(
        ctypes=ctypes,
        wintypes=wintypes,
        user32=user32,
        kernel32=kernel32,
        enum_proc=enum_proc,
        event_proc=event_proc,
    )
    return _WINAPI


def top_level_hwnd(win: Any) -> int:
    """Resolve a Tk child or native app handle to its top-level HWND."""
    if sys.platform != "win32" or win is None:
        return 0
    try:
        child = win if isinstance(win, int) else int(win.hwnd if hasattr(win, "hwnd") else win.winfo_id())
        root = int(_winapi().user32.GetAncestor(child, _GA_ROOT) or 0)
        return root or child
    except Exception:
        return 0


def _window_order_entry(hwnd: int) -> WindowOrderEntry | None:
    if sys.platform != "win32" or not hwnd:
        return None
    try:
        api = _winapi()
        if not api.user32.IsWindowVisible(hwnd):
            return None
        owner = api.wintypes.DWORD()
        api.user32.GetWindowThreadProcessId(hwnd, api.ctypes.byref(owner))
        style = int(api.user32.GetWindowLongW(hwnd, -20))
        return WindowOrderEntry(int(hwnd), int(owner.value), bool(style & _WS_EX_TOPMOST))
    except Exception:
        return None


def windows_in_z_order() -> list[WindowOrderEntry]:
    """Return visible top-level windows in Windows' top-to-bottom order."""
    if sys.platform != "win32":
        return []
    try:
        api = _winapi()
        rows: list[WindowOrderEntry] = []

        @api.enum_proc
        def collect(hwnd: int, _lparam: int) -> bool:
            row = _window_order_entry(int(hwnd))
            if row is not None:
                rows.append(row)
            return True

        api.user32.EnumWindows(collect, 0)
        return rows
    except Exception:
        return []


def process_executable_name(pid: int) -> str:
    """Return one process executable basename without retaining or reporting its path."""
    if sys.platform != "win32" or pid <= 0:
        return ""
    handle = None
    try:
        api = _winapi()
        handle = api.kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return ""
        size = api.wintypes.DWORD(32768)
        buffer = api.ctypes.create_unicode_buffer(size.value)
        if not api.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, api.ctypes.byref(size)):
            return ""
        return ntpath.basename(buffer.value)
    except Exception:
        return ""
    finally:
        if handle:
            try:
                _winapi().kernel32.CloseHandle(handle)
            except Exception:
                pass


def _matching_overlay_above(
    rows: Iterable[WindowOrderEntry],
    own_hwnds: set[int],
    allowed: set[str],
    name_for_pid: Callable[[int], str],
) -> str:
    """Return the configured executable above a later MO surface, if any."""
    matching_above = ""
    own_pid = os.getpid()
    names: dict[int, str] = {}
    for row in rows:
        if row.hwnd in own_hwnds:
            if matching_above:
                return matching_above
            continue
        if not row.topmost or row.pid <= 0 or row.pid == own_pid:
            continue
        if row.pid not in names:
            names[row.pid] = str(name_for_pid(row.pid) or "")
        name = names[row.pid]
        if name.casefold() in allowed:
            matching_above = name
    return ""


def _visible_top_level_hwnds(windows: Iterable[Any]) -> tuple[int, ...]:
    handles: list[int] = []
    for win in windows:
        try:
            if isinstance(win, int):
                if not win or not _winapi().user32.IsWindowVisible(win):
                    continue
            elif hasattr(win, "hwnd"):
                if not win.is_visible():
                    continue
            elif win is None or not bool(win.winfo_exists()) or not bool(win.winfo_viewable()):
                continue
        except Exception:
            continue
        hwnd = top_level_hwnd(win)
        if hwnd and hwnd not in handles:
            handles.append(hwnd)
    return tuple(handles)


def _raise_hwnds_topmost(hwnds: Iterable[int]) -> bool:
    """Raise visible MO HWNDs as one non-activating Z-order update."""
    if sys.platform != "win32":
        return False
    try:
        api = _winapi()
        visible = [int(hwnd) for hwnd in hwnds if hwnd and api.user32.IsWindowVisible(hwnd)]
        if not visible:
            return False
        flags = _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE | _SWP_NOOWNERZORDER
        batch = api.user32.BeginDeferWindowPos(len(visible))
        if batch:
            for hwnd in visible:
                batch = api.user32.DeferWindowPos(
                    batch, hwnd, _HWND_TOPMOST, 0, 0, 0, 0, flags,
                )
                if not batch:
                    break
            if batch and api.user32.EndDeferWindowPos(batch):
                return True
        lifted = False
        for hwnd in visible:
            if api.user32.SetWindowPos(hwnd, _HWND_TOPMOST, 0, 0, 0, 0, flags):
                lifted = True
        return lifted
    except Exception:
        return False


class OverlayCompatibility:
    """Keep MO above exact approved overlay executables without periodic Z-order fighting."""

    def __init__(self, executable_names: Any = None) -> None:
        self._allowed: set[str] = set()
        self._last_check_at = 0.0
        self._process_names: dict[int, tuple[str, float]] = {}
        self._own_hwnds: tuple[int, ...] = ()
        self._event_hook: Any = None
        self._event_callback: Any = None
        self._pending_match = ""
        self.set_executable_names(executable_names)

    @property
    def executable_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._allowed))

    @property
    def event_driven(self) -> bool:
        return bool(self._event_hook)

    def set_executable_names(self, value: Any) -> None:
        self._allowed = {name.casefold() for name in normalize_keep_above_apps(value)}
        self._last_check_at = 0.0
        self._process_names.clear()
        self._pending_match = ""
        if not self._allowed:
            self.close()

    def _name_for_pid(self, pid: int) -> str:
        current = time.monotonic()
        cached = self._process_names.get(pid)
        if cached is None or current - cached[1] >= _PROCESS_NAME_CACHE_SECONDS:
            cached = (process_executable_name(pid), current)
            self._process_names[pid] = cached
        return cached[0]

    def _handle_window_event(self, event: int, hwnd: int, object_id: int) -> None:
        if event not in {_EVENT_OBJECT_SHOW, _EVENT_OBJECT_REORDER} or object_id != _OBJID_WINDOW:
            return
        row = _window_order_entry(hwnd)
        if row is None or not row.topmost or row.pid == os.getpid() or row.hwnd in self._own_hwnds:
            return
        name = self._name_for_pid(row.pid)
        if name.casefold() not in self._allowed:
            return
        if _raise_hwnds_topmost(self._own_hwnds):
            self._pending_match = name

    def _install_event_hook(self) -> None:
        if sys.platform != "win32" or self._event_hook or not self._allowed:
            return
        try:
            api = _winapi()

            @api.event_proc
            def callback(
                _hook: Any,
                event: int,
                hwnd: int,
                object_id: int,
                _child_id: int,
                _thread_id: int,
                _event_time: int,
            ) -> None:
                try:
                    self._handle_window_event(int(event), int(hwnd or 0), int(object_id))
                except Exception:
                    return

            hook = api.user32.SetWinEventHook(
                _EVENT_OBJECT_SHOW,
                _EVENT_OBJECT_REORDER,
                None,
                callback,
                0,
                0,
                _WINEVENT_OUTOFCONTEXT | _WINEVENT_SKIPOWNPROCESS,
            )
            if hook:
                self._event_callback = callback
                self._event_hook = hook
        except Exception:
            self._event_hook = None
            self._event_callback = None

    def maintain(self, windows: Iterable[Any], *, now: float | None = None, force: bool = False) -> str:
        """Refresh MO handles and recover any reorder event missed by the Windows hook."""
        if sys.platform != "win32" or not self._allowed:
            return ""
        self._own_hwnds = _visible_top_level_hwnds(windows)
        if not self._own_hwnds:
            return ""
        self._install_event_hook()
        pending = self._pending_match
        self._pending_match = ""
        current = time.monotonic() if now is None else float(now)
        if pending and not force:
            return pending
        if not force and current - self._last_check_at < _FALLBACK_CHECK_SECONDS:
            return ""
        self._last_check_at = current
        matched = _matching_overlay_above(
            windows_in_z_order(), set(self._own_hwnds), self._allowed, self._name_for_pid,
        )
        if not matched or not _raise_hwnds_topmost(self._own_hwnds):
            return pending
        return matched

    def allowed_foreground(self) -> bool:
        """Whether the foreground app is explicitly configured to keep MO visible."""
        if sys.platform != "win32" or not self._allowed:
            return False
        try:
            api = _winapi()
            hwnd = api.user32.GetForegroundWindow()
            if not hwnd:
                return False
            owner = api.wintypes.DWORD()
            api.user32.GetWindowThreadProcessId(hwnd, api.ctypes.byref(owner))
            return self._name_for_pid(int(owner.value)).casefold() in self._allowed
        except Exception:
            return False

    def close(self) -> None:
        hook = self._event_hook
        self._event_hook = None
        self._event_callback = None
        if hook:
            try:
                _winapi().user32.UnhookWinEvent(hook)
            except Exception:
                pass
