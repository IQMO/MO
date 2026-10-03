"""Windows synthetic-pen injection for the phone Trackpad's Board mode."""
from __future__ import annotations

import ctypes
import math
import os
from ctypes import wintypes
from typing import Any, Callable


PT_TOUCH = 2
PT_PEN = 3
POINTER_FEEDBACK_DEFAULT = 1
POINTER_FLAG_INRANGE = 0x00000002
POINTER_FLAG_INCONTACT = 0x00000004
POINTER_FLAG_CANCELED = 0x00008000
POINTER_FLAG_PRIMARY = 0x00002000
POINTER_FLAG_DOWN = 0x00010000
POINTER_FLAG_UPDATE = 0x00020000
POINTER_FLAG_UP = 0x00040000
PEN_FLAG_BARREL = 0x00000001
PEN_FLAG_INVERTED = 0x00000002
PEN_FLAG_ERASER = 0x00000004
PEN_MASK_PRESSURE = 0x00000001
PEN_MASK_ROTATION = 0x00000002
PEN_MASK_TILT_X = 0x00000004
PEN_MASK_TILT_Y = 0x00000008


class POINTER_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerType", wintypes.DWORD),
        ("pointerId", wintypes.UINT),
        ("frameId", wintypes.UINT),
        ("pointerFlags", wintypes.DWORD),
        ("sourceDevice", wintypes.HANDLE),
        ("hwndTarget", wintypes.HWND),
        ("ptPixelLocation", wintypes.POINT),
        ("ptHimetricLocation", wintypes.POINT),
        ("ptPixelLocationRaw", wintypes.POINT),
        ("ptHimetricLocationRaw", wintypes.POINT),
        ("dwTime", wintypes.DWORD),
        ("historyCount", wintypes.UINT),
        ("InputData", ctypes.c_int32),
        ("dwKeyStates", wintypes.DWORD),
        ("PerformanceCount", ctypes.c_uint64),
        ("ButtonChangeType", wintypes.DWORD),
    ]


class POINTER_TOUCH_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerInfo", POINTER_INFO),
        ("touchFlags", wintypes.DWORD),
        ("touchMask", wintypes.DWORD),
        ("rcContact", wintypes.RECT),
        ("rcContactRaw", wintypes.RECT),
        ("orientation", wintypes.UINT),
        ("pressure", wintypes.UINT),
    ]


class POINTER_PEN_INFO(ctypes.Structure):
    _fields_ = [
        ("pointerInfo", POINTER_INFO),
        ("penFlags", wintypes.DWORD),
        ("penMask", wintypes.DWORD),
        ("pressure", wintypes.UINT),
        ("rotation", wintypes.UINT),
        ("tiltX", ctypes.c_int32),
        ("tiltY", ctypes.c_int32),
    ]


class _POINTER_UNION(ctypes.Union):
    _fields_ = [("touchInfo", POINTER_TOUCH_INFO), ("penInfo", POINTER_PEN_INFO)]


class POINTER_TYPE_INFO(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("type", wintypes.DWORD), ("value", _POINTER_UNION)]


