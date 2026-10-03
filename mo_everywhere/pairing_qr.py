"""Trusted-terminal QR presentation for the existing one-use pairing grant."""
from __future__ import annotations

import ipaddress
import atexit
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .registry import DEVICE_CAPABILITIES, DEVICE_SCOPES, DeviceRegistry


PAIRING_PAYLOAD_TYPE = "mo_pair"
PAIRING_PAYLOAD_VERSION = 1
MAX_ORIGIN_CHARS = 512
MAX_PAIRING_PAYLOAD_BYTES = 1024
ANDROID_PAIRING_SCOPES = (
    "attachment_upload",
    "conversation_read",
    "conversation_write",
    "continuity_read",
    "continuity_sync",
    "file_browse",
    "file_manage",
    "file_transfer",
    "remote_control",
)
ANDROID_PHONE_CONTROL_SCOPES = ANDROID_PAIRING_SCOPES + ("remote_host",)


class PairingQrError(ValueError):
    """The trusted terminal cannot safely create a pairing QR."""


def pairing_origin(config: dict[str, Any]) -> str:
    block = config.get("consistent_everywhere") if isinstance(config.get("consistent_everywhere"), dict) else {}
    api = block.get("api") if isinstance(block.get("api"), dict) else {}
    return normalize_https_origin(api.get("public_url"))


def normalize_https_origin(value: Any) -> str:
    """Return a normalized HTTPS origin with no path, user info, query, or fragment."""
    raw = str(value or "").strip()
    if not raw:
        raise PairingQrError("consistent_everywhere.api.public_url is required for QR pairing")
    if (
        len(raw) > MAX_ORIGIN_CHARS
        or any(ch in raw for ch in "\\?#\r\n\t")
        or any(ch.isspace() for ch in raw)
    ):
        raise PairingQrError("QR pairing public_url must be one bounded HTTPS origin")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        raise PairingQrError("QR pairing public_url is invalid") from None
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise PairingQrError("QR pairing requires a verified HTTPS public_url")
    if port is not None and port < 1:
        raise PairingQrError("QR pairing public_url port is invalid")
    if parsed.username is not None or parsed.password is not None:
        raise PairingQrError("QR pairing public_url must not contain user information")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise PairingQrError("QR pairing public_url must not contain a path, query, or fragment")
    host = _normalized_host(parsed.hostname)
    authority = host if port in {None, 443} else f"{host}:{port}"
    origin = f"https://{authority}"
    if len(origin) > MAX_ORIGIN_CHARS:
        raise PairingQrError("QR pairing public_url is too long")
    return origin


def build_pairing_payload(
    *,
    hub: Any,
    code: Any,
    expires_at: Any,
    capability: Any,
    scopes: Any = (),
) -> dict[str, Any]:
    origin = normalize_https_origin(hub)
    clean_code = str(code or "").strip()
    if not clean_code.startswith("mo_pair_") or not (24 <= len(clean_code) <= 256):
        raise PairingQrError("pairing code is invalid")
    if any(not (ch.isascii() and (ch.isalnum() or ch in "-_")) for ch in clean_code):
        raise PairingQrError("pairing code is invalid")
    try:
        expiry = int(float(expires_at))
    except (TypeError, ValueError, OverflowError):
        raise PairingQrError("pairing expiry is invalid") from None
    if expiry <= 0:
        raise PairingQrError("pairing expiry is invalid")
    clean_capability = str(capability or "").strip().lower()
    if clean_capability not in DEVICE_CAPABILITIES:
        raise PairingQrError("pairing capability is invalid")
    if isinstance(scopes, str):
        scopes = [scopes]
    try:
        clean_scopes = sorted({str(scope or "").strip().lower() for scope in scopes})
    except TypeError:
        raise PairingQrError("pairing scopes are invalid") from None
    if any(scope not in DEVICE_SCOPES for scope in clean_scopes):
        raise PairingQrError("pairing scopes are invalid")
    payload = {
        "type": PAIRING_PAYLOAD_TYPE,
        "version": PAIRING_PAYLOAD_VERSION,
        "hub": origin,
        "code": clean_code,
        "expires_at": expiry,
        "capability": clean_capability,
        "scopes": clean_scopes,
    }
    if len(pairing_payload_json(payload).encode("utf-8")) > MAX_PAIRING_PAYLOAD_BYTES:
        raise PairingQrError("pairing QR payload is too large")
    return payload


