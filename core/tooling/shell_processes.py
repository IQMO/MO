"""Shared shell process tracking for MO."""

import os
import subprocess
import sys
import threading
import time
from typing import Any

from ..runtime.subprocess_flags import apply_windows_hidden_process_flags

_SHELL_PROCESS_LOCK = threading.Lock()
_SHELL_PROCESSES: dict[int, dict[str, Any]] = {}


def _register_shell_process(
    proc: subprocess.Popen,
    command: str,
    cwd: str,
    timeout: int,
    *,
    background: bool = False,
):
    with _SHELL_PROCESS_LOCK:
        _SHELL_PROCESSES[proc.pid] = {
            "pid": proc.pid,
            "command": " ".join((command or "").split())[:160],
            "cwd": cwd,
            "started": time.time(),
            "timeout": timeout,
            "thread_id": threading.get_ident(),
            "background": bool(background),
            "last_output_at": 0.0,
            "output_tail": "",
            "proc": proc,
        }


def _unregister_shell_process(pid: int):
    with _SHELL_PROCESS_LOCK:
        _SHELL_PROCESSES.pop(pid, None)


def _record_shell_output(pid: int, text: str) -> None:
    """Retain live output in the existing process record, never a second log."""
    with _SHELL_PROCESS_LOCK:
        info = _SHELL_PROCESSES.get(pid)
        if info is not None:
            info["output_tail"] = (str(info["output_tail"]) + text)[-4096:]
            info["last_output_at"] = time.time()


def kill_process_tree(pid: int) -> bool:
    """Terminate a launched process group/tree without raising."""
    try:
        if sys.platform == "win32":
            kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "timeout": 5,
            }
            apply_windows_hidden_process_flags(kwargs)
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                **kwargs,
            )
            deadline = time.time() + 2.0
            while _windows_pid_alive(pid) and time.time() < deadline:
                time.sleep(0.05)
            if not _windows_pid_alive(pid):
                return True
            return _windows_terminate_pid(pid)
        os.killpg(os.getpgid(pid), 15)
        return True
    except Exception:
        try:
            os.kill(pid, 15)
            return True
        except Exception:
            return False


def _windows_pid_alive(pid: int) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000, False, int(pid))  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x00000102  # WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def _windows_terminate_pid(pid: int) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x0001, False, int(pid))  # PROCESS_TERMINATE
        if not handle:
            return False
        try:
            return bool(kernel32.TerminateProcess(handle, 1))
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def active_shell_processes() -> list[dict[str, Any]]:
    from ..utils.text_safety import redact_secret_values

    active = []
    stale: list[int] = []
    with _SHELL_PROCESS_LOCK:
        for pid, info in list(_SHELL_PROCESSES.items()):
            proc = info.get("proc")
            if proc is not None and proc.poll() is None:
                snapshot = {k: v for k, v in info.items() if k != "proc"}
                snapshot["output_tail"] = redact_secret_values(snapshot.get("output_tail") or "")
                active.append(snapshot)
            else:
                stale.append(pid)
        for pid in stale:
            _SHELL_PROCESSES.pop(pid, None)
    return active


def kill_shell_process(pid: int) -> bool:
    """Kill one tracked shell process by PID and remove it from the registry.

    Returns True if the process was found and killed, False if it was not
    tracked, already dead, or the kill failed.
    """
    with _SHELL_PROCESS_LOCK:
        info = _SHELL_PROCESSES.get(pid)
        if not info:
            return False
        proc = info.get("proc")
        if proc is not None and proc.poll() is not None:
            _SHELL_PROCESSES.pop(pid, None)
            return False
    killed = kill_process_tree(pid)
    with _SHELL_PROCESS_LOCK:
        _SHELL_PROCESSES.pop(pid, None)
    return killed


def cleanup_shell_processes() -> dict[str, Any]:
    active = active_shell_processes()
    killed = []
    for info in active:
        pid = int(info["pid"])
        try:
            if kill_process_tree(pid):
                killed.append({k: v for k, v in info.items() if k != "proc"})
        finally:
            with _SHELL_PROCESS_LOCK:
                _SHELL_PROCESSES.pop(pid, None)
    return {"killed": len(killed), "active_after": len(active_shell_processes())}
