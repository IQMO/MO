"""Bounded, versioned data helpers for Game Collaboration."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_HISTORY = 200
MAX_ITEMS = 100
MAX_TEXT = 2_000
PROJECT_KEY_LENGTH = 16


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(value: Any, limit: int = MAX_TEXT) -> str:
    return " ".join(str(value or "").split())[: max(1, int(limit))]


def normalize_project_root(value: str | Path) -> Path:
    return Path(str(value or "")).expanduser().resolve(strict=False)


def project_key_for(root: str | Path) -> str:
    normalized = str(normalize_project_root(root)).replace("\\", "/").casefold()
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()[:PROJECT_KEY_LENGTH]


def display_name_for(root: str | Path, requested: str = "") -> str:
    name = clean_text(requested, 120)
    if name:
        return name
    path = normalize_project_root(root)
    return clean_text(path.name or str(path), 120) or "Game project"


def new_record(root: str | Path, display_name: str = "") -> dict[str, Any]:
    project_root = normalize_project_root(root)
    timestamp = now_iso()
    return {
        "schema_version": SCHEMA_VERSION,
        "project_key": project_key_for(project_root),
        "display_name": display_name_for(project_root, display_name),
        "project_root": str(project_root),
        "project_root_digest": project_key_for(project_root),
        "record_revision": 1,
        "project_status": "active",
        "active_phase": "planning",
        "requirements": [],
        "decisions": [],
        "open_questions": [],
        "proposals": [],
        "approvals": [],
        "artifacts": [],
        "sources": [],
        "history": [{"at": timestamp, "event": "created", "detail": "Game Collaboration started"}],
        "last_terminal_binding": {
            "mode": "active",
            "surface": "terminal",
            "record_revision": 1,
        },
        "updated_at": timestamp,
    }


def validate_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ValueError("Game Collaboration record must be an object")
    if int(record.get("schema_version", 0) or 0) != SCHEMA_VERSION:
        raise ValueError("unsupported Game Collaboration record schema")
    key = clean_text(record.get("project_key"), 64)
    root = clean_text(record.get("project_root"), 1024)
    if not key or not root or key != project_key_for(root):
        raise ValueError("invalid Game Collaboration project identity")
    if clean_text(record.get("project_root_digest"), 64) != key:
        raise ValueError("invalid Game Collaboration project digest")
    if record.get("project_status") not in {"draft", "active", "paused", "archived"}:
        raise ValueError("invalid Game Collaboration project status")
    if int(record.get("record_revision", 0) or 0) < 1:
        raise ValueError("invalid Game Collaboration revision")
    for field in ("requirements", "decisions", "open_questions", "proposals", "approvals", "artifacts", "sources", "history"):
        if not isinstance(record.get(field), list):
            raise ValueError(f"invalid Game Collaboration field: {field}")
    return record


def bounded_copy(record: dict[str, Any]) -> dict[str, Any]:
    """Return a safe bounded record suitable for persistence or rendering."""
    output = deepcopy(record)
    for field in ("requirements", "decisions", "open_questions", "proposals", "approvals", "artifacts", "sources"):
        items = output.get(field, [])
        output[field] = [
            {clean_text(k, 80): clean_text(v, MAX_TEXT) if not isinstance(v, (dict, list)) else v for k, v in item.items()}
            if isinstance(item, dict) else {"text": clean_text(item)}
            for item in items[-MAX_ITEMS:]
        ]
    history = output.get("history", [])
    output["history"] = [
        {"at": clean_text(item.get("at"), 64), "event": clean_text(item.get("event"), 120), "detail": clean_text(item.get("detail"), 600)}
        for item in history[-MAX_HISTORY:]
        if isinstance(item, dict)
    ]
    output["display_name"] = clean_text(output.get("display_name"), 120)
    output["active_phase"] = clean_text(output.get("active_phase"), 120) or "planning"
    output["updated_at"] = clean_text(output.get("updated_at"), 64) or now_iso()
    return validate_record(output)


def append_history(record: dict[str, Any], event: str, detail: str) -> None:
    history = record.setdefault("history", [])
    history.append({"at": now_iso(), "event": clean_text(event, 120), "detail": clean_text(detail, 600)})
    record["history"] = history[-MAX_HISTORY:]


def next_item_id(record: dict[str, Any], field: str, prefix: str) -> str:
    used = {str(item.get("id") or "") for item in record.get(field, []) if isinstance(item, dict)}
    for number in range(1, MAX_ITEMS + 1):
        candidate = f"{prefix}-{number:03d}"
        if candidate not in used:
            return candidate
    raise ValueError(f"too many Game Collaboration {field}")
