"""MO heartbeat and surface-continuity snapshots.

Heartbeat is a lightweight local truth pulse. It records where MO is being used
(terminal, Telegram, future server surfaces), the active session/task state, and
basic environment signals. The records are orientation only; live tools,
taskboards, and fresh verification remain the source of truth.
"""
from __future__ import annotations

from core.state.configuration_defaults import DEFAULT_PREFERENCES

import json
import os
import subprocess
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import traceback

from ..utils.atomic_write import atomic_write_text
from .backend_monitor import get_monitor, redact_monitor_text
from .instance import get_instance_id
from .lock import _pid_alive
from .subprocess_flags import apply_windows_hidden_process_flags
from . import surface_identity as _surface_identity
from ..utils.jsonl_utils import read_recent_ledger_entries, resolve_ledger_path
from ..utils.number_utils import as_int as _as_int
from ..utils.text_utils import cap_text
from ..state.paths import ENV_HEARTBEAT_LEDGER_DISABLE, ENV_HEARTBEAT_LEDGER_PATH, HEARTBEAT_LEDGER_PATH, resolve_state_path


@dataclass
class HeartbeatService:
    """Small periodic heartbeat writer for long-lived MO processes."""

    agent: Any
    gateway: Any = None
    surface: str = "terminal"
    interval_seconds: float = DEFAULT_PREFERENCES["heartbeat.interval_seconds"]
    enabled: bool = True
    scope_factory: Any = None
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
        self._record("startup")
        interval = max(5.0, float(self.interval_seconds or DEFAULT_PREFERENCES["heartbeat.interval_seconds"]))
        while not self._stop.wait(interval):
            self._record("periodic")
            self._care()

    def _care(self) -> None:
        """MO Care rides every MO process's heartbeat; one process looks per interval (see ``mo_care.tick``)."""
        try:
            from core.systemcare.mo_care import tick

            config = getattr(self.agent, "config", None)
            tick(config if isinstance(config, dict) else {})
        except Exception:
            return

    def _record(self, event: str) -> None:
        try:
            scope = self.scope_factory() if callable(self.scope_factory) else nullcontext()
            with scope:
                record_heartbeat(self.agent, gateway=self.gateway, surface=self.surface, event=event)
        except Exception:
            # A secondary-surface scope can disappear during shutdown. One failed
            # pulse must not kill the long-lived heartbeat thread or leak into the
            # foreground session by retrying without its owning scope.
            return


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
        ledger_path = _resolve_heartbeat_path(_configured_heartbeat_path(agent, path))
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
        # Once per terminal launch, drop entries for instances whose process is
        # long gone so the registry can't surface phantom "stale"/"live" siblings
        # (the source of the mo-handoff drift notice). Gated on "startup" so the
        # per-pid liveness probe runs once, not on every heartbeat.
        if str(event) == "startup":
            _prune_stale_instances(ledger_path)
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


STALE_INSTANCE_RETENTION_SECONDS = 1800  # keep dead-pid entries ~30 min (recent handoff history)


def _prune_stale_instances(
    ledger_path: Path,
    *,
    retention_seconds: float = STALE_INSTANCE_RETENTION_SECONDS,
) -> None:
    """Drop heartbeat entries for dead instances older than the retention window.

    The append-only ledger otherwise carries every exited instance until the 2 MB
    size cap, so the instance registry keeps surfacing phantom "stale"/"live"
    siblings (the mo-handoff drift notice). Keep entries whose pid is still alive,
    plus any recent enough to be a just-exited handoff; drop the rest. Unparseable
    or pid-less lines are kept untouched.
    """
    try:
        if not ledger_path.exists():
            return
        now = time.time()
        lines = ledger_path.read_text(encoding="utf-8", errors="replace").splitlines()
        kept: list[str] = []
        dropped = 0
        alive_cache: dict[int, bool] = {}
        for raw in lines:
            if not raw.strip():
                continue
            try:
                item = json.loads(raw)
                pid = int(item.get("pid") or 0)
                created = float(item.get("created_at") or 0.0)
            except Exception:
                kept.append(raw)
                continue
            if pid <= 0:
                kept.append(raw)
                continue
            if pid not in alive_cache:
                alive_cache[pid] = _pid_alive(pid)
            recent = bool(created) and (now - created) <= retention_seconds
            if alive_cache[pid] or recent:
                kept.append(raw)
            else:
                dropped += 1
        if dropped:
            atomic_write_text(ledger_path, ("\n".join(kept) + "\n") if kept else "", encoding="utf-8")
    except Exception:
        return


