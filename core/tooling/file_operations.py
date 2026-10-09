"""Cumulative file operation tracking across MO sessions.

Append-only JSONL written at session closeout. The data is best-effort and is
used for continuity context, not as proof of current file state.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..utils.atomic_write import atomic_write_text
from ..state.paths import FILE_OPERATIONS_PATH, resolve_state_path
from .tool_constants import FILE_MUTATION_TOOLS

FILE_OPS_PATH = Path(FILE_OPERATIONS_PATH)
MAX_KEEP = 500


def default_file_ops_path() -> Path:
    """Active file-ops ledger: private state home or explicit project-relative state.

    Resolves through ``path_defaults`` so the ledger follows the same private-home
    rules as the rest of MO's state instead of polluting the project tree.
    """
    return Path(resolve_state_path(str(FILE_OPS_PATH), default=str(FILE_OPS_PATH)))
_READ_TOOLS = {"read_file"}


def _read_tool_audit_files(
    since_ts: float,
    *,
    audit_path: str | Path | None = None,
    session_id: str,
) -> tuple[list[str], list[str]]:
    """Extract successful file operations after ``since_ts``.

    Only rows carrying the exact session correlation id are eligible; missing
    attribution fails closed so one session never adopts another's operations.
    """
    if not session_id:
        return [], []
    read_files: set[str] = set()
    modified_files: set[str] = set()
    path = Path(audit_path) if audit_path is not None else Path(resolve_state_path("logs/tool_audit.jsonl"))
    if not path.exists():
        return [], []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                if float(entry.get("ts", 0) or 0) < float(since_ts or 0):
                    continue
            except (TypeError, ValueError):
                continue
            if str(entry.get("session_id") or "") != session_id:
                continue
            if bool(entry.get("blocked")) or bool(entry.get("error")):
                continue
            args = entry.get("arguments") if isinstance(entry.get("arguments"), dict) else {}
            path_arg = _clean_path(str(args.get("path") or args.get("file_path") or ""))
            if not path_arg:
                continue
            tool = str(entry.get("tool") or "")
            if tool in _READ_TOOLS:
                read_files.add(path_arg)
            elif tool in FILE_MUTATION_TOOLS:
                modified_files.add(path_arg)
                read_files.add(path_arg)
    except Exception:
        return [], []
    return sorted(read_files), sorted(modified_files)


def write_file_ops(
    session_id: str,
    run_id: str,
    since_ts: float,
    *,
    provider: str = "",
    model: str = "",
    turn_count: int = 0,
    path: str | Path | None = None,
    audit_path: str | Path | None = None,
) -> None:
    """Append one session file-operation record from tool audit data."""
    if path is None:
        path = default_file_ops_path()
    read_files, modified_files = _read_tool_audit_files(
        since_ts,
        audit_path=audit_path,
        session_id=str(session_id or ""),
    )
    if not read_files and not modified_files:
        return
    record = {
        "session_id": str(session_id or ""),
        "run_id": str(run_id or ""),
        "closed_at": round(time.time(), 3),
        "files_read": read_files,
        "files_modified": modified_files,
        "provider": str(provider or ""),
        "model": str(model or ""),
        "turn_count": max(0, int(turn_count or 0)),
    }
    try:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        _prune_file_ops(out)
    except Exception:
        return


def read_file_ops(limit: int = 50, *, path: str | Path | None = None) -> list[dict[str, Any]]:
    """Return recent file-operation records, newest first."""
    src = Path(path) if path is not None else default_file_ops_path()
    if not src.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        for line in reversed(src.read_text(encoding="utf-8", errors="replace").splitlines()):
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
            if len(records) >= max(1, int(limit or 50)):
                break
    except Exception:
        return []
    return records


def accumulated_files(limit: int = 50) -> dict[str, dict[str, Any]]:
    """Aggregate read/modify counts across recent session records."""
    files: dict[str, dict[str, Any]] = {}
    for record in read_file_ops(limit):
        sid = str(record.get("session_id") or "?")
        for file_path in record.get("files_read", []) or []:
            item = files.setdefault(str(file_path), {"reads": 0, "modifies": 0, "last_session": "", "sessions": []})
            item["reads"] += 1
            _mark_session(item, sid)
        for file_path in record.get("files_modified", []) or []:
            item = files.setdefault(str(file_path), {"reads": 0, "modifies": 0, "last_session": "", "sessions": []})
            item["modifies"] += 1
            _mark_session(item, sid)
    return files


def _mark_session(item: dict[str, Any], session_id: str) -> None:
    item["last_session"] = session_id
    if session_id not in item["sessions"]:
        item["sessions"].append(session_id)


def _prune_file_ops(path: Path) -> None:
    try:
        lines = [line for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        if len(lines) > MAX_KEEP:
            atomic_write_text(path, "\n".join(lines[-MAX_KEEP:]) + "\n", encoding="utf-8")
    except Exception:
        return


def _clean_path(value: str) -> str:
    text = str(value or "").replace("\\", "/").strip().lstrip("./")
    if not text or text.startswith(("logs/", "memory/")):
        return ""
    return text