def pairing_payload_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def issue_pairing_payload(
    registry: DeviceRegistry,
    *,
    origin: str,
    capability: str,
    scopes: list[str] | tuple[str, ...],
    ttl_seconds: int = 300,
) -> dict[str, Any]:
    """Create one registry grant and return its strict QR payload."""
    clean_origin = normalize_https_origin(origin)
    grant = registry.create_pairing_grant(
        capability,
        ttl_seconds=ttl_seconds,
        scopes=scopes,
    )
    return build_pairing_payload(
        hub=clean_origin,
        code=grant.code,
        expires_at=grant.expires_at,
        capability=grant.capability,
        scopes=grant.scopes,
    )


def ensure_qr_support() -> None:
    _segno()


def create_android_pairing_image(config: dict[str, Any], *, phone_control: bool = False) -> tuple[Path, int]:
    """Reuse the coordinator grant and private, expiring operator image owner."""
    from core.state.paths import resolve_state_path
    from .client import ContinuityClient

    if not isinstance(phone_control, bool):
        raise PairingQrError("phone_control must be true or false")
    ensure_qr_support()
    payload = ContinuityClient(config).create_android_pairing(phone_control=phone_control)
    now = time.time()
    expiry = int(payload["expires_at"])
    if expiry <= now:
        raise PairingQrError("pairing grant has expired; request a new QR")
    qr_dir = Path(resolve_state_path("run/everywhere-pairing", config)).resolve(strict=False)
    qr_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(qr_dir, 0o700)
    except OSError:
        pass
    for stale in qr_dir.glob("android-pairing-*.png"):
        try:
            if now - stale.stat().st_mtime > 15 * 60:
                stale.unlink(missing_ok=True)
        except OSError:
            pass
    target = render_qr_png(payload, qr_dir / f"android-pairing-{uuid.uuid4().hex}.png")
    atexit.register(target.unlink, missing_ok=True)
    cleanup = threading.Timer(max(1.0, min(905.0, expiry - now + 2.0)), lambda: target.unlink(missing_ok=True))
    cleanup.daemon = True
    cleanup.start()
    return target, expiry


def render_terminal_qr(payload: dict[str, Any]) -> str:
    """Render a quiet-zone QR with Unicode half blocks and no ANSI/log output."""
    encoded = pairing_payload_json(payload)
    if len(encoded.encode("utf-8")) > MAX_PAIRING_PAYLOAD_BYTES:
        raise PairingQrError("pairing QR payload is too large")
    try:
        qr = _segno().make(encoded, error="m", micro=False)
        rows = [tuple(bool(cell) for cell in row) for row in qr.matrix_iter(scale=1, border=4)]
    except PairingQrError:
        raise
    except Exception:
        raise PairingQrError("pairing QR could not be rendered") from None
    if len(rows) % 2:
        rows.append(tuple(False for _ in rows[0]))
    glyph = {(False, False): " ", (True, False): "▀", (False, True): "▄", (True, True): "█"}
    lines = [
        "".join(glyph[(top, bottom)] for top, bottom in zip(rows[index], rows[index + 1]))
        for index in range(0, len(rows), 2)
    ]
    return "\n".join(lines)


def render_qr_png(payload: dict[str, Any], path: str | Path) -> Path:
    """Write a scan-safe PNG without exposing the encoded payload as text."""
    encoded = pairing_payload_json(payload)
    if len(encoded.encode("utf-8")) > MAX_PAIRING_PAYLOAD_BYTES:
        raise PairingQrError("pairing QR payload is too large")
    target = Path(path).expanduser().resolve(strict=False)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        qr = _segno().make(encoded, error="m", micro=False)
        qr.save(str(target), kind="png", scale=4, border=4, dark="#071114", light="#ffffff")
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    except PairingQrError:
        raise
    except Exception:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        raise PairingQrError("pairing QR could not be rendered") from None
    return target


def _segno() -> Any:
    try:
        import segno
    except ImportError:
        raise PairingQrError(
            "terminal QR support is unavailable; install requirements-everywhere.txt"
        ) from None
    return segno


def _normalized_host(value: str) -> str:
    if "%" in value:
        raise PairingQrError("QR pairing public_url host is invalid")
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        try:
            host = value.encode("idna").decode("ascii").lower()
        except UnicodeError:
            raise PairingQrError("QR pairing public_url host is invalid") from None
        labels = host.split(".")
        if (
            len(host) > 253
            or any(not label or len(label) > 63 for label in labels)
            or any(label.startswith("-") or label.endswith("-") for label in labels)
            or any(not (ch.isalnum() or ch == "-") for label in labels for ch in label)
        ):
            raise PairingQrError("QR pairing public_url host is invalid")
        return host
    return f"[{address.compressed}]" if address.version == 6 else address.compressed
