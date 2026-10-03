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

from ..runtime.backend_monitor import current_monitor_context, redact_monitor_text
from ..state.paths import resolve_state_path
from ..utils.jsonl_utils import prune_jsonl_log

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
    turn_id: str = "",
    worker_id: str = "",
    reason: str = "",
    from_provider: str = "",
    from_model: str = "",
    to_provider: str = "",
    to_model: str = "",
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    cache_hit_tokens: int = 0,
    cache_miss_tokens: int = 0,
    cache_write_tokens: int = 0,
    cache_key_tag: str = "",
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
        if not turn_id:
            turn_id = str(current_monitor_context().get("turn_id") or "")
        payload: dict[str, Any] = {
            "ts": round(time.time(), 3),
            "event": redact_monitor_text(str(event or ""), 80),
            "surface": redact_monitor_text(str(surface or ""), 80),
            "provider": redact_monitor_text(str(provider or ""), 80),
            "model": redact_monitor_text(str(model or ""), 120),
            "request": redact_monitor_text(str(request or ""), 40),
            "session_id": redact_monitor_text(str(session_id or ""), 120),
            "turn_id": redact_monitor_text(str(turn_id or ""), 120),
            "worker_id": redact_monitor_text(str(worker_id or ""), 80),
            "reason": redact_monitor_text(str(reason or ""), 180),
            "from_provider": redact_monitor_text(str(from_provider or ""), 80),
            "from_model": redact_monitor_text(str(from_model or ""), 120),
            "to_provider": redact_monitor_text(str(to_provider or ""), 80),
            "to_model": redact_monitor_text(str(to_model or ""), 120),
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "total_tokens": int(total_tokens or 0),
            "cache_hit_tokens": int(cache_hit_tokens or 0),
            "cache_miss_tokens": int(cache_miss_tokens or 0),
        }
        if cache_write_tokens:
            payload["cache_write_tokens"] = int(cache_write_tokens)
        if cache_key_tag:
            payload["cache_key_tag"] = redact_monitor_text(str(cache_key_tag), 20)
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
    prune_jsonl_log(
        path,
        env_max_bytes_var="MO_PROVIDER_AUDIT_MAX_BYTES",
        env_keep_lines_var="MO_PROVIDER_AUDIT_KEEP_LINES",
        default_max_bytes=DEFAULT_MAX_BYTES,
        default_keep_lines=DEFAULT_KEEP_LINES,
    )
