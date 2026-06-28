"""Safe backend monitor events for MO proof work."""
from __future__ import annotations

import contextvars
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any
import traceback

from ..state.paths import ENV_MO_STATE_HOME, mo_home, private_state_enabled

SAFE_EVENT_TYPES = {
    "taskboard",
    "backend_status",
    "provider_request",
    "provider_response",
    "assistant_text",
    "provider_error",
    "provider_fallback",
    "tool_call",
    "tool_result",
    "turn_limit",
    "memory_index",
    "memory_recall",
    "memory_cleanup",
    "memory_fts5_warning",
    "sandbox_guard",
    "sandbox_blocked",
    "goal_step",
    "goal_auditor",
    "goal_finish",
    "worker_event",
    "code_graph_context",
    "tool_compress",
    "live_steer",
    "session_quarantine",
    "turn_start",
    "turn_context",
    "turn_health",
    "turn_intercept",
    "turn_end",
    "turn_error",
    "session_event",
    "session_compact",
    "slash_command",
    "context_handoff",
    "consistency_boundary",
    "ghost_event",
    "board_advance",
    "prt_event",
    "prt_review",
    "heartbeat",
}

_ACTIVE_MONITOR: "BackendMonitor | None" = None
_MONITOR_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("mo_backend_monitor_context")


def set_monitor(monitor: "BackendMonitor | None") -> None:
    global _ACTIVE_MONITOR
    _ACTIVE_MONITOR = monitor


def get_monitor() -> "BackendMonitor | None":
    return _ACTIVE_MONITOR


def active_monitor_path() -> Path | None:
    """Return the active monitor path only when it belongs to the current monitor dir."""
    monitor = get_monitor()
    path = Path(getattr(monitor, "path", "")) if monitor is not None else None
    if not path or not path.exists():
        return None
    try:
        if path.parent.resolve() != BackendMonitor._monitor_dir().resolve():
            return None
    except Exception:
        return None
    return path


SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,'\"}]+"),
    re.compile(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,'\"}]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b"),
    re.compile(r"(?i)(password\s*[:=]\s*)[^\s,'\"}]+"),
    re.compile(r"(?i)(token\s*[:=]\s*)[^\s,'\"}]+"),
    re.compile(r"(?i)(/bot)[0-9]{5,}:[A-Za-z0-9_-]+"),
)


def redact_monitor_text(value: Any, limit: int = 700) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for pattern in SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}[redacted]" if match.groups() else "[redacted]", text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    if len(text) > limit:
        return text[:limit].rstrip() + "…"
    return text


@contextmanager
def monitor_context(**values: Any):
    """Attach best-effort correlation fields to monitor events in this context."""
    current = dict(_MONITOR_CONTEXT.get({}) or {})
    clean = {str(k): v for k, v in values.items() if v not in (None, "")}
    token = _MONITOR_CONTEXT.set({**current, **clean})
    try:
        yield
    finally:
        try:
            _MONITOR_CONTEXT.reset(token)
        except Exception:
            traceback.print_exc()


def current_monitor_context() -> dict[str, Any]:
    """Return the active monitor correlation context for gate/runtime hooks."""
    try:
        return dict(_MONITOR_CONTEXT.get({}) or {})
    except Exception:
        traceback.print_exc()
        return {}