class WindowsPenInjector:
    """Inject bounded PT_PEN packets with pressure/tilt into Windows."""

    def __init__(
        self,
        *,
        target_rect: Callable[[], tuple[int, int, int, int] | None],
    ) -> None:
        if os.name != "nt":
            raise OSError("Windows synthetic pen is unavailable")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        create = getattr(self._user32, "CreateSyntheticPointerDevice", None)
        inject = getattr(self._user32, "InjectSyntheticPointerInput", None)
        destroy = getattr(self._user32, "DestroySyntheticPointerDevice", None)
        if create is None or inject is None or destroy is None:
            raise OSError("Windows synthetic pen APIs are unavailable")
        create.argtypes = [wintypes.DWORD, wintypes.ULONG, wintypes.DWORD]
        create.restype = wintypes.HANDLE
        inject.argtypes = [wintypes.HANDLE, ctypes.POINTER(POINTER_TYPE_INFO), wintypes.UINT]
        inject.restype = wintypes.BOOL
        destroy.argtypes = [wintypes.HANDLE]
        destroy.restype = None
        self._inject = inject
        self._destroy = destroy
        self._device = create(PT_PEN, 1, POINTER_FEEDBACK_DEFAULT)
        if not self._device:
            raise ctypes.WinError(ctypes.get_last_error())
        self._down = False
        self._frame = 0
        self._target_rect = target_rect
        self._last = (0.5, 0.5, 0.0, 0.0, 0.0, 0, False)

    def send(self, message: dict[str, Any]) -> None:
        samples = list(message.get("history") or [])
        for sample in samples:
            if not isinstance(sample, list) or len(sample) < 5:
                continue
            self._inject_sample(
                "move",
                float(sample[0]), float(sample[1]), float(sample[2]),
                float(sample[3]), float(sample[4]),
                int(sample[5]) if len(sample) > 5 else 0,
                eraser=bool(message.get("eraser")),
            )
        self._inject_sample(
            str(message.get("phase") or "move"),
            float(message["x"]), float(message["y"]), float(message.get("pressure") or 0.0),
            float(message.get("tilt") or 0.0), float(message.get("orientation") or 0.0),
            int(message.get("buttons") or 0), eraser=bool(message.get("eraser")),
        )

    def release_all(self) -> None:
        if self._down:
            try:
                x, y, pressure, tilt, orientation, buttons, eraser = self._last
                self._inject_sample(
                    "cancel", x, y, pressure, tilt, orientation, buttons, eraser=eraser
                )
            except OSError:
                self._down = False

    def close(self) -> None:
        self.release_all()
        device, self._device = self._device, None
        if device:
            self._destroy(device)

    def _inject_sample(
        self,
        phase: str,
        x: float,
        y: float,
        pressure: float,
        tilt: float,
        orientation: float,
        buttons: int,
        *,
        eraser: bool,
    ) -> None:
        if not self._device:
            raise OSError("Windows synthetic pen device is closed")
        target = self._target_rect()
        if target is None:
            raise OSError("MO Design Board is not the foreground target")
        left, top, right, bottom = target
        px = left + round(max(0.0, min(1.0, x)) * max(0, right - left - 1))
        py = top + round(max(0.0, min(1.0, y)) * max(0, bottom - top - 1))
        selected = phase.strip().lower()
        if selected == "down":
            flags = POINTER_FLAG_INRANGE | POINTER_FLAG_INCONTACT | POINTER_FLAG_PRIMARY | POINTER_FLAG_DOWN
            self._down = True
        elif selected in {"up", "cancel"}:
            flags = POINTER_FLAG_INRANGE | POINTER_FLAG_PRIMARY | POINTER_FLAG_UP
            if selected == "cancel":
                flags |= POINTER_FLAG_CANCELED
            self._down = False
        elif selected == "hover":
            flags = POINTER_FLAG_INRANGE | POINTER_FLAG_PRIMARY | POINTER_FLAG_UPDATE
        else:
            flags = POINTER_FLAG_INRANGE | POINTER_FLAG_PRIMARY | POINTER_FLAG_UPDATE
            if self._down:
                flags |= POINTER_FLAG_INCONTACT
        magnitude = math.degrees(max(0.0, min(math.pi / 2, tilt)))
        tilt_x = round(math.sin(orientation) * magnitude)
        tilt_y = round(-math.cos(orientation) * magnitude)
        rotation = round((math.degrees(orientation) % 360.0))
        pen_flags = 0
        if eraser:
            # WebView exposes the standard eraser button inconsistently for
            # synthetic pens. Barrel is a second native pen signal that maps
            # to PointerEvent.buttons, so the trusted Board can distinguish
            # an eraser tip without a private side channel.
            pen_flags |= PEN_FLAG_BARREL | PEN_FLAG_INVERTED | PEN_FLAG_ERASER
        # Preserve phone button state in the wire contract, but do not assign a
        # Windows/S Pen button behavior before the separate Trackpad settings
        # review. The explicit eraser tool type above remains supported.
        self._frame += 1
        info = POINTER_TYPE_INFO()
        info.type = PT_PEN
        pen = info.penInfo
        pen.pointerInfo.pointerType = PT_PEN
        pen.pointerInfo.pointerId = 1
        pen.pointerInfo.frameId = self._frame
        pen.pointerInfo.pointerFlags = flags
        pen.pointerInfo.ptPixelLocation = wintypes.POINT(px, py)
        pen.pointerInfo.ptPixelLocationRaw = wintypes.POINT(px, py)
        pen.penFlags = pen_flags
        pen.penMask = PEN_MASK_PRESSURE | PEN_MASK_ROTATION | PEN_MASK_TILT_X | PEN_MASK_TILT_Y
        pen.pressure = round(max(0.0, min(1.0, pressure)) * 1024)
        pen.rotation = rotation
        pen.tiltX = max(-90, min(90, tilt_x))
        pen.tiltY = max(-90, min(90, tilt_y))
        if not self._inject(self._device, ctypes.byref(info), 1):
            raise ctypes.WinError(ctypes.get_last_error())
        self._last = (x, y, pressure, tilt, orientation, buttons, eraser)


__all__ = ["WindowsPenInjector"]
