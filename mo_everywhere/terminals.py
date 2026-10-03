"""Supervise MO terminals the hub owns on its own machine.

A paired controller can already run turns, goals, and bounded workers through
this hub. What it could not do is start the one thing that is actually
watchable: a real MO terminal. Live Control relays a terminal only while one is
already running, so remote terminal work depended on someone opening an SSH
session by hand and leaving it open.

This module starts and stops exactly that, and nothing else. It launches MO's
own entrypoint under a detached pty with its own instance id, so the terminal
advertises the ordinary ``mo_session`` lane and appears beside any other host.
It is not a shell: the command is MO's entrypoint, the arguments are fixed, and
the caller never supplies a program, argument, or environment value.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.runtime.instance import ENV_MO_INSTANCE_ID, ENV_MO_TERMINAL_SESSION
from core.state.paths import (
    ENV_CODEX_AUTH_PATH,
    ENV_DEFAULT_ROOTS,
    ENV_MO_CONFIG,
    ENV_MO_HOME,
    ENV_MO_LOCAL_EXTENSION_ROOT,
    ENV_MO_PROJECT_CWD,
    ENV_MO_STATE_LOCAL,
    PROFILE_DB_PATH,
    codex_auth_path,
    local_extension_root,
    mo_home,
    private_state_enabled,
    project_cwd,
    resolve_state_path,
    runtime_config_path,
)

SESSION_PREFIX = "mo-term-"
MAX_HUB_TERMINALS = 6
_START_TIMEOUT_SECONDS = 20
_MULTIPLEXER = "tmux"
_PORTABLE_CONVERSATION_RE = re.compile(r"conv_[0-9a-f]{32}")
_HANDOFF_READY_TIMEOUT_SECONDS = 10


class TerminalError(RuntimeError):
    """The hub cannot start or stop a terminal as asked."""


@dataclass(frozen=True)
class HubTerminal:
    terminal_id: str
    instance_id: str
    session: str
    project_path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "terminal_id": self.terminal_id,
            "instance_id": self.instance_id,
            # The live-control host advertises this exact label, so a client can
            # pair a listed terminal with its lease without a second registry.
            "host_label": f"MO Terminal · {self.instance_id}",
            "project_path": self.project_path,
        }


def _multiplexer() -> str:
    path = shutil.which(_MULTIPLEXER)
    if not path:
        raise TerminalError(
            "this hub cannot host terminals because its machine has no tmux"
        )
    return path


def _entrypoint() -> Path:
    """Return MO's own entrypoint next to the running service."""
    root = Path(__file__).resolve().parents[1]
    entry = root / "mo.py"
    if not entry.is_file():
        raise TerminalError("MO entrypoint is missing beside this hub")
    return entry


def _session_name(terminal_id: str) -> str:
    return f"{SESSION_PREFIX}{terminal_id}"


def _terminal_session_environment(
    config: dict[str, Any],
    terminal_id: str,
    config_path: str,
    selected_project: Path,
) -> dict[str, str]:
    """Pin this hub's non-secret runtime routing into a new tmux session.

    A long-lived tmux server owns its own environment. Inheriting that ambient
    state can send a later MO terminal to an old profile, project, extension,
    or Codex auth file even though the controller authorized the correct hub.
    Resolve the serving process's canonical owners once and override only those
    routing values in the new session; provider credential contents never enter
    argv.
    """
    return {
        "HOME": str(Path.home()),
        "PATH": os.getenv("PATH", ""),
        ENV_MO_CONFIG: config_path,
        ENV_MO_HOME: str(mo_home(config)),
        ENV_MO_PROJECT_CWD: str(selected_project),
        ENV_MO_STATE_LOCAL: "0" if private_state_enabled(config) else "1",
        ENV_DEFAULT_ROOTS: os.getenv(ENV_DEFAULT_ROOTS, ""),
        ENV_CODEX_AUTH_PATH: str(
            Path(codex_auth_path()).expanduser().resolve(strict=False)
        ),
        ENV_MO_LOCAL_EXTENSION_ROOT: str(local_extension_root(config)),
        ENV_MO_INSTANCE_ID: terminal_id,
        "MO_INVOKED_AS": "mo",
        # A hub terminal owns a new slot and publishes it after startup.
        ENV_MO_TERMINAL_SESSION: "",
    }


