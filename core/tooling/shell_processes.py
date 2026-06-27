"""Shared shell process tracking for MO."""

import os
import subprocess
import sys
import threading
import traceback
import time
from typing import Any

_SHELL_PROCESS_LOCK = threading.Lock()
_SHELL_PROCESSES: dict[int, dict[str, Any]] = {}


def _register_shell_process(proc: subprocess.Popen, command: str, cwd: str, timeout: int):
    with _SHELL_PROCESS_LOCK:
        _SHELL_PROCESSES[proc.pid] = {
            "pid": proc.pid,
            "command": " ".join((command or "").split())[:160],
            "cwd": cwd,
            "started": time.time(),
            "timeout": timeout,
            "proc": proc,
        }


def _unregister_shell_process(pid: int):
    with _SHELL_PROCESS_LOCK:
        _SHELL_PROCESSES.pop(pid, None)


def _kill_process_tree(pid: int) -> bool:
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            return True
        os.killpg(os.getpgid(pid), 15)
        return True
    except Exception:
        try:
            os.kill(pid, 15)
            return True
        except Exception:
            return False


def active_shell_processes() -> list[dict[str, Any]]:
    active = []
    stale: list[int] = []
    with _SHELL_PROCESS_LOCK:
        for pid, info in list(_SHELL_PROCESSES.items()):
            proc = info.get("proc")
            if proc is not None and proc.poll() is None:
                active.append({k: v for k, v in info.items() if k != "proc"})
            else:
                stale.append(pid)
        for pid in stale:
            _SHELL_PROCESSES.pop(pid, None)
    return active


def cleanup_shell_processes() -> dict[str, Any]:
    active = active_shell_processes()
    killed = []
    for info in active:
        pid = int(info["pid"])
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5,
                )
            else:
                os.kill(pid, 15)
            killed.append({k: v for k, v in info.items() if k != "proc"})
        except Exception:
            # Kill failed, but still remove from registry to avoid retrying
            traceback.print_exc()
        finally:
            # Always remove from registry, even if kill failed
            with _SHELL_PROCESS_LOCK:
                _SHELL_PROCESSES.pop(pid, None)
    return {"killed": len(killed), "active_after": len(active_shell_processes())}
