"""Shared JSONL read/write utilities — single source of truth.

Used by learning, skills, session closeout, heartbeat, task_board,
and anywhere JSONL files are read.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable

from .path_defaults import resolve_state_path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file, returning all valid dict rows. Returns [] on any failure."""
    rows: list[dict[str, Any]] = []
    try:
        if not path.exists():
            return []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    except OSError:
        return []
    return rows


def resolve_ledger_path(
    *,
    path: str | Path | None = None,
    disable_env: str,
    path_env: str,
    default_name: str,
) -> Path | None:
    """Resolve a JSONL-ledger path using env vars and state-home fallback.

    Shared by heartbeat and task_board ledger resolution.
    Returns None when ledger writes are disabled.
    """
    if os.environ.get(disable_env, "").strip().lower() in {"1", "true", "yes"}:
        return None
    if path:
        return Path(path)
    env_path = os.environ.get(path_env, "")
    if env_path:
        return Path(env_path)
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    return Path(resolve_state_path(default_name))


def read_recent_ledger_entries(
    raw_lines: list[str],
    limit: int,
    *,
    filter_fn: Callable[[dict[str, Any]], bool] | None = None,
) -> list[dict[str, Any]]:
    """Parse recent JSONL ledger entries with optional filtering.

    Reads lines newest-first, applies filter_fn (return True to keep),
    and returns results oldest-first up to limit.
    """
    matches: list[dict[str, Any]] = []
    for raw in reversed(raw_lines):
        try:
            item = json.loads(raw)
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        if filter_fn and not filter_fn(item):
            continue
        matches.append(item)
        if len(matches) >= max(1, int(limit or 1)):
            break
    return list(reversed(matches))
