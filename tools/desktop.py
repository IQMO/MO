"""Native desktop actuation for MO computer-use (Step 4).

MO drives the real mouse and keyboard via ``pyautogui`` so it can carry out a
task end to end — e.g. open an app from the Start menu (press Win, type the
name, Enter), click a button, fill a field. Pixel/OS-level control is inherently
less reliable than the browser's DOM, so:

- the cross-to-corner FAILSAFE is ON (slam the mouse to a screen corner to abort);
- a short pause precedes each action so a human can interrupt;
- ``point_on_screen`` is the SAFE, non-actuating primitive — it only shows the
  MO overlay arrow/bubble (Guided mode), driving nothing.

These tools live in the ``ACTUATION_TOOLS`` sandbox lane.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pg():
    """Import pyautogui lazily with safety defaults (and a clear error if absent)."""
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.3
    return pyautogui


def execute_screen_size(arguments: dict[str, Any]) -> str:
    try:
        w, h = _pg().size()
    except Exception as exc:  # noqa: BLE001
        return f"Error: {type(exc).__name__}: {exc}"
    return f"Screen size: {w}x{h}"


def execute_point_on_screen(arguments: dict[str, Any]) -> str:
    """Guided mode: show the MO arrow + bubble at (x, y). Actuates nothing."""
    try:
        x = int(arguments.get("x"))
        y = int(arguments.get("y"))
    except Exception:
        return "Error: point_on_screen requires integer 'x' and 'y'."
    label = str(arguments.get("label", "") or "here")
    seconds = float(arguments.get("seconds", 4) or 4)
    # Prefer the live desktop Ghost orb: MO points with its own animated moon
    # (clicky-style) rather than spawning a one-shot bubble. Falls through to the
    # subprocess overlay when no orb is running (e.g. terminal-only Guide mode).
    try:
        from core.ghost.desktop_pointer import point_with_desktop_orb
        if point_with_desktop_orb(x, y, label, seconds):
            return f"Pointing at ({x},{y}): {label}"
    except Exception:
        pass
    try:
        subprocess.Popen(
            [sys.executable, "-m", "interface.screen_overlay", str(x), str(y), label, str(seconds)],
            cwd=_REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # noqa: BLE001
        return f"Error: overlay failed: {type(exc).__name__}: {exc}"
    return f"Pointing at ({x},{y}): {label}"


def execute_move_pointer(arguments: dict[str, Any]) -> str:
    try:
        x, y = int(arguments.get("x")), int(arguments.get("y"))
    except Exception:
        return "Error: move_pointer requires integer 'x' and 'y'."
    try:
        _pg().moveTo(x, y, duration=0.4)
    except Exception as exc:  # noqa: BLE001
        return f"Error: {type(exc).__name__}: {exc}"
    return f"Moved pointer to ({x},{y})."


def execute_mouse_click(arguments: dict[str, Any]) -> str:
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
            pg.click(x=int(x), y=int(y), clicks=clicks, button=button, interval=0.1)
            return f"Clicked {button} x{clicks} at ({int(x)},{int(y)})."
        pg.click(clicks=clicks, button=button, interval=0.1)
        return f"Clicked {button} x{clicks} at current pointer."
    except Exception as exc:  # noqa: BLE001
        return f"Error: {type(exc).__name__}: {exc}"


def execute_type_text(arguments: dict[str, Any]) -> str:
    text = str(arguments.get("text", ""))
    if not text:
        return "Error: type_text requires 'text'."
    try:
        time.sleep(0.4)
        _pg().write(text, interval=0.02)
    except Exception as exc:  # noqa: BLE001
        return f"Error: {type(exc).__name__}: {exc}"
    return f"Typed {len(text)} chars."


def execute_press_key(arguments: dict[str, Any]) -> str:
    """Press a key or chord. 'keys' is a single key ('enter', 'win') or a
    combo joined with '+' ('ctrl+c'). Accepts a list for a sequence."""
    keys = arguments.get("keys")
    if not keys:
        return "Error: press_key requires 'keys' (e.g. 'enter', 'win', 'ctrl+c')."
    try:
        pg = _pg()
        time.sleep(0.3)
        seq = keys if isinstance(keys, list) else [keys]
        for item in seq:
            item = str(item)
            if "+" in item:
                pg.hotkey(*[p.strip() for p in item.split("+")])
            else:
                pg.press(item)
    except Exception as exc:  # noqa: BLE001
        return f"Error: {type(exc).__name__}: {exc}"
    return f"Pressed: {keys}"
