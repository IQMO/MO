"""Let a phone drive this computer's pointer as a trackpad.

The phone reaches the Desktop through ``adb reverse``: the phone connects to its
own localhost and adb carries it to a server bound on this machine's loopback.
That is why this needs no discovery or firewall rule. Every launch also carries
a one-use session nonce; loopback and ADB transport alone are not treated as
proof that the Android peer is the session Desktop just opened.

The phone owns feel: speed, sensitivity and smoothing are applied there and
arrive here as ready deltas, so tuning never needs a Desktop change.
"""
from __future__ import annotations

import hmac
import json
import socket
import sys
import threading
from typing import Any, Callable, Protocol


TRACKPAD_PORT = 8099
# One gesture cannot move the pointer further than this in a single message,
# so a corrupt or hostile delta cannot fling the cursor across the desktop.
MAX_STEP_PIXELS = 300.0
MAX_SCROLL_STEP = 5
MAX_MESSAGE_BYTES = 8_192
MAX_TEXT_CHARS = 2_000
MAX_SWITCHABLE_WINDOWS = 16
MAX_WINDOW_TITLE_CHARS = 120
AUTH_VERSION = 1
AUTH_TIMEOUT_SECONDS = 2.0
AUTH_READY_LINE = b'{"t":"ready","v":1}\n'
SESSION_NONCE_MIN_CHARS = 32
SESSION_NONCE_MAX_CHARS = 128
NEWLINE = chr(10).encode()
_BUTTONS = {"left", "middle", "right"}
_NAVIGATION_KEYS = {"back", "forward"}
_REMOTE_KEYS = {
    "backspace", "delete", "down", "end", "enter", "escape", "home",
    "left", "pagedown", "pageup", "right", "space", "tab", "up",
}
_MODIFIERS = {"alt", "ctrl", "shift", "win"}
_WINDOW_ID_PREFIX = "window-"
_PEN_PHASES = {"hover", "down", "move", "up", "cancel"}


class TrackpadError(RuntimeError):
    """A safe, operator-readable trackpad failure."""


class PointerActuator(Protocol):
    """The desktop side of a trackpad gesture."""

    def move(self, dx: float, dy: float) -> None: ...
    def click(self, button: str, double: bool) -> None: ...
    def press(self, button: str) -> None: ...
    def release(self, button: str) -> None: ...
    def scroll(self, amount: int) -> None: ...
    def zoom(self, amount: int) -> None: ...
    def navigate(self, key: str) -> None: ...
    def type_text(self, value: str) -> None: ...
    def key(self, key: str, modifiers: list[str]) -> None: ...
    def pen(self, message: dict[str, Any]) -> None: ...


