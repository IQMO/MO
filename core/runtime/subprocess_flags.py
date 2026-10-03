"""Cross-platform subprocess flags for background helper processes."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Callable


CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


def windows_creationflags(
    *,
    no_window: bool = True,
    detached: bool = False,
    new_process_group: bool = True,
    below_normal_priority: bool = False,
) -> int:
    """Return Windows creation flags for helper processes; 0 elsewhere."""
    if sys.platform != "win32":
        return 0
    flags = 0
    if no_window:
        flags |= int(getattr(subprocess, "CREATE_NO_WINDOW", CREATE_NO_WINDOW))
    if detached:
        flags |= int(getattr(subprocess, "DETACHED_PROCESS", DETACHED_PROCESS))
    if new_process_group:
        flags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", CREATE_NEW_PROCESS_GROUP))
    if below_normal_priority:
        flags |= int(getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", BELOW_NORMAL_PRIORITY_CLASS))
    return flags


def windows_hidden_startupinfo() -> Any | None:
    """Best-effort STARTUPINFO that asks Windows not to show a console window."""
    if sys.platform != "win32" or not hasattr(subprocess, "STARTUPINFO"):
        return None
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
    startupinfo.wShowWindow = 0
    return startupinfo


def apply_windows_hidden_process_flags(
    kwargs: dict[str, Any],
    *,
    detached: bool = False,
    new_process_group: bool = True,
    below_normal_priority: bool = False,
) -> dict[str, Any]:
    """Mutate Popen kwargs so Windows helper processes do not open terminals."""
    if sys.platform != "win32":
        return kwargs
    kwargs["creationflags"] = int(kwargs.get("creationflags") or 0) | windows_creationflags(
        no_window=True,
        detached=detached,
        new_process_group=new_process_group,
        below_normal_priority=below_normal_priority,
    )
    startupinfo = windows_hidden_startupinfo()
    if startupinfo is not None:
        kwargs.setdefault("startupinfo", startupinfo)
    return kwargs


def gui_python_executable() -> str:
    """Return pythonw.exe for GUI helpers on Windows when it is available."""
    if sys.platform != "win32":
        return sys.executable
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return str(candidate) if candidate.exists() else sys.executable


def run_with_file_capture(
    command: list[str], *, timeout: float, cwd: str | None = None,
    encoding: str | None = "utf-8", errors: str = "replace",
    cancelled: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess[Any]:
    """Capture a bounded helper without waiting for inherited pipe writers.

    Windows GUI descendants can retain anonymous output pipes after the helper
    exits. File-backed output lets process completion and timeout remain bounded.
    """
    import tempfile
    import time

    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as error_output:
        kwargs = {"cwd": cwd, "stdin": subprocess.DEVNULL, "stdout": output,
                  "stderr": error_output}
        apply_windows_hidden_process_flags(kwargs)
        if cancelled is not None:
            if cancelled():
                raise InterruptedError("Native inspection cancelled before launch")
            if sys.platform != "win32":
                kwargs["start_new_session"] = True
        with subprocess.Popen(command, **kwargs) as process:
            deadline = time.monotonic() + timeout
            try:
                if cancelled is None:
                    process.wait(timeout=timeout)
                else:
                    while True:
                        if cancelled():
                            raise InterruptedError("Native inspection cancelled")
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise subprocess.TimeoutExpired(command, timeout)
                        try:
                            process.wait(timeout=min(0.1, remaining))
                            break
                        except subprocess.TimeoutExpired:
                            continue
            except BaseException:
                if process.poll() is None:
                    if cancelled is not None:
                        from core.tooling.shell_processes import kill_process_tree
                        kill_process_tree(process.pid)
                    if process.poll() is None:
                        process.kill()
                process.wait()
                raise
        output.seek(0)
        error_output.seek(0)
        stdout, stderr = output.read(), error_output.read()
        return subprocess.CompletedProcess(
            command, process.returncode,
            stdout.decode(encoding, errors=errors) if encoding else stdout,
            stderr.decode(encoding, errors=errors) if encoding else stderr,
        )


def console_python_executable() -> str:
    """Return the sibling console interpreter when running under pythonw.exe."""
    if sys.platform != "win32":
        return sys.executable
    current = Path(sys.executable)
    if current.name.lower() == "pythonw.exe":
        candidate = current.with_name("python.exe")
        if candidate.is_file():
            return str(candidate)
    return str(current)
