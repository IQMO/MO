"""Native MO Desktop screen/input lane for MO Live Control."""
from __future__ import annotations

import hashlib
import io
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from core.desktop.runtime import bind_target, invalidate_target, record_action, record_observation, validate_action
from core.state.paths import resolve_state_path
from mo_everywhere.live_control import KILL_SWITCH_PATH


MAX_FRAME_WIDTH = 1920
MAX_FRAME_HEIGHT = 1920
MAX_FRAME_BYTES = 512 * 1024
MAX_TEXT_CHARS = 2_000
_KEYS = frozenset({
    "backspace", "delete", "down", "end", "enter", "escape", "home",
    "left", "pagedown", "pageup", "right", "space", "tab", "up",
})
_MODIFIERS = frozenset({"alt", "ctrl", "shift", "win"})


class DesktopScreenLane:
    """Capture one bounded primary display and actuate only its leased target."""

    def __init__(self, config: dict[str, Any], *, on_state: Callable[[str], None] | None = None):
        self.config = config
        self.on_state = on_state
        self._session_id = ""
        self._owner_id = ""
        self._send_event: Callable[[str, dict[str, Any]], bool] | None = None
        self._send_frame: Callable[[str, bytes], bool] | None = None
        self._stop = threading.Event()
        self._client_ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_digest = ""
        self._target: Any = None
        self._observation: Any = None
        self._mouse_down = False
        self._mouse_button = "left"
        self._pressed_keys: set[str] = set()

    def open(self, session_id: str, send_event: Callable[..., bool], send_frame: Callable[..., bool]) -> None:
        self.close(self._session_id)
        self._session_id = session_id
        self._owner_id = f"live-control:{session_id}"
        self._send_event = send_event
        self._send_frame = send_frame
        self._last_digest = ""
        self._stop.clear()
        self._client_ready.clear()
        self._state("active")
        self._thread = threading.Thread(target=self._capture_loop, name="mo-live-screen", daemon=True)
        self._thread.start()

    def receive(self, session_id: str, payload: dict[str, Any]) -> None:
        if session_id != self._session_id or self._disabled():
            return
        kind = str(payload.get("type") or "")
        if kind == "client_connected":
            self._client_ready.set()
            self._last_digest = ""
            return
        if kind == "screen_visibility":
            if payload.get("visible") is True:
                self._last_digest = ""
                self._client_ready.set()
            else:
                self._client_ready.clear()
                self._release_inputs()
            return
        if kind == "pointer":
            self._pointer(payload)
        elif kind == "key":
            self._key(payload)
        elif kind == "text":
            self._text(payload)

    def close(self, session_id: str) -> None:
        if session_id and self._session_id and session_id != self._session_id:
            return
        self._stop.set()
        self._client_ready.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        self._release_inputs()
        if self._owner_id:
            invalidate_target("screen", reason="live_control_closed", owner_id=self._owner_id)
        self._thread = None
        self._session_id = ""
        self._owner_id = ""
        self._send_event = None
        self._send_frame = None
        self._target = None
        self._observation = None
        self._last_digest = ""
        self._state("closed")

    def _capture_loop(self) -> None:
        fps = _configured_fps(self.config)
        interval = 1.0 / fps
        while not self._stop.is_set():
            if not self._client_ready.wait(0.25):
                continue
            if self._disabled():
                self._notice("Remote control disabled locally")
                self._end_session("disabled")
                return
            started = time.monotonic()
            try:
                frame, meta = _capture_frame()
                if self._stop.is_set() or not self._owner_id:
                    return
                if not self._client_ready.is_set():
                    continue
                digest = hashlib.sha256(frame).hexdigest()
                # A static display is still a current observation. Renew the
                # owned target/observation every capture while transmitting
                # pixels only when they change.
                self._bind_observation(meta)
                if digest != self._last_digest:
                    self._last_digest = digest
                    self._event({"type": "screen_meta", **meta})
                    sender = self._send_frame
                    if sender is not None and not sender(self._session_id, frame):
                        self._end_session("frame_send_failed")
                        return
            except Exception as exc:
                self._notice("Screen capture unavailable: " + type(exc).__name__)
                self._end_session("capture_unavailable")
                return
            self._stop.wait(max(0.01, interval - (time.monotonic() - started)))

    def _bind_observation(self, meta: dict[str, Any]) -> None:
        width = int(meta["control_width"])
        height = int(meta["control_height"])
        bounds = (0, 0, width - 1, height - 1)
        emit_event = self._target is None or self._target.bounds != bounds
        self._target = bind_target(
            kind="screen",
            identity="primary-display",
            label="primary display",
            bounds=bounds,
            metadata={"live_control": True},
            owner_id=self._owner_id,
        )
        self._observation = record_observation(
            "live_control_frame",
            target=self._target,
            origin="pixels",
            trust="owner_remote",
            foreground_identity="primary display",
            signature=f"pixels:{meta['frame_width']}x{meta['frame_height']}",
            emit_event=emit_event,
        )

    def _pointer(self, payload: dict[str, Any]) -> None:
        try:
            x_ratio = float(payload.get("x"))
            y_ratio = float(payload.get("y"))
        except (TypeError, ValueError):
            self._notice("Pointer input rejected")
            return
        if not 0.0 <= x_ratio <= 1.0 or not 0.0 <= y_ratio <= 1.0:
            self._notice("Pointer input rejected")
            return
        try:
            pg = _pyautogui()
            width, height = pg.size()
            x = min(width - 1, max(0, round(x_ratio * max(1, width - 1))))
            y = min(height - 1, max(0, round(y_ratio * max(1, height - 1))))
            target, observation, error = validate_action(
                "screen",
                owner_id=self._owner_id,
                point=(x, y),
                require_observation=True,
                max_age=3.0,
            )
            if error:
                self._notice("Refresh the remote screen before input")
                return
            action = str(payload.get("action") or "move").lower()
            button = str(payload.get("button") or "left").lower()
            if button not in {"left", "middle", "right"}:
                raise ValueError("button")
            if action == "down" and self._mouse_down:
                raise ValueError("mouse already held")
            if action == "up" and (not self._mouse_down or button != self._mouse_button):
                raise ValueError("mouse hold mismatch")
            pg.moveTo(x, y, duration=0)
            if action == "click":
                pg.click(button=button)
            elif action == "double_click":
                pg.doubleClick(button=button, interval=0.12)
            elif action == "down":
                pg.mouseDown(button=button)
                self._mouse_down = True
                self._mouse_button = button
            elif action == "up":
                pg.mouseUp(button=button)
                self._mouse_down = False
                self._mouse_button = "left"
            elif action == "scroll":
                delta = max(-5, min(5, int(payload.get("delta") or 0)))
                pg.scroll(delta)
            elif action != "move":
                raise ValueError("action")
            record_action(
                "live_control_pointer",
                target=target,
                observation=observation,
                status="executed",
                state_changed=action not in {"move"},
                invalidate=False,
            )
        except Exception as exc:
            self._notice("Pointer input failed: " + type(exc).__name__)

    def _key(self, payload: dict[str, Any]) -> None:
        key = str(payload.get("key") or "").lower()
        action = str(payload.get("action") or "press").lower()
        raw_modifiers = payload.get("modifiers") if isinstance(payload.get("modifiers"), list) else []
        modifiers = [str(item or "").lower() for item in raw_modifiers]
        if key not in _KEYS or action not in {"down", "press", "up"} or any(item not in _MODIFIERS for item in modifiers):
            self._notice("Key input rejected")
            return
        target, observation, error = validate_action(
            "screen", owner_id=self._owner_id, require_observation=True, max_age=3.0
        )
        if error:
            self._notice("Refresh the remote screen before input")
            return
        try:
            pg = _pyautogui()
            if modifiers and action == "press":
                pg.hotkey(*modifiers, key)
            elif action == "down":
                pg.keyDown(key)
                self._pressed_keys.add(key)
            elif action == "up":
                pg.keyUp(key)
                self._pressed_keys.discard(key)
            else:
                pg.press(key)
            record_action(
                "live_control_key",
                target=target,
                observation=observation,
                status="executed",
                state_changed=True,
                invalidate=False,
            )
        except Exception as exc:
            self._notice("Key input failed: " + type(exc).__name__)

    def _text(self, payload: dict[str, Any]) -> None:
        value = str(payload.get("value") or "")
        if not 1 <= len(value) <= MAX_TEXT_CHARS or any(ord(char) < 0x20 and char not in "\t\r\n" for char in value):
            self._notice("Text input rejected")
            return
        target, observation, error = validate_action(
            "screen", owner_id=self._owner_id, require_observation=True, max_age=3.0
        )
        if error:
            self._notice("Refresh the remote screen before input")
            return
        try:
            _type_text(value)
            record_action(
                "live_control_text",
                target=target,
                observation=observation,
                status="executed",
                state_changed=True,
                invalidate=False,
            )
        except Exception as exc:
            self._notice("Text input failed: " + type(exc).__name__)

    def _release_inputs(self) -> None:
        try:
            pg = _pyautogui()
            if self._mouse_down:
                pg.mouseUp(button=self._mouse_button)
            for key in list(self._pressed_keys):
                pg.keyUp(key)
        except Exception:
            pass
        self._mouse_down = False
        self._mouse_button = "left"
        self._pressed_keys.clear()

    def _end_session(self, reason: str) -> None:
        """Release the lease when capture stops for a reason the hub cannot see.

        The capture thread ending is invisible to both sides: the host still
        lists the session as occupied, so every later `session_open` is refused,
        and the controller keeps a frozen last frame. Telling the hub converts a
        wedge that lasts until lease expiry into a normal close, and a screen
        lock or transient capture error can then be retried immediately.
        """
        self._event({"type": "session_closed", "reason": str(reason or "closed")[:48]})

    def _event(self, payload: dict[str, Any]) -> None:
        if self._session_id and self._send_event is not None:
            self._send_event(self._session_id, payload)

    def _notice(self, message: str) -> None:
        self._event({"type": "notice", "message": str(message or "")[:120]})

    def _disabled(self) -> bool:
        return self._stop.is_set() or Path(resolve_state_path(KILL_SWITCH_PATH, self.config)).is_file()

    def _state(self, value: str) -> None:
        if self.on_state is not None:
            try:
                self.on_state(value)
            except Exception:
                pass