def parse_message(raw: str) -> dict[str, Any] | None:
    """Read one trackpad message, or None when it is not usable.

    Unknown message types return None rather than raising: a newer phone must
    never be able to kill a working session by sending a word this build does
    not know.
    """
    if not raw or len(raw.encode("utf-8", "ignore")) > MAX_MESSAGE_BYTES:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    kind = str(payload.get("t") or "")
    if kind == "m":
        dx = _bounded_step(payload.get("dx"))
        dy = _bounded_step(payload.get("dy"))
        if dx is None or dy is None:
            return None
        return {"t": "m", "dx": dx, "dy": dy}
    if kind in {"c", "d", "u"}:
        button = str(payload.get("b") or "left")
        if button not in _BUTTONS:
            return None
        message = {"t": kind, "b": button}
        if kind == "c":
            message["double"] = payload.get("double") is True
        return message
    if kind in {"s", "z"}:
        try:
            amount = int(payload.get("dy") or 0)
        except (TypeError, ValueError):
            return None
        if amount == 0:
            return None
        return {"t": kind, "dy": max(-MAX_SCROLL_STEP, min(MAX_SCROLL_STEP, amount))}
    if kind == "k":
        key = str(payload.get("k") or "")
        if key not in _NAVIGATION_KEYS:
            return None
        return {"t": "k", "k": key}
    if kind == "x":
        value = payload.get("text")
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= MAX_TEXT_CHARS
            or any(ord(char) < 0x20 and char not in "\t\r\n" for char in value)
        ):
            return None
        return {"t": "x", "text": value}
    if kind == "q":
        key = str(payload.get("k") or "").lower()
        raw_modifiers = payload.get("modifiers")
        if not isinstance(raw_modifiers, list):
            raw_modifiers = []
        modifiers = [str(value or "").lower() for value in raw_modifiers]
        if (
            key not in _REMOTE_KEYS
            or len(modifiers) > 3
            or len(set(modifiers)) != len(modifiers)
            or any(value not in _MODIFIERS for value in modifiers)
        ):
            return None
        return {"t": "q", "k": key, "modifiers": modifiers}
    if kind == "w":
        return {"t": "w"}
    if kind == "a":
        window_id = str(payload.get("id") or "")
        suffix = window_id.removeprefix(_WINDOW_ID_PREFIX)
        if not suffix.isdigit() or window_id != _WINDOW_ID_PREFIX + suffix:
            return None
        return {"t": "a", "id": window_id}
    if kind == "p" and payload.get("v") == 2:
        phase = str(payload.get("phase") or "").strip().lower()
        x = _bounded_unit(payload.get("x"))
        y = _bounded_unit(payload.get("y"))
        pressure = _bounded_unit(payload.get("pressure", 0))
        tilt = _bounded_number(payload.get("tilt", 0), -1.571, 1.571)
        orientation = _bounded_number(payload.get("orientation", 0), -6.284, 6.284)
        sequence = _bounded_integer(payload.get("seq", 0), 0, 2_147_483_647)
        timestamp = _bounded_integer(payload.get("ts", 0), 0, 9_223_372_036_854_775_807)
        buttons = _bounded_integer(payload.get("buttons", 0), 0, 255)
        history = _pen_history(payload.get("history"))
        if (
            phase not in _PEN_PHASES or x is None or y is None or pressure is None
            or tilt is None or orientation is None or sequence is None or timestamp is None
            or buttons is None or history is None
        ):
            return None
        return {
            "t": "p", "v": 2, "phase": phase, "x": x, "y": y,
            "pressure": pressure, "tilt": tilt, "orientation": orientation,
            "buttons": buttons, "eraser": payload.get("eraser") is True,
            "ts": timestamp, "seq": sequence, "history": history,
        }
    return None


def _bounded_step(value: Any) -> float | None:
    try:
        step = float(value)
    except (TypeError, ValueError):
        return None
    if step != step or step in (float("inf"), float("-inf")):
        return None
    return max(-MAX_STEP_PIXELS, min(MAX_STEP_PIXELS, step))


def _bounded_unit(value: Any) -> float | None:
    return _bounded_number(value, 0.0, 1.0)


def _bounded_number(value: Any, low: float, high: float) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed != parsed or parsed in (float("inf"), float("-inf")) or not low <= parsed <= high:
        return None
    return parsed


