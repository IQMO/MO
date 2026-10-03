"""Exact Chrome-tab bridge for MO browser computer-use.

Chrome owns the browser and starts this module as a native-messaging host.  The
host exposes one authenticated loopback endpoint inside MO's private runtime
state; :class:`BrowserBridgeClient` is the sole Python transport used by
``tools.browser``. The extension attaches only to the selected ordinary tab.

The module also owns the explicit install/status/uninstall lifecycle so native
host registration, generated launchers, and rollback cannot drift apart.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import queue
import secrets
import shlex
import socket
import select
import socketserver
import struct
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlparse

from core.runtime import lock as runtime_lock
from core.state.paths import resolve_state_path
from core.utils.atomic_write import atomic_write_json, atomic_write_text


HOST_NAME = "com.mo_agent.connected_tab"
PROTOCOL_VERSION = 1
EXTENSION_ID = "dnanpnconomdfnkdbgnlmdonepkjgbab"
EXTENSION_ORIGIN = f"chrome-extension://{EXTENSION_ID}/"
BRIDGE_DESCRIPTOR = "run/browser-control/bridge.json"
INSTALL_DIR = "memory/surfaces/browser"
MAX_NATIVE_FROM_CHROME = 64 * 1024 * 1024
MAX_NATIVE_TO_CHROME = 1024 * 1024
MAX_CLIENT_REQUEST = 512 * 1024
MAX_CLIENT_RESPONSE = 32 * 1024 * 1024
ALLOWED_CDP_METHODS = frozenset({
    "Runtime.evaluate", "Page.navigate", "Page.captureScreenshot",
    "Input.insertText", "Input.dispatchKeyEvent",
})


class BrowserBridgeError(RuntimeError):
    """Connected-Chrome bridge is absent, busy, or rejected the request."""


class BrowserBridgeUnavailable(BrowserBridgeError):
    """The optional Connected Tab transport is not currently available."""


def browser_empty_catalog_message() -> str:
    """Return the operator-safe result for a valid empty tab catalog."""
    return (
        "MO Connected Tab returned a valid tab catalog with zero available targets. "
        "Open the requested ordinary HTTP(S) page in the connected Chrome profile. "
        "Private/protected pages, tabs stopped in Chrome, and tabs held by another debugger "
        "are excluded. No extension click is required to connect an available tab."
    )


def _browser_bridge_state_message(observation: str, operation: str) -> str:
    """Describe only the bridge state observed by one failed request."""
    outcome = (
        "The tab catalog was not checked."
        if operation == "catalog"
        else "The requested bridge operation did not complete."
    )
    return (
        f"{observation} {outcome} Connected-tab DOM and viewport access were not established. "
        "Native-host repair is automatic on browser-tool use; Chrome owns extension installation. "
        "Do not substitute native UI when the request requires Connected Tab."
    )


def _configure_native_stdio() -> None:
    """Keep Chrome's length-prefixed frames byte-exact on Windows."""
    if os.name != "nt":
        return
    import msvcrt

    msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
    msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)


