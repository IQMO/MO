"""Private provider/model audit trail.

Records safe lifecycle facts about provider/model usage and switching without
storing prompts, tool payloads, responses, secrets, or provider internals.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from ..utils.atomic_write import atomic_write_text
from ..runtime.backend_monitor import redact_monitor_text
from ..utils.env_utils import int_env
from ..state.paths import resolve_state_path

LOG_PATH = Path("logs/provider_audit.jsonl")
DEFAULT_MAX_BYTES = 1_000_000
DEFAULT_KEEP_LINES = 2_000


def append_provider_audit(
    event: str,
    *,
    surface: str = "",
    provider: str = "",
    model: str = "",
    request: str | int = "",
    session_id: str = "",
    worker_id: str = "",
    reason: str = "",
    from_provider: str = "",
    from_model: str = "",
    to_provider: str = "",
    to_model: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    ok: bool | None = None,
) -> None:
    """Append one redacted provider/model event.

    Tests are silent by default to avoid polluting local logs; set
    MO_PROVIDER_AUDIT_FORCE=1 when testing the audit file directly.
    """
    try:
        if os.environ.get("PYTEST_CURRENT_TEST") and os.environ.get("MO_PROVIDER_AUDIT_FORCE") != "1":
            return
        log_path = Path(resolve_state_path(str(LOG_PATH), default=str(LOG_PATH)))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {
            "ts": round(time.time(), 3),
            "event": redact_monitor_text(str(event or ""), 80),
            "surface": redact_monitor_text(str(surface or ""), 80),
            "provider": redact_monitor_text(str(provider or ""), 80),
            "model": redact_monitor_text(str(model or ""), 120),
            "request": redact_monitor_text(str(request or ""), 40),
            "session_id": redact_monitor_text(str(session_id or ""), 120),
            "worker_id": redact_monitor_text(str(worker_id or ""), 80),
            "reason": redact_monitor_text(str(reason or ""), 180),
            "from_provider": redact_monitor_text(str(from_provider or ""), 80),
            "from_model": redact_monitor_text(str(from_model or ""), 120),
            "to_provider": redact_monitor_text(str(to_provider or ""), 80),
            "to_model": redact_monitor_text(str(to_model or ""), 120),
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "total_tokens": int(total_tokens or 0),
        }
        if ok is not None:
            payload["ok"] = bool(ok)
        if str(event or "") == "context_handoff":
            payload["text"] = "Context handoff audit record is orientation only, not proof."
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
        _prune_provider_audit_log(log_path)
    except Exception:
        return


def _prune_provider_audit_log(path: Path) -> None:
    """Keep provider audit recent and bounded without storing prompts/responses."""
    max_bytes = max(0, int_env("MO_PROVIDER_AUDIT_MAX_BYTES", DEFAULT_MAX_BYTES))
    if max_bytes <= 0:
        return
    try:
        if not path.exists() or path.stat().st_size <= max_bytes:
            return
        keep_lines = max(1, int_env("MO_PROVIDER_AUDIT_KEEP_LINES", DEFAULT_KEEP_LINES))
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-keep_lines:]
        while len(("\n".join(lines) + "\n").encode("utf-8")) > max_bytes and len(lines) > 1:
            lines.pop(0)
        atomic_write_text(path, "\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        return