def _configured_fps(config: dict[str, Any]) -> float:
    block = config.get("consistent_everywhere") if isinstance(config.get("consistent_everywhere"), dict) else {}
    live = block.get("live_control") if isinstance(block.get("live_control"), dict) else {}
    screen = live.get("screen") if isinstance(live.get("screen"), dict) else {}
    return max(1.0, min(8.0, float(screen.get("fps", 6.0) or 6.0)))


def _capture_frame() -> tuple[bytes, dict[str, int | str]]:
    from PIL import Image, ImageGrab

    image = ImageGrab.grab().convert("RGB")
    source_width, source_height = image.size
    ratio = min(1.0, MAX_FRAME_WIDTH / source_width, MAX_FRAME_HEIGHT / source_height)
    if ratio < 1.0:
        image = image.resize(
            (max(1, round(source_width * ratio)), max(1, round(source_height * ratio))),
            Image.Resampling.LANCZOS,
        )
    encoded = b""
    while True:
        for quality in (82, 74, 66, 58, 50):
            data = io.BytesIO()
            image.save(data, format="JPEG", quality=quality, optimize=False)
            encoded = data.getvalue()
            if len(encoded) <= MAX_FRAME_BYTES:
                break
        if len(encoded) <= MAX_FRAME_BYTES:
            break
        width, height = image.size
        next_size = (max(1, round(width * 0.85)), max(1, round(height * 0.85)))
        if next_size == image.size:
            break
        image = image.resize(next_size, Image.Resampling.LANCZOS)
    if len(encoded) > MAX_FRAME_BYTES:
        raise RuntimeError("encoded frame exceeds live-control bound")
    frame_width, frame_height = image.size
    return encoded, {
        "format": "jpeg",
        "frame_width": frame_width,
        "frame_height": frame_height,
        "control_width": int(source_width),
        "control_height": int(source_height),
    }


def _pyautogui() -> Any:
    import pyautogui

    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.02
    return pyautogui


def _type_text(value: str) -> None:
    if os.name != "nt":
        _pyautogui().write(value, interval=0.005)
        return
    import ctypes
    from ctypes import wintypes

    class KeyboardInput(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class MouseInput(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        ]

    class HardwareInput(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class InputUnion(ctypes.Union):
        _fields_ = [
            ("mi", MouseInput),
            ("ki", KeyboardInput),
            ("hi", HardwareInput),
        ]

    class Input(ctypes.Structure):
        _anonymous_ = ("union",)
        _fields_ = [("type", wintypes.DWORD), ("union", InputUnion)]

    units = value.encode("utf-16-le")
    for offset in range(0, len(units), 2):
        code = int.from_bytes(units[offset:offset + 2], "little")
        for flags in (0x0004, 0x0004 | 0x0002):
            item = Input(type=1, ki=KeyboardInput(0, code, flags, 0, 0))
            if ctypes.windll.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(item)) != 1:
                raise OSError("SendInput failed")
