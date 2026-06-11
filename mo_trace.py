#!/usr/bin/env python3
# ruff: noqa: E402
"""mo_trace.py — Session recorder and behavior validator for MO Agent.

Records agent signals from a single turn or a full interactive session into a
timestamped .trace file, then runs built-in validators and prints a pass/fail
report.

Usage:
    python mo_trace.py run "Your prompt here"
    python mo_trace.py serve [mo-args...]
    python mo_trace.py replay <trace_file>
    python mo_trace.py list
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

# Allow running from any working directory while keeping imports stable.
_AGENT_ROOT = Path(__file__).resolve().parent
os.chdir(_AGENT_ROOT)
sys.path.insert(0, str(_AGENT_ROOT))

from core.text_safety import configure_utf8_stdio

configure_utf8_stdio()

TRACE_DIR = Path("memory/traces")

# Fixed JSONL sources monitored during a session. Backend monitor logs are
# timestamped files and are discovered separately.
JSONL_PATHS: list[tuple[str, str]] = [
    ("logs/tool_audit.jsonl", "tool"),
    ("logs/ghost_audit.jsonl", "ghost"),
    ("memory/file_operations.jsonl", "fileops"),
    ("logs/provider_audit.jsonl", "provider"),
    ("memory/learning_suggestions.jsonl", "learning_suggestions"),
]


# ─── helpers ─────────────────────────────────────────────────────────────────


def _read_jsonl_tail(path: str | Path, since_bytes: int = 0) -> list[dict[str, Any]]:
    """Read JSONL entries appended after ``since_bytes``.

    If a file was rotated/truncated during the session, read the current file
    from the beginning instead of silently dropping evidence.
    """
    path = Path(path)
    if not path.exists():
        return []
    total = path.stat().st_size
    if total <= 0:
        return []
    offset = max(0, int(since_bytes or 0))
    if total < offset:
        offset = 0
    if total == offset:
        return []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        fh.seek(offset)
        new_data = fh.read()
    entries: list[dict[str, Any]] = []
    for line in new_data.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _trace_config() -> dict[str, Any]:
    """Best-effort load of the active runtime config without changing behavior."""
    try:
        from core.path_defaults import default_config_path
        from core.provider.provider import load_config

        config_path = default_config_path(
            agent_root=str(_AGENT_ROOT),
            caller_cwd=os.environ.get("MO_PROJECT_CWD", str(_AGENT_ROOT)),
        )
        if Path(config_path).exists():
            return load_config(config_path)
    except Exception:
        traceback.print_exc()
    return {}


def _resolve_state_artifact(path: str | Path, config: dict[str, Any] | None = None) -> Path:
    """Resolve a runtime artifact path the same way MO private state does.

    Trace must work in clean ``MO_HOME`` runs, not only in the checkout.  If no
    config/private home is available (tests, legacy checkout state), keep the
    relative path relative to the current cwd.
    """
    raw = str(path or "")
    p = Path(raw)
    if p.is_absolute():
        return p
    try:
        from core.path_defaults import resolve_state_path
        if config:
            return Path(resolve_state_path(raw, config))
    except Exception:
        traceback.print_exc()
    state_home = os.environ.get("MO_STATE_HOME", "").strip() or os.environ.get("MO_HOME", "").strip()
    if state_home:
        return Path(state_home) / p
    return p


def _backend_monitor_paths(config: dict[str, Any] | None = None) -> list[Path]:
    dirs: list[Path] = []
    configured = os.environ.get("MO_BACKEND_MONITOR_DIR", "").strip()
    if configured:
        dirs.append(Path(configured))
    dirs.append(_resolve_state_artifact("logs/monitor", config))
    dirs.append(Path("logs/monitor"))

    seen: set[str] = set()
    paths: list[Path] = []
    for directory in dirs:
        for path in sorted(directory.glob("backend_monitor*.jsonl")) if directory.exists() else []:
            key = str(path.resolve(strict=False))
            if key not in seen:
                paths.append(path)
                seen.add(key)
    return paths


def _source_paths(config: dict[str, Any] | None = None) -> dict[str, list[Path]]:
    cfg = config or _trace_config()
    sources: dict[str, list[Path]] = {}
    for rel_path, name in JSONL_PATHS:
        source_path: str | Path = rel_path
        if name == "tool":
            sandbox = cfg.get("sandbox", {}) if isinstance(cfg.get("sandbox", {}), dict) else {}
            source_path = sandbox.get("audit_log") or rel_path
        paths = [_resolve_state_artifact(source_path, cfg)]
        legacy = Path(rel_path)
        if str(legacy) not in {str(p) for p in paths}:
            paths.append(legacy)
        sources[name] = paths
    sources["backend"] = _backend_monitor_paths(cfg)
    return sources


def _file_sizes(config: dict[str, Any] | None = None) -> dict[str, dict[str, int]]:
    sizes: dict[str, dict[str, int]] = {}
    for name, paths in _source_paths(config).items():
        sizes[name] = {str(path): path.stat().st_size for path in paths if path.exists()}
    return sizes


def _collect_jsonl_delta(
    sizes_before: dict[str, dict[str, int]],
    config: dict[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    delta: dict[str, list[dict[str, Any]]] = {}
    sources = _source_paths(config)
    for name, before_by_path in sizes_before.items():
        for path_str in before_by_path:
            path = Path(path_str)
            if path not in sources.setdefault(name, []):
                sources[name].append(path)
    for name, paths in sources.items():
        entries: list[dict[str, Any]] = []
        before_by_path = sizes_before.get(name, {})
        seen: set[str] = set()
        for path in paths:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            entries.extend(_read_jsonl_tail(path, since_bytes=before_by_path.get(key, 0)))
        delta[name] = entries
    return delta


@contextmanager
def _trace_environment(session_id: str):
    """Enable backend monitor capture for the traced process only."""
    keys = ["MO_BACKEND_MONITOR_DIR", "MO_BACKEND_MONITOR", "MO_BACKEND_MONITOR_DISABLED"]
    old = {key: os.environ.get(key) for key in keys}
    monitor_dir = (TRACE_DIR / session_id / "monitor").resolve()
    monitor_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MO_BACKEND_MONITOR_DIR"] = str(monitor_dir)
    os.environ["MO_BACKEND_MONITOR"] = "1"
    os.environ.pop("MO_BACKEND_MONITOR_DISABLED", None)
    try:
        yield monitor_dir
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _snapshot_learning(db_path: str | Path = "memory/learning.sqlite", config: dict[str, Any] | None = None) -> dict[str, Any]:
    db_path = _resolve_state_artifact(db_path, config)
    if not db_path.exists():
        return {"status": "not_found", "path": str(db_path)}
    try:
        conn = sqlite3.connect(str(db_path))
        cur = conn.execute("SELECT COUNT(*) FROM turns")
        turn_count = int(cur.fetchone()[0])
        cur = conn.execute("SELECT COUNT(*) FROM turns_fts")
        indexed = int(cur.fetchone()[0])
        cur = conn.execute("SELECT MIN(rowid), MAX(rowid) FROM turns")
        id_range = cur.fetchone()
        cur = conn.execute("SELECT turn_id, user, updated_at FROM turns ORDER BY rowid DESC LIMIT 5")
        recent = [{"turn_id": row[0], "user_preview": row[1][:50] if row[1] else "", "updated_at": row[2]} for row in cur.fetchall()]
        conn.close()
        return {
            "status": "ok",
            "total_turns": turn_count,
            "indexed": indexed,
            "rowid_range": {"min": id_range[0], "max": id_range[1]} if id_range else {},
            "recent": recent,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _snapshot_closeout_dir(path: str | Path = "memory/session_closeouts", config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Count closeout files and flag unresolved markers."""
    p = _resolve_state_artifact(path, config)
    if not p.exists():
        return {"status": "not_found"}
    try:
        files = sorted(p.glob("*.md"))
        unresolved = 0
        for f in files:
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
                if "**unresolved**" in content.lower() or "open task" in content.lower():
                    unresolved += 1
            except Exception:
                traceback.print_exc()
        return {
            "status": "ok",
            "total_files": len(files),
            "unresolved_count": unresolved,
            "latest": str(files[-1].name) if files else None,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _snapshot_graph_artifacts(path: str | Path = "memory/structural_graph", config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Check active structural graph status without building it."""
    try:
        from core.graph.structural_graph import graph_status
        status = graph_status(os.environ.get("MO_PROJECT_CWD") or str(_AGENT_ROOT))
        if status.get("available"):
            return {"status": "ok", **status}
    except Exception:
        traceback.print_exc()
    p = _resolve_state_artifact(path, config)
    if not p.exists():
        return {"status": "not_found", "path": str(p)}
    try:
        graph_file = p / "graph.json"
        if not graph_file.exists():
            return {"status": "no_graph_json", "path": str(graph_file)}
        st = graph_file.stat()
        return {
            "status": "ok",
            "size_bytes": st.st_size,
            "age_seconds": time.time() - st.st_mtime,
            "path": str(graph_file),
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _snapshot_heartbeat(path: str | Path = "memory/heartbeat", config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Count heartbeat entries."""
    p = _resolve_state_artifact(path, config)
    if not p.exists():
        return {"status": "not_found"}
    try:
        hb_file = p / "heartbeats.jsonl"
        if not hb_file.exists():
            return {"status": "no_heartbeat_file"}
        count = 0
        with hb_file.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.strip():
                    count += 1
        return {"status": "ok", "entry_count": count}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def _snapshot_profile_files(path: str | Path = "memory/profile", config: dict[str, Any] | None = None) -> dict[str, Any]:
    """List profile .md files and their sizes."""
    p = _resolve_state_artifact(path, config)
    if not p.exists():
        return {"status": "not_found", "path": str(p)}
    try:
        files = {}
        for f in sorted(p.glob("*.md")):
            files[f.name] = f.stat().st_size
        return {"status": "ok", "path": str(p), "files": files, "count": len(files)}
    except Exception as exc:
        return {"status": "error", "error": str(exc), "path": str(p)}


def _count_jsonl(path: Path) -> int:
    try:
        if not path.exists():
            return 0
        return sum(1 for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())
    except Exception:
        return 0


def _snapshot_runtime_artifacts(config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Snapshot mission-relevant private runtime artifacts before/after a trace."""
    cfg = config or _trace_config()

    def p(rel: str) -> Path:
        return _resolve_state_artifact(rel, cfg)

    def file_meta(rel: str) -> dict[str, Any]:
        path = p(rel)
        try:
            if not path.exists():
                return {"status": "not_found", "path": str(path)}
            st = path.stat()
            return {"status": "ok", "path": str(path), "size_bytes": st.st_size, "line_count": _count_jsonl(path) if path.suffix == ".jsonl" else None}
        except Exception as exc:
            return {"status": "error", "path": str(path), "error": str(exc)}

    def dir_meta(rel: str, pattern: str = "*") -> dict[str, Any]:
        path = p(rel)
        try:
            if not path.exists():
                return {"status": "not_found", "path": str(path), "count": 0}
            items = sorted(path.glob(pattern))
            latest = max((item.stat().st_mtime for item in items), default=0.0)
            return {"status": "ok", "path": str(path), "count": len(items), "latest_mtime": latest or None}
        except Exception as exc:
            return {"status": "error", "path": str(path), "error": str(exc)}

    return {
        "closeouts": _snapshot_closeout_dir(config=cfg),
        "graph": _snapshot_graph_artifacts(config=cfg),
        "heartbeat": _snapshot_heartbeat(config=cfg),
        "profile": _snapshot_profile_files(config=cfg),
        "sessions": dir_meta("memory/sessions", "*.json*"),
        "taskboards": file_meta("memory/taskboards/taskboards.jsonl"),
        "goal_runs": dir_meta("memory/goal-runs", "*.json"),
        "scheduler_jobs": file_meta("memory/scheduler/jobs.json"),
        "scheduler_runs": file_meta("memory/scheduler/runs.jsonl"),
        "telegram_db": file_meta("memory/telegram.sqlite"),
        "learning_suggestions": file_meta("memory/learning_suggestions.jsonl"),
    }


class _TraceMonitor:
    """In-memory event recorder used as a BackendMonitor-like collector."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, etype: str, **payload: Any) -> None:
        self.events.append({"type": etype, "payload": payload, "ts": time.time()})


# ─── JSONL event normalization ───────────────────────────────────────────────


def _safe_ts(entry: dict[str, Any]) -> float:
    try:
        return float(entry.get("ts") or time.time())
    except (TypeError, ValueError):
        return time.time()


def _events_from_jsonl_entry(source: str, entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert source-specific JSONL rows into validator-native events."""
    ts = _safe_ts(entry)
    if source == "backend":
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {k: v for k, v in entry.items() if k not in {"ts", "type"}}
        return [{"type": str(entry.get("type") or "backend_event"), "payload": payload, "ts": ts, "source": source}]
    if source == "tool":
        payload = {
            "tool": str(entry.get("tool") or "?"),
            "surface": str(entry.get("surface") or ""),
            "worker_id": str(entry.get("worker_id") or ""),
            "blocked": bool(entry.get("blocked")),
            "error": bool(entry.get("blocked")),
            "result_chars": int(entry.get("result_chars") or 0),
            "summary": {"argument_keys": sorted((entry.get("arguments") or {}).keys()) if isinstance(entry.get("arguments"), dict) else []},
        }
        return [
            {"type": "tool_call", "payload": payload, "ts": ts, "source": source},
            {"type": "tool_result", "payload": payload, "ts": ts, "source": source},
        ]
    if source == "provider":
        event = str(entry.get("event") or "provider_audit")
        return [{"type": event, "payload": dict(entry), "ts": ts, "source": source}]
    if source == "ghost":
        return [{"type": "ghost_event", "payload": dict(entry), "ts": ts, "source": source}]
    if source == "fileops":
        return [{"type": "file_operation", "payload": dict(entry), "ts": ts, "source": source}]
    if source == "learning_suggestions":
        return [{"type": "learning_suggestion", "payload": dict(entry), "ts": ts, "source": source}]
    return [{"type": f"_{source}", "payload": dict(entry), "ts": ts, "source": source}]


def _events_from_jsonl_delta(jsonl_delta: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for source, entries in jsonl_delta.items():
        for entry in entries:
            if isinstance(entry, dict):
                events.extend(_events_from_jsonl_entry(source, entry))
    return events


_WORK_PROMPT_RE = re.compile(
    r"\b(read|edit|write|create|fix|debug|test|run|inspect|audit|review|grep|find|search|implement|refactor|file|repo|code|tool|e2e|mission|commit|push)\b",
    re.I,
)
_SIMPLE_PROMPT_RE = re.compile(r"^\s*(hi|hello|hey|thanks|thank you|ok|okay|who are you\??|what are you\??)\s*$", re.I)
_SECRET_LEAK_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9_]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
]


def _prompt_kind(prompt: str) -> str:
    text = str(prompt or "").strip()
    if not text:
        return "empty"
    if text.startswith("/"):
        return "local_command"
    if _SIMPLE_PROMPT_RE.match(text):
        return "simple"
    if _WORK_PROMPT_RE.search(text):
        return "work"
    return "chat"


def _trace_events(trace: dict[str, Any]) -> list[dict[str, Any]]:
    events = trace.get("events", [])
    return events if isinstance(events, list) else []


def _current_turn_events(trace: dict[str, Any]) -> list[dict[str, Any]]:
    """Return events scoped to this served turn when turn_start is present."""
    events = sorted(_trace_events(trace), key=lambda e: float(e.get("ts") or 0.0))
    turn_starts = [float(e.get("ts") or 0.0) for e in events if str(e.get("type") or "") == "turn_start"]
    if not turn_starts:
        return events
    start_ts = min(turn_starts)
    return [e for e in events if float(e.get("ts") or 0.0) >= start_ts]


def _trace_jsonl(trace: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    delta = trace.get("jsonl_delta", {})
    return delta if isinstance(delta, dict) else {}


def _has_event(events: list[dict[str, Any]], *types: str) -> bool:
    wanted = set(types)
    return any(str(e.get("type") or "") in wanted for e in events)


def _provider_activity(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if str(e.get("type") or "").startswith("provider_") or str(e.get("payload", {}).get("event") or "").startswith("provider_")]


def _event_surface(event: dict[str, Any]) -> str:
    payload = event.get("payload", {}) if isinstance(event.get("payload", {}), dict) else {}
    return str(payload.get("surface") or "")


def _main_provider_activity(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in _provider_activity(events) if not _event_surface(e).startswith("ghost")]


def _work_expected(trace: dict[str, Any]) -> bool:
    kind = _prompt_kind(str(trace.get("prompt") or ""))
    if kind == "work":
        return True
    events = _trace_events(trace)
    return _has_event(events, "tool_call", "tool_result", "turn_context")


def _tuple3(result: Any, *, default_status: str | None = None) -> tuple[bool, str, str]:
    if isinstance(result, tuple) and len(result) >= 3:
        return bool(result[0]), str(result[1]), str(result[2])
    if isinstance(result, tuple) and len(result) >= 2:
        passed = bool(result[0])
        status = default_status or ("pass" if passed else "fail")
        return passed, str(result[1]), status
    return False, f"Invalid validator result: {result!r}", "fail"


# ─── validators ──────────────────────────────────────────────────────────────


def _tool_result_errors(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        e
        for e in events
        if e.get("type") == "tool_result"
        and (e.get("payload", {}).get("error") or e.get("payload", {}).get("blocked"))
    ]


def _trace_has_clean_completion(events: list[dict[str, Any]]) -> bool:
    if any(e.get("type") == "turn_end" and e.get("payload", {}).get("status") == "ok" for e in events):
        return True
    for e in events:
        if e.get("type") != "board_advance":
            continue
        payload = e.get("payload", {})
        try:
            completed = int(payload.get("completed") or 0)
            total = int(payload.get("total") or 0)
        except (TypeError, ValueError):
            continue
        if total > 0 and completed >= total:
            return True
    return False


def _v_tool_errors_trace(trace: dict[str, Any]) -> tuple[bool, str, str]:
    events = _current_turn_events(trace)
    errors = _tool_result_errors(events)
    if not errors:
        return (True, "No tool errors or blocks", "pass")

    tools = [str(e.get("payload", {}).get("tool") or "?") for e in errors]
    blocked = [e for e in errors if e.get("payload", {}).get("blocked")]
    providers = [
        e
        for e in events
        if e.get("type") == "provider_error" or e.get("payload", {}).get("event") == "provider_error"
    ]
    sandbox_blocks = [e for e in events if e.get("type") == "sandbox_blocked"]
    turn_limits = [e for e in events if e.get("type") == "turn_limit"]
    hard_parts = []
    if sandbox_blocks:
        hard_parts.append(f"{len(sandbox_blocks)} sandbox block(s)")
    if turn_limits:
        hard_parts.append(f"{len(turn_limits)} turn limit(s)")
    if providers:
        hard_parts.append(f"{len(providers)} provider error(s)")
    if not _trace_has_clean_completion(events):
        hard_parts.append("no clean completion evidence")

    detail = f"{len(errors)} tool error/block(s): {tools}"
    if hard_parts:
        return (False, f"{detail}; unresolved context: {', '.join(hard_parts)}", "fail")

    if blocked:
        return (
            True,
            f"{detail}; recovered after clean completion, including {len(blocked)} blocked result(s)",
            "warn",
        )
    return (True, f"{detail}; recovered after clean completion", "warn")


def _v_provider_errors(events: list[dict[str, Any]]) -> tuple[bool, str]:
    errors = [e for e in events if e.get("type") == "provider_error" or e.get("payload", {}).get("event") == "provider_error"]
    if errors:
        return (False, f"{len(errors)} provider error(s)")
    return (True, "No provider errors")


def _v_provider_errors_trace(trace: dict[str, Any]) -> tuple[bool, str, str]:
    passed, message = _v_provider_errors(_current_turn_events(trace))
    return (passed, message, "pass" if passed else "fail")


def _v_provider_lifecycle(trace: dict[str, Any]) -> tuple[bool, str, str]:
    events = _current_turn_events(trace)
    activity = _provider_activity(events)
    if not activity:
        kind = _prompt_kind(str(trace.get("prompt") or ""))
        if kind in {"simple", "local_command", "empty"}:
            return (True, "No provider activity; local/identity path may be intentional", "info")
        return (True, "No provider activity captured", "info")
    requests = [e for e in activity if e.get("type") == "provider_request" or e.get("payload", {}).get("event") == "provider_request"]
    responses = [e for e in activity if e.get("type") == "provider_response" or e.get("payload", {}).get("event") == "provider_response"]
    errors = [e for e in activity if e.get("type") == "provider_error" or e.get("payload", {}).get("event") == "provider_error"]
    switches = [e for e in activity if "switch" in str(e.get("type") or e.get("payload", {}).get("event") or "")]
    if errors and not responses:
        return (False, f"Provider lifecycle has {len(errors)} error(s) and no response", "fail")
    if requests and not responses and not errors:
        return (False, f"{len(requests)} provider request(s) without response/error audit", "fail")
    return (True, f"provider requests={len(requests)}, responses={len(responses)}, errors={len(errors)}, switches={len(switches)}", "pass")


def _v_tool_usage(events: list[dict[str, Any]]) -> tuple[bool, str]:
    calls = [e for e in events if e.get("type") == "tool_call"]
    if not calls:
        return (False, "No tool calls — agent may have just chatted")
    tools_used = sorted({str(e.get("payload", {}).get("tool", "?")) for e in calls})
    return (True, f"{len(calls)} tool call event(s): {tools_used}")


def _v_tool_usage_trace(trace: dict[str, Any]) -> tuple[bool, str, str]:
    events = _trace_events(trace)
    calls = [e for e in events if e.get("type") == "tool_call"]
    if calls:
        tools_used = sorted({str(e.get("payload", {}).get("tool", "?")) for e in calls})
        return (True, f"{len(calls)} tool call event(s): {tools_used}", "pass")
    kind = _prompt_kind(str(trace.get("prompt") or ""))
    if kind in {"simple", "local_command", "empty"}:
        return (True, f"No tool calls; {kind.replace('_', ' ')} turn does not require tools", "info")
    if _work_expected(trace):
        return (False, "No tool calls captured for a work-style prompt", "fail")
    return (True, "No tool calls; chat-style turn", "info")


def _v_tool_audit_trace(trace: dict[str, Any]) -> tuple[bool, str, str]:
    jsonl_entries = _trace_jsonl(trace)
    audit_entries = jsonl_entries.get("tool", [])
    calls = [e for e in _trace_events(trace) if e.get("type") == "tool_call"]
    if audit_entries:
        return (True, f"{len(audit_entries)} tool audit entr{'y' if len(audit_entries) == 1 else 'ies'}", "pass")
    # Monitor traces capture tool calls natively but may lack a separate "tool" audit key
    if trace.get("mode") == "monitor" and calls:
        guard_events = jsonl_entries.get("sandbox_guard", [])
        return (True, f"{len(calls)} tool calls, {len(guard_events)} guard events (monitor trace)", "pass")
    if calls:
        return (False, f"{len(calls)} tool call event(s) but no tool audit entries", "fail")
    return (True, "No tool audit entries; no tools observed", "info")


def _v_file_ops_trace(trace: dict[str, Any]) -> tuple[bool, str, str]:
    entries = _trace_jsonl(trace).get("fileops", [])
    if entries:
        return (True, f"{len(entries)} file operation record(s)", "pass")
    if str(trace.get("mode") or "") == "serve":
        return (True, "No session closeout file-operation record in this serve delta", "info")
    writes = [e for e in _trace_events(trace) if e.get("type") == "tool_result" and str(e.get("payload", {}).get("tool") or "") in {"write_file", "edit_file"}]
    if writes:
        return (True, "Write tools observed; file-operation ledger is written at session closeout", "info")
    return (True, "No file-operation ledger delta; no write closeout expected", "info")


def _v_learning_trace(trace: dict[str, Any]) -> tuple[bool, str, str]:
    learning = trace.get("learning", {}) if isinstance(trace.get("learning", {}), dict) else {}
    status = learning.get("status")
    if status != "ok":
        kind = _prompt_kind(str(trace.get("prompt") or ""))
        if kind == "local_command":
            return (True, f"Learning DB {status or 'missing'}; not expected for local command", "info")
        if trace.get("mode") == "monitor":
            return (True, f"Learning DB {status or 'not captured'}; monitor trace does not snapshot learning", "info")
        return (False, f"Learning DB: {status} {learning.get('error', '')}", "fail")
    total = int(learning.get("total_turns", 0) or 0)
    indexed = int(learning.get("indexed", 0) or 0)
    before = trace.get("learning_before", {}) if isinstance(trace.get("learning_before", {}), dict) else {}
    before_total = int(before.get("total_turns", 0) or 0) if before.get("status") == "ok" else None
    delta = total - before_total if before_total is not None else None
    if total == 0:
        return (False, "Learning DB has 0 turns — nothing indexed", "fail")
    if indexed <= 0:
        return (False, f"Learning DB has {total} turns but 0 FTS entries", "fail")
    suffix = f", delta={delta}" if delta is not None else ""
    return (True, f"{total} turns in learning DB, {indexed} indexed{suffix}", "pass")


def _v_memory_indexed_trace(trace: dict[str, Any]) -> tuple[bool, str, str]:
    events = _trace_events(trace)
    mem = [e for e in events if e.get("type") == "memory_index"]
    if mem:
        return (True, f"{len(mem)} memory index event(s)", "pass")
    kind = _prompt_kind(str(trace.get("prompt") or ""))
    if kind == "local_command":
        return (True, "No memory indexing expected for local slash command", "info")
    if _has_event(events, "provider_response", "auto_reply") or str(trace.get("stdout") or "").strip():
        return (False, "No memory indexing event for an answered turn", "fail")
    return (True, "No memory indexing event; no answered turn detected", "info")


def _v_context(events: list[dict[str, Any]]) -> tuple[bool, str]:
    ctx = [e for e in events if e.get("type") == "turn_context"]
    if not ctx:
        return (False, "No context bridge events")
    return (True, f"{len(ctx)} context bridge event(s)")


def _v_context_trace(trace: dict[str, Any]) -> tuple[bool, str, str]:
    events = _trace_events(trace)
    ctx = [e for e in events if e.get("type") == "turn_context"]
    if ctx:
        return (True, f"{len(ctx)} context bridge event(s)", "pass")
    kind = _prompt_kind(str(trace.get("prompt") or ""))
    if kind in {"local_command", "simple", "empty"} and not _main_provider_activity(events):
        return (True, f"No context bridge event; {kind.replace('_', ' ')} path did not call main provider", "info")
    if _main_provider_activity(events) or _work_expected(trace):
        return (False, "Main provider/work turn without turn_context evidence", "fail")
    if _provider_activity(events):
        return (True, "Provider activity was Ghost/side-channel only; no main turn_context expected", "info")
    return (True, "No context bridge event; no main provider/work path detected", "info")


def _v_ghost(jsonl_entries: dict[str, list[dict[str, Any]]]) -> tuple[bool, str]:
    entries = jsonl_entries.get("ghost", [])
    if not entries:
        return (True, "No ghost events (optional)")
    return (True, f"{len(entries)} ghost event(s)")


def _v_session_clean(events: list[dict[str, Any]]) -> tuple[bool, str]:
    blocks = [e for e in events if e.get("type") == "sandbox_blocked"]
    limits = [e for e in events if e.get("type") == "turn_limit"]
    total = len(blocks) + len(limits)
    if total:
        parts = []
        if blocks:
            parts.append(f"{len(blocks)} sandbox block(s)")
        if limits:
            details = []
            for limit in limits:
                diag = limit.get("diagnostics", {})
                kind = limit.get("kind", "")
                detail = f"limit={kind}"
                if diag:
                    tools = diag.get("top_tools", {})
                    if tools:
                        top = ", ".join(f"{t}({c})" for t, c in list(tools.items())[:3])
                        detail += f" tools=[{top}]"
                    if diag.get("files_modified"):
                        detail += " modified-files"
                    errors = diag.get("provider_errors", 0)
                    if errors:
                        detail += f" provider-errors={errors}"
                    fallbacks = diag.get("provider_fallbacks", 0)
                    if fallbacks:
                        detail += f" fallbacks={fallbacks}"
                    detail += f" at-round={diag.get('tool_rounds', 0)}/{diag.get('provider_requests', 0)}"
                details.append(detail)
            parts.append(f"{len(limits)} turn limit(s): {', '.join(details)}")
        return (False, "; ".join(parts))
    return (True, "Session clean")


def _v_output(stdout: str, trace_mode: str = "") -> tuple[bool, str]:
    if not stdout or len(stdout) < 10:
        if trace_mode == "monitor":
            return (True, "No stdout captured in monitor trace", "info")
        return (False, "Agent produced no meaningful output")
    return (True, f"Output: {len(stdout)} chars")


def _v_tool_compression(events: list[dict[str, Any]]) -> tuple[bool, str, str]:
    comp = [e for e in events if e.get("type") == "tool_compress"]
    if not comp:
        return (True, "No compression events (output below threshold or no tool output)", "info")

    issues: list[str] = []
    formats: list[str] = []
    total_before = 0
    total_after = 0
    total_saved = 0
    structural = 0
    truncate = 0
    unknown = 0
    for e in comp:
        pl = e.get("payload", {})
        if not isinstance(pl, dict):
            issues.append("non-dict payload")
            continue
        fmt = str(pl.get("format") or "?")
        formats.append(fmt)
        if fmt == "truncate":
            truncate += 1
        elif fmt == "?":
            unknown += 1
        else:
            structural += 1
        before = int(pl.get("before_chars") or 0)
        after = int(pl.get("after_chars") or 0)
        saved = int(pl.get("saved_chars") or 0)
        total_before += before
        total_after += after
        total_saved += saved
        if saved <= 0:
            issues.append(f"{fmt}: non-positive savings")
        if before and after and after >= before:
            issues.append(f"{fmt}: after>=before")
        if before and saved and after and saved != max(0, before - after):
            issues.append(f"{fmt}: saved mismatch")
    if issues:
        return (False, f"Invalid compression telemetry ({len(issues)}): {'; '.join(issues[:6])}; formats={formats}", "fail")

    saved_tokens = total_saved // 4
    pct = round((total_saved / max(1, total_before)) * 100, 1) if total_before else 0.0
    detail = (
        f"{len(comp)} context-saving event(s): {total_saved:,} chars (~{saved_tokens:,} tokens) saved, "
        f"{total_before:,}->{total_after:,} chars ({pct}%); structural={structural}, truncate={truncate}"
    )
    if unknown:
        detail += f", unknown={unknown}"
    detail += f"; formats={formats}"
    return (True, detail, "pass")


def _v_auto_reply_trace(trace: dict[str, Any]) -> tuple[bool, str, str]:
    events = _trace_events(trace)
    auto = [e for e in events if e.get("type") == "auto_reply" or str(e.get("payload", {}).get("kind") or "") == "auto_reply"]
    intercepts = [e for e in events if e.get("type") == "turn_intercept"]
    provider = _provider_activity(events)
    main_provider = _main_provider_activity(events)
    kind = _prompt_kind(str(trace.get("prompt") or ""))
    if auto:
        return (True, f"{len(auto)} auto-reply event(s)", "pass")
    if intercepts:
        kinds = sorted({str(e.get("payload", {}).get("kind") or "?") for e in intercepts})
        return (True, f"turn intercept(s) captured: {kinds}", "pass")
    if main_provider:
        return (True, "Main provider path used; auto-reply not expected for substantive turn", "info")
    if provider:
        return (True, "Only Ghost/side-channel provider activity; no main auto-reply needed", "info")
    if kind in {"simple", "local_command"}:
        return (True, "No provider call; local/simple path", "info")
    return (True, "No auto-reply event captured", "info")


def _v_critic_gate_trace(trace: dict[str, Any]) -> tuple[bool, str, str]:
    events = _trace_events(trace)
    blocked = [e for e in events if e.get("type") == "critic_blocked"]
    passed = [e for e in events if e.get("type") == "critic_passed"]
    errors = [e for e in events if e.get("type") == "critic_error"]
    if errors:
        return (False, f"{len(errors)} critic error(s)", "fail")
    if blocked:
        return (True, f"{len(blocked)} answer(s) blocked/redacted by critic", "pass")
    if passed:
        return (True, f"{len(passed)} clean answer(s) passed critic", "pass")
    if str(trace.get("stdout") or "").strip():
        return (True, "No critic telemetry; answer emitted without explicit critic event", "info")
    return (True, "No critic telemetry; no answer emitted", "info")


def _v_coordination_state_trace(trace: dict[str, Any]) -> tuple[bool, str, str]:
    events = _trace_events(trace)
    coord = [e for e in events if e.get("type") in {"coordination_state", "worker_event", "goal_step", "goal_run"}]
    ctx_coord = [e for e in events if e.get("type") == "turn_context" and ("coordination" in str(e.get("payload", {})).lower() or "worker" in str(e.get("payload", {})).lower())]
    ghost = _trace_jsonl(trace).get("ghost", [])
    if coord or ctx_coord or ghost:
        return (True, f"coordination/worker evidence: events={len(coord)}, context={len(ctx_coord)}, ghost={len(ghost)}", "pass")
    return (True, "No coordination/worker evidence; not a worker/goal turn", "info")


def _v_model_limits_trace(trace: dict[str, Any]) -> tuple[bool, str, str]:
    agent_meta = trace.get("agent", {}) if isinstance(trace.get("agent", {}), dict) else {}
    budget = agent_meta.get("context_budget_tokens") or agent_meta.get("tool_result_max_chars")
    events = _trace_events(trace)
    ctx = [e for e in events if e.get("type") == "turn_context"]
    if budget:
        return (True, f"agent limits captured (budget/tool cap={budget})", "pass")
    if any("budget" in str(e.get("payload", {})).lower() or "context" in str(e.get("payload", {})).lower() for e in ctx):
        return (True, "context/model limit hints found in turn_context", "pass")
    return (True, "No explicit model-limit telemetry in this trace", "info")


def _v_work_pattern_gate_trace(trace: dict[str, Any]) -> tuple[bool, str, str]:
    events = _trace_events(trace)
    ctx = [e for e in events if e.get("type") == "turn_context"]
    for c in ctx:
        flags = c.get("payload", {}).get("flags", {}) if isinstance(c.get("payload", {}), dict) else {}
        if flags.get("work_pattern"):
            return (True, "Work pattern active in context", "pass")
    if _prompt_kind(str(trace.get("prompt") or "")) == "work":
        return (True, "Work-style prompt but no work_pattern flag captured", "info")
    return (True, "No work-pattern flag needed for this prompt", "info")


def _v_mo_control_gate_trace(trace: dict[str, Any]) -> tuple[bool, str, str]:
    events = _trace_events(trace)
    for c in [e for e in events if e.get("type") == "turn_context"]:
        flags = c.get("payload", {}).get("flags", {}) if isinstance(c.get("payload", {}), dict) else {}
        if flags.get("mo_control"):
            return (True, "MO control workspace active in context", "pass")
    env = trace.get("environment", {}) if isinstance(trace.get("environment", {}), dict) else {}
    if env.get("MO_CONTROL_WORKSPACE"):
        return (True, "MO control workspace env captured", "pass")
    return (True, "No MO control workspace in this turn", "info")


def _v_timing_order_trace(trace: dict[str, Any]) -> tuple[bool, str, str]:
    events = sorted(_current_turn_events(trace), key=lambda e: float(e.get("ts") or 0.0))
    first: dict[str, float] = {}
    last: dict[str, float] = {}
    for e in events:
        typ = str(e.get("type") or "")
        ts = float(e.get("ts") or 0.0)
        first.setdefault(typ, ts)
        last[typ] = ts
    issues: list[str] = []
    main_provider_requests = [
        e for e in events
        if str(e.get("type") or "") == "provider_request"
        and not _event_surface(e).startswith("ghost")
    ]
    first_main_provider_request = (
        float(main_provider_requests[0].get("ts") or 0.0)
        if main_provider_requests
        else None
    )
    if first_main_provider_request is not None and "turn_context" in first and first_main_provider_request < first["turn_context"]:
        issues.append("provider_request before turn_context")
    if "tool_result" in first and "tool_call" in first and first["tool_result"] < first["tool_call"]:
        issues.append("tool_result before tool_call")
    if "memory_index" in first and "provider_request" in first and first["memory_index"] < first["provider_request"]:
        issues.append("memory_index before provider_request")
    if issues:
        return (False, "; ".join(issues), "fail")
    if events:
        return (True, f"Basic event order sane across {len(events)} event(s)", "pass")
    return (True, "No timestamped events to order", "info")


def _v_evidence_sources_trace(trace: dict[str, Any]) -> tuple[bool, str, str]:
    jd = _trace_jsonl(trace)
    counts = {name: len(entries) for name, entries in jd.items() if entries}
    events = _trace_events(trace)
    if counts:
        return (True, f"JSONL deltas: {counts}", "pass")
    if events:
        return (True, "In-memory monitor events captured; no JSONL deltas", "info")
    return (False, "No monitor events or JSONL evidence captured", "fail")


def _session_compact_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return primary compaction events, falling back to session_event mirrors only for old traces."""
    primary = [e for e in events if e.get("type") == "session_compact"]
    if primary:
        return primary
    fallback: list[dict[str, Any]] = []
    for e in events:
        pl = e.get("payload", {})
        if isinstance(pl, dict) and str(pl.get("kind") or "") == "session_compact":
            fallback.append(e)
    return fallback


def _primary_context_handoff_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return real handoff events, not provider-audit mirror rows."""
    primary: list[dict[str, Any]] = []
    for e in events:
        if e.get("type") != "context_handoff":
            continue
        payload = e.get("payload", {}) if isinstance(e.get("payload", {}), dict) else {}
        if e.get("source") == "provider" and payload.get("event") == "context_handoff":
            continue
        primary.append(e)
    return primary


def _v_session_momentum_trace(trace: dict[str, Any]) -> tuple[bool, str, str]:
    events = _trace_events(trace)
    comp = _session_compact_events(events)
    if comp:
        issues: list[str] = []
        saved = 0
        force_count = 0
        stages: list[str] = []
        for e in comp:
            pl = e.get("payload", {})
            if not isinstance(pl, dict):
                issues.append("non-dict payload")
                continue
            stage = str(pl.get("stage") or "?")
            stages.append(stage)
            event_saved = int(pl.get("saved_chars") or 0)
            before_messages = int(pl.get("before_messages") or 0)
            after_messages = int(pl.get("after_messages") or 0)
            before_chars = int(pl.get("before_chars") or 0)
            after_chars = int(pl.get("after_chars") or 0)
            pressure = float(pl.get("pressure") or 0.0)
            message_ratio = float(pl.get("message_ratio") or 0.0)
            force = bool(pl.get("force"))
            if force:
                force_count += 1
            saved += event_saved
            if not stage or stage == "?":
                issues.append("missing stage")
            if event_saved <= 0:
                issues.append(f"{stage}: non-positive savings")
            if before_messages and after_messages and after_messages >= before_messages:
                issues.append(f"{stage}: after_messages>=before_messages")
            if before_chars and after_chars and after_chars >= before_chars:
                issues.append(f"{stage}: after_chars>=before_chars")
            if not force and pressure < 0.25 and message_ratio < 0.25:
                issues.append(f"{stage}: compaction below minimum pressure without force")
            tb = pl.get("truth_boundary", {}) if isinstance(pl.get("truth_boundary"), dict) else {}
            evidence = tb.get("evidence_preserved") if isinstance(tb, dict) else None
            if isinstance(evidence, list) and not evidence:
                issues.append(f"{stage}: no preserved evidence anchors")
        if issues:
            return (False, f"Session momentum violations ({len(issues)}): {'; '.join(issues[:5])}", "fail")
        handoffs = _primary_context_handoff_events(events)
        handoff_text = f"; handoff also observed={len(handoffs)}" if handoffs else ""
        return (True, f"{len(comp)} session momentum compaction event(s), {saved:,} chars saved, stages={stages}, forced={force_count}{handoff_text}", "pass")
    handoffs = _primary_context_handoff_events(events)
    if handoffs:
        return (True, "Context handoff observed; no session compaction event in this trace (nothing eligible or pre-implementation trace)", "info")
    return (True, "No session momentum compaction needed in this trace", "info")


def _v_runtime_artifacts_trace(trace: dict[str, Any]) -> tuple[bool, str, str]:
    artifacts = trace.get("artifacts", {}) if isinstance(trace.get("artifacts", {}), dict) else {}
    if not artifacts:
        if trace.get("mode") == "monitor":
            return (True, "Monitor trace does not snapshot runtime artifacts", "info")
        return (False, "No runtime artifact snapshot", "fail")
    ok = [name for name, data in artifacts.items() if isinstance(data, dict) and data.get("status") in {"ok", "no_graph_json", "no_heartbeat_file"}]
    if ok:
        return (True, f"Artifact snapshots captured: {sorted(ok)}", "pass")
    return (True, "Artifact snapshots captured but no active artifacts found", "info")


def _v_zero_tool_turn(events: list[dict[str, Any]], jsonl_delta: dict[str, list[dict[str, Any]]]) -> tuple[bool, str]:
    tool_entries = jsonl_delta.get("tool", [])
    tool_calls = [e for e in events if e.get("type") == "tool_call"]
    if not tool_entries and not tool_calls:
        return (True, "Zero tool calls — clean simple-chat turn")
    return (True, f"{len(tool_entries)} tool audit entries, {len(tool_calls)} tool call events (expected for work turn)")


def _v_secret_redaction_trace(trace: dict[str, Any]) -> tuple[bool, str, str]:
    safe_blob = json.dumps(
        {
            "stdout": trace.get("stdout", ""),
            "events": _trace_events(trace),
            "jsonl_delta": _trace_jsonl(trace),
        },
        default=str,
        ensure_ascii=False,
    )
    leaks = [pat.pattern for pat in _SECRET_LEAK_PATTERNS if pat.search(safe_blob)]
    if leaks:
        return (False, f"Potential unredacted secret pattern(s): {len(leaks)}", "fail")
    redacted = [e for e in _trace_events(trace) if e.get("type") == "secret_redacted" or "redacted" in str(e.get("payload", {})).lower()]
    if redacted:
        return (True, f"{len(redacted)} secret redaction marker(s); no raw secret patterns", "pass")
    return (True, "No raw secret patterns found in captured trace", "pass")


def _v_learning_suggestions(jsonl_entries: dict[str, list[dict[str, Any]]]) -> tuple[bool, str]:
    entries = jsonl_entries.get("learning_suggestions", [])
    if entries:
        return (True, f"{len(entries)} learning suggestion(s) recorded")
    return (True, "No learning suggestions (not applicable or not triggered)")


def _v_anti_hallucination_contract(trace: dict[str, Any]) -> tuple[bool, str, str]:
    """Verify every compact/handoff event satisfies the anti-hallucination contract.

    Checks: deterministic (no provider calls), labeled (orientation only),
    evidence-preserving (tool/file references survive), loss-accounted
    (all omissions are named).
    """
    events = _trace_events(trace)
    compact_events = _session_compact_events(events)
    handoffs = _primary_context_handoff_events(events)

    if not compact_events and not handoffs:
        return (True, "No compact/handoff events to validate against contract", "info")

    issues: list[str] = []
    unlabeled_handoffs = 0
    for e in handoffs:
        pl = e.get("payload", {})
        if isinstance(pl, dict):
            text = str(pl.get("text", "") or pl.get("summary", "") or pl.get("reason", "") or "")
        else:
            text = str(pl or "")
        if "orientation" not in text.lower() and "not proof" not in text.lower():
            unlabeled_handoffs += 1
    if unlabeled_handoffs:
        issues.append(f"{unlabeled_handoffs}/{len(handoffs)} handoff missing orientation label")

    for e in compact_events:
        pl = e.get("payload", {})
        if not isinstance(pl, dict):
            continue
        # Deterministic: provider must not be involved
        if pl.get("involved_provider"):
            issues.append("provider involved in compaction")
        # Labeled: must say orientation — check both direct label and truth_boundary
        label = str(pl.get("label", "") or "")
        tb = pl.get("truth_boundary", {}) if isinstance(pl.get("truth_boundary"), dict) else {}
        if "orientation" not in label.lower() and "not proof" not in label.lower():
            if not tb.get("labeled"):
                issues.append("compacted content not labeled orientation-only")
        evidence = tb.get("evidence_preserved") if isinstance(tb, dict) else None
        if isinstance(evidence, list) and not evidence:
            issues.append("no preserved evidence anchors")
        elif isinstance(evidence, str) and not evidence.strip():
            issues.append("no preserved evidence anchors")
        # Loss-accounted: must name what was lost
        saved = int(pl.get("saved_chars", 0) or 0)
        before = int(pl.get("before_messages", 0) or 0)
        after = int(pl.get("after_messages", 0) or 0)
        if saved and before and after:
            # loss is named via saved_chars / message delta
            pass
        else:
            issues.append("loss not accounted via saved_chars/message delta")

    if issues:
        return (False, f"Anti-hallucination contract violations ({len(issues)}): {'; '.join(issues[:4])}", "fail")
    saved_total = sum(int(e.get("payload", {}).get("saved_chars", 0) or 0) for e in compact_events if isinstance(e.get("payload", {}), dict))
    if compact_events and handoffs:
        return (True, f"{len(compact_events)} compaction and {len(handoffs)} handoff event(s) satisfy contract; {saved_total:,} chars saved deterministically", "pass")
    if compact_events:
        return (True, f"{len(compact_events)} compaction event(s) satisfy contract; {saved_total:,} chars saved deterministically", "pass")
    return (True, f"{len(handoffs)} handoff event(s) — all labeled orientation", "pass")


def _v_closeout_artifacts_trace(trace: dict[str, Any]) -> tuple[bool, str, str]:
    artifacts = trace.get("artifacts", {}) if isinstance(trace.get("artifacts", {}), dict) else {}
    before = trace.get("artifacts_before", {}) if isinstance(trace.get("artifacts_before", {}), dict) else {}
    if trace.get("mode") == "monitor" and not artifacts:
        return (True, "Monitor trace has no closeout snapshot; closeout ledger is captured when the serve session ends", "info")
    close = artifacts.get("closeouts", {}) if isinstance(artifacts.get("closeouts", {}), dict) else {}
    prev = before.get("closeouts", {}) if isinstance(before.get("closeouts", {}), dict) else {}
    if close.get("status") == "not_found":
        return (True, "No closeout directory", "info")
    if close.get("status") != "ok":
        return (False, f"Closeout snapshot error: {close.get('error', close.get('status', 'unknown'))}", "fail")
    total = int(close.get("total_files", 0) or 0)
    prev_total = int(prev.get("total_files", 0) or 0) if prev.get("status") == "ok" else None
    delta = total - prev_total if prev_total is not None else None
    unresolved = int(close.get("unresolved_count", 0) or 0)
    delta_msg = f", delta={delta}" if delta is not None else ""
    if unresolved:
        return (True, f"{total} closeout file(s){delta_msg}; {unresolved} with unresolved markers", "info")
    return (True, f"{total} closeout file(s){delta_msg}; all clean", "pass")


VALIDATORS: list[tuple[str, Any]] = [
    ("Evidence sources", _v_evidence_sources_trace),
    ("Provider lifecycle", _v_provider_lifecycle),
    ("Tool errors", _v_tool_errors_trace),
    ("Provider errors", _v_provider_errors_trace),
    ("Tool usage", _v_tool_usage_trace),
    ("Tool audit", _v_tool_audit_trace),
    ("File operations", _v_file_ops_trace),
    ("Learning entries", _v_learning_trace),
    ("Memory indexed", _v_memory_indexed_trace),
    ("Context activity", _v_context_trace),
    ("Event order", _v_timing_order_trace),
    ("Session clean", _v_session_clean),
    ("Ghost events", _v_ghost),
    ("Output produced", _v_output),
    ("Auto-reply path", _v_auto_reply_trace),
    ("Critic answer gate", _v_critic_gate_trace),
    ("Coordination state", _v_coordination_state_trace),
    ("Model limits", _v_model_limits_trace),
    ("Tool compression", _v_tool_compression),
    ("Work pattern gate", _v_work_pattern_gate_trace),
    ("MO control gate", _v_mo_control_gate_trace),
    ("Secret redaction", _v_secret_redaction_trace),
    ("Learning suggestions", _v_learning_suggestions),
    ("Zero tool turn", _v_zero_tool_turn),
    ("Session momentum", _v_session_momentum_trace),
    ("Anti-hallucination contract", _v_anti_hallucination_contract),
    ("Runtime artifacts", _v_runtime_artifacts_trace),
    ("Closeout artifacts", _v_closeout_artifacts_trace),
]


# ─── trace metadata ─────────────────────────────────────────────────────────


def _git_probe(args: list[str]) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=str(_AGENT_ROOT), text=True, stderr=subprocess.DEVNULL, timeout=3).strip()
    except Exception:
        return ""


def _snapshot_environment_meta(config_path: str | Path | None = None, config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or _trace_config()
    runtime = cfg.get("runtime", {}) if isinstance(cfg.get("runtime", {}), dict) else {}
    return {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "agent_root": str(_AGENT_ROOT),
        "cwd": os.getcwd(),
        "project_cwd": os.environ.get("MO_PROJECT_CWD", str(_AGENT_ROOT)),
        "config_path": str(config_path or ""),
        "config_exists": bool(config_path and Path(config_path).exists()),
        "runtime_home": runtime.get("home", ""),
        "runtime_state": runtime.get("state", ""),
        "MO_HOME": os.environ.get("MO_HOME", ""),
        "MO_STATE_HOME": os.environ.get("MO_STATE_HOME", ""),
        "MO_CONFIG": os.environ.get("MO_CONFIG", ""),
        "MO_PROJECT_CWD": os.environ.get("MO_PROJECT_CWD", ""),
        "MO_BACKEND_MONITOR_DIR": os.environ.get("MO_BACKEND_MONITOR_DIR", ""),
        "git_commit": _git_probe(["rev-parse", "--short", "HEAD"]),
        "git_branch": _git_probe(["rev-parse", "--abbrev-ref", "HEAD"]),
        "git_dirty": bool(_git_probe(["status", "--porcelain"])),
    }


def _snapshot_agent_meta(agent: Any) -> dict[str, Any]:
    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    agent_cfg = cfg.get("agent", {}) if isinstance(cfg.get("agent", {}), dict) else {}
    model_cfg = cfg.get("model", {}) if isinstance(cfg.get("model", {}), dict) else {}
    return {
        "provider_name": str(getattr(agent, "provider_name", "") or ""),
        "model": str(getattr(agent, "model", "") or ""),
        "model_default": str(model_cfg.get("default", "") or ""),
        "model_fallback": str(model_cfg.get("fallback", "") or ""),
        "context_budget_tokens": getattr(agent, "context_budget_tokens", agent_cfg.get("context_budget_tokens")),
        "context_reserve_tokens": agent_cfg.get("context_reserve_tokens"),
        "max_tool_rounds": agent_cfg.get("max_tool_rounds"),
        "max_provider_requests": agent_cfg.get("max_provider_requests"),
        "tool_result_max_chars": agent_cfg.get("tool_result_max_chars"),
        "tool_compress_enabled": agent_cfg.get("tool_compress_enabled"),
    }


# ─── core: cmd_run ──────────────────────────────────────────────────────────


def cmd_run(prompt: str) -> dict[str, Any]:
    """Run one agent turn, capture everything, validate, print report."""
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    session_id = datetime.now().strftime("trace_%Y%m%d_%H%M%S")
    trace_path = TRACE_DIR / f"{session_id}.trace"

    print(f"MO Trace — session {session_id}")
    print(f"Prompt: {prompt[:120]}{'...' if len(prompt) > 120 else ''}")
    print()

    from core.agent.agent import create_agent
    from core.gateway import Gateway
    from core.path_defaults import default_config_path

    config_path = default_config_path(
        agent_root=str(_AGENT_ROOT),
        caller_cwd=os.environ.get("MO_PROJECT_CWD", str(_AGENT_ROOT)),
    )
    if not os.path.exists(config_path):
        print("[ERROR] No config found. Run `python mo.py --init` first.")
        return {"error": "no_config"}
    config = _trace_config()

    with _trace_environment(session_id):
        sizes_before = _file_sizes(config)
        learning_before = _snapshot_learning(config=config)
        artifacts_before = _snapshot_runtime_artifacts(config)

        agent = create_agent(config_path)
        agent_config = getattr(agent, "config", config) if isinstance(getattr(agent, "config", config), dict) else config
        gateway = Gateway(agent)
        monitor = _TraceMonitor()

        if hasattr(gateway, "monitor") and hasattr(gateway.monitor, "emit"):
            original_emit = gateway.monitor.emit

            def _patched_emit(event_type: str, payload: Any) -> None:
                safe_payload = payload if isinstance(payload, dict) else {"message": str(payload)}
                monitor.record(event_type, **safe_payload)
                original_emit(event_type, payload)

            gateway.monitor.emit = _patched_emit

        stdout_chunks: list[str] = []
        start = time.time()
        error: str | None = None

        def _capture_text(text: Any) -> None:
            value = str(text or "")
            if value:
                stdout_chunks.append(value)
                if not value.endswith("\n"):
                    stdout_chunks.append("\n")

        try:
            result = gateway.run_turn(
                prompt,
                route_source="trace_run",
                on_assistant_text=_capture_text,
                on_proposal=_capture_text,
            )
            if result and result not in "".join(stdout_chunks):
                stdout_chunks.append(str(result))
        except Exception as exc:
            error = str(exc)
            stdout_chunks.append(f"[TRACE ERROR] {error}")

        elapsed = time.time() - start
        jsonl_delta = _collect_jsonl_delta(sizes_before, agent_config)
        events = monitor.events + _events_from_jsonl_delta(jsonl_delta)
        learning_after = _snapshot_learning(config=agent_config)
        artifacts = _snapshot_runtime_artifacts(agent_config)
        environment = _snapshot_environment_meta(config_path, agent_config)
        agent_meta = _snapshot_agent_meta(agent)

    trace: dict[str, Any] = {
        "session_id": session_id,
        "mode": "run",
        "prompt": prompt,
        "started_at": start,
        "elapsed": round(elapsed, 2),
        "error": error,
        "stdout": "".join(stdout_chunks),
        "events": events,
        "jsonl_delta": jsonl_delta,
        "learning_before": learning_before,
        "learning": learning_after,
        "artifacts_before": artifacts_before,
        "artifacts": artifacts,
        "environment": environment,
        "agent": agent_meta,
    }

    trace_path.write_text(json.dumps(trace, indent=2, default=str), encoding="utf-8")
    print(f"Trace saved: {trace_path} ({trace_path.stat().st_size:,} bytes, {len(events)} events, {sum(len(v) for v in jsonl_delta.values())} JSONL entries)")
    print()

    report = _validate_report(trace)
    trace["validation"] = report
    trace_path.write_text(json.dumps(trace, indent=2, default=str), encoding="utf-8")
    _print_report(report)
    _write_trace_learning_suggestions(trace_path)
    return trace


# ─── replay / list ──────────────────────────────────────────────────────────


def _build_trace_from_dir(trace_dir: Path) -> dict[str, Any] | None:
    """Build a synthetic trace dict from a directory-based monitor trace."""
    monitor_dir = trace_dir / "monitor"
    if not monitor_dir.exists():
        return None
    jsonl_files = sorted(monitor_dir.glob("backend_monitor-*.jsonl"))
    if not jsonl_files:
        return None
    events: list[dict[str, Any]] = []
    jsonl_delta: dict[str, list[dict[str, Any]]] = {}
    first_ts: float | None = None
    last_ts: float | None = None
    for jf in jsonl_files:
        try:
            for line in jf.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                events.append(entry)
                etype = entry.get("type", "unknown")
                jsonl_delta.setdefault(etype, []).append(entry)
                ts = entry.get("timestamp") or entry.get("ts")
                if ts is not None:
                    try:
                        ts_f = float(ts)
                        if first_ts is None or ts_f < first_ts:
                            first_ts = ts_f
                        if last_ts is None or ts_f > last_ts:
                            last_ts = ts_f
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass
    elapsed = round(last_ts - first_ts, 2) if (first_ts is not None and last_ts is not None) else 0
    return {
        "session_id": trace_dir.name,
        "mode": "monitor",
        "prompt": "(monitor trace — prompt not captured)",
        "elapsed": elapsed,
        "events": events,
        "jsonl_delta": jsonl_delta,
        "stdout": "",
    }


def cmd_replay(path_str: str) -> None:
    trace_path = Path(path_str)
    if not trace_path.exists():
        # Try TRACE_DIR-relative path (matches cmd_list output)
        trace_path = TRACE_DIR / path_str
        if not trace_path.exists():
            print(f"[ERROR] Trace not found: {path_str}")
            return
    # Support both .trace files and directory-based traces
    if trace_path.is_dir():
        trace = _build_trace_from_dir(trace_path)
        if trace is None:
            print(f"[ERROR] No monitor data found in: {trace_path}")
            return
    else:
        try:
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[ERROR] Cannot parse trace: {exc}")
            return
    print(f"Replaying: {trace.get('session_id', '?')}")
    print(f"Prompt: {str(trace.get('prompt', '?'))[:120]}")
    print(f"Elapsed: {trace.get('elapsed', '?')}s")
    events = trace.get("events", [])
    print(f"Events: {len(events)}")
    jd = trace.get("jsonl_delta", {})
    print(f"JSONL entries: {sum(len(v) for v in jd.values())}")
    print()
    report = _validate_report(trace)
    _print_report(report)


def _read_dir_trace_meta(trace_dir: Path) -> dict[str, Any] | None:
    """Extract metadata from a directory-based trace (monitor JSONL fallback)."""
    monitor_dir = trace_dir / "monitor"
    if not monitor_dir.exists():
        return None
    jsonl_files = sorted(monitor_dir.glob("backend_monitor-*.jsonl"))
    if not jsonl_files:
        return None
    total_size = 0
    total_events = 0
    first_ts: str | None = None
    last_ts: str | None = None
    for jf in jsonl_files:
        total_size += jf.stat().st_size
        try:
            for line in jf.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                total_events += 1
                if first_ts is None:
                    try:
                        entry = json.loads(line)
                        first_ts = entry.get("timestamp", entry.get("ts", ""))
                    except json.JSONDecodeError:
                        pass
                try:
                    entry = json.loads(line)
                    last_ts = entry.get("timestamp", entry.get("ts", ""))
                except json.JSONDecodeError:
                    pass
        except Exception:
            pass
    return {
        "session_id": trace_dir.name,
        "mode": "monitor",
        "events": total_events,
        "size": total_size,
        "first_ts": first_ts,
        "last_ts": last_ts,
    }


def _list_trace_items() -> list[dict[str, Any]]:
    """Return combined list of .trace files and directory-based traces."""
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    if not TRACE_DIR.exists():
        return items
    # .trace files first (preferred — richer metadata)
    for trace_path in sorted(TRACE_DIR.glob("*.trace"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(trace_path.read_text(encoding="utf-8", errors="replace"))
            sid = data.get("session_id", trace_path.stem)
            mode = data.get("mode", "?")
            prompt = str(data.get("prompt", ""))[:70]
            events = len(data.get("events", []))
            items.append({
                "session_id": sid, "mode": mode, "prompt": prompt,
                "events": events, "size": trace_path.stat().st_size,
                "mtime": trace_path.stat().st_mtime, "path": str(trace_path),
                "kind": "file",
            })
            seen_ids.add(sid)
            seen_ids.add(trace_path.stem)
        except Exception:
            items.append({
                "session_id": trace_path.stem, "mode": "?", "prompt": "(corrupt)",
                "events": 0, "size": trace_path.stat().st_size,
                "mtime": trace_path.stat().st_mtime, "path": str(trace_path),
                "kind": "file",
            })
            seen_ids.add(trace_path.stem)
    # Directory-based traces (monitor fallback — skip if .trace already covers this session)
    for trace_dir in sorted(TRACE_DIR.glob("trace_*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if not trace_dir.is_dir():
            continue
        if trace_dir.name in seen_ids:
            continue
        meta = _read_dir_trace_meta(trace_dir)
        if meta is None:
            continue
        items.append({
            "session_id": meta["session_id"], "mode": meta["mode"], "prompt": "(monitor only)",
            "events": meta["events"], "size": meta["size"],
            "mtime": trace_dir.stat().st_mtime, "path": str(trace_dir),
            "kind": "dir",
        })
    items.sort(key=lambda it: it["mtime"], reverse=True)
    return items


def cmd_list() -> None:
    items = _list_trace_items()
    if not items:
        print("No traces found.")
        return
    print(f"{'Session':<24} {'Mode':<7} {'Events':<8} {'Bytes':<10} {'Kind':<6} Prompt")
    print("-" * 102)
    for item in items[:20]:
        print(f"{item['session_id']:<24} {item['mode']:<7} {item['events']:<8} {item['size']:<10,} {item['kind']:<6} {item['prompt']}")


# ─── cmd_serve ──────────────────────────────────────────────────────────────


def _ledger_row(trace: dict[str, Any]) -> dict[str, str]:
    """Extract one evidence-ledger row from a trace dict."""
    sid = str(trace.get("session_id", "?"))
    mode = str(trace.get("mode", "?"))
    prompt = str(trace.get("prompt", ""))[:60]
    elapsed = str(trace.get("elapsed", "?"))
    events = len(trace.get("events", []))
    val = trace.get("validation", [])
    passed = sum(1 for v in val if v.get("passed")) if val else "?"
    total = len(val) if val else "?"
    jd = trace.get("jsonl_delta", {})
    jl_count = sum(len(v) for v in jd.values())
    return {
        "session": sid,
        "mode": mode,
        "prompt": prompt,
        "elapsed": elapsed,
        "events": str(events),
        "jsonl_entries": str(jl_count),
        "validators_passed": f"{passed}/{total}" if total != "?" else "?",
    }


def cmd_ledger(trace_path: str | None = None) -> None:
    """Print the MISSION_E2E evidence ledger as markdown table."""
    rows: list[dict[str, str]] = []
    if trace_path:
        p = Path(trace_path)
        if not p.exists():
            # Try TRACE_DIR-relative path (matches cmd_list output)
            p = TRACE_DIR / trace_path
            if not p.exists():
                print(f"[ERROR] Not found: {trace_path}")
                return
        if p.is_dir():
            # Try directory-based trace first, then .trace files within
            trace = _build_trace_from_dir(p)
            if trace:
                row = _ledger_row(trace)
                row["session"] = trace["session_id"]
                rows.append(row)
            else:
                for tf in sorted(p.glob("*.trace"), key=lambda x: x.stat().st_mtime, reverse=True):
                    try:
                        data = json.loads(tf.read_text(encoding="utf-8", errors="replace"))
                        rows.append(_ledger_row(data))
                    except Exception:
                        rows.append({"session": tf.stem, "mode": "error", "prompt": "(corrupt)", "elapsed": "?", "events": "?", "jsonl_entries": "?", "validators_passed": "?"})
        else:
            try:
                data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                rows.append(_ledger_row(data))
            except Exception:
                rows.append({"session": p.stem, "mode": "error", "prompt": "(corrupt)", "elapsed": "?", "events": "?", "jsonl_entries": "?", "validators_passed": "?"})
    else:
        # Use combined listing from _list_trace_items
        items = _list_trace_items()
        for item in items:
            trace_file = Path(item["path"])
            if item["kind"] == "dir":
                trace = _build_trace_from_dir(trace_file)
                if trace:
                    row = _ledger_row(trace)
                    row["session"] = trace["session_id"]
                    rows.append(row)
            else:
                try:
                    data = json.loads(trace_file.read_text(encoding="utf-8", errors="replace"))
                    rows.append(_ledger_row(data))
                except Exception:
                    rows.append({"session": trace_file.stem, "mode": "error", "prompt": "(corrupt)", "elapsed": "?", "events": "?", "jsonl_entries": "?", "validators_passed": "?"})

    if not rows:
        print("No traces found.")
        return

    print("# E2E Evidence Ledger")
    print()
    print(f"Traces: {len(rows)}")
    print()
    header = "| Session | Mode | Prompt | Elapsed | Events | JSONL entries | Validators |"
    sep = "|" + "-" * 25 + "|" + "-" * 7 + "|" + "-" * 62 + "|" + "-" * 9 + "|" + "-" * 8 + "|" + "-" * 15 + "|" + "-" * 14 + "|"
    print(header)
    print(sep)
    for row in rows:
        print(f"| {row['session']:<23} | {row['mode']:<5} | {row['prompt']:<60} | {row['elapsed']:<7} | {row['events']:<6} | {row['jsonl_entries']:<13} | {row['validators_passed']:<12} |")
    print()
    print(f"Total traces: {len(rows)}")


def cmd_serve(mo_args: list[str] | None = None) -> dict[str, Any]:
    """Launch mo.py interactively, trace the entire session, validate at exit."""
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    session_id = datetime.now().strftime("trace_%Y%m%d_%H%M%S")
    trace_path = TRACE_DIR / f"{session_id}.trace"

    mo_py = _AGENT_ROOT / "mo.py"
    if not mo_py.exists():
        print(f"[ERROR] mo.py not found at {mo_py}")
        return {"error": "mo.py_not_found"}

    forwarded = list(mo_args or [])
    if forwarded and forwarded[0].lower() == "--trace":
        forwarded = forwarded[1:]
    args = [sys.executable, str(mo_py)] + forwarded
    print(f"MO Trace — session {session_id}")
    print(f"Launching: {' '.join(args)}")
    print("(trace will collect signals and validate when the session ends)")
    print()

    config = _trace_config()
    with _trace_environment(session_id):
        sizes_before = _file_sizes(config)
        learning_before = _snapshot_learning(config=config)
        artifacts_before = _snapshot_runtime_artifacts(config)
        environment = _snapshot_environment_meta(config=config)
        start = time.time()
        try:
            proc = subprocess.run(args, timeout=86400, env=os.environ.copy())  # 24h ceiling
            return_code = proc.returncode
            error: str | None = None
        except (Exception, KeyboardInterrupt) as exc:
            return_code = -1
            error = str(exc)
        elapsed = time.time() - start
        jsonl_delta = _collect_jsonl_delta(sizes_before, config)
        events = _events_from_jsonl_delta(jsonl_delta)
        learning_after = _snapshot_learning(config=config)
        artifacts = _snapshot_runtime_artifacts(config)

    trace: dict[str, Any] = {
        "session_id": session_id,
        "mode": "serve",
        "prompt": "(interactive session via serve)",
        "started_at": start,
        "elapsed": round(elapsed, 2),
        "return_code": return_code,
        "error": error,
        "stdout": "",  # Interactive stdout/stderr are intentionally not captured.
        "events": events,
        "jsonl_delta": jsonl_delta,
        "learning_before": learning_before,
        "learning": learning_after,
        "artifacts_before": artifacts_before,
        "artifacts": artifacts,
        "environment": environment,
        "agent": {},
    }

    trace_path.write_text(json.dumps(trace, indent=2, default=str), encoding="utf-8")
    print(f"\nSession ended (rc={return_code}, {round(elapsed)}s)")
    print(f"Trace saved: {trace_path} ({trace_path.stat().st_size:,} bytes, {len(events)} events, {sum(len(v) for v in jsonl_delta.values())} JSONL entries)")
    print()

    report = _validate_report(trace)
    trace["validation"] = report
    trace_path.write_text(json.dumps(trace, indent=2, default=str), encoding="utf-8")
    _print_report(report)
    _write_trace_learning_suggestions(trace_path)
    return trace


def _write_trace_learning_suggestions(trace_path: Path) -> int:
    """Analyze a fresh trace into inert learning suggestions, best-effort."""
    try:
        from core.learning.proactive_learning import write_learning_suggestions
        from core.learning.trace_learning import analyze_trace_file

        suggestions = analyze_trace_file(trace_path)
        if not suggestions:
            return 0
        out = write_learning_suggestions(suggestions)
        print(f"Trace learning: {len(suggestions)} suggestion(s) written to {out}")
        return len(suggestions)
    except Exception:
        return 0


# ─── validation engine ──────────────────────────────────────────────────────


def _validate_report(trace: dict[str, Any]) -> list[dict[str, Any]]:
    events = _trace_events(trace)
    jsonl_delta = _trace_jsonl(trace)
    stdout = trace.get("stdout", "")
    mode = str(trace.get("mode") or "run")

    trace_level = {
        "Evidence sources",
        "Provider lifecycle",
        "Provider errors",
        "Tool errors",
        "Tool usage",
        "Tool audit",
        "File operations",
        "Learning entries",
        "Memory indexed",
        "Context activity",
        "Event order",
        "Auto-reply path",
        "Critic answer gate",
        "Coordination state",
        "Model limits",
        "Work pattern gate",
        "MO control gate",
        "Secret redaction",
        "Session momentum",
        "Anti-hallucination contract",
        "Runtime artifacts",
        "Closeout artifacts",
    }

    results: list[dict[str, Any]] = []
    for name, fn in VALIDATORS:
        try:
            if name in trace_level:
                passed, msg, status = _tuple3(fn(trace))
            elif name == "Output produced":
                if mode == "serve":
                    passed, msg, status = True, "Interactive output is not captured in serve mode", "info"
                else:
                    passed, msg, status = _tuple3(fn(stdout, mode))
            elif name == "Ghost events":
                passed, msg, status = _tuple3(fn(jsonl_delta))
                if not jsonl_delta.get("ghost"):
                    status = "info"
            elif name == "Learning suggestions":
                passed, msg, status = _tuple3(fn(jsonl_delta))
                if not jsonl_delta.get("learning_suggestions"):
                    status = "info"
            elif name == "Zero tool turn":
                passed, msg, status = _tuple3(fn(events, jsonl_delta))
                if not jsonl_delta.get("tool") and not any(e.get("type") == "tool_call" for e in events):
                    status = "info"
            else:
                passed, msg, status = _tuple3(fn(events))
        except Exception as exc:
            passed, msg, status = False, f"Validator error: {exc}", "fail"
        results.append({"name": name, "passed": passed, "status": status, "message": msg})
    return results


def _print_report(report: list[dict[str, Any]]) -> None:
    total = len(report)
    failed = sum(1 for row in report if not row.get("passed"))
    warnings = sum(1 for row in report if row.get("status") == "warn")
    info = sum(1 for row in report if row.get("status") == "info")
    passed = total - failed
    print("=" * 60)
    print(f"BEHAVIOR VALIDATION: {passed}/{total} non-failing ({failed} fail, {warnings} warn, {info} info)")
    print("=" * 60)
    icons = {"pass": "[PASS]", "fail": "[FAIL]", "warn": "[WARN]", "info": "[INFO]"}
    for row in report:
        status = str(row.get("status") or ("pass" if row.get("passed") else "fail"))
        icon = icons.get(status, "[PASS]" if row.get("passed") else "[FAIL]")
        print(f"  {icon} {row['name']:<22} {row['message']}")
    print("=" * 60)
    if failed:
        print(f"[ISSUES] {failed} check(s) failed — review trace for details")


# ─── CLI ─────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MO Agent session recorder and behavior validator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run agent with a prompt and record trace")
    run_p.add_argument("prompt", type=str, help="User prompt for the agent")

    replay_p = sub.add_parser("replay", help="Re-validate an existing trace")
    replay_p.add_argument("trace", type=str, help="Path to .trace file")

    serve_p = sub.add_parser("serve", help="Launch mo.py interactively with auto-tracing", add_help=False)
    serve_p.add_argument("mo_args", nargs=argparse.REMAINDER, help="Arguments forwarded to mo.py")

    sub.add_parser("list", help="List recorded traces")

    ledger_p = sub.add_parser("ledger", help="Print E2E evidence ledger from traces")
    ledger_p.add_argument("trace", nargs="?", type=str, default=None, help="Optional .trace file or directory")

    args, unknown = parser.parse_known_args()
    if args.command == "run":
        cmd_run(args.prompt)
    elif args.command == "serve":
        mo_args = (args.mo_args or []) + unknown
        if mo_args and mo_args[0] == "--":
            mo_args = mo_args[1:]
        cmd_serve(mo_args=mo_args)
    elif args.command == "replay":
        cmd_replay(args.trace)
    elif args.command == "list":
        cmd_list()
    elif args.command == "ledger":
        cmd_ledger(args.trace)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
