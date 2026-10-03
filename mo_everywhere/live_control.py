"""Native, memory-only MO Live Control broker.

The hub authenticates and relays. It never captures a screen, injects input,
starts an agent, or persists frame/input payloads. Hosts connect outbound with
the exact ``remote_host`` scope; one paired controller with ``control`` plus the
exact ``remote_control`` scope may hold one short session lease per host.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.files.service import (
    FILE_LOCATION_KINDS,
    valid_file_capability_descriptor,
)
from core.state.paths import resolve_state_path
from core.utils.atomic_write import atomic_write_text

from .registry import DevicePrincipal, DeviceRegistry, RegistryError


KILL_SWITCH_PATH = "run/everywhere-remote.disabled"


def set_live_control_locally_disabled(
    config: dict[str, Any] | None = None,
    *,
    disabled: bool,
    reason: str = "local kill switch",
) -> None:
    """Toggle the device-local Live Control kill switch."""
    path = Path(resolve_state_path(KILL_SWITCH_PATH, config or {}))
    if disabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, str(reason or "local kill switch")[:120] + "\n", encoding="utf-8")
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


class SessionFault(RegistryError):
    """A fault scoped to one session or one message, not to the connection.

    A host multiplexes every one of its sessions over a single socket, so
    tearing that socket down for a per-session fault destroys unrelated live
    sessions. Both routine close paths produce such faults -- the hub drops a
    session before its `session_close` reaches the host, while the host's
    snapshot and capture threads are still emitting -- so these must leave the
    connection intact. Connection-scoped failures (unknown or superseded host,
    a disabled service, changed authority) keep raising `RegistryError`.
    """


PHONE_SEMANTIC_LANE = "phone_semantic_v1"
PHONE_FILES_LANE = "phone_files_v1"
PHONE_SYSTEM_LANE = "phone_system_v1"
PHONE_HOST_LANES = frozenset({PHONE_SEMANTIC_LANE, PHONE_FILES_LANE, PHONE_SYSTEM_LANE})
WORKSTATION_FILES_LANE = "files_v1"
HOST_ACTIONS_LANE = "host_actions_v1"
MAX_PHONE_FILE_ENTRIES = 12
MAX_PHONE_FILE_READ_BYTES = 6 * 1024
MIN_PHONE_CACHE_TRIM_BYTES = 16 * 1024 * 1024
MAX_PHONE_CACHE_TRIM_BYTES = 10 * 1024 * 1024 * 1024
SESSION_LANES = frozenset({"mo_session", "screen"})
LIVE_CONTROL_LANES = SESSION_LANES | PHONE_HOST_LANES | {
    WORKSTATION_FILES_LANE,
    HOST_ACTIONS_LANE,
}
CONTROLLER_SCOPE = "remote_control"
HOST_SCOPE = "remote_host"
SESSION_ID_BYTES = 32
MAX_WIRE_TEXT_BYTES = 16 * 1024
MAX_FILE_WIRE_BYTES = 512 * 1024
MAX_FRAME_BYTES = 512 * 1024
MAX_HOSTS = 16
HOST_STALE_SECONDS = 45
RESOURCE_REFRESH_SECONDS = 3.0
RESOURCE_RESPONSE_SECONDS = 2.5
RESOURCE_STALE_SECONDS = 15.0
# Bounded device power/attachment presence: a short-lived, opt-in fact the phone
# reports so the Desktop cube can mirror a verified charging state. It carries
# no identity beyond an opaque key and never grants any control authority.
PRESENCE_STALE_SECONDS = 120.0
PRESENCE_POWER_STATES = frozenset({"charging", "full", "battery", "unknown"})
PRESENCE_POWER_SOURCES = frozenset({"usb", "ac", "wireless", "unknown"})
PRESENCE_PHYSICAL_LINKS = frozenset({"usb", "network", "none", "unknown"})
EVERYWHERE_DISABLE_PATH = "run/everywhere.disabled"
DISABLED_CLOSE_CODE = 1013
_LIVE_KEY_NAMES = frozenset({
    "backspace", "delete", "down", "end", "enter", "escape", "home",
    "left", "pagedown", "pageup", "right", "space", "tab", "up",
})

_CLIENT_MESSAGE_TYPES = frozenset({
    "terminal_command",
    "close",
    "key",
    "ping",
    "pointer",
    "text",
    "viewport",
    "screen_visibility",
})
_HOST_MESSAGE_TYPES = frozenset({
    "resource_snapshot",
    "terminal_command_result",
    "notice",
    "pong",
    "screen_meta",
    "session_closed",
    "session_ready",
    "terminal_snapshot",
    "terminal_update",
    "phone_result",
    "file_result",
    "host_action_result",
})


@dataclass(frozen=True)
class LiveControlSettings:
    enabled: bool = False
    phone_semantic_enabled: bool = False
    session_seconds: int = 600
    idle_seconds: int = 120

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> "LiveControlSettings":
        block = (config or {}).get("consistent_everywhere")
        block = block if isinstance(block, dict) else {}
        live = block.get("live_control") if isinstance(block.get("live_control"), dict) else {}
        return cls(
            enabled=block.get("enabled") is True and live.get("enabled") is True,
            phone_semantic_enabled=live.get("phone_semantic_enabled") is True,
            session_seconds=max(60, min(900, int(live.get("session_seconds", 600) or 600))),
            idle_seconds=max(15, min(300, int(live.get("idle_seconds", 120) or 120))),
        )


@dataclass
class _Host:
    principal: DevicePrincipal
    websocket: Any
    host_key: str
    machine_key: str
    instance_id: str
    host_kind: str
    platform_family: str
    architecture: str
    app_version: str
    started_at: float
    label: str
    lanes: frozenset[str]
    connected_at: float
    last_seen_at: float
    command_menu: bool = False
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    phone_sequence: int = 0
    file_sequence: int = 0
    host_action_sequence: int = 0
    resources_supported: bool = False
    resources: dict[str, Any] | None = None
    resources_received_at: float = 0.0
    resources_requested_at: float | None = None
    resource_sequence: int = 0
    resource_observed: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class _Session:
    session_id: str
    controller: DevicePrincipal
    host_id: str
    lane: str
    created_at: float
    expires_at: float
    last_activity_at: float
    client: Any = None
    client_send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    client_sequence: int = 0
    host_sequence: int = 0
    screen_meta_seen: bool = False
    screen_visible: bool = True


@dataclass
class _PhoneRequest:
    request_id: str
    principal: DevicePrincipal
    host_id: str
    operation: str
    created_at: float
    expires_at: float
    future: asyncio.Future[dict[str, Any]]


@dataclass
class _FileRequest:
    request_id: str
    principal: DevicePrincipal
    host_id: str
    operation: str
    created_at: float
    expires_at: float
    future: asyncio.Future[dict[str, Any]]


@dataclass
class _HostActionRequest:
    request_id: str
    principal: DevicePrincipal
    host_id: str
    operation: str
    arguments: dict[str, Any]
    created_at: float
    expires_at: float
    future: asyncio.Future[dict[str, Any]]


class LiveControlBroker:
    """In-process relay state with strict bounds and no durable payload path."""

    def __init__(
        self,
        registry: DeviceRegistry,
        config: dict[str, Any] | None = None,
    ):
        self.registry = registry
        self.config = config or {}
        self.settings = LiveControlSettings.from_config(self.config)
        self.everywhere_disable = Path(resolve_state_path(EVERYWHERE_DISABLE_PATH, self.config))
        self.kill_switch = Path(resolve_state_path(KILL_SWITCH_PATH, self.config))
        self._hosts: dict[str, _Host] = {}
        self._sessions: dict[str, _Session] = {}
        self._phone_requests: dict[str, _PhoneRequest] = {}
        self._file_requests: dict[str, _FileRequest] = {}
        self._host_action_requests: dict[str, _HostActionRequest] = {}
        self._device_presence: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    def status(self, *, include_resources: bool = False) -> dict[str, Any]:
        self._expire_sync()
        disabled = self.is_disabled()
        hosts = [] if disabled else [
            {
                "host_id": host_id,
                "host_key": host.host_key,
                "machine_key": host.machine_key,
                "instance_id": host.instance_id,
                "host_kind": host.host_kind,
                "platform_family": host.platform_family,
                "architecture": host.architecture,
                "app_version": host.app_version,
                "started_at": host.started_at,
                "label": host.label,
                "lanes": sorted(host.lanes),
                "connected_at": host.connected_at,
                **({"resources_supported": host.resources_supported,
                    "resources": self._resource_status(host)} if include_resources else {}),
            }
            for host_id, host in sorted(self._hosts.items())
        ]
        return {
            "configured": self.settings.enabled,
            "enabled": self.settings.enabled and not disabled,
            "phone_semantic_enabled": self.settings.phone_semantic_enabled and not disabled,
            "locally_disabled": self.local_gate_closed(),
            "transport_owned_by_mo": True,
            "hosts": hosts,
        }

    @staticmethod
    def _resource_status(host: _Host) -> dict[str, Any] | None:
        if host.resources is None:
            return None
        return {
            **host.resources,
            "process": dict(host.resources["process"]),
            "sample_age_seconds": host.resources["sample_age_seconds"]
            + max(0.0, time.monotonic() - host.resources_received_at),
        }

    async def request_resources(self) -> None:
        """Collect one bounded observation per capable host without a control lease."""
        await self._require_enabled()
        now = time.monotonic()
        async with self._lock:
            self._expire_locked(time.time())
            hosts = [host for host in self._hosts.values() if host.resources_supported and (
                host.resources_requested_at is None
                or now - host.resources_requested_at >= RESOURCE_REFRESH_SECONDS
            )]
            for host in hosts:
                host.resources_requested_at = now
                host.resource_observed.clear()

        async def request(host: _Host) -> None:
            try:
                await asyncio.wait_for(
                    self._send_host(host, {"type": "resource_request"}), timeout=0.5,
                )
                await asyncio.wait_for(host.resource_observed.wait(), timeout=RESOURCE_RESPONSE_SECONDS)
            except Exception:
                # The cached observation ages independently of connection liveness.
                pass

        await asyncio.gather(*(request(host) for host in hosts))

    def local_gate_closed(self) -> bool:
        return self.everywhere_disable.is_file() or self.kill_switch.is_file()

    def is_disabled(self) -> bool:
        return not self.settings.enabled or self.local_gate_closed()

    async def close_if_disabled(self) -> bool:
        """Release every in-memory lease and socket when either local gate closes."""
        if not self.is_disabled():
            return False
        async with self._lock:
            sessions = list(self._sessions.values())
            hosts = list(self._hosts.values())
            phone_requests = list(self._phone_requests.values())
            file_requests = list(self._file_requests.values())
            host_action_requests = list(self._host_action_requests.values())
            self._sessions.clear()
            self._hosts.clear()
            self._phone_requests.clear()
            self._file_requests.clear()
            self._host_action_requests.clear()
        _fail_phone_requests(phone_requests, "phone actuation is disabled")
        _fail_file_requests(file_requests, "MO Files is disabled")
        _fail_host_action_requests(host_action_requests, "Desktop host actions are disabled")
        for session in sessions:
            await _safe_close(session.client, DISABLED_CLOSE_CODE)
            self._audit("live_control_session", session.controller.device_id, "disabled", session.lane)
        for host in hosts:
            await _safe_close(host.websocket, DISABLED_CLOSE_CODE)
            self._audit("live_control_host", host.principal.device_id, "disabled", "local_gate")
        return True

    async def _require_enabled(self) -> None:
        if await self.close_if_disabled():
            raise RegistryError("live control is disabled")

    async def report_device_presence(
        self, principal: DevicePrincipal, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Accept one bounded presence report and mirror it to Desktop hosts.

        The stored row and the pushed event carry only the validated power/link
        enums, a server timestamp, and an opaque per-device key — never the
        device id, label, or any credential. Rows expire with the same reaper
        that ages hosts, and Desktop applies its own expiry besides.
        """
        await self._require_enabled()
        if CONTROLLER_SCOPE not in principal.scopes:
            raise RegistryError("device scope is insufficient")
        row = _device_presence_row(payload)
        now = time.time()
        async with self._lock:
            self._expire_locked(now)
            self._device_presence[principal.device_id] = dict(row)
            desktop_hosts = [
                host
                for host in self._hosts.values()
                if host.host_kind == "desktop"
            ]
        push = {
            "type": "device_presence",
            "presence_key": _presence_key(principal.device_id),
            **row,
        }
        if desktop_hosts:
            # The mirror is cosmetic: sends run concurrently with a short
            # timeout so a half-dead Desktop socket can never hold the
            # phone's report (and with it the phone's own refresh) hostage.
            await asyncio.gather(
                *(self._push_presence(host, push) for host in desktop_hosts),
            )
        return dict(row)

    async def _push_presence(self, host: _Host, payload: dict[str, Any]) -> None:
        try:
            await asyncio.wait_for(self._send_host(host, payload), timeout=2.0)
        except Exception:
            return

    def device_presence(self, principal: DevicePrincipal) -> dict[str, Any] | None:
        """The caller's own current presence row, or None once it has aged out."""
        row = self._device_presence.get(principal.device_id)
        if not row or time.time() - row["reported_at"] > PRESENCE_STALE_SECONDS:
            return None
        return dict(row)

    async def register_host(
        self,
        principal: DevicePrincipal,
        websocket: Any,
        hello: dict[str, Any],
    ) -> str:
        await self._require_enabled()
        if HOST_SCOPE not in principal.scopes:
            raise RegistryError("device scope is insufficient")
        label = _bounded_label(hello.get("label"), principal.label or "MO host")
        lanes = _lanes(hello.get("lanes"))
        if not lanes:
            raise RegistryError("live control host has no supported lane")
        instance_key = _host_instance_key(hello.get("instance_key"), label, lanes)
        host_key = _stable_host_key(principal.device_id, instance_key)
        metadata = _host_metadata(hello, instance_key=instance_key, lanes=lanes)
        phone_host = bool(lanes & PHONE_HOST_LANES)
        automation_host = bool(lanes & {PHONE_SEMANTIC_LANE, PHONE_SYSTEM_LANE})
        if automation_host and not self.settings.phone_semantic_enabled:
            raise RegistryError("phone semantic control is disabled")
        if phone_host and (
            # A consented phone may serve files without Accessibility, using
            # the same bounded MO
            # Files contract every workstation speaks, so it appears as an
            # ordinary browsable source. No other lane is allowed alongside the
            # dedicated phone lanes.
            not lanes.issubset(PHONE_HOST_LANES | {WORKSTATION_FILES_LANE})
            or (PHONE_SYSTEM_LANE in lanes and PHONE_SEMANTIC_LANE not in lanes)
        ):
            raise RegistryError("phone host must use its dedicated lanes")
        if phone_host and WORKSTATION_FILES_LANE in lanes and (
            PHONE_FILES_LANE not in lanes
            or "file_browse" not in principal.scopes
        ):
            raise RegistryError("phone files source needs its consented files lane")
        if phone_host and (
            principal.capability != "control"
            or CONTROLLER_SCOPE not in principal.scopes
            or HOST_SCOPE not in principal.scopes
        ):
            raise RegistryError("phone semantic host authority is insufficient")
        if PHONE_SEMANTIC_LANE in lanes and hello.get("ui_text_consent") is not True:
            raise RegistryError("phone semantic host UI-text consent is required")
        if PHONE_FILES_LANE in lanes and hello.get("file_tree_consent") is not True:
            raise RegistryError("phone files host folder consent is required")
        if PHONE_SYSTEM_LANE in lanes and hello.get("privileged_tools_consent") is not True:
            raise RegistryError("phone system host privileged-tools consent is required")
        now = time.time()
        replaced_hosts: list[_Host] = []
        replaced_sessions: list[_Session] = []
        replaced_requests: list[_PhoneRequest] = []
        replaced_file_requests: list[_FileRequest] = []
        replaced_host_action_requests: list[_HostActionRequest] = []
        async with self._lock:
            self._expire_locked(now)
            for existing_id, existing in list(self._hosts.items()):
                same_host = existing.host_key == host_key
                same_phone = (
                    phone_host
                    and existing.principal.device_id == principal.device_id
                    and bool(existing.lanes & PHONE_HOST_LANES)
                )
                if not same_host and not same_phone:
                    continue
                replaced_hosts.append(self._hosts.pop(existing_id))
                replaced_requests.extend(self._pop_phone_requests_locked(existing_id))
                replaced_file_requests.extend(
                    self._pop_file_requests_locked(existing_id)
                )
                replaced_host_action_requests.extend(
                    self._pop_host_action_requests_locked(existing_id)
                )
                for session_id, session in list(self._sessions.items()):
                    if session.host_id == existing_id:
                        replaced_sessions.append(self._sessions.pop(session_id))
            if len(self._hosts) >= MAX_HOSTS:
                raise RegistryError("live control host limit reached")
            host_id = uuid.uuid4().hex
            self._hosts[host_id] = _Host(
                principal=principal,
                websocket=websocket,
                host_key=host_key,
                machine_key=metadata["machine_key"],
                instance_id=metadata["instance_id"],
                host_kind=metadata["host_kind"],
                platform_family=metadata["platform_family"],
                architecture=metadata["architecture"],
                app_version=metadata["app_version"],
                started_at=metadata["started_at"],
                label=label,
                lanes=lanes,
                connected_at=now,
                last_seen_at=now,
                command_menu=hello.get("command_menu") is True and "mo_session" in lanes,
                resources_supported=hello.get("resources_v1") is True and not phone_host,
            )
        _fail_phone_requests(replaced_requests, "phone host was replaced")
        _fail_file_requests(replaced_file_requests, "MO Files host was replaced")
        _fail_host_action_requests(
            replaced_host_action_requests,
            "Desktop host was replaced",
        )
        for session in replaced_sessions:
            await _safe_close(session.client, 4004)
            self._audit(
                "live_control_session",
                session.controller.device_id,
                "host_replaced",
                session.lane,
            )
        for replaced_host in replaced_hosts:
            await _safe_close(replaced_host.websocket, 4001)
            self._audit(
                "live_control_host",
                replaced_host.principal.device_id,
                "replaced",
                ",".join(sorted(replaced_host.lanes)),
            )
        self._audit("live_control_host", principal.device_id, "connected", ",".join(sorted(lanes)))
        return host_id

    async def unregister_host(self, host_id: str, websocket: Any) -> None:
        closing: list[_Session] = []
        phone_requests: list[_PhoneRequest] = []
        file_requests: list[_FileRequest] = []
        host_action_requests: list[_HostActionRequest] = []
        principal_id = ""
        async with self._lock:
            host = self._hosts.get(host_id)
            if host is None or host.websocket is not websocket:
                return
            principal_id = host.principal.device_id
            self._hosts.pop(host_id, None)
            for session_id, session in list(self._sessions.items()):
                if session.host_id == host_id:
                    closing.append(self._sessions.pop(session_id))
            phone_requests = self._pop_phone_requests_locked(host_id)
            file_requests = self._pop_file_requests_locked(host_id)
            host_action_requests = self._pop_host_action_requests_locked(host_id)
        for session in closing:
            await _safe_close(session.client, 4004)
        _fail_phone_requests(phone_requests, "phone host disconnected")
        _fail_file_requests(file_requests, "MO Files host disconnected")
        _fail_host_action_requests(host_action_requests, "Desktop host disconnected")
        self._audit("live_control_host", principal_id, "disconnected", "")

    async def request_phone(
        self,
        principal: DevicePrincipal,
        operation: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float = 12.0,
    ) -> dict[str, Any]:
        """Send one semantic request only to the phone that originated the turn."""
        await self._require_enabled()
        if (
            principal.capability != "control"
            or CONTROLLER_SCOPE not in principal.scopes
            or HOST_SCOPE not in principal.scopes
        ):
            self._audit("phone_actuation", principal.device_id, "denied", "authority")
            raise RegistryError("phone actuation authority is insufficient")
        clean_operation, clean_arguments = _phone_request(operation, arguments)
        required_lane = PHONE_SEMANTIC_LANE
        if clean_operation.startswith("files_"):
            required_lane = PHONE_FILES_LANE
        elif clean_operation in {
            "system_status", "cache_report", "cache_trim", "packages_list", "package_action",
            "shell_execute",
        }:
            required_lane = PHONE_SYSTEM_LANE
        if required_lane != PHONE_FILES_LANE and not self.settings.phone_semantic_enabled:
            raise RegistryError("phone semantic control is disabled")
        timeout = max(1.0, min(20.0, float(timeout_seconds)))
        now = time.time()
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        async with self._lock:
            self._expire_locked(now)
            matches = [
                (host_id, host)
                for host_id, host in self._hosts.items()
                if host.principal.device_id == principal.device_id
                and required_lane in host.lanes
            ]
            if len(matches) != 1:
                raise RegistryError("origin phone semantic host is unavailable")
            host_id, host = matches[0]
            if any(request.host_id == host_id for request in self._phone_requests.values()):
                raise RegistryError("origin phone semantic host is busy")
            pending = _PhoneRequest(
                request_id=request_id,
                principal=principal,
                host_id=host_id,
                operation=clean_operation,
                created_at=now,
                expires_at=now + timeout,
                future=loop.create_future(),
            )
            self._phone_requests[request_id] = pending
        try:
            await self._send_host(host, {
                "type": "phone_request",
                "request_id": request_id,
                "operation": clean_operation,
                "arguments": clean_arguments,
                "expires_at": pending.expires_at,
            })
            result = await asyncio.wait_for(asyncio.shield(pending.future), timeout=timeout)
        except asyncio.TimeoutError:
            self._audit("phone_actuation", principal.device_id, "timeout", clean_operation)
            raise RegistryError("phone actuation timed out") from None
        except RegistryError:
            self._audit("phone_actuation", principal.device_id, "failed", clean_operation)
            raise
        except Exception:
            self._audit("phone_actuation", principal.device_id, "unavailable", clean_operation)
            raise RegistryError("origin phone semantic host became unavailable") from None
        finally:
            async with self._lock:
                self._phone_requests.pop(request_id, None)
        self._audit("phone_actuation", principal.device_id, "completed", clean_operation)
        return result

    def file_sources(self) -> list[dict[str, Any]]:
        """Return online workstation file authorities without exposing paths."""
        self._expire_sync()
        if self.is_disabled():
            return []
        sources: list[dict[str, Any]] = []
        for host_id, host in sorted(
            self._hosts.items(),
            key=lambda item: (item[1].label.casefold(), item[1].host_key),
        ):
            if WORKSTATION_FILES_LANE not in host.lanes:
                continue
            kind = (
                # A phone is identified by its own lanes first: it may also
                # carry the screen lane to mirror itself, and that must not
                # make it report as a workstation.
                "phone"
                if host.lanes & PHONE_HOST_LANES
                else "desktop"
                if "screen" in host.lanes
                else "terminal"
                if "mo_session" in host.lanes
                else "machine"
            )
            sources.append(
                {
                    "source_id": f"host-{host_id}",
                    "host_key": host.host_key,
                    "label": host.label,
                    "kind": kind,
                    "online": True,
                    "operations": (
                        ["list", "read"]
                        if kind == "phone"
                        else [
                            "list",
                            "read",
                            "preview",
                            "edit_text",
                            "create_folder",
                            "rename",
                            "copy",
                            "move",
                            "delete",
                            "trash",
                            "restore",
                            "send",
                        ]
                    ),
                }
            )
        return sources

    async def request_files(
        self,
        principal: DevicePrincipal,
        source_id: Any,
        operation: Any,
        arguments: Any,
        *,
        timeout_seconds: float = 20.0,
    ) -> dict[str, Any]:
        """Route one file operation to its exact online native host."""
        await self._require_enabled()
        clean_operation, clean_arguments = _file_request(operation, arguments)
        if (
            principal.capability != "control"
            or "file_browse" not in principal.scopes
        ):
            self._audit("file_host_request", principal.device_id, "denied", "authority")
            raise RegistryError("MO Files authority is insufficient")
        if (
            clean_operation
            in {
                "write_text", "create_folder", "rename", "copy", "move",
                "delete", "trash", "restore",
            }
            and "file_manage" not in principal.scopes
        ):
            self._audit("file_host_request", principal.device_id, "denied", "manage")
            raise RegistryError("MO Files management authority is insufficient")
        if (
            clean_operation == "send"
            and "file_transfer" not in principal.scopes
        ):
            self._audit("file_host_request", principal.device_id, "denied", "transfer")
            raise RegistryError("MO Files transfer authority is insufficient")
        host_id = _file_source_host_id(source_id)
        timeout = max(1.0, min(30.0, float(timeout_seconds)))
        now = time.time()
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        async with self._lock:
            self._expire_locked(now)
            host = self._hosts.get(host_id)
            if host is None or WORKSTATION_FILES_LANE not in host.lanes:
                raise RegistryError("MO Files source is unavailable")
            if host.lanes & PHONE_HOST_LANES and clean_operation not in {
                "locations", "list", "read_text",
            }:
                raise RegistryError("phone MO Files sources are read-only")
            if any(
                request.host_id == host_id
                for request in self._file_requests.values()
            ):
                raise RegistryError("MO Files source is busy")
            pending = _FileRequest(
                request_id=request_id,
                principal=principal,
                host_id=host_id,
                operation=clean_operation,
                created_at=now,
                expires_at=now + timeout,
                future=loop.create_future(),
            )
            self._file_requests[request_id] = pending
        try:
            await self._send_host(
                host,
                {
                    "type": "file_request",
                    "request_id": request_id,
                    "operation": clean_operation,
                    "arguments": clean_arguments,
                    "expires_at": pending.expires_at,
                },
            )
            result = await asyncio.wait_for(
                asyncio.shield(pending.future), timeout=timeout
            )
        except asyncio.TimeoutError:
            self._audit(
                "file_host_request",
                principal.device_id,
                "timeout",
                clean_operation,
            )
            raise RegistryError("MO Files source timed out") from None
        except RegistryError:
            self._audit(
                "file_host_request",
                principal.device_id,
                "failed",
                clean_operation,
            )
            raise
        except Exception:
            self._audit(
                "file_host_request",
                principal.device_id,
                "unavailable",
                clean_operation,
            )
            raise RegistryError("MO Files source became unavailable") from None
        finally:
            async with self._lock:
                self._file_requests.pop(request_id, None)
        sanitized = _file_response(clean_operation, result)
        self._audit(
            "file_host_request",
            principal.device_id,
            "completed",
            clean_operation,
        )
        return sanitized

    async def request_host_action(
        self,
        principal: DevicePrincipal,
        host_id: Any,
        operation: Any,
        arguments: Any,
        client_request_id: Any,
        *,
        timeout_seconds: float = 20.0,
    ) -> dict[str, Any]:
        """Run one fixed action on one exact authenticated Desktop host."""
        await self._require_enabled()
        clean_host_id = _live_host_id(host_id)
        clean_operation, clean_arguments = _host_action(operation, arguments)
        request_id = _wire_request_id(client_request_id, "Desktop host action")
        if (
            principal.capability != "control"
            or CONTROLLER_SCOPE not in principal.scopes
        ):
            self._audit(
                "desktop_host_action", principal.device_id, "denied", "authority"
            )
            raise RegistryError("Desktop host action authority is insufficient")
        timeout = max(1.0, min(30.0, float(timeout_seconds)))
        now = time.time()
        loop = asyncio.get_running_loop()
        created = False
        async with self._lock:
            self._expire_locked(now)
            host = self._hosts.get(clean_host_id)
            if (
                host is None
                or HOST_ACTIONS_LANE not in host.lanes
                or host.host_kind != "desktop"
            ):
                raise RegistryError("Desktop host action is unavailable")
            pending = self._host_action_requests.get(request_id)
            if pending is not None and (
                pending.host_id != clean_host_id
                or pending.principal.device_id != principal.device_id
                or pending.operation != clean_operation
                or pending.arguments != clean_arguments
            ):
                raise RegistryError("Desktop host action id is already in use")
            if pending is None:
                pending = _HostActionRequest(
                    request_id=request_id,
                    principal=principal,
                    host_id=clean_host_id,
                    operation=clean_operation,
                    arguments=clean_arguments,
                    created_at=now,
                    expires_at=now + timeout,
                    future=loop.create_future(),
                )
                self._host_action_requests[request_id] = pending
                created = True
        try:
            if created:
                await self._send_host(
                    host,
                    {
                        "type": "host_action_request",
                        "request_id": request_id,
                        "operation": clean_operation,
                        "arguments": clean_arguments,
                        "expires_at": pending.expires_at,
                    },
                )
            result = await asyncio.wait_for(
                asyncio.shield(pending.future), timeout=timeout
            )
        except asyncio.TimeoutError:
            self._audit(
                "desktop_host_action", principal.device_id, "timeout", clean_operation
            )
            raise RegistryError("Desktop host action timed out") from None
        except RegistryError:
            self._audit(
                "desktop_host_action", principal.device_id, "failed", clean_operation
            )
            raise
        except Exception:
            self._audit(
                "desktop_host_action",
                principal.device_id,
                "unavailable",
                clean_operation,
            )
            raise RegistryError("Desktop host became unavailable") from None
        finally:
            if created:
                async with self._lock:
                    self._host_action_requests.pop(request_id, None)
        sanitized = _host_action_response(clean_operation, result)
        self._audit(
            "desktop_host_action", principal.device_id, "completed", clean_operation
        )
        return sanitized

    async def prepare(
        self,
        principal: DevicePrincipal,
        host_id: str,
        lane: str,
    ) -> dict[str, Any]:
        await self._require_enabled()
        if principal.capability != "control" or CONTROLLER_SCOPE not in principal.scopes:
            self._audit("live_control_prepare", principal.device_id, "denied", "authority")
            raise RegistryError("device authority is insufficient")
        clean_host = str(host_id or "").strip()
        clean_lane = str(lane or "").strip().lower()
        if clean_lane not in SESSION_LANES:
            raise RegistryError("requested live control host is unavailable")
        now = time.time()
        async with self._lock:
            self._expire_locked(now)
            host = self._hosts.get(clean_host)
            if host is None or clean_lane not in host.lanes:
                raise RegistryError("requested live control host is unavailable")
            if any(item.host_id == clean_host for item in self._sessions.values()):
                raise RegistryError("requested live control host is already controlled")
            session_id = uuid.uuid4().hex
            session = _Session(
                session_id=session_id,
                controller=principal,
                host_id=clean_host,
                lane=clean_lane,
                created_at=now,
                expires_at=now + self.settings.session_seconds,
                last_activity_at=now,
            )
            self._sessions[session_id] = session
        try:
            await self._send_host(host, {
                "type": "session_open",
                "session_id": session_id,
                "lane": clean_lane,
                "expires_at": session.expires_at,
            })
        except Exception:
            async with self._lock:
                self._sessions.pop(session_id, None)
                # A host that cannot receive its own session_open is gone. Leaving
                # it listed keeps advertising a terminal that every later lease
                # would also fail against -- and after a half-open reconnect that
                # stale duplicate sits beside the live entry under an identical label.
                if self._hosts.get(clean_host) is host:
                    self._hosts.pop(clean_host, None)
            await _safe_close(host.websocket, 4008)
            raise RegistryError("live control host became unavailable") from None
        self._audit("live_control_prepare", principal.device_id, "prepared", clean_lane)
        return {
            "session_id": session_id,
            "host_id": clean_host,
            "host_key": host.host_key,
            "lane": clean_lane,
            "prepared_at": now,
            "expires_at": session.expires_at,
            "websocket_path": f"/api/mo/live/client/{session_id}",
            "command_menu": host.command_menu and clean_lane == "mo_session",
            "transport_owned_by_mo": True,
            "screen_visibility_supported": clean_lane == "screen",
        }

    async def attach_client(self, principal: DevicePrincipal, session_id: str, websocket: Any) -> _Session:
        await self._require_enabled()
        now = time.time()
        async with self._lock:
            self._expire_locked(now)
            session = self._sessions.get(str(session_id or ""))
            if session is None or session.controller.device_id != principal.device_id:
                raise RegistryError("live control session is invalid or expired")
            if session.client is not None and session.client is not websocket:
                raise RegistryError("live control session already has a controller")
            host = self._hosts.get(session.host_id)
            if host is None:
                raise RegistryError("live control host is unavailable")
            session.client = websocket
            session.last_activity_at = now
        try:
            async with session.client_send_lock:
                await websocket.send_text(json.dumps({
                    "type": "session_ready",
                    "lane": session.lane,
                }, separators=(",", ":")))
            await self._send_host(host, {
                "type": "client_connected",
                "session_id": session.session_id,
                "lane": session.lane,
            })
        except Exception:
            # The lease is already bound to this socket, but the caller only
            # runs detach_client for an attach that returned. Release it here or
            # the host stays "already controlled" until the lease expires, with
            # re-attach refused because the session still holds a dead client.
            await self.close_session(session.session_id, reason="attach_failed")
            raise
        self._audit("live_control_client", principal.device_id, "connected", session.lane)
        return session

    async def detach_client(self, session_id: str, websocket: Any) -> None:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None or session.client is not websocket:
                return
        await self.close_session(session_id, reason="client_disconnected")

    async def close_session(self, session_id: str, *, reason: str) -> None:
        async with self._lock:
            session = self._sessions.pop(str(session_id or ""), None)
            host = self._hosts.get(session.host_id) if session is not None else None
        if session is None:
            return
        if host is not None:
            try:
                await self._send_host(host, {
                    "type": "session_close",
                    "session_id": session.session_id,
                    "reason": str(reason or "closed")[:48],
                })
            except Exception:
                pass
        await _safe_close(session.client, 1000)
        self._audit("live_control_session", session.controller.device_id, "closed", session.lane)

    async def client_text(self, session_id: str, websocket: Any, raw: str) -> None:
        await self._require_enabled()
        payload = _client_message(raw)
        sequence = payload["sequence"]
        async with self._lock:
            session = self._active_session_locked(session_id, websocket=websocket)
            if payload["type"] == "terminal_command" and session.lane != "mo_session":
                raise RegistryError("terminal commands require a MO terminal lease")
            if payload["type"] == "screen_visibility" and session.lane != "screen":
                raise RegistryError("screen visibility requires a screen lease")
            if sequence <= session.client_sequence:
                raise RegistryError("stale live control input")
            session.client_sequence = sequence
            if payload["type"] == "screen_visibility":
                session.screen_visible = payload["visible"]
            session.last_activity_at = time.time()
            host = self._hosts.get(session.host_id)
        if host is None:
            raise RegistryError("live control host is unavailable")
        if payload["type"] == "close":
            await self.close_session(session_id, reason="client_close")
            return
        if payload["type"] == "ping":
            async with session.client_send_lock:
                await websocket.send_text('{"type":"pong"}')
            return
        payload["session_id"] = session_id
        await self._send_host(host, payload)

    async def host_text(self, host_id: str, websocket: Any, raw: str) -> None:
        await self._require_enabled()
        payload = _host_message(raw)
        if payload["type"] == "resource_snapshot":
            async with self._lock:
                self._expire_locked(time.time())
                host = self._hosts.get(host_id)
                if host is None or host.websocket is not websocket or not host.resources_supported:
                    raise RegistryError("resource host is unavailable")
                if payload["sequence"] <= host.resource_sequence:
                    raise RegistryError("stale resource observation")
                host.resource_sequence = payload["sequence"]
                host.resources = payload["sample"]
                host.resources_received_at = time.monotonic()
                host.last_seen_at = time.time()
                host.resource_observed.set()
            return
        if payload["type"] == "ping":
            async with self._lock:
                self._expire_locked(time.time())
                host = self._hosts.get(host_id)
                if host is None or host.websocket is not websocket:
                    raise RegistryError("live control host is unavailable")
                host.last_seen_at = time.time()
            await self._send_host(host, {"type": "pong"})
            return
        if payload["type"] == "file_result":
            request_id = str(payload["request_id"])
            sequence = payload["sequence"]
            async with self._lock:
                host = self._hosts.get(host_id)
                if (
                    host is None
                    or host.websocket is not websocket
                    or WORKSTATION_FILES_LANE not in host.lanes
                ):
                    raise RegistryError("MO Files host is unavailable")
                pending = self._file_requests.get(request_id)
                if pending is None or pending.host_id != host_id:
                    raise RegistryError(
                        "MO Files request is invalid or expired"
                    )
                if sequence <= host.file_sequence:
                    raise RegistryError("stale MO Files result")
                host.file_sequence = sequence
                host.last_seen_at = time.time()
                future = pending.future
            if payload["ok"]:
                if not future.done():
                    future.set_result(dict(payload["result"]))
            elif not future.done():
                future.set_exception(RegistryError(str(payload["error"])))
            return
        if payload["type"] == "host_action_result":
            request_id = str(payload["request_id"])
            sequence = payload["sequence"]
            async with self._lock:
                host = self._hosts.get(host_id)
                if (
                    host is None
                    or host.websocket is not websocket
                    or HOST_ACTIONS_LANE not in host.lanes
                    or host.host_kind != "desktop"
                ):
                    raise RegistryError("Desktop host action is unavailable")
                pending = self._host_action_requests.get(request_id)
                if pending is None or pending.host_id != host_id:
                    raise RegistryError(
                        "Desktop host action is invalid or expired"
                    )
                if sequence <= host.host_action_sequence:
                    raise RegistryError("stale Desktop host action result")
                host.host_action_sequence = sequence
                host.last_seen_at = time.time()
                future = pending.future
            if payload["ok"]:
                if not future.done():
                    future.set_result(dict(payload["result"]))
            elif not future.done():
                future.set_exception(RegistryError(str(payload["error"])))
            return
        if payload["type"] == "phone_result":
            request_id = str(payload["request_id"])
            sequence = payload["sequence"]
            async with self._lock:
                host = self._hosts.get(host_id)
                if host is None or host.websocket is not websocket or not host.lanes & PHONE_HOST_LANES:
                    raise RegistryError("phone semantic host is unavailable")
                pending = self._phone_requests.get(request_id)
                if (
                    pending is None
                    or pending.host_id != host_id
                    or pending.principal.device_id != host.principal.device_id
                ):
                    raise RegistryError("phone actuation request is invalid or expired")
                if sequence <= host.phone_sequence:
                    raise RegistryError("stale phone actuation result")
                host.phone_sequence = sequence
                host.last_seen_at = time.time()
                future = pending.future
            if payload["ok"]:
                if not future.done():
                    future.set_result(dict(payload["result"]))
            elif not future.done():
                future.set_exception(RegistryError(str(payload["error"])))
            return
        session_id = str(payload.get("session_id", "") or "")
        sequence = payload["sequence"]
        async with self._lock:
            session = self._active_session_locked(session_id, host_id=host_id)
            kind = payload["type"]
            if kind == "session_ready" and payload.get("lane") != session.lane:
                raise SessionFault("live control host lane changed")
            if kind == "screen_meta":
                if session.lane != "screen":
                    raise SessionFault("live control host lane is invalid")
            if kind in {"terminal_snapshot", "terminal_update", "terminal_command_result"} and session.lane != "mo_session":
                raise SessionFault("live control host lane is invalid")
            if sequence <= session.host_sequence:
                raise SessionFault("stale live control host update")
            if kind == "screen_meta":
                # Latched only once the message is known to be current, so a
                # replayed metadata frame cannot unlock the frame path.
                session.screen_meta_seen = True
            session.host_sequence = sequence
            session.last_activity_at = time.time()
            self._touch_host_locked(host_id)
            client = session.client
        if client is not None:
            async with session.client_send_lock:
                if kind != "screen_meta" or session.screen_visible:
                    await client.send_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))
        if payload["type"] == "session_closed":
            await self.close_session(session_id, reason="host_close")

    async def host_frame(self, host_id: str, websocket: Any, data: bytes) -> None:
        await self._require_enabled()
        if len(data) <= SESSION_ID_BYTES or len(data) > SESSION_ID_BYTES + MAX_FRAME_BYTES:
            raise SessionFault("live control frame is outside bounds")
        try:
            session_id = data[:SESSION_ID_BYTES].decode("ascii")
        except UnicodeDecodeError:
            raise SessionFault("live control frame session is invalid") from None
        frame = data[SESSION_ID_BYTES:]
        if len(frame) < 4 or frame[:3] != b"\xff\xd8\xff":
            raise SessionFault("live control frame format is invalid")
        async with self._lock:
            session = self._active_session_locked(session_id, host_id=host_id)
            if session.lane != "screen":
                raise SessionFault("live control frame lane is invalid")
            if not session.screen_meta_seen:
                raise SessionFault("live control frame metadata is missing")
            session.last_activity_at = time.time()
            self._touch_host_locked(host_id)
            client = session.client
        if client is not None:
            async with session.client_send_lock:
                if session.screen_visible:
                    await client.send_bytes(frame)

    def _touch_host_locked(self, host_id: str) -> None:
        """Count any delivered host message as liveness, not just `ping`.

        A screen host streams frames far more often than its 10s ping, and a
        capture or actuation burst can delay that ping past HOST_STALE_SECONDS.
        Without this, the reaper evicts a demonstrably live host mid-session.
        """
        host = self._hosts.get(str(host_id or ""))
        if host is not None:
            host.last_seen_at = time.time()

    def _active_session_locked(
        self,
        session_id: str,
        *,
        websocket: Any | None = None,
        host_id: str = "",
    ) -> _Session:
        self._expire_locked(time.time())
        session = self._sessions.get(str(session_id or ""))
        if session is None:
            raise SessionFault("live control session is invalid or expired")
        if websocket is not None and session.client is not websocket:
            raise SessionFault("live control controller is invalid")
        if host_id and session.host_id != host_id:
            raise SessionFault("live control host is invalid")
        return session

    async def _send_host(self, host: _Host, payload: dict[str, Any]) -> None:
        async with host.send_lock:
            await host.websocket.send_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

    def _expire_sync(self) -> None:
        # Status is deliberately synchronous. Expired sessions are omitted here;
        # their sockets are closed by the next async broker operation/disconnect.
        self._expire_locked(time.time())

    def _expire_locked(self, now: float) -> None:
        for device_id, row in list(self._device_presence.items()):
            if now - row["reported_at"] > PRESENCE_STALE_SECONDS:
                self._device_presence.pop(device_id, None)
        stale_hosts: list[_Host] = []
        reaped_host_ids: set[str] = set()
        for host_id, host in list(self._hosts.items()):
            if now - host.last_seen_at > HOST_STALE_SECONDS:
                self._hosts.pop(host_id, None)
                stale_hosts.append(host)
                reaped_host_ids.add(host_id)
        expired: list[tuple[_Session, _Host | None]] = []
        for session_id, session in list(self._sessions.items()):
            # A session outlives its host only as a leak: the reaper already
            # dropped the host, and unregister_host returns early once the host
            # is gone, so nothing else would ever close this client.
            if (
                session.host_id in reaped_host_ids
                or session.expires_at <= now
                or now - session.last_activity_at > self.settings.idle_seconds
            ):
                self._sessions.pop(session_id, None)
                expired.append((session, self._hosts.get(session.host_id)))
        expired_requests: list[_PhoneRequest] = []
        for request_id, request in list(self._phone_requests.items()):
            if request.expires_at <= now or request.host_id not in self._hosts:
                expired_requests.append(self._phone_requests.pop(request_id))
        _fail_phone_requests(expired_requests, "phone actuation request expired")
        expired_file_requests: list[_FileRequest] = []
        for request_id, request in list(self._file_requests.items()):
            if request.expires_at <= now or request.host_id not in self._hosts:
                expired_file_requests.append(self._file_requests.pop(request_id))
        _fail_file_requests(expired_file_requests, "MO Files request expired")
        expired_host_actions: list[_HostActionRequest] = []
        for request_id, request in list(self._host_action_requests.items()):
            if request.expires_at <= now or request.host_id not in self._hosts:
                expired_host_actions.append(
                    self._host_action_requests.pop(request_id)
                )
        _fail_host_action_requests(
            expired_host_actions, "Desktop host action expired"
        )
        if expired or stale_hosts:
            try:
                asyncio.get_running_loop().create_task(self._finish_expired(expired, stale_hosts))
            except RuntimeError:
                # Broker mutations normally run in the ASGI loop. A synchronous
                # status probe may only omit the expired memory record.
                pass

    async def _finish_expired(
        self,
        expired: list[tuple[_Session, _Host | None]],
        stale_hosts: list[_Host],
    ) -> None:
        for session, host in expired:
            if host is not None:
                try:
                    await self._send_host(host, {
                        "type": "session_close",
                        "session_id": session.session_id,
                        "reason": "expired",
                    })
                except Exception:
                    pass
            await _safe_close(session.client, 4008)
            self._audit("live_control_session", session.controller.device_id, "expired", session.lane)
        for host in stale_hosts:
            await _safe_close(host.websocket, 4008)
            self._audit("live_control_host", host.principal.device_id, "expired", "heartbeat")

    def _audit(self, event: str, device_id: str, outcome: str, detail: str) -> None:
        self.registry.record_audit(event, device_id=device_id, outcome=outcome, detail=detail)

    def _pop_phone_requests_locked(self, host_id: str) -> list[_PhoneRequest]:
        rows: list[_PhoneRequest] = []
        for request_id, request in list(self._phone_requests.items()):
            if request.host_id == host_id:
                rows.append(self._phone_requests.pop(request_id))
        return rows

    def _pop_file_requests_locked(self, host_id: str) -> list[_FileRequest]:
        rows: list[_FileRequest] = []
        for request_id, request in list(self._file_requests.items()):
            if request.host_id == host_id:
                rows.append(self._file_requests.pop(request_id))
        return rows

    def _pop_host_action_requests_locked(
        self, host_id: str
    ) -> list[_HostActionRequest]:
        rows: list[_HostActionRequest] = []
        for request_id, request in list(self._host_action_requests.items()):
            if request.host_id == host_id:
                rows.append(self._host_action_requests.pop(request_id))
        return rows

    async def shutdown(self) -> None:
        """Close all memory-only host/session/request state during app shutdown."""
        async with self._lock:
            sessions = list(self._sessions.values())
            hosts = list(self._hosts.values())
            phone_requests = list(self._phone_requests.values())
            file_requests = list(self._file_requests.values())
            host_action_requests = list(self._host_action_requests.values())
            self._sessions.clear()
            self._hosts.clear()
            self._phone_requests.clear()
            self._file_requests.clear()
            self._host_action_requests.clear()
        _fail_phone_requests(phone_requests, "phone actuation hub stopped")
        _fail_file_requests(file_requests, "MO Files hub stopped")
        _fail_host_action_requests(host_action_requests, "Desktop host action hub stopped")
        for session in sessions:
            await _safe_close(session.client, 1012)
        for host in hosts:
            await _safe_close(host.websocket, 1012)


