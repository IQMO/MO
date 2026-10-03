"""One-shot local context/control handoff to an exact live terminal instance."""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from core.state.paths import MO_DESIGN_HANDOFF_DIR, MO_DESIGN_RUNTIME_DIR, resolve_state_path
from core.runtime.lock import _pid_alive, file_byte_lock
from core.utils.atomic_write import atomic_write_json, atomic_write_text


TERMINAL_CONTEXT_DIR = f"{MO_DESIGN_RUNTIME_DIR}/terminal-contexts"
TERMINAL_BOARD_DIR = f"{MO_DESIGN_RUNTIME_DIR}/terminal-boards"
_INSTANCE_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_DESIGN_RE = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")
_MAX_PROMPT_CHARS = 120_000
_MAX_AGE_SECONDS = 300.0
_BOARD_RESERVATION_SECONDS = 60.0
_BOARD_BINDING_LOCK = threading.RLock()


def terminal_board_binding(
    target: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the live Board renderer bound to one exact terminal."""
    instance_id, terminal_pid = _terminal_identity(target)
    path, lock_path = _terminal_board_paths(instance_id, config)
    with file_byte_lock(lock_path, _BOARD_BINDING_LOCK):
        row = _read_board_binding(path)
        if not _board_binding_live(row, instance_id=instance_id, terminal_pid=terminal_pid):
            _remove_board_binding(path)
            return None
        return _public_board_binding(row)


def reserve_terminal_board(
    design_id: str,
    target: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Reserve one standalone Board slot for one exact live terminal."""
    clean_design = str(design_id or "").strip()
    if not _DESIGN_RE.fullmatch(clean_design):
        raise ValueError("The MO Board design identity is invalid")
    instance_id, terminal_pid = _terminal_identity(target)
    if not _pid_alive(terminal_pid):
        raise RuntimeError("The originating MO terminal is no longer live")
    path, lock_path = _terminal_board_paths(instance_id, config)
    with file_byte_lock(lock_path, _BOARD_BINDING_LOCK):
        existing = _read_board_binding(path)
        if _board_binding_live(existing, instance_id=instance_id, terminal_pid=terminal_pid):
            if str(existing.get("design_id") or "") != clean_design:
                raise RuntimeError(
                    "This MO terminal already has a standalone Board. Close it before opening another."
                )
            status = "opening" if str(existing.get("state") or "") == "reserved" else "active"
            return {"status": status, **_public_board_binding(existing)}
        _remove_board_binding(path)
        token = secrets.token_hex(24)
        now = time.time()
        atomic_write_json(
            path,
            {
                "version": "board-link/v1",
                "state": "reserved",
                "instance_id": instance_id,
                "terminal_pid": terminal_pid,
                "design_id": clean_design,
                "renderer_pid": 0,
                "token": token,
                "created_at": now,
                "updated_at": now,
            },
            indent=None,
            ensure_ascii=False,
        )
        return {
            "status": "reserved",
            "instance_id": instance_id,
            "terminal_pid": terminal_pid,
            "design_id": clean_design,
            "renderer_pid": 0,
            "token": token,
        }


def activate_terminal_board(
    instance_id: str,
    design_id: str,
    token: str,
    *,
    renderer_pid: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Promote the launch reservation from inside the exact renderer process."""
    clean_instance = str(instance_id or "").strip()
    clean_design = str(design_id or "").strip()
    clean_token = str(token or "").strip()
    if not _INSTANCE_RE.fullmatch(clean_instance) or not _DESIGN_RE.fullmatch(clean_design):
        raise RuntimeError("The MO Board binding identity is invalid")
    pid = int(renderer_pid or os.getpid())
    if pid <= 0 or not _pid_alive(pid):
        raise RuntimeError("The MO Board renderer is unavailable")
    path, lock_path = _terminal_board_paths(clean_instance, config)
    with file_byte_lock(lock_path, _BOARD_BINDING_LOCK):
        row = _read_board_binding(path)
        if (
            not row
            or str(row.get("state") or "") != "reserved"
            or str(row.get("design_id") or "") != clean_design
            or not secrets.compare_digest(str(row.get("token") or ""), clean_token)
            or not 0 <= time.time() - _binding_float(row, "created_at") <= _BOARD_RESERVATION_SECONDS
        ):
            raise RuntimeError("The standalone MO Board reservation expired or was replaced")
        terminal_pid = _binding_int(row, "terminal_pid")
        if terminal_pid <= 0 or not _pid_alive(terminal_pid):
            _remove_board_binding(path)
            raise RuntimeError("The originating MO terminal is no longer live")
        row.update({"state": "active", "renderer_pid": pid, "updated_at": time.time()})
        atomic_write_json(path, row, indent=None, ensure_ascii=False)
        return _public_board_binding(row)


def release_terminal_board(
    instance_id: str,
    token: str,
    *,
    renderer_pid: int | None = None,
    config: dict[str, Any] | None = None,
) -> bool:
    """Release only the reservation or renderer that presents the same token."""
    clean_instance = str(instance_id or "").strip()
    clean_token = str(token or "").strip()
    if not _INSTANCE_RE.fullmatch(clean_instance) or not clean_token:
        return False
    path, lock_path = _terminal_board_paths(clean_instance, config)
    with file_byte_lock(lock_path, _BOARD_BINDING_LOCK):
        row = _read_board_binding(path)
        if not row or not secrets.compare_digest(str(row.get("token") or ""), clean_token):
            return False
        expected_renderer = int(renderer_pid or 0)
        recorded_renderer = _binding_int(row, "renderer_pid")
        if expected_renderer > 0 and recorded_renderer not in {0, expected_renderer}:
            return False
        _remove_board_binding(path)
        return True


def queue_terminal_turn(
    prompt: str,
    target: dict[str, Any],
    *,
    config: dict[str, Any] | None = None,
) -> str:
    """Queue one exact normal turn to one heartbeat-proven MO terminal."""
    return _queue_terminal_message(
        prompt,
        target,
        queue_dir_name=TERMINAL_CONTEXT_DIR,
        suffix=".context.txt",
        empty_message="MO terminal handoff is empty or too large",
        pending_message="That MO terminal already has a pending handoff",
        stale_message="The stale terminal handoff could not be replaced",
        config=config,
    )


def queue_terminal_control(kind: str, value: str, target: dict[str, Any], *,
                           project_root: str, expected_slot: str = "", config=None) -> str:
    """Queue a typed UI action, never reinterpret a normal Design message."""
    if kind not in {"command", "request", "stop", "steer", "terminal", "model", "mologrthim"}:
        raise ValueError("Unknown terminal control")
    payload = {"kind": kind, "value": value, "project": str(Path(project_root).resolve())}
    if expected_slot:
        payload["expected_slot"] = str(expected_slot)
    return _queue_terminal_message(
        json.dumps(payload),
        target, queue_dir_name=TERMINAL_CONTEXT_DIR, suffix=".context.txt",
        empty_message="Terminal control is empty or too large",
        pending_message="This terminal already has a pending handoff; retry after it is received",
        stale_message="The stale terminal handoff could not be replaced",
        config=config, control=True,
    )


def claim_terminal_request(
    *,
    instance_id: str,
    pid: int | None = None,
    config: dict[str, Any] | None = None,
) -> str | dict:
    """Claim normal-turn text or a distinctly typed control for this process."""
    return _claim_terminal_message(
        instance_id=instance_id,
        pid=pid,
        queue_dir_name=TERMINAL_CONTEXT_DIR,
        suffix=".context.txt",
        config=config,
    )


def _queue_terminal_message(
    prompt: str,
    target: dict[str, Any],
    *,
    queue_dir_name: str,
    suffix: str,
    empty_message: str,
    pending_message: str,
    stale_message: str,
    config: dict[str, Any] | None,
    control: bool = False,
) -> str:
    text = str(prompt or "").strip()
    if not text or len(text) > _MAX_PROMPT_CHARS:
        raise ValueError(empty_message)
    instance_id = str(target.get("instance_id") or "").strip()
    if not _INSTANCE_RE.fullmatch(instance_id):
        raise RuntimeError("The selected MO terminal identity is invalid")
    try:
        pid = int(target.get("pid") or 0)
    except (TypeError, ValueError, OverflowError):
        pid = 0
    if pid <= 0:
        raise RuntimeError("The selected MO terminal is no longer live")
    queue_dir = Path(resolve_state_path(queue_dir_name, config))
    queue_dir.mkdir(parents=True, exist_ok=True)
    queue_path = queue_dir / f"{instance_id}.json"
    if queue_path.is_file():
        try:
            existing = json.loads(queue_path.read_text(encoding="utf-8"))
            created_at = float(existing.get("created_at") or 0) if isinstance(existing, dict) else 0.0
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            created_at = 0.0
        if time.time() - created_at <= _MAX_AGE_SECONDS:
            raise RuntimeError(pending_message)
        try:
            queue_path.unlink(missing_ok=True)
        except OSError:
            raise RuntimeError(stale_message) from None
    prompt_id = secrets.token_hex(12)
    prompt_dir = Path(resolve_state_path(MO_DESIGN_HANDOFF_DIR, config))
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = prompt_dir / f"{prompt_id}{suffix}"
    atomic_write_text(prompt_path, text + "\n", encoding="utf-8")
    atomic_write_json(
        queue_path,
        {
            "prompt_id": prompt_id,
            "prompt_path": str(prompt_path),
            "instance_id": instance_id,
            "pid": pid,
            "created_at": time.time(),
            "control": control,
        },
        indent=None,
        ensure_ascii=False,
    )
    return prompt_id


def _claim_terminal_message(
    *,
    instance_id: str,
    pid: int | None,
    queue_dir_name: str,
    suffix: str,
    config: dict[str, Any] | None,
) -> str | dict:
    clean_instance = str(instance_id or "").strip()
    if not _INSTANCE_RE.fullmatch(clean_instance):
        return ""
    expected_pid = int(pid or os.getpid())
    queue_path = Path(resolve_state_path(queue_dir_name, config)) / f"{clean_instance}.json"
    if not queue_path.is_file():
        return ""
    claim_path = queue_path.with_name(f".{clean_instance}.{expected_pid}.{secrets.token_hex(4)}.claim")
    try:
        queue_path.replace(claim_path)
    except OSError:
        return ""
    prompt_path: Path | None = None
    restored = False
    try:
        row = json.loads(claim_path.read_text(encoding="utf-8"))
        if not isinstance(row, dict):
            return ""
        if str(row.get("instance_id") or "") != clean_instance or int(row.get("pid") or 0) != expected_pid:
            try:
                claim_path.replace(queue_path)
                restored = True
            except OSError:
                pass
            return ""
        if abs(time.time() - float(row.get("created_at") or 0)) > _MAX_AGE_SECONDS:
            return ""
        candidate = Path(str(row.get("prompt_path") or "")).expanduser().resolve(strict=False)
        root = Path(resolve_state_path(MO_DESIGN_HANDOFF_DIR, config)).resolve(strict=False)
        if candidate.parent != root or not candidate.name.endswith(suffix):
            return ""
        prompt_path = candidate
        text = prompt_path.read_text(encoding="utf-8", errors="replace").strip()
        if not 0 < len(text) <= _MAX_PROMPT_CHARS:
            return ""
        if row.get("control") is True:
            control = json.loads(text)
            if (isinstance(control, dict)
                    and control.get("kind") in {"command", "request", "stop", "steer", "terminal", "model", "mologrthim"}
                    and isinstance(control.get("value"), str)
                    and isinstance(control.get("project"), str)
                    and isinstance(control.get("expected_slot", ""), str)):
                return control
            return ""
        return text
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return ""
    finally:
        if not restored:
            try:
                claim_path.unlink(missing_ok=True)
            except OSError:
                pass
        if prompt_path is not None:
            try:
                prompt_path.unlink(missing_ok=True)
            except OSError:
                pass


def _terminal_identity(target: dict[str, Any]) -> tuple[str, int]:
    if not isinstance(target, dict):
        raise RuntimeError("The MO terminal binding is unavailable")
    instance_id = str(target.get("instance_id") or "").strip()
    try:
        terminal_pid = int(target.get("pid") or 0)
    except (TypeError, ValueError, OverflowError):
        terminal_pid = 0
    if not _INSTANCE_RE.fullmatch(instance_id) or terminal_pid <= 0:
        raise RuntimeError("The MO terminal binding is invalid")
    return instance_id, terminal_pid


def _terminal_board_paths(
    instance_id: str,
    config: dict[str, Any] | None,
) -> tuple[Path, Path]:
    directory = Path(resolve_state_path(TERMINAL_BOARD_DIR, config))
    return directory / f"{instance_id}.json", directory / ".bindings.lock"


def _read_board_binding(path: Path) -> dict[str, Any]:
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return row if isinstance(row, dict) and row.get("version") == "board-link/v1" else {}


def _board_binding_live(
    row: dict[str, Any],
    *,
    instance_id: str,
    terminal_pid: int,
) -> bool:
    recorded_terminal_pid = _binding_int(row, "terminal_pid")
    if (
        not row
        or str(row.get("instance_id") or "") != instance_id
        or recorded_terminal_pid != terminal_pid
        or not _DESIGN_RE.fullmatch(str(row.get("design_id") or ""))
        or len(str(row.get("token") or "")) < 32
        or not _pid_alive(terminal_pid)
    ):
        return False
    state = str(row.get("state") or "")
    if state == "reserved":
        age = time.time() - _binding_float(row, "created_at")
        return _binding_int(row, "renderer_pid") == 0 and 0 <= age <= _BOARD_RESERVATION_SECONDS
    if state != "active":
        return False
    renderer_pid = _binding_int(row, "renderer_pid")
    return renderer_pid > 0 and _pid_alive(renderer_pid)


def _public_board_binding(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "instance_id": str(row.get("instance_id") or ""),
        "terminal_pid": _binding_int(row, "terminal_pid"),
        "design_id": str(row.get("design_id") or ""),
        "renderer_pid": _binding_int(row, "renderer_pid"),
    }


def _binding_int(row: dict[str, Any], key: str) -> int:
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _binding_float(row: dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0.0)
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _remove_board_binding(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
