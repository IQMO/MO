"""Authenticated local command spool and fixed visible-terminal route."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from core.runtime.subprocess_flags import (
    apply_windows_hidden_process_flags,
    console_python_executable,
    gui_python_executable,
)
from core.state.paths import (
    MO_DESIGN_HANDOFF_DIR,
    MO_DESIGN_RUNTIME_DIR,
    resolve_state_path,
    runtime_config_path,
)
from core.utils.atomic_write import atomic_write_json, atomic_write_text


COMMAND_DIR = f"{MO_DESIGN_RUNTIME_DIR}/commands"
FOCUS_DIR = f"{MO_DESIGN_RUNTIME_DIR}/focus"


def current_terminal_target(
    config: dict[str, Any] | None = None,
    *,
    project_root: str = "",
    require_session: bool = True,
    prefer_recent: bool = False,
) -> dict[str, Any] | None:
    """Resolve one heartbeat-proven terminal, optionally pinned to a project.

    Design keeps strict single/bound selection.  A caller explicitly requesting
    ``prefer_recent`` may select the newest same-project heartbeat so a resident
    companion can hand work over without opening a duplicate terminal.
    """
    from mo_desktop.everywhere import desktop_binding, terminal_session_candidates

    cfg = config or {}
    sessions = Path(resolve_state_path("memory/sessions", cfg))
    candidates = terminal_session_candidates(
        cfg,
        sessions,
        require_session=require_session,
    )
    requested = str(project_root or "").strip()
    if requested:
        expected = os.path.normcase(str(Path(requested).expanduser().resolve(strict=False)))
        candidates = [
            row for row in candidates
            if os.path.normcase(str(Path(str(row.get("cwd") or "")).expanduser().resolve(strict=False))) == expected
        ]
    binding = desktop_binding(cfg)
    bound_id = str((binding or {}).get("thread_id") or "")
    if bound_id:
        bound = next((row for row in candidates if str(row.get("thread_id") or "") == bound_id), None)
        if bound is not None:
            return bound
    if len(candidates) == 1:
        return candidates[0]
    return candidates[0] if prefer_recent and candidates else None


def exact_terminal_target(
    target: dict[str, Any] | None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Revalidate one privately recorded terminal identity without guessing."""
    if not isinstance(target, dict):
        return None
    instance_id = str(target.get("instance_id") or "").strip()
    try:
        pid = int(target.get("pid") or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    if not instance_id or pid <= 0:
        return None
    from mo_desktop.everywhere import terminal_session_candidates

    sessions = Path(resolve_state_path("memory/sessions", config or {}))
    return next((
        row for row in terminal_session_candidates(config or {}, sessions)
        if str(row.get("instance_id") or "") == instance_id and int(row.get("pid") or 0) == pid
    ), None)


def queue_command(payload: dict[str, Any], secret: str, *, config: dict[str, Any] | None = None, command_id: str = "") -> str:
    clean_secret = str(secret or "")
    if len(clean_secret) < 32:
        raise RuntimeError("MO Design is not connected to MO Desktop")
    command_id = command_id or secrets.token_hex(16)
    if len(command_id) != 32 or any(char not in "0123456789abcdef" for char in command_id):
        raise ValueError("Invalid Design command identity")
    body = dict(payload)
    body["command_id"] = command_id
    body["created_at"] = time.time()
    encoded = _encoded(body)
    row = {
        "payload": body,
        "signature": hmac.new(clean_secret.encode(), encoded, hashlib.sha256).hexdigest(),
    }
    directory = Path(resolve_state_path(COMMAND_DIR, config))
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_json(directory / f"{command_id}.json", row, indent=None, ensure_ascii=False)
    return command_id


def validate_command(row: Any, secret: str) -> dict[str, Any] | None:
    if not isinstance(row, dict) or not isinstance(row.get("payload"), dict):
        return None
    payload = dict(row["payload"])
    signature = str(row.get("signature") or "")
    expected = hmac.new(str(secret).encode(), _encoded(payload), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    created = float(payload.get("created_at") or 0)
    if abs(time.time() - created) > 300:
        return None
    return payload


def queue_focus_request(
    design_id: str, secret: str, *, renderer_pid: int, terminal_synced: bool = False, config: dict[str, Any] | None = None,
) -> None:
    """Address one authenticated renderer without redirecting other open windows."""
    clean_secret = str(secret or "")
    if len(clean_secret) < 32:
        raise RuntimeError("MO Design is not connected to MO Desktop")
    body = {"design_id": str(design_id or "").strip(), "terminal_synced": terminal_synced is True, "created_at": time.time()}
    encoded = _encoded(body)
    row = {"payload": body, "signature": hmac.new(clean_secret.encode(), encoded, hashlib.sha256).hexdigest()}
    directory = Path(resolve_state_path(FOCUS_DIR, config))
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        directory / f"{_focus_channel(clean_secret, renderer_pid)}.json",
        row,
        indent=None,
        ensure_ascii=False,
    )


def consume_focus_request(secret: str, *, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Consume the latest renderer-focus request for this in-memory Desktop connection."""
    clean_secret = str(secret or "")
    if len(clean_secret) < 32:
        return None
    path = Path(resolve_state_path(FOCUS_DIR, config)) / f"{_focus_channel(clean_secret, os.getpid())}.json"
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    payload = validate_command(row, clean_secret)
    if not payload or not str(payload.get("design_id") or "").strip():
        return None
    return {"design_id": str(payload["design_id"]).strip(), "terminal_synced": payload.get("terminal_synced") is True}


def launch_normal_terminal(*, project_root: str, config: dict[str, Any] | None = None) -> str:
    """Open the normal MO terminal in an explicitly selected local project."""
    from core.runtime.instance import ENV_MO_INSTANCE_ID, ENV_MO_TERMINAL_SESSION

    workspace = _handoff_workspace(project_root, require_project=True)
    entrypoint = Path(__file__).resolve().parents[2] / "mo.py"
    environment = dict(os.environ)
    environment[ENV_MO_INSTANCE_ID] = secrets.token_hex(4)
    environment.pop(ENV_MO_TERMINAL_SESSION, None)
    environment.pop("MO_SHELL_TERMINAL", None)
    environment.pop("MO_SHELL_HOST_HWND", None)
    environment["MO_PROJECT_CWD"] = str(workspace)
    command = [console_python_executable(), str(entrypoint)]
    config_path = runtime_config_path(config)
    if config_path:
        command.extend(["--config", config_path])
    _open_visible_terminal(command, cwd=workspace, environment=environment, close_on_exit=False)
    return environment[ENV_MO_INSTANCE_ID]


def launch_prompt_terminal(
    prompt: str,
    *,
    config: dict[str, Any] | None = None,
    project_root: str = "",
    fallback_workspace: str = "",
) -> dict[str, str]:
    """Open one interactive MO terminal and start the handoff as a visible goal."""
    text = str(prompt or "").strip()
    if not text or len(text) > 120_000:
        raise ValueError("MO terminal handoff prompt is empty or too large")
    directory = Path(resolve_state_path(MO_DESIGN_HANDOFF_DIR, config))
    directory.mkdir(parents=True, exist_ok=True)
    _prune_prompt_files(directory)
    prompt_id = secrets.token_hex(12)
    prompt_path = directory / f"{prompt_id}.goal.txt"
    atomic_write_text(prompt_path, text + "\n", encoding="utf-8")
    entrypoint = Path(__file__).resolve().parents[2] / "mo.py"
    if not entrypoint.is_file():
        raise RuntimeError("MO terminal entrypoint is unavailable")
    workspace = _handoff_workspace(
        project_root,
        fallback_workspace=fallback_workspace,
    )
    environment = dict(os.environ)
    environment["MO_PROJECT_CWD"] = str(workspace)
    environment.pop("MO_DESIGN_COMPLETION_ID", None)
    environment.pop("MO_DESIGN_COMPLETION_DIR", None)
    mo_command = [console_python_executable(), str(entrypoint), "--startup-goal-file", str(prompt_path)]
    config_path = runtime_config_path(config)
    if config_path:
        mo_command.extend(["--config", config_path])
    _open_visible_terminal(
        mo_command,
        cwd=workspace,
        environment=environment,
        close_on_exit=False,
    )
    return {"route": "terminal", "prompt_id": prompt_id}


def launch_background_goal(
    prompt: str,
    *,
    project_root: str,
    config: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Start one real GoalRunner process without opening a terminal window."""
    text = str(prompt or "").strip()
    if not text or len(text) > 120_000:
        raise ValueError("MO Design handoff prompt is empty or too large")
    entrypoint = Path(__file__).resolve().parents[2] / "mo.py"
    if not entrypoint.is_file():
        raise RuntimeError("MO background entrypoint is unavailable")
    workspace = _handoff_workspace(project_root, require_project=True)
    directory = Path(resolve_state_path(MO_DESIGN_HANDOFF_DIR, config))
    directory.mkdir(parents=True, exist_ok=True)
    _prune_prompt_files(directory)
    prompt_id = secrets.token_hex(12)
    prompt_path = directory / f"{prompt_id}.goal.txt"
    atomic_write_text(prompt_path, text + "\n", encoding="utf-8")
    environment = dict(os.environ)
    environment["MO_PROJECT_CWD"] = str(workspace)
    environment["MO_DESIGN_COMPLETION_ID"] = prompt_id
    environment["MO_DESIGN_COMPLETION_DIR"] = resolve_state_path(
        f"{MO_DESIGN_HANDOFF_DIR}/completions",
        config,
    )
    command = [gui_python_executable(), str(entrypoint), "--goal-prompt-file", str(prompt_path)]
    config_path = runtime_config_path(config)
    if config_path:
        command.extend(["--config", config_path])
    options: dict[str, Any] = {
        "cwd": str(workspace),
        "env": environment,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    apply_windows_hidden_process_flags(options, detached=True, below_normal_priority=True)
    if os.name != "nt":
        options["start_new_session"] = True
    subprocess.Popen(command, **options)
    return {"route": "background", "prompt_id": prompt_id}


def _open_visible_terminal(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    close_on_exit: bool = False,
) -> None:
    if os.name == "nt":
        terminal_command = [
            environment.get("COMSPEC") or "cmd.exe",
            "/c" if close_on_exit else "/k",
            *command,
        ]
        flags = int(getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        flags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        subprocess.Popen(terminal_command, cwd=str(cwd), env=environment, creationflags=flags)
        return
    if sys.platform == "darwin":
        shell_command = f"cd {shlex.quote(str(cwd))} && {shlex.join(command)}"
        if close_on_exit:
            shell_command += "; exit"
        apple_script = (
            'tell application "Terminal"\n'
            "activate\n"
            f"do script {json.dumps(shell_command)}\n"
            "end tell"
        )
        subprocess.Popen(["/usr/bin/osascript", "-e", apple_script], env=environment)
        return
    launchers = (
        ("x-terminal-emulator", "-e"),
        ("gnome-terminal", "--"),
        ("konsole", "-e"),
    )
    for executable, separator in launchers:
        path = shutil.which(executable)
        if path:
            subprocess.Popen([path, separator, *command], cwd=str(cwd), env=environment)
            return
    raise RuntimeError("No supported desktop terminal is available")


def _encoded(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _focus_channel(secret: str, renderer_pid: int) -> str:
    if int(renderer_pid) <= 0:
        raise ValueError("The MO Design renderer is unavailable")
    return hashlib.sha256(f"{secret}:{int(renderer_pid)}".encode()).hexdigest()[:24]


def _prune_prompt_files(directory: Path) -> None:
    cutoff = time.time() - 24 * 60 * 60
    paths = list(directory.glob("*.txt"))
    paths.extend((directory / "completions").glob("*.json"))
    for path in paths:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def project_folder_state(project_root: str) -> str:
    """Classify the explicit implementation target without scanning its contents."""
    selected = str(project_root or "").strip()
    if not selected:
        return "missing"
    target = Path(selected).expanduser().resolve(strict=False)
    if not target.is_dir():
        return "unavailable"
    try:
        return "existing" if next(target.iterdir(), None) is not None else "empty"
    except OSError:
        return "unavailable"


def _handoff_workspace(
    project_root: str,
    *,
    fallback_workspace: str = "",
    require_project: bool = False,
) -> Path:
    selected = str(project_root or "").strip()
    if selected:
        workspace = Path(selected).expanduser().resolve(strict=False)
        if workspace.is_dir():
            return workspace
        raise RuntimeError("The selected MO Design project folder is unavailable")
    if require_project:
        raise RuntimeError("Background implementation needs a selected project folder")
    fallback = str(fallback_workspace or "").strip()
    if fallback:
        workspace = Path(fallback).expanduser().resolve(strict=False)
        if workspace.is_dir():
            return workspace
    return Path.home().resolve(strict=False)
