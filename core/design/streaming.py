"""Provider-tool delta bridge for flicker-free MO Design previews.

Only the ``mo_design`` tool's bounded visual fields are materialized.  Other
tool arguments are ignored and no provider payload is logged.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from core.state.paths import MO_DESIGN_RUNTIME_DIR, resolve_state_path
from core.utils.atomic_write import atomic_write_json


_LOCK = threading.RLock()
_STREAMS: dict[str, dict[str, Any]] = {}
_DESIGN_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")
_PREVIEW_INTERVAL_SECONDS = 0.15


def feed_tool_delta(stream_id: Any, *, name_delta: Any = "", arguments_delta: Any = "") -> None:
    key = str(stream_id or "default")[:160]
    if not key:
        return
    with _LOCK:
        row = _STREAMS.setdefault(key, {
            "name": "", "arguments": "", "last_preview_at": 0.0,
        })
        row["name"] += str(name_delta or "")
        row["arguments"] += str(arguments_delta or "")
        if len(row["name"]) > 80 or len(row["arguments"]) > 520_000:
            _STREAMS.pop(key, None)
            return
        if row["name"] != "mo_design":
            return
        now = time.monotonic()
        if now - float(row["last_preview_at"]) < _PREVIEW_INTERVAL_SECONDS:
            return
        snapshot = _snapshot(row["arguments"], key)
        if snapshot:
            row["last_preview_at"] = now
    if snapshot:
        path = live_preview_path(str(snapshot["design_id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, snapshot, indent=None, ensure_ascii=False)


def finish_tool_stream(stream_id: Any) -> None:
    with _LOCK:
        key = str(stream_id or "default")[:160]
        row = _STREAMS.pop(key, None)
        snapshot = _snapshot(str(row.get("arguments") or ""), key) if row and row.get("name") == "mo_design" else None
    if snapshot:
        path = live_preview_path(str(snapshot["design_id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, snapshot, indent=None, ensure_ascii=False)


def clear_live_preview(design_id: str = "") -> None:
    clean = _safe_design_id(design_id)
    if clean:
        live_preview_path(clean).unlink(missing_ok=True)


def read_live_preview(design_id: str) -> dict[str, Any] | None:
    clean = _safe_design_id(design_id)
    if not clean:
        return None
    path = live_preview_path(clean)
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if str(row.get("design_id") or "") != str(design_id or ""):
        return None
    if time.time() - float(row.get("updated_at") or 0) > 120:
        return None
    return row


def live_preview_path(design_id: str) -> Path:
    """Return the private per-design preview path after strict ID validation."""
    clean = _safe_design_id(design_id)
    if not clean:
        raise ValueError("invalid MO Design id")
    root = Path(resolve_state_path(MO_DESIGN_RUNTIME_DIR)) / "live"
    return root / f"{clean}.json"


def _snapshot(arguments: str, stream_id: str) -> dict[str, Any] | None:
    action = _partial_string(arguments, "action")
    if action and action != "update":
        return None
    design_id = _partial_string(arguments, "design_id")
    design_id = _safe_design_id(design_id)
    if not design_id:
        return None
    html = _partial_string(arguments, "html")
    css = _partial_string(arguments, "css")
    script = _partial_string(arguments, "script")
    if html is None and css is None and script is None:
        return None
    fields = [key for key, value in (("html", html), ("css", css), ("script", script)) if value is not None]
    if json.dumps("allow_scripts") in arguments:
        fields.append("allow_scripts")
    return {
        "stream_id": stream_id,
        "design_id": design_id,
        "html": html or "",
        "css": css or "",
        "script": script or "",
        "allow_scripts": _partial_boolean(arguments, "allow_scripts"),
        "fields": fields,
        "updated_at": time.time(),
    }


def _safe_design_id(value: Any) -> str:
    clean = str(value or "").strip().lower()
    return clean if _DESIGN_ID_RE.fullmatch(clean) else ""


def _partial_string(source: str, key: str) -> str | None:
    marker = json.dumps(str(key))
    start = source.find(marker)
    if start < 0:
        return None
    colon = source.find(":", start + len(marker))
    if colon < 0:
        return None
    quote = source.find('"', colon + 1)
    if quote < 0:
        return None
    out: list[str] = []
    escaped = False
    index = quote + 1
    while index < len(source):
        char = source[index]
        if escaped:
            if char == "u" and index + 4 < len(source):
                raw = source[index + 1:index + 5]
                try:
                    out.append(chr(int(raw, 16)))
                    index += 5
                    escaped = False
                    continue
                except ValueError:
                    pass
            out.append({"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f"}.get(char, char))
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            break
        else:
            out.append(char)
        index += 1
    return "".join(out)


def _partial_boolean(source: str, key: str) -> bool:
    marker = json.dumps(str(key))
    start = source.find(marker)
    if start < 0:
        return False
    colon = source.find(":", start + len(marker))
    return colon >= 0 and source[colon + 1:].lstrip().startswith("true")
