"""Outbound client for a native MO Live Control host.

This module stays off normal import paths. The WebSocket package, credentials,
and lane handlers are loaded only after an explicitly enabled host starts.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import queue
import re
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from core.state.paths import resolve_state_path

from .client import ContinuityClient, _credential_scoped_config, credential_path
from .live_control import (
    HOST_ACTIONS_LANE,
    KILL_SWITCH_PATH,
    PRESENCE_PHYSICAL_LINKS,
    PRESENCE_POWER_SOURCES,
    PRESENCE_POWER_STATES,
    WORKSTATION_FILES_LANE,
)


LIVE_HOST_CREDENTIAL_PATH = "credentials/everywhere-live-host.json"
MAX_HOST_MESSAGE_BYTES = 16 * 1024
MAX_FILE_RESULT_BYTES = 512 * 1024
MAX_FRAME_BYTES = 512 * 1024
AUTH_REFRESH_RETRY_SECONDS = 60.0


def _host_action_payload(
    operation: Any, arguments: Any
) -> tuple[str, dict[str, Any]] | None:
    clean_operation = str(operation or "").strip().lower()
    if clean_operation == "start_mo_terminal":
        return (clean_operation, {}) if arguments == {} else None
    if clean_operation == "stop_mo_terminal":
        if not isinstance(arguments, dict) or set(arguments) != {"instance_id"}:
            return None
        instance_id = str(arguments.get("instance_id") or "")
        if (
            not instance_id
            or len(instance_id) > 64
            or any(not (char.isalnum() or char in "-_") for char in instance_id)
        ):
            return None
        return clean_operation, {"instance_id": instance_id}
    if clean_operation != "start_portable_mo_terminal" or not isinstance(arguments, dict):
        return None
    if set(arguments) != {"conversation_id", "expected_revision"}:
        return None
    conversation_id = str(arguments.get("conversation_id") or "").strip().lower()
    revision = arguments.get("expected_revision")
    if (
        not re.fullmatch(r"conv_[0-9a-f]{32}", conversation_id)
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
    ):
        return None
    return clean_operation, {
        "conversation_id": conversation_id,
        "expected_revision": revision,
    }


# The presence vocabulary is owned by live_control; this host validates with
# the same sets instead of private copies.
_PRESENCE_POWER_STATES = PRESENCE_POWER_STATES
_PRESENCE_POWER_SOURCES = PRESENCE_POWER_SOURCES
_PRESENCE_PHYSICAL_LINKS = PRESENCE_PHYSICAL_LINKS


def _presence_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Validate one pushed device-presence fact; anything off-vocabulary is dropped."""
    presence_key = str(payload.get("presence_key") or "")
    power_state = str(payload.get("power_state") or "")
    power_source = str(payload.get("power_source") or "")
    physical_link = str(payload.get("physical_link") or "")
    reported_at = payload.get("reported_at")
    if (
        not re.fullmatch(r"[0-9a-f]{64}", presence_key)
        or power_state not in _PRESENCE_POWER_STATES
        or power_source not in _PRESENCE_POWER_SOURCES
        or physical_link not in _PRESENCE_PHYSICAL_LINKS
        or isinstance(reported_at, bool)
        or not isinstance(reported_at, (int, float))
        or not math.isfinite(float(reported_at))
        or not float(reported_at) > 0
    ):
        return None
    return {
        "presence_key": presence_key,
        "power_state": power_state,
        "power_source": power_source,
        "physical_link": physical_link,
        "reported_at": float(reported_at),
    }


