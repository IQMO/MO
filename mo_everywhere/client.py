"""Small stdlib client and private device credential storage for Everywhere."""
from __future__ import annotations

import errno
import base64
import json
import re
import hashlib
import math
import os
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from core.files.service import (
    FILE_SOURCE_KINDS,
    valid_file_capability_descriptor,
)
from core.state.continuity_events import ContinuityEvent
from core.state.attachments import (
    attachment_home,
    record_attachment,
    unique_attachment_path,
)
from core.runtime.lock import file_byte_lock
from core.state.paths import default_project_roots, resolve_state_path
from core.transfer.locking import transfer_operation_lock
from core.transfer.model import TransferError, named_destination_path
from core.utils.atomic_write import atomic_write_json
from core.utils.file_hash import file_sha256


CREDENTIAL_PATH = "credentials/everywhere.json"
TRANSFER_CREDENTIAL_PATH = "credentials/everywhere-transfer.json"
FILES_CREDENTIAL_PATH = "credentials/everywhere-files.json"
MAX_RESPONSE_BYTES = 512 * 1024
MAX_ERROR_RESPONSE_BYTES = 4 * 1024
MAX_FILE_TRANSFER_BYTES = 2 * 1024 * 1024 * 1024
MIN_FILE_TRANSFER_CHUNK_BYTES = 64 * 1024
MAX_FILE_TRANSFER_CHUNK_BYTES = 16 * 1024 * 1024
MAX_FILE_TRANSFER_CHUNKS = (
    MAX_FILE_TRANSFER_BYTES // MIN_FILE_TRANSFER_CHUNK_BYTES
)
FILE_TRANSFER_STATES = frozenset(
    {
        "created",
        "uploading",
        "offered",
        "claimed",
        "delivered",
        "done",
        "failed",
        "cancelled",
        "expired",
    }
)
_REFRESH_THREAD_LOCK = threading.RLock()
_WINDOWS_CREDENTIAL_PROTECTION = "windows-dpapi-user"


class EverywhereClientError(RuntimeError):
    """Safe transport/configuration error without credentials or response bodies."""


def _hub_rejection(exc: urllib.error.HTTPError, action: str) -> str:
    """Return one bounded public Hub rejection without exposing raw bodies."""
    message = f"Everywhere hub rejected {action} ({exc.code})"
    try:
        raw = exc.read(MAX_ERROR_RESPONSE_BYTES + 1)
        if len(raw) > MAX_ERROR_RESPONSE_BYTES:
            return message
        body = json.loads(raw or b"{}")
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return message
    detail = body.get("detail") if isinstance(body, dict) else None
    if not isinstance(detail, str):
        return message
    clean = " ".join(detail.split())
    if (
        not 1 <= len(clean) <= 240
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in clean)
    ):
        return message
    return f"{message}: {clean}"


@dataclass(frozen=True)
class DeviceCredentials:
    hub_url: str
    access_token: str
    refresh_token: str
    access_expires_at: float
    refresh_expires_at: float
    device_id: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DeviceCredentials":
        value = cls(
            hub_url=_hub_url(raw.get("hub_url")),
            access_token=_token(raw.get("access_token")),
            refresh_token=_token(raw.get("refresh_token")),
            access_expires_at=float(raw.get("access_expires_at") or 0.0),
            refresh_expires_at=float(raw.get("refresh_expires_at") or 0.0),
            device_id=_id(raw.get("device_id"), 64),
        )
        if not value.access_token or not value.refresh_token or not value.device_id:
            raise EverywhereClientError("Everywhere device credentials are incomplete")
        return value


def credential_path(config: dict[str, Any] | None = None) -> Path:
    block = (config or {}).get("consistent_everywhere")
    block = block if isinstance(block, dict) else {}
    continuity = block.get("continuity") if isinstance(block.get("continuity"), dict) else {}
    configured = str(continuity.get("credentials_file") or "").strip()
    if configured:
        path = Path(resolve_state_path(configured, config or {})).expanduser().resolve(strict=False)
    else:
        path = Path(resolve_state_path(CREDENTIAL_PATH, config or {})).resolve(strict=False)
    return path


def transfer_client_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Point cargo at its dedicated least-privilege device credential."""
    source = config or {}
    settings = source.get("file_transfer")
    settings = settings if isinstance(settings, dict) else {}
    configured = str(
        settings.get("credentials_file") or TRANSFER_CREDENTIAL_PATH
    ).strip()
    return _credential_scoped_config(source, configured)


def _credential_scoped_config(
    config: dict[str, Any] | None,
    credentials_file: str | Path,
) -> dict[str, Any]:
    """Copy config structure while changing only the continuity credential."""
    source = config or {}
    copied = dict(source)
    everywhere = dict(source.get("consistent_everywhere") or {})
    continuity = dict(everywhere.get("continuity") or {})
    continuity["credentials_file"] = str(
        resolve_state_path(str(credentials_file), source)
    )
    everywhere["continuity"] = continuity
    copied["consistent_everywhere"] = everywhere
    return copied


def files_client_config(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Point MO Files at its dedicated least-privilege controller identity."""
    return _credential_scoped_config(config, FILES_CREDENTIAL_PATH)


