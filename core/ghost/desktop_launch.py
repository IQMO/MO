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
import sys
import tempfile
from pathlib import Path
from typing import Any

_UNSET = object()


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
        from core.runtime_lock import _live_owner
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
    repo_root = Path(__file__).resolve().parents[2]
    popen_kw: dict = dict(
        cwd=str(repo_root),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    if os.name == "nt":
        popen_kw["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | NEW_PROCESS_GROUP
    try:
        subprocess.Popen([sys.executable, "-m", "interface.ghost_desktop", "--show"], **popen_kw)
    except Exception as exc:
        return f"Could not launch Ghost Desktop: {type(exc).__name__}: {exc}"
    return ("Launching Ghost Desktop as its own process — Win+Alt+M to summon, a tray "
            "icon will appear. It keeps running after you close this terminal.")