def _safe_monitor_value(value: Any, *, limit: int = 6000) -> Any:
    if isinstance(value, dict):
        return {redact_monitor_text(k, 120): _safe_monitor_value(v, limit=limit) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_monitor_value(item, limit=limit) for item in list(value)[:80]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact_monitor_text(value, limit)


def tool_call_names(tool_calls: Any) -> list[str]:
    """Extract tool/function names from a tool_calls list.

    Accepts both provider-SDK objects (``tc.function.name``) and plain dicts
    (``tc["function"]["name"]``). Missing names render as ``"?"``. Single source
    of truth for transcript/preview flattening across monitor, ghost, and handoff.
    """
    names: list[str] = []
    for tc in tool_calls or []:
        fn = getattr(tc, "function", None)
        if fn is None and isinstance(tc, dict):
            fn = tc.get("function", tc)
        if hasattr(fn, "name"):
            names.append(str(getattr(fn, "name", "?") or "?"))
        elif isinstance(fn, dict):
            names.append(str(fn.get("name") or "?"))
        else:
            names.append("?")
    return names


def preview_provider_messages(messages: list[dict], limit: int = 700) -> str:
    preview_lines: list[str] = []
    for msg in messages[-6:]:
        role = str(msg.get("role") or "?")
        if role == "system":
            preview_lines.append("system: [system prompt hidden]")
            continue
        content = msg.get("content") or ""
        if role == "tool":
            preview_lines.append(f"tool: [tool result chars={len(str(content))}]")
            continue
        if msg.get("tool_calls"):
            names = tool_call_names(msg.get("tool_calls"))
            preview_lines.append(f"assistant: [tool calls: {', '.join(names)}]")
            continue
        preview_lines.append(f"{role}: {redact_monitor_text(content, 220)}")
    return redact_monitor_text("\n".join(preview_lines), limit)


def preview_provider_response(content: str, tool_calls: list[Any] | None, limit: int = 700) -> str:
    parts: list[str] = []
    if content:
        parts.append(redact_monitor_text(content, 420))
    if tool_calls:
        parts.append("tool calls: " + ", ".join(tool_call_names(tool_calls)))
    return redact_monitor_text("\n".join(parts), limit)


class BackendMonitor:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else self._new_run_path()
        self.run_id = uuid.uuid4().hex[:12]
        self._seq = 0
        self._lock = threading.Lock()
        self.enabled = not self._disabled_by_env()
        if self.enabled:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if path is None:
                    self._cleanup_old_logs(keep=50, min_age_seconds=3600)
            except Exception:
                # Diagnostics must never stop MO startup.
                traceback.print_exc()
        self.process: subprocess.Popen | None = None

    @staticmethod
    def _disabled_by_env() -> bool:
        return os.environ.get("MO_BACKEND_MONITOR_DISABLED") == "1" or os.environ.get("MO_BACKEND_MONITOR") == "0"

    @staticmethod
    def _monitor_dir() -> Path:
        configured = os.environ.get("MO_BACKEND_MONITOR_DIR")
        if configured:
            return Path(configured)
        state_home = os.environ.get(ENV_MO_STATE_HOME, "").strip()
        if state_home:
            return Path(state_home) / "logs" / "monitor"
        if private_state_enabled():
            return mo_home() / "logs" / "monitor"  # private-by-default, not the cwd
        return Path("logs/monitor")

    @staticmethod
    def _new_run_path() -> Path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        suffix = uuid.uuid4().hex[:8]
        return BackendMonitor._monitor_dir() / f"backend_monitor-{stamp}-{suffix}.jsonl"

    @staticmethod
    def _cleanup_old_logs(keep: int = 50, min_age_seconds: float = 3600) -> None:
        """Keep recent monitor logs without deleting an active live-session log.

        Tests and short-lived Gateways can create many monitors quickly; deleting
        logs that are only seconds old destroys the evidence trail for a running
        MO session. Cleanup therefore only removes logs beyond the keep count
        after they are old enough to be safely considered historical.
        """
        parent = BackendMonitor._monitor_dir()
        if not parent.exists():
            return
        now = time.time()
        files = sorted(parent.glob("backend_monitor-*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep:]:
            try:
                if now - old.stat().st_mtime < min_age_seconds:
                    continue
                old.unlink()
            except OSError:
                pass

    def emit(self, event_type: str, payload: dict | str) -> None:
        """Append a monitor event, best-effort only.

        The backend monitor is diagnostics, not runtime authority.  Disk/path/JSON
        failures are swallowed so deleting or breaking the monitor never changes
        MO's provider/tool/session behavior.
        """
        if not self.enabled or event_type not in SAFE_EVENT_TYPES:
            return
        try:
            safe_payload = payload if isinstance(payload, dict) else {"message": str(payload)}
            safe_payload = _safe_monitor_value(safe_payload)
            if not isinstance(safe_payload, dict):
                safe_payload = {"message": str(safe_payload)}
            context = _safe_monitor_value(_MONITOR_CONTEXT.get({}) or {})
            if isinstance(context, dict):
                for key, value in context.items():
                    safe_payload.setdefault(key, value)
            with self._lock:
                self._seq += 1
                event = {
                    "ts": round(time.time(), 3),
                    "run_id": self.run_id,
                    "seq": self._seq,
                    "type": event_type,
                    "payload": safe_payload,
                }
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            # Operator lifecycle hooks ride the same event stream (outside the
            # lock; fire-and-forget; can never raise into the turn loop).
            from .hooks import dispatch_hooks
            dispatch_hooks(event_type, safe_payload)
        except Exception:
            return

    def emit_text(self, text: str) -> None:
        self.emit("backend_status", {"message": text})

    def open_window(self) -> None:
        if not self.enabled or os.environ.get("MO_OPEN_BACKEND_MONITOR") != "1":
            return
        if self.process and self.process.poll() is None:
            return
        root = Path(__file__).resolve().parents[1]
        # Optional local diagnostic launchers resolve from the profile extension
        # root, not the checkout. Absent on user clones, open_window no-ops.
        from ..state.paths import operator_pack_root
        monitor = operator_pack_root() / "mo_monitor.py"
        if not monitor.exists():
            return
        log_path = str(self.path.resolve())
        os.environ["MO_BACKEND_MONITOR_PATH"] = log_path
        self.process = subprocess.Popen(
            [sys.executable, str(monitor), log_path],
            cwd=str(root),
            stdout=None,
            stderr=None,
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
        )

    def close_window(self) -> None:
        if not self.process or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)


def latest_monitor_path() -> Path | None:
    """Newest backend-monitor jsonl (the current/most recent session), or None."""
    parent = BackendMonitor._monitor_dir()
    if not parent.exists():
        return None
    files = sorted(parent.glob("backend_monitor-*.jsonl"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


# Surfaces whose turns are NOT part of a Main-MO logical run and
# must be excluded from its economy record — Ghost/desktop activity shares the same
# per-process monitor file but is a separate entity (see the multi-instance proposal).
GHOST_SURFACES = frozenset({"desktop", "ghost", "companion"})


def economy_summary(
    monitor_path: str | Path | None = None,
    *,
    turn_ids: "set[str] | frozenset[str] | None" = None,
    session_ids: "set[str] | frozenset[str] | None" = None,
    exclude_surfaces: "set[str] | frozenset[str] | None" = None,
    instance_ids: "set[str] | frozenset[str] | None" = None,
) -> dict[str, Any]:
    """Deterministic provider/tool/error/compression counts from a backend monitor.

    The authoritative source of session economy so records never need to be
    estimated or stale.
    Tool errors are counted from ``tool_result.error``/``is_error`` and blocks from
    ``tool_result.blocked`` (NOT only the rarer ``tool_error``/``sandbox_blocked``
    event types — those miss a failed ``tool_result`` that later recovered).

    Logical-run scoping (multi-instance proposal, amendment #5): one process monitor can
    hold a Main-MO run PLUS handoff segments PLUS interleaved Ghost/desktop turns.
    ``turn_ids`` restricts counting to exact turn correlation ids. ``session_ids``
    restricts counting to a run's segment ids. ``instance_ids`` can further restrict
    to specific process instances. ``exclude_surfaces`` drops events tagged by either
    route_source or normalized surface (e.g. ``GHOST_SURFACES``).
    Defaults preserve whole-file behavior for existing callers.
    """
    path = Path(monitor_path) if monitor_path else latest_monitor_path()
    out = {
        "source": path.name if path else None,
        "provider_requests": 0, "provider_responses": 0, "provider_errors": 0,
        "tool_calls": 0, "tool_errors": 0, "sandbox_blocked": 0,
        "compression_events": 0,
        "error_tools": [], "blocked_tools": [],
    }
    if not path or not Path(path).exists():
        return out
    _err_tools: "set[str]" = set()
    _blk_tools: "set[str]" = set()
    for line in Path(path).open(encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        t = d.get("type")
        p = d.get("payload", {}) or {}
        if turn_ids is not None:
            tid = p.get("turn_id") or d.get("turn_id")
            if tid not in turn_ids:
                continue
        if session_ids is not None:
            sid = p.get("session_id") or d.get("session_id")
            if sid not in session_ids:
                continue  # not part of this logical run's segments
        if instance_ids is not None:
            iid = p.get("instance_id") or d.get("instance_id")
            if iid not in instance_ids:
                continue
        if exclude_surfaces:
            exclude = {_monitor_surface_key(item) for item in exclude_surfaces}
            surfaces = _monitor_surface_keys(p, d)
            if exclude.intersection(surfaces):
                continue  # Ghost/desktop turn — not part of the Main-MO run
        if t == "provider_request":
            out["provider_requests"] += 1
        elif t == "provider_response":
            out["provider_responses"] += 1
        elif t == "provider_error":
            out["provider_errors"] += 1
        elif t == "tool_call":
            out["tool_calls"] += 1
        elif t == "tool_result":
            if p.get("error") or p.get("is_error"):
                out["tool_errors"] += 1
                _err_tools.add(str(p.get("tool") or ""))
            if p.get("blocked"):
                out["sandbox_blocked"] += 1
                _blk_tools.add(str(p.get("tool") or ""))
        elif t == "tool_error":
            out["tool_errors"] += 1
            _err_tools.add(str(p.get("tool") or ""))
        elif t == "sandbox_blocked":
            out["sandbox_blocked"] += 1
            _blk_tools.add(str(p.get("tool") or ""))
        elif t == "tool_compress":
            out["compression_events"] += 1
    out["error_tools"] = sorted(t for t in _err_tools if t)
    out["blocked_tools"] = sorted(t for t in _blk_tools if t)
    return out


def _monitor_surface_key(value: Any) -> str:
    raw = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "": "",
        "user": "terminal",
        "main": "terminal",
        "pc": "terminal",
        "companion": "desktop",
    }
    return aliases.get(raw, raw)


def _monitor_surface_keys(payload: dict[str, Any], event: dict[str, Any]) -> set[str]:
    keys: set[str] = set()
    for value in (
        payload.get("route_source"),
        payload.get("surface"),
        event.get("route_source"),
        event.get("surface"),
    ):
        raw = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
        if raw:
            keys.add(raw)
        normalized = _monitor_surface_key(raw)
        if normalized:
            keys.add(normalized)
    return keys


def format_economy_record(summary: dict[str, Any] | None = None) -> str:
    """Markdown economy block from `economy_summary()` — the canonical Gate 2f record."""
    s = summary or economy_summary()
    lines = [
        "### Economy Record (Gate 2f — runtime/monitor, authoritative)\n"
        f"- Source: {s.get('source')}\n"
        f"- Provider requests: {s.get('provider_requests', 0)} "
        f"(responses: {s.get('provider_responses', 0)}, errors: {s.get('provider_errors', 0)})\n"
        f"- Tool calls: {s.get('tool_calls', 0)} "
        f"(errors: {s.get('tool_errors', 0)}, sandbox-blocked: {s.get('sandbox_blocked', 0)})\n"
        f"- Compression events: {s.get('compression_events', 0)}\n"
    ]
    error_tools = [str(t).strip() for t in (s.get("error_tools") or []) if str(t).strip()]
    blocked_tools = [str(t).strip() for t in (s.get("blocked_tools") or []) if str(t).strip()]
    if error_tools:
        lines.append(f"- Error tools: {', '.join(sorted(error_tools))}\n")
    if blocked_tools:
        lines.append(f"- Blocked tools: {', '.join(sorted(blocked_tools))}\n")
    return "".join(lines)
