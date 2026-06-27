"""Cross-platform subprocess flags for background helper processes."""

from __future__ import annotations

import subprocess
import sys
from typing import Any


CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def windows_creationflags(
    *,
    no_window: bool = True,
    detached: bool = False,
    new_process_group: bool = True,
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
) -> dict[str, Any]:
    """Mutate Popen kwargs so Windows helper processes do not open terminals."""
    if sys.platform != "win32":
        return kwargs
    kwargs["creationflags"] = int(kwargs.get("creationflags") or 0) | windows_creationflags(
        no_window=True,
        detached=detached,
        new_process_group=new_process_group,
    )
    startupinfo = windows_hidden_startupinfo()
    if startupinfo is not None:
        kwargs.setdefault("startupinfo", startupinfo)
    return kwargs
