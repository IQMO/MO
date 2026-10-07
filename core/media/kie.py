"""Kie HTTP boundary: fixed credential destination, bounded public downloads.

No SDK, provider payload logging, automatic billable retries, callback server or
third-party upload fallback. Redirected downloads never carry API credentials.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

from .catalog import settings

API_ORIGIN = "https://api.kie.ai"
MAX_JSON_BYTES = 2 * 1024 * 1024


def check_network(config: dict, url: str) -> None:
    """Apply the shared network policy at every outbound boundary, including UI."""
    from core.tooling.sandbox import _guard_web_tools

    if _guard_web_tools("web_fetch", {"url": url}, (config or {}).get("sandbox", {})):
        raise ProviderError("Media network access is blocked by the configured host policy.")


def require_credential(config: dict) -> str:
    from core.state.secrets import resolve_secret

    key_name = str(settings(config).get("api_key_env") or "KIE_API_KEY")
    key = resolve_secret(key_name, config=config, service="providers")
    if not key:
        raise ProviderError("Kie credential is missing. Add it through MO's provider credential setup.")
    return key


class ProviderError(RuntimeError):
    """Value-free error; the provider's raw response can contain private inputs."""

    def __init__(self, message: str, *, uncertain: bool = False):
        super().__init__(message)
        self.uncertain = uncertain


class _PublicHTTPS(http.client.HTTPSConnection):
    def connect(self):
        # Pin the validated address at the actual connect boundary; a second DNS
        # lookup must not turn a checked public download into a local request.
        addresses = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
            raise ValueError("Media transfer requires a public HTTPS destination.")
        raw = socket.create_connection(addresses[0][4][:2], self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


def _connection(url: str):
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        raise ValueError("Media transfer URL is malformed.") from None
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or port not in (None, 443) or parsed.fragment):
        raise ValueError("Media transfer requires an ordinary public HTTPS URL.")
    conn = _PublicHTTPS(parsed.hostname, timeout=20, context=ssl.create_default_context())
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    return conn, target


def request(config: dict, path: str, payload: dict | None = None):
    check_network(config, API_ORIGIN)
    key = require_credential(config)
    if not path.startswith("/api/v1/") or "\n" in path or "\r" in path:
        raise ValueError("Unsupported Kie route.")
    conn, target = _connection(API_ORIGIN + path)
    sent = False
    try:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        sent = payload is not None
        conn.request("POST" if payload is not None else "GET", target, body=data,
                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        response = conn.getresponse()
        raw = response.read(MAX_JSON_BYTES + 1)
        if response.status != 200:
            raise ProviderError(f"Kie returned HTTP {response.status}; no automatic resubmission.",
                                uncertain=sent and response.status >= 500)
        if len(raw) > MAX_JSON_BYTES:
            raise ValueError("oversized response")
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("invalid response")
        if result.get("code") != 200:
            code = result.get("code")
            label = str(code) if isinstance(code, int) else "unrecognized status"
            raise ProviderError(f"Kie rejected the request ({label}); no automatic resubmission.",
                                uncertain=sent and (not isinstance(code, int) or code >= 500))
        return result.get("data")
    except ProviderError:
        raise
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise ProviderError("Kie response could not be verified; no automatic resubmission.", uncertain=sent) from exc
    finally:
        conn.close()


def credits(config: dict) -> dict:
    value = request(config, "/api/v1/chat/credit")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProviderError("Kie returned an unreadable credit balance.")
    import math

    if not math.isfinite(value) or value < 0:
        raise ProviderError("Kie returned an invalid credit balance.")
    return {"provider": "Kie", "credits": value, "checked_at": time.time(), "is_price_quote": False}


def task(config: dict, task_id: str) -> dict:
    value = request(config, "/api/v1/jobs/recordInfo?" + urlencode({"taskId": task_id}))
    if not isinstance(value, dict) or value.get("taskId") != task_id:
        raise ProviderError("Kie returned a mismatched task record.")
    return value


def download(url: str, destination: Path, *, config: dict, max_bytes: int, cancel=None) -> None:
    """Download into an exclusively created local stage, with no auth headers."""
    current = url
    created = False
    try:
        for _ in range(5):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Local download stopped; provider task is unchanged.")
            check_network(config, current)
            conn, target = _connection(current)
            try:
                conn.request("GET", target, headers={"Accept-Encoding": "identity", "User-Agent": "MO-Agent-media"})
                response = conn.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location:
                        raise ValueError("Invalid media download redirect.")
                    current = urljoin(current, location)
                    continue
                if response.status != 200:
                    raise ProviderError(f"Media download returned HTTP {response.status}.")
                declared = response.getheader("Content-Length")
                if declared and (not declared.isdigit() or int(declared) > max_bytes):
                    raise ValueError("Media download exceeds the size limit.")
                count = 0
                with destination.open("xb") as handle:
                    created = True
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise InterruptedError("Local download stopped; provider task is unchanged.")
                        chunk = response.read(min(256 * 1024, max_bytes - count + 1))
                        if not chunk:
                            break
                        count += len(chunk)
                        if count > max_bytes:
                            raise ValueError("Media download exceeds the size limit.")
                        handle.write(chunk)
                if not count or (declared and count != int(declared)):
                    raise ValueError("Incomplete media download.")
                return
            finally:
                conn.close()
        raise ValueError("Too many media download redirects.")
    except BaseException as exc:
        # The caller owns this unique stage. Never remove a pre-existing file.
        if created:
            destination.unlink(missing_ok=True)
        if isinstance(exc, http.client.HTTPException):
            raise ProviderError("Media download response could not be verified; resume this job without resubmitting.") from None
        raise
