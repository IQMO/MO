"""MO-host workspace pane over the existing Everywhere terminal protocol."""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, replace
from typing import Any, Callable

from mo_everywhere.client import ContinuityClient, files_client_config
from mo_everywhere.registry import RegistryError
from mo_everywhere.live_control import MAX_WIRE_TEXT_BYTES, _host_message, _resource_sample
from mo_everywhere.live_host import (
    _is_auth_rejection,
    _websocket_url,
    connect_websocket,
)

from .tui_app import LOGO_LINES
from .workspace_transport import WorkspaceChangeNotifier

_DEFAULT_COLUMNS = 80
_DEFAULT_ROWS = 24


class _RemoteClosed(RuntimeError):
    pass


class _RemoteSessionClosed(ConnectionError):
    pass


def _wire_text(value: Any, name: str, limit: int = 80) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit or any(ord(char) < 0x20 for char in text):
        raise ValueError(f"MO host {name} is invalid")
    return text


@dataclass(frozen=True)
class RunningMoTerminal:
    """One currently advertised MO terminal that the workspace may attach."""

    instance_id: str
    label: str
    host_id: str = ""
    machine_key: str = ""
    platform_family: str = ""
    app_version: str = ""
    observed_at: float = 0.0
    resources_supported: bool = False
    resources: dict[str, Any] | None = None
    connected: bool = True
    project_path: str = ""


@dataclass(frozen=True)
class WorkspaceDiscovery:
    terminals: tuple[RunningMoTerminal, ...]
    projects: tuple[dict[str, str], ...] = ()
    project_error: str = ""


def discover_workspace(
    config: dict[str, Any] | None,
    *,
    client_factory: Callable[..., Any] = ContinuityClient,
    include_resources: bool = False,
) -> WorkspaceDiscovery:
    """Read live terminals and the serving host's existing project navigation."""
    client = client_factory(files_client_config(config or {}), timeout=5.0)
    status = (client.live_control_status(include_resources=True)
              if include_resources else client.live_control_status())
    observed_at = time.monotonic()
    if status.get("enabled") is not True:
        raise RuntimeError("MO Live Control is unavailable")
    hosts = status.get("hosts")
    if not isinstance(hosts, list):
        raise ValueError("MO host status is invalid")
    terminals: dict[str, RunningMoTerminal] = {}
    for host in hosts:
        if not isinstance(host, dict):
            continue
        lanes = host.get("lanes")
        if not isinstance(lanes, list) or "mo_session" not in lanes:
            continue
        try:
            instance_id = _wire_text(host.get("instance_id"), "instance id", 64)
            label = _wire_text(
                host.get("label") or f"MO Terminal · {instance_id}",
                "label",
            )
        except ValueError:
            continue
        resources = None
        if include_resources and host.get("resources") is not None:
            try:
                resources = _resource_sample(host["resources"])
            except (TypeError, ValueError, RegistryError):
                # Keep terminal discovery usable when just its observation is invalid.
                resources = None
        terminals.setdefault(
            instance_id,
            RunningMoTerminal(
                instance_id=instance_id, label=label,
                host_id=str(host.get("host_id") or "")[:80],
                machine_key=str(host.get("machine_key") or "")[:80],
                platform_family=str(host.get("platform_family") or "")[:20],
                app_version=" ".join(str(host.get("app_version") or "").split())[:80],
                observed_at=observed_at,
                resources_supported=host.get("resources_supported") is True,
                resources=resources,
            ),
        )
    projects = []
    project_error = ""
    try:
        catalog = client.hub_terminals()
        for project in catalog.get("projects", []):
            projects.append({"path": _wire_text(project.get("path"), "project path", 1024),
                             "name": _wire_text(project.get("name"), "project name", 160)})
        if catalog.get("supported") is True and "projects" not in catalog:
            project_error = "Host project selection needs a host update."
        project_paths = {project["path"] for project in projects}
        for terminal in catalog.get("terminals", []):
            instance_id = terminal.get("instance_id")
            path = terminal.get("project_path")
            if instance_id in terminals and path in project_paths:
                terminals[instance_id] = replace(terminals[instance_id], project_path=path)
    except Exception as exc:
        project_error = f"Host projects unavailable: {type(exc).__name__}."
    return WorkspaceDiscovery(tuple(terminals[key] for key in sorted(terminals)), tuple(projects), project_error)


