"""Small MO-native scheduler.

Scheduler v1 is intentionally private/config-driven. It does not expose a public
cron tool and it does not let the provider create jobs. Jobs come from local
runtime config/files, run through Gateway/GoalRunner, write append-only run
logs, and optionally deliver results to Telegram.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import traceback

from .backend_monitor import get_monitor, redact_monitor_text
from .heartbeat import record_heartbeat
from ..session.session import Session
from ..agent.agent_utils import load_session_from_manager
from ..state.paths import resolve_state_path
from .lock import acquire_runtime_lock, release_runtime_lock
from ..state.secrets import resolve_secret

DEFAULT_SCHEDULER_DIR = "memory/scheduler"
DEFAULT_JOBS_PATH = f"{DEFAULT_SCHEDULER_DIR}/jobs.json"
DEFAULT_RUNS_PATH = f"{DEFAULT_SCHEDULER_DIR}/runs.jsonl"
DEFAULT_LOCK_PATH = f"{DEFAULT_SCHEDULER_DIR}/tick.lock"
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
    tick_seconds: float = 30.0
    enabled: bool = False
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
        return runs

    def _loop(self) -> None:
        record_heartbeat(self.agent, gateway=self.gateway, surface="scheduler", event="scheduler_start")
        interval = max(5.0, float(self.tick_seconds or 30.0))
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
            with self._execution_lock():
                if kind == "turn":
                    output = self._run_turn_job(job)
                elif kind == "goal":
                    output = self._run_goal_job(job)
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
        isolated = getattr(self.agent, "isolated_session", None)
        if callable(isolated):
            with isolated(session):
                result = self.gateway.run_turn(prompt, route_source="scheduler")
        else:
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
        isolated = getattr(self.agent, "isolated_session", None)
        if callable(isolated):
            with isolated(session):
                result = _run()
        else:
            result = _run()
        _save_scheduler_session(self.agent, session_name, session)
        return result

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


def start_scheduler_service_if_enabled(agent: Any, gateway: Any = None) -> SchedulerService | None:
    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    scheduler_cfg = cfg.get("scheduler", {}) if isinstance(cfg.get("scheduler", {}), dict) else {}
    if scheduler_cfg.get("enabled", False) is not True:
        return None
    if os.environ.get(ENV_SCHEDULER_DISABLE, "").lower() in {"1", "true", "yes"}:
        return None
    resource_lock = acquire_runtime_lock(lock_name="mo-scheduler.lock", label="MO scheduler")
    if resource_lock is None:
        _emit_scheduler_event("scheduler_not_started", {"reason": "resource lock held"})
        return None
    paths = SchedulerPaths(
        jobs=Path(resolve_state_path(scheduler_cfg.get("jobs_path") or DEFAULT_JOBS_PATH, cfg)),
        runs=Path(resolve_state_path(scheduler_cfg.get("runs_path") or DEFAULT_RUNS_PATH, cfg)),
        lock=Path(resolve_state_path(scheduler_cfg.get("lock_path") or DEFAULT_LOCK_PATH, cfg)),
    )
    service = SchedulerService(
        agent=agent,
        gateway=gateway or getattr(agent, "gateway", None),
        paths=paths,
        tick_seconds=float(scheduler_cfg.get("tick_seconds", 30) or 30),
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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


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
        elif _schedule_kind(job) == "interval":
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
    secret_files = tuple(getattr(telegram, "secret_files", ()) or ()) if telegram is not None else ()
    token = resolve_secret(token_env, files=secret_files).strip()
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
            data = {"kind": kind, "component": "scheduler"}
            data.update(payload or {})
            monitor.emit("session_event", data)
    except Exception:
        traceback.print_exc()
