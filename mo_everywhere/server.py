"""Lazy uvicorn lifecycle for the optional MO Everywhere web surface."""
from __future__ import annotations

import ipaddress
import importlib.util
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.state.everywhere_readiness import everywhere_authority
from core.state.paths import resolve_state_path


@dataclass(frozen=True)
class ApiSettings:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8765
    blocked_reason: str = ""

    @classmethod
    def from_config(cls, config: dict[str, Any] | None = None) -> "ApiSettings":
        block = (config or {}).get("consistent_everywhere")
        block = block if isinstance(block, dict) else {}
        api = block.get("api") if isinstance(block.get("api"), dict) else {}
        authority = everywhere_authority(config or {})
        requested = (
            block.get("enabled") is True
            and api.get("enabled") is True
            and not Path(resolve_state_path("run/everywhere.disabled", config or {})).is_file()
        )
        blocked_reason = ""
        if requested and not authority.api_allowed:
            blocked_reason = "; ".join(authority.conflicts) or "MO Everywhere API requires explicit device_role: server"
        return cls(
            enabled=requested and authority.api_allowed,
            host=str(api.get("host") or "127.0.0.1").strip(),
            port=max(1, min(65535, int(api.get("port", 8765) or 8765))),
            blocked_reason=blocked_reason,
        )


@dataclass
class EverywhereApiServer:
    agent: Any
    gateway: Any
    config: dict[str, Any]
    settings: ApiSettings
    _server: Any = field(default=None, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)

    def start(self) -> bool:
        if self.settings.blocked_reason:
            raise RuntimeError(f"MO Everywhere API blocked: {self.settings.blocked_reason}")
        if not self.settings.enabled or self._thread and self._thread.is_alive():
            return False
        if not _loopback(self.settings.host):
            raise RuntimeError("MO Everywhere binds loopback only; expose it through a TLS reverse proxy")
        try:
            import uvicorn
        except ImportError as exc:
            raise RuntimeError("MO Everywhere needs the optional uvicorn dependency") from exc
        if not _websocket_backend_available():
            raise RuntimeError(
                "MO Everywhere needs an optional Uvicorn WebSocket backend "
                "(websockets or wsproto)"
            )
        from .app import create_app

        app = create_app(self.agent, self.gateway, config=self.config)
        uvicorn_config = uvicorn.Config(
            app,
            host=self.settings.host,
            port=self.settings.port,
            workers=1,
            access_log=False,
            log_level="warning",
            proxy_headers=False,
        )
        self._server = uvicorn.Server(uvicorn_config)
        self._thread = threading.Thread(target=self._server.run, name="mo-everywhere-api", daemon=True)
        self._thread.start()
        deadline = time.monotonic() + 5.0
        while self._thread.is_alive() and not bool(getattr(self._server, "started", False)):
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        if not bool(getattr(self._server, "started", False)):
            self._server.should_exit = True
            self._thread.join(timeout=1.0)
            raise RuntimeError("MO Everywhere API did not become ready")
        return True

    def stop(self, timeout: float = 5.0) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=max(0.0, timeout))


def start_everywhere_api_if_enabled(agent: Any, gateway: Any) -> EverywhereApiServer | None:
    config = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    settings = ApiSettings.from_config(config)
    server = EverywhereApiServer(agent, gateway, config, settings)
    return server if server.start() else None


def _loopback(host: str) -> bool:
    value = str(host or "").strip().lower()
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _websocket_backend_available() -> bool:
    return (
        importlib.util.find_spec("websockets") is not None
        or importlib.util.find_spec("wsproto") is not None
    )