def load_credentials(config: dict[str, Any] | None = None) -> DeviceCredentials:
    try:
        raw = json.loads(credential_path(config).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise EverywhereClientError("Everywhere device is not paired") from None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        raise EverywhereClientError("Everywhere device credentials are unreadable") from None
    if not isinstance(raw, dict):
        raise EverywhereClientError("Everywhere device credentials are unreadable")
    if raw.get("protection") == _WINDOWS_CREDENTIAL_PROTECTION:
        try:
            encoded = str(raw.get("ciphertext") or "")
            clear = _windows_unprotect(base64.b64decode(encoded, validate=True))
            raw = json.loads(clear.decode("utf-8"))
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            raise EverywhereClientError("Everywhere device credentials are unreadable") from None
        if not isinstance(raw, dict):
            raise EverywhereClientError("Everywhere device credentials are unreadable")
    return DeviceCredentials.from_dict(raw)


def save_credentials(credentials: DeviceCredentials, config: dict[str, Any] | None = None) -> Path:
    path = credential_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(
        asdict(credentials),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if os.name == "nt":
        protected = _windows_protect(raw)
        payload: dict[str, Any] = {
            "version": 2,
            "protection": _WINDOWS_CREDENTIAL_PROTECTION,
            "ciphertext": base64.b64encode(protected).decode("ascii"),
        }
    else:
        payload = asdict(credentials)
    atomic_write_json(path, payload, indent=2, ensure_ascii=False)
    if os.name != "nt":
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return path


def _windows_protect(value: bytes) -> bytes:
    if os.name != "nt":
        raise OSError("Windows credential protection is unavailable")
    return _windows_crypt(value, protect=True)


def _windows_unprotect(value: bytes) -> bytes:
    if os.name != "nt":
        raise OSError("Windows credential protection is unavailable")
    return _windows_crypt(value, protect=False)


def _windows_crypt(value: bytes, *, protect: bool) -> bytes:
    """Protect one bounded credential payload with the current Windows user."""
    import ctypes
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    if not value or len(value) > MAX_RESPONSE_BYTES:
        raise OSError("credential payload is invalid")
    source_buffer = ctypes.create_string_buffer(value)
    source = DataBlob(
        len(value),
        ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    output = DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    if protect:
        ok = crypt32.CryptProtectData(
            ctypes.byref(source),
            ctypes.c_wchar_p("MO Everywhere"),
            None,
            None,
            None,
            flags,
            ctypes.byref(output),
        )
    else:
        description = wintypes.LPWSTR()
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source),
            ctypes.byref(description),
            None,
            None,
            None,
            flags,
            ctypes.byref(output),
        )
        if description:
            kernel32.LocalFree(description)
    if not ok:
        raise OSError(ctypes.get_last_error(), "Windows credential protection failed")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        if output.pbData:
            kernel32.LocalFree(output.pbData)


def join_hub(
    hub_url: str,
    pairing_code: str,
    label: str,
    config: dict[str, Any] | None = None,
    *,
    timeout: float = 15.0,
) -> DeviceCredentials:
    """Redeem one hub-issued pairing code into the device-private credential file."""
    base = _hub_url(hub_url)
    code = str(pairing_code or "").strip()
    clean_label = " ".join(str(label or "").split())[:80]
    if not code.startswith("mo_pair_") or len(code) < 32 or not clean_label:
        raise EverywhereClientError("a valid pairing code and device label are required")
    payload = json.dumps({"code": code, "label": clean_label}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        base + "/api/mo/pair",
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=max(2.0, min(60.0, float(timeout)))) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise EverywhereClientError(_hub_rejection(exc, "pairing")) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise EverywhereClientError("Everywhere hub is unreachable") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise EverywhereClientError("Everywhere hub response is too large")
    try:
        body = json.loads(raw or b"{}")
        device = body.get("device") if isinstance(body, dict) and isinstance(body.get("device"), dict) else {}
        credentials = DeviceCredentials.from_dict({
            **(body if isinstance(body, dict) else {}),
            "hub_url": base,
            "device_id": device.get("device_id"),
        })
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError, EverywhereClientError):
        raise EverywhereClientError("Everywhere hub returned invalid pairing credentials") from None
    save_credentials(credentials, config)
    return credentials


