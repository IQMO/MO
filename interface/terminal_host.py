"""Per-terminal visual state helpers.

Prompt-toolkit can paint MO's cell grid, but terminal-host padding and the gap
beside a visible scrollbar are drawn by the emulator. OSC 11 updates only the
current terminal session's default background, so those host-owned pixels can
follow the active skin without editing the user's terminal profile.

The terminal title is also host-owned. While MO is working, its existing
one-cell activity spinner is prefixed to the stable title so Windows exposes the
same visible animation in the tab and taskbar; idle always restores the stable
identity used by MO Desktop.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Any

from .formatting import DOTS4_PHASES, brand_spinner_frame

_FALSE_ENV_VALUES = {"0", "false", "no", "off"}
_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
TERMINAL_IDLE_TITLE = "MO"


def terminal_background_sync_enabled(env: dict[str, str] | None = None) -> bool:
    """Return True when MO should ask the current terminal to match its skin bg."""
    values = os.environ if env is None else env
    if str(values.get("NO_COLOR", "")).strip():
        return False
    raw = str(values.get("MO_SYNC_TERMINAL_BG", "1")).strip().lower()
    return raw not in _FALSE_ENV_VALUES


def _write_terminal_sequence(
    sequence: str,
    *,
    output: Any = None,
    stream: Any = None,
) -> bool:
    """Write a raw terminal sequence to prompt-toolkit output or stdout."""
    if output is not None and hasattr(output, "write_raw"):
        try:
            output.write_raw(sequence)
            if hasattr(output, "flush"):
                output.flush()
            return True
        except Exception:
            return False

    target = sys.stdout if stream is None else stream
    if not getattr(target, "isatty", lambda: False)():
        return False
    try:
        target.write(sequence)
        target.flush()
        return True
    except Exception:
        return False


def terminal_background_sequence(
    color: str,
    *,
    env: dict[str, str] | None = None,
) -> str:
    """Return the validated OSC 11 sequence owned by this terminal session."""
    clean = str(color or "").strip()
    if not terminal_background_sync_enabled(env) or not _HEX_COLOR_RE.match(clean):
        return ""
    return f"\033]11;{clean}\a"


def set_terminal_background(
    color: str,
    *,
    output: Any = None,
    stream: Any = None,
    env: dict[str, str] | None = None,
) -> bool:
    """Set the current terminal session's default background with OSC 11."""
    sequence = terminal_background_sequence(color, env=env)
    return bool(
        sequence
        and _write_terminal_sequence(sequence, output=output, stream=stream)
    )


def reset_terminal_background(
    *,
    output: Any = None,
    stream: Any = None,
    env: dict[str, str] | None = None,
) -> bool:
    """Reset the current terminal session's default background with OSC 111."""
    if not terminal_background_sync_enabled(env):
        return False
    return _write_terminal_sequence("\033]111\a", output=output, stream=stream)


TERMINAL_TITLE_MAX_CHARS = 128
_TERMINAL_STABLE_TITLE_MAX_CHARS = TERMINAL_TITLE_MAX_CHARS - 2
_TERMINAL_TITLE_SEPARATOR = " · "
_TASK_PROGRESS_RE = re.compile(r"^tasks [1-9]\d*/[1-9]\d*$")


def _terminal_title_field(value: object, limit: int) -> str:
    clean = "".join(
        character if character >= " " and character != "\x7f" else " "
        for character in str(value or "")
    )
    return " ".join(clean.split()).replace("·", "-")[:limit]


