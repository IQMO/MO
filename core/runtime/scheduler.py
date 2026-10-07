"""Small MO-native scheduler shared by the service, provider tool, and commands.

Scheduled MO turns run through Gateway/GoalRunner; compute jobs may run only an
existing private script. Plain reminders emit their text without a model turn.
All entry points share one locked store and append-only run ledger, with optional
Desktop or Telegram surfacing.
"""
from __future__ import annotations

from core.state.configuration_defaults import DEFAULT_PREFERENCES

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable
import traceback

from .backend_monitor import get_monitor, redact_monitor_text
from .heartbeat import record_heartbeat
from .subprocess_flags import apply_windows_hidden_process_flags
from ..session.session import Session
from ..agent.agent_utils import load_session_from_manager
from ..state.paths import resolve_state_path
from .lock import acquire_runtime_lock, release_runtime_lock
from ..state.secrets import resolve_secret
from ..utils.atomic_write import atomic_write_text

DEFAULT_SCHEDULER_DIR = "memory/scheduler"
DEFAULT_JOBS_PATH = f"{DEFAULT_SCHEDULER_DIR}/jobs.json"
DEFAULT_RUNS_PATH = f"{DEFAULT_SCHEDULER_DIR}/runs.jsonl"
DEFAULT_LOCK_PATH = f"{DEFAULT_SCHEDULER_DIR}/tick.lock"
DEFAULT_SCRIPTS_DIR = f"{DEFAULT_SCHEDULER_DIR}/scripts"
ENV_SCHEDULER_DISABLE = "MO_SCHEDULER_DISABLE"
SCHEDULED_TASK_CREATION_FOLLOWUP = "Do you want me to remind you about this scheduled task later?"


@dataclass(frozen=True)
class SchedulerRun:
    job_id: str
    status: str
    started_at: float
    finished_at: float
    kind: str
    output: str = ""
    error: str = ""
    delivered: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": int((self.finished_at - self.started_at) * 1000),
            "kind": self.kind,
            "output_preview": redact_monitor_text(self.output, 500),
            "error": redact_monitor_text(self.error, 500),
            "delivered": self.delivered,
        }


@dataclass
class SchedulerPaths:
    jobs: Path = field(default_factory=lambda: Path(DEFAULT_JOBS_PATH))
    runs: Path = field(default_factory=lambda: Path(DEFAULT_RUNS_PATH))
    lock: Path = field(default_factory=lambda: Path(DEFAULT_LOCK_PATH))


