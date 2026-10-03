"""Stable, device-local identity for optional cross-device MO features."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from ..utils.atomic_write import atomic_write_json
from .paths import mo_home


DEVICE_FILE = "device.json"


def device_identity(config: dict[str, Any] | None = None) -> dict[str, str]:
    """Return a stable opaque device id without leaking a hostname or user name."""
    cfg = config or {}
    block = cfg.get("consistent_everywhere") if isinstance(cfg.get("consistent_everywhere"), dict) else {}
    configured = _clean_id(block.get("device_id"))
    configured_label = _clean_label(block.get("device_label"))
    if configured:
        return {"device_id": configured, "label": configured_label}

    path = mo_home(cfg) / DEVICE_FILE
    raw = _read_identity(path)
    existing = _clean_id(raw.get("device_id") if isinstance(raw, dict) else "")
    if existing:
        return {"device_id": existing, "label": configured_label or _clean_label(raw.get("label"))}

    created = {"device_id": uuid.uuid4().hex, "label": configured_label}
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        # Another MO process won first-run creation. Wait briefly for its one
        # small write, then return the shared identity instead of inventing one.
        for _attempt in range(20):
            existing = _read_identity(path)
            existing_id = _clean_id(existing.get("device_id") if isinstance(existing, dict) else "")
            if existing_id:
                return {
                    "device_id": existing_id,
                    "label": configured_label or _clean_label(existing.get("label")),
                }
            time.sleep(0.01)
        raise RuntimeError("device identity file exists but is unreadable")
    except OSError:
        # Preserve the normal atomic writer's clearer platform behavior for
        # filesystems that do not support exclusive creation as expected.
        atomic_write_json(path, created, indent=2, ensure_ascii=False)
        _make_private(path)
        return created
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(created, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    _make_private(path)
    return created


def _read_identity(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        return raw if isinstance(raw, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _clean_id(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum() or ch in "-_")[:64]


def _clean_label(value: Any) -> str:
    return " ".join(str(value or "").split())[:80]


def _make_private(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        path.chmod(0o600)
    except OSError:
        return