def _ellipsize_title_field(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    if limit <= 1:
        return "…"[:limit]
    return value[: limit - 1].rstrip() + "…"


def terminal_identity_title(
    worker_id: str = "",
    model: str = "",
    *,
    topic: str = "",
    task_current: int = 0,
    task_total: int = 0,
    provider: str = "",
) -> str:
    """Build the one bounded MO title shown by terminal hosts and workspaces."""
    instance = _terminal_title_field(worker_id, 64)
    clean_model = _terminal_title_field(model, 40)
    clean_provider = _terminal_title_field(provider, 24)
    runtime = "/".join(value for value in (clean_provider, clean_model) if value)
    clean_topic = _terminal_title_field(topic, 60)
    try:
        total = max(0, int(task_total))
        current = max(1, min(int(task_current), total)) if total else 0
    except (TypeError, ValueError):
        current = total = 0
    progress = f"tasks {current}/{total}" if current and total else ""

    def joined() -> str:
        parts = [TERMINAL_IDLE_TITLE]
        if clean_topic:
            parts.append(clean_topic)
            if progress:
                parts.append(progress)
        parts.extend(value for value in (instance, runtime) if value)
        return _TERMINAL_TITLE_SEPARATOR.join(parts)

    title = joined()
    if len(title) <= _TERMINAL_STABLE_TITLE_MAX_CHARS:
        return title

    if clean_topic:
        overflow = len(title) - _TERMINAL_STABLE_TITLE_MAX_CHARS
        clean_topic = _ellipsize_title_field(clean_topic, max(12, len(clean_topic) - overflow))
        title = joined()
    if len(title) > _TERMINAL_STABLE_TITLE_MAX_CHARS and runtime:
        overflow = len(title) - _TERMINAL_STABLE_TITLE_MAX_CHARS
        runtime = _ellipsize_title_field(runtime, max(12, len(runtime) - overflow))
        title = joined()
    if len(title) > _TERMINAL_STABLE_TITLE_MAX_CHARS and runtime:
        runtime = ""
        title = joined()
    if len(title) > _TERMINAL_STABLE_TITLE_MAX_CHARS and clean_topic:
        overflow = len(title) - _TERMINAL_STABLE_TITLE_MAX_CHARS
        clean_topic = _ellipsize_title_field(clean_topic, max(1, len(clean_topic) - overflow))
        title = joined()
    return title[:_TERMINAL_STABLE_TITLE_MAX_CHARS]


def _terminal_title_frame(window_title: str) -> tuple[str, bool]:
    current = str(window_title or "").strip()
    for frame in DOTS4_PHASES:
        prefix = f"{frame} "
        if current.startswith(prefix):
            return current[len(prefix):], True
    return current, False


def mo_terminal_identity(window_title: str) -> tuple[str, str, bool] | None:
    """Read instance/detail identity from legacy or richer MO OSC titles."""
    stable, busy = _terminal_title_frame(window_title)
    parts = [part.strip() for part in stable.split("·")]
    if len(parts) < 2 or parts[0] != TERMINAL_IDLE_TITLE or not parts[1]:
        return None
    task_index = next(
        (index for index, part in enumerate(parts[1:], start=1) if _TASK_PROGRESS_RE.match(part)),
        -1,
    )
    instance_index = task_index + 1 if task_index > 0 and task_index + 1 < len(parts) else -1
    if instance_index < 0 and len(parts) >= 4 and "/" in parts[-1]:
        instance_index = len(parts) - 2
    if instance_index > 0:
        instance = parts[instance_index]
        detail = _TERMINAL_TITLE_SEPARATOR.join(
            parts[1:instance_index] + parts[instance_index + 1:]
        )
        if instance:
            return instance[:64], detail[:80], busy
    return parts[1][:64], _TERMINAL_TITLE_SEPARATOR.join(parts[2:])[:80], busy


def terminal_busy_title(now: float | None = None, *, identity: str = TERMINAL_IDLE_TITLE) -> str:
    """Return the visible title for one frame of MO's working animation."""
    return f"{brand_spinner_frame(now)} {str(identity or TERMINAL_IDLE_TITLE).strip()}"


def terminal_title_matches(window_title: str, published_title: str) -> bool:
    """Match MO Desktop's stable identity against idle, detailed, or animated titles."""
    current, _busy = _terminal_title_frame(window_title)
    stable = str(published_title or "").strip()
    if not stable:
        return False
    return current == stable or current.startswith(f"{stable} · ")


def focus_terminal(instance_id: str) -> bool:
    """Focus one exact MO identity, never a guessed terminal-host process."""
    if sys.platform != "win32" or not instance_id:
        return False
    import ctypes
    import time
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.GetForegroundWindow.restype = wintypes.HWND
    matches = []

    @callback_type
    def visit(hwnd, _param):
        if user32.IsWindowVisible(hwnd):
            title = ctypes.create_unicode_buffer(user32.GetWindowTextLengthW(hwnd) + 1)
            user32.GetWindowTextW(hwnd, title, len(title))
            identity = mo_terminal_identity(title.value)
            if identity and identity[0] == instance_id:
                matches.append(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    if len(matches) != 1:
        return False
    user32.ShowWindow(matches[0], 9)
    user32.SetForegroundWindow(matches[0])
    # A terminal host may acknowledge activation after the native call returns.
    # Verify the exact window briefly, without another launcher or input hack.
    for _ in range(20):
        if user32.GetForegroundWindow() == matches[0]:
            return True
        time.sleep(0.01)
    return user32.GetForegroundWindow() == matches[0]


def publish_terminal_title(
    title: str,
    *,
    output: Any = None,
    stream: Any = None,
) -> bool:
    """Publish a safe terminal title through prompt-toolkit or a TTY stream."""
    clean = "".join(
        character
        for character in str(title or "")
        if character >= " " and character != "\x7f"
    ).strip()[:TERMINAL_TITLE_MAX_CHARS]
    if not clean:
        return False
    return _write_terminal_sequence(f"\033]0;{clean}\a", output=output, stream=stream)


def skin_terminal_background_sequence(
    *,
    env: dict[str, str] | None = None,
) -> str:
    """Return the active skin's validated OSC 11 sequence without writing it."""
    try:
        from .theming import get_skin

        return terminal_background_sequence(get_skin().bg_dark, env=env)
    except Exception:
        return ""


def sync_terminal_background_to_skin(*, output: Any = None, stream: Any = None) -> bool:
    """Apply the active skin's main background to the current terminal session."""
    sequence = skin_terminal_background_sequence()
    return bool(
        sequence
        and _write_terminal_sequence(sequence, output=output, stream=stream)
    )