def _bounded_integer(value: Any, low: int, high: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if low <= parsed <= high else None


def _pen_history(value: Any) -> list[list[float | int]] | None:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or len(value) > 64:
        return None
    result: list[list[float | int]] = []
    for row in value:
        if not isinstance(row, list) or len(row) not in {5, 6, 7}:
            return None
        x = _bounded_unit(row[0])
        y = _bounded_unit(row[1])
        pressure = _bounded_unit(row[2])
        tilt = _bounded_number(row[3], -1.571, 1.571)
        orientation = _bounded_number(row[4], -6.284, 6.284)
        buttons = _bounded_integer(row[5], 0, 255) if len(row) >= 6 else 0
        timestamp = _bounded_integer(row[6], 0, 9_223_372_036_854_775_807) if len(row) >= 7 else 0
        if None in {x, y, pressure, tilt, orientation, buttons, timestamp}:
            return None
        result.append([x, y, pressure, tilt, orientation, buttons, timestamp])
    return result


def coalesce_moves(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge runs of movement into one step, preserving every other gesture.

    A pointer only has to end up in the right place; replaying each intermediate
    step of a run adds latency without adding accuracy. Clicks and holds are
    never merged, and their order against movement is kept, so a drag still
    presses and releases where it should.
    """
    merged: list[dict[str, Any]] = []
    dx = dy = 0.0
    for message in messages:
        if message.get("t") == "m":
            dx += float(message["dx"])
            dy += float(message["dy"])
            continue
        if dx or dy:
            merged.append({"t": "m", "dx": dx, "dy": dy})
            dx = dy = 0.0
        merged.append(message)
    if dx or dy:
        merged.append({"t": "m", "dx": dx, "dy": dy})
    return merged


def apply_message(message: dict[str, Any], actuator: PointerActuator) -> None:
    """Drive one parsed message onto the desktop pointer."""
    kind = message.get("t")
    if kind == "m":
        actuator.move(float(message["dx"]), float(message["dy"]))
    elif kind == "c":
        actuator.click(str(message["b"]), bool(message.get("double")))
    elif kind == "d":
        actuator.press(str(message["b"]))
    elif kind == "u":
        actuator.release(str(message["b"]))
    elif kind == "s":
        actuator.scroll(int(message["dy"]))
    elif kind == "z":
        actuator.zoom(int(message["dy"]))
    elif kind == "k":
        actuator.navigate(str(message["k"]))
    elif kind == "x":
        actuator.type_text(str(message["text"]))
    elif kind == "q":
        actuator.key(str(message["k"]), list(message["modifiers"]))
    elif kind == "p":
        inject = getattr(actuator, "pen", None)
        if callable(inject):
            inject(message)


def _mo_design_board_rect() -> tuple[int, int, int, int] | None:
    """Fail closed unless the trusted foreground Board published current bounds."""
    import os

    if os.name != "nt":
        return None
    try:
        import ctypes

        user32 = ctypes.windll.user32
        handle = user32.GetForegroundWindow()
        if not handle:
            return None
        length = int(user32.GetWindowTextLengthW(handle))
        buffer = ctypes.create_unicode_buffer(max(1, length + 1))
        user32.GetWindowTextW(handle, buffer, len(buffer))
        title = str(buffer.value or "")
        if not title.startswith(("MO Board — ", "MO Design Board — ")):
            return None
        from mo_desktop.design_studio.board_geometry import read_board_rect

        return read_board_rect(int(handle))
    except Exception:
        return None


def switchable_windows(*, limit: int = MAX_SWITCHABLE_WINDOWS) -> list[tuple[int, str, bool]]:
    """Return the user-visible task projection; phone keeps its existing 16-row bound."""
    limit = max(1, min(256, int(limit)))
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        dwmapi = getattr(ctypes.windll, "dwmapi", None)
        foreground = int(user32.GetForegroundWindow() or 0)
        rows: list[tuple[int, str, bool]] = []
        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @enum_proc
        def collect(hwnd: int, _lparam: int) -> bool:
            handle = int(hwnd)
            if len(rows) >= limit:
                return False
            if not user32.IsWindowVisible(handle):
                return True
            ex_style = int(user32.GetWindowLongW(handle, -20))
            owner = int(user32.GetWindow(handle, 4) or 0)
            is_tool = bool(ex_style & 0x00000080)
            is_app = bool(ex_style & 0x00040000)
            if (is_tool or owner) and not is_app:
                return True
            if dwmapi is not None:
                cloaked = wintypes.DWORD()
                if dwmapi.DwmGetWindowAttribute(
                    handle, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
                ) == 0 and cloaked.value:
                    return True
            length = int(user32.GetWindowTextLengthW(handle))
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(min(length, MAX_WINDOW_TITLE_CHARS) + 1)
            user32.GetWindowTextW(handle, buffer, len(buffer))
            title = " ".join(str(buffer.value or "").split())
            if title:
                rows.append((handle, title[:MAX_WINDOW_TITLE_CHARS], handle == foreground))
            return True

        user32.EnumWindows(collect, 0)
        return rows
    except Exception:
        return []


def activate_switchable_window(handle: int, *, limit: int = MAX_SWITCHABLE_WINDOWS,
                              minimize_active: bool = False) -> bool:
    """Switch a projected window; Focus may minimize an already-foreground target."""
    rows = switchable_windows() if limit == MAX_SWITCHABLE_WINDOWS else switchable_windows(limit=limit)
    if sys.platform != "win32" or handle not in {row[0] for row in rows}:
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        if minimize_active and int(user32.GetForegroundWindow() or 0) == handle and not user32.IsIconic(handle):
            user32.ShowWindow(handle, 6)
            return bool(user32.IsIconic(handle))
        if user32.IsIconic(handle):
            user32.ShowWindow(handle, 9)
        user32.SetForegroundWindow(handle)
        return int(user32.GetForegroundWindow() or 0) == handle
    except Exception:
        return False


class DesktopPointer:
    """Actuate the real pointer through the library the Desktop already uses."""

    def __init__(self) -> None:
        self._held: set[str] = set()
        self._pyautogui: Any = None
        self._screen: tuple[int, int] | None = None
        self._pen_injector: Any = None
        self._pen_unavailable = False

    def _tool(self) -> Any:
        if self._pyautogui is None:
            import pyautogui

            self._pyautogui = pyautogui
        tool = self._pyautogui
        # pyautogui sleeps 100ms after every call by default. At a pointer's
        # event rate that is not a pause, it is a queue: the desktop applied
        # nine moves a second while the phone sent sixty, so the cursor
        # crawled on long after the finger stopped. These are module globals
        # and Live Control re-arms FAILSAFE/PAUSE on every call in this same
        # process, so re-assert the pointer profile on every access rather
        # than only at first import.
        tool.FAILSAFE = False
        tool.PAUSE = 0
        return tool

    def _size(self) -> tuple[int, int]:
        if self._screen is None:
            self._screen = tuple(self._tool().size())
        return self._screen

    def move(self, dx: float, dy: float) -> None:
        # Resolved against the current position and clamped to the display, the
        # same absolute call Live Control already drives this pointer with. A
        # relative move can drift under display scaling and can walk the cursor
        # off-screen where it can no longer be recovered by dragging back.
        tool = self._tool()
        width, height = self._size()
        x, y = tool.position()
        target_x = min(width - 1, max(0, int(round(x + dx))))
        target_y = min(height - 1, max(0, int(round(y + dy))))
        if (target_x, target_y) != (x, y):
            tool.moveTo(target_x, target_y, duration=0)

    def click(self, button: str, double: bool) -> None:
        tool = self._tool()
        if double:
            tool.doubleClick(button=button, interval=0.12)
        else:
            tool.click(button=button)

    def press(self, button: str) -> None:
        if button in self._held:
            return
        self._held.add(button)
        self._tool().mouseDown(button=button)

    def release(self, button: str) -> None:
        if button not in self._held:
            return
        self._held.discard(button)
        self._tool().mouseUp(button=button)

    def scroll(self, amount: int) -> None:
        self._tool().scroll(amount)

    def zoom(self, amount: int) -> None:
        """Map a bounded phone pinch to the platform's ordinary zoom gesture."""
        tool = self._tool()
        modifier = "command" if sys.platform == "darwin" else "ctrl"
        tool.keyDown(modifier)
        try:
            tool.scroll(amount)
        finally:
            tool.keyUp(modifier)

    def navigate(self, key: str) -> None:
        # Back and forward are what a mouse's side buttons do in practice, and
        # these reach every ordinary browser and file manager.
        self._tool().hotkey("alt", "left" if key == "back" else "right")

    def type_text(self, value: str) -> None:
        # Reuse Live Control's native Unicode SendInput path rather than
        # introducing a second keyboard injector for Trackpad.
        from mo_desktop.live_control import _type_text

        _type_text(value)

    def key(self, key: str, modifiers: list[str]) -> None:
        tool = self._tool()
        if modifiers:
            tool.hotkey(*modifiers, key)
        else:
            tool.press(key)

    def pen(self, message: dict[str, Any]) -> None:
        """Inject pen only while trusted MO Design is the foreground target."""
        target = _mo_design_board_rect()
        if target is None:
            self._release_pen()
            return
        if self._pen_unavailable:
            return
        try:
            if self._pen_injector is None:
                from .windows_pen import WindowsPenInjector

                self._pen_injector = WindowsPenInjector(target_rect=_mo_design_board_rect)
            self._pen_injector.send(message)
        except Exception:
            # Mouse fallback is unsafe here: the Board could still have Select
            # active, turning phone ink into moves or pans. Native pen support
            # therefore fails closed instead of changing interaction meaning.
            self._pen_unavailable = True
            self._release_pen()

    def warm(self) -> None:
        """Load and measure before the first gesture, not during it.

        The first call imports pyautogui and queries the display, which cost
        about half a second and landed on the operator's first drag.
        """
        try:
            self._tool()
            self._size()
        except Exception:
            pass

    def release_all(self) -> None:
        """Never leave a button held when a session ends."""
        for button in list(self._held):
            try:
                self.release(button)
            except Exception:
                self._held.discard(button)
        self._release_pen()

    def close(self) -> None:
        self.release_all()
        injector, self._pen_injector = self._pen_injector, None
        if injector is not None:
            try:
                injector.close()
            except Exception:
                pass

    def _release_pen(self) -> None:
        if self._pen_injector is not None:
            try:
                self._pen_injector.release_all()
            except Exception:
                pass


class TrackpadServer:
    """Accept one authenticated phone session on loopback and drive the pointer."""

    def __init__(
        self,
        *,
        nonce: str,
        port: int = 0,
        actuator: PointerActuator | None = None,
        on_state: Callable[[str], None] | None = None,
    ) -> None:
        clean_nonce = str(nonce or "")
        if (
            not SESSION_NONCE_MIN_CHARS <= len(clean_nonce) <= SESSION_NONCE_MAX_CHARS
            or any(not (char.isalnum() or char in "-_") for char in clean_nonce)
        ):
            raise TrackpadError("The trackpad session credential is invalid.")
        self.port = int(port)
        self.actuator = actuator or DesktopPointer()
        self.on_state = on_state
        self._nonce = clean_nonce
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._window_handles: dict[str, int] = {}
        self._next_window_id = 0
        self.connected = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        if not self._nonce:
            raise TrackpadError("Start a new trackpad session to reconnect.")
        listener = socket.socket()
        # This socket drives the pointer, so the port must not be shareable.
        # Windows lets SO_REUSEADDR bind over a live listener, which would let
        # another process take the phone's gestures; SO_EXCLUSIVEADDRUSE is the
        # option that actually refuses a second binder there.
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            listener.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            # Loopback only: adb carries the phone here, so nothing on the
            # network can reach the pointer.
            listener.bind(("127.0.0.1", self.port))
        except OSError as exc:
            listener.close()
            raise TrackpadError(
                "The trackpad port is already in use." if exc.errno else
                "The trackpad could not be started."
            ) from None
        self.port = int(listener.getsockname()[1])
        listener.listen(1)
        listener.settimeout(0.5)
        self._socket = listener
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._serve, name="mo-trackpad", daemon=True
        )
        warm = getattr(self.actuator, "warm", None)
        if callable(warm):
            warm()
        self._thread.start()
        self._state("waiting")

    def stop(self) -> None:
        self._stop.set()
        self._nonce = ""
        listener, self._socket = self._socket, None
        if listener is not None:
            try:
                listener.close()
            except Exception:
                pass
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        release = getattr(self.actuator, "release_all", None)
        if callable(release):
            release()
        close = getattr(self.actuator, "close", None)
        if callable(close):
            close()
        self._window_handles.clear()
        self._next_window_id = 0
        self.connected = False

    def _serve(self) -> None:
        listener = self._socket
        try:
            while not self._stop.is_set() and listener is not None:
                try:
                    client, _ = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                initial = self._authenticate(client)
                if initial is None:
                    try:
                        client.close()
                    except Exception:
                        pass
                    continue
                self.connected = True
                self._state("connected")
                try:
                    self._drain(client, initial)
                finally:
                    self.connected = False
                    try:
                        client.close()
                    except Exception:
                        pass
                    release = getattr(self.actuator, "release_all", None)
                    if callable(release):
                        release()
                    if not self._stop.is_set():
                        self._state("ended")
                # A successful nonce is consumed. Reconnecting requires Desktop
                # to create a fresh listener, reverse mapping, and Activity.
                break
        finally:
            if listener is not None:
                try:
                    listener.close()
                except Exception:
                    pass
            if self._socket is listener:
                self._socket = None

    def _authenticate(self, client: socket.socket) -> bytes | None:
        nonce = self._nonce
        if not nonce:
            return None
        client.settimeout(AUTH_TIMEOUT_SECONDS)
        buffer = b""
        while NEWLINE not in buffer:
            try:
                chunk = client.recv(4096)
            except (socket.timeout, OSError):
                return None
            if not chunk:
                return None
            buffer += chunk
            if len(buffer) > MAX_MESSAGE_BYTES:
                return None
        line, remainder = buffer.split(NEWLINE, 1)
        try:
            hello = json.loads(line.decode("utf-8"))
        except (TypeError, UnicodeDecodeError, ValueError):
            return None
        offered = hello.get("nonce") if isinstance(hello, dict) else None
        if (
            not isinstance(offered, str)
            or hello.get("t") != "hello"
            or hello.get("v") != AUTH_VERSION
            or not hmac.compare_digest(offered, nonce)
        ):
            return None
        try:
            client.sendall(AUTH_READY_LINE)
        except OSError:
            return None
        self._nonce = ""
        return remainder

    def _drain(self, client: socket.socket, initial: bytes = b"") -> None:
        client.settimeout(1.0)
        buffer = initial
        last_pen_sequence = -1
        while not self._stop.is_set():
            if NEWLINE not in buffer:
                try:
                    chunk = client.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    return
                if not chunk:
                    return
                buffer += chunk
            if len(buffer) > MAX_MESSAGE_BYTES * 64:
                # A peer that never sends a newline is not a trackpad.
                return
            batch: list[dict[str, Any]] = []
            while NEWLINE in buffer:
                line, buffer = buffer.split(NEWLINE, 1)
                message = parse_message(line.decode("utf-8", "ignore").strip())
                if message is not None:
                    if message.get("t") == "p":
                        sequence = int(message["seq"])
                        # TCP preserves wire order and the phone may omit move
                        # packets under pressure, so gaps are valid. Replayed or
                        # out-of-order pen packets are not.
                        if sequence <= last_pen_sequence:
                            continue
                        last_pen_sequence = sequence
                    batch.append(message)
            for message in coalesce_moves(batch):
                try:
                    kind = message.get("t")
                    if kind == "w":
                        self._send_windows(client)
                    elif kind == "a":
                        window_id = str(message["id"])
                        handle = self._window_handles.get(window_id)
                        activated = bool(
                            handle is not None and activate_switchable_window(handle)
                        )
                        self._send_message(
                            client,
                            {"t": "activation", "id": window_id, "ok": activated},
                        )
                        self._send_windows(client)
                    else:
                        apply_message(message, self.actuator)
                except Exception:
                    # One bad gesture must not end the session.
                    continue

    @staticmethod
    def _message_payload(message: dict[str, Any]) -> bytes:
        return json.dumps(
            message,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8") + NEWLINE

    @classmethod
    def _send_message(cls, client: socket.socket, message: dict[str, Any]) -> None:
        payload = cls._message_payload(message)
        if len(payload) > MAX_MESSAGE_BYTES:
            raise TrackpadError("The trackpad reply exceeded its wire bound.")
        client.sendall(payload)

    def _send_windows(self, client: socket.socket) -> None:
        rows = switchable_windows()
        known_ids = {handle: window_id for window_id, handle in self._window_handles.items()}
        handles: dict[str, int] = {}
        items: list[dict[str, Any]] = []
        for handle, title, active in rows:
            window_id = known_ids.get(handle)
            new_id = window_id is None
            if new_id:
                window_id = f"{_WINDOW_ID_PREFIX}{self._next_window_id}"
            item = {"id": window_id, "title": title, "active": active}
            candidate = [*items, item]
            if len(self._message_payload({"t": "windows", "items": candidate})) > MAX_MESSAGE_BYTES:
                break
            if new_id:
                self._next_window_id += 1
            items = candidate
            handles[window_id] = handle
        self._send_message(client, {"t": "windows", "items": items})
        self._window_handles = handles

    def _state(self, value: str) -> None:
        if self.on_state is not None:
            try:
                self.on_state(value)
            except Exception:
                pass
