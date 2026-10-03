"""Publish the current Board DOM bounds for native pen targeting."""
from __future__ import annotations

import math
import os
from typing import Any

BOARD_GEOMETRY_PROPERTIES = tuple(
    f"MO_BOARD_GEOMETRY_V1_{name}"
    for name in ("L", "T", "R", "B", "VW", "VH")
)
_COORD_SCALE = 1_000
_VALUE_BIAS = 1


def project_board_rect(
    css_rect: tuple[float, float, float, float],
    viewport: tuple[float, float],
    client: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    left, top, right, bottom = css_rect
    width, height = viewport
    client_left, client_top, client_right, client_bottom = client
    if (
        width <= 0 or height <= 0 or left < 0 or top < 0
        or right > width or bottom > height
        or right - left < 80 or bottom - top < 80
    ):
        return None
    scale_x = (client_right - client_left) / width
    scale_y = (client_bottom - client_top) / height
    result = (
        client_left + round(left * scale_x),
        client_top + round(top * scale_y),
        client_left + round(right * scale_x),
        client_top + round(bottom * scale_y),
    )
    return result if result[2] - result[0] >= 80 and result[3] - result[1] >= 80 else None


def publish_board_rect(
    window: Any,
    css_rect: tuple[float, float, float, float] | None,
    viewport: tuple[float, float],
) -> tuple[int, int, int, int] | None:
    if os.name != "nt" or window is None:
        return None
    import ctypes
    from ctypes import wintypes

    native = getattr(window, "native", None)
    if native is None or bool(getattr(native, "IsDisposed", False)) or bool(getattr(native, "Disposing", False)):
        return None
    raw = getattr(native, "Handle", 0)
    handle = int(raw.ToInt64() if hasattr(raw, "ToInt64") else raw or 0)
    if not handle:
        return None
    user32 = ctypes.windll.user32
    user32.SetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.HANDLE]
    user32.SetPropW.restype = wintypes.BOOL
    user32.RemovePropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user32.RemovePropW.restype = wintypes.HANDLE
    for name in BOARD_GEOMETRY_PROPERTIES:
        user32.RemovePropW(handle, name)
    if css_rect is None:
        return None
    values = (*css_rect, *viewport)
    if (
        not all(math.isfinite(value) for value in values)
        or project_board_rect(
            css_rect,
            viewport,
            (0, 0, round(viewport[0]), round(viewport[1])),
        ) is None
    ):
        return None
    for name, value in zip(BOARD_GEOMETRY_PROPERTIES, values):
        user32.SetPropW(
            handle,
            name,
            round(value * _COORD_SCALE) + _VALUE_BIAS,
        )
    return project_board_rect(
        css_rect,
        viewport,
        (0, 0, round(viewport[0]), round(viewport[1])),
    )


def read_board_rect(handle: int) -> tuple[int, int, int, int] | None:
    if os.name != "nt" or int(handle) <= 0:
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.GetPropW.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
    user32.GetPropW.restype = wintypes.HANDLE
    user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user32.GetClientRect.restype = wintypes.BOOL
    user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    user32.ClientToScreen.restype = wintypes.BOOL
    values = [
        int(user32.GetPropW(int(handle), name) or 0)
        for name in BOARD_GEOMETRY_PROPERTIES
    ]
    if not all(values):
        return None
    decoded = tuple(
        (value - _VALUE_BIAS) / _COORD_SCALE
        for value in values
    )
    css_rect = decoded[:4]
    viewport = decoded[4:]
    rect, origin = wintypes.RECT(), wintypes.POINT()
    if (
        not user32.GetClientRect(int(handle), ctypes.byref(rect))
        or not user32.ClientToScreen(int(handle), ctypes.byref(origin))
    ):
        return None
    client = (origin.x, origin.y, origin.x + rect.right, origin.y + rect.bottom)
    return project_board_rect(css_rect, viewport, client)
