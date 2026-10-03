"""Shared runtime resource lock helpers.

Terminal MO instances are allowed to run concurrently; callers use
``acquire_runtime_lock`` only for singleton resources such as the headless
service, Telegram poller, or scheduler, and always name their lock file.

``file_byte_lock`` is the one shared cross-process byte-range lock body for
stores that guard a small file family with a sibling ``*.lock`` file
(taskboard ledger, session catalog, profile/learning transactions, transfer
operations, attachment index, Everywhere credential refresh). Sites keep only
their lock-path derivation and thread-lock selection.
"""
from __future__ import annotations

import atexit
import os
import sys
import tempfile
import threading
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class RuntimeLock:
    path: Path
    pid: int


@contextmanager
def file_byte_lock(
    lock_path: Path,
    thread_lock: "threading.Lock | threading.RLock | None" = None,
) -> Iterator[None]:
    """Hold an exclusive cross-process byte-range lock on ``lock_path``.

    Opens ``a+b``, seeds one byte so there is something to lock, holds an
    exclusive ``msvcrt``/``fcntl`` lock for the ``with`` body, and always
    unlocks. The byte lock is process-scoped, so an optional caller-owned
    ``thread_lock`` serializes threads inside this process.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    guard = thread_lock if thread_lock is not None else nullcontext()
    with guard, lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def acquire_runtime_lock(
    *,
    lock_name: str,
    lock_dir: str | Path | None = None,
    label: str = "MO Agent",
    skip_env: str = "MO_SKIP_LOCK",
    quiet: bool = False,
    fail_open: bool = True,
) -> RuntimeLock | None:
    """Acquire a singleton runtime resource lock.

    Returns a RuntimeLock when acquired, ``None`` when another live process owns
    the lock. Lock failures retain the historical fail-open default; strict
    singleton callers can pass ``fail_open=False``.
    """
    directory = Path(lock_dir) if lock_dir is not None else Path(tempfile.gettempdir())
    official = directory / lock_name
    if os.environ.get(skip_env) == "1":
        return RuntimeLock(official, os.getpid())

    fallback = RuntimeLock(official, os.getpid()) if fail_open else None

    try:
        owner = _live_owner(official)
        if owner:
            if not quiet:
                print(f"{label} is already running (pid {owner}). Use that instance or close it first.")
            return None
        if official.exists() and _recent_lock_claim(official):
            return None
        for _attempt in range(2):
            try:
                fd = os.open(str(official), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                owner = _live_owner(official)
                if owner:
                    if not quiet:
                        print(f"{label} is already running (pid {owner}). Use that instance or close it first.")
                    return None
                # O_EXCL creates the file before its PID can be written. Treat a
                # just-created unreadable/empty claim as contention, not stale
                # state, so concurrent singleton launches cannot both proceed.
                if _recent_lock_claim(official):
                    return None
                try:
                    official.unlink()
                except OSError:
                    return fallback
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(str(os.getpid()))
            _register_cleanup(official)
            return RuntimeLock(official, os.getpid())
        return fallback
    except Exception:
        return fallback


def release_runtime_lock(lock: RuntimeLock | None) -> None:
    """Release a lock acquired by this process, best-effort."""
    if lock is None:
        return
    try:
        path = lock.path
        if path.exists() and path.read_text(encoding="utf-8", errors="replace").strip() == str(lock.pid):
            path.unlink()
    except Exception:
        return


def runtime_lock_owner(lock_name: str) -> int | None:
    """Return a live singleton owner without acquiring or changing the lock."""
    clean_name = Path(str(lock_name or "")).name
    if not clean_name:
        return None
    return _live_owner(Path(tempfile.gettempdir()) / clean_name)


def _live_owner(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8", errors="replace").strip())
    except Exception:
        return None
    if pid <= 0 or pid == os.getpid():
        return None
    if _pid_alive(pid) and not _pid_started_after_record(pid, _path_mtime(path)):
        return pid
    return None


def _path_mtime(path: Path) -> float:
    try:
        return float(path.stat().st_mtime)
    except OSError:
        return 0.0


def _pid_started_after_record(
    pid: int,
    recorded_at: float,
    *,
    tolerance_seconds: float = 1.0,
) -> bool:
    """Return whether ``pid`` was created after a persisted ownership record.

    PID values are reusable. A live process that started after a lock or
    readiness marker was written cannot own that record. If process creation
    time is unavailable, retain the existing liveness-only behavior.
    """
    if recorded_at <= 0:
        return False
    started_at = _pid_started_at(pid)
    if started_at is None:
        return False
    return started_at > recorded_at + max(0.0, float(tolerance_seconds))


def _pid_started_at(pid: int) -> float | None:
    """Return the process creation time as a Unix timestamp when available."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        class _FileTime(ctypes.Structure):
            _fields_ = [
                ("low", ctypes.c_ulong),
                ("high", ctypes.c_ulong),
            ]

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            created = _FileTime()
            exited = _FileTime()
            kernel = _FileTime()
            user = _FileTime()
            queried = kernel32.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            )
            if not queried:
                return None
            ticks = (int(created.high) << 32) | int(created.low)
            if ticks <= 116_444_736_000_000_000:
                return None
            return (ticks - 116_444_736_000_000_000) / 10_000_000.0
        finally:
            kernel32.CloseHandle(handle)
    except (OSError, PermissionError):
        return None
    except Exception:
        return None


def _recent_lock_claim(path: Path, *, now: float | None = None, grace_seconds: float = 2.0) -> bool:
    """True while an incomplete O_EXCL lock creator may still be writing its PID."""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
        if raw:
            return False
    except OSError:
        pass
    try:
        age = (time.time() if now is None else float(now)) - path.stat().st_mtime
    except OSError:
        return False
    return 0.0 <= age <= max(0.0, float(grace_seconds))


def _pid_alive(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                # A terminated process object can remain open while another
                # component still holds a handle. OpenProcess therefore proves
                # only that the object exists, not that the process is running.
                exit_code = ctypes.c_ulong()
                queried = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                return bool(queried and exit_code.value == 259)  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except (OSError, PermissionError):
        return False
    except Exception:
        return False


def _register_cleanup(path: Path) -> None:
    def _cleanup() -> None:
        release_runtime_lock(RuntimeLock(path, os.getpid()))

    atexit.register(_cleanup)
