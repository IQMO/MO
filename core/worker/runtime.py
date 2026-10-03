"""Generic background MO worker runtime.

Runs independent background worker turns in isolated sessions while the worker
registry owns visible lifecycle truth.
"""
from __future__ import annotations

from core.state.configuration_defaults import DEFAULT_PREFERENCES

from contextlib import nullcontext
import threading
from typing import Any, Callable
import traceback

from ..context.gateway_helpers import select_template, words
from ..provider.provider import clean_provider_error, current_provider_request_limit, provider_request_overrides
from ..context.work_patterns import estimate_work_complexity
from ..session.session import Session, assistant_result_is_incomplete
from .registry import WorkerRecord, ensure_worker_registry, extract_worker_paths, normalize_worker_paths


def summarize_background_test_output(command: object, state: str) -> str:
    """Name the invoked scope; a pytest run is not MO's complete delivery gate."""
    from tools.shell import _verification_command_reuse_family

    family = _verification_command_reuse_family(command)
    scope = {"mo-test-suite:broad": "full suite", "pytest:broad": "pytest run"}.get(family, "command")
    if state == "completed":
        return f"{scope} {'passed' if family else 'completed'}"
    if state == "timed_out":
        return f"{scope} timed out"
    if state == "cancelled":
        return f"{scope} cancelled"
    return f"{scope} failed"


def format_worker_completion_notice(record: WorkerRecord) -> str:
    """Return a compact user-facing completion notice for native terminals."""
    kind = str(getattr(record, "kind", "worker") or "worker").lower()
    state = str(getattr(record, "state", "") or "").lower()
    summary = str(getattr(record, "result_summary", "") or getattr(record, "note", "") or getattr(record, "objective", "") or "finished").strip()
    summary = " ".join(summary.split())[:180]
    if kind == "prt":
        label = "PRT"
    elif str(getattr(record, "source", "") or "").lower() == "prt":
        label = "PRT correction"
    elif kind == "shell":
        label = "Tests"
    else:
        label = "Worker"
    if kind == "shell" and state == "completed":
        status = "passed"
    elif kind == "shell" and "timed out" in str(getattr(record, "note", "") or "").lower():
        status = "timed out"
    elif kind == "shell" and state == "blocked":
        status = "failed"
    elif kind == "shell" and state == "cancelled":
        status = "cancelled"
    elif state == "completed":
        status = "completed"
    elif state == "blocked":
        status = "blocked"
    elif state == "cancelled":
        status = "paused"
    else:
        status = state or "finished"
    return f"{label} {status}: {summary} · detail /status"


def notify_native_async(agent: Any, record: WorkerRecord | None) -> None:
    """Notify native terminal callback, if one is installed."""
    if record is None:
        return
    callback = getattr(agent, "_native_async_notice", None)
    if not callable(callback):
        return
    try:
        callback(format_worker_completion_notice(record))
    except Exception:
        traceback.print_exc()

BACKGROUND_WORKER_SYSTEM = """

## Background Worker Protocol
- You are a background MO worker, not MO Desktop and not the foreground chat.
- Work only on the assigned objective; do not take unrelated tasks.
- Use tools for evidence before claiming completion.
- Keep changes minimal and coordination-safe; avoid broad refactors unless explicitly requested.
- If the assigned objective is review/audit/report-only, do not edit files.
- Do not commit, push, deploy, delete data, change credentials, or expose secrets.
- If active workers or workspace context suggest conflict, pause and report the conflict instead of overwriting.
- Final response must be compact and include: Result, Evidence, Files changed, Blocked/Next.
- Start Blocked/Next with "none" when unblocked, or state the blocking reason. Put optional next steps after a semicolon.
"""

WorkerFinishCallback = Callable[[WorkerRecord, str], None]
WorkerActivityCallback = Callable[[str], None]
WorkerActionCallback = Callable[[dict[str, Any]], None]


