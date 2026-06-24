"""MO heartbeat and surface-continuity snapshots.

Heartbeat is a lightweight local truth pulse. It records where MO is being used
(terminal, Telegram, future server surfaces), the active session/task state, and
basic environment signals. The records are orientation only; live tools,
taskboards, and fresh verification remain the source of truth.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import traceback

from .atomic_write import atomic_write_text
from .backend_monitor import get_monitor, redact_monitor_text
from .instance import get_instance_id
from .number_utils import as_int as _as_int
from .path_defaults import ENV_MO_STATE_HOME, HEARTBEAT_LEDGER_PATH, ENV_HEARTBEAT_LEDGER_DISABLE, ENV_HEARTBEAT_LEDGER_PATH


SURFACE_ALIASES = {
    "": "terminal",
    "user": "terminal",
    "main": "terminal",
    "pc": "terminal",
    "desktop": "desktop",
    "companion": "desktop",
    "telegram": "telegram",
    "cron": "cron",
    "server": "server",
    "heartbeat": "heartbeat",
}


@dataclass
class HeartbeatService:
    """Small periodic heartbeat writer for long-lived MO processes."""

    agent: Any
    gateway: Any = None
    surface: str = "terminal"
    interval_seconds: float = 60.0
    enabled: bool = True
    _stop: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)

    def start(self) -> bool:
        if not self.enabled or self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="mo-heartbeat", daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=max(0.0, timeout))

    def _loop(self) -> None:
        record_heartbeat(self.agent, gateway=self.gateway, surface=self.surface, event="startup")
        interval = max(5.0, float(self.interval_seconds or 60.0))
        while not self._stop.wait(interval):
            record_heartbeat(self.agent, gateway=self.gateway, surface=self.surface, event="periodic")


def normalize_surface(surface: str) -> str:
    value = str(surface or "").strip().lower().replace(" ", "_").replace("-", "_")
    return SURFACE_ALIASES.get(value, value or "terminal")


def record_heartbeat(
    agent: Any,
    *,
    gateway: Any = None,
    surface: str = "terminal",
    event: str = "heartbeat",
    note: str = "",
    path: str | Path | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Append one heartbeat snapshot. Failures never break a turn."""
    try:
        ledger_path = _resolve_heartbeat_path(path)
        if ledger_path is None:
            return None
        snapshot = build_heartbeat_snapshot(
            agent,
            gateway=gateway,
            surface=surface,
            event=event,
            note=note,
            extra=extra,
        )
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(snapshot, ensure_ascii=False, sort_keys=True) + "\n")
        _prune_heartbeat_ledger(ledger_path)
        monitor = get_monitor()
        if monitor:
            monitor.emit("heartbeat", {k: v for k, v in snapshot.items() if k not in {"git", "extra"}})
        return snapshot
    except Exception:
        return None


HEARTBEAT_LEDGER_MAX_LINES = 2000
HEARTBEAT_LEDGER_MAX_BYTES = 2_000_000


def _prune_heartbeat_ledger(
    ledger_path: Path,
    *,
    max_lines: int = HEARTBEAT_LEDGER_MAX_LINES,
    max_bytes: int = HEARTBEAT_LEDGER_MAX_BYTES,
) -> None:
    """Bound the append-only heartbeat ledger to its most recent snapshots.

    Cheap path: only rewrites once the file grows past ``max_bytes`` (so the
    common case is a single ``stat``), then trims to the last ``max_lines`` lines.
    """
    try:
        if ledger_path.stat().st_size <= max_bytes:
            return
        lines = ledger_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) <= max_lines:
            return
        kept = lines[-max_lines:]
        atomic_write_text(ledger_path, "\n".join(kept) + "\n", encoding="utf-8")
    except Exception:
        return