def _bounded_label(value: Any, fallback: str) -> str:
    text = " ".join(str(value or fallback or "MO host").split())
    if not text or len(text) > 80 or any(ord(char) < 0x20 for char in text):
        raise RegistryError("live control host label is invalid")
    return text


def _host_instance_key(value: Any, label: str, lanes: frozenset[str]) -> str:
    """Return one reconnect-stable instance identity within a host principal.

    New hosts send ``instance_key`` explicitly. The label-derived branch is a
    rolling compatibility bridge for already-running hosts from before the
    stable-key protocol; remove it once every supported host process has been
    restarted from a release that sends ``instance_key``.
    """
    if value is None or value == "":
        if lanes == frozenset({"screen"}) and label == "MO Desktop":
            return "desktop"
        terminal_prefix = "MO Terminal · "
        if lanes == frozenset({"mo_session"}) and label.startswith(terminal_prefix):
            suffix = label[len(terminal_prefix):].strip()
            if suffix and len(suffix) <= 64 and all(
                char.isalnum() or char in "-_" for char in suffix
            ):
                return f"terminal:{suffix}"
        if lanes and lanes.issubset(PHONE_HOST_LANES):
            return "phone"
        # COMPAT(live-host-legacy-identity): replaced-by explicit instance keys; remove-when supported pre-key hosts have aged out
        legacy = hashlib.sha256(
            f"{label}\0{','.join(sorted(lanes))}".encode("utf-8")
        ).hexdigest()
        return f"legacy:{legacy}"
    if not isinstance(value, str):
        raise RegistryError("live control host instance key is invalid")
    text = value.strip()
    if (
        not text
        or len(text) > 80
        or any(ord(char) < 0x21 or ord(char) > 0x7E for char in text)
    ):
        raise RegistryError("live control host instance key is invalid")
    return text


