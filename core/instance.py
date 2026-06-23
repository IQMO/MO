"""MO process instance identity and lightweight discovery helpers."""
from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from .path_defaults import HEARTBEAT_LEDGER_PATH, resolve_state_path
from .runtime_lock import _pid_alive

ENV_MO_INSTANCE_ID = "MO_INSTANCE_ID"

_INSTANCE_ID: str | None = None


def get_instance_id() -> str:
    """Return a stable short id for this process."""
    global _INSTANCE_ID
    if _INSTANCE_ID:
        return _INSTANCE_ID
    configured = _sanitize_instance_id(os.environ.get(ENV_MO_INSTANCE_ID, ""))
    _INSTANCE_ID = configured or uuid.uuid4().hex[:8]
    os.environ.setdefault(ENV_MO_INSTANCE_ID, _INSTANCE_ID)
    return _INSTANCE_ID


def instance_session_slot(instance_id: str | None = None, *, prefix: str = "main") -> str:
    """Default session slot for a fresh terminal instance."""
    safe_prefix = "".join(ch for ch in str(prefix or "main") if ch.isalnum() or ch in "-_.")[:32] or "main"
    return f"{safe_prefix}-{_sanitize_instance_id(instance_id or get_instance_id())}"


def shared_session_enabled(config: dict[str, Any] | None = None) -> bool:
    """Compatibility escape hatch for the old global-main auto-resume behavior."""
    runtime = (config or {}).get("runtime")
    if not isinstance(runtime, dict):
        return False
    return runtime.get("shared_session") is True


def recent_instance_snapshots(
    config: dict[str, Any] | None = None,
    *,
    current_pid: int | None = None,
    max_age_seconds: float = 24 * 3600,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return latest heartbeat snapshot per other PID.

    This is intentionally observational: it does not kill or clean processes. It
    gives startup UX enough truth to distinguish live sibling instances from old
    stale heartbeat rows.
    """
    path = Path(resolve_state_path(HEARTBEAT_LEDGER_PATH, config))
    now = time.time()
    current = int(current_pid if current_pid is not None else os.getpid())
    seen: set[int] = set()
    out: list[dict[str, Any]] = []
    if not path.exists() or not path.is_file():
        legacy = _legacy_agent_lock_snapshot(current=current, seen=seen, now=now)
        return [legacy] if legacy is not None else []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    for raw in reversed(lines):
        try:
            item = json.loads(raw)
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        try:
            pid = int(item.get("pid") or 0)
        except Exception:
            pid = 0
        if pid <= 0 or pid == current or pid in seen:
            continue
        created = _safe_float(item.get("created_at"))
        if created and now - created > max_age_seconds:
            continue
        seen.add(pid)
        item = dict(item)
        item["pid_alive"] = _pid_alive(pid)
        item["age_seconds"] = max(0.0, now - created) if created else 0.0
        out.append(item)
        if len(out) >= max(1, int(limit or 1)):
            break
    legacy = _legacy_agent_lock_snapshot(current=current, seen=seen, now=now)
    if legacy is not None:
        out.append(legacy)
    return out


def render_existing_instances_notice(
    config: dict[str, Any] | None = None,
    *,
    current_pid: int | None = None,
    max_items: int = 5,
) -> str:
    """Render a concise startup notice for sibling/stale instances."""
    items = recent_instance_snapshots(config, current_pid=current_pid, limit=max_items)
    if not items:
        return ""
    lines = ["MO instance notice: this terminal starts as an isolated instance."]
    for item in items:
        state = "live" if item.get("pid_alive") else "stale"
        instance = str(item.get("instance_id") or "legacy")[:32]
        surface = str(item.get("surface") or "unknown")[:24]
        slot = str(item.get("slot") or "")[:48]
        session_id = str(item.get("session_id") or "")[:48]
        age = _age_text(float(item.get("age_seconds") or 0.0))
        lines.append(
            f"  - {state}: pid {item.get('pid')} · instance {instance} · "
            f"{surface} · {age} ago · slot {slot or '-'} · session {session_id or '-'}"
        )
    lines.append("Singleton resources such as Telegram, scheduler, and Ghost tray/hotkey are resource-locked.")
    return "\n".join(lines)


def _sanitize_instance_id(value: str | None) -> str:
    text = "".join(ch for ch in str(value or "").strip() if ch.isalnum() or ch in "-_").strip("-_")
    return text[:24]


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _age_text(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def _legacy_agent_lock_snapshot(*, current: int, seen: set[int], now: float) -> dict[str, Any] | None:
    path = Path(tempfile.gettempdir()) / "mo-agent.lock"
    if not path.exists():
        return None
    try:
        pid = int(path.read_text(encoding="utf-8", errors="replace").strip())
    except Exception:
        return None
    if pid <= 0 or pid == current or pid in seen:
        return None
    try:
        created = path.stat().st_mtime
    except Exception:
        created = 0.0
    return {
        "pid": pid,
        "pid_alive": _pid_alive(pid),
        "age_seconds": max(0.0, now - created) if created else 0.0,
        "surface": "legacy-lock",
        "instance_id": "legacy",
        "slot": "",
        "session_id": "",
    }