class ContinuityClient:
    """Authenticated cursor client; urllib stays outside every terminal turn."""

    def __init__(self, config: dict[str, Any] | None = None, *, timeout: float = 15.0):
        self.config = config or {}
        self.timeout = max(2.0, min(60.0, float(timeout)))
        self._credentials = load_credentials(self.config)

    @property
    def hub_key(self) -> str:
        parsed = urllib.parse.urlsplit(self._credentials.hub_url)
        return _id(f"{parsed.scheme}-{parsed.hostname}-{parsed.port or ''}", 120)

    @property
    def device_id(self) -> str:
        return self._credentials.device_id

    def publish(self, event: ContinuityEvent) -> int:
        body = self._authorized_json(
            "POST",
            "/api/mo/continuity/events",
            {"event": event.as_dict(include_cursor=False)},
        )
        try:
            return max(0, int(body.get("cursor") or 0))
        except (TypeError, ValueError):
            raise EverywhereClientError("Everywhere hub returned an invalid continuity cursor") from None

    def events(self, *, after: int = 0, limit: int = 100) -> tuple[list[ContinuityEvent], int]:
        query = urllib.parse.urlencode({"after": max(0, int(after)), "limit": max(1, min(200, int(limit)))})
        body = self._authorized_json("GET", f"/api/mo/continuity/events?{query}")
        raw_events = body.get("events") if isinstance(body.get("events"), list) else []
        events = [ContinuityEvent.from_dict(raw, max_age_seconds=30 * 24 * 60 * 60) for raw in raw_events if isinstance(raw, dict)]
        try:
            cursor = max([int(body.get("cursor") or after), *(event.remote_cursor for event in events)])
        except (TypeError, ValueError):
            raise EverywhereClientError("Everywhere hub returned an invalid continuity cursor") from None
        return events, cursor

    def thread_summaries(self, limit: int = 20) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"limit": max(1, min(100, int(limit)))})
        body = self._authorized_json("GET", f"/api/mo/continuity/threads?{query}")
        return [item for item in body.get("threads", []) if isinstance(item, dict)]

    def device_status(self) -> dict[str, Any]:
        """Inspect this authenticated identity; a readable credential is not proof."""
        body = self._authorized_json("GET", "/api/mo/device")
        scopes = body.get("scopes")
        if (set(body) != {"device_id", "label", "capability", "scopes"}
                or body.get("capability") not in {"notify", "control"}
                or not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes)):
            raise EverywhereClientError("Everywhere hub returned invalid device status")
        return body

    def android_devices(self) -> list[dict[str, Any]]:
        """Read paired Android metadata through the existing coordinator authority."""
        from .registry import DEVICE_CAPABILITIES, DEVICE_SCOPES

        rows = self._authorized_json("GET", "/api/mo/devices/android").get("devices")
        if not isinstance(rows, list):
            raise EverywhereClientError("Everywhere hub returned invalid Android devices")
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"device_id", "label", "capability", "scopes", "last_seen_at"}:
                raise EverywhereClientError("Everywhere hub returned invalid Android device metadata")
            device_id, label, scopes, seen_at = row["device_id"], row["label"], row["scopes"], row["last_seen_at"]
            if (not isinstance(device_id, str) or not re.fullmatch(r"[0-9a-f]{32}", device_id)
                    or device_id in seen or not isinstance(label, str) or not 1 <= len(label) <= 80
                    or any(ord(c) < 32 or ord(c) == 127 for c in label)
                    or not isinstance(row["capability"], str) or row["capability"] not in DEVICE_CAPABILITIES
                    or not isinstance(scopes, list) or any(not isinstance(s, str) or s not in DEVICE_SCOPES for s in scopes)
                    or isinstance(seen_at, bool) or not isinstance(seen_at, (int, float)) or not math.isfinite(seen_at) or seen_at < 0):
                raise EverywhereClientError("Everywhere hub returned invalid Android device metadata")
            seen.add(device_id)
        return rows

    def create_android_pairing(self, *, phone_control: bool = False) -> dict[str, Any]:
        """Request one fixed Android grant from the serving registry.

        The hub reuses this device's existing coordinator credential. The
        returned one-time code stays inside the strict payload and is intended
        only for immediate local QR rendering.
        """
        body = self._authorized_json(
            "POST",
            "/api/mo/pairing/android",
            {"phone_control": bool(phone_control)},
        )
        raw = body.get("pairing") if isinstance(body.get("pairing"), dict) else {}
        try:
            from .pairing_qr import build_pairing_payload

            return build_pairing_payload(
                hub=raw.get("hub"),
                code=raw.get("code"),
                expires_at=raw.get("expires_at"),
                capability=raw.get("capability"),
                scopes=raw.get("scopes"),
            )
        except (TypeError, ValueError):
            raise EverywhereClientError("Everywhere hub returned an invalid pairing grant") from None

    def hub_terminals(self) -> dict[str, Any]:
        """List terminals owned by the serving Everywhere hub."""
        return self._authorized_json("GET", "/api/mo/terminals")

    def start_hub_terminal(self, terminal_id: str = "", *, project_path: str = "") -> dict[str, Any]:
        """Start one bounded hub-owned MO terminal, optionally idempotently."""
        terminal_id = _id(terminal_id, 64)
        payload = {"terminal_id": terminal_id} if terminal_id else {}
        if project_path:
            payload["project_path"] = project_path
        return self._authorized_json("POST", "/api/mo/terminals", payload)

    def start_desktop_terminal(
        self,
        host_id: str,
        client_request_id: str,
    ) -> dict[str, Any]:
        """Ask one exact advertised Desktop actuator to start MO."""
        host_id = _exact_hex_id(host_id, "Desktop host id")
        request_id = _exact_hex_id(client_request_id, "Desktop terminal request id")
        return self._authorized_json(
            "POST",
            "/api/mo/live/host-actions",
            {
                "host_id": host_id,
                "action": "start_mo_terminal",
                "client_request_id": request_id,
            },
        )

    def stop_desktop_terminal(
        self,
        host_id: str,
        instance_id: str,
        client_request_id: str,
    ) -> dict[str, Any]:
        """Stop one exact terminal retained by one Desktop actuator."""
        host_id = _exact_hex_id(host_id, "Desktop host id")
        request_id = _exact_hex_id(client_request_id, "Desktop terminal request id")
        instance_id = _id(instance_id, 64)
        if not instance_id:
            raise EverywhereClientError("Desktop terminal instance id is invalid")
        return self._authorized_json(
            "POST",
            "/api/mo/live/host-actions",
            {
                "host_id": host_id,
                "action": "stop_mo_terminal",
                "client_request_id": request_id,
                "instance_id": instance_id,
            },
        )

    def stop_hub_terminal(self, terminal_id: str, *, missing_ok: bool = False) -> bool:
        """Stop one exact hub-owned MO terminal."""
        terminal_id = _id(terminal_id, 64)
        query = "?missing_ok=true" if missing_ok else ""
        body = self._authorized_json(
            "DELETE",
            f"/api/mo/terminals/{urllib.parse.quote(terminal_id, safe='')}{query}",
        )
        return body.get("stopped") is True

    def live_control_status(self, *, include_resources: bool = False) -> dict[str, Any]:
        """Return current bounded Live Control host advertisements."""
        query = "?resources=true" if include_resources else ""
        return self._authorized_json("GET", "/api/mo/live/status" + query)

    def prepare_live_control_session(self, host_id: str, lane: str) -> dict[str, Any]:
        """Lease one advertised host lane for this controller."""
        host_id = _id(host_id, 80)
        lane = str(lane or "").strip()
        if lane not in {"mo_session", "screen"}:
            raise EverywhereClientError("Live Control lane is invalid")
        return self._authorized_json(
            "POST",
            "/api/mo/live/session",
            {"host_id": host_id, "lane": lane},
        )

    def websocket_authority(self) -> tuple[str, str]:
        """Return a fresh hub origin/access token for one outbound WSS connect.

        The raw values stay in process memory. Reloading the atomic credential
        file under the refresh lock lets sibling clients reuse a newer rotation
        instead of replaying an older refresh parent.
        """
        with _credential_refresh_lock(self.config):
            try:
                latest = load_credentials(self.config)
            except EverywhereClientError:
                latest = self._credentials
            if latest.device_id == self._credentials.device_id and latest.access_expires_at > self._credentials.access_expires_at:
                self._credentials = latest
            if self._credentials.access_expires_at <= time.time() + 30.0:
                self._refresh_unlocked()
            return self._credentials.hub_url, self._credentials.access_token

    def refresh_websocket_authority(self) -> tuple[str, str]:
        """Rotate rejected WebSocket authority without trusting local wall time.

        The serving hub is the authority on access expiry. A workstation clock
        can lag behind it, so a rejected handshake must be able to refresh even
        when the locally stored expiry still appears to be in the future.
        """
        with _credential_refresh_lock(self.config):
            try:
                latest = load_credentials(self.config)
            except EverywhereClientError:
                latest = self._credentials
            if (
                latest.device_id == self._credentials.device_id
                and latest.access_expires_at > self._credentials.access_expires_at
            ):
                self._credentials = latest
            else:
                self._refresh_unlocked()
            return self._credentials.hub_url, self._credentials.access_token

    def _authorized_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            return self._json_request(method, path, payload, token=self._credentials.access_token)
        except _Unauthorized:
            self._refresh()
            return self._json_request(method, path, payload, token=self._credentials.access_token)

    def _refresh(self) -> None:
        with _credential_refresh_lock(self.config):
            try:
                latest = load_credentials(self.config)
            except EverywhereClientError:
                latest = self._credentials
            if latest.device_id == self._credentials.device_id and latest.access_expires_at > self._credentials.access_expires_at:
                self._credentials = latest
                return
            self._refresh_unlocked()

    def _refresh_unlocked(self) -> None:
        body = self._json_request(
            "POST",
            "/api/mo/refresh",
            {"refresh_token": self._credentials.refresh_token},
            token="",
        )
        device = body.get("device") if isinstance(body.get("device"), dict) else {}
        self._credentials = DeviceCredentials.from_dict({
            **body,
            "hub_url": self._credentials.hub_url,
            "device_id": device.get("device_id") or self._credentials.device_id,
        })
        save_credentials(self._credentials, self.config)

    def _json_request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        token: str,
    ) -> dict[str, Any]:
        url = self._credentials.hub_url.rstrip("/") + "/" + str(path or "").lstrip("/")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise _Unauthorized() from None
            raise EverywhereClientError(_hub_rejection(exc, "the request")) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise EverywhereClientError("Everywhere hub is unreachable") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise EverywhereClientError("Everywhere hub response is too large")
        try:
            body = json.loads(raw or b"{}")
        except (UnicodeError, json.JSONDecodeError):
            raise EverywhereClientError("Everywhere hub returned invalid JSON") from None
        if not isinstance(body, dict):
            raise EverywhereClientError("Everywhere hub returned an invalid response")
        return body


