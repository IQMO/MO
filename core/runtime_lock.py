"""Shared MO Agent process lock helpers.

The lock is intentionally process-level, not Telegram-specific: a TUI instance
and a headless service using the same config can otherwise start two Telegram
pollers. Callers may pass ``legacy_lock_names`` to also honor older lock-file
names during a migration.
"""
from __future__ import annotations

import atexit
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import traceback


@dataclass(frozen=True)
class RuntimeLock:
    path: Path
    pid: int


def acquire_runtime_lock(
    *,
    lock_name: str = "mo-agent.lock",
    legacy_lock_names: Iterable[str] = (),
    label: str = "MO Agent",
    skip_env: str = "MO_SKIP_LOCK",
) -> RuntimeLock | None:
    """Acquire the shared MO Agent process lock.

    Returns a RuntimeLock when acquired, ``None`` when another live process owns
    any official/legacy lock. Lock failures fail open so a stale temp/permission
    problem does not brick local startup.
    """
    if os.environ.get(skip_env) == "1":
        return RuntimeLock(Path(tempfile.gettempdir()) / lock_name, os.getpid())

    lock_dir = Path(tempfile.gettempdir())
    official = lock_dir / lock_name
    candidates = [official]
    for name in legacy_lock_names or ():
        path = lock_dir / str(name)
        if path not in candidates:
            candidates.append(path)

    try:
        for path in candidates:
            owner = _live_owner(path)
            if owner:
                print(f"{label} is already running (pid {owner}). Use that instance or close it first.")
                return None
        official.write_text(str(os.getpid()), encoding="utf-8")
        _register_cleanup(official)
        return RuntimeLock(official, os.getpid())
    except Exception:
        return RuntimeLock(official, os.getpid())


def _live_owner(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8", errors="replace").strip())
    except Exception:
        return None
    if pid <= 0 or pid == os.getpid():
        return None
    if _pid_alive(pid):
        return pid
    return None


def _pid_alive(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        os.kill(pid, 0)
        return True
    except (OSError, PermissionError):
        return False
    except Exception:
        return False


def _register_cleanup(path: Path) -> None:
    def _cleanup() -> None:
        try:
            if path.exists() and path.read_text(encoding="utf-8", errors="replace").strip() == str(os.getpid()):
                path.unlink()
        except Exception:
            traceback.print_exc()

    atexit.register(_cleanup)