def _host_metadata(
    hello: dict[str, Any],
    *,
    instance_key: str,
    lanes: frozenset[str],
) -> dict[str, Any]:
    machine_key = str(hello.get("machine_key") or "").strip().lower()
    if machine_key and (
        len(machine_key) != 64
        or any(char not in "0123456789abcdef" for char in machine_key)
    ):
        raise RegistryError("live control host machine key is invalid")

    inferred_kind = (
        # The phone's own lanes identify it first. A phone may also carry the
        # screen lane to mirror itself, and inferring "desktop" from that would
        # contradict its declared kind and refuse the registration outright.
        "phone" if lanes & PHONE_HOST_LANES else
        "desktop" if "screen" in lanes else
        "terminal" if "mo_session" in lanes else
        "other"
    )
    host_kind = str(hello.get("host_kind") or inferred_kind).strip().lower()
    if host_kind not in {"desktop", "terminal", "phone", "other"}:
        raise RegistryError("live control host kind is invalid")
    if host_kind != inferred_kind:
        raise RegistryError("live control host kind is inconsistent")

    if instance_key.startswith("terminal:"):
        instance_id = instance_key.removeprefix("terminal:")
    elif instance_key in {"desktop", "phone"}:
        instance_id = instance_key
    else:
        instance_id = hashlib.sha256(instance_key.encode("utf-8")).hexdigest()[:32]
    if (
        not instance_id
        or len(instance_id) > 64
        or any(not (char.isalnum() or char in "-_") for char in instance_id)
    ):
        raise RegistryError("live control host instance id is invalid")

    platform_family = str(hello.get("platform_family") or "other").strip().lower()
    if platform_family not in {"windows", "linux", "macos", "android", "other"}:
        raise RegistryError("live control host platform is invalid")
    architecture = str(hello.get("architecture") or "other").strip().lower()
    if architecture not in {"x86_64", "arm64", "x86", "other"}:
        raise RegistryError("live control host architecture is invalid")
    app_version = " ".join(str(hello.get("app_version") or "").split())
    if len(app_version) > 80 or any(ord(char) < 0x20 for char in app_version):
        raise RegistryError("live control host app version is invalid")
    raw_started = hello.get("started_at", 0.0)
    if isinstance(raw_started, bool):
        raise RegistryError("live control host start time is invalid")
    try:
        started_at = float(raw_started or 0.0)
    except (TypeError, ValueError):
        raise RegistryError("live control host start time is invalid") from None
    if not math.isfinite(started_at) or started_at < 0.0 or started_at > time.time() + 300:
        raise RegistryError("live control host start time is invalid")
    return {
        "machine_key": machine_key,
        "instance_id": instance_id,
        "host_kind": host_kind,
        "platform_family": platform_family,
        "architecture": architecture,
        "app_version": app_version,
        "started_at": started_at,
    }