def validate_cdp_request(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Return one exact allowlisted CDP payload or reject widened authority."""
    if method not in ALLOWED_CDP_METHODS:
        raise BrowserBridgeError(f"CDP method is not allowed: {method or '(blank)'}")
    if not isinstance(params, dict):
        raise BrowserBridgeError("CDP params must be an object")
    if method == "Runtime.evaluate":
        expression = str(params.get("expression") or "")
        if not expression or len(expression) > 200_000:
            raise BrowserBridgeError("page script is blank or exceeds 200000 characters")
        if set(params) - {"expression", "returnByValue", "awaitPromise"}:
            raise BrowserBridgeError("Runtime.evaluate received unsupported parameters")
        if params.get("returnByValue") is not True or params.get("awaitPromise") is not True:
            raise BrowserBridgeError("Runtime.evaluate must return a bounded value and await promises")
        return {"expression": expression, "returnByValue": True, "awaitPromise": True}
    if method == "Page.navigate":
        url = str(params.get("url") or "")
        if set(params) != {"url"} or len(url) > 4096 or urlparse(url).scheme.lower() not in {"http", "https"}:
            raise BrowserBridgeError("Page.navigate accepts one bounded http:// or https:// URL")
        return {"url": url}
    if method == "Input.insertText":
        if set(params) != {"text"} or not isinstance(params["text"], str) or len(params["text"]) > 200_000:
            raise BrowserBridgeError("Input.insertText accepts one bounded text string")
        return dict(params)
    if method == "Input.dispatchKeyEvent":
        if (
            set(params) != {"type", "key", "code", "modifiers", "windowsVirtualKeyCode", "text"}
            or params["type"] not in ("keyDown", "keyUp")
            or any(not isinstance(params[key], str) or not 1 <= len(params[key]) <= 64 for key in ("key", "code"))
            or type(params["modifiers"]) is not int or not 0 <= params["modifiers"] <= 15
            or type(params["windowsVirtualKeyCode"]) is not int or not 0 <= params["windowsVirtualKeyCode"] <= 255
            or not isinstance(params["text"], str) or len(params["text"]) > 1
        ):
            raise BrowserBridgeError("Input.dispatchKeyEvent accepts one bounded page key event")
        return dict(params)
    expected = {"format": "png", "fromSurface": True, "captureBeyondViewport": False}
    if params != expected:
        raise BrowserBridgeError("Page.captureScreenshot is restricted to the visible PNG viewport")
    return expected


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise EOFError("native message ended before its declared length")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def read_native_message(stream: BinaryIO) -> dict[str, Any] | None:
    """Read one Chrome native-messaging frame; ``None`` means clean EOF."""
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise EOFError("truncated native message header")
    length = struct.unpack("=I", header)[0]
    if length <= 0 or length > MAX_NATIVE_FROM_CHROME:
        raise ValueError("native message length is outside the accepted boundary")
    payload = json.loads(_read_exact(stream, length).decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("native message must be a JSON object")
    return payload


def write_native_message(stream: BinaryIO, payload: dict[str, Any]) -> None:
    """Write one bounded Chrome native-messaging frame."""
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_NATIVE_TO_CHROME:
        raise ValueError("native response exceeds Chrome's host-to-extension limit")
    stream.write(struct.pack("=I", len(encoded)))
    stream.write(encoded)
    stream.flush()


def _remove_stale_descriptor(path: Path, descriptor: object) -> None:
    """Remove only the dead host descriptor that this request actually read."""
    if not isinstance(descriptor, dict):
        return
    token = str(descriptor.get("token") or "")
    pid = descriptor.get("pid")
    if not token or pid is None:
        return
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return
    if not isinstance(current, dict):
        return
    if str(current.get("token") or "") != token or current.get("pid") != pid:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


class _NativeSession:
    def __init__(self, output: BinaryIO) -> None:
        self.output = output
        self.call_lock = threading.Lock()
        self.pending_lock = threading.Lock()
        self.pending: dict[str, queue.Queue[dict[str, Any]]] = {}
        self.connected = threading.Event()
        self.closed = threading.Event()

    def receive(self, message: dict[str, Any]) -> None:
        self.connected.set()
        request_id = str(message.get("id") or "")
        if message.get("type") != "response" or not request_id:
            return
        with self.pending_lock:
            waiter = self.pending.get(request_id)
        if waiter is not None:
            waiter.put(message)

    def call(self, operation: str, payload: dict[str, Any], *, timeout: float = 25.0, cancelled: Any = None) -> Any:
        deadline = time.monotonic() + timeout

        def remaining() -> float:
            if self.closed.is_set():
                raise BrowserBridgeError("Chrome disconnected from the MO browser bridge")
            if cancelled is not None and cancelled():
                raise BrowserBridgeError("browser request cancelled")
            left = deadline - time.monotonic()
            if left <= 0:
                raise BrowserBridgeError(f"Chrome timed out handling {operation}")
            return left

        while not self.call_lock.acquire(timeout=min(0.05, remaining())):
            pass
        try:
            if self.closed.is_set():
                raise BrowserBridgeError("Chrome disconnected from the MO browser bridge")
            while not self.connected.wait(timeout=min(0.05, remaining())):
                pass
            request_id = uuid.uuid4().hex
            waiter: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
            with self.pending_lock:
                self.pending[request_id] = waiter
            try:
                write_native_message(self.output, {
                    "type": "request",
                    "id": request_id,
                    "operation": operation,
                    "expires_at": (time.time() + remaining()) * 1000,
                    **payload,
                })
                while True:
                    try:
                        response = waiter.get(timeout=min(0.05, remaining()))
                        break
                    except queue.Empty:
                        pass
                if not bool(response.get("ok")):
                    raise BrowserBridgeError(str(response.get("error") or "Chrome rejected the request"))
                return response.get("result")
            finally:
                with self.pending_lock:
                    self.pending.pop(request_id, None)
        finally:
            self.call_lock.release()

    def stop(self) -> None:
        self.closed.set()
        with self.pending_lock:
            waiters = list(self.pending.values())
        for waiter in waiters:
            try:
                waiter.put_nowait({"type": "response", "ok": False, "error": "Chrome disconnected"})
            except queue.Full:
                pass


class _BridgeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, address: tuple[str, int], host: "BrowserBridgeHost") -> None:
        self.host_runtime = host
        super().__init__(address, _BridgeHandler)


class _BridgeHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline(MAX_CLIENT_REQUEST + 1)
        if not raw or len(raw) > MAX_CLIENT_REQUEST:
            return
        try:
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("request must be a JSON object")
            def disconnected() -> bool:
                try:
                    readable, _, _ = select.select([self.connection], [], [], 0)
                    return bool(readable and not self.connection.recv(1, socket.MSG_PEEK))
                except OSError:
                    return True

            # Protocol 1 clients may half-close their write side after sending.
            # Deadline-aware clients keep it open and close it to cancel.
            response = self.server.host_runtime.handle_client(  # type: ignore[attr-defined]
                request, cancelled=disconnected if "deadline" in request else None,
            )
        except Exception as exc:  # noqa: BLE001 - boundary returns a typed failure
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_CLIENT_RESPONSE:
            encoded = b'{"ok":false,"error":"bridge response exceeded the local size boundary"}'
        self.wfile.write(encoded + b"\n")


class BrowserBridgeHost:
    """One native host, one extension port, and one serialized acting lease."""

    def __init__(self, output: BinaryIO) -> None:
        self.session = _NativeSession(output)
        self.token = secrets.token_urlsafe(32)
        self.server: _BridgeServer | None = None
        self.server_thread: threading.Thread | None = None
        self.controller_lock = threading.Lock()
        self.controller: tuple[int, str, str] | None = None
        self.descriptor_path = Path(resolve_state_path(BRIDGE_DESCRIPTOR))

    def start(self) -> None:
        self.server = _BridgeServer(("127.0.0.1", 0), self)
        self.server_thread = threading.Thread(target=self.server.serve_forever, name="mo-browser-bridge", daemon=True)
        self.server_thread.start()
        port = int(self.server.server_address[1])
        self.descriptor_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.descriptor_path, {
            "protocol": PROTOCOL_VERSION,
            "host": "127.0.0.1",
            "port": port,
            "pid": os.getpid(),
            "token": self.token,
        })
        try:
            os.chmod(self.descriptor_path, 0o600)
        except OSError:
            pass

    def stop(self) -> None:
        self.session.stop()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        try:
            current = json.loads(self.descriptor_path.read_text(encoding="utf-8"))
            if hmac.compare_digest(str(current.get("token") or ""), self.token):
                self.descriptor_path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            pass

    def receive(self, message: dict[str, Any]) -> None:
        if message.get("type") == "event" and message.get("event") == "detached":
            detached = str(message.get("target") or "")
            with self.controller_lock:
                if self.controller is not None and self.controller[2] == detached:
                    self.controller = None
        self.session.receive(message)

    @staticmethod
    def _controller_identity(request: dict[str, Any]) -> tuple[int, str, str]:
        try:
            pid = int(request.get("pid") or 0)
        except (TypeError, ValueError) as exc:
            raise BrowserBridgeError("controller pid is invalid") from exc
        owner = str(request.get("owner") or "").strip()[:300]
        target = str(request.get("target") or "").strip()[:100]
        if pid <= 0 or not owner or not target or not runtime_lock._pid_alive(pid):
            raise BrowserBridgeError("controller identity is not a live MO process")
        return pid, owner, target

    def _claim(self, request: dict[str, Any]) -> None:
        pid, owner, target = self._controller_identity(request)
        with self.controller_lock:
            if self.controller is not None:
                current_pid, current_owner, _current_target = self.controller
                if not runtime_lock._pid_alive(current_pid):
                    self.controller = None
                elif (current_pid, current_owner) != (pid, owner):
                    raise BrowserBridgeError("a different live MO process currently controls the shared Chrome tab")
            self.controller = (pid, owner, target)

    def _release(self, request: dict[str, Any]) -> bool:
        identity = self._controller_identity(request)
        with self.controller_lock:
            if self.controller != identity:
                return False
            self.controller = None
            return True

    def handle_client(self, request: dict[str, Any], *, cancelled: Any = None) -> dict[str, Any]:
        supplied = str(request.get("token") or "")
        if not hmac.compare_digest(supplied, self.token):
            return {"ok": False, "error": "bridge authentication failed"}
        operation = str(request.get("operation") or "")
        claimed = False
        try:
            timeout = min(25.0, float(request.get("deadline", time.monotonic() + 25.0)) - time.monotonic())
            if timeout <= 0 or (cancelled is not None and cancelled()):
                raise BrowserBridgeError("browser request expired or cancelled")
            if operation == "ping":
                return {"ok": True, "result": {"protocol": PROTOCOL_VERSION, "extension": self.session.connected.is_set()}}
            if operation == "catalog":
                result = self.session.call("catalog", {}, timeout=timeout, cancelled=cancelled)
            elif operation == "release":
                result = {"released": self._release(request)}
            elif operation == "cdp":
                method = str(request.get("method") or "")
                access = str(request.get("access") or "").strip().lower()
                if access not in {"observe", "act"}:
                    raise BrowserBridgeError("CDP access must be observe or act")
                if (method == "Page.navigate" or method.startswith("Input.")) and access != "act":
                    raise BrowserBridgeError(f"{method} requires acting-owner access")
                if method == "Page.captureScreenshot" and access != "observe":
                    raise BrowserBridgeError("Page.captureScreenshot is observation-only")
                params = request.get("params")
                params = validate_cdp_request(method, params)
                if access == "act":
                    self._claim(request)
                    claimed = True
                result = self.session.call("cdp", {
                    "target": str(request.get("target") or ""),
                    "method": method,
                    "params": params,
                }, timeout=timeout, cancelled=cancelled)
            else:
                raise BrowserBridgeError(f"unsupported bridge operation: {operation or '(blank)'}")
            return {"ok": True, "result": result}
        except BrowserBridgeError as exc:
            if claimed:
                self._release(request)
            return {"ok": False, "error": str(exc)}


class BrowserBridgeClient:
    """Small request client; reads the current private descriptor on every call."""

    def __init__(self, *, owner_id: str, auto_prepare: bool = False) -> None:
        self.owner_id = str(owner_id or "")
        self.auto_prepare = bool(auto_prepare)
        self._registration_prepared = False

    def _prepare_registration(self, operation: str) -> None:
        if not self.auto_prepare or self._registration_prepared:
            return
        try:
            ensure_installed()
        except (BrowserBridgeError, OSError, ValueError) as exc:
            raise BrowserBridgeUnavailable(_browser_bridge_state_message(
                "The MO Connected Tab native-host registration could not be prepared automatically.",
                operation,
            )) from exc
        self._registration_prepared = True

    def _request(self, operation: str, *, timeout: float = 25.0, cancel_event: Any = None, **payload: Any) -> Any:
        deadline = time.monotonic() + max(0.01, min(float(timeout), 30.0))

        def remaining() -> float:
            if cancel_event is not None and cancel_event.is_set():
                raise BrowserBridgeError("browser request cancelled")
            left = deadline - time.monotonic()
            if left <= 0:
                raise BrowserBridgeError("browser request timed out")
            return left

        remaining()
        self._prepare_registration(operation)
        path = Path(resolve_state_path(BRIDGE_DESCRIPTOR))
        try:
            descriptor = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            observation = (
                "The native-host registration is ready, but Chrome has not opened the "
                "MO Connected Tab channel; the extension may be absent or disconnected."
                if self.auto_prepare
                else "The MO Connected Tab runtime descriptor is unavailable."
            )
            raise BrowserBridgeUnavailable(_browser_bridge_state_message(observation, operation)) from exc
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise BrowserBridgeUnavailable(_browser_bridge_state_message(
                "The MO Connected Tab runtime descriptor is malformed.", operation
            )) from exc
        if not isinstance(descriptor, dict):
            raise BrowserBridgeUnavailable(_browser_bridge_state_message(
                "The MO Connected Tab runtime descriptor is malformed.", operation
            ))
        if "protocol" not in descriptor:
            raise BrowserBridgeUnavailable(_browser_bridge_state_message(
                "The MO Connected Tab runtime descriptor is malformed.", operation
            ))
        try:
            protocol = int(descriptor.get("protocol") or 0)
        except (ValueError, TypeError) as exc:
            raise BrowserBridgeUnavailable(_browser_bridge_state_message(
                "The MO Connected Tab runtime descriptor is malformed.", operation
            )) from exc
        if protocol != PROTOCOL_VERSION:
            raise BrowserBridgeUnavailable(_browser_bridge_state_message(
                "The MO Connected Tab runtime descriptor uses an unsupported protocol. "
                "Reload MO Connected Tab in chrome://extensions and restart MO to load matching code.", operation
            ))
        try:
            host = str(descriptor.get("host") or "")
            port = int(descriptor.get("port") or 0)
            token = str(descriptor.get("token") or "")
            pid = int(descriptor.get("pid") or 0)
            if pid <= 0:
                raise ValueError("invalid descriptor pid")
            if not runtime_lock._pid_alive(pid):
                _remove_stale_descriptor(path, descriptor)
                raise BrowserBridgeUnavailable(_browser_bridge_state_message(
                    "The MO Connected Tab runtime descriptor is stale because its recorded host process is not running.",
                    operation,
                ))
            if host != "127.0.0.1" or not (1 <= port <= 65535) or not token:
                raise ValueError("malformed descriptor")
        except (ValueError, TypeError) as exc:
            raise BrowserBridgeUnavailable(_browser_bridge_state_message(
                "The MO Connected Tab runtime descriptor is malformed.", operation
            )) from exc
        request = {
            "token": token,
            "operation": operation,
            "pid": os.getpid(),
            "owner": self.owner_id,
            "deadline": deadline,
            **payload,
        }
        encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(encoded) > MAX_CLIENT_REQUEST:
            raise BrowserBridgeError("browser bridge request exceeds the local size boundary")
        try:
            with socket.create_connection((host, port), timeout=min(5.0, remaining())) as connection:
                connection.settimeout(remaining())
                connection.sendall(encoded)
                chunks: list[bytes] = []
                total = 0
                while True:
                    connection.settimeout(min(0.1, remaining()))
                    try:
                        chunk = connection.recv(65536)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_CLIENT_RESPONSE:
                        raise BrowserBridgeError("browser bridge response exceeds the local size boundary")
                    chunks.append(chunk)
                    if chunk.endswith(b"\n"):
                        break
        except (OSError, TimeoutError) as exc:
            raise BrowserBridgeUnavailable(_browser_bridge_state_message(
                "The MO Connected Tab bridge endpoint is unavailable.", operation
            )) from exc
        try:
            response = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BrowserBridgeError("Chrome browser bridge returned an invalid response") from exc
        if not isinstance(response, dict) or not bool(response.get("ok")):
            raise BrowserBridgeError(str(response.get("error") if isinstance(response, dict) else "bridge request failed"))
        return response.get("result")

    def catalog(self, *, timeout: float = 5.0, cancel_event: Any = None) -> list[dict[str, Any]]:
        result = self._request("catalog", timeout=timeout, cancel_event=cancel_event)
        if not isinstance(result, list) or any(not isinstance(item, dict) for item in result):
            raise BrowserBridgeError("Chrome returned an invalid shared-tab catalog")
        return result

    def command(
        self,
        target: str,
        method: str,
        params: dict[str, Any],
        *,
        access: str,
        timeout: float = 20.0,
        cancel_event: Any = None,
    ) -> dict[str, Any]:
        result = self._request(
            "cdp",
            target=str(target or ""),
            method=method,
            params=params,
            access=str(access or ""),
            timeout=timeout,
            cancel_event=cancel_event,
        )
        if not isinstance(result, dict):
            raise BrowserBridgeError("Chrome returned an invalid CDP result")
        return result

    def release(self, target: str) -> bool:
        result = self._request("release", target=str(target or ""), timeout=1.0)
        return bool(result.get("released")) if isinstance(result, dict) else False

    def ping(self) -> dict[str, Any]:
        result = self._request("ping")
        return result if isinstance(result, dict) else {}


def _extension_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "clients" / "chrome"


def _install_dir() -> Path:
    return Path(resolve_state_path(INSTALL_DIR))


def _launcher_text() -> tuple[str, str]:
    repo = Path(__file__).resolve().parents[1]
    python = Path(sys.executable).resolve(strict=False)
    if os.name == "nt":
        safe_repo = str(repo).replace("%", "%%")
        safe_python = str(python).replace("%", "%%")
        # ``Path.write_text`` performs Windows newline translation. Supplying
        # CRLF here would install CR CR LF batch lines.
        return "host.bat", f'@echo off\ncd /d "{safe_repo}"\n"{safe_python}" -B -m core.browser_bridge native-host %*\n'
    return "host.sh", f"#!/bin/sh\ncd -- {shlex.quote(str(repo))}\nexec {shlex.quote(str(python))} -B -m core.browser_bridge native-host \"$@\"\n"


def _native_manifest(launcher: Path) -> dict[str, Any]:
    return {
        "name": HOST_NAME,
        "description": "MO explicit connected-Chrome bridge",
        "path": str(launcher.resolve(strict=False)),
        "type": "stdio",
        "allowed_origins": [EXTENSION_ORIGIN],
    }


def _windows_registry_paths() -> list[str]:
    return [rf"Software\Google\Chrome\NativeMessagingHosts\{HOST_NAME}"]


def _posix_manifest_paths() -> list[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        return [
            home / "Library" / "Application Support" / "Google" / "Chrome" / "NativeMessagingHosts" / f"{HOST_NAME}.json",
            home / "Library" / "Application Support" / "Chromium" / "NativeMessagingHosts" / f"{HOST_NAME}.json",
        ]
    return [
        home / ".config" / "google-chrome" / "NativeMessagingHosts" / f"{HOST_NAME}.json",
        home / ".config" / "chromium" / "NativeMessagingHosts" / f"{HOST_NAME}.json",
    ]


def install() -> dict[str, Any]:
    """Install the current checkout's native host registration, idempotently."""
    install_dir = _install_dir()
    launcher_name, launcher_text = _launcher_text()
    launcher = install_dir / launcher_name
    manifest_path = install_dir / f"{HOST_NAME}.json"
    record_path = install_dir / "install.json"
    manifest = _native_manifest(launcher)
    registrations: list[str] = []
    file_targets = [launcher, manifest_path, record_path]
    registry_existing: dict[str, str] = {}
    if os.name == "nt":
        import winreg

        for registry_path in _windows_registry_paths():
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path) as key:
                    existing = str(winreg.QueryValueEx(key, "")[0] or "")
            except FileNotFoundError:
                existing = ""
            if existing and Path(existing).resolve(strict=False) != manifest_path.resolve(strict=False):
                raise BrowserBridgeError(f"native host registration is owned by another manifest: {registry_path}")
            registry_existing[registry_path] = existing
    else:
        file_targets.extend(_posix_manifest_paths())
        expected_launcher = launcher.resolve(strict=False)
        for target in _posix_manifest_paths():
            if not target.exists():
                continue
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise BrowserBridgeError(f"refusing to replace unreadable native host manifest: {target}") from exc
            existing_launcher = Path(str(existing.get("path") or "")).resolve(strict=False)
            if existing.get("name") != HOST_NAME or existing_launcher != expected_launcher:
                raise BrowserBridgeError(f"native host manifest path is owned by another install: {target}")
    previous: dict[Path, str | None] = {}
    for target in file_targets:
        try:
            previous[target] = target.read_text(encoding="utf-8")
        except FileNotFoundError:
            previous[target] = None
    try:
        install_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(launcher, launcher_text, encoding="utf-8")
        if os.name != "nt":
            os.chmod(launcher, 0o700)
        atomic_write_json(manifest_path, manifest)
        if os.name == "nt":
            for registry_path in _windows_registry_paths():
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, registry_path) as key:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, str(manifest_path.resolve(strict=False)))
                registrations.append(registry_path)
        else:
            payload = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
            for target in _posix_manifest_paths():
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(target, payload, encoding="utf-8")
                registrations.append(str(target))
        record = {
            "protocol": PROTOCOL_VERSION,
            "manifest": str(manifest_path.resolve(strict=False)),
            "launcher": str(launcher.resolve(strict=False)),
            "registrations": registrations,
            "extension_id": EXTENSION_ID,
        }
        atomic_write_json(record_path, record)
    except Exception:
        if os.name == "nt":
            for registry_path in registrations:
                if not registry_existing.get(registry_path):
                    try:
                        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, registry_path)
                    except FileNotFoundError:
                        pass
        for target, content in previous.items():
            if content is None:
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(target, content, encoding="utf-8")
        raise
    return {**record, "extension_dir": str(_extension_dir())}