@dataclass
class SchedulerService:
    agent: Any
    gateway: Any
    paths: SchedulerPaths = field(default_factory=SchedulerPaths)
    tick_seconds: float = DEFAULT_PREFERENCES["scheduler.tick_seconds"]
    enabled: bool = False
    on_run: Callable[[SchedulerRun], Any] | None = None
    _stop: threading.Event = field(default_factory=threading.Event, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _run_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def start(self) -> bool:
        if not self.enabled or self._thread and self._thread.is_alive():
            return False
        self._ensure_store()
        self.startup_check()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="mo-scheduler", daemon=True)
        self._thread.start()
        _emit_scheduler_event("scheduler_started", {"jobs_path": str(self.paths.jobs), "runs_path": str(self.paths.runs)})
        return True

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=max(0.0, timeout))
        release_runtime_lock(getattr(self, "_runtime_lock", None))
        self._runtime_lock = None
        _emit_scheduler_event("scheduler_stopped", {})

    def startup_check(self, *, now: float | None = None) -> dict[str, Any]:
        """Validate scheduled tasks at service boot and send due review prompts."""
        current = float(now if now is not None else time.time())
        self._ensure_store()
        with _FileLock(self.paths.lock):
            data = _load_jobs(self.paths.jobs)
            jobs = _jobs_list(data)
            changed = False
            summary = {"total": len(jobs), "enabled": 0, "disabled": 0, "due": 0, "review_due": 0, "stale_claims_cleared": 0}
            for job in jobs:
                changed = _normalize_job_schedule(job, current) or changed
                if job.get("enabled", True):
                    summary["enabled"] += 1
                else:
                    summary["disabled"] += 1
                if _job_due(job, current):
                    summary["due"] += 1
                if _clear_stale_claim(job, current):
                    summary["stale_claims_cleared"] += 1
                    changed = True
                if _review_due(job, current):
                    summary["review_due"] += 1
                    if _send_review_prompt(self.agent, job, current):
                        changed = True
            if changed:
                _save_jobs(self.paths.jobs, data)
        _emit_scheduler_event("scheduler_startup_check", summary)
        return summary

    def tick(self, *, now: float | None = None) -> list[SchedulerRun]:
        """Claim and run due scheduled tasks once. Safe to call from tests."""
        if not self.enabled or os.environ.get(ENV_SCHEDULER_DISABLE, "").lower() in {"1", "true", "yes"}:
            return []
        from core.systemcare.dashboard import read_dashboard_status
        if read_dashboard_status(self.agent.config).get("pause_schedules"):
            return []
        self._ensure_store()
        current = float(now if now is not None else time.time())
        with _FileLock(self.paths.lock):
            data = _load_jobs(self.paths.jobs)
            jobs = _jobs_list(data)
            due: list[dict[str, Any]] = []
            changed = False
            for job in jobs:
                changed = _normalize_job_schedule(job, current) or changed
                if _job_due(job, current):
                    _claim_job(job, current)
                    due.append(dict(job))
                    changed = True
            if changed:
                _save_jobs(self.paths.jobs, data)
        runs: list[SchedulerRun] = []
        for job in due:
            run = self._run_job(job)
            runs.append(run)
            self._record_run_and_update_job(job, run)
            callback = self.on_run or getattr(
                self.agent, "_everywhere_scheduler_run_callback", None
            )
            if callable(callback):
                try:
                    callback(run)
                except Exception as exc:
                    _emit_scheduler_event(
                        "scheduler_run_callback_error",
                        {
                            "job_id": run.job_id,
                            "error_type": type(exc).__name__,
                            "error": redact_monitor_text(exc, 160),
                        },
                    )
        return runs

    def _loop(self) -> None:
        record_heartbeat(self.agent, gateway=self.gateway, surface="scheduler", event="scheduler_start")
        interval = max(5.0, float(self.tick_seconds or DEFAULT_PREFERENCES["scheduler.tick_seconds"]))
        while not self._stop.wait(interval):
            try:
                runs = self.tick()
                if runs:
                    record_heartbeat(self.agent, gateway=self.gateway, surface="scheduler", event="scheduler_tick", extra={"runs": len(runs)})
            except Exception as exc:
                _emit_scheduler_event("scheduler_tick_error", {"error_type": type(exc).__name__, "error": redact_monitor_text(exc, 240)})

    def _run_job(self, job: dict[str, Any]) -> SchedulerRun:
        started = time.time()
        job_id = _job_id(job)
        kind = str(job.get("kind") or "turn").strip().lower()
        output = ""
        error = ""
        status = "ok"
        delivered = False
        try:
            if kind == "reminder":
                output = str(job.get("prompt") or "").strip()
                if not output:
                    raise ValueError("Scheduled reminder is empty")
            else:
                with self._execution_lock():
                    if kind == "turn":
                        output = self._run_turn_job(job)
                    elif kind == "goal":
                        output = self._run_goal_job(job)
                    elif kind == "role":
                        output = self._run_role_job(job)
                    elif kind == "script":
                        output = self._run_script_job(job)
                    elif kind == "systemcare":
                        from core.systemcare.automation import run_scheduled_care
                        output = run_scheduled_care(self.agent.config, job.get("systemcare") or {})
                    else:
                        raise ValueError(f"Unsupported scheduler job kind: {kind}")
            delivered = _deliver_if_configured(self.agent, job, output)
        except Exception as exc:
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
        finished = time.time()
        run = SchedulerRun(job_id=job_id, status=status, started_at=started, finished_at=finished, kind=kind, output=output, error=error, delivered=delivered)
        _append_run(self.paths.runs, run.as_dict())
        _emit_scheduler_event("scheduler_job_run", run.as_dict())
        return run

    def _run_turn_job(self, job: dict[str, Any]) -> str:
        prompt = str(job.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("Scheduled turn job missing prompt")
        session_name = str(job.get("session") or job.get("session_name") or f"scheduler-{_job_id(job)}")
        session = _load_scheduler_session(self.agent, session_name)
        with _scheduler_turn_scope(self.agent, session, session_name):
            result = self.gateway.run_turn(prompt, route_source="scheduler")
        _save_scheduler_session(self.agent, session_name, session)
        return str(result or "")

    def _run_goal_job(self, job: dict[str, Any]) -> str:
        objective = str(job.get("prompt") or job.get("objective") or "").strip()
        if not objective:
            raise ValueError("Scheduled goal job missing prompt/objective")
        from ..goal import GoalRunner

        session_name = str(job.get("session") or job.get("session_name") or f"scheduler-{_job_id(job)}")
        session = _load_scheduler_session(self.agent, session_name)
        max_iterations = max(1, int(job.get("max_iterations", 10) or 10))

        def _run() -> str:
            runner = GoalRunner(self.agent)
            parts = [str(runner.start(objective) or "")]
            for _ in range(max_iterations - 1):
                if not getattr(self.agent, "_goal_active", False):
                    break
                parts.append(str(runner.continue_goal() or ""))
            return "\n\n".join(part for part in parts if part)

        # Isolate the goal's session like _run_turn_job so scheduled goal tool
        # chains don't contaminate (or get contaminated by) the live conversation.
        with _scheduler_turn_scope(self.agent, session, session_name):
            result = _run()
        _save_scheduler_session(self.agent, session_name, session)
        return result

    def _run_role_job(self, job: dict[str, Any]) -> str:
        """Run a scheduled turn governed by a named role (a skill with `role:`),
        reusing the same overlay + tool-scope + mastery wiring as a /role worker —
        synchronously, so the output still flows through delivery."""
        objective = str(job.get("prompt") or job.get("objective") or "").strip()
        role = str(job.get("role") or "").strip()
        if not objective:
            raise ValueError("Scheduled role job missing prompt/objective")
        if not role:
            raise ValueError("Scheduled role job missing role")
        from ..skills import (
            default_skill_roots,
            resolve_role,
            role_overlay_text,
            record_skill_outcome,
        )

        agent = self.agent
        roots = default_skill_roots(
            getattr(agent, "project_cwd", None),
            getattr(agent, "runtime_home", None),
            profile=getattr(agent, "profile", None),
            config=getattr(agent, "config", None),
        )
        role_skill = resolve_role(
            role,
            roots,
            profile=getattr(agent, "profile", None),
            project_cwd=getattr(agent, "project_cwd", None),
        )
        if role_skill is None:
            raise ValueError(f"Scheduled role job references unknown role: {role}")

        base = str(getattr(agent, "system_message", "You are MO.") or "You are MO.")
        role_context = (
            ""
            if callable(getattr(agent, "provider_scope", None))
            else role_overlay_text(role_skill)
        )
        session = Session(base + role_context)
        session._loaded_meta = {"surface": "scheduler"}
        session_name = str(
            job.get("session") or job.get("session_name") or f"scheduler-{_job_id(job)}"
        )
        result = ""
        try:
            with _scheduler_turn_scope(
                agent,
                session,
                session_name,
                role=role_skill,
            ):
                result = self.gateway.run_turn(objective, route_source="scheduler")
        finally:
            try:
                # A scheduled run counts as a use; nobody checked its report, so it is not a success.
                record_skill_outcome(getattr(role_skill, "source", ""), "use")
            except Exception:
                pass
        return str(result or "")

    def _run_script_job(self, job: dict[str, Any]) -> str:
        """Run an explicitly installed private script without an LLM turn."""
        from ..tooling.sandbox import safe_env

        script = _private_script_path(self.agent, str(job.get("script") or ""))
        suffix = script.suffix.lower()
        if suffix == ".py":
            command = [sys.executable, str(script)]
        elif suffix in {".ps1"} and os.name == "nt":
            command = ["powershell.exe", "-NoProfile", "-File", str(script)]
        elif suffix in {".cmd", ".bat"} and os.name == "nt":
            command = ["cmd.exe", "/d", "/c", str(script)]
        elif suffix in {".sh", ".bash"} and os.name != "nt":
            command = ["/bin/bash", str(script)]
        else:
            raise ValueError(f"Unsupported scheduled script type: {suffix or '<none>'}")
        timeout = max(1, min(3600, int(job.get("timeout_seconds") or 300)))
        run_kwargs = {
            "cwd": str(script.parent), "capture_output": True, "text": True,
            "timeout": timeout, "check": False, "env": safe_env(),
        }
        apply_windows_hidden_process_flags(run_kwargs)
        completed = subprocess.run(command, **run_kwargs)
        output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
        if completed.returncode:
            raise RuntimeError(f"script exited {completed.returncode}: {output[:1000]}")
        return output or "Scheduled script completed with no output."

    def _record_run_and_update_job(self, claimed_job: dict[str, Any], run: SchedulerRun) -> None:
        current = run.finished_at
        with _FileLock(self.paths.lock):
            data = _load_jobs(self.paths.jobs)
            for job in _jobs_list(data):
                if _job_id(job) != _job_id(claimed_job):
                    continue
                job.pop("running_since", None)
                job["last_run_at"] = current
                job["last_status"] = run.status
                job["last_error"] = run.error
                job["run_count"] = int(job.get("run_count") or 0) + 1
                if _schedule_kind(job) == "once":
                    job["enabled"] = False
                    job["next_run_at"] = None
                else:
                    job["next_run_at"] = _next_run_after(job, current)
                _send_review_prompt(self.agent, job, current)
                break
            _save_jobs(self.paths.jobs, data)

    def _execution_lock(self):
        tg = getattr(self.agent, "telegram_gateway", None) or getattr(self.agent, "_telegram_gateway", None)
        lock = getattr(tg, "agent_lock", None)
        return lock if lock is not None else self._run_lock

    def _ensure_store(self) -> None:
        self.paths.jobs.parent.mkdir(parents=True, exist_ok=True)
        self.paths.runs.parent.mkdir(parents=True, exist_ok=True)
        self.paths.lock.parent.mkdir(parents=True, exist_ok=True)
        if not self.paths.jobs.exists():
            _save_jobs(self.paths.jobs, {"jobs": []})


def scheduler_service_enabled(agent: Any) -> bool:
    """Return whether this process is configured to start the scheduler."""
    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    scheduler_cfg = cfg.get("scheduler", {}) if isinstance(cfg.get("scheduler", {}), dict) else {}
    disabled_by_env = os.environ.get(ENV_SCHEDULER_DISABLE, "").lower() in {"1", "true", "yes"}
    from core.systemcare.config import normalized_systemcare_preferences
    care_enabled = normalized_systemcare_preferences(cfg)["automation"]["enabled"]
    return (scheduler_cfg.get("enabled", DEFAULT_PREFERENCES["scheduler.enabled"]) is True or care_enabled) and not disabled_by_env


def start_scheduler_service_if_enabled(agent: Any, gateway: Any = None) -> SchedulerService | None:
    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    scheduler_cfg = cfg.get("scheduler", {}) if isinstance(cfg.get("scheduler", {}), dict) else {}
    if not scheduler_service_enabled(agent):
        return None
    resource_lock = acquire_runtime_lock(lock_name="mo-scheduler.lock", label="MO scheduler", quiet=True)
    if resource_lock is None:
        _emit_scheduler_event("scheduler_not_started", {"reason": "resource lock held"})
        return None
    paths = scheduler_paths(agent)
    service = SchedulerService(
        agent=agent,
        gateway=gateway or getattr(agent, "gateway", None),
        paths=paths,
        tick_seconds=float(scheduler_cfg.get("tick_seconds", DEFAULT_PREFERENCES["scheduler.tick_seconds"]) or DEFAULT_PREFERENCES["scheduler.tick_seconds"]),
        enabled=True,
    )
    service._runtime_lock = resource_lock
    if not service.start():
        release_runtime_lock(resource_lock)
        return None
    try:
        setattr(agent, "scheduler_service", service)
    except Exception:
        traceback.print_exc()
    return service


def scheduler_paths(agent: Any) -> SchedulerPaths:
    """Resolve the one scheduler store used by services, commands, and Desktop."""
    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    scheduler_cfg = cfg.get("scheduler", {}) if isinstance(cfg.get("scheduler", {}), dict) else {}
    return SchedulerPaths(
        jobs=Path(resolve_state_path(scheduler_cfg.get("jobs_path") or DEFAULT_JOBS_PATH, cfg)),
        runs=Path(resolve_state_path(scheduler_cfg.get("runs_path") or DEFAULT_RUNS_PATH, cfg)),
        lock=Path(resolve_state_path(scheduler_cfg.get("lock_path") or DEFAULT_LOCK_PATH, cfg)),
    )


def manage_scheduler_jobs(
    agent: Any,
    action: str,
    arguments: dict[str, Any] | None = None,
    *,
    now: float | None = None,
    owner: dict[str, str] | None = None,
    required_owner: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Manage scheduler jobs through the same locked JSON store the service uses."""
    args = dict(arguments or {})
    verb = str(action or args.pop("action", "") or "list").strip().lower()
    if verb not in {"create", "list", "update", "pause", "resume", "run", "remove"}:
        raise ValueError(f"Unsupported schedule action: {verb}")
    if owner is not None and verb != "create":
        raise ValueError("Schedule ownership can only be set during creation")
    if required_owner is not None and verb not in {"list", "remove"}:
        raise ValueError("Schedule ownership can only restrict listing or removal")
    normalized_owner = _scheduler_owner(owner) if owner is not None else None
    normalized_required_owner = _scheduler_owner(required_owner) if required_owner is not None else None
    current = float(now if now is not None else time.time())
    paths = scheduler_paths(agent)
    if verb == "list" and not paths.jobs.exists():
        return _scheduler_result(agent, verb, jobs=[])
    paths.jobs.parent.mkdir(parents=True, exist_ok=True)
    paths.lock.parent.mkdir(parents=True, exist_ok=True)
    with _FileLock(paths.lock):
        data = _load_jobs(paths.jobs)
        jobs = _jobs_list(data)
        changed = False
        for job in jobs:
            changed = _normalize_job_schedule(job, current) or changed

        if verb == "list":
            if changed:
                _save_jobs(paths.jobs, data)
            visible_jobs = (
                [job for job in jobs if _job_owned_by(job, normalized_required_owner)]
                if normalized_required_owner is not None
                else jobs
            )
            return _scheduler_result(agent, verb, jobs=visible_jobs)

        if verb == "create":
            job = _new_job(agent, args, current)
            if normalized_owner is not None:
                job["_owner"] = normalized_owner
            jobs.append(job)
            data["jobs"] = jobs
            _save_jobs(paths.jobs, data)
            return _scheduler_result(agent, verb, job=job)

        job = _find_job(jobs, str(args.get("job_id") or args.get("id") or args.get("name") or ""))
        if job is None:
            raise ValueError("Scheduled task not found")
        if verb == "remove":
            if normalized_required_owner is not None and not _job_owned_by(job, normalized_required_owner):
                raise ValueError("Scheduled task not found")
            data["jobs"] = [item for item in jobs if item is not job]
            _save_jobs(paths.jobs, data)
            return _scheduler_result(agent, verb, job=job)
        if verb == "pause":
            job["enabled"] = False
            job["next_run_at"] = None
        elif verb == "resume":
            job["enabled"] = True
            job["next_run_at"] = _resume_at(job, current)
        elif verb == "run":
            job["enabled"] = True
            job["next_run_at"] = current
        elif verb == "update":
            _update_job(agent, job, args, current)
        _save_jobs(paths.jobs, data)
        return _scheduler_result(agent, verb, job=job)


def format_scheduler_jobs(result: dict[str, Any]) -> str:
    """Return compact local-command/provider output without a second view model."""
    jobs = list(result.get("jobs") or ([result["job"]] if result.get("job") else []))
    if not jobs:
        return "[SCHEDULE] No scheduled tasks."
    lines = [f"[SCHEDULE] {str(result.get('action') or 'list').upper()} · {len(jobs)} task(s)"]
    for job in jobs[:50]:
        enabled = "active" if job.get("enabled", True) else "paused"
        name = str(job.get("name") or job.get("id") or "task")
        kind = str(job.get("kind") or "turn")
        when = _format_next_run(job.get("next_run_at"))
        lines.append(f"- {name} [{job.get('id')}] · {kind} · {enabled} · {when}")
    if result.get("scheduler_enabled") is not True:
        lines.append("Scheduler service is disabled; tasks are saved but will not run until scheduler.enabled is true in the resident MO service.")
    return "\n".join(lines)


def _scheduler_result(agent: Any, action: str, *, jobs: list[dict[str, Any]] | None = None, job: dict[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"action": action, "scheduler_enabled": scheduler_service_enabled(agent)}
    if jobs is not None:
        result["jobs"] = [dict(item) for item in jobs]
    if job is not None:
        result["job"] = dict(job)
    return result


def _scheduler_owner(value: dict[str, str]) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != {"surface", "device_id"}:
        raise ValueError("Schedule owner requires exact surface and device identity")
    surface = str(value.get("surface") or "").strip()
    device_id = str(value.get("device_id") or "").strip()
    if not surface or len(surface) > 40 or not device_id or len(device_id) > 128:
        raise ValueError("Schedule owner identity is invalid")
    return {"surface": surface, "device_id": device_id}


def _job_owned_by(job: dict[str, Any], owner: dict[str, str]) -> bool:
    stored = job.get("_owner")
    return isinstance(stored, dict) and stored == owner


def _new_job(agent: Any, args: dict[str, Any], now: float) -> dict[str, Any]:
    kind = str(args.get("kind") or ("script" if args.get("script") else "turn")).strip().lower()
    if kind not in {"turn", "goal", "role", "script", "reminder", "systemcare"}:
        raise ValueError(f"Unsupported scheduled task kind: {kind}")
    prompt = str(args.get("prompt") or args.get("objective") or "").strip()
    role = str(args.get("role") or "").strip()
    script = str(args.get("script") or "").strip()
    if kind == "script":
        _private_script_path(agent, script)
    elif kind != "systemcare" and not prompt:
        raise ValueError("Scheduled task requires a prompt/objective")
    if kind == "role" and not role:
        raise ValueError("Scheduled role task requires a role")
    schedule = parse_schedule(str(args.get("schedule") or ""), now=now)
    job: dict[str, Any] = {
        "id": f"job-{uuid.uuid4().hex[:8]}",
        "name": str(args.get("name") or "").strip()[:80],
        "kind": kind,
        "enabled": True,
        "schedule": schedule,
        "created_at": now,
        "created_from": str(getattr(agent, "_provider_surface", lambda: "terminal")() or "terminal")[:40],
    }
    if kind == "systemcare":
        from core.systemcare.config import normalized_systemcare_preferences
        job["systemcare"] = normalized_systemcare_preferences({"mo_desktop": {"systemcare": {"automation": args.get("systemcare") or {}}}})["automation"]
    if prompt:
        job["prompt"] = prompt
    if role:
        job["role"] = role
    if script:
        job["script"] = script
        job["timeout_seconds"] = max(1, min(3600, int(args.get("timeout_seconds") or 300)))
    deliver = _delivery_config(args.get("deliver"))
    if deliver:
        job["deliver"] = deliver
    if args.get("review_later") is True:
        job["review"] = {"ask_later": True, "after_runs": 1}
    _normalize_job_schedule(job, now)
    return job


def _update_job(agent: Any, job: dict[str, Any], args: dict[str, Any], now: float) -> None:
    if "kind" in args:
        job["kind"] = str(args.get("kind") or "").strip().lower()
    for key in ("name", "prompt", "objective", "role"):
        if key in args:
            target = "prompt" if key == "objective" else key
            job[target] = str(args.get(key) or "").strip()
    if "schedule" in args:
        job["schedule"] = parse_schedule(str(args.get("schedule") or ""), now=now)
        job["next_run_at"] = None
    if "deliver" in args:
        delivery = _delivery_config(args.get("deliver"))
        if delivery:
            job["deliver"] = delivery
        else:
            job.pop("deliver", None)
    if "script" in args:
        script = str(args.get("script") or "").strip()
        _private_script_path(agent, script)
        job["script"] = script
    if "timeout_seconds" in args:
        job["timeout_seconds"] = max(1, min(3600, int(args.get("timeout_seconds") or 300)))
    if "review_later" in args:
        if args.get("review_later") is True:
            job["review"] = {"ask_later": True, "after_runs": 1}
        else:
            job.pop("review", None)
    _validate_job(agent, job)
    if job.get("enabled", True) and job.get("next_run_at") is None:
        job["next_run_at"] = _resume_at(job, now)


def _validate_job(agent: Any, job: dict[str, Any]) -> None:
    kind = str(job.get("kind") or "turn").lower()
    if kind not in {"turn", "goal", "role", "script", "reminder", "systemcare"}:
        raise ValueError(f"Unsupported scheduled task kind: {kind}")
    if kind == "script":
        _private_script_path(agent, str(job.get("script") or ""))
    elif kind != "systemcare" and not str(job.get("prompt") or "").strip():
        raise ValueError("Scheduled task requires a prompt/objective")
    if kind == "role" and not str(job.get("role") or "").strip():
        raise ValueError("Scheduled role task requires a role")


def _find_job(jobs: list[dict[str, Any]], reference: str) -> dict[str, Any] | None:
    needle = str(reference or "").strip().lower()
    if not needle:
        raise ValueError("Scheduled task id or name is required")
    exact = [job for job in jobs if str(job.get("id") or "").lower() == needle]
    if exact:
        return exact[0]
    named = [job for job in jobs if str(job.get("name") or "").lower() == needle]
    if len(named) > 1:
        raise ValueError(f"Scheduled task name is ambiguous: {reference}")
    return named[0] if named else None


def parse_schedule(value: str, *, now: float | None = None) -> dict[str, Any]:
    """Parse the small schedule language MO already documented: delay, interval, or ISO time."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("Schedule is required, for example '30m', 'every 2h', or an ISO timestamp")
    current = float(now if now is not None else time.time())
    lowered = text.lower()
    if lowered.startswith("every "):
        return {"type": "interval", "interval_seconds": _duration_seconds(text[6:].strip())}
    if lowered.startswith("daily at "):
        clock = _parse_clock(text[9:].strip())
        return {"type": "daily", "at": clock}
    try:
        return {"type": "once", "run_at": current + _duration_seconds(text)}
    except ValueError:
        pass
    raw = text[3:].strip() if lowered.startswith("at ") else text
    try:
        run_at = datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except ValueError as exc:
        raise ValueError(f"Invalid schedule: {value!r}") from exc
    return {"type": "once", "run_at": run_at}


def _delivery_config(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    text = str(value or "").strip()
    if not text or text in {"local", "desktop"}:
        return {"target": text} if text else {}
    if text.lower().startswith("telegram:"):
        chat_id = text.split(":", 1)[1].strip()
        if not chat_id:
            raise ValueError("Telegram delivery requires a chat id")
        return {"target": "telegram", "telegram_chat_id": chat_id}
    raise ValueError("Delivery must be local, desktop, or telegram:<chat-id>")


def _private_script_path(agent: Any, value: str) -> Path:
    script_name = str(value or "").strip()
    if not script_name or Path(script_name).is_absolute():
        raise ValueError("Scheduled scripts must be relative files under the private MO scripts directory")
    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    root = Path(resolve_state_path(DEFAULT_SCRIPTS_DIR, cfg)).resolve(strict=False)
    script = (root / script_name).resolve(strict=False)
    try:
        script.relative_to(root)
    except ValueError as exc:
        raise ValueError("Scheduled script escapes the private MO scripts directory") from exc
    if not script.is_file():
        raise ValueError(f"Scheduled script not found under private MO scripts: {script_name}")
    return script


def _resume_at(job: dict[str, Any], now: float) -> float | None:
    if _schedule_kind(job) in {"interval", "daily"}:
        return _next_run_after(job, now)
    run_at = _run_at(job)
    return max(now, run_at) if run_at is not None else now


def _parse_clock(value: str) -> str:
    try:
        parsed = datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValueError("Daily schedules require 24-hour HH:MM time") from exc
    return parsed.strftime("%H:%M")


def _format_next_run(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value)).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return "not scheduled"


def _load_jobs(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {"jobs": []}
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
        if isinstance(data, list):
            return {"jobs": data}
        if isinstance(data, dict):
            data.setdefault("jobs", [])
            return data
    except Exception:
        traceback.print_exc()
    return {"jobs": []}


def _save_jobs(path: Path, data: dict[str, Any]) -> None:
    atomic_write_text(
        path,
        json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
    )


def _jobs_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    jobs = data.get("jobs")
    if isinstance(jobs, list):
        return [job for job in jobs if isinstance(job, dict)]
    return []


def _job_id(job: dict[str, Any]) -> str:
    value = str(job.get("id") or "").strip()
    if not value:
        value = f"job-{abs(hash(json.dumps(job, sort_keys=True, default=str))) % 1_000_000}"
        job["id"] = value
    return value[:80]


def _schedule_kind(job: dict[str, Any]) -> str:
    schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
    kind = str(schedule.get("type") or schedule.get("kind") or "").lower()
    if not kind:
        kind = "interval" if (schedule.get("interval_seconds") or schedule.get("every") or job.get("interval_seconds")) else "once"
    return kind


def _normalize_job_schedule(job: dict[str, Any], now: float) -> bool:
    changed = False
    if "id" not in job:
        _job_id(job)
        changed = True
    if job.get("enabled") is None:
        job["enabled"] = True
        changed = True
    if job.get("next_run_at") is None and job.get("enabled", True):
        if job.get("run_immediately"):
            job["next_run_at"] = now
        elif _schedule_kind(job) in {"interval", "daily"}:
            job["next_run_at"] = _next_run_after(job, now)
        else:
            job["next_run_at"] = _run_at(job)
        changed = True
    return changed


def _job_due(job: dict[str, Any], now: float) -> bool:
    if job.get("enabled") is False:
        return False
    if job.get("running_since"):
        return False
    try:
        next_run = float(job.get("next_run_at") or 0.0)
    except Exception:
        return False
    return next_run > 0 and next_run <= now


def _claim_job(job: dict[str, Any], now: float) -> None:
    job["running_since"] = now
    job["last_claimed_at"] = now


def _clear_stale_claim(job: dict[str, Any], now: float, *, stale_seconds: float = 900.0) -> bool:
    try:
        running_since = float(job.get("running_since") or 0.0)
    except Exception:
        running_since = 0.0
    if running_since and now - running_since > stale_seconds:
        job.pop("running_since", None)
        job["last_status"] = "recovered_stale_claim"
        return True
    return False


def _next_run_after(job: dict[str, Any], now: float) -> float | None:
    if _schedule_kind(job) == "daily":
        schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
        hour, minute = (int(part) for part in _parse_clock(str(schedule.get("at") or "")).split(":"))
        local_now = datetime.fromtimestamp(now).astimezone()
        candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate.timestamp() <= now:
            candidate += timedelta(days=1)
        return candidate.timestamp()
    seconds = _interval_seconds(job)
    if seconds <= 0:
        return None
    return float(now + seconds)


def _interval_seconds(job: dict[str, Any]) -> int:
    schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
    raw = schedule.get("interval_seconds") or schedule.get("seconds") or job.get("interval_seconds")
    if raw:
        try:
            return max(1, int(raw))
        except Exception:
            traceback.print_exc()
    every = schedule.get("every") or job.get("every")
    if every:
        return _duration_seconds(str(every))
    return 0


def _run_at(job: dict[str, Any]) -> float | None:
    schedule = job.get("schedule") if isinstance(job.get("schedule"), dict) else {}
    value = schedule.get("run_at") or job.get("run_at")
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        traceback.print_exc()
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _duration_seconds(value: str) -> int:
    import re

    match = re.match(r"^\s*(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)\s*$", str(value), re.I)
    if not match:
        raise ValueError(f"Invalid schedule duration: {value!r}")
    amount = int(match.group(1))
    unit = match.group(2).lower()[0]
    return amount * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def _append_run(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")


def _load_scheduler_session(agent: Any, session_name: str) -> Session:
    return load_session_from_manager(
        agent, session_name,
        session_id_prefix=f"mo-scheduler-{_safe_name(session_name)}",
        sanitize=False,
    )


@contextmanager
def _scheduler_turn_scope(
    agent: Any,
    session: Session,
    session_name: str,
    *,
    role: Any = None,
):
    """Install every scheduler-owned turn boundary from one lifecycle owner."""
    with ExitStack() as stack:
        isolated = getattr(agent, "isolated_session", None)
        if callable(isolated):
            stack.enter_context(isolated(session))
        surface = getattr(agent, "surface_session_scope", None)
        if callable(surface):
            stack.enter_context(surface(str(session_name or "")))
        provider = getattr(agent, "provider_scope", None)
        if role is not None and callable(provider):
            stack.enter_context(provider("scheduler", role=role))
        yield


def _save_scheduler_session(agent: Any, session_name: str, session: Session) -> None:
    manager = getattr(agent, "_sessions", None)
    if manager and hasattr(manager, "save_snapshot"):
        try:
            manager.save_snapshot(session_name, session, extra_meta={"surface": "scheduler"})
        except Exception:
            traceback.print_exc()


def _safe_name(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isalnum() or ch in "-_.")[:40] or "job"


def _deliver_if_configured(agent: Any, job: dict[str, Any], text: str) -> bool:
    deliver = job.get("deliver") if isinstance(job.get("deliver"), dict) else {}
    chat_id = str(deliver.get("telegram_chat_id") or deliver.get("telegram_chat") or "").strip()
    if not chat_id:
        return False
    message = str(text or "").strip() or "MO scheduled task completed."
    return _send_telegram_message(agent, chat_id, message)


def _review_due(job: dict[str, Any], now: float) -> bool:
    review = job.get("review") if isinstance(job.get("review"), dict) else {}
    if not review or review.get("ask_later") is not True:
        return False
    try:
        last_asked = float(review.get("last_asked_at") or 0.0)
    except Exception:
        last_asked = 0.0
    interval = max(3600.0, float(review.get("repeat_after_seconds") or 604800.0))
    if last_asked and now - last_asked < interval:
        return False
    try:
        next_ask = float(review.get("next_ask_at") or 0.0)
    except Exception:
        next_ask = 0.0
    if next_ask and next_ask <= now:
        return True
    try:
        after_runs = int(review.get("after_runs") or 0)
    except Exception:
        after_runs = 0
    return after_runs > 0 and int(job.get("run_count") or 0) >= after_runs


def _send_review_prompt(agent: Any, job: dict[str, Any], now: float) -> bool:
    if not _review_due(job, now):
        return False
    review = job.get("review") if isinstance(job.get("review"), dict) else {}
    deliver = job.get("deliver") if isinstance(job.get("deliver"), dict) else {}
    chat_id = str(review.get("telegram_chat_id") or deliver.get("telegram_chat_id") or deliver.get("telegram_chat") or "").strip()
    if not chat_id:
        return False
    task_id = _job_id(job)
    prompt = str(review.get("prompt") or f"Reminder about scheduled task: {task_id}. You asked me to remind you later because you might reconsider it. Do you want to keep it, change it, or remove it?").strip()
    if not _send_telegram_message(agent, chat_id, prompt):
        return False
    review["last_asked_at"] = now
    review["next_ask_at"] = now + max(3600.0, float(review.get("repeat_after_seconds") or 604800.0))
    job["review"] = review
    return True


def _send_telegram_message(agent: Any, chat_id: str, text: str) -> bool:
    telegram = getattr(agent, "telegram_gateway", None) or getattr(agent, "_telegram_gateway", None)
    if telegram is not None and not getattr(telegram, "enabled", False):
        return False
    token_env = str(getattr(telegram, "token_env", "TELEGRAM_BOT_TOKEN") if telegram is not None else "TELEGRAM_BOT_TOKEN")
    token = resolve_secret(
        token_env,
        config=getattr(agent, "config", {}) or {},
        service="telegram",
    ).strip()
    if not token:
        return False
    import httpx

    message = str(text or "").strip() or "MO scheduled task completed."
    if len(message) > 3500:
        message = message[:3400].rstrip() + "\n\n[truncated]"
    response = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat_id, "text": message}, timeout=15.0)
    data = response.json()
    return bool(data.get("ok"))


class _FileLock:
    def __init__(self, path: Path, *, stale_seconds: float = 300.0):
        self.path = Path(path)
        self.stale_seconds = stale_seconds
        self.fd: int | None = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()
        try:
            if self.path.exists() and now - self.path.stat().st_mtime > self.stale_seconds:
                self.path.unlink()
        except Exception:
            traceback.print_exc()
        try:
            self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(self.fd, str(os.getpid()).encode("ascii", errors="ignore"))
        except FileExistsError as exc:
            raise RuntimeError(f"scheduler tick already locked: {self.path}") from exc
        return self

    def __exit__(self, *_args):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except Exception:
                traceback.print_exc()
            self.fd = None
        try:
            self.path.unlink()
        except Exception:
            traceback.print_exc()


def _emit_scheduler_event(kind: str, payload: dict[str, Any]) -> None:
    try:
        monitor = get_monitor()
        if monitor:
            data = {"component": "scheduler"}
            data.update(payload or {})
            if "kind" in (payload or {}):
                data["job_kind"] = payload["kind"]   # a run's own kind must not overwrite the event's name
            data["kind"] = kind
            monitor.emit("session_event", data)
    except Exception:
        traceback.print_exc()