def _stable_host_key(device_id: str, instance_key: str) -> str:
    """Opaque stable routing identity; live authority still uses ``host_id``."""
    return hashlib.sha256(
        f"mo-live-host-v1\0{device_id}\0{instance_key}".encode("utf-8")
    ).hexdigest()


def _presence_key(device_id: str) -> str:
    """Opaque per-device presence identity; carries no reversible device fact."""
    return hashlib.sha256(
        f"mo-device-presence-v1\0{device_id}".encode("utf-8")
    ).hexdigest()


def _device_presence_row(payload: Any) -> dict[str, Any]:
    """Validate one bounded presence report; anything outside the vocabulary fails closed."""
    if not isinstance(payload, dict) or set(payload) - {
        "power_state",
        "power_source",
        "physical_link",
    }:
        raise RegistryError("device presence report is invalid")
    power_state = str(payload.get("power_state") or "").strip().lower()
    power_source = str(payload.get("power_source") or "").strip().lower()
    physical_link = str(payload.get("physical_link") or "").strip().lower()
    if (
        power_state not in PRESENCE_POWER_STATES
        or power_source not in PRESENCE_POWER_SOURCES
        or physical_link not in PRESENCE_PHYSICAL_LINKS
    ):
        raise RegistryError("device presence report is invalid")
    return {
        "power_state": power_state,
        "power_source": power_source,
        "physical_link": physical_link,
        "reported_at": time.time(),
    }


