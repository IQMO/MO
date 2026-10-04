"""MO process instance identity and lightweight discovery helpers."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from ..state.paths import HEARTBEAT_LEDGER_PATH, resolve_state_path
from .lock import _pid_alive

ENV_MO_INSTANCE_ID = "MO_INSTANCE_ID"
# The window title MO gave its own terminal. Children it spawns (MO Desktop) inherit this and
# use it to recognise the terminal window they belong to — one terminal host process can own
# many windows, so the process alone does not identify it.
ENV_MO_TERMINAL_TITLE = "MO_TERMINAL_TITLE"
# The session slot MO terminal writes. Children must NOT derive this from MO_INSTANCE_ID: a child
# that has no id calls get_instance_id(), which setdefault()s a fresh one — the env would then be
# set and point at a session that does not exist. The terminal states its own slot instead.
ENV_MO_TERMINAL_SESSION = "MO_TERMINAL_SESSION"
MAX_INSTANCE_ID_CHARS = 64

_INSTANCE_ID: str | None = None
# Rows are appended in time order; this tolerates a clock that stepped back.
_LEDGER_ORDER_SLACK_SECONDS = 600.0


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


def recent_instance_snapshots(
    config: dict[str, Any] | None = None,
    *,
    current_pid: int | None = None,
    max_age_seconds: float = 24 * 3600,
    limit: int = 8,
) -> list[dict[str, Any]]:
    """Return latest heartbeat snapshot per other PID.

    This is intentionally observational: it does not kill or clean processes. It
    gives the startup display enough truth to distinguish live sibling instances from old
    stale heartbeat rows.
    """
    path = Path(resolve_state_path(HEARTBEAT_LEDGER_PATH, config))
    now = time.time()
    current = int(current_pid if current_pid is not None else os.getpid())
    seen: set[int] = set()
    out: list[dict[str, Any]] = []
    if not path.exists() or not path.is_file():
        return []
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
            if now - created > max_age_seconds + _LEDGER_ORDER_SLACK_SECONDS:
                break  # append-only ledger: every earlier row is older still
            continue
        seen.add(pid)
        item = dict(item)
        item["pid_alive"] = _pid_alive(pid)
        item["age_seconds"] = max(0.0, now - created) if created else 0.0
        out.append(item)
        if len(out) >= max(1, int(limit or 1)):
            break
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
    live = [item for item in items if item.get("pid_alive")]
    stale_count = sum(1 for item in items if not item.get("pid_alive"))
    lines = ["MO instance notice: this terminal starts as an isolated instance."]
    # List only LIVE sibling instances (the ones that actually matter for
    # multi-instance coordination); collapse stale history to one line so the
    # header isn't flooded with dead-pid entries.
    for item in live:
        instance = str(item.get("instance_id") or "unknown")[:32]
        surface = str(item.get("surface") or "unknown")[:24]
        slot = str(item.get("slot") or "")[:48]
        session_id = str(item.get("session_id") or "")[:48]
        age = _age_text(float(item.get("age_seconds") or 0.0))
        lines.append(
            f"  - live: pid {item.get('pid')} · instance {instance} · "
            f"{surface} · {age} ago · slot {slot or '-'} · session {session_id or '-'}"
        )
    if stale_count:
        lines.append(f"  - {stale_count} stale instance(s) — use /heartbeat instances to refresh")
    lines.append("Singleton resources such as Telegram and scheduler are resource-locked.")
    return "\n".join(lines)


def _sanitize_instance_id(value: str | None) -> str:
    text = "".join(ch for ch in str(value or "").strip() if ch.isalnum() or ch in "-_").strip("-_")
    # Hub-owned and portable-conversation terminals deliberately use longer
    # opaque ids. Preserve the full protocol-bounded value so their advertised
    # Live Control identity remains exactly equal to the request-owned id.
    return text[:MAX_INSTANCE_ID_CHARS]


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


