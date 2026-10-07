"""Specialists a Project Architect proposes wait here for the user's yes before they join the project team.

Same rule as workflow candidates: a proposal is staged inert, and only the user's own words ("hire <name>"),
handled before any provider call, write its skill pack. The model cannot approve itself. The ledger is
append-only; the newest row per (project, role) is the candidate's state.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..state.paths import resolve_state_path

LEDGER = "memory/learning/role-candidates.jsonl"


def _path(config: dict | None) -> Path:
    return Path(resolve_state_path(LEDGER, config or {}))


def _key(project_root: str, role: str) -> tuple[str, str]:
    return str(Path(project_root)).casefold(), str(role or "").casefold()


def _append(config: dict | None, row: dict[str, Any]) -> None:
    path = _path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _latest(config: dict | None) -> dict[tuple[str, str], dict[str, Any]]:
    try:
        lines = _path(config).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("project_root") and row.get("role"):
            latest[_key(row["project_root"], row["role"])] = row
    return latest


def stage(config: dict | None, *, project_root: str, role: str, name: str, description: str, body: str,
          triggers: tuple[str, ...]) -> dict[str, Any]:
    """Stage (or replace) a proposed specialist for the user's decision."""
    row = {"status": "candidate", "at": time.time(), "project_root": str(project_root), "role": role, "name": name,
           "description": description, "body": body, "triggers": list(triggers)}
    _append(config, row)
    return row


def pending(config: dict | None, project_root: str) -> list[dict[str, Any]]:
    """Candidates still waiting for the user in this project, oldest first."""
    here = str(Path(project_root)).casefold()
    rows = [row for (project, _role), row in _latest(config).items() if project == here and row.get("status") == "candidate"]
    return sorted(rows, key=lambda row: float(row.get("at") or 0.0))


def find(config: dict | None, project_root: str, wanted: str) -> dict[str, Any] | None:
    """The waiting candidate the user named, by display name or role id."""
    clean = " ".join(str(wanted or "").split()).casefold()
    for row in pending(config, project_root):
        if clean in {str(row.get("name") or "").casefold(), str(row.get("role") or "").casefold()}:
            return row
    return None


def mark(config: dict | None, candidate: dict[str, Any], status: str) -> None:
    """Close a candidate as hired (its pack was written) or declined."""
    _append(config, {"status": status, "at": time.time(), "project_root": candidate["project_root"],
                     "role": candidate["role"], "name": candidate.get("name", "")})