def _lanes(value: Any) -> frozenset[str]:
    if not isinstance(value, list) or len(value) > len(LIVE_CONTROL_LANES):
        return frozenset()
    return frozenset(str(item or "").strip().lower() for item in value) & LIVE_CONTROL_LANES




def _file_source_host_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text.startswith("host-"):
        raise RegistryError("MO Files source is invalid")
    host_id = text[5:]
    if len(host_id) != 32 or any(char not in "0123456789abcdef" for char in host_id):
        raise RegistryError("MO Files source is invalid")
    return host_id


def _live_host_id(value: Any) -> str:
    text = str(value or "")
    if (
        len(text) != SESSION_ID_BYTES
        or any(char not in "0123456789abcdef" for char in text)
    ):
        raise RegistryError("live control host is invalid")
    return text


def desktop_terminal_actuators(
    status: Any,
    *,
    machine_key: str = "",
) -> list[dict[str, Any]]:
    """Return exact Desktop hosts that can launch MO terminals.

    Hub handoffs and workstation workspace panes consume the same capability
    filter so ``host_actions_v1`` does not acquire a second routing meaning.
    An optional opaque machine key narrows the result without using a hostname,
    path, label, or controller identity as authority.
    """
    wanted_machine = str(machine_key or "").strip().lower()
    if wanted_machine and (
        len(wanted_machine) != 64
        or any(char not in "0123456789abcdef" for char in wanted_machine)
    ):
        return []
    hosts = status.get("hosts") if isinstance(status, dict) else None
    rows: list[dict[str, Any]] = []
    for host in hosts if isinstance(hosts, list) else ():
        if not isinstance(host, dict):
            continue
        try:
            _live_host_id(host.get("host_id"))
        except RegistryError:
            continue
        host_key = str(host.get("host_key") or "")
        if (
            len(host_key) != 64
            or any(char not in "0123456789abcdef" for char in host_key)
            or host.get("host_kind") != "desktop"
            or HOST_ACTIONS_LANE not in set(host.get("lanes") or ())
            or (wanted_machine and host.get("machine_key") != wanted_machine)
        ):
            continue
        rows.append(host)
    return rows


def _wire_request_id(value: Any, label: str) -> str:
    text = str(value or "")
    if (
        len(text) != SESSION_ID_BYTES
        or any(char not in "0123456789abcdef" for char in text)
    ):
        raise RegistryError(f"{label} id is invalid")
    return text


def _host_action(operation: Any, arguments: Any) -> tuple[str, dict[str, Any]]:
    clean_operation = str(operation or "").strip().casefold()
    if clean_operation not in {
        "start_mo_terminal",
        "start_portable_mo_terminal",
        "stop_mo_terminal",
    }:
        raise RegistryError("Desktop host action is invalid")
    if not isinstance(arguments, dict):
        raise RegistryError("Desktop host action fields are invalid")
    if clean_operation == "start_mo_terminal":
        if arguments:
            raise RegistryError("Desktop host action fields are invalid")
        return clean_operation, {}
    if clean_operation == "stop_mo_terminal":
        _exact_keys(arguments, {"instance_id"})
        instance_id = str(arguments.get("instance_id") or "")
        if (
            not instance_id
            or len(instance_id) > 64
            or any(not (char.isalnum() or char in "-_") for char in instance_id)
        ):
            raise RegistryError("Desktop host action fields are invalid")
        return clean_operation, {"instance_id": instance_id}
    _exact_keys(arguments, {"conversation_id", "expected_revision"})
    conversation_id = str(arguments.get("conversation_id") or "").strip().lower()
    revision = arguments.get("expected_revision")
    if (
        not re.fullmatch(r"conv_[0-9a-f]{32}", conversation_id)
        or not _exact_int(revision)
        or int(revision) < 1
    ):
        raise RegistryError("Desktop host action fields are invalid")
    return clean_operation, {
        "conversation_id": conversation_id,
        "expected_revision": int(revision),
    }


def _host_action_response(operation: str, value: Any) -> dict[str, Any]:
    if operation not in {
        "start_mo_terminal",
        "start_portable_mo_terminal",
        "stop_mo_terminal",
    } or not isinstance(value, dict):
        raise RegistryError("Desktop host returned an invalid action result")
    _exact_keys(value, {"instance_id", "host_label"})
    instance_id = str(value.get("instance_id") or "")
    if (
        not instance_id
        or len(instance_id) > 64
        or any(not (char.isalnum() or char in "-_") for char in instance_id)
    ):
        raise RegistryError("Desktop host returned an invalid terminal")
    return {
        "instance_id": instance_id,
        "host_label": _bounded_label(
            value.get("host_label"), f"MO Terminal · {instance_id}"
        ),
    }


