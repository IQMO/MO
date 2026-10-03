"""Canonical credential broker for MO's private runtime.

Only this module turns logical credential names into values. Callers receive one
requested value; status callers receive presence metadata only. Raw credential
files are a hard model-tool boundary, so they never enter provider context.

Each service has exactly one user-facing source under MO's private profile.
Logical ``*_env`` names remain configuration metadata, but process-environment
values, alternate files, and legacy combined files are not credential sources.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .paths import mo_home


SERVICE_FILE_RELPATHS: dict[str, str] = {
    "providers": "credentials/providers.env",
    "telegram": "credentials/telegram.env",
    "gmail": "credentials/gmail.env",
}

_SERVICE_ALIASES = {
    "provider": "providers",
    "image": "providers",
    "embeddings": "providers",
}
_MCP_SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass(frozen=True)
class SecretStatus:
    key: str
    present: bool
    source: str = ""


def _service_name(service: str | None) -> str:
    value = str(service or "").strip().lower()
    return _SERVICE_ALIASES.get(value, value)


def parse_env_file(path: str | Path) -> dict[str, str]:
    """Parse a minimal KEY=VALUE file. Returns {} on read/parse failures."""
    p = Path(path)
    if not p.exists() or not p.is_file():
        return {}
    out: dict[str, str] = {}
    try:
        for raw in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            out[key] = value.strip().strip('"').strip("'")
    except Exception:
        return {}
    return out


def canonical_secret_file(service: str, config: dict[str, Any] | None = None) -> Path | None:
    """Return the private canonical file for a known or scoped MCP service."""
    name = _service_name(service)
    relpath = SERVICE_FILE_RELPATHS.get(name)
    if relpath is None and _MCP_SERVICE_RE.fullmatch(name):
        relpath = f"credentials/mcp/{name}.env"
    return (mo_home(config) / relpath) if relpath else None


def resolve_secret(
    key: str,
    *,
    config: dict[str, Any] | None = None,
    service: str = "",
) -> str:
    """Resolve one value from its service's canonical profile file."""
    name = str(key or "").strip()
    if not name:
        return ""
    path = canonical_secret_file(service, config)
    return parse_env_file(path).get(name, "") if path is not None else ""


def secret_status(
    key: str,
    *,
    config: dict[str, Any] | None = None,
    service: str = "",
) -> SecretStatus:
    """Return canonical presence metadata, never a value or raw path."""
    name = str(key or "").strip()
    if not name:
        return SecretStatus(key="", present=False)
    path = canonical_secret_file(service, config)
    if path is None or not parse_env_file(path).get(name, ""):
        return SecretStatus(key=name, present=False)
    return SecretStatus(
        key=name,
        present=True,
        source=f"canonical:{_service_name(service)}",
    )


def service_status(
    service: str,
    keys: Iterable[str],
    *,
    config: dict[str, Any] | None = None,
) -> list[SecretStatus]:
    """Return a bounded, value-free inventory for a known service."""
    return [
        secret_status(key, config=config, service=service)
        for key in dict.fromkeys(str(item).strip() for item in keys if str(item).strip())
    ]