class BackgroundWorkerRuntime:
    """Run normal Agent workers asynchronously or inline for one-shot callers."""

    def __init__(self, agent: Any, *, max_workers: int = DEFAULT_PREFERENCES["agent.background_workers_max"]):
        self.agent = agent
        self.max_workers = max(1, int(max_workers or DEFAULT_PREFERENCES["agent.background_workers_max"]))
        self._lock = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}

    def _resolve_role_skill(self, role: str, *, project_cwd: str | None = None):
        """Resolve a role in the worker's exact project scope; fail closed if absent."""
        if not role:
            return None
        try:
            from ..skills import default_skill_roots, resolve_role
            agent = self.agent
            if project_cwd is None:
                project_reader = getattr(agent, "_effective_project_cwd", None)
                project_cwd = (
                    project_reader() if callable(project_reader)
                    else getattr(agent, "project_cwd", None)
                )
            roots = default_skill_roots(
                project_cwd,
                getattr(agent, "runtime_home", None),
                profile=getattr(agent, "profile", None),
                config=getattr(agent, "config", None),
            )
            return resolve_role(
                role,
                roots,
                profile=getattr(agent, "profile", None),
                project_cwd=project_cwd,
            )
        except Exception:
            traceback.print_exc()
            return None

    def active_count(self, *, exclude: str = "") -> int:
        registry = ensure_worker_registry(self.agent)
        return sum(1 for record in registry.active() if record.kind in {"worker", "prt"} and record.id != exclude)

    def wait_for(
        self,
        *,
        kinds: set[str] | None = None,
        timeout: float = 3.0,
        worker_ids: set[str] | list[str] | tuple[str, ...] | None = None,
    ) -> list[str]:
        """Join selected active workers briefly; return still-running ids."""
        import time
        deadline = time.time() + max(0.0, float(timeout or 0.0))
        kinds = kinds or {"prt"}
        selected_ids = None if worker_ids is None else {str(item) for item in worker_ids if str(item)}
        while True:
            with self._lock:
                items = list(self._threads.items())
            registry = ensure_worker_registry(self.agent)
            selected = [
                (wid, thread) for wid, thread in items
                if (selected_ids is None or wid in selected_ids)
                and registry.get(wid) and registry.get(wid).kind in kinds
            ]
            if not selected:
                return []
            remaining = deadline - time.time()
            if remaining <= 0:
                return [wid for wid, thread in selected if thread.is_alive()]
            _, thread = selected[0]
            thread.join(min(0.2, remaining))

    def start(
        self,
        objective: str,
        *,
        source: str = "user",
        worker_id: str | None = None,
        on_finish: WorkerFinishCallback | None = None,
        on_activity: WorkerActivityCallback | None = None,
        on_action: WorkerActionCallback | None = None,
        custom_target: Callable[[str, str, WorkerFinishCallback | None], None] | None = None,
        role: str = "",
        lane: str = "",
        project_cwd: str | None = None,
        allowed_roots: list[str] | None = None,
        notify_completion: bool = True,
        provider_surface: str = "worker",
        run_inline: bool = False,
        claimed_paths: list[str] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> WorkerRecord:
        objective = str(objective or "").strip()
        registry = ensure_worker_registry(self.agent)
        role = str(role or "").strip()
        role_skill = self._resolve_role_skill(role, project_cwd=project_cwd) if role else None
        if role and role_skill is None:
            # Fail loud: a requested role that does not resolve must not silently
            # run as a generic, ungoverned worker.
            return registry.create(
                kind="worker", source=source, route="background", objective=objective,
                state="blocked", role=role, note=f"role '{role}' not found", worker_id=worker_id,
            )
        with self._lock:
            claimed_paths = extract_worker_paths(objective) if claimed_paths is None else normalize_worker_paths(claimed_paths)
            record = registry.get(worker_id)
            if record and record.kind == "prt":
                claimed_paths = []
            elif record and not claimed_paths:
                claimed_paths = list(getattr(record, "claimed_paths", []) or [])
            if not record:
                record = registry.create(
                    kind="worker",
                    source=source,
                    route="background",
                    objective=objective,
                    state="offered",
                    note="background worker offered",
                    worker_id=worker_id,
                    claimed_paths=claimed_paths,
                    role=role,
                    project_root=str(getattr(role_skill, "project_root", "") or ""),
                )
            conflicts = registry.conflicts(claimed_paths, exclude=record.id)
            if conflicts and record.state != "running":
                path_note = ", ".join(conflict.id for conflict in conflicts[:3])
                registry.update(record.id, "blocked", f"workspace conflict with active worker {path_note}")
                return registry.get(record.id) or record
            active = self.active_count(exclude=record.id)
            if run_inline:
                # A nested inline worker uses its caller's execution slot.
                active -= sum(thread is threading.current_thread() for thread in self._threads.values())
            if active >= self.max_workers and record.state != "running":
                registry.update(record.id, "blocked", f"background worker limit reached ({self.max_workers})")
                return registry.get(record.id) or record
            registry.update(record.id, "accepted", "background worker accepted")
            registry.update(record.id, "running", "background worker running")
            target_func = custom_target if custom_target else self._run
            args = (record.id, objective, on_finish)
            if not custom_target:
                args = (
                    record.id,
                    objective,
                    on_finish,
                    on_activity,
                    on_action,
                    role_skill,
                    str(lane or "").strip(),
                    str(project_cwd or "").strip() or None,
                    list(allowed_roots) if allowed_roots is not None else None,
                    bool(notify_completion),
                    str(provider_surface or "worker").strip() or "worker",
                    cancel_event,
                )
            if not run_inline:
                request_limit = current_provider_request_limit()
                if request_limit is not None:
                    # Preserve the allowance, including an explicit unlimited
                    # override, without inheriting unrelated caller context.
                    def thread_target(*worker_args):
                        with provider_request_overrides({"_request_limit": request_limit}):
                            target_func(*worker_args)
                else:
                    thread_target = target_func
                thread = threading.Thread(
                    target=thread_target,
                    args=args,
                    daemon=True,
                    name=f"mo-worker-{record.id}",
                )
                self._threads[record.id] = thread
                thread.start()
        if run_inline:
            target_func(*args)
        return registry.get(record.id) or record

    def _run(
        self,
        worker_id: str,
        objective: str,
        on_finish: WorkerFinishCallback | None,
        on_activity: WorkerActivityCallback | None = None,
        on_action: WorkerActionCallback | None = None,
        role_skill=None,
        lane: str = "",
        project_cwd: str | None = None,
        allowed_roots: list[str] | None = None,
        notify_completion: bool = True,
        provider_surface: str = "worker",
        cancel_event: threading.Event | None = None,
    ) -> None:
        registry = ensure_worker_registry(self.agent)
        result = ""
        state = "completed"
        note = "background worker finished"
        try:
            if cancel_event is not None and cancel_event.is_set():
                return
            registry.update(worker_id, "running", "background worker turn started")
            prompt = build_background_worker_prompt(objective, role_skill=role_skill)
            base_system = str(getattr(self.agent, "system_message", "You are MO.") or "You are MO.")
            has_shared_role_scope = callable(getattr(self.agent, "provider_scope", None))
            overlay = (
                BACKGROUND_WORKER_SYSTEM
                if role_skill is None or has_shared_role_scope
                else _role_overlay(role_skill)
            )
            worker_session = Session(base_system + overlay)
            monitor = getattr(getattr(self.agent, "gateway", None), "monitor", None)
            lane_scope = (
                self.agent.lane_scope(lane)
                if lane and callable(getattr(self.agent, "lane_scope", None))
                else nullcontext()
            )
            workspace_scope = (
                self.agent.workspace_scope(project_cwd=project_cwd, allowed_roots=allowed_roots)
                if (project_cwd is not None or allowed_roots is not None)
                and callable(getattr(self.agent, "workspace_scope", None))
                else nullcontext()
            )
            with workspace_scope:
                with lane_scope:
                    turn_options = {"on_activity": on_activity, "on_action": on_action}
                    if cancel_event is not None:
                        turn_options["cancel_event"] = cancel_event
                    if hasattr(self.agent, "isolated_session"):
                        with self.agent.isolated_session(worker_session):
                            if hasattr(self.agent, "provider_scope"):
                                with self.agent.provider_scope(provider_surface, worker_id=worker_id, role=role_skill):
                                    result = self.agent.run_turn(prompt, monitor=monitor, **turn_options)
                            else:
                                result = self.agent.run_turn(prompt, monitor=monitor, **turn_options)
                    elif hasattr(self.agent, "provider_scope"):
                        with self.agent.provider_scope(provider_surface, worker_id=worker_id, role=role_skill):
                            result = self.agent.run_turn(prompt, **turn_options)
                    else:
                        result = self.agent.run_turn(prompt, **turn_options)
            if assistant_result_is_incomplete(result):
                state = "blocked"
                note = "background worker blocked"
            elif worker_result_indicates_blocked(result):
                state = "blocked"
                note = "background worker reported blocked next step"
        except Exception as exc:
            detail = clean_provider_error(str(exc))
            result = "\n".join([
                "MO worker error: background turn failed",
                "  where: background worker runtime",
                "Fix: retry the worker or run the task in the foreground if this repeats.",
                f"  detail: {detail}",
            ])
            state = "blocked"
            note = "background worker error"
        finally:
            if cancel_event is not None and cancel_event.is_set():
                state, note = "cancelled", "background worker stopped by request"
                result = "Result: Stopped by request. Saved work was retained."
            result_summary, evidence = summarize_worker_result(result)
            record = registry.update(
                worker_id,
                state,
                note,
                result_summary=result_summary,
                evidence=evidence,
                result_report=result,
            )
            _record_role_outcome(role_skill, state)
            if record and on_finish:
                try:
                    on_finish(record, result)
                except Exception as e:
                    try:
                        from ..runtime.backend_monitor import get_monitor
                        monitor = get_monitor()
                        if monitor:
                            monitor.emit("worker_on_finish_error", {"worker_id": worker_id, "error": str(e)[:200]})
                    except Exception:
                        traceback.print_exc()
            if notify_completion:
                notify_native_async(self.agent, record)
            with self._lock:
                self._threads.pop(worker_id, None)


def summarize_worker_result(result: str) -> tuple[str, list[str]]:
    """Extract a compact result card from a worker final answer."""
    lines = [line.strip().strip("-• ") for line in str(result or "").splitlines() if line.strip()]
    summary = ""
    evidence: list[str] = []
    for line in lines:
        lower = line.lower()
        if lower.startswith("result:") and not summary:
            summary = line.split(":", 1)[1].strip()
        elif lower.startswith("evidence:"):
            value = line.split(":", 1)[1].strip()
            if value:
                evidence.extend(_split_evidence(value))
        elif lower.startswith(("files changed:", "blocked/next:")):
            value = line.split(":", 1)[1].strip()
            if value and value.lower() not in {"none", "n/a"}:
                evidence.append(line)
    if not summary and lines:
        summary = lines[0]
    return summary[:500], evidence[:12]


def worker_result_indicates_blocked(result: str) -> bool:
    """True when the worker's required Blocked/Next field names a real blocker."""
    for raw in str(result or "").splitlines():
        line = raw.strip().strip("-• ")
        lower = line.lower()
        if not lower.startswith("blocked/next:"):
            continue
        value = line.split(":", 1)[1].strip().lower()
        # This field contains both status and next steps. Classify its status
        # clause, not the length of the optional follow-up explanation.
        status = value.split(";", 1)[0].split(".", 1)[0].strip()
        return status not in {"", "none", "n/a", "na", "no", "not blocked", "nothing", "no blocker", "no blockers", "nothing blocked"}
    return False


def _split_evidence(value: str) -> list[str]:
    parts = []
    for chunk in value.replace(";", ",").split(","):
        item = chunk.strip()
        if item:
            parts.append(item[:240])
    return parts or ([value[:240]] if value else [])


# Verbs that mean "make a change" — including ones the template classifier treats as
# review ("patch") or doesn't classify ("harden"/"refactor"). A review objective that
# ALSO asks for a change must not have its edit capability stripped.
_CHANGE_INTENT_WORDS = frozenset({
    "fix", "patch", "harden", "repair", "secure", "refactor", "rewrite", "implement",
    "build", "add", "update", "modify", "improve", "optimize", "optimise", "migrate",
    "remove", "replace",
})


def build_background_worker_prompt(objective: str, role_skill=None) -> str:
    objective_text = str(objective or "").strip()
    # Only a PURE review forbids edits. "audit X and harden it" / "investigate the
    # crash and patch it" carry change intent, so the worker keeps edit capability;
    # the sandbox still gates actual writes.
    review_only = (
        select_template(objective_text) == "deep_review"
        and not (words(objective_text) & _CHANGE_INTENT_WORDS)
    )
    review_guard = "Review only: report findings, no edits. " if review_only else ""
    complexity = estimate_work_complexity(objective_text)
    role_banner = ""
    if role_skill is not None:
        label = getattr(role_skill, "role", "") or getattr(role_skill, "name", "") or "role"
        role_banner = f"Role: {label} — obey the active role contract in your instructions.\n"
    return (
        "[BACKGROUND WORKER]\n"
        f"{role_banner}"
        f"Objective: {objective_text}\n"
        f"Complexity: {complexity}\n\n"
        f"{review_guard}"
        "Coordinate with active worker state from context. "
        "Use tools for evidence. Stop after this objective is completed or clearly blocked. "
        "Do not ask the operator unless blocked by missing approval or unsafe action."
    )


def _role_overlay(role_skill) -> str:
    """System overlay that pins a role skill as the worker's operating contract."""
    try:
        from ..skills import role_overlay_text
        return role_overlay_text(role_skill)
    except Exception:
        return BACKGROUND_WORKER_SYSTEM


def _record_role_outcome(role_skill, state: str) -> None:
    """Feed the role worker's outcome back to its skill mastery, so 'sticking'
    becomes measurable (success on completion, correction on block/error)."""
    if role_skill is None:
        return
    source = str(getattr(role_skill, "source", "") or "")
    if not source:
        return
    try:
        from ..skills import record_skill_outcome
        record_skill_outcome(source, "success" if state == "completed" else "correction")
    except Exception:
        pass


def ensure_worker_runtime(agent: Any) -> BackgroundWorkerRuntime:
    runtime = getattr(agent, "worker_runtime", None)
    if isinstance(runtime, BackgroundWorkerRuntime):
        return runtime
    max_workers = DEFAULT_PREFERENCES["agent.background_workers_max"]
    try:
        max_workers = int(getattr(agent, "config", {}).get("agent", {}).get("background_workers_max", DEFAULT_PREFERENCES["agent.background_workers_max"]) or DEFAULT_PREFERENCES["agent.background_workers_max"])
    except Exception:
        max_workers = DEFAULT_PREFERENCES["agent.background_workers_max"]
    runtime = BackgroundWorkerRuntime(agent, max_workers=max_workers)
    try:
        setattr(agent, "worker_runtime", runtime)
    except Exception:
        traceback.print_exc()
    return runtime
