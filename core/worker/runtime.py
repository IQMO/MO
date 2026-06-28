"""Generic background MO worker runtime.

Runs independent background worker turns in isolated sessions while the worker
registry owns visible lifecycle truth.
"""
from __future__ import annotations

import threading
from typing import Any, Callable
import traceback

from ..context.gateway_helpers import select_template, words
from ..provider.provider import clean_provider_error
from ..context.work_patterns import estimate_work_complexity
from ..session.session import Session
from .registry import WorkerRecord, ensure_worker_registry, extract_worker_paths


def format_worker_completion_notice(record: WorkerRecord) -> str:
    """Return a compact user-facing completion notice for native terminals."""
    kind = str(getattr(record, "kind", "worker") or "worker").lower()
    state = str(getattr(record, "state", "") or "").lower()
    summary = str(getattr(record, "result_summary", "") or getattr(record, "note", "") or getattr(record, "objective", "") or "finished").strip()
    summary = " ".join(summary.split())[:180]
    label = "PRT" if kind == "prt" else "Worker"
    if state == "completed":
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
- You are a background MO worker, not Ghost and not the foreground chat.
- Work only on the assigned objective; do not take unrelated tasks.
- Use tools for evidence before claiming completion.
- Keep changes minimal and coordination-safe; avoid broad refactors unless explicitly requested.
- If the assigned objective is review/audit/report-only, do not edit files.
- Do not commit, push, deploy, delete data, change credentials, or expose secrets.
- If active workers or workspace context suggest conflict, pause and report the conflict instead of overwriting.
- Final response must be compact and include: Result, Evidence, Files changed, Blocked/Next.
"""

WorkerFinishCallback = Callable[[WorkerRecord, str], None]


class BackgroundWorkerRuntime:
    """Tiny thread-backed runner for independent background MO work."""

    def __init__(self, agent: Any, *, max_workers: int = 3):
        self.agent = agent
        self.max_workers = max(1, int(max_workers or 3))
        self._lock = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}

    def _resolve_role_skill(self, role: str):
        """Resolve a role name to its governing skill via the agent's skill roots.
        Returns None when the role is unknown (caller fails loud)."""
        if not role:
            return None
        try:
            from ..skills import default_skill_roots, resolve_role
            agent = self.agent
            roots = default_skill_roots(
                getattr(agent, "project_cwd", None),
                getattr(agent, "runtime_home", None),
                profile=getattr(agent, "profile", None),
                config=getattr(agent, "config", None),
            )
            return resolve_role(role, roots, profile=getattr(agent, "profile", None))
        except Exception:
            traceback.print_exc()
            return None

    def active_count(self, *, exclude: str = "") -> int:
        registry = ensure_worker_registry(self.agent)
        return sum(1 for record in registry.active() if record.kind in {"worker", "prt"} and record.id != exclude)

    def wait_for(self, *, kinds: set[str] | None = None, timeout: float = 3.0) -> list[str]:
        """Join active background threads briefly at shutdown; returns still-running ids."""
        import time
        deadline = time.time() + max(0.0, float(timeout or 0.0))
        kinds = kinds or {"prt"}
        while True:
            with self._lock:
                items = list(self._threads.items())
            registry = ensure_worker_registry(self.agent)
            selected = [(wid, thread) for wid, thread in items if (registry.get(wid) and registry.get(wid).kind in kinds)]
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
        source: str = "ghost",
        worker_id: str | None = None,
        on_finish: WorkerFinishCallback | None = None,
        custom_target: Callable[[str, str, WorkerFinishCallback | None], None] | None = None,
        role: str = "",
    ) -> WorkerRecord:
        objective = str(objective or "").strip()
        registry = ensure_worker_registry(self.agent)
        role = str(role or "").strip()
        role_skill = self._resolve_role_skill(role) if role else None
        if role and role_skill is None:
            # Fail loud: a requested role that does not resolve must not silently
            # run as a generic, ungoverned worker.
            return registry.create(
                kind="worker", source=source, route="background", objective=objective,
                state="blocked", role=role, note=f"role '{role}' not found", worker_id=worker_id,
            )
        claimed_paths = extract_worker_paths(objective)
        record = registry.get(worker_id)
        if record and not claimed_paths:
            claimed_paths = list(getattr(record, "claimed_paths", []) or [])
        if not record:
            record = registry.create(kind="worker", source=source, route="background", objective=objective, state="offered", note="background worker offered", worker_id=worker_id, claimed_paths=claimed_paths, role=role)

        with self._lock:
            conflicts = registry.conflicts(claimed_paths, exclude=record.id)
            if conflicts and record.state != "running":
                path_note = ", ".join(conflict.id for conflict in conflicts[:3])
                registry.update(record.id, "blocked", f"workspace conflict with active worker {path_note}")
                return registry.get(record.id) or record
            if self.active_count(exclude=record.id) >= self.max_workers and record.state != "running":
                registry.update(record.id, "blocked", f"background worker limit reached ({self.max_workers})")
                return registry.get(record.id) or record
            registry.update(record.id, "accepted", "background worker accepted")
            registry.update(record.id, "running", "background worker running")
            target_func = custom_target if custom_target else self._run
            args = (record.id, objective, on_finish)
            if not custom_target:
                args = (record.id, objective, on_finish, role_skill)
            thread = threading.Thread(
                target=target_func,
                args=args,
                daemon=True,
                name=f"mo-worker-{record.id}",
            )
            self._threads[record.id] = thread
            thread.start()
            return registry.get(record.id) or record

    def _run(self, worker_id: str, objective: str, on_finish: WorkerFinishCallback | None, role_skill=None) -> None:
        registry = ensure_worker_registry(self.agent)
        result = ""
        state = "completed"
        note = "background worker finished"
        try:
            registry.update(worker_id, "running", "background worker turn started")
            prompt = build_background_worker_prompt(objective, role_skill=role_skill)
            base_system = str(getattr(self.agent, "system_message", "You are MO.") or "You are MO.")
            overlay = _role_overlay(role_skill) if role_skill else BACKGROUND_WORKER_SYSTEM
            worker_session = Session(base_system + overlay)
            monitor = getattr(getattr(self.agent, "gateway", None), "monitor", None)
            if hasattr(self.agent, "isolated_session"):
                with self.agent.isolated_session(worker_session):
                    if hasattr(self.agent, "provider_scope"):
                        with self.agent.provider_scope("worker", worker_id=worker_id, role=role_skill):
                            result = self.agent.run_turn(prompt, monitor=monitor)
                    else:
                        result = self.agent.run_turn(prompt, monitor=monitor)
            elif hasattr(self.agent, "provider_scope"):
                with self.agent.provider_scope("worker", worker_id=worker_id, role=role_skill):
                    result = self.agent.run_turn(prompt)
            else:
                result = self.agent.run_turn(prompt)
            if str(result or "").startswith(("Error:", "MO provider error:", "[MAX")):
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
            result_summary, evidence = summarize_worker_result(result)
            record = registry.update(worker_id, state, note, result_summary=result_summary, evidence=evidence)
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
        if not value or value in {"none", "n/a", "na", "no", "not blocked", "nothing"}:
            return False
        if any(marker in value for marker in ("not blocked", "no blocker", "nothing blocked", "none")) and len(value.split()) <= 4:
            return False
        return True
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
    max_workers = 3
    try:
        max_workers = int(getattr(agent, "config", {}).get("agent", {}).get("background_workers_max", 3) or 3)
    except Exception:
        max_workers = 3
    runtime = BackgroundWorkerRuntime(agent, max_workers=max_workers)
    try:
        setattr(agent, "worker_runtime", runtime)
    except Exception:
        traceback.print_exc()
    return runtime
