"""MO Desktop — screen brightness: Shift + wheel over the cubes (beside volume's plain wheel).

The display under the cubes is the target. A built-in panel that Windows itself drives
(WMI ``WmiMonitorBrightness``, matched by the display's own hardware id) gets its real
brightness, so the laptop's keys and MO always agree. Any other display (HDMI,
DisplayPort) gets MO's own dim layer on that display only: black, click-through,
excluded from screen capture (MO's computer use still sees the real screen) and placed
just below the cubes so MO stays bright above it.

Lazy and graceful like ``volume``: pieces that are unavailable return None. Call from the
GUI thread.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

DIM_STEP = 0.05
DIM_MAX = 0.9
BRIGHTNESS_STEP = 5
_PANEL_CACHE_SECONDS = 30.0
_SAVE_DELAY_MS = 1500

_dim_level = 0.0
_layer: Any = None
_layer_rect: tuple[int, int, int, int] | None = None
_save: Callable[[float], Any] | None = None
_schedule: Callable[[float, Callable[[], None]], Any] | None = None
_save_pending = False
_panels: tuple[float, dict[str, str]] = (0.0, {})


def configure(*, level: float = 0.0, save: Callable[[float], Any] | None = None,
              schedule: Callable[[float, Callable[[], None]], Any] | None = None) -> None:
    """Desktop start: the saved dim level and how to persist a changed one."""
    global _dim_level, _save, _schedule
    _dim_level = max(0.0, min(DIM_MAX, float(level or 0.0)))
    _save, _schedule = save, schedule


def dim_level() -> float:
    return _dim_level


def set_dim_level(level: float, x: int, y: int, *, anchor_hwnd: int = 0) -> bool:
    """Settings: a new dim level, shown at once on the display at ``x, y`` (never a panel)."""
    global _dim_level
    _dim_level = max(0.0, min(DIM_MAX, float(level or 0.0)))
    if _dim_level <= 0:
        clear_dim()
        return True
    return restore(x, y, anchor_hwnd=anchor_hwnd)


def display_at(x: int, y: int) -> tuple[str, tuple[int, int, int, int]] | None:
    """The display (device name, monitor rect) holding screen point ``x, y``."""
    try:
        import win32api

        info = win32api.GetMonitorInfo(win32api.MonitorFromPoint((int(x), int(y)), 2))
        return str(info["Device"]), tuple(int(v) for v in info["Monitor"])
    except Exception:
        return None


def hardware_id(device: str) -> str:
    """The active monitor's hardware id on ``device`` (e.g. ``SAM0FE0``), or ``""``."""
    try:
        import win32api

        index = 0
        while True:
            try:
                monitor = win32api.EnumDisplayDevices(device, index, 1)  # EDD_GET_DEVICE_INTERFACE_NAME
            except Exception:
                return ""
            if int(monitor.StateFlags) & 1:  # DISPLAY_DEVICE_ACTIVE
                parts = str(monitor.DeviceID).split("#")
                return parts[1].upper() if len(parts) > 1 else ""
            index += 1
    except Exception:
        return ""


def _wmi() -> Any:
    import comtypes.client

    return comtypes.client.CoGetObject("winmgmts:root\\wmi")


def _panel_instances() -> dict[str, str]:
    """Hardware id -> WMI instance for every panel whose brightness Windows drives."""
    global _panels
    checked, panels = _panels
    if time.monotonic() - checked < _PANEL_CACHE_SECONDS:
        return panels
    panels = {}
    try:
        for row in _wmi().ExecQuery("SELECT InstanceName FROM WmiMonitorBrightness"):
            instance = str(row.Properties_.Item("InstanceName").Value or "")
            parts = instance.split("\\")
            if len(parts) > 1:
                panels[parts[1].upper()] = instance
    except Exception:
        panels = {}
    _panels = (time.monotonic(), panels)
    return panels


def _panel_brightness(instance: str) -> Optional[int]:
    try:
        query = f"SELECT CurrentBrightness FROM WmiMonitorBrightness WHERE InstanceName='{_wql(instance)}'"
        for row in _wmi().ExecQuery(query):
            return int(row.Properties_.Item("CurrentBrightness").Value)
    except Exception:
        return None
    return None


def _set_panel_brightness(instance: str, value: int) -> bool:
    try:
        query = f"SELECT * FROM WmiMonitorBrightnessMethods WHERE InstanceName='{_wql(instance)}'"
        for row in _wmi().ExecQuery(query):
            params = row.Methods_.Item("WmiSetBrightness").InParameters.SpawnInstance_()
            params.Properties_.Item("Timeout").Value = 0
            params.Properties_.Item("Brightness").Value = int(value)
            row.ExecMethod_("WmiSetBrightness", params)
            return True
    except Exception:
        return False
    return False


