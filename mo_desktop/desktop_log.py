"""Private runtime logging for the standalone MO Desktop process."""
from __future__ import annotations

import time
import traceback
import os
import hashlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DEFAULT_LOG_MAX_BYTES = 1_000_000
_DEFAULT_LOG_MAX_LINES = 2_000
_READY_MARKER_NAME = "mo-desktop.ready"


def write_stderr(message: str) -> None:
    """Best-effort process stderr without importing the Desktop GUI owner."""
    stream = getattr(sys, "stderr", None)
    if stream is None:
        return
    try:
        stream.write(message)
    except Exception:
        pass


@dataclass(frozen=True)
class DesktopOwnershipRecord:
    pid: int
    generation: str
    recorded_at: float


def _pid_alive(pid: int) -> bool:
    from core.runtime.lock import _pid_alive as check

    return check(pid)


def _pid_started_after_record(pid: int, recorded_at: float) -> bool:
    from core.runtime.lock import _pid_started_after_record as check

    return check(pid, recorded_at)


def _positive_env_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _prune_desktop_log(path: Path) -> None:
    """Bound the standalone lifecycle log without adding a logging dependency."""
    max_bytes = _positive_env_int("MO_DESKTOP_LOG_MAX_BYTES", _DEFAULT_LOG_MAX_BYTES)
    try:
        if path.stat().st_size <= max_bytes:
            return
        max_lines = _positive_env_int("MO_DESKTOP_LOG_MAX_LINES", _DEFAULT_LOG_MAX_LINES)
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
        while lines and len(("\n".join(lines) + "\n").encode("utf-8")) > max_bytes:
            del lines[: max(1, len(lines) // 8)]
        replacement = path.with_name(path.name + ".tmp")
        replacement.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        os.replace(replacement, path)
    except Exception:
        return


def log_event(message: str, *, config: dict[str, Any] | None = None) -> None:
    """Append a small redacted diagnostic line under ~/.mo/logs."""
    try:
        from core.state.paths import resolve_state_path
        from core.tooling.sandbox import redact_sensitive_text

        path = Path(resolve_state_path("logs/mo_desktop.log", config))
        path.parent.mkdir(parents=True, exist_ok=True)
        safe = redact_sensitive_text(str(message or "")).replace("\r", " ").replace("\n", " ")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {safe[:1200]}\n")
        _prune_desktop_log(path)
    except Exception:
        return


def log_exception(label: str, *, config: dict[str, Any] | None = None) -> None:
    log_event(f"{label}: {traceback.format_exc()}", config=config)


def ready_marker_path(config: dict[str, Any] | None = None) -> Path:
    """Return the user-stable readiness marker shared by Desktop callers."""
    from mo_desktop.desktop_launch import desktop_transient_dir

    _ = config  # Retained for the established public helper signature.
    return desktop_transient_dir() / _READY_MARKER_NAME


def mark_ready(
    *,
    config: dict[str, Any] | None = None,
    source_stamp: str | None = None,
) -> bool:
    """Publish GUI readiness without forcing source inspection on the GUI thread.

    ``source_stamp=None`` preserves the synchronous helper contract for callers
    that explicitly need a complete marker.  MO Desktop publishes ``pending``
    first and replaces it from a short worker so Git latency can never hold the
    GUI event loop closed after the surfaces are ready.
    """
    try:
        from core.utils.atomic_write import atomic_write_text

        lock = desktop_lock_record()
        if lock is None or lock.pid != os.getpid():
            return False
        path = ready_marker_path(config)
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = current_source_stamp() if source_stamp is None else str(source_stamp or "pending")
        atomic_write_text(
            path,
            f"{os.getpid()}\n{time.time()}\n{stamp}\n{lock.generation}\n",
        )
        return True
    except Exception:
        return False


def clear_ready(*, config: dict[str, Any] | None = None) -> None:
    for path in (ready_marker_path(config),):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            if not lines or int(lines[0]) == os.getpid():
                path.unlink(missing_ok=True)
        except Exception:
            continue


def desktop_lock_record() -> DesktopOwnershipRecord | None:
    """Return the valid Desktop singleton record, including its generation."""
    from mo_desktop.desktop_launch import desktop_transient_dir

    path = desktop_transient_dir() / "mo-desktop.lock"
    try:
        pid = int(path.read_text(encoding="utf-8", errors="replace").strip())
        stat = path.stat()
    except Exception:
        return None
    recorded_at = float(stat.st_mtime)
    alive = pid == os.getpid() or (
        pid > 0
        and _pid_alive(pid)
        and not _pid_started_after_record(pid, recorded_at)
    )
    if not alive:
        return None
    return DesktopOwnershipRecord(
        pid=pid,
        generation=str(int(stat.st_mtime_ns)),
        recorded_at=recorded_at,
    )


def ready_record(config: dict[str, Any] | None = None) -> DesktopOwnershipRecord | None:
    for path in (ready_marker_path(config),):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            pid = int(lines[0])
        except Exception:
            continue
        try:
            started_at = float(lines[1])
        except (IndexError, TypeError, ValueError):
            started_at = 0.0
        generation = str(lines[3] if len(lines) > 3 else "").strip()
        if (
            pid > 0
            and generation
            and _pid_alive(pid)
            and not _pid_started_after_record(pid, started_at)
        ):
            return DesktopOwnershipRecord(pid, generation, started_at)
        try:
            path.unlink(missing_ok=True)  # clean a dead marker
        except Exception:
            pass
    return None


def ready_owner(config: dict[str, Any] | None = None) -> int | None:
    record = ready_record(config)
    return record.pid if record is not None else None


def current_source_stamp() -> str:
    """Identify the tracked revision plus relevant uncommitted source state.

    Only a short commit id and a digest are retained; file names and source
    contents never enter the private ready marker or diagnostics report.
    """
    root = Path(__file__).resolve().parents[1]
    head = _git_output(root, ["rev-parse", "HEAD"])
    if not head:
        return "unavailable"
    changes = _git_output(
        root,
        [
            "status",
            "--porcelain=v1",
            "--untracked-files=normal",
            "--",
            "core",
            "interface",
            "mo_desktop",
            "mo_everywhere",
            "tools",
            "mo.py",
        ],
        allow_empty=True,
    )
    if changes is None:
        return head[:12] + ".unknown"
    if not changes:
        return head[:12] + ".clean"
    paths = ["core", "interface", "mo_desktop", "mo_everywhere", "tools", "mo.py"]
    diff = _git_output(root, ["diff", "HEAD", "--binary", "--", *paths], allow_empty=True)
    untracked = _git_output(
        root, ["ls-files", "--others", "--exclude-standard", "-z", "--", *paths],
        allow_empty=True,
    )
    if diff is None or untracked is None:
        return head[:12] + ".unknown"
    digest = hashlib.sha256(changes.encode("utf-8", errors="replace"))
    digest.update(diff.encode("utf-8", errors="replace"))
    try:
        for name in sorted(filter(None, untracked.split("\0"))):
            digest.update(name.encode("utf-8", errors="replace"))
            digest.update((root / name).read_bytes())
    except OSError:
        return head[:12] + ".unknown"
    return head[:12] + ".dirty." + digest.hexdigest()[:12]


def ready_metadata(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Describe the resident process and whether it matches this checkout."""
    marker = None
    lines: list[str] = []
    for candidate in (ready_marker_path(config),):
        try:
            lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()
            marker = candidate
            break
        except OSError:
            continue
    if marker is None:
        lock = desktop_lock_record()
        if lock is None:
            return {"state": "not-running", "alive": False}
        return {
            "state": "starting",
            "alive": True,
            "pid": lock.pid,
            "started_at": lock.recorded_at,
            "lock_owner": lock.pid,
            "lock_generation": lock.generation,
        }
    try:
        pid = int(lines[0])
    except (IndexError, TypeError, ValueError):
        return {"state": "invalid-marker", "alive": False}
    try:
        started_at = float(lines[1])
    except (IndexError, TypeError, ValueError):
        started_at = 0.0
    resident_stamp = str(lines[2] if len(lines) > 2 else "").strip()
    generation = str(lines[3] if len(lines) > 3 else "").strip()
    alive = (
        pid > 0
        and _pid_alive(pid)
        and not _pid_started_after_record(pid, started_at)
    )
    metadata: dict[str, Any] = {
        "state": "stopped" if not alive else "unknown-revision",
        "alive": alive,
        "pid": pid,
        "started_at": started_at,
        "source_stamp": resident_stamp,
        "lock_generation": generation,
    }
    if not alive:
        return metadata
    lock = desktop_lock_record()
    metadata["lock_owner"] = lock.pid if lock is not None else None
    if (
        lock is None
        or not generation
        or lock.pid != pid
        or lock.generation != generation
    ):
        metadata["state"] = "inconsistent"
        return metadata
    if resident_stamp == "pending":
        metadata["state"] = "starting"
        return metadata
    checkout_stamp = current_source_stamp()
    metadata["checkout_stamp"] = checkout_stamp
    if all(stamp and stamp != "unavailable" and not stamp.endswith(".unknown")
           for stamp in (resident_stamp, checkout_stamp)):
        metadata["state"] = "current" if resident_stamp == checkout_stamp else "stale"
    return metadata


def _git_output(root: Path, args: list[str], *, allow_empty: bool = False) -> str | None:
    from core.runtime.subprocess_flags import apply_windows_hidden_process_flags

    kwargs: dict[str, Any] = {
        "cwd": str(root),
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 5.0,
        "check": False,
    }
    apply_windows_hidden_process_flags(kwargs)
    try:
        result = subprocess.run(["git", *args], **kwargs)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    output = str(result.stdout or "").strip()
    return output if output or allow_empty else None