def _file_request(operation: Any, arguments: Any) -> tuple[str, dict[str, Any]]:
    clean_operation = str(operation or "").strip().casefold()
    if clean_operation not in {
        "locations",
        "list",
        "read_text",
        "preview",
        "write_text",
        "create_folder",
        "rename",
        "copy",
        "move",
        "delete",
        "trash",
        "restore",
        "send",
    }:
        raise RegistryError("MO Files operation is invalid")
    if not isinstance(arguments, dict) or len(arguments) > 8:
        raise RegistryError("MO Files operation fields are invalid")
    clean = dict(arguments)
    if clean_operation == "locations":
        _exact_keys(clean, set())
    elif clean_operation == "list":
        _exact_keys(
            clean,
            {"location_id", "path", "limit"},
            optional={"path", "limit"},
        )
        clean["location_id"] = _file_id(clean.get("location_id"), "location")
        clean["path"] = _file_relative(clean.get("path", ""), allow_root=True)
        limit = clean.get("limit", 150)
        if not _exact_int(limit) or not 1 <= limit <= 500:
            raise RegistryError("MO Files entry limit is invalid")
        clean["limit"] = limit
    elif clean_operation in {"read_text", "preview"}:
        _exact_keys(clean, {"location_id", "path"})
        clean["location_id"] = _file_id(clean.get("location_id"), "location")
        clean["path"] = _file_relative(clean.get("path"), allow_root=False)
    elif clean_operation == "write_text":
        _exact_keys(
            clean,
            {"location_id", "path", "text", "expected_sha256"},
        )
        clean["location_id"] = _file_id(clean.get("location_id"), "location")
        clean["path"] = _file_relative(clean.get("path"), allow_root=False)
        text = clean.get("text")
        if (
            not isinstance(text, str)
            or len(text.encode("utf-8")) > 384 * 1024
            or "\x00" in text
        ):
            raise RegistryError("MO Files text is outside bounds")
        clean["expected_sha256"] = _file_digest_value(
            clean.get("expected_sha256")
        )
    elif clean_operation == "create_folder":
        _exact_keys(clean, {"location_id", "parent_path", "name"}, optional={"parent_path"})
        clean["location_id"] = _file_id(clean.get("location_id"), "location")
        clean["parent_path"] = _file_relative(clean.get("parent_path", ""), allow_root=True)
        clean["name"] = _file_name(clean.get("name"))
    elif clean_operation == "rename":
        _exact_keys(
            clean,
            {"location_id", "path", "new_name", "expected_sha256"},
            optional={"expected_sha256"},
        )
        clean["location_id"] = _file_id(clean.get("location_id"), "location")
        clean["path"] = _file_relative(clean.get("path"), allow_root=False)
        clean["new_name"] = _file_name(clean.get("new_name"))
        clean["expected_sha256"] = _optional_file_digest(
            clean.get("expected_sha256")
        )
    elif clean_operation in {"copy", "move"}:
        _exact_keys(
            clean,
            {
                "source_location_id",
                "path",
                "target_location_id",
                "target_directory",
                "expected_sha256",
            },
            optional={"target_directory", "expected_sha256"},
        )
        clean["source_location_id"] = _file_id(
            clean.get("source_location_id"), "source location"
        )
        clean["target_location_id"] = _file_id(
            clean.get("target_location_id"), "target location"
        )
        clean["path"] = _file_relative(clean.get("path"), allow_root=False)
        clean["target_directory"] = _file_relative(
            clean.get("target_directory", ""), allow_root=True
        )
        clean["expected_sha256"] = _optional_file_digest(
            clean.get("expected_sha256")
        )
    elif clean_operation == "delete":
        _exact_keys(
            clean,
            {"location_id", "path", "expected_sha256"},
            optional={"expected_sha256"},
        )
        clean["location_id"] = _file_id(clean.get("location_id"), "location")
        clean["path"] = _file_relative(clean.get("path"), allow_root=False)
        clean["expected_sha256"] = _optional_file_digest(
            clean.get("expected_sha256")
        )
    elif clean_operation == "trash":
        _exact_keys(clean, {"limit"}, optional={"limit"})
        limit = clean.get("limit", 150)
        if not _exact_int(limit) or not 1 <= limit <= 500:
            raise RegistryError("MO Files Trash limit is invalid")
        clean["limit"] = limit
    elif clean_operation == "restore":
        _exact_keys(clean, {"trash_id"})
        clean["trash_id"] = _file_trash_id(clean.get("trash_id"))
    else:
        _exact_keys(clean, {"location_id", "path", "target_device_id"})
        clean["location_id"] = _file_id(clean.get("location_id"), "location")
        clean["path"] = _file_relative(clean.get("path"), allow_root=False)
        clean["target_device_id"] = _file_id(
            clean.get("target_device_id"), "transfer target"
        )
    try:
        encoded = json.dumps(
            clean, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise RegistryError("MO Files operation fields are invalid") from None
    if len(encoded) > MAX_FILE_WIRE_BYTES - 1024:
        raise RegistryError("MO Files operation is outside bounds")
    return clean_operation, clean


def _file_id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if (
        not 1 <= len(text) <= 100
        or not text.isascii()
        or any(not (char.isalnum() or char in "-_.:") for char in text)
    ):
        raise RegistryError(f"MO Files {label} is invalid")
    return text


def _file_name(value: Any) -> str:
    name = str(value or "").strip()
    if (
        not 1 <= len(name) <= 180
        or name in {".", ".."}
        or any(char in name for char in "/\\\x00")
        or name.startswith(".")
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in name)
    ):
        raise RegistryError("MO Files name is invalid")
    return name


def _file_relative(value: Any, *, allow_root: bool) -> str:
    if not isinstance(value, str):
        raise RegistryError("MO Files path is invalid")
    raw = value.strip().replace("\\", "/")
    if raw.startswith("/"):
        raise RegistryError("MO Files path is invalid")
    clean = raw
    if (
        len(clean) > 2048
        or ":" in clean
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in clean)
    ):
        raise RegistryError("MO Files path is invalid")
    parts = clean.split("/") if clean else []
    if not allow_root and not parts:
        raise RegistryError("MO Files path is required")
    if any(
        part in {"", ".", ".."}
        or len(part) > 180
        or part.startswith(".")
        for part in parts
    ):
        raise RegistryError("MO Files path is invalid")
    return "/".join(parts)


