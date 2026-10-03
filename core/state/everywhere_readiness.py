"""Canonical, value-free MO Everywhere topology and readiness truth.

This module is intentionally dependency-light. Runtime owners use the same
effective role and authority decision without importing the API, registry, GUI,
or Android implementation. Human renderers consume the structured result; they
never become control flow themselves.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .paths import EVERYWHERE_HUB_DB_PATH, codex_auth_path, resolve_state_path
from .secrets import secret_status


DISABLE_PATH = "run/everywhere.disabled"
EVERYWHERE_API_VERSION = 2
ANDROID_PUBLIC_DISTRIBUTION = "Google Play"
VALID_DEVICE_ROLES = frozenset({"workstation", "server", "mobile"})
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True)
class EverywhereAuthority:
    configured: bool
    enabled: bool
    configured_role: str
    role: str
    role_explicit: bool
    api_configured: bool
    legacy_hub_local: bool
    hub_owner: bool
    conflicts: tuple[str, ...]

    @property
    def api_allowed(self) -> bool:
        return (
            self.enabled
            and self.api_configured
            and self.role_explicit
            and self.hub_owner
            and not self.conflicts
        )

    @property
    def registry_admin_allowed(self) -> bool:
        return self.role_explicit and self.hub_owner and not self.conflicts


@dataclass(frozen=True)
class DependencyReadiness:
    fastapi: bool
    uvicorn: bool
    websocket: bool
    qr: bool

    @property
    def hub_ready(self) -> bool:
        return self.fastapi and self.uvicorn and self.websocket

    @property
    def pairing_ready(self) -> bool:
        return self.hub_ready and self.qr

    def render(self) -> str:
        return (
            f"FastAPI={self.fastapi} Uvicorn={self.uvicorn} "
            f"WebSocket={self.websocket} QR={self.qr}"
        )


@dataclass(frozen=True)
class CredentialReadiness:
    primary: str
    live_host: str
    provider: str

    @property
    def provider_ready(self) -> bool:
        return self.provider == "present"


@dataclass(frozen=True)
class ReadinessIssue:
    code: str
    message: str
    next_action: str


@dataclass(frozen=True)
class EverywhereReadiness:
    authority: EverywhereAuthority
    locally_disabled: bool
    dependencies: DependencyReadiness
    credentials: CredentialReadiness
    api_loopback: bool
    public_url: str
    endpoint: str
    endpoint_state: str
    registry_state: str
    issues: tuple[ReadinessIssue, ...]
    next_action: str

    @property
    def activation_blockers(self) -> tuple[ReadinessIssue, ...]:
        return self.issues

    @property
    def can_activate(self) -> bool:
        return self.authority.enabled and not self.activation_blockers

    @property
    def pairing_blockers(self) -> tuple[str, ...]:
        blocked: list[str] = []
        if not self.authority.api_allowed:
            blocked.append("this device is not the enabled serving hub")
        if self.locally_disabled:
            blocked.append("the local Everywhere gate is disabled")
        if not self.dependencies.pairing_ready:
            blocked.append("hub or QR dependencies are missing")
        if not self.credentials.provider_ready:
            blocked.append("the serving hub has no configured provider credential present")
        if not self.api_loopback:
            blocked.append("the API is not configured for a loopback bind")
        if not self.public_url:
            blocked.append("the serving hub has no verified public HTTPS origin")
        if self.endpoint_state != "reachable":
            blocked.append(f"the serving hub health endpoint is {self.endpoint_state}")
        return tuple(blocked)

    @property
    def can_pair_android(self) -> bool:
        return not self.pairing_blockers


def everywhere_authority(config: dict[str, Any] | None = None) -> EverywhereAuthority:
    cfg = config or {}
    raw_block = cfg.get("consistent_everywhere")
    configured = isinstance(raw_block, dict)
    block = raw_block if configured else {}
    api = block.get("api") if isinstance(block.get("api"), dict) else {}
    continuity = block.get("continuity") if isinstance(block.get("continuity"), dict) else {}
    raw_role = str(block.get("device_role") or "").strip().lower()
    role_explicit = raw_role in VALID_DEVICE_ROLES
    api_configured = api.get("enabled") is True
    # The inferred value is display/migration context only. Authority always
    # requires an explicit role so an old API toggle cannot create a new hub.
    role = raw_role if role_explicit else ("server" if api_configured else "workstation")
    legacy_hub_local = continuity.get("hub_local") is True
    conflicts: list[str] = []
    if api_configured and (not role_explicit or role != "server"):
        conflicts.append("API enablement requires explicit device_role: server")
    if legacy_hub_local and (not role_explicit or role != "server"):
        conflicts.append("continuity.hub_local conflicts with the effective device role")
    return EverywhereAuthority(
        configured=configured,
        enabled=configured and block.get("enabled") is True,
        configured_role=raw_role,
        role=role,
        role_explicit=role_explicit,
        api_configured=api_configured,
        legacy_hub_local=legacy_hub_local,
        hub_owner=role_explicit and role == "server",
        conflicts=tuple(conflicts),
    )


def dependency_readiness() -> DependencyReadiness:
    return DependencyReadiness(
        fastapi=_module_available("fastapi"),
        uvicorn=_module_available("uvicorn"),
        websocket=_module_available("websockets") or _module_available("wsproto"),
        qr=_module_available("segno"),
    )


def build_everywhere_readiness(
    config: dict[str, Any] | None = None,
    *,
    check_health: bool = True,
) -> EverywhereReadiness:
    cfg = config or {}
    authority = everywhere_authority(cfg)
    block = cfg.get("consistent_everywhere") if isinstance(cfg.get("consistent_everywhere"), dict) else {}
    api = block.get("api") if isinstance(block.get("api"), dict) else {}
    continuity = block.get("continuity") if isinstance(block.get("continuity"), dict) else {}
    host = str(api.get("host") or "127.0.0.1").strip().lower()
    api_loopback = host in LOOPBACK_HOSTS
    public_url = str(api.get("public_url") or "").strip().rstrip("/")
    primary_state, primary_url = _device_credential_state(cfg, live_host=False)
    live_host_state, _live_url = _device_credential_state(cfg, live_host=True)
    endpoint = public_url if authority.hub_owner else (primary_url or public_url)
    endpoint_state = hub_health(endpoint) if check_health and endpoint else "not configured"
    deps = dependency_readiness()
    credentials = CredentialReadiness(
        primary=primary_state,
        live_host=live_host_state,
        provider=provider_credential_state(cfg),
    )
    disabled = Path(resolve_state_path(DISABLE_PATH, cfg)).is_file()
    registry_state = registry_readiness(cfg, authority)
    issues: list[ReadinessIssue] = []

    if not authority.configured:
        issues.append(ReadinessIssue(
            "config_absent",
            "consistent_everywhere config block is absent",
            "Add the neutral consistent_everywhere block to the private config.",
        ))
    elif not authority.enabled:
        issues.append(ReadinessIssue(
            "feature_disabled",
            "consistent_everywhere.enabled is not true",
            "Enable Everywhere in the private config, then rerun `/everywhere setup`.",
        ))
    if not authority.role_explicit:
        issues.append(ReadinessIssue(
            "role_required",
            "device_role must explicitly select the single hub owner or a workstation",
            "Set device_role to server on the hub owner, otherwise workstation.",
        ))
    for conflict in authority.conflicts:
        issues.append(ReadinessIssue("topology_conflict", conflict, "Correct the conflicting role/API settings."))
    if authority.hub_owner:
        if not credentials.provider_ready:
            issues.append(ReadinessIssue(
                "provider_missing",
                "the serving hub has no configured provider credential present",
                "Configure it in MO's existing credential broker, run `/reload` in a terminal or restart the serving service, then rerun `/everywhere setup`.",
            ))
        if not deps.hub_ready:
            issues.append(ReadinessIssue(
                "hub_dependencies_missing",
                "optional FastAPI/Uvicorn/WebSocket hub dependencies are missing",
                "Install the existing Everywhere requirements on the serving hub.",
            ))
        if not deps.qr:
            issues.append(ReadinessIssue(
                "qr_dependency_missing",
                "the Segno QR dependency is missing",
                "Install the existing Everywhere requirements before Android pairing.",
            ))
        if not authority.api_configured:
            issues.append(ReadinessIssue(
                "api_disabled",
                "the serving hub API is not enabled",
                "Enable consistent_everywhere.api on the serving hub.",
            ))
        if not api_loopback:
            issues.append(ReadinessIssue(
                "api_not_loopback",
                "the MO API must bind loopback and sit behind the TLS reverse proxy",
                "Set the API host to loopback and expose only the verified HTTPS/WSS proxy.",
            ))
        if not public_url:
            issues.append(ReadinessIssue(
                "public_url_missing",
                "the serving hub public HTTPS origin is not configured",
                "Set consistent_everywhere.api.public_url to the verified HTTPS origin.",
            ))
    elif authority.role_explicit and authority.role in {"workstation", "mobile"}:
        if registry_state.startswith("unexpected local registry"):
            issues.append(ReadinessIssue(
                "second_registry",
                registry_state,
                "Review the stale local registry; this device must pair to the serving hub instead.",
            ))
        if continuity.get("auto_sync") is True and credentials.primary != "valid":
            issues.append(ReadinessIssue(
                "device_not_paired",
                f"this device needs a valid serving-hub credential (current: {credentials.primary})",
                "Create pairing on the serving hub and join this device without creating a local registry.",
            ))

    if issues:
        next_action = issues[0].next_action
    elif disabled:
        next_action = "Review the plan, then run `/everywhere setup --confirm` to release the local gate."
    elif authority.hub_owner and endpoint_state != "reachable":
        next_action = "Start or restart the MO service and TLS proxy, then verify hub health."
    elif authority.hub_owner:
        next_action = (
            f"Install MO Everywhere from {ANDROID_PUBLIC_DISTRIBUTION} when available, "
            "or use a private owner-device build for local maintenance; then run "
            "`/everywhere pair android` here."
        )
    elif credentials.primary != "valid":
        next_action = "Pair this device to the serving hub."
    else:
        next_action = "Everywhere is ready on this device."

    return EverywhereReadiness(
        authority=authority,
        locally_disabled=disabled,
        dependencies=deps,
        credentials=credentials,
        api_loopback=api_loopback,
        public_url=public_url,
        endpoint=endpoint,
        endpoint_state=endpoint_state,
        registry_state=registry_state,
        issues=tuple(issues),
        next_action=next_action,
    )


def provider_credential_state(config: dict[str, Any] | None = None) -> str:
    cfg = config or {}
    providers = cfg.get("providers") if isinstance(cfg.get("providers"), list) else []
    for provider in providers:
        if not isinstance(provider, dict):
            continue
        kind = str(provider.get("type") or provider.get("api_mode") or "").strip().lower()
        name = str(provider.get("name") or "").strip().lower()
        if kind == "mock":
            return "present"
        if name == "openai-codex" or kind == "codex_responses":
            if Path(codex_auth_path(provider.get("auth_path"))).expanduser().is_file():
                return "present"
            continue
        try:
            host = (
                urllib.parse.urlsplit(str(provider.get("base_url") or "")).hostname
                or ""
            ).lower()
        except ValueError:
            host = ""
        if host in LOOPBACK_HOSTS:
            return "present"
        key = str(provider.get("api_key_env") or "").strip()
        if key and secret_status(key, config=cfg, service="providers").present:
            return "present"
    return "missing" if providers else "not configured"


def hub_health(base_url: str) -> str:
    raw = str(base_url or "").strip().rstrip("/")
    try:
        parsed = urllib.parse.urlsplit(raw)
        loopback = (parsed.hostname or "").lower() in LOOPBACK_HOSTS
        if not parsed.hostname or (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback)):
            return "invalid (HTTPS required outside loopback)"
        request = urllib.request.Request(raw + "/api/mo/health", headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=2.0) as response:
            body = response.read(4097)
        if len(body) > 4096:
            return "invalid response"
        payload = json.loads(body or b"{}")
        if not isinstance(payload, dict):
            return "invalid response"
        contract_matches = (
            payload.get("surface") == "mo_everywhere"
            and isinstance(payload.get("api_version"), int)
            and not isinstance(payload.get("api_version"), bool)
            and payload.get("api_version") == EVERYWHERE_API_VERSION
            and payload.get("role") == "server"
            and payload.get("hub_owner") is True
        )
        if not contract_matches:
            return "incompatible"
        return "reachable" if payload.get("ok") is True else "unavailable"
    except (urllib.error.URLError, TimeoutError, OSError):
        return "unreachable"
    except (UnicodeError, json.JSONDecodeError, ValueError):
        return "invalid response"


def registry_readiness(config: dict[str, Any], authority: EverywhereAuthority | None = None) -> str:
    resolved = authority or everywhere_authority(config)
    path = Path(resolve_state_path(EVERYWHERE_HUB_DB_PATH, config))
    if not resolved.hub_owner:
        if path.is_file():
            return "unexpected local registry on a non-hub device (ignored)"
        primary, _url = _device_credential_state(config, live_host=False)
        return "remote (this device is paired)" if primary == "valid" else "not initialized"
    if not path.is_file():
        return "not initialized"
    try:
        uri = path.resolve(strict=True).as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1.0) as db:
            active = int(db.execute("SELECT COUNT(*) FROM device WHERE revoked_at IS NULL").fetchone()[0])
        return f"initialized ({active} active device{'s' if active != 1 else ''})"
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return "unreadable"


def _device_credential_state(config: dict[str, Any], *, live_host: bool) -> tuple[str, str]:
    try:
        from mo_everywhere.client import load_credentials
        from mo_everywhere.live_host import live_host_client_config

        target = live_host_client_config(config) if live_host else config
        credentials = load_credentials(target)
        return "valid", credentials.hub_url
    except Exception as exc:
        if type(exc).__name__ == "EverywhereClientError":
            return ("absent" if "not paired" in str(exc).lower() else "invalid"), ""
        return "unreadable", ""


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, AttributeError, ValueError):
        return False