def uninstall() -> dict[str, Any]:
    """Remove only registrations and generated files that resolve to this install."""
    install_dir = _install_dir().resolve(strict=False)
    manifest_path = (install_dir / f"{HOST_NAME}.json").resolve(strict=False)
    removed: list[str] = []
    if os.name == "nt":
        import winreg

        for registry_path in _windows_registry_paths():
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry_path) as key:
                    existing = str(winreg.QueryValueEx(key, "")[0] or "")
                if Path(existing).resolve(strict=False) == manifest_path:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, registry_path)
                    removed.append(registry_path)
            except FileNotFoundError:
                pass
    else:
        for target in _posix_manifest_paths():
            try:
                existing = json.loads(target.read_text(encoding="utf-8"))
                if existing.get("name") == HOST_NAME and Path(str(existing.get("path") or "")).resolve(strict=False).parent == install_dir:
                    target.unlink()
                    removed.append(str(target))
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                pass
    for name in ("install.json", f"{HOST_NAME}.json", "host.bat", "host.cmd", "host.sh"):
        target = (install_dir / name).resolve(strict=False)
        if target.parent == install_dir:
            try:
                target.unlink()
                removed.append(str(target))
            except FileNotFoundError:
                pass
    try:
        install_dir.rmdir()
    except OSError:
        pass
    return {"removed": removed}