def _run(args: list[str], *, timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


class HubTerminalSupervisor:
    """Start, list, and stop hub-owned MO terminals under one bounded cap."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        # FastAPI offloads start/stop to worker threads.  Keep the lifecycle
        # linearizable so a client whose POST times out can DELETE its known id
        # without that cleanup overtaking the still-running start operation.
        # The re-entrant lock also permits start's failed handoff path to call
        # stop for the same terminal.
        self._lifecycle_lock = threading.RLock()

    def available(self) -> bool:
        try:
            _multiplexer()
        except TerminalError:
            return False
        return _entrypoint().is_file()

    def projects(self) -> list[dict[str, str]]:
        """Expose this host's curated project locations, never client paths."""
        from core.profile import Profile

        profile_path = self.config.get("paths", {}).get("memory_file", PROFILE_DB_PATH)
        profile = Profile.load(resolve_state_path(profile_path, self.config))
        current = project_cwd().resolve()
        projects = {str(current): {"path": str(current), "name": current.name or str(current)}}
        for entry in profile.project_locations():
            if entry.path:
                projects[entry.path] = {"path": entry.path, "name": entry.name}
        return list(projects.values())

    def _project_directory(self, requested: str) -> Path:
        if not requested:
            return project_cwd().resolve()
        if not isinstance(requested, str) or len(requested) > 1024 or any(ord(char) < 32 for char in requested):
            raise TerminalError("terminal project is invalid")
        # The selected value must be advertised by this host. A local drive
        # path, a guessed matching name or an arbitrary path is not authority.
        paths = {item["path"] for item in self.projects()}
        if requested not in paths:
            raise TerminalError("terminal project is no longer available on this host")
        return Path(requested)

    def list(self) -> list[HubTerminal]:
        try:
            binary = _multiplexer()
        except TerminalError:
            return []
        proc = _run([binary, "list-sessions", "-F", "#{session_name}"])
        if proc.returncode != 0:
            # No server running means no sessions, which is not an error.
            return []
        terminals: list[HubTerminal] = []
        for line in (proc.stdout or "").splitlines():
            name = line.strip()
            if not name.startswith(SESSION_PREFIX):
                continue
            terminal_id = name[len(SESSION_PREFIX):]
            if terminal_id:
                terminals.append(
                    HubTerminal(
                        terminal_id=terminal_id,
                        instance_id=terminal_id,
                        session=name,
                        project_path=self._session_project(binary, name),
                    )
                )
        return terminals

    @staticmethod
    def _session_project(binary: str, session: str) -> str:
        result = _run([binary, "show-environment", "-t", session, ENV_MO_PROJECT_CWD])
        prefix = ENV_MO_PROJECT_CWD + "="
        value = str(result.stdout or "").strip()
        return value[len(prefix):] if result.returncode == 0 and value.startswith(prefix) else ""

    def start(
        self,
        *,
        terminal_id: str = "",
        portable_conversation_id: str = "",
        expected_revision: int = 0,
        project_path: str = "",
    ) -> HubTerminal:
        with self._lifecycle_lock:
            return self._start_locked(
                terminal_id=terminal_id,
                portable_conversation_id=portable_conversation_id,
                expected_revision=expected_revision,
                project_path=project_path,
            )

    def _start_locked(
        self,
        *,
        terminal_id: str = "",
        portable_conversation_id: str = "",
        expected_revision: int = 0,
        project_path: str = "",
    ) -> HubTerminal:
        binary = _multiplexer()
        entry = _entrypoint()
        selected_project = self._project_directory(project_path)
        running = self.list()
        requested_id = str(terminal_id or "").strip()
        if requested_id and (
            len(requested_id) > 64
            or not all(ch.isalnum() or ch in "-_" for ch in requested_id)
        ):
            raise TerminalError("terminal id is invalid")
        existing = None
        if requested_id:
            existing = next((item for item in running if item.terminal_id == requested_id), None)
            if existing is not None and not portable_conversation_id:
                if project_path and existing.project_path != str(selected_project):
                    raise TerminalError("the existing terminal belongs to a different project")
                return existing
        if len(running) >= MAX_HUB_TERMINALS:
            raise TerminalError(
                f"this hub already runs its maximum of {MAX_HUB_TERMINALS} terminals"
            )
        # The instance id doubles as the terminal id so the advertised host
        # label identifies the session without a second lookup table.
        terminal_id = requested_id or uuid.uuid4().hex[:8]
        conversation_id = str(portable_conversation_id or "").strip().lower()
        revision = expected_revision
        if conversation_id or revision:
            if not _PORTABLE_CONVERSATION_RE.fullmatch(conversation_id):
                raise TerminalError("portable conversation is invalid")
            if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
                raise TerminalError("portable conversation revision is invalid")
        session = _session_name(terminal_id)
        config_path = runtime_config_path(self.config)
        session_environment = _terminal_session_environment(
            self.config,
            terminal_id,
            config_path,
            selected_project,
        )
        env = dict(os.environ)
        env.update(session_environment)
        command = [sys.executable, str(entry)]
        if config_path:
            command += ["--config", config_path]
        if conversation_id:
            command += [
                "--portable-conversation", conversation_id,
                "--portable-revision", str(revision),
                "--handoff-ready-id", terminal_id,
            ]
        ready_path: Path | None = None
        if conversation_id:
            ready_path = self._handoff_ready_path(terminal_id)
        if existing is not None:
            if ready_path is not None and ready_path.is_file():
                return existing
            raise TerminalError("the existing terminal did not confirm the portable conversation")
        if ready_path is not None:
            try:
                ready_path.unlink(missing_ok=True)
            except OSError as exc:
                raise TerminalError("terminal handoff readiness is unavailable") from exc
        proc = subprocess.run(
            [
                binary, "new-session", "-d", "-s", session, "-c", str(selected_project),
                # Repeated -e values override the persistent tmux server's
                # environment for this session. They contain routing paths and
                # identity only, never provider or controller credential values.
                *[
                    part
                    for key, value in session_environment.items()
                    for part in ("-e", f"{key}={value}")
                ],
                "--", *command,
            ],
            capture_output=True,
            text=True,
            timeout=_START_TIMEOUT_SECONDS,
            env=env,
            cwd=str(entry.parent),
            check=False,
        )
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[:160]
            raise TerminalError(f"the hub could not start a terminal: {detail}")
        if ready_path is not None:
            deadline = time.monotonic() + _HANDOFF_READY_TIMEOUT_SECONDS
            while time.monotonic() < deadline and not ready_path.is_file():
                time.sleep(0.05)
            if not ready_path.is_file():
                self.stop(terminal_id)
                raise TerminalError("the terminal did not confirm the portable conversation")
        return HubTerminal(terminal_id=terminal_id, instance_id=terminal_id, session=session,
                           project_path=str(selected_project))

    def stop(self, terminal_id: str) -> bool:
        with self._lifecycle_lock:
            return self._stop_locked(terminal_id)

    def _stop_locked(self, terminal_id: str) -> bool:
        # Reject rather than sanitize: silently rewriting an id would turn a
        # malformed request into a kill aimed at some other session name.
        clean = str(terminal_id or "")
        if not clean or len(clean) > 64 or not all(ch.isalnum() or ch in "-_" for ch in clean):
            raise TerminalError("terminal id is invalid")
        binary = _multiplexer()
        proc = _run([binary, "kill-session", "-t", _session_name(clean)])
        if proc.returncode != 0:
            return False
        self._forget_session_slot(clean)
        try:
            self._handoff_ready_path(clean).unlink(missing_ok=True)
        except OSError:
            pass
        return True

    def _forget_session_slot(self, terminal_id: str) -> None:
        """Drop the stopped terminal's conversation snapshot.

        Each hub terminal owns a `main-<instance>` slot. Left behind, those
        accumulate one dead conversation per start and surface as stale history.
        """
        try:
            sessions = Path(resolve_state_path("memory/sessions", self.config))
            snapshot = sessions / "conversations" / f"main-{terminal_id}.json"
            if snapshot.is_file():
                snapshot.unlink()
        except OSError:
            pass

    def _handoff_ready_path(self, terminal_id: str) -> Path:
        root = Path(resolve_state_path("run/terminal-handoffs", self.config))
        root.mkdir(parents=True, exist_ok=True)
        return root / f"{terminal_id}.ready"
