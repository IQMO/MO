"""Native desktop actuation behind MO's canonical computer-use facade.

MO drives the real mouse and keyboard via ``pyautogui`` so it can carry out a
task end to end — e.g. open an app from the Start menu (press Win, type the
name, Enter), click a button, fill a field. Pixel/OS-level control is inherently
less reliable than the browser's DOM, so:

- the cross-to-corner FAILSAFE is ON (slam the mouse to a screen corner to abort);
- a short pause precedes each action so a human can interrupt;
- ``point_on_screen`` is the SAFE, non-actuating primitive — it only shows the
  MO overlay arrow/bubble (Guided mode), driving nothing.

These engines are reached through ``computer_act`` in the sandbox actuation lane.
"""
from __future__ import annotations

import os
import subprocess
import time
from typing import Any

from core.desktop.win32 import foreground_window_handle as _foreground_window_handle
from core.runtime.subprocess_flags import apply_windows_hidden_process_flags, gui_python_executable

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_KEY_ALIASES = {
    "control": "ctrl",
    "ctl": "ctrl",
    "return": "enter",
    "escape": "esc",
    "windows": "win",
    "winleft": "win",
    "winright": "win",
    "pageup": "pgup",
    "pagedown": "pgdn",
    "delete": "del",
}
_NAMED_KEYS = frozenset({
    "alt", "ctrl", "shift", "win", "enter", "esc", "tab", "space", "backspace",
    "del", "insert", "home", "end", "pgup", "pgdn", "up", "down", "left", "right",
    "capslock", "numlock", "scrolllock", "pause", "printscreen", "apps", "volumeup",
    "volumedown", "volumemute", "playpause", "nexttrack", "prevtrack",
})


def _pg():
    """Import pyautogui lazily with safety defaults (and a clear error if absent)."""
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.3
    return pyautogui


def _screen_dimensions(arguments: dict[str, Any]) -> str:
    try:
        if os.name == "nt":
            w, h = _windows_screen_dimensions()
        else:
            w, h = _pg().size()
    except Exception as exc:  # noqa: BLE001
        return f"Error: {type(exc).__name__}: {exc}"
    return f"Screen size: {w}x{h}"


def _windows_screen_dimensions() -> tuple[int, int]:
    """Read primary-display geometry without loading the actuation stack."""
    import ctypes

    user32 = ctypes.windll.user32
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass
    width = int(user32.GetSystemMetrics(0))
    height = int(user32.GetSystemMetrics(1))
    if width <= 0 or height <= 0:
        raise RuntimeError("primary display geometry is unavailable")
    return width, height


def execute_desktop_sync(arguments: dict[str, Any]) -> str:
    """Ask MO Desktop to recharge: lock the cube, take the terminal's current focus as context,
    then announce it. Actuates nothing, so it is safe in the explain/point lane."""
    try:
        from mo_desktop.desktop_pointer import sync_with_desktop
        focus = sync_with_desktop()
    except Exception as exc:  # noqa: BLE001
        return f"Error: desktop_sync failed: {type(exc).__name__}: {exc}"
    if focus is None:
        return "Error: MO Desktop is not running, so there is nothing to sync."
    if not focus:
        return "Synced, but MO terminal has no active session to read."
    return f"Synced with MO terminal. Its current focus: {focus}"


_FALSEY = {"", "0", "false", "no", "none", "null", "off"}


