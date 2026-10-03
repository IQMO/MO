"""Read-only, side-effect-free SystemCare status for Dashboard surfaces."""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from core.state.paths import resolve_state_path

from .state import process_is_alive


def read_dashboard_status(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Read the existing ledger without creating directories or a database."""
    cfg = config or {}
    db_path = Path(resolve_state_path("memory/systemcare.sqlite", cfg))
    active_path = Path(resolve_state_path("run/systemcare/active.json", cfg))
    active = _active_operation(active_path)
    base = {
        "available": os.name == "nt",
        "state": "not_scanned",
        "label": "Calibrate & scan",
        "reclaimable_bytes": 0,
        "last_scan_at": 0.0,
        "active": bool(active),
        "calibrated": False,
        "pause_schedules": False,
    }
    if active:
        kind = str(active.get("kind") or "")
        base.update(
            state="scanning" if kind in {"scanning", "calibrating"} else "active",
            label="Scan in progress" if kind in {"scanning", "calibrating"} else "Maintenance in progress",
        )
    if not db_path.is_file():
        return base
    try:
        uri = db_path.resolve(strict=False).as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=0.15) as db:
            calibration = db.execute(
                "SELECT payload FROM calibrations ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            scan = db.execute(
                "SELECT payload FROM scans ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='backups'").fetchone():
                game = db.execute("""SELECT payload FROM backups WHERE json_extract(payload,'$.action')='game_on'
                    AND json_extract(payload,'$.state')!='restored' ORDER BY created_at DESC LIMIT 1""").fetchone()
                if game:
                    base["pause_schedules"] = (_payload(game[0]).get("subject") or {}).get("pause_schedules") is True
        base["calibrated"] = bool(calibration)
        if active or not scan:
            return base
        payload = _payload(scan[0])
        state = str(payload.get("state") or "")
        findings = payload.get("findings") if isinstance(payload.get("findings"), list) else []
        review_count = sum(
            1 for item in findings
            if isinstance(item, dict) and item.get("severity") in {"review", "attention"}
        )
        reclaimable = sum(
            max(0, int(item.get("reclaimable_bytes") or 0))
            for item in findings if isinstance(item, dict)
        )
        if state == "failed":
            label, public_state = "Last scan needs attention", "attention"
        elif state == "cancelled":
            label, public_state = "Scan cancelled", "ready"
        elif review_count:
            label, public_state = f"{review_count} item{'s' if review_count != 1 else ''} to review", "review"
        else:
            label, public_state = "No review items", "ready"
        base.update(
            state=public_state,
            label=label,
            reclaimable_bytes=reclaimable,
            last_scan_at=float(payload.get("completed_at") or 0.0),
        )
    except (OSError, sqlite3.Error, TypeError, ValueError):
        base.update(state="unavailable", label="Status unavailable")
    return base


def _payload(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(str(raw or "{}"))
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _active_operation(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            return {}
        pid = int(value.get("pid") or 0)
        if pid <= 0 or not process_is_alive(pid):
            return {}
        return value
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