def _installation_status() -> dict[str, Any]:
    """Inspect native-host registration without starting or repairing it."""
    install_dir = _install_dir()
    launcher_name, launcher_text = _launcher_text()
    launcher = install_dir / launcher_name
    manifest = install_dir / f"{HOST_NAME}.json"
    expected_manifest = _native_manifest(launcher)
    files_valid = False
    try:
        installed_manifest = json.loads(manifest.read_text(encoding="utf-8"))
        files_valid = (
            installed_manifest == expected_manifest
            and launcher.read_text(encoding="utf-8") == launcher_text
        )
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        files_valid = False
    registered = False
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _windows_registry_paths()[0]) as key:
                registered = Path(str(winreg.QueryValueEx(key, "")[0])).resolve(strict=False) == manifest.resolve(strict=False)
        except (FileNotFoundError, OSError):
            registered = False
    else:
        expected_launcher = (_install_dir() / _launcher_text()[0]).resolve(strict=False)
        for path in _posix_manifest_paths():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                registered = bool(
                    existing.get("name") == HOST_NAME
                    and Path(str(existing.get("path") or "")).resolve(strict=False) == expected_launcher
                )
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                registered = False
            if registered:
                break
    return {
        "installed": files_valid and registered,
        "registered": registered,
        "files_valid": files_valid,
        "extension_id": EXTENSION_ID,
        "extension_dir": str(_extension_dir()),
    }


