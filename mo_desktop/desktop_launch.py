"""Single source of truth for launching the standalone MO Desktop process.

Reads the MO Desktop config block, checks whether a desktop process is already
alive (via its runtime lock), and spawns ``python -m mo_desktop
--show`` DETACHED so it outlives the caller (e.g. the terminal). Deliberately free
of any companion/tray/voice imports, so the terminal can offer a Win+Alt+M
launcher / ``/desktop`` without loading the heavy GUI stack at startup
(enforced by tests/test_mo_startup.py).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.runtime.subprocess_flags import apply_windows_hidden_process_flags, console_python_executable

_UNSET = object()
_LAUNCH_MARKER_NAME = "mo-desktop-launching.lock"
_LAUNCH_MARKER_TTL_SECONDS = 8.0
_SUMMON_MARKER_NAME = "mo-desktop-summon.request"
_SUMMON_MARKER_TTL_SECONDS = 10.0


def desktop_transient_dir() -> Path:
    """Return one user-stable directory for Desktop locks and IPC markers.

    Sandboxed callers may override ``TEMP`` for their own scratch files.  On
    Windows that must not split Desktop singleton and summon coordination from
    the already-running user process, whose normal temp root is LocalAppData.
    """
    if sys.platform == "win32":
        local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
        if local_app_data:
            candidate = Path(local_app_data) / "Temp"
            if candidate.is_dir():
                return candidate
    return Path(tempfile.gettempdir())


@dataclass(frozen=True)
class DesktopResidentStatus:
    state: str
    lock_owner: int | None
    ready_owner: int | None
    generation: str = ""

    @property
    def blocks_launch(self) -> bool:
        return self.state != "absent"


def mo_desktop_config_block(config: Any) -> dict:
    """Read the ``mo_desktop`` config block."""
    if not isinstance(config, dict):
        return {}
    block = config.get("mo_desktop")
    return block if isinstance(block, dict) else {}


def mo_desktop_running() -> bool:
    """True when any live resident ownership evidence blocks a new launch."""
    return desktop_resident_status().blocks_launch


def desktop_resident_status(
    config: dict[str, Any] | None = None,
) -> DesktopResidentStatus:
    """Project lock and ready evidence through one fail-closed authority."""
    try:
        from mo_desktop.desktop_log import desktop_lock_record, ready_record

        lock = desktop_lock_record()
        ready = ready_record(config)
    except Exception:
        return DesktopResidentStatus("indeterminate", None, None)
    if lock is None and ready is None:
        return DesktopResidentStatus("absent", None, None)
    if lock is None:
        return DesktopResidentStatus("inconsistent", None, ready.pid, ready.generation)
    if ready is None:
        return DesktopResidentStatus("starting", lock.pid, None, lock.generation)
    if lock.pid != ready.pid or lock.generation != ready.generation:
        return DesktopResidentStatus(
            "inconsistent", lock.pid, ready.pid, lock.generation,
        )
    return DesktopResidentStatus("ready", lock.pid, ready.pid, lock.generation)


def _launch_marker_path() -> Path:
    return desktop_transient_dir() / _LAUNCH_MARKER_NAME


def _summon_marker_path() -> Path:
    return desktop_transient_dir() / _SUMMON_MARKER_NAME


def request_mo_desktop_summon(owner: int, design_id: str = "", *, terminal_synced: bool = False) -> bool:
    """Ask resident Desktop to summon itself or focus one saved Design."""
    focus_id = str(design_id or "").strip()
    if focus_id and (len(focus_id) > 80 or not all(char.isalnum() or char in "_-" for char in focus_id)):
        return False
    marker = _summon_marker_path()
    pending = marker.with_name(f"{marker.name}.{os.getpid()}.tmp")
    try:
        pending.write_text(
            f"{int(owner)}\n{time.time()}\n{focus_id}\n{'terminal' if terminal_synced else 'studio'}\n",
            encoding="utf-8",
        )
        os.replace(str(pending), str(marker))
        return True
    except Exception:
        try:
            pending.unlink()
        except OSError:
            pass
        return False


def consume_mo_desktop_summon(
    *, owner: int | None = None, now: float | None = None,
) -> bool | dict[str, Any]:
    """Consume one fresh summon or Design-focus request for this Desktop."""
    marker = _summon_marker_path()
    try:
        lines = marker.read_text(encoding="utf-8", errors="replace").splitlines()
        requested_owner = int(lines[0])
        requested_at = float(lines[1])
        design_id = lines[2].strip() if len(lines) > 2 else ""
        terminal_synced = len(lines) > 3 and lines[3].strip() == "terminal"
    except Exception:
        return False
    finally:
        try:
            marker.unlink()
        except OSError:
            pass
    current_owner = os.getpid() if owner is None else int(owner)
    current_time = time.time() if now is None else float(now)
    if requested_owner != current_owner or not 0.0 <= current_time - requested_at <= _SUMMON_MARKER_TTL_SECONDS:
        return False
    if not design_id:
        return True
    if len(design_id) > 80 or not all(char.isalnum() or char in "_-" for char in design_id):
        return False
    return {"design_id": design_id, "terminal_synced": terminal_synced}


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


def _spawn_mo_desktop_detached(*, config_path: str | Path | None = None) -> None:
    """Spawn one console-less Desktop process; lifecycle callers own gating."""
    repo_root = Path(__file__).resolve().parents[1]
    command = [console_python_executable(), "-m", "mo_desktop", "--show"]
    if config_path:
        command.extend(["--config", str(config_path)])
    popen_kw: dict = dict(
        cwd=str(repo_root),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    # CREATE_NO_WINDOW keeps the resident itself console-less. It does not provide
    # an inheritable hidden console, so every captured internal console child must
    # independently use apply_windows_hidden_process_flags.
    apply_windows_hidden_process_flags(popen_kw)
    subprocess.Popen(command, **popen_kw)


def relaunch_mo_desktop_detached(*, config_path: str | Path | None = None) -> str:
    """Replace a resident after its caller has released the singleton lock."""
    try:
        _spawn_mo_desktop_detached(config_path=config_path)
    except Exception as exc:
        return f"Could not restart MO Desktop: {type(exc).__name__}: {exc}"
    return "Restarting MO Desktop with the current configuration."


def launch_mo_desktop_detached(config: Any = _UNSET) -> str:
    """Spawn MO Desktop as its OWN detached process that outlives the caller.

    The mo-desktop.lock makes a duplicate launch a no-op, so this is safe when one is
    already running. When ``config`` is passed, it must be enabled or this refuses
    with a message (the slash-command path); when called with no argument the
    caller has already gated on enabled (the hotkey path), so it spawns directly.
    Returns a user-facing status string.
    """
    if config is not _UNSET and not mo_desktop_config_block(config).get("enabled", False):
        return (
            "MO Desktop is disabled. Set mo_desktop.enabled: true "
            "in your config, then try again."
        )
    resident = desktop_resident_status(
        config if isinstance(config, dict) else None
    )
    if resident.state == "ready" and resident.lock_owner is not None:
        if request_mo_desktop_summon(resident.lock_owner):
            return "Summoning the existing MO Desktop companion."
        return "MO Desktop is running, but its summon request could not be delivered — use Win+Alt+M."
    if resident.state == "starting":
        return "MO Desktop is starting — wait a moment, then summon it with Win+Alt+M."
    if resident.state != "absent":
        return (
            "MO Desktop ownership is inconsistent; refusing to launch a duplicate. "
            "Restart the existing Desktop resident cleanly."
        )
    if not _mark_launch_attempt():
        return "MO Desktop is already launching — wait a moment, then summon it with Win+Alt+M."
    try:
        from core.state.paths import runtime_config_path

        _spawn_mo_desktop_detached(
            config_path=runtime_config_path(config) if isinstance(config, dict) else None
        )
    except Exception as exc:
        _clear_launch_marker()
        return f"Could not launch MO Desktop: {type(exc).__name__}: {exc}"
    return ("Launching MO Desktop as its own process — Win+Alt+M to summon, a tray "
            "icon will appear. It keeps running after you close this terminal.")
