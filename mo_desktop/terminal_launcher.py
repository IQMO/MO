"""Fixed MO-terminal actuator advertised by an authenticated Desktop host.

This is deliberately not a command runner.  The caller chooses neither a
program, argument, directory, environment value, nor window flag; it can ask
only for one fresh interactive MO terminal.  Live Control owns authentication
and exact-host routing before this adapter is reached.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any

from core.runtime.subprocess_flags import console_python_executable
from core.state.paths import runtime_config_path


HOST_ACTIONS_LANE = "host_actions_v1"
START_MO_TERMINAL = "start_mo_terminal"
START_PORTABLE_MO_TERMINAL = "start_portable_mo_terminal"
STOP_MO_TERMINAL = "stop_mo_terminal"
_IDEMPOTENCY_TTL_SECONDS = 10 * 60
_MAX_IDEMPOTENCY_ROWS = 32


class DesktopTerminalLauncher:
    """Launch only MO's canonical terminal entrypoint in a fresh console."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        popen: Any = subprocess.Popen,
        run: Any = subprocess.run,
        clock: Any = time.time,
    ) -> None:
        self.config = config or {}
        self._popen = popen
        self._run = run
        self._clock = clock
        self._completed: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._failed: OrderedDict[str, tuple[float, str]] = OrderedDict()
        # Only processes launched by this Desktop actuator are stoppable. The
        # exact instance id is the capability boundary; arbitrary PIDs and
        # commands never cross the Live Control protocol.
        self._processes: dict[str, tuple[Any, str]] = {}

    @property
    def enabled(self) -> bool:
        return sys.platform == "win32" and self._entrypoint().is_file()

    def execute(
        self,
        operation: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        stopping = operation == STOP_MO_TERMINAL
        portable = operation == START_PORTABLE_MO_TERMINAL
        if operation not in {
            START_MO_TERMINAL,
            START_PORTABLE_MO_TERMINAL,
            STOP_MO_TERMINAL,
        }:
            raise ValueError("unsupported Desktop host action")
        conversation_id = ""
        revision = 0
        stop_instance_id = ""
        if stopping:
            stop_instance_id = _stop_arguments(arguments)
        elif portable:
            conversation_id, revision = _portable_arguments(arguments)
        elif arguments:
            raise ValueError("unsupported Desktop host action")
        if not _request_id(idempotency_key):
            raise ValueError("Desktop host action id is invalid")
        if not self.enabled:
            raise RuntimeError("Desktop terminal launch is unavailable")
        self._prune()
        cached = self._completed.get(idempotency_key)
        if cached is not None:
            self._completed.move_to_end(idempotency_key)
            return dict(cached[1])
        failed = self._failed.get(idempotency_key)
        if failed is not None:
            self._failed.move_to_end(idempotency_key)
            raise RuntimeError(failed[1])

        if stopping:
            try:
                result = self._stop(stop_instance_id)
            except Exception as exc:
                message = str(exc) or "MO Desktop could not stop the requested terminal"
                self._failed[idempotency_key] = (self._clock(), message)
                self._failed.move_to_end(idempotency_key)
                raise RuntimeError(message) from None
            self._remember(idempotency_key, result)
            return dict(result)

        instance_id = uuid.uuid4().hex[:8]
        entrypoint = self._entrypoint()
        environment = dict(os.environ)
        environment["MO_INSTANCE_ID"] = instance_id
        environment.pop("MO_TERMINAL_SESSION", None)
        environment.setdefault("MO_PROJECT_CWD", str(entrypoint.parent))
        command = [
            environment.get("COMSPEC") or "cmd.exe",
            "/k",
            console_python_executable(),
            str(entrypoint),
        ]
        config_path = runtime_config_path(self.config)
        if config_path:
            command.extend(["--config", config_path])
        ready_path = None
        if portable:
            from core.state.paths import resolve_state_path

            ready_path = Path(resolve_state_path(
                f"run/terminal-handoffs/{instance_id}.ready",
                self.config,
            ))
            ready_path.parent.mkdir(parents=True, exist_ok=True)
            ready_path.unlink(missing_ok=True)
            command.extend([
                "--portable-conversation",
                conversation_id,
                "--portable-revision",
                str(revision),
                "--handoff-ready-id",
                instance_id,
            ])
        flags = int(getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
        flags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        try:
            process = self._popen(
                command,
                cwd=str(entrypoint.parent),
                env=environment,
                creationflags=flags,
            )
            if portable and not self._wait_for_ready(ready_path, process):
                raise RuntimeError("MO Desktop terminal could not validate the conversation")
        except Exception as exc:
            message = (
                str(exc)
                if isinstance(exc, RuntimeError) and str(exc)
                else "MO Desktop could not start the requested terminal"
            )
            self._failed[idempotency_key] = (self._clock(), message)
            self._failed.move_to_end(idempotency_key)
            if ready_path is not None:
                ready_path.unlink(missing_ok=True)
            raise RuntimeError(message) from None
        if ready_path is not None:
            ready_path.unlink(missing_ok=True)
        result = {
            "instance_id": instance_id,
            "host_label": f"MO Terminal · {instance_id}",
        }
        self._processes[instance_id] = (process, result["host_label"])
        self._remember(idempotency_key, result)
        return dict(result)

    def _stop(self, instance_id: str) -> dict[str, Any]:
        owned = self._processes.get(instance_id)
        if owned is None:
            raise RuntimeError("That terminal is not owned by this MO Desktop instance")
        process, host_label = owned
        poll = getattr(process, "poll", None)
        if callable(poll) and poll() is not None:
            self._processes.pop(instance_id, None)
            return {"instance_id": instance_id, "host_label": host_label}
        pid = getattr(process, "pid", None)
        if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
            raise RuntimeError("MO Desktop could not stop the requested terminal")
        options: dict[str, Any] = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "timeout": 5,
        }
        from core.runtime.subprocess_flags import apply_windows_hidden_process_flags

        apply_windows_hidden_process_flags(options)
        completed = self._run(
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            **options,
        )
        if int(getattr(completed, "returncode", 1)) != 0:
            raise RuntimeError("MO Desktop could not stop the requested terminal")
        self._processes.pop(instance_id, None)
        return {"instance_id": instance_id, "host_label": host_label}

    def _remember(self, idempotency_key: str, result: dict[str, Any]) -> None:
        self._completed[idempotency_key] = (self._clock(), result)
        self._completed.move_to_end(idempotency_key)
        while len(self._completed) > _MAX_IDEMPOTENCY_ROWS:
            self._completed.popitem(last=False)

    @staticmethod
    def error_message(_error: Exception) -> str:
        return "MO Desktop could not complete the terminal action"

    @staticmethod
    def _entrypoint() -> Path:
        return Path(__file__).resolve().parents[1] / "mo.py"

    def _prune(self) -> None:
        for instance_id, (process, _label) in list(self._processes.items()):
            poll = getattr(process, "poll", None)
            if callable(poll) and poll() is not None:
                self._processes.pop(instance_id, None)
        cutoff = self._clock() - _IDEMPOTENCY_TTL_SECONDS
        for key, (created_at, _result) in list(self._completed.items()):
            if created_at < cutoff:
                self._completed.pop(key, None)
        for key, (created_at, _message) in list(self._failed.items()):
            if created_at < cutoff:
                self._failed.pop(key, None)
        while len(self._failed) > _MAX_IDEMPOTENCY_ROWS:
            self._failed.popitem(last=False)

    @staticmethod
    def _wait_for_ready(ready_path: Path | None, process: Any) -> bool:
        if ready_path is None:
            return False
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if ready_path.is_file():
                return True
            poll = getattr(process, "poll", None)
            if callable(poll) and poll() is not None:
                return False
            time.sleep(0.05)
        return False


def _request_id(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 32 and all(char in "0123456789abcdef" for char in text)


def _portable_arguments(arguments: Any) -> tuple[str, int]:
    if not isinstance(arguments, dict) or set(arguments) != {
        "conversation_id",
        "expected_revision",
    }:
        raise ValueError("unsupported Desktop host action")
    conversation_id = str(arguments.get("conversation_id") or "").strip().lower()
    revision = arguments.get("expected_revision")
    if (
        len(conversation_id) != 37
        or not conversation_id.startswith("conv_")
        or any(char not in "0123456789abcdef" for char in conversation_id[5:])
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
    ):
        raise ValueError("unsupported Desktop host action")
    return conversation_id, revision


def _stop_arguments(arguments: Any) -> str:
    if not isinstance(arguments, dict) or set(arguments) != {"instance_id"}:
        raise ValueError("unsupported Desktop host action")
    instance_id = str(arguments.get("instance_id") or "")
    if (
        not instance_id
        or len(instance_id) > 64
        or any(not (char.isalnum() or char in "-_") for char in instance_id)
    ):
        raise ValueError("unsupported Desktop host action")
    return instance_id