def _wql(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def nudge(x: int, y: int, step: int, *, anchor_hwnd: int = 0) -> Optional[str]:
    """One wheel notch (``step`` +1 brighter / -1 darker) for the display at ``x, y``.

    Returns the cube label text ("brightness 60%" or "dim 30%"), or None if unavailable.
    """
    display = display_at(x, y)
    if display is None:
        return None
    device, rect = display
    instance = _panel_instances().get(hardware_id(device))
    if instance:
        current = _panel_brightness(instance)
        if current is None:
            return None
        value = max(0, min(100, current + (BRIGHTNESS_STEP if step > 0 else -BRIGHTNESS_STEP)))
        return f"brightness  {value}%" if _set_panel_brightness(instance, value) else None
    global _dim_level
    _dim_level = round(max(0.0, min(DIM_MAX, _dim_level + (-DIM_STEP if step > 0 else DIM_STEP))), 2)
    if not apply_dim(rect, anchor_hwnd=anchor_hwnd):
        return None
    _schedule_save()
    return f"dim  {round(_dim_level * 100)}%"


def restore(x: int, y: int, *, anchor_hwnd: int = 0) -> bool:
    """Desktop start: bring back the saved dim on the display at ``x, y`` (never a panel)."""
    display = display_at(x, y)
    if display is None or _panel_instances().get(hardware_id(display[0])):
        return False
    return apply_dim(display[1], anchor_hwnd=anchor_hwnd)


def apply_dim(rect: tuple[int, int, int, int], *, anchor_hwnd: int = 0) -> bool:
    """Show the dim layer over ``rect`` at the current level (none at 0)."""
    global _layer, _layer_rect
    if _dim_level <= 0:
        clear_dim()
        return True
    try:
        from PIL import Image
        from mo_desktop.layered import NativeLayeredWindow

        if _layer is None:
            _layer = NativeLayeredWindow(int(anchor_hwnd or 0), title="MO Desktop — Dim")
            _layer.exclude_from_capture(True)
            _layer_rect = None
        # Below EVERY visible MO surface (panel, Focus, labels), not only the cube window:
        # anything of MO's under the cube would otherwise be dimmed too.
        _layer._target_hwnd = lowest_own_window(exclude=int(_layer.hwnd)) or int(anchor_hwnd or 0)
        opacity = round(_dim_level * 255)
        left, top, right, bottom = rect
        if rect != _layer_rect:
            black = Image.new("RGBA", (max(1, right - left), max(1, bottom - top)), (0, 0, 0, 255))
            if not _layer.blit(black, left, top, premultiplied=True, opacity=opacity):
                return False
            _layer_rect = rect
        elif not (_layer.set_opacity(opacity) and _layer.place_behind(left, top, right - left, bottom - top)):
            return False
        return keep_in_top_band(_layer, rect)
    except Exception:
        return False


def keep_in_top_band(layer: Any, rect: tuple[int, int, int, int]) -> bool:
    """An MO backdrop (the dim layer, the pointing spotlight) must stay in the always-on-top band
    (or an app clicked to the front would rise above it), yet below MO's own surfaces; re-assert
    both if the placement did not hold."""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        if not user32.GetWindowLongW(layer.hwnd, -20) & 0x00000008:
            from mo_desktop.layered import _winapi

            _winapi().user32.SetWindowPos(layer.hwnd, -1, 0, 0, 0, 0, 0x0013)  # TOPMOST, no move/size/activate
            left, top, right, bottom = rect
            layer.place_behind(left, top, right - left, bottom - top)
        return bool(user32.GetWindowLongW(layer.hwnd, -20) & 0x00000008)
    except Exception:
        return True


def lowest_own_window(*, exclude: int = 0) -> int:
    """The bottom-most visible always-on-top window of this process, or 0."""
    try:
        import ctypes
        import os
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetWindow.restype = wintypes.HWND
        user32.GetTopWindow.restype = wintypes.HWND
        own, lowest = os.getpid(), 0
        hwnd = user32.GetTopWindow(None)
        while hwnd:
            if not user32.GetWindowLongW(hwnd, -20) & 0x00000008:  # WS_EX_TOPMOST band ends
                break
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            value = int(hwnd)
            if pid.value == own and value != exclude and user32.IsWindowVisible(hwnd):
                lowest = value
            hwnd = user32.GetWindow(hwnd, 2)  # GW_HWNDNEXT
        return lowest
    except Exception:
        return 0


def clear_dim() -> None:
    global _layer, _layer_rect
    if _layer is not None:
        try:
            _layer.destroy()
        except Exception:
            pass
    _layer, _layer_rect = None, None


def _schedule_save() -> None:
    """Persist the level once the wheel has been still for a moment, not every notch."""
    global _save_pending
    if _save is None or _save_pending:
        return
    if _schedule is None:
        _save(_dim_level)
        return
    _save_pending = True

    def flush() -> None:
        global _save_pending
        _save_pending = False
        if _save is not None:
            _save(_dim_level)

    _schedule(_SAVE_DELAY_MS, flush)