def live_host_client_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a shallow structural copy pointed at the dedicated host token."""
    return _credential_scoped_config(config, LIVE_HOST_CREDENTIAL_PATH)


def live_host_credentials_present(config: dict[str, Any] | None = None) -> bool:
    return credential_path(live_host_client_config(config)).is_file()


class LiveControlHost:
    """One reconnecting host socket multiplexed over bounded lane handlers."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        label: str,
        handlers: dict[str, Any],
        instance_key: str = "",
        on_status: Callable[[str], None] | None = None,
        on_presence: Callable[[dict[str, Any]], None] | None = None,
        connector: Callable[[str, str], Any] | None = None,
    ):
        self.config = config
        self.label = " ".join(str(label or "MO host").split())[:80] or "MO host"
        # Third-party/test callers created before the explicit instance-key
        # protocol may omit the key; production Desktop and TUI callers always
        # provide their own.
        # COMPAT(live-host-legacy-identity): replaced-by explicit instance keys; remove-when supported pre-key hosts have aged out
        self.instance_key = str(instance_key or "").strip() or (
            "legacy:" + hashlib.sha256(self.label.encode("utf-8")).hexdigest()
        )
        if not self.instance_key or len(self.instance_key) > 80 or any(
            ord(char) < 0x21 or ord(char) > 0x7E for char in self.instance_key
        ):
            raise ValueError("live control host instance key is invalid")
        self.handlers = {
            key: value
            for key, value in handlers.items()
            if key in {"mo_session", "screen", WORKSTATION_FILES_LANE, HOST_ACTIONS_LANE}
        }
        self.lanes = frozenset(self.handlers)
        self.machine_key = local_machine_key(config)
        self.host_kind = _host_kind(self.handlers)
        self.platform_family = _platform_family()
        self.architecture = _architecture()
        self.started_at = time.time()
        try:
            from core.update.version import current_version

            self.app_version = current_version()[:80]
        except Exception:
            self.app_version = "MO v1.0"
        self.on_status = on_status
        self.on_presence = on_presence
        self._connector = connector or _connect_websocket
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._socket: Any = None
        self._lifecycle_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._event_lock = threading.RLock()
        self._status_lock = threading.Lock()
        self._last_status = ""
        self._sessions: dict[str, str] = {}
        self._sequences: dict[str, int] = {}
        self._input_queue: queue.Queue = queue.Queue(maxsize=256)
        self._input_thread: threading.Thread | None = None
        self._request_queue: queue.Queue = queue.Queue(maxsize=8)
        self._request_thread: threading.Thread | None = None
        self._request_sequence = 0
        self._resource_sampler: Any = None
        self._resource_pending = False
        self._handshake_confirmed = False
        self._next_auth_refresh_at = 0.0

    @property
    def enabled(self) -> bool:
        block = self.config.get("consistent_everywhere")
        block = block if isinstance(block, dict) else {}
        live = block.get("live_control") if isinstance(block.get("live_control"), dict) else {}
        host = live.get("host") if isinstance(live.get("host"), dict) else {}
        return block.get("enabled") is True and live.get("enabled") is True and host.get("enabled") is True

    def start(self) -> bool:
        with self._lifecycle_lock:
            workers = (self._thread, self._input_thread, self._request_thread)
            if (
                not self.enabled
                or not self.lanes
                or any(worker is not None and worker.is_alive() for worker in workers)
            ):
                return False
            # A prior stop can leave queued actuation that belonged to a closed
            # lease. A restarted host must begin with a clean generation instead
            # of applying stale input to the next session.
            _drain_queue(self._input_queue)
            _drain_queue(self._request_queue)
            self._stop.clear()
            self._input_thread = threading.Thread(
                target=self._input_worker, name="mo-live-control-input", daemon=True
            )
            self._input_thread.start()
            if WORKSTATION_FILES_LANE in self.handlers or HOST_ACTIONS_LANE in self.handlers:
                self._request_thread = threading.Thread(
                    target=self._request_worker,
                    name="mo-live-control-requests",
                    daemon=True,
                )
                self._request_thread.start()
            self._thread = threading.Thread(target=self._run, name="mo-live-control-host", daemon=True)
            self._thread.start()
            return True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        socket = self._socket
        if socket is not None:
            try:
                socket.close()
            except Exception:
                pass
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, timeout))
        # Workers poll the shared stop event. Sentinels used to survive a stop
        # when a worker had already exited, immediately killing the next worker
        # and making all remote keyboard/pointer input disappear after restart.
        input_thread = self._input_thread
        if input_thread and input_thread.is_alive() and input_thread is not threading.current_thread():
            input_thread.join(timeout=max(0.0, timeout))
        self._input_thread = None
        request_thread = self._request_thread
        if (
            request_thread
            and request_thread.is_alive()
            and request_thread is not threading.current_thread()
        ):
            request_thread.join(timeout=max(0.0, timeout))
        self._request_thread = None
        self._thread = None
        self._socket = None
        _drain_queue(self._input_queue)
        _drain_queue(self._request_queue)
        self._resource_pending = False
        self._resource_sampler = None
        self._close_all()
        self._status("stopped")

    def send_event(self, session_id: str, payload: dict[str, Any]) -> bool:
        # Numbering and transmission are one atomic step. Two threads emit per
        # session on both lanes -- a snapshot/capture thread and the socket
        # thread's notices -- and the hub rejects any sequence it has already
        # seen. If the thread holding N loses the wire to N+1, the hub reads a
        # backwards sequence and drops the entire host connection, taking every
        # other session on this host with it.
        with self._event_lock:
            body = dict(payload)
            body["session_id"] = session_id
            with self._state_lock:
                sequence = self._sequences.get(session_id, 0) + 1
                self._sequences[session_id] = sequence
            body["sequence"] = sequence
            try:
                raw = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
            except (TypeError, ValueError):
                return False
            if len(raw.encode("utf-8")) > MAX_HOST_MESSAGE_BYTES:
                return False
            return self._send(raw)

    def send_frame(self, session_id: str, frame: bytes) -> bool:
        if len(session_id) != 32 or not frame or len(frame) > MAX_FRAME_BYTES:
            return False
        try:
            prefix = session_id.encode("ascii")
        except UnicodeEncodeError:
            return False
        return self._send(prefix + bytes(frame))

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            if self._locally_disabled():
                # Connecting here would register and advertise every lane for
                # the second before the loop notices, so the phone sees a host
                # it can lease but never reach. Waiting also lets the gate
                # reopen without restarting the process.
                self._status("disabled")
                if self._stop.wait(2.0):
                    break
                continue
            if not live_host_credentials_present(self.config):
                self._status("unpaired")
                if self._stop.wait(2.0):
                    break
                continue
            try:
                client = ContinuityClient(live_host_client_config(self.config), timeout=10.0)
                socket = self._connect_authorized(client)
                self._socket = socket
                self._send_required_json({
                    "type": "hello",
                    "instance_key": self.instance_key,
                    "machine_key": self.machine_key,
                    "host_kind": self.host_kind,
                    "platform_family": self.platform_family,
                    "architecture": self.architecture,
                    "app_version": self.app_version,
                    "started_at": self.started_at,
                    "label": self.label,
                    "lanes": sorted(self.lanes),
                    "command_menu": getattr(self.handlers.get("mo_session"), "command_menu", False) is True,
                    "resources_v1": True,
                })
                self._send_required_json({"type": "ping"})
                self._status("securing")
                self._handshake_confirmed = False
                last_ping = time.monotonic()
                while not self._stop.is_set():
                    if self._locally_disabled():
                        raise PermissionError("live control locally disabled")
                    if time.monotonic() - last_ping >= 10.0:
                        self._send_required_json({"type": "ping"})
                        last_ping = time.monotonic()
                    try:
                        value = socket.recv()
                    except TimeoutError:
                        continue
                    except Exception as exc:
                        if _is_timeout(exc):
                            continue
                        raise
                    if value in {None, ""}:
                        raise ConnectionError("live control host socket closed")
                    if isinstance(value, bytes):
                        continue
                    self._receive(str(value))
                    if self._handshake_confirmed:
                        # The hub accepted this registration. Every rejection
                        # (kill switch, host limit, bad label) arrives after the
                        # socket is up, so resetting on connect alone turned a
                        # refusal into a permanent one-per-second retry.
                        backoff = 1.0
            except Exception as exc:
                self._status("unavailable:" + type(exc).__name__)
            finally:
                socket = self._socket
                self._socket = None
                if socket is not None:
                    try:
                        socket.close()
                    except Exception:
                        pass
                self._close_all()
            if self._stop.wait(backoff):
                break
            backoff = min(30.0, backoff * 2.0)

    def _connect_authorized(self, client: ContinuityClient) -> Any:
        hub, token = client.websocket_authority()
        url = _websocket_url(hub, "/api/mo/live/host")
        try:
            socket = self._connector(url, token)
        except Exception as exc:
            now = time.monotonic()
            if not _is_auth_rejection(exc) or now < self._next_auth_refresh_at:
                raise
            self._next_auth_refresh_at = now + AUTH_REFRESH_RETRY_SECONDS
            hub, token = client.refresh_websocket_authority()
            socket = self._connector(_websocket_url(hub, "/api/mo/live/host"), token)
        self._next_auth_refresh_at = 0.0
        return socket

    def _receive(self, raw: str) -> None:
        if self._locally_disabled():
            return
        raw_bytes = len(raw.encode("utf-8"))
        if raw_bytes > MAX_FILE_RESULT_BYTES:
            return
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(payload, dict):
            return
        kind = str(payload.get("type") or "")
        if kind != "file_request" and raw_bytes > MAX_HOST_MESSAGE_BYTES:
            return
        session_id = str(payload.get("session_id") or "")
        if kind == "pong":
            self._handshake_confirmed = True
            self._status("connected")
            return
        if kind == "resource_request":
            if set(payload) != {"type"}:
                return
            with self._state_lock:
                if self._resource_pending:
                    return
                try:
                    self._request_queue.put_nowait(("resources", None, "", "", {}))
                except queue.Full:
                    return
                self._resource_pending = True
            return
        if kind == "file_request":
            request_id = str(payload.get("request_id") or "")
            operation = str(payload.get("operation") or "")
            arguments = payload.get("arguments")
            handler = self.handlers.get(WORKSTATION_FILES_LANE)
            if (
                len(request_id) != 32
                or not operation
                or len(operation) > 32
                or not isinstance(arguments, dict)
                or handler is None
            ):
                return
            try:
                self._request_queue.put_nowait(
                    ("file", handler, request_id, operation, arguments)
                )
            except queue.Full:
                self._send_file_result(
                    request_id,
                    ok=False,
                    error="MO Files host is busy",
                )
            return
        if kind == "host_action_request":
            request_id = str(payload.get("request_id") or "")
            action = _host_action_payload(
                payload.get("operation"), payload.get("arguments")
            )
            handler = self.handlers.get(HOST_ACTIONS_LANE)
            if (
                len(request_id) != 32
                or any(char not in "0123456789abcdef" for char in request_id)
                or action is None
                or handler is None
            ):
                return
            operation, arguments = action
            try:
                self._request_queue.put_nowait(
                    ("host_action", handler, request_id, operation, arguments)
                )
            except queue.Full:
                self._send_host_action_result(
                    request_id,
                    ok=False,
                    error="MO Desktop is busy",
                )
            return
        if kind == "device_presence":
            # Bounded phone power/attachment fact pushed by the hub so the
            # Desktop cube can mirror a verified charging state. Consumers
            # apply their own expiry; a malformed payload is simply dropped.
            row = _presence_payload(payload)
            if row is None or self.on_presence is None:
                return
            try:
                self.on_presence(row)
            except Exception:
                pass
            return
        if kind == "session_open":
            lane = str(payload.get("lane") or "")
            handler = self.handlers.get(lane)
            with self._state_lock:
                occupied = bool(self._sessions)
            if len(session_id) != 32 or handler is None or occupied:
                self.send_event(session_id, {"type": "session_closed", "sequence": 1, "reason": "unavailable"})
                # The refusal numbered a session this host never opened, so no
                # close path would ever reclaim that counter.
                with self._state_lock:
                    self._sequences.pop(session_id, None)
                return
            with self._state_lock:
                self._sessions[session_id] = lane
                self._sequences[session_id] = 0
            self.send_event(session_id, {"type": "session_ready", "lane": lane})
            handler.open(session_id, self.send_event, self.send_frame)
            return
        with self._state_lock:
            lane = self._sessions.get(session_id)
        handler = self.handlers.get(lane or "")
        if handler is None:
            return
        if kind == "session_close":
            self._close_session(session_id)
            return
        if kind in {"client_connected", "pointer", "key", "text", "viewport", "terminal_command", "screen_visibility"}:
            # Actuation is slow -- a pointer move alone costs a pyautogui
            # pause -- and running it here would block the socket thread that
            # also sends the 10s ping and reads session_close. A starved ping
            # gets the host reaped as stale mid-drag, and a late close keeps
            # the screen captured after the operator released the lease.
            try:
                self._input_queue.put_nowait((handler, session_id, payload))
            except queue.Full:
                if kind == "screen_visibility":
                    # Visibility must not be silently lost: continuing to
                    # capture a hidden screen is worse than ending an
                    # overloaded lease through its existing close path.
                    self.send_event(session_id, {"type": "session_closed", "reason": "unavailable"})
                    self._close_session(session_id)
                    return
                # Input is a live stream, not a log: a backlog this deep is
                # already stale, so drop rather than grow without bound.
                pass

    def _input_worker(self) -> None:
        """Apply lane input off the socket thread, in arrival order."""
        while not self._stop.is_set():
            try:
                item = self._input_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if item is None:
                break
            handler, session_id, payload = item
            try:
                handler.receive(session_id, payload)
            except Exception:
                pass

    def _request_worker(self) -> None:
        """Execute bounded host calls without starving the heartbeat."""
        while not self._stop.is_set():
            try:
                item = self._request_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if item is None:
                break
            request_kind, handler, request_id, operation, arguments = item
            if request_kind == "resources":
                try:
                    self._send_resources()
                except Exception:
                    # A failed observation must not kill the file/action worker.
                    pass
                finally:
                    with self._state_lock:
                        self._resource_pending = False
                continue
            try:
                if request_kind == "host_action":
                    result = handler.execute(
                        operation,
                        arguments,
                        idempotency_key=request_id,
                    )
                else:
                    result = handler.execute(operation, arguments)
                if not isinstance(result, dict):
                    raise ValueError("invalid live host response")
            except Exception as exc:
                formatter = getattr(handler, "error_message", None)
                message = (
                    formatter(exc)
                    if callable(formatter)
                    else "MO host operation failed"
                )
                if request_kind == "host_action":
                    self._send_host_action_result(
                        request_id, ok=False, error=message
                    )
                else:
                    self._send_file_result(request_id, ok=False, error=message)
            else:
                if request_kind == "host_action":
                    self._send_host_action_result(
                        request_id, ok=True, result=result
                    )
                else:
                    self._send_file_result(request_id, ok=True, result=result)

    def _send_resources(self) -> None:
        """Sample this process tree on the existing request worker, never the socket loop."""
        from dataclasses import asdict
        from core.runtime.resources import ResourceSampler

        if self._resource_sampler is None:
            self._resource_sampler = ResourceSampler()
        roots = {"main": os.getpid()}
        snapshot = self._resource_sampler.sample(roots)
        if snapshot.source not in {"unavailable", "unsupported"}:
            # Each explicit request measures a fresh interval, even after a long
            # idle gap. Nothing samples again until another request arrives.
            if self._stop.wait(1.0) or self._locally_disabled():
                return
            snapshot = self._resource_sampler.sample(roots)
        sample = {
            "state": snapshot.state,
            "system_cpu_percent": snapshot.system_cpu_percent,
            "memory_percent": snapshot.memory_percent,
            "memory_used_bytes": snapshot.memory_used_bytes,
            "memory_total_bytes": snapshot.memory_total_bytes,
            "process": asdict(snapshot.trees["main"]),
            "sample_age_seconds": max(0.0, time.monotonic() - snapshot.sampled_at),
        }
        with self._event_lock:
            self._request_sequence += 1
            self._send_json({"type": "resource_snapshot", "sequence": self._request_sequence, "sample": sample})

    def _send_file_result(
        self,
        request_id: str,
        *,
        ok: bool,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> bool:
        return self._send_result_envelope(
            "file_result",
            request_id,
            ok=ok,
            result=result,
            error=error,
            error_chars=240,
            max_bytes=MAX_FILE_RESULT_BYTES,
            oversize_error="MO Files response is too large",
        )

    def _send_host_action_result(
        self,
        request_id: str,
        *,
        ok: bool,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> bool:
        return self._send_result_envelope(
            "host_action_result",
            request_id,
            ok=ok,
            result=result,
            error=error,
            error_chars=160,
            max_bytes=MAX_HOST_MESSAGE_BYTES,
        )

    def _send_result_envelope(
        self,
        message_type: str,
        request_id: str,
        *,
        ok: bool,
        result: dict[str, Any] | None,
        error: str,
        error_chars: int,
        max_bytes: int,
        oversize_error: str = "",
    ) -> bool:
        """Serialize one bounded live-host result under the shared sequence lock."""
        with self._event_lock:
            self._request_sequence += 1
            payload = {
                "type": message_type,
                "request_id": request_id,
                "sequence": self._request_sequence,
                "ok": bool(ok),
            }
            if ok:
                payload["result"] = result or {}
            else:
                payload["error"] = " ".join(str(error).split())[:error_chars]
            try:
                raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            except (TypeError, ValueError):
                return False
            if len(raw.encode("utf-8")) > max_bytes:
                if not oversize_error:
                    return False
                payload["ok"] = False
                payload.pop("result", None)
                payload["error"] = oversize_error
                raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            return self._send(raw)

    def _close_session(self, session_id: str) -> None:
        with self._state_lock:
            lane = self._sessions.pop(session_id, "")
            self._sequences.pop(session_id, None)
        handler = self.handlers.get(lane)
        if handler is not None:
            try:
                handler.close(session_id)
            except Exception:
                pass

    def _close_all(self) -> None:
        with self._state_lock:
            session_ids = list(self._sessions)
        for session_id in session_ids:
            self._close_session(session_id)

    def _send_json(self, payload: dict[str, Any]) -> bool:
        try:
            raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        except (TypeError, ValueError):
            return False
        return len(raw.encode("utf-8")) <= MAX_HOST_MESSAGE_BYTES and self._send(raw)

    def _send_required_json(self, payload: dict[str, Any]) -> None:
        """Reconnect when a required host heartbeat cannot reach the socket."""
        if not self._send_json(payload):
            raise ConnectionError("live control host send failed")

    def _send(self, value: str | bytes) -> bool:
        socket = self._socket
        if socket is None or self._stop.is_set():
            return False
        try:
            with self._send_lock:
                socket.send(value)
            return True
        except Exception:
            return False

    def _status(self, value: str) -> None:
        status = str(value or "")[:80]
        with self._status_lock:
            if status == self._last_status:
                return
            self._last_status = status
        if self.on_status is None:
            return
        try:
            self.on_status(status)
        except Exception:
            pass

    def _locally_disabled(self) -> bool:
        return Path(resolve_state_path(KILL_SWITCH_PATH, self.config)).is_file()


def local_machine_key(config: dict[str, Any]) -> str:
    """Return a cross-process opaque key without exposing local machine facts."""
    from core.state.device import device_identity

    device_id = str(device_identity(config).get("device_id") or "")
    return hashlib.sha256(
        f"mo-live-machine-v1\0{device_id}".encode("utf-8")
    ).hexdigest()


def _host_kind(handlers: dict[str, Any]) -> str:
    if "screen" in handlers:
        return "desktop"
    if "mo_session" in handlers:
        return "terminal"
    if "phone_semantic_v1" in handlers:
        return "phone"
    return "other"


def _platform_family() -> str:
    value = platform.system().strip().lower()
    return {
        "windows": "windows",
        "linux": "linux",
        "darwin": "macos",
    }.get(value, "other")


def _architecture() -> str:
    value = platform.machine().strip().lower()
    if value in {"amd64", "x86_64"}:
        return "x86_64"
    if value in {"arm64", "aarch64"}:
        return "arm64"
    if value in {"x86", "i386", "i686"}:
        return "x86"
    return "other"


def _drain_queue(values: queue.Queue) -> None:
    while True:
        try:
            values.get_nowait()
        except queue.Empty:
            return


def _websocket_url(hub: str, path: str) -> str:
    parsed = urllib.parse.urlsplit(str(hub or ""))
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urllib.parse.urlunsplit((scheme, parsed.netloc, "/" + path.lstrip("/"), "", ""))


def connect_websocket(url: str, token: str) -> Any:
    """Open one outbound MO WebSocket.

    This module owns the only outbound WebSocket connector, so other surfaces
    call this rather than repeating the optional-import dance. Import it lazily:
    the package is loaded only when a connection is actually opened.
    """
    return _connect_websocket(url, token)


def _connect_websocket(url: str, token: str) -> Any:
    try:
        import websocket

        socket = websocket.create_connection(
            url,
            subprotocols=[f"mo-access.{token}"],
            timeout=2.0,
            enable_multithread=True,
        )

        class _WebsocketClientAdapter:
            def send(self, value: str | bytes) -> None:
                if isinstance(value, bytes):
                    socket.send_binary(value)
                else:
                    socket.send(value)

            def recv(self) -> str | bytes:
                return socket.recv()

            def close(self) -> None:
                socket.close()

        return _WebsocketClientAdapter()
    except ImportError:
        from websockets.sync.client import connect

        socket = connect(
            url,
            subprotocols=[f"mo-access.{token}"],
            open_timeout=10.0,
            close_timeout=2.0,
        )

        class _WebsocketsAdapter:
            def send(self, value: str | bytes) -> None:
                socket.send(value)

            def recv(self) -> str | bytes:
                return socket.recv(timeout=2.0)

            def close(self) -> None:
                socket.close()

        return _WebsocketsAdapter()


def _is_timeout(exc: Exception) -> bool:
    return type(exc).__name__ in {"TimeoutError", "WebSocketTimeoutException"}


def _websocket_status_code(exc: Exception) -> int:
    value = getattr(exc, "status_code", None)
    if value is None:
        value = getattr(exc, "status", None)
    if value is None:
        response = getattr(exc, "response", None)
        value = getattr(response, "status_code", None) or getattr(response, "status", None)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _is_auth_rejection(exc: Exception) -> bool:
    return _websocket_status_code(exc) in {401, 403}