class RemoteMoTerminalProcess(WorkspaceChangeNotifier):
    """Own one launched MO terminal, its Live Control lease, and text stream."""

    def __init__(
        self,
        *,
        pane_id: str,
        config: dict[str, Any] | None,
        on_change: Callable[[], None] | None = None,
        columns: int = _DEFAULT_COLUMNS,
        rows: int = _DEFAULT_ROWS,
        attached_instance_id: str = "",
        attached_label: str = "",
        project_path: str = "",
        client_factory: Callable[..., Any] = ContinuityClient,
        connector: Callable[[str, str], Any] = connect_websocket,
        startup_timeout: float = 20.0,
        poll_interval: float = 0.25,
        keepalive_interval: float = 10.0,
    ) -> None:
        self.pane_id = str(pane_id)
        self.config = config or {}
        self.project_path = _wire_text(project_path, "project path", 1024) if project_path else ""
        self.on_change = on_change
        self.columns = max(20, int(columns or _DEFAULT_COLUMNS))
        self.rows = max(3, int(rows or _DEFAULT_ROWS))
        attached_id = (
            _wire_text(attached_instance_id, "instance id", 64)
            if attached_instance_id
            else ""
        )
        self.cwd_label = (
            _wire_text(attached_label, "label") if attached_label else "MO host"
        )
        self.state = "starting"
        self.error = ""
        self.exit_code: int | None = None
        self._client_factory = client_factory
        self._connector = connector
        self._startup_timeout = max(0.01, float(startup_timeout))
        self._poll_interval = max(0.005, float(poll_interval))
        self._keepalive_interval = max(0.01, float(keepalive_interval))
        self._client: Any = None
        self._socket: Any = None
        self._attached_instance_id = attached_id
        self._terminal_id = attached_id
        self._session_id = ""
        self._lines: tuple[str, ...] = ()
        self._notice_text = ""
        self._host_sequence = 0
        self._client_sequence = 0
        self._stream_snapshot_received = False
        self.command_menu_available = False
        self._pending_command: tuple[str, Callable[[dict[str, Any]], None]] | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._closed = False

    @property
    def alive(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive() and not self._closed)

    @property
    def instance_id(self) -> str:
        """Return the stable terminal identity represented by this pane."""
        with self._lock:
            return self._terminal_id

    @property
    def attached(self) -> bool:
        return bool(self._attached_instance_id)

    @property
    def owns_terminal(self) -> bool:
        """False only when closing this pane must detach without stopping MO."""
        return not self.attached

    def start(self) -> None:
        """Start the existing hub-terminal flow without blocking the TUI."""
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run,
                name=f"mo-workspace-host-{self.pane_id}",
                daemon=True,
            )
            self._thread.start()
        self._notify()

    def _run(self) -> None:
        try:
            # The workstation's existing controller identity starts and leases
            # the serving hub's terminal. The notify coordinator and remote
            # host identities intentionally cannot launch or lease it.
            client = self._client_factory(files_client_config(self.config), timeout=5.0)
            self._client = client
            instance_id = self._attached_instance_id or self._start_owned_terminal(client)
            consecutive_early_closes = 0
            while not self._stop.is_set():
                # host_id is live socket authority, not reconnect identity. A
                # host transport reconnect gets a fresh value, so resolve the
                # stable terminal instance before every lease instead of
                # replaying the stale authority captured at first startup.
                host_id = self._wait_for_host(client, instance_id)
                descriptor = client.prepare_live_control_session(host_id, "mo_session")
                session_id = _wire_text(descriptor.get("session_id"), "session id", 32)
                if len(session_id) != 32:
                    raise ValueError("MO host session id is invalid")
                expected_path = f"/api/mo/live/client/{session_id}"
                if descriptor.get("websocket_path") != expected_path:
                    raise ValueError("MO host WebSocket path is invalid")
                socket = self._connect(client, expected_path)
                with self._lock:
                    self._session_id = session_id
                    self._socket = socket
                    self._client_sequence = 0
                    self._host_sequence = 0
                    self._stream_snapshot_received = False
                    self.command_menu_available = descriptor.get("command_menu") is True
                connected_at = time.monotonic()
                try:
                    self._read_stream(socket)
                except _RemoteSessionClosed:
                    with self._lock:
                        if self._socket is socket:
                            self._socket = None
                    try:
                        socket.close()
                    except Exception:
                        pass
                    raise
                except ConnectionError:
                    self._end_command_request("Host disconnected before the command result arrived. Its outcome is unknown; it was not retried.")
                    with self._lock:
                        received_snapshot = self._stream_snapshot_received
                        retry = not self._closed
                        if self._socket is socket:
                            self._socket = None
                            self._session_id = ""
                        if retry:
                            self.state = "starting"
                    try:
                        socket.close()
                    except Exception:
                        pass
                    stable_stream = (
                        received_snapshot
                        and time.monotonic() - connected_at >= 1.0
                    )
                    consecutive_early_closes = (
                        0 if stable_stream else consecutive_early_closes + 1
                    )
                    if retry and consecutive_early_closes < 2:
                        self._notify()
                    if (
                        retry
                        and consecutive_early_closes < 2
                        and not self._stop.wait(self._poll_interval)
                    ):
                        continue
                    raise
        except _RemoteClosed:
            pass
        except Exception as exc:
            self._fail(exc)
        finally:
            cleanup_error = self._stop_owned_terminal()
            with self._lock:
                if cleanup_error is not None:
                    self.state = "error"
                    message = " ".join(str(cleanup_error).split())
                    self.error = f"{type(cleanup_error).__name__}: {message}"[:300]
                elif self._closed:
                    self.state = "exited"
                elif self.state != "error":
                    self.state = "error"
                    self.error = "MO host terminal closed unexpectedly"
            self._notify()

    def _start_owned_terminal(self, client: Any) -> str:
        requested_id = uuid.uuid4().hex
        with self._lock:
            self._terminal_id = requested_id
        created = (client.start_hub_terminal(requested_id, project_path=self.project_path)
                   if self.project_path else client.start_hub_terminal(requested_id))
        terminal_id = _wire_text(created.get("terminal_id"), "terminal id", 64)
        # Record the server-reported terminal identity before any later field
        # validation can reject the response, so teardown stops the terminal
        # that was actually created instead of orphaning it.
        with self._lock:
            self._terminal_id = terminal_id
        instance_id = _wire_text(created.get("instance_id"), "instance id", 80)
        if terminal_id != requested_id or instance_id != requested_id:
            raise ValueError("MO host returned a mismatched terminal identity")
        if self.project_path and created.get("project_path") != self.project_path:
            raise ValueError("MO host returned a mismatched terminal project")
        self.cwd_label = _wire_text(created.get("host_label") or "MO host", "label")
        return instance_id

    def _wait_for_host(self, client: Any, instance_id: str) -> str:
        deadline = time.monotonic() + self._startup_timeout
        while True:
            if self._stop.is_set():
                raise _RemoteClosed()
            status = client.live_control_status()
            if status.get("enabled") is not True:
                raise RuntimeError("MO Live Control is unavailable")
            hosts = status.get("hosts")
            if not isinstance(hosts, list):
                raise ValueError("MO host status is invalid")
            for host in hosts:
                if not isinstance(host, dict) or host.get("instance_id") != instance_id:
                    continue
                lanes = host.get("lanes")
                if isinstance(lanes, list) and "mo_session" in lanes:
                    return _wire_text(host.get("host_id"), "host id", 80)
            if time.monotonic() >= deadline:
                raise TimeoutError("started MO terminal did not advertise Live Control")
            self._stop.wait(self._poll_interval)

    def _connect(self, client: Any, path: str) -> Any:
        hub, authority = client.websocket_authority()
        try:
            return self._connector(_websocket_url(hub, path), authority)
        except Exception as exc:
            if not _is_auth_rejection(exc):
                raise
        hub, authority = client.refresh_websocket_authority()
        return self._connector(_websocket_url(hub, path), authority)

    def _read_stream(self, socket: Any) -> None:
        last_ping = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_ping >= self._keepalive_interval:
                if not self._send({"type": "ping"}):
                    raise ConnectionError("MO host keepalive failed")
                last_ping = now
            try:
                value = socket.recv()
            except TimeoutError:
                continue
            except Exception as exc:
                if type(exc).__name__ in {"WebSocketTimeoutException", "TimeoutError"}:
                    continue
                if self._stop.is_set():
                    raise _RemoteClosed() from exc
                raise ConnectionError("MO host stream closed") from exc
            if value in {None, ""}:
                if self._stop.is_set():
                    raise _RemoteClosed()
                raise ConnectionError("MO host stream closed")
            if not isinstance(value, str):
                raise ValueError("MO host protocol received binary data")
            self._receive(value)
        raise _RemoteClosed()

    def _receive(self, raw: str) -> None:
        if len(raw.encode("utf-8")) > MAX_WIRE_TEXT_BYTES:
            raise ValueError("MO host protocol message is outside bounds")
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            raise ValueError("MO host protocol message is invalid") from None
        if not isinstance(payload, dict):
            raise ValueError("MO host protocol message is invalid")
        kind = payload.get("type")
        # The broker confirms the client attachment before the terminal host
        # emits its sequenced lane-ready message. This exact two-field event is
        # transport state, not host output, so accept it without advancing the
        # host sequence or claiming the terminal is ready for commands.
        if kind == "session_ready" and "sequence" not in payload:
            if set(payload) != {"type", "lane"} or payload.get("lane") != "mo_session":
                raise ValueError("MO host protocol message is invalid")
            return
        if kind == "pong":
            if set(payload) != {"type"}:
                raise ValueError("MO host protocol message is invalid")
            return
        if kind not in {"session_ready", "terminal_snapshot", "terminal_update", "notice", "session_closed", "terminal_command_result"}:
            raise ValueError("MO host protocol message type is invalid")
        # The hub forwards the validated host payload unchanged, including its
        # session identifier. Bind every sequenced message to the exact lease
        # owned by this pane; accepting an omitted or mismatched id would make
        # the client-side validation weaker than the canonical wire contract.
        if payload.get("session_id") != self._session_id:
            raise ValueError("MO host protocol session is invalid")
        try:
            message = _host_message(raw)
        except Exception:
            raise ValueError("MO host protocol message is invalid") from None
        sequence = int(message["sequence"])
        command_callback = None
        with self._lock:
            if sequence <= self._host_sequence:
                raise ValueError("MO host protocol sequence is stale")
            self._host_sequence = sequence
            if kind == "session_ready":
                self.state = "running"
            elif kind in {"terminal_snapshot", "terminal_update"}:
                self._lines = tuple(message["lines"])
                self._notice_text = ""
                self._stream_snapshot_received = True
                self.state = "working" if message["busy"] else (
                    "blocked" if message.get("attention") is True else "idle"
                )
            elif kind == "notice":
                self._notice_text = str(message["message"])
            elif kind == "terminal_command_result":
                pending = self._pending_command
                if pending is not None and pending[0] == message["request_id"]:
                    command_callback = pending[1]
                    self._pending_command = None
            elif kind == "session_closed":
                reason = " ".join(str(message.get("reason") or "closed").split())[:80]
                raise _RemoteSessionClosed(reason)
        if command_callback is not None:
            command_callback(message)
        self._notify()

    def request_command(self, value: str, action: str, callback: Callable[[dict[str, Any]], None]) -> bool:
        """Send one explicit menu interaction to this pane's exact live lease."""
        if not self.command_menu_available:
            return False
        request_id = uuid.uuid4().hex
        with self._lock:
            self._pending_command = (request_id, callback)
        if self._send({"type": "terminal_command", "request_id": request_id, "action": action, "value": value}):
            return True
        with self._lock:
            self._pending_command = None
        return False

    def _end_command_request(self, message: str) -> None:
        with self._lock:
            pending = self._pending_command
            self._pending_command = None
            self.command_menu_available = False
        if pending is not None:
            pending[1]({
                "request_id": pending[0], "mode": "error", "title": "commands",
                "text": message, "items": [],
            })

    def _send(self, body: dict[str, Any]) -> bool:
        with self._send_lock:
            socket = self._socket
            if socket is None or self._closed:
                return False
            self._client_sequence += 1
            payload = dict(body, sequence=self._client_sequence)
            try:
                socket.send(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
                return True
            except Exception:
                return False

    def send_line(self, text: str) -> bool:
        value = str(text or "")
        if not 1 <= len(value) <= 12_000 or not value.strip():
            return False
        if any(ord(char) < 0x20 and char not in "\t\r\n" for char in value) or "\x7f" in value:
            return False
        return self._send({"type": "text", "value": value})

    def send_input(self, text: str) -> bool:
        """Forward one interactive key payload without submitting semantic text."""
        value = str(text or "")
        key = {
            "\r": "enter", "\n": "enter", "\t": "tab", "\x1b": "escape",
            "\x7f": "backspace", "\x1b[A": "up", "\x1b[B": "down",
            "\x1b[C": "right", "\x1b[D": "left", "\x1b[H": "home",
            "\x1b[F": "end", "\x1b[3~": "delete", "\x1b[5~": "pageup",
            "\x1b[6~": "pagedown", "\x1b[Z": "tab",
        }.get(value)
        if key:
            return self._send({"type": "key", "key": key, "action": "press"})
        if len(value) == 1 and 1 <= ord(value) <= 26:
            return self._send({
                "type": "key",
                "key": chr(ord(value) + 96),
                "action": "press",
                "modifiers": ["ctrl"],
            })
        if value == " ":
            return self._send({"type": "key", "key": "space", "action": "press"})
        if len(value) == 1 and 0x21 <= ord(value) <= 0x7E:
            modifiers = ["shift"] if value.isupper() else []
            payload: dict[str, Any] = {
                "type": "key",
                "key": value.lower() if modifiers else value,
                "action": "press",
            }
            if modifiers:
                payload["modifiers"] = modifiers
            return self._send(payload)
        return False

    def send_interrupt(self) -> bool:
        return self._send({"type": "key", "key": "escape", "action": "press"})

    def resize(self, *, columns: int, rows: int) -> None:
        self.columns = max(20, int(columns or _DEFAULT_COLUMNS))
        self.rows = max(3, int(rows or _DEFAULT_ROWS))

    def screen_lines(self) -> tuple[str, ...]:
        with self._lock:
            lines = self._lines
            # A host snapshot is logical transcript text, not an independent
            # terminal viewport. The workspace title already owns identity, so
            # do not repeat the canonical four-row landing logo inside a small
            # tile. Match the canonical logo rather than maintaining a copy.
            if len(lines) >= len(LOGO_LINES):
                if all(lines[index].startswith(logo) for index, logo in enumerate(LOGO_LINES)):
                    lines = lines[len(LOGO_LINES):]
            if self._notice_text:
                lines += (f"MO host: {self._notice_text}",)
            # MO-host snapshots are bounded transcript history, not a VT screen.
            # Keep all supplied rows so the owning workspace tile can scroll them.
            return lines

    def screen_fragments(self) -> tuple[tuple[tuple[str, str], ...], ...]:
        return tuple((("", line),) for line in self.screen_lines())

    def close(self, *, timeout: float = 0.75) -> None:
        with self._lock:
            if self._closed:
                return
            self.state = "closing"
        self._end_command_request("Host command connection closed")
        self._notify()
        self._send({"type": "close"})
        with self._lock:
            self._closed = True
        self._stop.set()
        socket = self._socket
        if socket is not None:
            try:
                socket.close()
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))
        with self._lock:
            # The worker owns the start request and exact terminal cleanup. If
            # that request is still in flight, remain visibly closing rather
            # than claiming teardown completed or racing a not-found DELETE.
            if thread is None or not thread.is_alive():
                if self.state != "error":
                    self.state = "exited"
        self._notify()

    def _stop_owned_terminal(self) -> Exception | None:
        with self._cleanup_lock:
            with self._lock:
                terminal_id = self._terminal_id
                client = self._client
            if not terminal_id or client is None:
                return None
            if self.attached:
                # This pane owns only its lease. The original terminal process
                # remains with the window or hub that started it.
                return None
            try:
                # A false result with missing_ok is positive evidence that the
                # exact request-owned id is already absent.
                client.stop_hub_terminal(terminal_id, missing_ok=True)
            except Exception as exc:
                return exc
            with self._lock:
                if self._terminal_id == terminal_id:
                    self._terminal_id = ""
            return None

    def _fail(self, exc: Exception) -> None:
        self._end_command_request("Host command connection closed before a result arrived. The command was not retried.")
        message = " ".join(str(exc).split())
        with self._lock:
            self.state = "error"
            self.error = (message if isinstance(exc, ConnectionError) else f"{type(exc).__name__}: {message}")[:300]
        self._notify()