def build_heartbeat_snapshot(
    agent: Any,
    *,
    gateway: Any = None,
    surface: str = "terminal",
    event: str = "heartbeat",
    note: str = "",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    session = getattr(agent, "session", None)
    session_id = str(getattr(session, "session_id", "") or "")
    board = getattr(gateway, "last_task_board", None) if gateway is not None else None
    snapshot = {
        "created_at": time.time(),
        "event": str(event or "heartbeat")[:80],
        "surface": normalize_surface(surface),
        "instance_id": get_instance_id(),
        "note": redact_monitor_text(note, 240),
        "pid": os.getpid(),
        "cwd": redact_monitor_text(os.getcwd(), 260),
        "session_id": redact_monitor_text(session_id, 120),
        "slot": redact_monitor_text(str(getattr(getattr(agent, "_sessions", None), "current_name", "main") or "main"), 80),
        "turn_count": _as_int(getattr(session, "turn_count", 0)),
        "message_count": len(getattr(session, "messages", []) or []),
        "provider": redact_monitor_text(str(getattr(agent, "provider_name", "") or ""), 80),
        "model": redact_monitor_text(str(getattr(agent, "model", "") or ""), 120),
        "context": _context_pressure(agent),
        "taskboard": _taskboard_state(board, session_id=session_id),
        "workers": _worker_state(agent),
        "goal": _goal_state(agent),
        "git": _git_state(),
        "extra": _safe_extra(extra),
    }
    return snapshot


def read_recent_heartbeats(
    *,
    limit: int = 5,
    path: str | Path | None = None,
    surface: str = "",
    instance_id: str = "",
) -> list[dict[str, Any]]:
    """Read recent heartbeat snapshots, oldest-to-newest. Returns [] on failure."""
    try:
        ledger_path = _resolve_heartbeat_path(path)
        if ledger_path is None or not ledger_path.exists() or not ledger_path.is_file():
            return []
        raw_lines = ledger_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    wanted = normalize_surface(surface) if surface else ""
    wanted_instance = str(instance_id or "").strip()
    matches: list[dict[str, Any]] = []
    for raw in reversed(raw_lines):
        try:
            item = json.loads(raw)
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        if wanted and normalize_surface(str(item.get("surface") or "")) != wanted:
            continue
        if wanted_instance and str(item.get("instance_id") or "") != wanted_instance:
            continue
        matches.append(item)
        if len(matches) >= max(1, int(limit or 1)):
            break
    return list(reversed(matches))


def build_surface_environment_context(
    agent: Any,
    *,
    current_surface: str = "terminal",
    max_chars: int = 300,
) -> str:
    """Return a compact environment-awareness block for every non-greeting turn.

    Injects surface, OS, CWD, and shell so the provider never guesses runtime context.
    This is factual, always-current, and should be treated as a non-negotiable
    environment contract.
    """
    import platform

    surface = normalize_surface(current_surface or getattr(agent, "_current_route_source", "terminal"))
    cwd = str(getattr(agent, "project_cwd", "") or os.getcwd())
    os_name = platform.system() or "Unknown"
    # Detect active shell
    if os.environ.get("SHELL"):
        shell_path = os.environ["SHELL"]
    elif os.name == "nt":
        # On Windows, COMSPEC reliably names the active command processor
        # (cmd.exe, powershell.exe, pwsh.exe). PSModulePath only indicates
        # PowerShell is installed, not that it's the active shell.
        shell_path = os.environ.get("COMSPEC", "unknown")
    else:
        shell_path = os.environ.get("COMSPEC", "unknown")
    shell_name = Path(shell_path).name if shell_path and shell_path != "unknown" else shell_path
    return f"Surface: {surface} | OS: {os_name} | CWD: {cwd} | Shell: {shell_name}"


def build_surface_continuity_context(
    agent: Any,
    *,
    current_surface: str = "terminal",
    path: str | Path | None = None,
    max_chars: int = 900,
) -> str:
    """Return provider-facing continuity context when the user changes surfaces."""
    current = normalize_surface(current_surface or getattr(agent, "_current_route_source", "terminal"))
    recent = read_recent_heartbeats(limit=12, path=path, instance_id=get_instance_id())
    other = None
    for item in reversed(recent):
        surface = normalize_surface(str(item.get("surface") or ""))
        if surface and surface != current:
            other = item
            break
    if not other:
        return ""
    age = _age_text(time.time() - float(other.get("created_at") or 0.0))
    task = other.get("taskboard") if isinstance(other.get("taskboard"), dict) else {}
    goal = other.get("goal") if isinstance(other.get("goal"), list) else []
    lines = [
        "### MO Heartbeat Surface Continuity — orientation only",
        f"Current surface: {current}. Recent other surface: {normalize_surface(str(other.get('surface') or ''))} ({age} ago).",
        "Maintain consistent behavior/profile/workflow across surfaces, but verify live files, taskboard, workers, and goals before factual claims.",
        f"Recent slot/session: `{redact_monitor_text(other.get('slot', ''), 80)}` / `{redact_monitor_text(other.get('session_id', ''), 120)}`.",
    ]
    if task:
        title = redact_monitor_text(task.get("title", ""), 160)
        lines.append(
            f"Recent board: {task.get('completed', 0)}/{task.get('total', 0)} done"
            + (f" — {title}" if title else "")
        )
    if goal:
        lines.append("Recent goal: " + redact_monitor_text("; ".join(str(x) for x in goal[:2]), 240))
    return _cap_text("\n".join(lines).strip(), max_chars)


def render_heartbeat_status(agent: Any = None, *, gateway: Any = None, path: str | Path | None = None) -> str:
    instance_id = str(getattr(agent, "instance_id", "") or get_instance_id()) if agent is not None else ""
    latest = read_recent_heartbeats(limit=1, path=path, instance_id=instance_id)
    if latest:
        item = latest[-1]
    elif agent is not None:
        item = build_heartbeat_snapshot(agent, gateway=gateway, surface=getattr(agent, "_current_route_source", "terminal"), event="status")
    else:
        return "Heartbeat: no heartbeat snapshots recorded yet."
    surface = normalize_surface(str(item.get("surface") or ""))
    age = _age_text(time.time() - float(item.get("created_at") or 0.0))
    task = item.get("taskboard") if isinstance(item.get("taskboard"), dict) else {}
    lines = [
        "Heartbeat:",
        f"  surface: {surface} · {age} ago",
        f"  session id:   {item.get('session_id', '')}",
        f"  session slot: {item.get('slot', '')}",
        f"  model:        {item.get('provider', '')} / {item.get('model', '')}",
    ]
    if task and task.get("total"):
        lines.append(f"  board:   {task.get('completed', 0)}/{task.get('total', 0)} done · state {task.get('state', '')}")
    return "\n".join(lines)


def start_heartbeat_service_if_enabled(agent: Any, gateway: Any = None, *, surface: str = "terminal") -> HeartbeatService | None:
    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    hb_cfg = cfg.get("heartbeat", {}) if isinstance(cfg.get("heartbeat", {}), dict) else {}
    if hb_cfg.get("enabled", True) is False:
        return None
    interval = float(hb_cfg.get("interval_seconds", 60) or 60)
    service = HeartbeatService(agent=agent, gateway=gateway, surface=surface, interval_seconds=interval, enabled=True)
    service.start()
    try:
        setattr(agent, "heartbeat_service", service)
    except Exception:
        traceback.print_exc()
    return service


def _resolve_heartbeat_path(path: str | Path | None = None) -> Path | None:
    if os.environ.get(ENV_HEARTBEAT_LEDGER_DISABLE, "").strip().lower() in {"1", "true", "yes"}:
        return None
    if path:
        return Path(path)
    env_path = os.environ.get(ENV_HEARTBEAT_LEDGER_PATH, "")
    if env_path:
        return Path(env_path)
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    state_home = os.environ.get(ENV_MO_STATE_HOME, "").strip()
    if state_home:
        return Path(state_home) / HEARTBEAT_LEDGER_PATH
    return Path(HEARTBEAT_LEDGER_PATH)


def _context_pressure(agent: Any) -> dict[str, Any]:
    try:
        from .session.handoff import context_pressure
        pressure = context_pressure(agent)
        return {
            "pressure": round(float(pressure.get("pressure") or 0.0), 4),
            "chars": _as_int(pressure.get("chars", 0)),
            "budget_chars": _as_int(pressure.get("budget_chars", 0)),
            "messages": _as_int(pressure.get("message_count", 0)),
            "max_history": _as_int(pressure.get("max_history", 0)),
            "trimmed": _as_int(pressure.get("trimmed_messages_count", 0)),
        }
    except Exception:
        return {}


def _taskboard_state(board: Any, *, session_id: str = "") -> dict[str, Any]:
    if board is None:
        try:
            from .tasking.task_board import read_recent_snapshots
            from .tasking.task_board_context import compile_board_context_from_snapshot, task_row_counts
            recent = read_recent_snapshots(limit=1, session_id=session_id) if session_id else []
            if recent:
                item = recent[-1]
                tasks = list(item.get("tasks") or [])
                counts = task_row_counts(tasks)
                context = compile_board_context_from_snapshot(item, max_tasks=3, max_evidence=1, max_chars=500)
                return {
                    "source": str(item.get("source") or "ledger"),
                    "title": redact_monitor_text(item.get("title", ""), 160),
                    "state": str(item.get("state") or ""),
                    "total": counts["total"],
                    "completed": counts["completed"],
                    "open": counts["open"],
                    "active_task_id": context.get("active_task_id", ""),
                    "ready_task_id": context.get("ready_task_id", ""),
                }
        except Exception:
            traceback.print_exc()
        return {"source": "none", "total": 0, "completed": 0, "open": 0, "state": ""}
    tasks = list(getattr(board, "tasks", []) or [])
    counts = {
        "total": len(tasks),
        "completed": sum(1 for task in tasks if getattr(task, "status", "") == "completed"),
        "open": sum(1 for task in tasks if getattr(task, "status", "") in {"pending", "active", "blocked"}),
    }
    try:
        from .tasking.task_board_context import compile_board_context, task_row_counts
        context = compile_board_context(board, max_tasks=3, max_evidence=1, max_chars=500)
        counts = task_row_counts(tasks)
    except Exception:
        context = {"active_task_id": "", "ready_task_id": "", "graph": {"valid": True}}
    return {
        "source": str(getattr(board, "source", "live") or "live"),
        "title": redact_monitor_text(str(getattr(board, "title", "") or ""), 160),
        "state": str(getattr(board, "state", "") or ""),
        "total": counts["total"],
        "completed": counts["completed"],
        "open": counts["open"],
        "active_task_id": context.get("active_task_id", ""),
        "ready_task_id": context.get("ready_task_id", ""),
        "graph_valid": bool((context.get("graph") or {}).get("valid", True)),
    }


def _worker_state(agent: Any) -> list[str]:
    try:
        from .coordination_state import worker_summary_lines
        return [redact_monitor_text(item, 220) for item in worker_summary_lines(agent)[:6]]
    except Exception:
        return []


def _goal_state(agent: Any) -> list[str]:
    try:
        from .coordination_state import goal_summary_lines
        return [redact_monitor_text(item, 220) for item in goal_summary_lines(agent)[:6]]
    except Exception:
        return []


def _git_state() -> list[str]:
    try:
        proc = subprocess.run(
            ["git", "status", "--short", "--branch"],
            text=True,
            capture_output=True,
            timeout=2,
        )
        if proc.returncode != 0:
            return []
        return [redact_monitor_text(line, 240) for line in proc.stdout.splitlines()[:12] if line.strip()]
    except Exception:
        return []


def _safe_extra(extra: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(extra, dict):
        return {}
    safe: dict[str, Any] = {}
    for key, value in list(extra.items())[:20]:
        if isinstance(value, (int, float, bool)) or value is None:
            safe[str(key)[:80]] = value
        else:
            safe[str(key)[:80]] = redact_monitor_text(value, 300)
    return safe


def _age_text(seconds: float) -> str:
    try:
        seconds = max(0, int(seconds))
    except Exception:
        seconds = 0
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def _cap_text(text: str, max_chars: int) -> str:
    value = str(text or "").strip()
    if not max_chars or len(value) <= max_chars:
        return value
    return value[: max(0, max_chars - 30)].rstrip() + "\n[heartbeat context truncated]"