def _as_bool(value: Any) -> bool:
    """Providers send tool arguments as JSON strings: from_capture arrives as ``"True"`` — and so
    would ``"False"``, which is a truthy Python string. Coerce honestly."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in _FALSEY
    return bool(value)


def _resolve_capture_coords(x: int, y: int, arguments: dict[str, Any]) -> tuple[int, int]:
    """Convert image-space coordinates to screen pixels when the model says it read them off a
    canonical screen-observation image. Wide captures are downscaled, so the two spaces differ."""
    if not _as_bool(arguments.get("from_capture")):
        return (x, y)
    try:
        from .screen import to_screen_coords
        return to_screen_coords(x, y)
    except Exception:
        return (x, y)


def _point_box(arguments: dict[str, Any]) -> tuple[int, int, int, int] | None:
    """The optional ``box`` {x, y, width, height} to outline, in screen pixels."""
    raw = arguments.get("box")
    if not isinstance(raw, dict):
        return None
    try:
        bx, by, bw, bh = (int(raw[key]) for key in ("x", "y", "width", "height"))
    except (KeyError, TypeError, ValueError):
        return None
    if bw <= 0 or bh <= 0:
        return None
    x0, y0 = _resolve_capture_coords(bx, by, arguments)
    x1, y1 = _resolve_capture_coords(bx + bw, by + bh, arguments)
    return (x0, y0, max(1, x1 - x0), max(1, y1 - y0))


def execute_point_on_screen(arguments: dict[str, Any]) -> str:
    """Guided mode: show the MO arrow + bubble at (x, y). Actuates nothing."""
    try:
        x = int(arguments.get("x"))
        y = int(arguments.get("y"))
    except Exception:
        return "Error: point_on_screen requires integer 'x' and 'y'."
    x, y = _resolve_capture_coords(x, y, arguments)
    label = str(arguments.get("label", "") or "here")
    seconds = float(arguments.get("seconds", 4) or 4)
    box = _point_box(arguments)
    try:
        number = max(0, min(99, int(arguments.get("number") or 0)))
    except (TypeError, ValueError):
        number = 0
    try:
        from mo_desktop.desktop_pointer import point_with_desktop_cube
        if point_with_desktop_cube(x, y, label, seconds, box=box, number=number,
                                   zoom=_as_bool(arguments.get("zoom"))):
            outlined = " (outlined)" if box else ""
            return f"Pointing with MO Desktop cube at ({x},{y}){outlined}: {label}"
    except Exception:
        pass
    try:
        popen_kwargs = {
            "cwd": _REPO_ROOT,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        apply_windows_hidden_process_flags(popen_kwargs)
        subprocess.Popen(
            [gui_python_executable(), "-m", "interface.screen_overlay", str(x), str(y), label, str(seconds)],
            **popen_kwargs,
        )
    except Exception as exc:  # noqa: BLE001
        return f"Error: overlay failed: {type(exc).__name__}: {exc}"
    return f"Pointing at ({x},{y}): {label}"


def _execute_move_pointer(arguments: dict[str, Any]) -> str:
    from core.desktop.runtime import native_desktop_scope

    try:
        x, y = int(arguments.get("x")), int(arguments.get("y"))
    except Exception:
        return "Error: computer_act action=move requires integer 'x' and 'y'."
    x, y = _resolve_capture_coords(x, y, arguments)
    target, observation, guard = _desktop_action_context(point=(x, y))
    if guard:
        return guard
    try:
        pg = _pg()
        with native_desktop_scope(invalidate=True):
            pg.moveTo(x, y, duration=0.4)
    except Exception as exc:  # noqa: BLE001
        return f"Error: {type(exc).__name__}: {exc}"
    _record_desktop_action("computer_act", target, observation, state_changed=False, invalidate=False)
    return f"Moved pointer to ({x},{y})."


def _computer_runtime_turn() -> bool:
    try:
        from core.runtime.backend_monitor import current_monitor_context

        return bool(current_monitor_context())
    except Exception:
        return False


def _desktop_action_context(*, point: tuple[int, int] | None = None, require_observation: bool = True):
    # Direct headless adapter calls have no runtime owner and retain their unit-
    # level behavior. Every real Agent tool call has a monitor context, on both
    # Terminal and Desktop, and therefore uses the owner/observation gate.
    if not _computer_runtime_turn():
        return None, None, None
    try:
        from core.desktop.runtime import active_target, validate_action

        kind = "desktop" if active_target("desktop") is not None else "screen"
        target, observation, error = validate_action(
            kind, point=point, require_observation=require_observation
        )
        if error is None and target is not None:
            error = _foreground_target_guard(target)
        return target, observation, error
    except Exception as exc:
        return None, None, f"Error: desktop target validation failed: {type(exc).__name__}: {exc}"


def _foreground_target_guard(target: Any) -> str | None:
    """Stop raw input when focus moved away after the target observation."""
    metadata = getattr(target, "metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    expected = int(metadata.get("native_handle") or metadata.get("foreground_handle") or 0)
    current = _foreground_window_handle()
    if expected and current and expected != current:
        return (
            "Error: the observed target is not the foreground window. Background capture "
            "does not activate it. Use computer_act kind=desktop action=focus with a current "
            "ref for the intended window, then observe it and use the fresh target before "
            "raw mouse or keyboard input."
        )
    return None


def _record_desktop_action(
    tool: str,
    target: Any,
    observation: Any,
    *,
    state_changed: bool | None,
    invalidate: bool = True,
) -> None:
    if target is None:
        return
    try:
        from core.desktop.runtime import record_action

        record_action(
            tool,
            target=target,
            observation=observation,
            status="executed",
            state_changed=state_changed,
            invalidate=invalidate,
        )
    except Exception:
        pass


def _execute_mouse_click(arguments: dict[str, Any]) -> str:
    from core.desktop.runtime import native_desktop_scope

    pg = None
    try:
        pg = _pg()
    except Exception as exc:  # noqa: BLE001
        return f"Error: pyautogui unavailable: {exc}"
    button = str(arguments.get("button", "left") or "left")
    clicks = int(arguments.get("clicks", 1) or 1)
    x, y = arguments.get("x"), arguments.get("y")
    try:
        time.sleep(0.4)
        if x is not None and y is not None:
            ix, iy = int(x), int(y)
            ix, iy = _resolve_capture_coords(ix, iy, arguments)
            target, observation, guard = _desktop_action_context(point=(ix, iy))
            if guard:
                return guard
            with native_desktop_scope(invalidate=True):
                pg.click(x=ix, y=iy, clicks=clicks, button=button, interval=0.1)
            _record_desktop_action("computer_act", target, observation, state_changed=True)
            return f"Clicked {button} x{clicks} at ({ix},{iy})."
        try:
            px, py = pg.position()
            target, observation, guard = _desktop_action_context(point=(int(px), int(py)))
        except Exception as exc:  # noqa: BLE001
            return f"Error: current pointer target validation failed: {type(exc).__name__}: {exc}"
        if guard:
            return guard
        with native_desktop_scope(invalidate=True):
            pg.click(clicks=clicks, button=button, interval=0.1)
        _record_desktop_action("computer_act", target, observation, state_changed=True)
        return f"Clicked {button} x{clicks} at current pointer."
    except Exception as exc:  # noqa: BLE001
        return f"Error: {type(exc).__name__}: {exc}"


def _execute_drag_pointer(arguments: dict[str, Any]) -> str:
    """Drag one segment or one bounded continuous path inside the observed target."""
    from core.desktop.runtime import native_desktop_scope

    raw_points = arguments.get("points")
    continuous_path = raw_points is not None
    if continuous_path:
        if not isinstance(raw_points, list) or not 2 <= len(raw_points) <= 64:
            return "Error: computer_act action=drag points must contain 2 to 64 coordinate objects."
        points: list[tuple[int, int]] = []
        for index, point in enumerate(raw_points, 1):
            try:
                x, y = int(point["x"]), int(point["y"])
            except (KeyError, TypeError, ValueError):
                return f"Error: computer_act action=drag point {index} requires integer x and y."
            points.append(_resolve_capture_coords(x, y, arguments))
    else:
        try:
            start_x = int(arguments.get("start_x"))
            start_y = int(arguments.get("start_y"))
            end_x = int(arguments.get("end_x"))
            end_y = int(arguments.get("end_y"))
        except Exception:
            return (
                "Error: computer_act action=drag requires points or integer "
                "start_x, start_y, end_x, and end_y."
            )
        points = [
            _resolve_capture_coords(start_x, start_y, arguments),
            _resolve_capture_coords(end_x, end_y, arguments),
        ]

    button = str(arguments.get("button", "left") or "left").strip().lower()
    if button not in {"left", "middle", "right"}:
        return "Error: computer_act action=drag button must be left, middle, or right."
    try:
        duration = max(0.1, min(float(arguments.get("duration", 0.8) or 0.8), 10.0))
    except Exception:
        return "Error: computer_act action=drag duration must be a number."

    target, observation, guard = _desktop_action_context(point=points[0])
    for point in points[1:]:
        if guard:
            break
        _target, _observation, guard = _desktop_action_context(point=point)
    if guard:
        return guard

    try:
        pg = _pg()
        time.sleep(0.4)
        with native_desktop_scope(invalidate=True):
            pg.moveTo(*points[0], duration=0.2)
            if continuous_path:
                pressed = False
                try:
                    pg.mouseDown(button=button, _pause=False)
                    pressed = True
                    segment_delay = duration / (len(points) - 1)
                    for x, y in points[1:]:
                        cancel_event = arguments.get("_cancel_event")
                        if cancel_event is not None and cancel_event.is_set():
                            return "Error: computer operation cancelled during drag; pointer released."
                        if target is not None and (foreground_error := _foreground_target_guard(target)):
                            return foreground_error
                        # Sub-100ms PyAutoGUI durations are instantaneous and Windows may
                        # coalesce them before the application sees held-button motion.
                        # Emit each point immediately, then yield its share of the bounded
                        # total duration so drawing surfaces receive one continuous path.
                        pg.moveTo(x, y, duration=0, _pause=False)
                        time.sleep(segment_delay)
                finally:
                    if pressed:
                        pg.mouseUp(button=button, _pause=False)
            else:
                pg.dragTo(*points[1], duration=duration, button=button)
    except Exception as exc:  # noqa: BLE001
        return f"Error: {type(exc).__name__}: {exc}"
    _record_desktop_action("computer_act", target, observation, state_changed=True)
    if continuous_path:
        return f"Dragged {button} through {len(points)} points as one continuous stroke."
    return f"Dragged {button} from {points[0]} to {points[1]}."


def _execute_scroll_pointer(arguments: dict[str, Any]) -> str:
    """Send a bounded raw wheel scroll inside the current observed target."""
    from core.desktop.runtime import native_desktop_scope

    try:
        delta = int(arguments.get("delta"))
    except Exception:
        return "Error: computer_act action=scroll requires integer 'delta' (positive up, negative down)."
    if delta == 0 or abs(delta) > 120:
        return "Error: computer_act action=scroll delta must be between -120 and 120 and cannot be zero."
    x, y = arguments.get("x"), arguments.get("y")
    try:
        if x is None or y is None:
            pg = _pg()
            px, py = pg.position()
            ix, iy = int(px), int(py)
        else:
            ix, iy = int(x), int(y)
            ix, iy = _resolve_capture_coords(ix, iy, arguments)
            pg = _pg()
    except Exception as exc:  # noqa: BLE001
        return f"Error: computer_act action=scroll needs valid x/y coordinates: {exc}"
    target, observation, guard = _desktop_action_context(point=(ix, iy))
    if guard:
        return guard
    try:
        time.sleep(0.3)
        # PyAutoGUI 0.9.54 passes this value straight to Windows ``dwData``;
        # one native wheel detent is WHEEL_DELTA (120), not 1. Other backends
        # consume logical clicks as documented, so normalize only on Windows.
        with native_desktop_scope(invalidate=True):
            pg.moveTo(ix, iy, duration=0.2)
            pg.scroll(delta * 120 if os.name == "nt" else delta)
    except Exception as exc:  # noqa: BLE001
        return f"Error: {type(exc).__name__}: {exc}"
    _record_desktop_action("computer_act", target, observation, state_changed=True)
    return f"Scrolled {delta} wheel step(s) at ({ix},{iy})."


def _execute_type_text(arguments: dict[str, Any]) -> str:
    from core.desktop.runtime import native_desktop_scope

    text = str(arguments.get("text", ""))
    if not text:
        return "Error: computer_act action=type requires 'text'."
    try:
        time.sleep(0.4)
        target, observation, runtime_guard = _desktop_action_context()
        if runtime_guard:
            return runtime_guard
        pg = _pg()
        with native_desktop_scope(invalidate=True):
            for character in text:
                cancelled = arguments.get("_cancel_event")
                if cancelled is not None and cancelled.is_set():
                    return "Error: computer operation cancelled during typing."
                if target is not None and (guard := _foreground_target_guard(target)):
                    return guard
                pg.write(character, _pause=False)
    except Exception as exc:  # noqa: BLE001
        return f"Error: {type(exc).__name__}: {exc}"
    _record_desktop_action("computer_act", target, observation, state_changed=True)
    return f"Typed {len(text)} chars."


def _execute_press_key(arguments: dict[str, Any]) -> str:
    """Press a key or chord. 'keys' is a single key ('enter', 'win') or a
    combo joined with '+' ('ctrl+c'). Accepts a list for a sequence."""
    from core.desktop.runtime import native_desktop_scope

    keys = arguments.get("keys") or arguments.get("key")
    if not keys:
        return "Error: computer_act action=key requires 'keys' (e.g. 'enter', 'win', 'ctrl+c')."
    try:
        sequence = _normalize_key_sequence(keys)
    except ValueError as exc:
        return f"Error: {exc}"
    target, observation, guard = _desktop_action_context()
    if guard:
        return guard
    try:
        pg = _pg()
        time.sleep(0.3)
        with native_desktop_scope(invalidate=True):
            for chord in sequence:
                cancelled = arguments.get("_cancel_event")
                if cancelled is not None and cancelled.is_set():
                    return "Error: computer operation cancelled during key sequence."
                if target is not None and (guard := _foreground_target_guard(target)):
                    return guard
                if len(chord) > 1:
                    pg.hotkey(*chord)
                else:
                    pg.press(chord[0])
    except Exception as exc:  # noqa: BLE001
        return f"Error: {type(exc).__name__}: {exc}"
    _record_desktop_action("computer_act", target, observation, state_changed=True)
    rendered = ", ".join("+".join(chord) for chord in sequence)
    return f"Pressed: {rendered}"


def _normalize_key_sequence(keys: object) -> list[tuple[str, ...]]:
    """Validate one key/chord or a bounded sequence without eval/coercion."""
    if isinstance(keys, str):
        items = [keys]
    elif isinstance(keys, list):
        items = keys
    else:
        raise ValueError("computer_act action=key keys must be a string or a list of strings.")
    if not 1 <= len(items) <= 12:
        raise ValueError("computer_act action=key accepts between 1 and 12 key/chord items.")
    sequence: list[tuple[str, ...]] = []
    for raw in items:
        if not isinstance(raw, str):
            raise ValueError("computer_act action=key list items must be strings.")
        item = raw.strip().lower()
        if not item or len(item) > 64:
            raise ValueError("computer_act action=key items must contain 1-64 characters.")
        if any(marker in item for marker in ("[", "]", "{", "}", "'", '"', ",")):
            raise ValueError(
                "computer_act action=key received an encoded collection; pass one chord string "
                "such as 'win+alt+m' or a real list of strings."
            )
        parts = tuple(_KEY_ALIASES.get(part.strip(), part.strip()) for part in item.split("+"))
        if not 1 <= len(parts) <= 4 or any(not part for part in parts):
            raise ValueError("computer_act action=key chords must contain 1-4 non-empty keys.")
        for part in parts:
            valid = (
                part in _NAMED_KEYS
                or (len(part) == 1 and part.isascii() and part.isalnum())
                or (part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24)
            )
            if not valid:
                raise ValueError(f"computer_act action=key does not recognize key {part!r}.")
        sequence.append(parts)
    return sequence
