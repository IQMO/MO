"""Single source of truth for launching the standalone desktop Ghost process.

Reads the desktop-Ghost config block, checks whether a Ghost process is already
alive (via its runtime lock), and spawns ``python -m interface.ghost_desktop
--show`` DETACHED so it outlives the caller (e.g. the terminal). Deliberately free
of any ``interface.ghost_desktop`` import, so the terminal can offer a Win+Alt+M
launcher / ``/ghost launch`` without loading the heavy GUI stack at startup
(enforced by tests/test_mo_startup.py).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from core.runtime.subprocess_flags import apply_windows_hidden_process_flags, gui_python_executable

_UNSET = object()
_LAUNCH_MARKER_NAME = "ghost-launching.lock"
_LAUNCH_MARKER_TTL_SECONDS = 8.0


def ghost_config_block(config: Any) -> dict:
    """Read the desktop-Ghost config block (new ``ghost`` key, legacy ``desktop_companion``)."""
    if not isinstance(config, dict):
        return {}
    block = config.get("ghost")
    if not isinstance(block, dict):
        block = config.get("desktop_companion")
    return block if isinstance(block, dict) else {}


def ghost_desktop_running() -> bool:
    """True if a desktop Ghost process currently holds its runtime lock."""
    try:
        from core.runtime.lock import _live_owner
    except Exception:
        return False
    tmp = Path(tempfile.gettempdir())
    for name in ("ghost.lock", "mo-companion.lock"):
        try:
            if _live_owner(tmp / name):
                return True
        except Exception:
            continue
    return False


def _launch_marker_path() -> Path:
    return Path(tempfile.gettempdir()) / _LAUNCH_MARKER_NAME


def _launch_recent(path: Path | None = None, *, now: float | None = None) -> bool:
    marker = path or _launch_marker_path()
    try:
        started = float(marker.read_text(encoding="utf-8", errors="replace").splitlines()[0])
    except Exception:
        return False
    current = time.time() if now is None else float(now)
    if current - started <= _LAUNCH_MARKER_TTL_SECONDS:
        return True
    try:
        marker.unlink()
    except OSError:
        pass
    return False


def _mark_launch_attempt(path: Path | None = None) -> bool:
    marker = path or _launch_marker_path()
    if _launch_recent(marker):
        return False
    try:
        fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if _launch_recent(marker):
            return False
        try:
            marker.unlink()
            fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError:
            return False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{time.time()}\n{os.getpid()}\n")
        return True
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        return False


def _clear_launch_marker(path: Path | None = None) -> None:
    try:
        (path or _launch_marker_path()).unlink()
    except OSError:
        pass


def launch_ghost_desktop_detached(config: Any = _UNSET) -> str:
    """Spawn Ghost Desktop as its OWN detached process that outlives the caller.

    The ghost.lock makes a duplicate launch a no-op, so this is safe when one is
    already running. When ``config`` is passed, it must be enabled or this refuses
    with a message (the slash-command path); when called with no argument the
    caller has already gated on enabled (the hotkey path), so it spawns directly.
    Returns a user-facing status string.
    """
    if config is not _UNSET and not ghost_config_block(config).get("enabled", False):
        return ("Ghost Desktop is disabled. Set desktop_companion.enabled: true "
                "(or ghost.enabled) in your config, then try again.")
    if ghost_desktop_running():
        return "Ghost Desktop is already running — Win+Alt+M to summon it."
    if not _mark_launch_attempt():
        return "Ghost Desktop is already launching — wait a moment, then summon it with Win+Alt+M."
    repo_root = Path(__file__).resolve().parents[2]
    popen_kw: dict = dict(
        cwd=str(repo_root),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    apply_windows_hidden_process_flags(popen_kw, detached=True)
    try:
        subprocess.Popen([gui_python_executable(), "-m", "interface.ghost_desktop", "--show"], **popen_kw)
    except Exception as exc:
        _clear_launch_marker()
        return f"Could not launch Ghost Desktop: {type(exc).__name__}: {exc}"
    return ("Launching Ghost Desktop as its own process — Win+Alt+M to summon, a tray "
            "icon will appear. It keeps running after you close this terminal.")