def ensure_installed() -> dict[str, Any]:
    """Return a valid native-host registration, repairing it idempotently."""
    current = _installation_status()
    if current["installed"]:
        return current
    install()
    repaired = _installation_status()
    if not repaired["installed"]:
        raise BrowserBridgeError("native-host registration repair did not produce a valid installation")
    return repaired


def status() -> dict[str, Any]:
    current = _installation_status()
    live = False
    extension = False
    error = ""
    try:
        ping = BrowserBridgeClient(owner_id=f"status:{os.getpid()}").ping()
        live = True
        extension = bool(ping.get("extension"))
    except BrowserBridgeError as exc:
        error = str(exc)
    return {
        **current,
        "bridge_live": live,
        "extension_connected": extension,
        "error": error,
    }


def run_native_host(origin: str = "") -> int:
    if origin and origin != EXTENSION_ORIGIN:
        return 2
    _configure_native_stdio()
    host = BrowserBridgeHost(sys.stdout.buffer)
    host.start()
    try:
        while True:
            message = read_native_message(sys.stdin.buffer)
            if message is None:
                break
            if int(message.get("protocol") or PROTOCOL_VERSION) != PROTOCOL_VERSION:
                continue
            host.receive(message)
    except (EOFError, ValueError, OSError, json.JSONDecodeError):
        return 1
    finally:
        host.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MO connected-Chrome native bridge")
    parser.add_argument("command", choices=("install", "status", "uninstall", "native-host"))
    parser.add_argument("origin", nargs="?", default="")
    args, _chrome_arguments = parser.parse_known_args(argv)
    if args.command == "native-host":
        return run_native_host(args.origin)
    try:
        result = install() if args.command == "install" else uninstall() if args.command == "uninstall" else status()
    except (BrowserBridgeError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.command == "install":
        print("\nIn Chrome, open chrome://extensions, enable Developer mode, choose Load unpacked, and select extension_dir. MO discovers ordinary tabs and connects to the requested tab automatically.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
