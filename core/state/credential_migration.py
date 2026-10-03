"""Lossless, value-free migration into MO's scoped credential layout."""
# COMPAT(state-home-upgraders): replaced-by explicit --init repair flow; remove-when operator retires the old-home upgrade lane
from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..utils.atomic_write import atomic_write_text
from .paths import mo_home
from .secrets import parse_env_file


_ASSIGNMENT_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
@dataclass
class CredentialMigrationReport:
    home: Path
    migrated: list[str] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.migrated or self.archived)


def migrate_legacy_credentials(
    *,
    home: str | Path | None = None,
    config: dict[str, Any] | None = None,
) -> CredentialMigrationReport:
    """Migrate known legacy files without returning or logging secret values.

    Canonical non-empty values win. Missing or placeholder canonical entries
    are filled from legacy files, the result is verified in-process, and only
    then is the exact source moved to a private archive.
    """
    root = Path(home).expanduser().resolve(strict=False) if home else mo_home(config)
    report = CredentialMigrationReport(home=root)
    (root / "credentials" / "mcp").mkdir(parents=True, exist_ok=True)
    (root / "credentials" / "legacy").mkdir(parents=True, exist_ok=True)

    telegram_cfg = (config or {}).get("telegram") if isinstance((config or {}).get("telegram"), dict) else {}
    telegram_key = str(telegram_cfg.get("bot_token_env") or "TELEGRAM_BOT_TOKEN").strip()

    combined = root / ".env"
    if combined.is_file():
        try:
            assignments = _env_assignments(combined)
            provider_rows: dict[str, str] = {}
            telegram_rows: dict[str, str] = {}
            for key, raw_value in assignments.items():
                if key in {telegram_key, "TELEGRAM_BOT_TOKEN"}:
                    telegram_rows[key] = raw_value
                else:
                    provider_rows[key] = raw_value
            _merge_and_verify(root / "credentials" / "providers.env", provider_rows)
            _merge_and_verify(root / "credentials" / "telegram.env", telegram_rows)
            _archive_exact(combined, root / "credentials" / "legacy" / "root.env.bak")
            report.migrated.append("legacy combined env -> scoped credential files")
            report.archived.append("legacy combined env")
        except Exception:
            report.warnings.append("Legacy combined credential migration could not be verified; source was preserved.")

    return report


def _env_assignments(path: Path) -> dict[str, str]:
    rows: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="strict").splitlines():
        match = _ASSIGNMENT_RE.match(raw.strip())
        if match:
            rows[match.group(1)] = match.group(2)
    return rows


def _merge_and_verify(target: Path, incoming: dict[str, str]) -> None:
    if not incoming:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = target.read_text(encoding="utf-8", errors="strict").splitlines() if target.is_file() else []
    positions: dict[str, int] = {}
    values: dict[str, str] = {}
    for index, raw in enumerate(lines):
        match = _ASSIGNMENT_RE.match(raw.strip())
        if match:
            positions[match.group(1)] = index
            values[match.group(1)] = match.group(2)
    for key, raw_value in incoming.items():
        if key not in positions:
            positions[key] = len(lines)
            values[key] = raw_value
            lines.append(f"{key}={raw_value}")
        elif not _normalized_value(values[key]) and _normalized_value(raw_value):
            lines[positions[key]] = f"{key}={raw_value}"
            values[key] = raw_value
    expected = {key: _normalized_value(values[key]) for key in incoming}
    atomic_write_text(target, "\n".join(lines).rstrip() + "\n", encoding="utf-8")
    _make_private(target)
    resolved = parse_env_file(target)
    for key, expected_value in expected.items():
        if key not in resolved or resolved[key] != expected_value:
            raise OSError("credential migration verification failed")


def _normalized_value(raw_value: str) -> str:
    return str(raw_value).strip().strip('"').strip("'")


def _archive_exact(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    chosen = target
    if chosen.exists() and _digest(chosen) != _digest(source):
        index = 1
        while chosen.with_name(f"{target.name}.{index}").exists():
            index += 1
        chosen = chosen.with_name(f"{target.name}.{index}")
    if chosen.exists():
        if _digest(chosen) != _digest(source):
            raise OSError("credential archive collision")
        source.unlink()
        return
    try:
        os.replace(source, chosen)
    except OSError:
        shutil.copy2(source, chosen)
        if _digest(chosen) != _digest(source):
            chosen.unlink(missing_ok=True)
            raise OSError("credential archive verification failed")
        source.unlink()
    _make_private(chosen)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_private(path: Path) -> None:
    if os.name == "nt":
        return
    path.chmod(0o600)
