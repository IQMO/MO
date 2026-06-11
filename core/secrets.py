"""Local secret resolution helpers.

Secrets are never printed by this module. Resolution is deliberately simple:
OS environment first, then configured key=value files. This lets an operator
reuse existing local secret stores without copying keys into tracked files.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_SECRET_FILES = (Path(".env"), Path("memory/unreachable/secrets.env"))
ENV_SECRET_FILES = "MO_SECRET_FILES"


@dataclass(frozen=True)
class SecretStatus:
    key: str
    present: bool
    source: str = ""


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


def secret_file_candidates(*, root: str | Path = ".", files: Iterable[str | Path] | None = None) -> list[Path]:
    """Return local defaults, MO_SECRET_FILES, and explicit files in order."""
    root_path = Path(root)
    candidates: list[Path] = []

    for rel in DEFAULT_SECRET_FILES:
        candidates.append(root_path / rel)

    env_files = os.getenv(ENV_SECRET_FILES, "")
    for item in env_files.replace(";", os.pathsep).split(os.pathsep):
        item = item.strip()
        if item:
            candidates.append(Path(item))

    for item in files or []:
        if item:
            candidates.append(Path(item))

    deduped: list[Path] = []
    seen: set[str] = set()
    for p in candidates:
        try:
            key = str(p.expanduser().resolve(strict=False))
        except Exception:
            key = str(p)
        if key not in seen:
            seen.add(key)
            deduped.append(p)
    return deduped


def resolve_secret(key: str, *, root: str | Path = ".", files: Iterable[str | Path] | None = None) -> str:
    """Resolve a secret value from env/configured files without logging it."""
    name = str(key or "").strip()
    if not name:
        return ""
    value = os.getenv(name)
    if value:
        return value
    for path in secret_file_candidates(root=root, files=files):
        value = parse_env_file(path).get(name, "")
        if value:
            return value
    return ""


def secret_status(key: str, *, root: str | Path = ".", files: Iterable[str | Path] | None = None) -> SecretStatus:
    """Return only presence/source metadata, never the secret value."""
    name = str(key or "").strip()
    if not name:
        return SecretStatus(key="", present=False)
    if os.getenv(name):
        return SecretStatus(key=name, present=True, source="env")
    for path in secret_file_candidates(root=root, files=files):
        value = parse_env_file(path).get(name, "")
        if value:
            try:
                source = str(Path(path).resolve(strict=False)).replace("\\", "/")
            except Exception:
                source = str(path).replace("\\", "/")
            return SecretStatus(key=name, present=True, source=source)
    return SecretStatus(key=name, present=False)