def record_computer_activity(
    agent: Any,
    tool: str,
    *,
    arguments: dict[str, Any] | None = None,
    active: bool,
    surface: str = "terminal",
    path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Publish a bounded computer-use cue through the canonical heartbeat ledger."""
    previous = getattr(agent, "_computer_activity", None)
    previous = previous if isinstance(previous, dict) else {}
    now = time.time()
    args = arguments if isinstance(arguments, dict) else {}
    activity = {
        "active": bool(active),
        "tool": str(tool or "")[:48],
        "kind": str(args.get("kind") or "")[:32],
        "operation": str(args.get("operation") or args.get("action") or "")[:48],
        "started_at": float(previous.get("started_at") or now),
        "updated_at": now,
    }
    try:
        setattr(agent, "_computer_activity", activity)
        return record_heartbeat(
            agent,
            gateway=getattr(agent, "gateway", None),
            surface=surface,
            event="computer_tool_active" if active else "computer_tool_finished",
            path=path,
        )
    finally:
        if not active:
            try:
                setattr(agent, "_computer_activity", {})
            except Exception:
                pass


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
    board = _live_taskboard(agent, gateway=gateway, session_id=session_id)
    normalized_surface = _surface_identity.normalize_runtime_surface(surface)
    from ..provider.model_catalog import active_model_selection

    return {
        "created_at": time.time(),
        "event": str(event or "heartbeat")[:80],
        "surface": normalized_surface,
        "instance_id": get_instance_id(),
        "note": redact_monitor_text(note, 240),
        "pid": os.getpid(),
        # The run id this process stamps on its monitor events: readers tell work left open by a closed MO from live work.
        "monitor_run": str(getattr(get_monitor(), "run_id", "") or ""),
        "cwd": redact_monitor_text(os.getcwd(), 260),
        "session_id": redact_monitor_text(session_id, 120),
        "slot": redact_monitor_text(_active_session_slot(agent), 80),
        "turn_count": _as_int(getattr(session, "turn_count", 0)),
        "message_count": len(getattr(session, "messages", []) or []),
        "provider": redact_monitor_text(str(getattr(agent, "provider_name", "") or ""), 80),
        "model": redact_monitor_text(str(getattr(agent, "model", "") or ""), 120),
        "model_selection": {key: redact_monitor_text(value, 120) for key, value in active_model_selection(agent).items()},
        "model_control": _safe_extra(getattr(agent, "_settings_model_control", None)),
        "context": _context_pressure(agent),
        "taskboard": _taskboard_state(board, session_id=session_id),
        "workers": _worker_state(agent),
        "goal": _goal_state(agent),
        # Desktop publishes liveness and bounded work ownership; repository
        # state belongs to Terminal/Role project work. Avoid spawning Git from
        # every Desktop turn and periodic background heartbeat.
        "git": (
            []
            if normalized_surface in _surface_identity.DESKTOP_SURFACES
            else _git_state()
        ),
        "computer_activity": _computer_activity_state(getattr(agent, "_computer_activity", None)),
        "turn": _turn_state(getattr(agent, "_heartbeat_turn", None)),
        "recent_files": _recent_files_state(agent),
        # An MO Shell pane has no window title of its own; its host window lets the rail show it.
        "shell_host": _shell_host_window(),
        "extra": _safe_extra(extra),
    }


def _live_taskboard(agent: Any, *, gateway: Any = None, session_id: str = "") -> Any | None:
    """Return a live taskboard for heartbeat without reviving stale sessions."""
    candidates = []
    if getattr(agent, "_goal_active", False):
        candidates.append(getattr(agent, "_goal_task_board", None))
    if gateway is not None:
        candidates.append(getattr(gateway, "last_task_board", None))
    candidates.append(getattr(agent, "_active_task_board", None))
    for board in candidates:
        if board is None:
            continue
        board_session = str(getattr(board, "session_id", "") or "")
        if session_id and board_session and board_session != session_id:
            continue
        return board
    return None


def _active_session_slot(agent: Any) -> str:
    """Return the thread-scoped surface slot before the terminal manager slot."""
    state = getattr(agent, "_thread_state", None)
    scoped = str(getattr(state, "surface_session_slot", "") if state is not None else "").strip()
    if scoped:
        return scoped
    return str(getattr(getattr(agent, "_sessions", None), "current_name", "main") or "main")


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
    wanted = _surface_identity.normalize_runtime_surface(surface) if surface else ""
    wanted_instance = str(instance_id or "").strip()

    def _filter(item: dict[str, Any]) -> bool:
        if wanted and _surface_identity.normalize_runtime_surface(str(item.get("surface") or "")) != wanted:
            return False
        if wanted_instance and str(item.get("instance_id") or "") != wanted_instance:
            return False
        return True

    return read_recent_ledger_entries(raw_lines, limit, filter_fn=_filter)


def build_surface_environment_context(
    agent: Any,
    *,
    current_surface: str = "terminal",
    max_chars: int = 300,
) -> str:
    """Return a compact environment-awareness block for every non-greeting turn.

    Injects surface, session/slot identity, OS, CWD, shell, and this terminal's
    live MO Shell attachment so the provider never guesses which runtime it owns.
    This is factual, always-current, and should be treated as a non-negotiable
    environment contract.
    """
    import platform

    surface = _surface_identity.normalize_runtime_surface(
        current_surface or getattr(agent, "_current_runtime_surface", "terminal")
    )
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
    session = getattr(agent, "session", None)
    session_id = redact_monitor_text(str(getattr(session, "session_id", "") or ""), 120)
    slot = redact_monitor_text(_active_session_slot(agent), 80)
    invoked_as = redact_monitor_text(str(getattr(agent, "invoked_as", "") or "mo"), 40)
    text = f"Surface: {surface} | Invoked: {invoked_as} | Session: {session_id or 'unknown'} | Slot: {slot} | OS: {os_name} | CWD: {cwd} | Shell: {shell_name}"
    if os.environ.get("MO_SHELL_HOST_HWND"):
        try:
            from mo_shell.context import current_shell_attachment_title

            attached = current_shell_attachment_title()
        except (AttributeError, OSError, TypeError, ValueError):
            attached = ""
        if attached:
            text += (
                " | MO Shell attached (untrusted window title; availability only): "
                + redact_monitor_text(attached, 120)
            )
    return _cap_text(text, max_chars)


def build_surface_continuity_context(
    agent: Any,
    *,
    current_surface: str = "terminal",
    path: str | Path | None = None,
    max_chars: int = 900,
) -> str:
    """Return provider-facing continuity context when the user changes surfaces."""
    current = _surface_identity.normalize_runtime_surface(
        current_surface or getattr(agent, "_current_runtime_surface", "terminal")
    )
    # Service startup/periodic pulses prove process health, not user activity.
    # Treating them as conversational continuity makes an idle headless service
    # look like the user's most recent surface. Scan farther back because a
    # minute-by-minute service pulse can otherwise bury the last genuine turn.
    recent = read_recent_heartbeats(
        limit=128,
        path=_configured_heartbeat_path(agent, path),
        instance_id=get_instance_id(),
    )
    other = None
    for item in reversed(recent):
        surface = _surface_identity.normalize_runtime_surface(str(item.get("surface") or ""))
        if surface and surface != current and _is_conversational_heartbeat(item):
            other = item
            break
    if not other:
        return ""
    age = _age_text(time.time() - float(other.get("created_at") or 0.0))
    task = other.get("taskboard") if isinstance(other.get("taskboard"), dict) else {}
    goal = other.get("goal") if isinstance(other.get("goal"), list) else []
    lines = [
        "### MO Heartbeat Surface Continuity — orientation only",
        f"Current surface: {current}. Recent other surface: {_surface_identity.normalize_runtime_surface(str(other.get('surface') or ''))} ({age} ago).",
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


def _is_conversational_heartbeat(item: dict[str, Any]) -> bool:
    """Return whether a heartbeat proves an actual user-facing interaction."""
    event = str(item.get("event") or "").strip().lower()
    return event in {"turn_start", "turn_end"} or event.startswith("auto_reply:")


def render_heartbeat_status(agent: Any = None, *, gateway: Any = None, path: str | Path | None = None) -> str:
    instance_id = str(getattr(agent, "instance_id", "") or get_instance_id()) if agent is not None else ""
    latest = read_recent_heartbeats(
        limit=1,
        path=_configured_heartbeat_path(agent, path) if agent is not None else path,
        instance_id=instance_id,
    )
    if latest:
        item = latest[-1]
    elif agent is not None:
        item = build_heartbeat_snapshot(
            agent,
            gateway=gateway,
            surface=getattr(agent, "_current_runtime_surface", "terminal"),
            event="status",
        )
    else:
        return "Heartbeat: no heartbeat snapshots recorded yet."
    surface = _surface_identity.normalize_runtime_surface(str(item.get("surface") or ""))
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


def start_heartbeat_service_if_enabled(
    agent: Any,
    gateway: Any = None,
    *,
    surface: str = "terminal",
    scope_factory: Any = None,
) -> HeartbeatService | None:
    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    hb_cfg = cfg.get("heartbeat", {}) if isinstance(cfg.get("heartbeat", {}), dict) else {}
    if hb_cfg.get("enabled", DEFAULT_PREFERENCES["heartbeat.enabled"]) is False:
        return None
    interval = float(hb_cfg.get("interval_seconds", DEFAULT_PREFERENCES["heartbeat.interval_seconds"]) or DEFAULT_PREFERENCES["heartbeat.interval_seconds"])
    service = HeartbeatService(
        agent=agent,
        gateway=gateway,
        surface=surface,
        interval_seconds=interval,
        enabled=True,
        scope_factory=scope_factory,
    )
    service.start()
    try:
        setattr(agent, "heartbeat_service", service)
    except Exception:
        traceback.print_exc()
    return service


def _resolve_heartbeat_path(path: str | Path | None = None) -> Path | None:
    return resolve_ledger_path(
        path=path,
        disable_env=ENV_HEARTBEAT_LEDGER_DISABLE,
        path_env=ENV_HEARTBEAT_LEDGER_PATH,
        default_name=HEARTBEAT_LEDGER_PATH,
    )


def _configured_heartbeat_path(agent: Any, path: str | Path | None) -> str | Path | None:
    """Honor an explicit config runtime home without defeating env overrides."""
    if path is not None or os.environ.get(ENV_HEARTBEAT_LEDGER_PATH, "").strip():
        return path
    config = getattr(agent, "config", None)
    runtime = config.get("runtime") if isinstance(config, dict) and isinstance(config.get("runtime"), dict) else {}
    if runtime.get("home"):
        return resolve_state_path(HEARTBEAT_LEDGER_PATH, config)
    return path


def _context_pressure(agent: Any) -> dict[str, Any]:
    try:
        from ..session.handoff import context_pressure
        pressure = context_pressure(agent)
        return {
            "pressure": round(float(pressure.get("pressure") or 0.0), 4),
            "raw_pressure": round(float(pressure.get("raw_pressure") or pressure.get("pressure") or 0.0), 4),
            "pressure_source": str(pressure.get("pressure_source") or ""),
            "chars": _as_int(pressure.get("chars", 0)),
            "budget_chars": _as_int(pressure.get("budget_chars", 0)),
            "char_ratio": round(float(pressure.get("char_ratio") or 0.0), 4),
            "estimated_tokens": _as_int(pressure.get("estimated_tokens", 0)),
            "budget_tokens": _as_int(pressure.get("budget_tokens", 0)),
            "token_ratio": round(float(pressure.get("token_ratio") or 0.0), 4),
            "messages": _as_int(pressure.get("message_count", 0)),
            "max_history": _as_int(pressure.get("max_history", 0)),
            "message_ratio": round(float(pressure.get("message_ratio") or 0.0), 4),
            "retention_ratio": round(float(pressure.get("retention_ratio") or 0.0), 4),
            "retention_over_limit": bool(pressure.get("retention_over_limit")),
            "over_limit": bool(pressure.get("over_limit")),
            "trimmed": _as_int(pressure.get("trimmed_messages_count", 0)),
        }
    except Exception:
        return {}


def _taskboard_task_title(tasks: list[Any], task_id: Any) -> str:
    """Return one redacted task title without making heartbeat a task owner."""
    from ..tasking.task_board_context import task_row_title, task_row_value

    wanted = str(task_id or "").strip()
    if not wanted:
        return ""
    for task in tasks:
        if str(task_row_value(task, "id", "") or "").strip() == wanted:
            title = task_row_title(task)
            return redact_monitor_text(title, 180) if title else ""
    return ""


def _taskboard_state(board: Any, *, session_id: str = "") -> dict[str, Any]:
    if board is None:
        try:
            from ..tasking.task_board import read_recent_snapshots
            from ..tasking.task_board_context import compile_board_context_from_snapshot, task_row_counts
            recent = read_recent_snapshots(limit=1, session_id=session_id) if session_id else []
            if recent:
                item = recent[-1]
                tasks = list(item.get("tasks") or [])
                counts = task_row_counts(tasks)
                context = compile_board_context_from_snapshot(item, max_tasks=3, max_evidence=1, max_chars=500)
                active_id = str(context.get("active_task_id") or "")
                ready_id = str(context.get("ready_task_id") or "")
                return {
                    "source": str(item.get("source") or "ledger"),
                    "plan_owner": str(item.get("plan_owner") or ""),
                    "title": redact_monitor_text(item.get("title", ""), 160),
                    "state": str(item.get("state") or ""),
                    "total": counts["total"],
                    "completed": counts["completed"],
                    "open": counts["open"],
                    "active_task_id": active_id,
                    "ready_task_id": ready_id,
                    "active_task_title": _taskboard_task_title(tasks, active_id),
                    "next_task_title": "" if active_id else _taskboard_task_title(tasks, ready_id),
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
        from ..tasking.task_board_context import compile_board_context, task_row_counts
        context = compile_board_context(board, max_tasks=3, max_evidence=1, max_chars=500)
        counts = task_row_counts(tasks)
    except Exception:
        context = {"active_task_id": "", "ready_task_id": "", "graph": {"valid": True}}
    active_id = str(context.get("active_task_id") or "")
    ready_id = str(context.get("ready_task_id") or "")
    return {
        "source": str(getattr(board, "source", "live") or "live"),
        "plan_owner": str(getattr(board, "plan_owner", "") or ""),
        "title": redact_monitor_text(str(getattr(board, "title", "") or ""), 160),
        "state": str(getattr(board, "state", "") or ""),
        "total": counts["total"],
        "completed": counts["completed"],
        "open": counts["open"],
        "active_task_id": active_id,
        "ready_task_id": ready_id,
        "active_task_title": _taskboard_task_title(tasks, active_id),
        "next_task_title": "" if active_id else _taskboard_task_title(tasks, ready_id),
        "graph_valid": bool((context.get("graph") or {}).get("valid", True)),
    }


def _shell_host_window() -> int:
    try:
        return max(0, int(os.environ.get("MO_SHELL_HOST_HWND") or 0))
    except ValueError:
        return 0


def _turn_state(value: Any) -> dict[str, Any]:
    """The running turn (set by the Gateway for its duration), or {} when idle."""
    if not isinstance(value, dict):
        return {}
    try:
        started = float(value.get("started_at") or 0.0)
    except (TypeError, ValueError):
        started = 0.0
    return {"busy": True, "request": redact_monitor_text(value.get("request") or "", 100), "started_at": started}


_RECENT_FILE_SECONDS = 1800.0


def _recent_files_state(agent: Any) -> list[dict[str, Any]]:
    """Files this process changed in the last 30 minutes, newest first (at most five): a path
    inside the working folder stays relative, any other path shows only its name."""
    now = time.time()
    try:
        base = Path(os.getcwd()).resolve()
    except OSError:
        base = None
    rows: list[dict[str, Any]] = []
    for path, at in reversed(list(getattr(agent, "_recent_writes", []) or [])):
        try:
            at = float(at)
            target = Path(str(path))
        except (TypeError, ValueError):
            continue
        if now - at > _RECENT_FILE_SECONDS:
            continue
        shown = target.name
        if base is not None:
            try:
                shown = target.relative_to(base).as_posix()
            except ValueError:
                pass
        rows.append({"path": redact_monitor_text(shown, 200), "at": at})
        if len(rows) >= 5:
            break
    return rows


def _worker_state(agent: Any) -> list[str]:
    try:
        from ..context.coordination_state import worker_summary_lines
        return [redact_monitor_text(item, 220) for item in worker_summary_lines(agent)[:6]]
    except Exception:
        return []


def _goal_state(agent: Any) -> list[str]:
    try:
        from ..context.coordination_state import goal_summary_lines
        return [redact_monitor_text(item, 220) for item in goal_summary_lines(agent)[:6]]
    except Exception:
        return []


def _git_state() -> list[str]:
    try:
        kwargs = {
            "text": True,
            "capture_output": True,
            "timeout": 2,
        }
        apply_windows_hidden_process_flags(kwargs)
        proc = subprocess.run(
            ["git", "status", "--short", "--branch"],
            **kwargs,
        )
        if proc.returncode != 0:
            return []
        return [redact_monitor_text(line, 240) for line in proc.stdout.splitlines()[:12] if line.strip()]
    except Exception:
        return []


def _computer_activity_state(activity: Any) -> dict[str, Any]:
    if not isinstance(activity, dict) or not activity:
        return {}
    return {
        "active": bool(activity.get("active")),
        "tool": redact_monitor_text(activity.get("tool", ""), 48),
        "kind": redact_monitor_text(activity.get("kind", ""), 32),
        "operation": redact_monitor_text(activity.get("operation", ""), 48),
        "started_at": float(activity.get("started_at") or 0.0),
        "updated_at": float(activity.get("updated_at") or 0.0),
    }


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
    return cap_text(text, max_chars, marker="[heartbeat context truncated]")
