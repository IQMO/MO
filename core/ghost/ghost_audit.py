"""Private safe audit trail for Ghost side-chat and routing events."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from ..backend_monitor import redact_monitor_text
from ..env_utils import int_env
from ..path_defaults import ENV_MO_STATE_HOME, mo_home, private_state_enabled

LOG_PATH = Path("logs/ghost_audit.jsonl")


def _prune_ghost_audit_log(path: Path) -> None:
    max_bytes = max(0, int_env("MO_GHOST_AUDIT_MAX_BYTES", 1_000_000))
    if max_bytes <= 0:
        return
    try:
        if not path.exists() or path.stat().st_size <= max_bytes:
            return
        keep = max(1, int_env("MO_GHOST_AUDIT_KEEP_LINES", 2_000))
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-keep:]
        while len(("\n".join(lines) + "\n").encode("utf-8")) > max_bytes and len(lines) > 1:
            lines.pop(0)
        path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    except Exception:
        return


def append_ghost_audit(
    event: str,
    *,
    user_text: str = "",
    response_text: str = "",
    route: str = "",
    action: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    """Append a redacted Ghost audit event.

    Stores only high-level side-chat/routing facts. It intentionally does not
    store raw provider prompts, raw system prompts, or full context snapshots.
    """
    try:
        if os.environ.get("PYTEST_CURRENT_TEST") and os.environ.get("MO_GHOST_AUDIT_FORCE") != "1":
            return
        state_home = os.environ.get(ENV_MO_STATE_HOME, "").strip()
        if state_home:
            log_path = Path(state_home) / LOG_PATH
        elif private_state_enabled():
            log_path = mo_home() / LOG_PATH  # private-by-default, not the cwd
        else:
            log_path = LOG_PATH
        log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": round(time.time(), 3),
            "event": redact_monitor_text(event, 80),
            "user": redact_monitor_text(user_text, 700),
            "response": redact_monitor_text(response_text, 1200),
            "route": redact_monitor_text(route, 80),
            "action": redact_monitor_text(action, 120),
        }
        if extra:
            payload["extra"] = {str(k)[:80]: redact_monitor_text(v, 240) for k, v in extra.items()}
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        _prune_ghost_audit_log(log_path)
    except Exception:
        return