class FilesClient(ContinuityClient):
    """Authenticated client for the source-aware MO Files boundary."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        timeout: float = 15.0,
    ):
        super().__init__(files_client_config(config), timeout=timeout)

    def sources(self) -> list[dict[str, Any]]:
        body = self._authorized_json("GET", "/api/mo/files/sources")
        raw_sources = body.get("sources")
        if set(body) != {"sources"} or not isinstance(raw_sources, list):
            raise EverywhereClientError(
                "Everywhere hub returned invalid file sources"
            )
        sources: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw_sources:
            if not isinstance(item, dict) or set(item) != {
                "source_id",
                "host_key",
                "label",
                "kind",
                "online",
                "operations",
            }:
                raise EverywhereClientError(
                    "Everywhere hub returned invalid file sources"
                )
            source_id = str(item.get("source_id") or "")
            host_key = str(item.get("host_key") or "")
            label = str(item.get("label") or "")
            kind = str(item.get("kind") or "")
            operations = item.get("operations")
            valid_source = source_id == "hub" or (
                source_id.startswith("host-")
                and len(source_id) == 37
                and all(char in "0123456789abcdef" for char in source_id[5:])
            )
            valid_host_key = host_key == "hub" or (
                len(host_key) == 64
                and all(char in "0123456789abcdef" for char in host_key)
            )
            if (
                not valid_source
                or source_id in seen
                or not valid_host_key
                or not valid_file_capability_descriptor(
                    label=label,
                    kind=kind,
                    availability=item.get("online"),
                    operations=operations,
                    allowed_kinds=FILE_SOURCE_KINDS,
                )
            ):
                raise EverywhereClientError(
                    "Everywhere hub returned invalid file sources"
                )
            seen.add(source_id)
            sources.append(dict(item))
        if len(sources) > 50:
            raise EverywhereClientError(
                "Everywhere hub returned invalid file sources"
            )
        return sources

    def locations(self, source_id: str) -> list[dict[str, Any]]:
        result = self._file_get(
            "locations",
            "/api/mo/files/locations",
            {"source_id": _file_source_id(source_id)},
        )
        return list(result["locations"])

    def directory(
        self,
        source_id: str,
        location_id: str,
        path: str = "",
        *,
        limit: int = 150,
    ) -> dict[str, Any]:
        return self._file_get(
            "list",
            "/api/mo/files",
            {
                "source_id": _file_source_id(source_id),
                "location_id": _id(location_id, 80),
                "path": str(path or ""),
                "limit": max(1, min(500, int(limit))),
            },
        )

    def read_text(
        self, source_id: str, location_id: str, path: str
    ) -> dict[str, Any]:
        return self._file_get(
            "read_text",
            "/api/mo/files/text",
            {
                "source_id": _file_source_id(source_id),
                "location_id": _id(location_id, 80),
                "path": str(path or ""),
            },
        )

    def write_text(
        self,
        source_id: str,
        location_id: str,
        path: str,
        text: str,
        expected_sha256: str,
    ) -> dict[str, Any]:
        return self._file_json(
            "write_text",
            "PUT",
            "/api/mo/files/text",
            {
                "source_id": _file_source_id(source_id),
                "location_id": location_id,
                "path": path,
                "text": text,
                "expected_sha256": expected_sha256,
            },
        )

    def rename(
        self,
        source_id: str,
        location_id: str,
        path: str,
        name: str,
        expected_sha256: str = "",
    ) -> dict[str, Any]:
        return self._file_json(
            "rename",
            "POST",
            "/api/mo/files/rename",
            {
                "source_id": _file_source_id(source_id),
                "location_id": location_id,
                "path": path,
                "new_name": name,
                "expected_sha256": expected_sha256,
            },
        )

    def create_folder(
        self,
        source_id: str,
        location_id: str,
        parent_path: str,
        name: str,
    ) -> dict[str, Any]:
        return self._file_json(
            "create_folder",
            "POST",
            "/api/mo/files/folders",
            {
                "source_id": _file_source_id(source_id),
                "location_id": location_id,
                "parent_path": parent_path,
                "name": name,
            },
        )

    def organize(
        self,
        operation: str,
        source_id: str,
        location_id: str,
        path: str,
        target_location_id: str,
        target_directory: str,
        expected_sha256: str = "",
    ) -> dict[str, Any]:
        if operation not in {"copy", "move"}:
            raise EverywhereClientError("MO Files operation is invalid")
        return self._file_json(
            operation,
            "POST",
            f"/api/mo/files/{operation}",
            {
                "source_id": _file_source_id(source_id),
                "source_location_id": location_id,
                "path": path,
                "target_location_id": target_location_id,
                "target_directory": target_directory,
                "expected_sha256": expected_sha256,
            },
        )

    def delete(
        self,
        source_id: str,
        location_id: str,
        path: str,
        expected_sha256: str = "",
    ) -> dict[str, Any]:
        return self._file_json(
            "delete",
            "DELETE",
            "/api/mo/files",
            {
                "source_id": _file_source_id(source_id),
                "location_id": location_id,
                "path": path,
                "expected_sha256": expected_sha256,
            },
        )

    def trash(self, source_id: str, *, limit: int = 150) -> dict[str, Any]:
        return self._file_get(
            "trash",
            "/api/mo/files/trash",
            {
                "source_id": _file_source_id(source_id),
                "limit": max(1, min(500, int(limit))),
            },
        )

    def restore(self, source_id: str, trash_id: str) -> dict[str, Any]:
        return self._file_json(
            "restore",
            "POST",
            "/api/mo/files/restore",
            {
                "source_id": _file_source_id(source_id),
                "trash_id": trash_id,
            },
        )

    def send(
        self,
        source_id: str,
        location_id: str,
        path: str,
        target_device_id: str,
    ) -> dict[str, Any]:
        return self._file_json(
            "send",
            "POST",
            "/api/mo/files/send",
            {
                "source_id": _file_source_id(source_id),
                "location_id": location_id,
                "path": path,
                "target_device_id": _id(target_device_id, 80),
            },
        )

    def _file_get(
        self, operation: str, path: str, query: dict[str, Any]
    ) -> dict[str, Any]:
        encoded = urllib.parse.urlencode(query)
        return self._validated_file_response(
            operation, self._authorized_json("GET", f"{path}?{encoded}")
        )

    def _file_json(
        self,
        operation: str,
        method: str,
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return self._validated_file_response(
            operation, self._authorized_json(method, path, payload)
        )

    @staticmethod
    def _validated_file_response(
        operation: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            from .live_control import _file_response

            return _file_response(operation, body)
        except Exception as error:
            from .registry import RegistryError

            if isinstance(error, RegistryError):
                raise EverywhereClientError(
                    "Everywhere hub returned invalid MO Files data"
                ) from None
            raise


class TransferClient(ContinuityClient):
    """Authenticated resumable cargo client using the Everywhere authority."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        timeout: float = 15.0,
    ):
        super().__init__(transfer_client_config(config), timeout=timeout)

    def targets(self) -> list[dict[str, str]]:
        body = self._authorized_json("GET", "/api/mo/transfers/targets")
        raw_targets = body.get("targets")
        if not isinstance(raw_targets, list) or len(raw_targets) > 100:
            raise EverywhereClientError(
                "Everywhere hub returned invalid transfer targets"
            )
        targets: list[dict[str, str]] = []
        seen: set[str] = set()
        for item in raw_targets:
            if not isinstance(item, dict):
                raise EverywhereClientError(
                    "Everywhere hub returned invalid transfer targets"
                )
            device_id = str(item.get("device_id") or "")
            label = str(item.get("label") or "")
            kind = str(item.get("kind") or "")
            if (
                not device_id
                or device_id != _id(device_id, 80)
                or device_id in seen
                or not label
                or len(label) > 80
                or any(ord(character) < 32 or ord(character) == 127 for character in label)
                or kind not in {"hub", "device"}
            ):
                raise EverywhereClientError(
                    "Everywhere hub returned invalid transfer targets"
                )
            seen.add(device_id)
            targets.append(
                {"device_id": device_id, "label": label, "kind": kind}
            )
        return targets

    def transfers(
        self,
        *,
        direction: str = "all",
        states: tuple[str, ...] | list[str] = (),
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode(
            {
                "direction": str(direction or "all"),
                "states": ",".join(str(value) for value in states if value),
                "limit": max(1, min(200, int(limit))),
            }
        )
        body = self._authorized_json("GET", f"/api/mo/transfers?{query}")
        raw_transfers = body.get("transfers")
        if not isinstance(raw_transfers, list) or len(raw_transfers) > 200:
            raise EverywhereClientError(
                "Everywhere hub returned invalid transfer history"
            )
        if not all(isinstance(item, dict) for item in raw_transfers):
            raise EverywhereClientError(
                "Everywhere hub returned invalid transfer history"
            )
        transfers = [_validated_transfer(item) for item in raw_transfers]
        if any(
            self._credentials.device_id
            not in {item["sender_device_id"], item["target_device_id"]}
            for item in transfers
        ):
            raise EverywhereClientError(
                "Everywhere hub returned transfer history for another device"
            )
        return transfers

    def clear_terminal_history(self) -> int:
        body = self._authorized_json(
            "POST", "/api/mo/transfers/history/clear", {}
        )
        try:
            cleared = int(body.get("cleared"))
        except (TypeError, ValueError):
            raise EverywhereClientError(
                "Everywhere hub returned an invalid history result"
            ) from None
        if cleared < 0 or set(body) != {"cleared"}:
            raise EverywhereClientError(
                "Everywhere hub returned an invalid history result"
            )
        return cleared

    def send_file(
        self,
        path: str | Path,
        *,
        target_device_id: str,
        source_surface: str,
        destination: str = "catalog",
        path_hint: str = "",
        client_request_id: str = "",
        purpose: str = "cargo",
        on_progress: Any = None,
    ) -> dict[str, Any]:
        source = Path(path).expanduser().resolve(strict=True)
        if not source.is_file():
            raise EverywhereClientError("transfer source is not a file")
        size = int(source.stat().st_size)
        if size < 1 or size > MAX_FILE_TRANSFER_BYTES:
            raise EverywhereClientError("transfer source exceeds the file limit")
        digest = file_sha256(source)
        raw_request_id = str(client_request_id or "").strip()
        request_id = _id(raw_request_id, 96) or uuid.uuid4().hex
        if raw_request_id and request_id != raw_request_id:
            raise EverywhereClientError("client_request_id is invalid")
        body = self._authorized_json(
            "POST",
            "/api/mo/transfers",
            {
                "target_device_id": target_device_id,
                "source_surface": source_surface,
                "purpose": purpose,
                "destination": destination,
                "name": source.name,
                "bytes": size,
                "sha256": digest,
                "path_hint": path_hint,
                "client_request_id": request_id,
            },
        )
        transfer = _transfer_body(body)
        if (
            transfer["sender_device_id"] != self._credentials.device_id
            or transfer["client_request_id"] != request_id
            or transfer["target_device_id"] != target_device_id
            or transfer["source_surface"] != source_surface
            or transfer["purpose"] != purpose
            or transfer["destination"] != destination
            or transfer["name"] != source.name
            or transfer["bytes"] != size
            or transfer["sha256"] != digest
            or transfer["path_hint"] != path_hint
        ):
            raise EverywhereClientError(
                "Everywhere hub returned a different transfer"
            )
        created_transfer = transfer
        if str(transfer.get("state") or "") in {
            "offered", "claimed", "delivered", "done"
        }:
            return transfer
        if str(transfer.get("state") or "") in {
            "failed", "cancelled", "expired"
        }:
            raise EverywhereClientError(
                "transfer request is already "
                + str(transfer.get("state") or "closed")
            )
        received = _expand_ranges(body.get("received_ranges"))
        transfer_id = str(transfer["transfer_id"])
        chunk_bytes = int(transfer["chunk_bytes"])
        chunk_count = int(transfer["chunk_count"])
        with source.open("rb") as handle:
            for index in range(chunk_count):
                content = handle.read(chunk_bytes)
                if index not in received:
                    raw, _headers = self._authorized_bytes(
                        "PUT",
                        f"/api/mo/transfers/{urllib.parse.quote(transfer_id, safe='')}/chunks/{index}",
                        content,
                        headers={
                            "Content-Type": "application/octet-stream",
                            "X-MO-Chunk-SHA256": hashlib.sha256(content).hexdigest(),
                        },
                    )
                    try:
                        uploaded = _transfer_body(
                            json.loads(raw.decode("utf-8"))
                        )
                    except (
                        UnicodeDecodeError,
                        json.JSONDecodeError,
                        AttributeError,
                        TypeError,
                        ValueError,
                    ):
                        raise EverywhereClientError(
                            "Everywhere hub returned an invalid transfer"
                        ) from None
                    if (
                        not _same_transfer_identity(uploaded, created_transfer)
                        or uploaded["state"] != "uploading"
                    ):
                        raise EverywhereClientError(
                            "Everywhere hub returned a different transfer"
                        )
                if callable(on_progress):
                    on_progress(min(size, (index + 1) * chunk_bytes), size)
        completed = self._authorized_json(
            "POST",
            f"/api/mo/transfers/{urllib.parse.quote(transfer_id, safe='')}/complete",
            {},
        )
        transfer = _transfer_body(completed)
        if (
            not _same_transfer_identity(transfer, created_transfer)
            or transfer["state"] not in {"offered", "claimed", "delivered", "done"}
        ):
            raise EverywhereClientError(
                "Everywhere hub returned a different transfer"
            )
        return transfer

    def cancel(self, transfer_id: str) -> dict[str, Any]:
        requested_id = _transfer_identifier(transfer_id)
        transfer = _transfer_body(
            self._authorized_json(
                "DELETE",
                f"/api/mo/transfers/{urllib.parse.quote(requested_id, safe='')}",
            )
        )
        if (
            transfer["transfer_id"] != requested_id
            or self._credentials.device_id not in {
                transfer["sender_device_id"],
                transfer["target_device_id"],
            }
        ):
            raise EverywhereClientError(
                "Everywhere hub returned a transfer for another device"
            )
        return transfer

    def receive_available(
        self,
        *,
        limit: int = 20,
        on_progress: Any = None,
    ) -> list[dict[str, Any]]:
        settings = (self.config.get("file_transfer") or {})
        if not isinstance(settings, dict) or settings.get("enabled") is not True:
            return []
        if settings.get("auto_accept", True) is not True:
            return []
        received: list[dict[str, Any]] = []
        for transfer in self.transfers(
            direction="incoming",
            states=("offered", "claimed", "delivered"),
            limit=limit,
        ):
            if (
                str(transfer.get("destination")) == "named_path"
                and settings.get("auto_accept_named_paths") is not True
            ):
                continue
            received.append(
                self.receive_one(
                    transfer,
                    destination_path=(
                        str(transfer.get("path_hint") or "")
                        if str(transfer.get("destination")) == "named_path"
                        else None
                    ),
                    on_progress=on_progress,
                )
            )
        return received

    def receive_one(
        self,
        transfer: dict[str, Any] | str,
        *,
        destination_path: str | Path | None = None,
        allowed_roots: list[str | Path] | tuple[str | Path, ...] | None = None,
        on_progress: Any = None,
    ) -> dict[str, Any]:
        transfer_id = _transfer_identifier(
            transfer if isinstance(transfer, str) else transfer.get("transfer_id")
        )
        with _transfer_receive_lock(self.config, transfer_id):
            return self._receive_one(
                transfer,
                destination_path=destination_path,
                allowed_roots=allowed_roots,
                on_progress=on_progress,
            )

    def _receive_one(
        self,
        transfer: dict[str, Any] | str,
        *,
        destination_path: str | Path | None = None,
        allowed_roots: list[str | Path] | tuple[str | Path, ...] | None = None,
        on_progress: Any = None,
    ) -> dict[str, Any]:
        provided_record = not isinstance(transfer, str)
        if isinstance(transfer, str):
            requested_id = _transfer_identifier(transfer)
            transfer = _transfer_body(
                self._authorized_json(
                    "GET",
                    f"/api/mo/transfers/{urllib.parse.quote(requested_id, safe='')}",
                )
            )
            if transfer["transfer_id"] != requested_id:
                raise EverywhereClientError(
                    "Everywhere hub returned a different transfer"
                )
        item = _validated_transfer(transfer)
        if provided_record:
            current = _transfer_body(
                self._authorized_json(
                    "GET",
                    f"/api/mo/transfers/{urllib.parse.quote(item['transfer_id'], safe='')}",
                )
            )
            if not _same_transfer_identity(current, item):
                raise EverywhereClientError(
                    "Everywhere hub returned a different transfer"
                )
            item = current
        if item["target_device_id"] != self._credentials.device_id:
            raise EverywhereClientError(
                "Everywhere hub returned a transfer for another device"
            )
        transfer_id = str(item.get("transfer_id") or "")
        digest = str(item.get("sha256") or "")
        size = int(item.get("bytes") or 0)
        chunk_bytes = int(item.get("chunk_bytes") or 0)
        chunk_count = int(item.get("chunk_count") or 0)
        if not transfer_id or size < 1 or chunk_bytes < 1 or chunk_count < 1:
            raise EverywhereClientError("Everywhere hub returned an invalid transfer")
        if (
            str(item.get("destination") or "catalog") == "named_path"
            and destination_path is None
        ):
            raise EverywhereClientError(
                "named-path transfer requires explicit target confirmation"
            )
        if (
            str(item.get("destination") or "catalog") == "catalog"
            and destination_path is not None
        ):
            raise EverywhereClientError(
                "catalog transfer does not accept a named destination"
            )
        if str(item.get("state")) == "offered":
            accepted = _transfer_body(
                self._authorized_json(
                    "POST",
                    f"/api/mo/transfers/{urllib.parse.quote(transfer_id, safe='')}/accept",
                    {},
                )
            )
            if (
                accepted["target_device_id"] != self._credentials.device_id
                or not _same_transfer_identity(accepted, item)
            ):
                raise EverywhereClientError(
                    "Everywhere hub returned a transfer for another device"
                )
            item = accepted
        if item["state"] == "done":
            return {**item, "local_path": ""}
        receive_root = Path(
            resolve_state_path("memory/transfers/incoming", self.config)
        ).resolve(strict=False)
        receive_root.mkdir(parents=True, exist_ok=True)
        staged = receive_root / f"{_id(transfer_id, 80)}.part"
        receipt_marker = receive_root / f"{_id(transfer_id, 80)}.receipt.json"
        requested = None
        if destination_path:
            try:
                requested = named_destination_path(
                    destination_path,
                    default_project_roots(self.config)
                    if allowed_roots is None
                    else allowed_roots,
                )
            except TransferError as exc:
                raise EverywhereClientError(str(exc)) from None
        try:
            marker = json.loads(receipt_marker.read_text(encoding="utf-8"))
            prior_destination = Path(str(marker.get("path") or "")).resolve(strict=False)
            prior_cataloged = marker.get("cataloged") is True
            prior_digest = str(marker.get("sha256") or "").lower()
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            prior_destination = Path()
            prior_cataloged = False
            prior_digest = ""
        reserved_destination: Path | None = None
        if str(prior_destination) and prior_digest == digest:
            if requested is not None:
                try:
                    prior_allowed = named_destination_path(
                        prior_destination,
                        default_project_roots(self.config)
                        if allowed_roots is None
                        else allowed_roots,
                    )
                    reserved_destination = (
                        prior_destination
                        if prior_allowed == prior_destination
                        else None
                    )
                except TransferError:
                    reserved_destination = None
            else:
                try:
                    prior_destination.relative_to(
                        attachment_home(self.config).resolve(strict=False)
                    )
                    reserved_destination = prior_destination
                except ValueError:
                    reserved_destination = None
        if (
            reserved_destination is not None
            and reserved_destination.is_file()
            and int(reserved_destination.stat().st_size) == size
            and file_sha256(reserved_destination) == digest
        ):
            staged.unlink(missing_ok=True)
            if destination_path is None and not prior_cataloged:
                record_attachment(
                    self.config,
                    reserved_destination,
                    origin=f"file_transfer:{item.get('source_surface') or 'remote'}",
                    attachment_id=transfer_id,
                )
                atomic_write_json(
                    receipt_marker,
                    {
                        "path": str(reserved_destination),
                        "sha256": digest,
                        "cataloged": True,
                    },
                    ensure_ascii=False,
                )
            _queue_received_transfer_notice(
                self.config,
                transfer_id=transfer_id,
                name=str(item.get("name") or "attachment"),
                size_bytes=size,
            )
            done = _transfer_body(
                self._authorized_json(
                    "POST",
                    f"/api/mo/transfers/{urllib.parse.quote(transfer_id, safe='')}/receipt",
                    {"sha256": digest},
                )
            )
            if (
                not _same_transfer_identity(done, item)
                or done["state"] != "done"
            ):
                raise EverywhereClientError(
                    "Everywhere hub returned a different transfer"
                )
            receipt_marker.unlink(missing_ok=True)
            return {
                **done,
                "local_path": str(reserved_destination),
            }
        if item["state"] not in {"claimed", "delivered"}:
            raise EverywhereClientError("transfer is no longer available to receive")
        if reserved_destination is not None and reserved_destination.exists():
            if (
                not reserved_destination.is_file()
                or reserved_destination.stat().st_size != 0
            ):
                raise EverywhereClientError(
                    "reserved transfer destination was changed"
                )
        try:
            current = int(staged.stat().st_size)
        except OSError:
            current = 0
        if current < 0 or current > size:
            current = 0
        start_index = current // chunk_bytes
        aligned = start_index * chunk_bytes
        with staged.open("r+b" if staged.exists() else "w+b") as handle:
            handle.truncate(aligned)
            handle.seek(aligned)
            for index in range(start_index, chunk_count):
                content, headers = self._authorized_bytes(
                    "GET",
                    f"/api/mo/transfers/{urllib.parse.quote(transfer_id, safe='')}/content/{index}",
                    None,
                    max_bytes=chunk_bytes,
                )
                header_digest = str(headers.get("x-mo-chunk-sha256") or "").lower()
                expected_chunk = (
                    size - index * chunk_bytes
                    if index == chunk_count - 1
                    else chunk_bytes
                )
                if (
                    len(content) != expected_chunk
                    or hashlib.sha256(content).hexdigest() != header_digest
                ):
                    raise EverywhereClientError("transfer chunk digest mismatch")
                handle.write(content)
                handle.flush()
                if callable(on_progress):
                    on_progress(min(size, handle.tell()), size)
            os.fsync(handle.fileno())
        if staged.stat().st_size != size or file_sha256(staged) != digest:
            staged.unlink(missing_ok=True)
            raise EverywhereClientError("transfer whole-file digest mismatch")
        if reserved_destination is not None:
            destination = reserved_destination
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                try:
                    destination.open("xb").close()
                except FileExistsError:
                    raise EverywhereClientError(
                        "reserved transfer destination was changed"
                    ) from None
        elif requested is None:
            while True:
                destination = unique_attachment_path(
                    self.config, str(item.get("name") or "attachment")
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    destination.open("xb").close()
                    break
                except FileExistsError:
                    continue
        else:
            destination = requested.resolve(strict=False)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                destination.open("xb").close()
            except FileExistsError:
                raise EverywhereClientError(
                    "transfer destination already exists"
                ) from None
        destination.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(
            receipt_marker,
            {
                "path": str(destination.resolve(strict=False)),
                "sha256": digest,
                "cataloged": False,
            },
            ensure_ascii=False,
        )
        _install_transfer_file(
            staged,
            destination,
            transfer_id,
            expected_bytes=size,
            expected_sha256=digest,
        )
        if requested is None:
            record_attachment(
                self.config,
                destination,
                origin=f"file_transfer:{item.get('source_surface') or 'remote'}",
                attachment_id=transfer_id,
            )
            atomic_write_json(
                receipt_marker,
                {
                    "path": str(destination.resolve(strict=False)),
                    "sha256": digest,
                    "cataloged": True,
                },
                ensure_ascii=False,
            )
        _queue_received_transfer_notice(
            self.config,
            transfer_id=transfer_id,
            name=str(item.get("name") or "attachment"),
            size_bytes=size,
        )
        try:
            done = _transfer_body(
                self._authorized_json(
                    "POST",
                    f"/api/mo/transfers/{urllib.parse.quote(transfer_id, safe='')}/receipt",
                    {"sha256": digest},
                )
            )
            if (
                not _same_transfer_identity(done, item)
                or done["state"] != "done"
            ):
                raise EverywhereClientError(
                    "Everywhere hub returned a different transfer"
                )
        except Exception:
            # Target custody has begun. Keep the verified local file and let a
            # later status/receipt retry release the hub copy.
            raise
        receipt_marker.unlink(missing_ok=True)
        return {**done, "local_path": str(destination.resolve(strict=False))}

    def _authorized_bytes(
        self,
        method: str,
        path: str,
        data: bytes | None,
        *,
        headers: dict[str, str] | None = None,
        max_bytes: int = MAX_RESPONSE_BYTES,
    ) -> tuple[bytes, Any]:
        try:
            return self._bytes_request(
                method,
                path,
                data,
                token=self._credentials.access_token,
                headers=headers,
                max_bytes=max_bytes,
            )
        except _Unauthorized:
            self._refresh()
            return self._bytes_request(
                method,
                path,
                data,
                token=self._credentials.access_token,
                headers=headers,
                max_bytes=max_bytes,
            )

    def _bytes_request(
        self,
        method: str,
        path: str,
        data: bytes | None,
        *,
        token: str,
        headers: dict[str, str] | None,
        max_bytes: int,
    ) -> tuple[bytes, Any]:
        url = self._credentials.hub_url.rstrip("/") + "/" + str(path).lstrip("/")
        request_headers = {"Accept": "application/octet-stream"}
        request_headers.update(headers or {})
        if token:
            request_headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            url,
            data=data,
            headers=request_headers,
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(max(1, int(max_bytes)) + 1)
                response_headers = response.headers
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise _Unauthorized() from None
            raise EverywhereClientError(
                _hub_rejection(exc, "the request")
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise EverywhereClientError("Everywhere hub is unreachable") from None
        if len(raw) > max_bytes:
            raise EverywhereClientError("Everywhere hub response is too large")
        return raw, response_headers


@contextmanager
def _transfer_receive_lock(config: dict[str, Any], transfer_id: str):
    """Serialize one target-device receipt across local surfaces/processes."""
    identity_source = (
        f"{credential_path(config).resolve(strict=False)}"
        f"\0receive\0{_transfer_identifier(transfer_id)}"
    )
    with transfer_operation_lock(config, identity=identity_source):
        yield


@contextmanager
def _credential_refresh_lock(config: dict[str, Any]):
    """Serialize one rotating credential lineage across local MO processes."""
    identity = hashlib.sha256(str(credential_path(config)).encode("utf-8", errors="replace")).hexdigest()[:16]
    lock_path = Path(resolve_state_path(f"run/everywhere-refresh-{identity}.lock", config))
    with file_byte_lock(lock_path, _REFRESH_THREAD_LOCK):
        yield


class _Unauthorized(EverywhereClientError):
    def __init__(self) -> None:
        super().__init__("Everywhere device authorization was rejected. Pair this device to the serving hub again.")


def _hub_url(value: Any) -> str:
    raw = str(value or "").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise EverywhereClientError("Everywhere hub URL must be an origin without credentials, path, query, or fragment")
    loopback = (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise EverywhereClientError("Everywhere hub must use HTTPS or loopback HTTP")
    if not parsed.hostname:
        raise EverywhereClientError("Everywhere hub URL is invalid")
    return raw


def _token(value: Any) -> str:
    token = str(value or "").strip()
    return token if 24 <= len(token) <= 256 else ""


def _id(value: Any, limit: int) -> str:
    return "".join(
        ch
        for ch in str(value or "").strip()
        if ch.isascii() and (ch.isalnum() or ch in "-_.")
    )[:limit]


def _exact_hex_id(value: Any, label: str) -> str:
    text = str(value or "")
    if len(text) != 32 or any(char not in "0123456789abcdef" for char in text):
        raise EverywhereClientError(f"{label} is invalid")
    return text


def _file_source_id(value: Any) -> str:
    source_id = str(value or "").strip()
    if source_id == "hub":
        return source_id
    if (
        source_id.startswith("host-")
        and len(source_id) == 37
        and all(char in "0123456789abcdef" for char in source_id[5:])
    ):
        return source_id
    raise EverywhereClientError("MO Files source is invalid")


def _transfer_identifier(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw or raw != _id(raw, 80):
        raise EverywhereClientError("transfer ID is invalid")
    return raw


def _install_transfer_file(
    staged: Path,
    destination: Path,
    transfer_id: str,
    *,
    expected_bytes: int,
    expected_sha256: str,
) -> None:
    """Atomically install verified bytes, including across filesystem volumes."""
    if (
        staged.stat().st_size != int(expected_bytes)
        or file_sha256(staged) != str(expected_sha256).lower()
    ):
        raise EverywhereClientError("transfer staging bytes changed before install")
    if destination.exists() and (
        not destination.is_file() or destination.stat().st_size != 0
    ):
        raise EverywhereClientError("reserved transfer destination was changed")
    try:
        os.replace(staged, destination)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV and getattr(exc, "winerror", None) != 17:
            raise
    local_stage = destination.with_name(
        f".{destination.name}.{_id(transfer_id, 80)}.part"
    )
    local_stage.unlink(missing_ok=True)
    try:
        with staged.open("rb") as source, local_stage.open("xb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
        if (
            local_stage.stat().st_size != int(expected_bytes)
            or file_sha256(local_stage) != str(expected_sha256).lower()
        ):
            raise EverywhereClientError(
                "transfer destination copy failed verification"
            )
        os.replace(local_stage, destination)
        staged.unlink(missing_ok=True)
    except Exception:
        local_stage.unlink(missing_ok=True)
        raise


def _queue_received_transfer_notice(
    config: dict[str, Any],
    *,
    transfer_id: str,
    name: str,
    size_bytes: int,
) -> None:
    """Persist the exactly-once notice before releasing hub custody."""
    from core.transfer.presence import queue_transfer_notice

    queue_transfer_notice(
        config,
        transfer_id=transfer_id,
        name=name,
        size_bytes=size_bytes,
    )


def _transfer_body(body: dict[str, Any]) -> dict[str, Any]:
    transfer = body.get("transfer") if isinstance(body.get("transfer"), dict) else {}
    required = {
        "transfer_id",
        "client_request_id",
        "sender_device_id",
        "target_device_id",
        "source_surface",
        "purpose",
        "destination",
        "name",
        "bytes",
        "sha256",
        "chunk_bytes",
        "chunk_count",
        "uploaded_bytes",
        "state",
        "created_at",
        "updated_at",
        "expires_at",
    }
    if not required.issubset(transfer):
        raise EverywhereClientError("Everywhere hub returned an invalid transfer")
    return _validated_transfer(transfer)


def _same_transfer_identity(
    current: dict[str, Any],
    expected: dict[str, Any],
) -> bool:
    immutable = (
        "transfer_id",
        "client_request_id",
        "sender_device_id",
        "target_device_id",
        "source_surface",
        "purpose",
        "destination",
        "name",
        "bytes",
        "sha256",
        "chunk_bytes",
        "chunk_count",
        "path_hint",
    )
    return all(current.get(key) == expected.get(key) for key in immutable)


def _validated_transfer(raw: dict[str, Any]) -> dict[str, Any]:
    """Fail closed on transfer metadata before it can choose I/O bounds or paths."""
    transfer = dict(raw)
    try:
        transfer_id = str(transfer["transfer_id"])
        client_request_id = str(transfer["client_request_id"])
        sender = str(transfer["sender_device_id"])
        target = str(transfer["target_device_id"])
        surface = str(transfer["source_surface"])
        purpose = str(transfer["purpose"])
        destination = str(transfer["destination"])
        name = str(transfer["name"])
        digest = str(transfer["sha256"])
        size = int(transfer["bytes"])
        chunk_bytes = int(transfer["chunk_bytes"])
        chunk_count = int(transfer["chunk_count"])
        uploaded_bytes = int(transfer.get("uploaded_bytes") or 0)
        state = str(transfer["state"])
        created_at = float(transfer.get("created_at") or 0.0)
        updated_at = float(transfer.get("updated_at") or 0.0)
        expires_at = float(transfer.get("expires_at") or 0.0)
        path_hint = str(transfer.get("path_hint") or "")
        failure = str(transfer.get("failure") or "")
    except (KeyError, TypeError, ValueError):
        raise EverywhereClientError(
            "Everywhere hub returned an invalid transfer"
        ) from None
    identifiers = (
        (transfer_id, 80),
        (sender, 80),
        (target, 80),
        (surface, 40),
    )
    if any(not value or value != _id(value, limit) for value, limit in identifiers):
        raise EverywhereClientError("Everywhere hub returned an invalid transfer")
    if (
        (
            client_request_id
            and client_request_id != _id(client_request_id, 96)
        )
        or (purpose == "cargo" and not client_request_id)
        or
        purpose not in {"cargo", "turn_context"}
        or destination not in {"catalog", "named_path"}
        or not name
        or len(name) > 180
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
        or len(path_hint) > 512
        or any(ord(character) < 32 or ord(character) == 127 for character in path_hint)
        or len(failure) > 240
        or any(
            (ord(character) < 32 and character not in "\t\n\r")
            or ord(character) == 127
            for character in failure
        )
    ):
        raise EverywhereClientError("Everywhere hub returned an invalid transfer")
    if (
        (destination == "named_path" and not path_hint)
        or (destination == "catalog" and path_hint)
    ):
        raise EverywhereClientError("Everywhere hub returned an invalid transfer")
    integer_fields = (
        transfer.get("bytes"),
        transfer.get("chunk_bytes"),
        transfer.get("chunk_count"),
        transfer.get("uploaded_bytes"),
    )
    timestamp_fields = (
        transfer.get("created_at"),
        transfer.get("updated_at"),
        transfer.get("expires_at"),
    )
    if (
        any(not isinstance(value, int) or isinstance(value, bool) for value in integer_fields)
        or any(
            not isinstance(value, (int, float)) or isinstance(value, bool)
            for value in timestamp_fields
        )
        or size < 1
        or size > MAX_FILE_TRANSFER_BYTES
        or len(digest) != 64
        or digest != digest.lower()
        or any(character not in "0123456789abcdef" for character in digest)
        or chunk_bytes < MIN_FILE_TRANSFER_CHUNK_BYTES
        or chunk_bytes > MAX_FILE_TRANSFER_CHUNK_BYTES
        or chunk_count != (size + chunk_bytes - 1) // chunk_bytes
        or uploaded_bytes < 0
        or uploaded_bytes > size
        or state not in FILE_TRANSFER_STATES
        or not all(
            math.isfinite(value) and value >= 0.0
            for value in (created_at, updated_at, expires_at)
        )
        or updated_at < created_at
        or (
            state in {"created", "uploading", "offered", "claimed", "delivered"}
            and expires_at < updated_at
        )
        or (
            state in {"done", "failed", "cancelled", "expired"}
            and expires_at != 0.0
        )
    ):
        raise EverywhereClientError("Everywhere hub returned an invalid transfer")
    transfer.update(
        {
            "transfer_id": transfer_id,
            "client_request_id": client_request_id,
            "sender_device_id": sender,
            "target_device_id": target,
            "source_surface": surface,
            "purpose": purpose,
            "destination": destination,
            "name": name,
            "bytes": size,
            "sha256": digest,
            "chunk_bytes": chunk_bytes,
            "chunk_count": chunk_count,
            "uploaded_bytes": uploaded_bytes,
            "state": state,
            "created_at": created_at,
            "updated_at": updated_at,
            "expires_at": expires_at,
            "path_hint": path_hint,
            "failure": failure,
        }
    )
    return transfer


def _expand_ranges(value: Any) -> set[int]:
    result: set[int] = set()
    if not isinstance(value, list):
        return result
    for item in value:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not isinstance(item[0], int)
            or isinstance(item[0], bool)
            or not isinstance(item[1], int)
            or isinstance(item[1], bool)
        ):
            continue
        start, end = item
        if 0 <= start <= end < MAX_FILE_TRANSFER_CHUNKS:
            result.update(range(start, end + 1))
    return result