def _file_digest_value(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise RegistryError("MO Files revision is invalid")
    return text


def _optional_file_digest(value: Any) -> str:
    return "" if value in {None, ""} else _file_digest_value(value)


def _file_trash_id(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if len(text) != 32 or any(char not in "0123456789abcdef" for char in text):
        raise RegistryError("MO Files Trash item is invalid")
    return text


def _phone_request(operation: Any, arguments: Any) -> tuple[str, dict[str, Any]]:
    clean_operation = str(operation or "").strip().lower()
    if clean_operation not in {
        "observe", "click", "set_text", "scroll", "back", "home",
        "files_list", "files_analyze", "files_read", "files_delete",
        "capabilities", "system_status", "cache_report", "cache_trim",
        "packages_list", "package_action", "shell_execute",
    }:
        raise RegistryError("phone actuation operation is invalid")
    if not isinstance(arguments, dict) or len(arguments) > 12:
        raise RegistryError("phone actuation arguments are invalid")
    try:
        encoded = json.dumps(arguments, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError):
        raise RegistryError("phone actuation arguments are invalid") from None
    if len(encoded) > 12 * 1024:
        raise RegistryError("phone actuation arguments are outside bounds")
    clean = dict(arguments)
    if clean_operation == "observe":
        _exact_keys(clean, {"query", "max_nodes"}, optional={"query", "max_nodes"})
        query = clean.get("query", "")
        max_nodes = clean.get("max_nodes", 40)
        if not isinstance(query, str) or len(query) > 160 or any(ord(char) < 0x20 for char in query):
            raise RegistryError("phone observation query is invalid")
        if not _exact_int(max_nodes) or not 1 <= max_nodes <= 40:
            raise RegistryError("phone observation node limit is invalid")
    elif clean_operation == "click":
        _exact_keys(clean, {"target", "snapshot_id"})
        _phone_id(clean.get("target"), "target")
        _phone_id(clean.get("snapshot_id"), "snapshot")
    elif clean_operation == "set_text":
        _exact_keys(clean, {"target", "snapshot_id", "text"})
        _phone_id(clean.get("target"), "target")
        _phone_id(clean.get("snapshot_id"), "snapshot")
        text = clean.get("text")
        if not isinstance(text, str) or not 1 <= len(text) <= 2_000 or any(
            ord(char) < 0x20 and char not in "\t\r\n" for char in text
        ):
            raise RegistryError("phone actuation text is invalid")
    elif clean_operation == "scroll":
        _exact_keys(clean, {"target", "snapshot_id", "direction"}, optional={"target"})
        if "target" in clean:
            _phone_id(clean.get("target"), "target")
        _phone_id(clean.get("snapshot_id"), "snapshot")
        if clean.get("direction") not in {"up", "down", "left", "right"}:
            raise RegistryError("phone scroll direction is invalid")
    elif clean_operation in {"capabilities", "system_status"}:
        _exact_keys(clean, set())
    elif clean_operation == "cache_report":
        _exact_keys(clean, {"include_system", "max_entries"}, optional={"include_system", "max_entries"})
        include_system = clean.get("include_system", False)
        max_entries = clean.get("max_entries", 20)
        if type(include_system) is not bool:
            raise RegistryError("phone cache scope is invalid")
        if not _exact_int(max_entries) or not 1 <= max_entries <= 20:
            raise RegistryError("phone cache entry limit is invalid")
    elif clean_operation == "cache_trim":
        _exact_keys(clean, {"bytes_to_free"})
        value = clean.get("bytes_to_free")
        if not _exact_int(value) or not MIN_PHONE_CACHE_TRIM_BYTES <= value <= MAX_PHONE_CACHE_TRIM_BYTES:
            raise RegistryError("phone cache trim amount is outside bounds")
    elif clean_operation == "packages_list":
        _exact_keys(clean, {"scope", "max_entries"}, optional={"scope", "max_entries"})
        scope = clean.get("scope", "user")
        max_entries = clean.get("max_entries", 200)
        if scope not in {"user", "all"}:
            raise RegistryError("phone package scope is invalid")
        if not _exact_int(max_entries) or not 1 <= max_entries <= 200:
            raise RegistryError("phone package entry limit is invalid")
    elif clean_operation == "package_action":
        _exact_keys(clean, {"action", "package", "permission"}, optional={"permission"})
        action = clean.get("action")
        if action not in {
            "force_stop", "enable", "disable", "clear_data", "uninstall",
            "grant_permission", "revoke_permission",
        }:
            raise RegistryError("phone package action is invalid")
        clean["package"] = _phone_package_name(clean.get("package"), "package")
        permission_action = action in {"grant_permission", "revoke_permission"}
        if permission_action:
            clean["permission"] = _phone_package_name(clean.get("permission"), "permission")
        elif "permission" in clean:
            raise RegistryError("phone package permission is invalid for this action")
    elif clean_operation == "shell_execute":
        _exact_keys(clean, {"command"})
        command = clean.get("command")
        if (
            not isinstance(command, str)
            or not 1 <= len(command) <= 4 * 1024
            or any(ord(char) == 0 or (ord(char) < 0x20 and char not in "\t\r\n") for char in command)
        ):
            raise RegistryError("phone shell command is invalid")
    else:
        if clean_operation in {"back", "home"}:
            _exact_keys(clean, {"snapshot_id"}, optional={"snapshot_id"})
            if "snapshot_id" in clean:
                _phone_id(clean.get("snapshot_id"), "snapshot")
        elif clean_operation == "files_list":
            _exact_keys(clean, {"path", "max_entries"}, optional={"path", "max_entries"})
            clean["path"] = _phone_file_path(clean.get("path", ""), allow_root=True)
            max_entries = clean.get("max_entries", MAX_PHONE_FILE_ENTRIES)
            if not _exact_int(max_entries) or not 1 <= max_entries <= MAX_PHONE_FILE_ENTRIES:
                raise RegistryError("phone files entry limit is invalid")
        elif clean_operation == "files_read":
            _exact_keys(clean, {"path", "max_bytes"}, optional={"max_bytes"})
            clean["path"] = _phone_file_path(clean.get("path"), allow_root=False)
            max_bytes = clean.get("max_bytes", MAX_PHONE_FILE_READ_BYTES)
            if not _exact_int(max_bytes) or not 1 <= max_bytes <= MAX_PHONE_FILE_READ_BYTES:
                raise RegistryError("phone files read limit is invalid")
        elif clean_operation == "files_analyze":
            _exact_keys(
                clean,
                {"path", "max_documents", "max_depth"},
                optional={"path", "max_documents", "max_depth"},
            )
            clean["path"] = _phone_file_path(clean.get("path", ""), allow_root=True)
            max_documents = clean.get("max_documents", 200)
            max_depth = clean.get("max_depth", 8)
            if not _exact_int(max_documents) or not 1 <= max_documents <= 400:
                raise RegistryError("phone files analysis document limit is invalid")
            if not _exact_int(max_depth) or not 1 <= max_depth <= 12:
                raise RegistryError("phone files analysis depth is invalid")
        else:
            _exact_keys(clean, {"path"})
            clean["path"] = _phone_file_path(clean.get("path"), allow_root=False)
    return clean_operation, clean


def _phone_package_name(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise RegistryError(f"phone {label} is invalid")
    clean = value.strip()
    framework_package = label == "package" and clean == "android"
    if not 3 <= len(clean) <= 220 or (not framework_package and "." not in clean):
        raise RegistryError(f"phone {label} is invalid")
    if any(not (char.isalnum() or char in "._") for char in clean):
        raise RegistryError(f"phone {label} is invalid")
    if any(not part or not all(char.isalnum() or char == "_" for char in part) for part in clean.split(".")):
        raise RegistryError(f"phone {label} is invalid")
    return clean


def _phone_file_path(value: Any, *, allow_root: bool) -> str:
    if not isinstance(value, str):
        raise RegistryError("phone files path is invalid")
    clean = value.strip().replace("\\", "/").strip("/")
    if len(clean) > 512 or any(ord(char) < 0x20 for char in clean):
        raise RegistryError("phone files path is invalid")
    segments = [segment for segment in clean.split("/") if segment]
    if not allow_root and not segments:
        raise RegistryError("phone files path is required")
    if any(segment in {".", ".."} or len(segment) > 120 for segment in segments):
        raise RegistryError("phone files path is invalid")
    return "/".join(segments)


def _phone_id(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not 1 <= len(text) <= 96 or not text.isascii() or any(
        not (char.isalnum() or char in "-_.:") for char in text
    ):
        raise RegistryError(f"phone {label} is invalid")
    return text


def _file_response(operation: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError("MO Files host returned an invalid response")
    if operation == "locations":
        _exact_keys(value, {"locations"})
        locations = value.get("locations")
        if not isinstance(locations, list) or len(locations) > 100:
            raise RegistryError("MO Files host returned invalid locations")
        clean_locations = [_file_location_result(item) for item in locations]
        if len({item["location_id"] for item in clean_locations}) != len(
            clean_locations
        ):
            raise RegistryError("MO Files host returned duplicate locations")
        return {"locations": clean_locations}
    if operation == "list":
        _exact_keys(value, {"location", "path", "entries", "truncated"})
        entries = value.get("entries")
        if (
            not isinstance(entries, list)
            or len(entries) > 500
            or type(value.get("truncated")) is not bool
        ):
            raise RegistryError("MO Files host returned an invalid listing")
        clean_entries = [_file_entry_result(item) for item in entries]
        if len({item["path"] for item in clean_entries}) != len(clean_entries):
            raise RegistryError("MO Files host returned duplicate items")
        return {
            "location": _file_location_result(value.get("location")),
            "path": _file_relative(value.get("path"), allow_root=True),
            "entries": clean_entries,
            "truncated": value["truncated"],
        }
    if operation in {"read_text", "write_text"}:
        return _file_document_result(value)
    if operation == "preview":
        return _file_preview_result(value)
    if operation in {"create_folder", "rename", "copy", "move", "restore"}:
        _exact_keys(value, {"item"})
        return {"item": _file_entry_result(value.get("item"))}
    if operation == "delete":
        _exact_keys(value, {"deleted", "trash_id"})
        trash_id = str(value.get("trash_id") or "")
        if (
            value.get("deleted") is not True
            or len(trash_id) != 32
            or any(char not in "0123456789abcdef" for char in trash_id)
        ):
            raise RegistryError("MO Files host returned invalid deletion status")
        return {"deleted": True, "trash_id": trash_id}
    if operation == "trash":
        _exact_keys(value, {"items", "truncated"})
        items = value.get("items")
        if not isinstance(items, list) or len(items) > 500 or type(value.get("truncated")) is not bool:
            raise RegistryError("MO Files host returned invalid Trash status")
        clean_items = [_file_trash_result(item) for item in items]
        if len({item["trash_id"] for item in clean_items}) != len(clean_items):
            raise RegistryError("MO Files host returned duplicate Trash items")
        return {"items": clean_items, "truncated": value["truncated"]}
    _exact_keys(value, {"transfer"})
    transfer = value.get("transfer")
    if not isinstance(transfer, dict):
        raise RegistryError("MO Files host returned invalid transfer status")
    name = _file_name(transfer.get("name"))
    state = str(transfer.get("state") or "").strip().casefold()
    failure = " ".join(str(transfer.get("failure") or "").split())[:240]
    if (
        not 1 <= len(state) <= 40
        or not state.isascii()
        or any(not (char.isalnum() or char in "-_") for char in state)
    ):
        raise RegistryError("MO Files host returned invalid transfer status")
    return {"transfer": {"name": name, "state": state, "failure": failure}}


def _file_location_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError("MO Files host returned invalid location")
    _exact_keys(
        value,
        {"location_id", "label", "kind", "writable", "operations"},
    )
    location_id = _file_id(value.get("location_id"), "location")
    label = " ".join(str(value.get("label") or "").split())
    kind = str(value.get("kind") or "").strip().casefold()
    operations = value.get("operations")
    if (
        not valid_file_capability_descriptor(
            label=label,
            kind=kind,
            availability=value.get("writable"),
            operations=operations,
            allowed_kinds=FILE_LOCATION_KINDS,
        )
    ):
        raise RegistryError("MO Files host returned invalid location")
    return {
        "location_id": location_id,
        "label": label,
        "kind": kind,
        "writable": value["writable"],
        "operations": list(operations),
    }


def _file_preview_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError("MO Files host returned invalid preview")
    _exact_keys(
        value,
        {
            "location_id", "path", "name", "kind", "mime_type", "bytes",
            "sha256", "payload_base64",
        },
    )
    location_id = _file_id(value.get("location_id"), "location")
    path = _file_relative(value.get("path"), allow_root=False)
    name = _file_name(value.get("name"))
    kind = str(value.get("kind") or "").strip().casefold()
    mime_type = str(value.get("mime_type") or "").strip().casefold()
    byte_count = value.get("bytes")
    digest = _file_digest_value(value.get("sha256"))
    payload = value.get("payload_base64")
    expected_mime = {"image": "image/", "pdf": "application/pdf"}
    if (
        path.rsplit("/", 1)[-1] != name
        or kind not in expected_mime
        or not isinstance(byte_count, int)
        or isinstance(byte_count, bool)
        or not 1 <= byte_count <= 8 * 1024 * 1024
        or not isinstance(payload, str)
        or len(payload) > 11_184_812
        or (
            kind == "image" and not mime_type.startswith(expected_mime[kind])
        )
        or (kind == "pdf" and mime_type != expected_mime[kind])
    ):
        raise RegistryError("MO Files host returned invalid preview")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError, TypeError):
        raise RegistryError("MO Files host returned invalid preview") from None
    if len(raw) != byte_count or hashlib.sha256(raw).hexdigest() != digest:
        raise RegistryError("MO Files host returned invalid preview")
    return {
        "location_id": location_id,
        "path": path,
        "name": name,
        "kind": kind,
        "mime_type": mime_type,
        "bytes": byte_count,
        "sha256": digest,
        "payload_base64": payload,
    }


def _file_entry_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError("MO Files host returned invalid item")
    _exact_keys(
        value,
        {"name", "path", "kind", "bytes", "modified_at", "editable", "sha256"},
    )
    name = _file_name(value.get("name"))
    path = _file_relative(value.get("path"), allow_root=False)
    kind = value.get("kind")
    size = value.get("bytes")
    modified = value.get("modified_at")
    editable = value.get("editable")
    digest = value.get("sha256")
    if (
        kind not in {"file", "folder"}
        or (kind == "folder" and (size is not None or digest is not None))
        or (
            kind == "file"
            and (
                not _exact_int(size)
                or size < 0
                or (digest is None and editable is not False)
                or (
                    digest is not None
                    and (
                        not isinstance(digest, str)
                        or _file_digest_value(digest) != digest.casefold()
                    )
                )
            )
        )
        or not isinstance(modified, (int, float))
        or isinstance(modified, bool)
        or not math.isfinite(float(modified))
        or float(modified) < 0
        or type(editable) is not bool
        or path.rpartition("/")[2] != name
    ):
        raise RegistryError("MO Files host returned invalid item")
    return {
        "name": name,
        "path": path,
        "kind": kind,
        "bytes": size,
        "modified_at": float(modified),
        "editable": editable,
        "sha256": None if digest is None else digest.casefold(),
    }


def _file_trash_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError("MO Files host returned an invalid Trash item")
    _exact_keys(
        value,
        {"trash_id", "location_id", "location_label", "original_path", "name", "kind", "bytes", "deleted_at"},
    )
    name = _file_name(value.get("name"))
    original_path = _file_relative(value.get("original_path"), allow_root=False)
    label = " ".join(str(value.get("location_label") or "").split())
    kind = value.get("kind")
    size = value.get("bytes")
    deleted_at = value.get("deleted_at")
    if (
        original_path.rpartition("/")[2] != name
        or not 1 <= len(label) <= 80
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in label)
        or kind not in {"file", "folder"}
        or (kind == "folder" and size is not None)
        or (kind == "file" and (not _exact_int(size) or size < 0))
        or not isinstance(deleted_at, (int, float))
        or isinstance(deleted_at, bool)
        or not math.isfinite(float(deleted_at))
        or float(deleted_at) < 0
    ):
        raise RegistryError("MO Files host returned an invalid Trash item")
    return {
        "trash_id": _file_trash_id(value.get("trash_id")),
        "location_id": _file_id(value.get("location_id"), "location"),
        "location_label": label,
        "original_path": original_path,
        "name": name,
        "kind": kind,
        "bytes": size,
        "deleted_at": float(deleted_at),
    }


def _file_document_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryError("MO Files host returned invalid document")
    _exact_keys(
        value,
        {"location_id", "path", "text", "sha256", "bytes"},
    )
    text = value.get("text")
    size = value.get("bytes")
    if (
        not isinstance(text, str)
        or "\x00" in text
        or len(text.encode("utf-8")) > 384 * 1024
        or not _exact_int(size)
        or size != len(text.encode("utf-8"))
    ):
        raise RegistryError("MO Files host returned invalid document")
    digest = _file_digest_value(value.get("sha256"))
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != digest:
        raise RegistryError("MO Files host returned invalid document")
    return {
        "location_id": _file_id(value.get("location_id"), "location"),
        "path": _file_relative(value.get("path"), allow_root=False),
        "text": text,
        "sha256": digest,
        "bytes": size,
    }


def _wire_message(raw: str, allowed_types: frozenset[str]) -> dict[str, Any]:
    if not isinstance(raw, str):
        raise RegistryError("live control message is outside bounds")
    raw_bytes = len(raw.encode("utf-8"))
    if raw_bytes > MAX_FILE_WIRE_BYTES:
        raise RegistryError("live control message is outside bounds")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        raise RegistryError("live control message is invalid") from None
    if not isinstance(payload, dict) or str(payload.get("type") or "") not in allowed_types:
        raise RegistryError("live control message type is invalid")
    if payload.get("type") != "file_result" and raw_bytes > MAX_WIRE_TEXT_BYTES:
        raise RegistryError("live control message is outside bounds")
    if len(payload) > 16:
        raise RegistryError("live control message has too many fields")
    return payload


def _client_message(raw: str) -> dict[str, Any]:
    payload = _wire_message(raw, _CLIENT_MESSAGE_TYPES)
    kind = payload["type"]
    _require_sequence(payload)
    common = {"type", "sequence"}
    if kind in {"close", "ping"}:
        _exact_keys(payload, common)
    elif kind == "screen_visibility":
        _exact_keys(payload, common | {"visible"})
        if not isinstance(payload.get("visible"), bool):
            raise RegistryError("live control screen visibility is invalid")
    elif kind == "terminal_command":
        _exact_keys(payload, common | {"request_id", "action", "value"})
        _wire_request_id(payload.get("request_id"), "Terminal command")
        value = payload.get("value")
        if (
            payload.get("action") not in {"query", "select", "run"}
            or not isinstance(value, str)
            or not 1 <= len(value) <= 12_000
            or not value.strip().startswith("/")
            or any(ord(char) < 0x20 and char not in "\t\r\n" for char in value)
            or "\x7f" in value
        ):
            raise RegistryError("terminal command is invalid")
    elif kind == "text":
        _exact_keys(payload, common | {"value"})
        value = payload.get("value")
        if not isinstance(value, str) or not 1 <= len(value) <= 12_000 or not value.strip():
            raise RegistryError("live control text is invalid")
        if any(ord(char) < 0x20 and char not in "\t\r\n" for char in value) or "\x7f" in value:
            raise RegistryError("live control text is invalid")
    elif kind == "key":
        _exact_keys(payload, common | {"key", "action", "modifiers"}, optional={"action", "modifiers"})
        key = payload.get("key")
        if (
            not isinstance(key, str)
            or (
                key.lower() not in _LIVE_KEY_NAMES
                and (len(key) != 1 or not 0x21 <= ord(key) <= 0x7E)
            )
        ):
            raise RegistryError("live control key is invalid")
        if payload.get("action", "press") != "press":
            raise RegistryError("live control key is invalid")
        modifiers = payload.get("modifiers", [])
        if (
            not isinstance(modifiers, list)
            or len(modifiers) > 3
            or len(set(modifiers)) != len(modifiers)
            or any(
            not isinstance(item, str) or item not in {"alt", "ctrl", "shift"} for item in modifiers
            )
        ):
            raise RegistryError("live control key is invalid")
    elif kind == "pointer":
        _exact_keys(
            payload,
            common | {"action", "x", "y", "button", "delta"},
            optional={"button", "delta"},
        )
        if payload.get("action") not in {"move", "click", "double_click", "down", "up", "scroll"}:
            raise RegistryError("live control pointer is invalid")
        if not _ratio(payload.get("x")) or not _ratio(payload.get("y")):
            raise RegistryError("live control pointer is invalid")
        if payload.get("button", "left") not in {"left", "middle", "right"}:
            raise RegistryError("live control pointer is invalid")
        delta = payload.get("delta", 0)
        if not _exact_int(delta) or not -5 <= delta <= 5:
            raise RegistryError("live control pointer is invalid")
    elif kind == "viewport":
        _exact_keys(payload, common | {"width", "height"})
        if any(not _exact_int(payload.get(key)) or not 1 <= payload[key] <= 8_192 for key in ("width", "height")):
            raise RegistryError("live control viewport is invalid")
    return payload


def _resource_sample(value: Any) -> dict[str, Any]:
    """Validate numeric observations only; never accept host paths, PIDs or logs."""
    fields = {"state", "system_cpu_percent", "memory_percent", "memory_used_bytes",
              "memory_total_bytes", "process", "sample_age_seconds"}
    states = {"ready", "warming", "partial", "unavailable", "unsupported"}
    if (not isinstance(value, dict) or set(value) != fields
            or not isinstance(value.get("state"), str) or value["state"] not in states):
        raise RegistryError("resource observation is invalid")
    process = value.get("process")
    if (not isinstance(process, dict)
            or set(process) != {"state", "cpu_percent", "memory_bytes", "process_count"}
            or not isinstance(process.get("state"), str) or process["state"] not in states):
        raise RegistryError("resource process observation is invalid")

    def number(item: Any, maximum: float, *, integer: bool = False, nullable: bool = True) -> None:
        if item is None and nullable:
            return
        valid_type = type(item) is int if integer else type(item) in {int, float}
        if not valid_type or not 0 <= item <= maximum or not math.isfinite(item):
            raise RegistryError("resource measurement is invalid")

    for key in ("system_cpu_percent", "memory_percent"):
        number(value[key], 100)
    for key in ("memory_used_bytes", "memory_total_bytes"):
        number(value[key], 2 ** 63 - 1, integer=True)
    number(process["cpu_percent"], 100)
    number(process["memory_bytes"], 2 ** 63 - 1, integer=True)
    number(process["process_count"], 512, integer=True)
    number(value["sample_age_seconds"], 86_400, nullable=False)
    if (value["memory_used_bytes"] is not None and value["memory_total_bytes"] is not None
            and value["memory_used_bytes"] > value["memory_total_bytes"]):
        raise RegistryError("resource memory observation is invalid")
    if value["state"] == "ready" and any(value[key] is None for key in (
        "system_cpu_percent", "memory_percent", "memory_used_bytes", "memory_total_bytes",
    )):
        raise RegistryError("resource coverage is incomplete")
    if process["state"] == "ready" and any(process[key] is None for key in (
        "cpu_percent", "memory_bytes", "process_count",
    )):
        raise RegistryError("resource process coverage is incomplete")
    return {**value, "process": dict(process)}


def _host_message(raw: str) -> dict[str, Any]:
    payload = _wire_message(raw, _HOST_MESSAGE_TYPES | frozenset({"ping"}))
    kind = payload["type"]
    if kind == "resource_snapshot":
        _exact_keys(payload, {"type", "sequence", "sample"})
        if not _exact_int(payload.get("sequence")) or not 1 <= payload["sequence"] < 2 ** 53:
            raise RegistryError("resource sequence is invalid")
        payload["sample"] = _resource_sample(payload.get("sample"))
        return payload
    if kind == "ping":
        _exact_keys(payload, {"type"})
        return payload
    if kind == "file_result":
        common = {"type", "request_id", "sequence", "ok"}
        if payload.get("ok") is True:
            _exact_keys(payload, common | {"result"})
            if not isinstance(payload.get("result"), dict):
                raise RegistryError("MO Files result is invalid")
        elif payload.get("ok") is False:
            _exact_keys(payload, common | {"error"})
            if (
                not isinstance(payload.get("error"), str)
                or not 1 <= len(payload["error"]) <= 240
                or any(
                    ord(char) < 0x20 and char not in "\t\r\n"
                    for char in payload["error"]
                )
            ):
                raise RegistryError("MO Files error is invalid")
        else:
            raise RegistryError("MO Files result is invalid")
        request_id = payload.get("request_id")
        if (
            not isinstance(request_id, str)
            or len(request_id) != SESSION_ID_BYTES
            or any(char not in "0123456789abcdef" for char in request_id)
        ):
            raise RegistryError("MO Files request is invalid")
        _require_sequence(payload)
        return payload
    if kind == "host_action_result":
        common = {"type", "request_id", "sequence", "ok"}
        if payload.get("ok") is True:
            _exact_keys(payload, common | {"result"})
            if not isinstance(payload.get("result"), dict):
                raise RegistryError("Desktop host action result is invalid")
        elif payload.get("ok") is False:
            _exact_keys(payload, common | {"error"})
            if (
                not isinstance(payload.get("error"), str)
                or not 1 <= len(payload["error"]) <= 160
                or any(ord(char) < 0x20 for char in payload["error"])
            ):
                raise RegistryError("Desktop host action error is invalid")
        else:
            raise RegistryError("Desktop host action result is invalid")
        _wire_request_id(payload.get("request_id"), "Desktop host action")
        _require_sequence(payload)
        return payload
    if kind == "phone_result":
        common = {"type", "request_id", "sequence", "ok"}
        if payload.get("ok") is True:
            _exact_keys(payload, common | {"result"})
            if not isinstance(payload.get("result"), dict):
                raise RegistryError("phone actuation result is invalid")
        elif payload.get("ok") is False:
            _exact_keys(payload, common | {"error"})
            if not isinstance(payload.get("error"), str) or not 1 <= len(payload["error"]) <= 160:
                raise RegistryError("phone actuation error is invalid")
        else:
            raise RegistryError("phone actuation result is invalid")
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or len(request_id) != SESSION_ID_BYTES or not request_id.isascii():
            raise RegistryError("phone actuation request is invalid")
        _require_sequence(payload)
        return payload
    _require_sequence(payload)
    common = {"type", "session_id", "sequence"}
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or len(session_id) != SESSION_ID_BYTES or not session_id.isascii():
        raise RegistryError("live control host session is invalid")
    if kind == "session_ready":
        _exact_keys(payload, common | {"lane"})
        if payload.get("lane") not in SESSION_LANES:
            raise RegistryError("live control host lane is invalid")
    elif kind == "notice":
        _exact_keys(payload, common | {"message"})
        if not isinstance(payload.get("message"), str) or len(payload["message"]) > 120:
            raise RegistryError("live control host notice is invalid")
    elif kind == "terminal_command_result":
        _exact_keys(payload, common | {"request_id", "mode", "title", "text", "items"})
        _wire_request_id(payload.get("request_id"), "Terminal command")
        if payload.get("mode") not in {"menu", "report", "notice", "error", "input"}:
            raise RegistryError("terminal command result is invalid")
        for key, limit in (("title", 160), ("text", 12_000)):
            value = payload.get(key)
            if not isinstance(value, str) or len(value) > limit or "\x00" in value:
                raise RegistryError("terminal command result is invalid")
        items = payload.get("items")
        if not isinstance(items, list) or len(items) > 128:
            raise RegistryError("terminal command menu is invalid")
        for item in items:
            if (
                not isinstance(item, list) or len(item) != 4
                or any(not isinstance(part, str) or len(part) > 2048 or "\x00" in part for part in item)
                or not item[0].startswith("/")
                or item[3] not in {"command", "insert", "submenu", "run"}
            ):
                raise RegistryError("terminal command menu is invalid")
    elif kind == "screen_meta":
        fields = {"format", "frame_width", "frame_height", "control_width", "control_height"}
        _exact_keys(payload, common | fields)
        if payload.get("format") != "jpeg":
            raise RegistryError("live control screen metadata is invalid")
        limits = {"frame_width": 2_048, "frame_height": 2_048, "control_width": 16_384, "control_height": 16_384}
        if any(not _exact_int(payload.get(key)) or not 1 <= payload[key] <= limit for key, limit in limits.items()):
            raise RegistryError("live control screen metadata is invalid")
    elif kind in {"terminal_snapshot", "terminal_update"}:
        _exact_keys(
            payload,
            common | {"lines", "busy", "activity", "attention"},
            optional={"attention"},
        )
        lines = payload.get("lines")
        if not isinstance(lines, list) or len(lines) > 160 or any(
            not isinstance(line, str) or len(line) > 2_000 for line in lines
        ):
            raise RegistryError("live control terminal snapshot is invalid")
        if sum(len(line.encode("utf-8")) for line in lines) > 12 * 1024:
            raise RegistryError("live control terminal snapshot is invalid")
        if (
            not isinstance(payload.get("busy"), bool)
            or ("attention" in payload and not isinstance(payload["attention"], bool))
            or not isinstance(payload.get("activity"), str)
            or len(payload["activity"]) > 160
        ):
            raise RegistryError("live control terminal snapshot is invalid")
    elif kind == "session_closed":
        _exact_keys(payload, common | {"reason"}, optional={"reason"})
        if not isinstance(payload.get("reason", ""), str) or len(payload.get("reason", "")) > 48:
            raise RegistryError("live control host close is invalid")
    elif kind == "pong":
        _exact_keys(payload, common)
    return payload


def _require_sequence(payload: dict[str, Any]) -> None:
    sequence = payload.get("sequence")
    if not _exact_int(sequence) or not 1 <= sequence <= 2**63 - 1:
        raise RegistryError("live control sequence is invalid")


def _exact_keys(payload: dict[str, Any], allowed: set[str], *, optional: set[str] | None = None) -> None:
    optional = optional or set()
    if set(payload) - allowed or (allowed - optional) - set(payload):
        raise RegistryError("live control message fields are invalid")


def _exact_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _ratio(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0.0 <= value <= 1.0


async def _safe_close(websocket: Any, code: int) -> None:
    if websocket is None:
        return
    try:
        await websocket.close(code=code)
    except Exception:
        pass


def _fail_phone_requests(requests: list[_PhoneRequest], message: str) -> None:
    for request in requests:
        if not request.future.done():
            request.future.set_exception(RegistryError(message))


def _fail_file_requests(requests: list[_FileRequest], message: str) -> None:
    for request in requests:
        if not request.future.done():
            request.future.set_exception(RegistryError(message))


def _fail_host_action_requests(
    requests: list[_HostActionRequest], message: str
) -> None:
    for request in requests:
        if not request.future.done():
            request.future.set_exception(RegistryError(message))
