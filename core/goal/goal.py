"""MO — Goal runner: autonomous multi-iteration work with auditor gate.

/goal <task> runs sequential iterations of Agent.run_turn() against a flat plan.
Ctrl+G toggles background/foreground. The GoalAuditor enforces profile-driven
quality before marking completion.

No subagents, no GOAP, no PlanStore. Just a loop around existing primitives:
run_turn + TaskBoard-shaped display + sandbox + monitor. Goal state is mirrored
in the lightweight worker registry for UI/status routing only.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import traceback

from ..utils.atomic_write import atomic_write_json
from ..gates.consistency_boundary import check_consistency_boundary, emit_consistency_boundary
from ..context.coordination_state import active_conflicts_for_text
from ..utils.env_utils import int_env
from ..tasking.task_board import TaskBoard, TaskItem, record_snapshot
from ..tasking import task_evidence
from .goal_auditor import GoalAuditor
from ..state.paths import resolve_state_path
from ..session.session import (
    assistant_result_is_incomplete,
    assistant_result_is_provider_or_runtime_error,
    assistant_result_is_request_limit,
)
from ..utils.text_utils import format_blocker_reason, words as _words
from ..worker import ensure_worker_registry



# ── Data structures ───────────────────────────────────────────────

GOAL_MAX_WALL_SECONDS = 4 * 60 * 60
GOAL_MAX_CONSECUTIVE_INCOMPLETE_TURNS = 3
# Final-audit repairs of one row before the goal pauses with the auditor's reason.
_MAX_COMPLETION_REPAIRS = 3
GOAL_BLOCKER_KINDS = {
    "none",
    "missing_evidence",
    "needs_user_input",
    "run_failed",
    "external_wait",
    "goal_not_met_yet",
    "provider_error",
    "workspace_conflict",
    "stopped_by_user",
}


def _normalize_goal_blocker_kind(kind: str) -> str:
    value = str(kind or "").strip().lower().replace("-", "_").replace(" ", "_")
    return value if value in GOAL_BLOCKER_KINDS else ""


def _goal_blocker_kind(reason: str, *, state: str = "", feedback: str = "") -> str:
    if assistant_result_is_request_limit(reason):
        return "needs_user_input"
    state_text = str(state or "").strip().lower()
    if state_text == "completed":
        return "none"
    text = f"{state_text} {reason or ''} {feedback or ''}".lower()
    if "stopped by user" in text:
        return "stopped_by_user"
    if "workspace conflict" in text:
        return "workspace_conflict"
    if "provider" in text or "runtime error" in text or "unavailable" in text:
        return "provider_error"
    if "operator" in text or "approval" in text or "needs input" in text or "user input" in text:
        return "needs_user_input"
    if any(token in text for token in ("evidence", "verification", "test", "failing", "auditor requires")):
        return "missing_evidence"
    if any(token in text for token in ("time budget", "external", "wait", "paused before")):
        return "external_wait"
    if any(token in text for token in ("not complete", "no open steps", "stale goal", "re-plan", "approach")):
        return "goal_not_met_yet"
    if state_text in {"blocked", "paused"}:
        return "goal_not_met_yet"
    return "run_failed"


def _format_goal_blocker(kind: str, reason: str) -> str:
    return format_blocker_reason(_normalize_goal_blocker_kind(kind), reason)


def _agent_tool_audit_path(agent: Any) -> Path | None:
    sandbox_cfg = getattr(agent, "sandbox_config", {}) if isinstance(getattr(agent, "sandbox_config", None), dict) else {}
    audit_path = sandbox_cfg.get("audit_log")
    if audit_path:
        return Path(audit_path)
    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    runtime_cfg = cfg.get("runtime", {}) if isinstance(cfg.get("runtime", {}), dict) else {}
    if os.environ.get("PYTEST_CURRENT_TEST") and not runtime_cfg.get("home"):
        return None
    resolved = resolve_state_path("logs/tool_audit.jsonl", cfg)
    return Path(resolved) if resolved else None


def _prune_goal_runs(root: Path) -> None:
    keep = int_env("MO_GOAL_RUNS_KEEP", 50)
    if keep <= 0:
        return
    try:
        files = sorted(root.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in files[keep:]:
            path.unlink(missing_ok=True)
    except Exception:
        return


@dataclass
class GoalBudget:
    max_wall_seconds: float = float(GOAL_MAX_WALL_SECONDS)

    def __post_init__(self) -> None:
        try:
            seconds = float(self.max_wall_seconds)
        except (TypeError, ValueError):
            seconds = float(GOAL_MAX_WALL_SECONDS)
        self.max_wall_seconds = min(max(1.0, seconds), float(GOAL_MAX_WALL_SECONDS))

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GoalStep:
    id: str
    title: str
    status: str = "pending"
    evidence: list[str] = field(default_factory=list)
    blocker: str = ""
    reopened_count: int = 0
    iterations_run: int = 0
    blocker_kind: str = ""
    kind: str = ""
    completion_gate: str = ""
    depends_on: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    expected_evidence: list[str] = field(default_factory=list)
    test_strategy: str = ""

    @property
    def is_open(self) -> bool:
        return self.status in {"pending", "active"}

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GoalPlan:
    objective: str
    steps: list[GoalStep]
    run_id: str = ""
    started_at: float = 0.0
    finished_at: float | None = None
    budget: GoalBudget = field(default_factory=GoalBudget)
    state: str = "running"  # running | completed | blocked | paused
    stop_reason: str = ""
    iterations_run: int = 0
    auditor_feedback: str = ""
    consecutive_provider_errors: int = 0
    consecutive_incomplete_turns: int = 0
    context_savings_start_chars: int = 0
    context_savings_start_ops: int = 0
    context_savings_chars: int = 0
    context_savings_ops: int = 0
    replans_run: int = 0
    last_replan_reason: str = ""
    stop_kind: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    verification_criteria: list[str] = field(default_factory=list)

    def completed_count(self) -> int:
        return sum(1 for s in self.steps if s.status == "completed")

    def open_count(self) -> int:
        return sum(1 for s in self.steps if s.is_open)

    def next_open_step(self) -> GoalStep | None:
        for s in self.steps:
            if s.status in {"pending", "active"}:
                return s
        return None

    def all_done(self) -> bool:
        return all(s.status == "completed" for s in self.steps)

    def as_dict(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "state": self.state,
            "stop_reason": self.stop_reason,
            "iterations_run": self.iterations_run,
            "budget": self.budget.as_dict(),
            "steps": [s.as_dict() for s in self.steps],
            "auditor_feedback": self.auditor_feedback,
            "consecutive_provider_errors": self.consecutive_provider_errors,
            "consecutive_incomplete_turns": self.consecutive_incomplete_turns,
            "context_savings_start_chars": self.context_savings_start_chars,
            "context_savings_start_ops": self.context_savings_start_ops,
            "context_savings_chars": self.context_savings_chars,
            "context_savings_ops": self.context_savings_ops,
            "replans_run": self.replans_run,
            "last_replan_reason": self.last_replan_reason,
            "stop_kind": self.stop_kind,
            "acceptance_criteria": list(self.acceptance_criteria),
            "verification_criteria": list(self.verification_criteria),
        }


# ── Plan decomposition (deterministic, no provider call) ─────────


def _step_title_is_broad_repair(title: str) -> bool:
    text = str(title or "").lower()
    return "fix confirmed" in text or "broken/incomplete" in text


def _evidence_is_support_artifact_write(item: str) -> bool:
    text = str(item or "").lower().replace("\\", "/")
    if not text.startswith(("write_file:", "edit_file:")):
        return False
    path = text.split(":", 1)[1].split(" ", 1)[0].rsplit("/", 1)[-1]
    return (
        path.startswith("_check")
        or path.startswith("_verify")
        or path.startswith("_dbg")
        or path.startswith("check_")
        or "check_compile" in path
        or "verify_menu" in path
    )


def _has_scoped_write_evidence(items: list[str]) -> bool:
    return any(
        str(item).startswith(("write_file:", "edit_file:"))
        and "[failed]" not in str(item).lower()
        and not _evidence_is_support_artifact_write(str(item))
        for item in items
    )


def decompose_goal(objective: str) -> list[GoalStep]:
    """Seed the plan with the operator's objective verbatim — by design, one step.

    Decomposition is intentionally NOT done here (an earlier regex "title
    stamping" approach was removed). The real multi-step work happens at run
    time, where it can be evidence-driven instead of guessed:
      - the agent's own taskboard breaks the objective into tracked tasks while
        it executes (`GoalRunner` iterates `run_turn` against this step), and
      - `GoalAuditor` gates each iteration; on rejection `_replan_via_no_tools`
        supplies a revised approach.
    Keeping this seed verbatim avoids inventing fake structure the operator did
    not ask for.
    """
    return [GoalStep("1", str(objective or "").strip()[:200])]


def _criterion_id(value: object) -> str:
    match = re.match(r"\s*(AC\d+)\b", str(value or ""), flags=re.I)
    return match.group(1).upper() if match else ""


def _goal_acceptance_criteria(objective: str) -> list[str]:
    """Preserve explicit numbered acceptance sections as stable goal AC ids."""
    text = " ".join(str(objective or "").split())
    if not text:
        return []
    lowered = text.casefold()
    start = -1
    for marker in ("required outcomes:", "acceptance criteria:", "required results:"):
        marker_at = lowered.find(marker)
        if marker_at >= 0 and (start < 0 or marker_at < start):
            start = marker_at + len(marker)
    scoped = text[start:] if start >= 0 else text
    numbered = list(re.finditer(r"(?:^|\s)([1-9]\d?)\.\s+", scoped))
    selected: list[re.Match[str]] = []
    expected = 1
    for match in numbered:
        number = int(match.group(1))
        if number == expected:
            selected.append(match)
            expected += 1
        elif selected and number > expected:
            break
    if len(selected) >= 2:
        criteria: list[str] = []
        for index, match in enumerate(selected):
            end = selected[index + 1].start() if index + 1 < len(selected) else len(scoped)
            section = " ".join(scoped[match.end():end].split())
            preview = section[:320].rstrip(" ,;:-")
            criteria.append(f"AC{index + 1}: {preview or f'Outcome {index + 1}'}")
        return criteria
    return [f"AC1: {text[:500].rstrip()}"]


def _goal_verification_criteria(objective: str, criteria: list[str]) -> list[str]:
    """Return AC ids that need an explicit verification row before completion."""
    try:
        from ..context.work_patterns import select_work_pattern

        pattern = select_work_pattern(objective)
        require_all = bool(pattern and getattr(pattern, "requires_verification", False))
    except Exception:
        require_all = False
    result: list[str] = []
    for criterion in criteria:
        criterion_id = _criterion_id(criterion)
        text = str(criterion or "").casefold()
        if criterion_id and (
            require_all
            or any(word in text for word in ("verify", "prove", "test", "acceptance", "installed candidate"))
        ):
            result.append(criterion_id)
    return result


# ── Goal Runner ───────────────────────────────────────────────────

class GoalRunner:
    """Autonomous mission runner: iterates Agent.run_turn() against a flat plan."""

    def __init__(self, agent: Any):
        self.agent = agent
        self._cancel_event = threading.Event()
        self.on_board_event: object = None

    def _agent_context_savings_chars(self) -> int:
        if callable(getattr(self.agent, "_tool_context_saved_chars", None)):
            return max(0, int(self.agent._tool_context_saved_chars() or 0))
        return max(0, int(getattr(self.agent, "result_cap_total_saved", 0) or 0))

    def _agent_context_savings_ops(self) -> int:
        if callable(getattr(self.agent, "_tool_context_saving_ops", None)):
            return max(0, int(self.agent._tool_context_saving_ops() or 0))
        return max(0, int(getattr(self.agent, "result_cap_total_ops", 0) or 0))

    def start(self, objective: str, *, budget: GoalBudget | None = None) -> str:
        """Start a new goal. Returns initial progress line."""
        objective = str(objective or "").strip()
        if not objective:
            return "Usage: /goal <task>"

        self._cancel_event = threading.Event()

        budget = budget or GoalBudget()
        run_id = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        steps = decompose_goal(objective)
        acceptance_criteria = _goal_acceptance_criteria(objective)

        plan = GoalPlan(
            objective=objective,
            steps=steps,
            run_id=run_id,
            started_at=time.time(),
            budget=budget,
            context_savings_start_chars=self._agent_context_savings_chars(),
            context_savings_start_ops=self._agent_context_savings_ops(),
            acceptance_criteria=acceptance_criteria,
            verification_criteria=_goal_verification_criteria(objective, acceptance_criteria),
        )
        # Activate first step
        if plan.steps:
            plan.steps[0].status = "active"

        self.agent._goal_plan = plan
        self.agent._goal_active = True
        self.agent._goal_session = self._new_goal_session()
        self.agent._goal_task_board = self._new_goal_task_board(plan)
        worker_id = getattr(self.agent, "_goal_worker_id", "") or ""
        registry = ensure_worker_registry(self.agent)
        existing_worker = registry.get(worker_id) if worker_id else None
        if (
            existing_worker is not None
            and existing_worker.active
            and str(existing_worker.objective or "").strip() == objective
        ):
            registry.update(worker_id, "running", "goal plan accepted")
        else:
            record = registry.create(kind="goal", source="user", route="background", objective=objective, state="running", note="goal plan accepted", worker_id=plan.run_id)
            self.agent._goal_worker_id = record.id

        # Run first iteration
        return self._run_iteration()

    def continue_goal(self) -> str:
        """Run the next iteration of an active goal."""
        plan = getattr(self.agent, "_goal_plan", None)
        if not plan or not getattr(self.agent, "_goal_active", False):
            return "No active goal. Use /goal <task> to start one."
        if plan.state != "running":
            return f"Goal is {plan.state}: {_format_goal_blocker(getattr(plan, 'stop_kind', ''), plan.stop_reason)}"
        if self._cancel_event.is_set():
            self._cancel_event = threading.Event()
        plan.finished_at = None
        board = getattr(self.agent, "_goal_task_board", None)
        if board is not None and str(getattr(board, "state", "") or "") == "paused":
            board.state = "active"
            board.updated_at = time.time()
            record_snapshot(board, "goal_resumed", source="goal", state="active")
        return self._run_iteration()

    def stop(self) -> str:
        """Stop the active goal."""
        plan = getattr(self.agent, "_goal_plan", None)
        if not plan:
            return "No active goal."
        self._cancel_event.set()
        plan.state = "paused"
        plan.stop_reason = "stopped by user"
        plan.stop_kind = "stopped_by_user"
        plan.finished_at = plan.finished_at or time.time()
        self.agent._goal_active = False
        ensure_worker_registry(self.agent).update(getattr(self.agent, "_goal_worker_id", ""), "paused", "stopped by user")
        board = getattr(self.agent, "_goal_task_board", None)
        if board is not None:
            board.state = "paused"
            board.updated_at = time.time()
            record_snapshot(board, "goal_paused", source="goal", state="paused")
        self._persist(plan)
        monitor = getattr(self.agent, "_goal_monitor", None) or getattr(getattr(self.agent, "gateway", None), "monitor", None)
        if monitor:
            monitor.emit("goal_finish", {
                "run_id": plan.run_id,
                "state": "paused",
                "reason": "stopped by user",
                "completed": plan.completed_count(),
                "total": len(plan.steps),
            })
        elapsed = self._elapsed_text(plan)
        return f"[GOAL STOPPED] {plan.completed_count()}/{len(plan.steps)} done · {elapsed}\nGoal paused. Use /goal resume to continue."

    def status(self) -> str:
        """Return compact status of the active goal."""
        plan = getattr(self.agent, "_goal_plan", None)
        if not plan:
            return "No active goal."
        return self._format_progress(plan)

    def _run_iteration(self) -> str:
        """Run one iteration of the goal. Returns progress or final result."""
        plan: GoalPlan = self.agent._goal_plan

        # Budget checks
        elapsed = time.time() - plan.started_at
        if elapsed > plan.budget.max_wall_seconds:
            return self._finish(plan, "paused", f"time budget reached ({elapsed:.0f}s)")

        # Check if all done
        if plan.all_done():
            return self._try_complete(plan)

        # Get next step
        step = plan.next_open_step()
        if not step:
            return self._finish(plan, "blocked", "no open steps but plan not complete")

        # Mark active
        step.status = "active"
        conflict_text = f"{getattr(plan, 'objective', '')}\n{getattr(step, 'title', '')}"
        _claimed_paths, conflicts = active_conflicts_for_text(
            self.agent,
            conflict_text,
            exclude=str(getattr(self.agent, "_goal_worker_id", "") or getattr(plan, "run_id", "") or ""),
        )
        if conflicts:
            conflict_ids = ", ".join(getattr(record, "id", "") for record in conflicts[:3])
            return self._finish(plan, "paused", f"workspace conflict with active worker {conflict_ids}; goal paused before step execution")
        plan.iterations_run += 1
        step.iterations_run = max(0, int(getattr(step, "iterations_run", 0) or 0)) + 1
        task_board = getattr(self.agent, "_goal_task_board", None)
        if task_board is None:
            task_board = self.to_task_board(plan)
            self.agent._goal_task_board = task_board
        completed_before = {
            str(task.id)
            for task in list(getattr(task_board, "tasks", []) or [])
            if str(getattr(task, "status", "") or "") == "completed"
        }

        # Build turn prompt
        prompt = self._build_turn_prompt(plan, step)

        # Emit monitor event
        monitor = getattr(self.agent, "_goal_monitor", None) or getattr(getattr(self.agent, "gateway", None), "monitor", None)
        if monitor:
            monitor.emit("backend_status", {"message": f"goal iteration {plan.iterations_run}: working '{step.title}'"})
            monitor.emit("goal_step", {
                "run_id": plan.run_id,
                "iteration": plan.iterations_run,
                "step_id": step.id,
                "title": step.title,
                "status": step.status,
            })

        # Timestamp used to collect only this turn's tool-audit evidence below.
        evidence_since = time.time()

        # Run the turn in an isolated goal session so foreground chat stays clean.
        try:
            goal_session = getattr(self.agent, "_goal_session", None) or self._new_goal_session()
            self.agent._goal_session = goal_session
            worker_id = getattr(self.agent, "_goal_worker_id", "") or plan.run_id
            if hasattr(self.agent, "isolated_session"):
                with self.agent.isolated_session(goal_session):
                    if hasattr(self.agent, "provider_scope"):
                        with self.agent.provider_scope("goal", worker_id=worker_id):
                            result = self._run_agent_turn(prompt, monitor, task_board=task_board)
                    else:
                        result = self._run_agent_turn(prompt, monitor, task_board=task_board)
            elif hasattr(self.agent, "provider_scope"):
                with self.agent.provider_scope("goal", worker_id=worker_id):
                    result = self._run_agent_turn(prompt, monitor, task_board=task_board)
            else:
                result = self._run_agent_turn(prompt, monitor, task_board=task_board)
        except Exception as exc:
            if plan.state != "running" or not getattr(self.agent, "_goal_active", False):
                return (
                    f"[GOAL STOPPED] {plan.completed_count()}/{len(plan.steps)} done · "
                    f"{self._elapsed_text(plan)}\nGoal paused. Use /goal resume to continue."
                )
            return self._handle_provider_failure(plan, step, f"runtime error: {type(exc).__name__}: {str(exc)[:200]}")

        if plan.state != "running" or not getattr(self.agent, "_goal_active", False):
            return (
                f"[GOAL STOPPED] {plan.completed_count()}/{len(plan.steps)} done · "
                f"{self._elapsed_text(plan)}\nGoal paused. Use /goal resume to continue."
            )

        if assistant_result_is_request_limit(result):
            if getattr(task_board, "tasks", None):
                self._sync_plan_from_task_board(plan, task_board)
            return self._finish(plan, "paused", str(result).strip().splitlines()[0])
        if assistant_result_is_provider_or_runtime_error(result):
            return self._handle_provider_failure(plan, step, "provider error; retry same goal step")
        if assistant_result_is_incomplete(result):
            if getattr(task_board, "tasks", None):
                self._sync_plan_from_task_board(plan, task_board)
            first_line = str(result or "").strip().splitlines()[0][:180]
            plan.consecutive_incomplete_turns = max(
                0,
                int(getattr(plan, "consecutive_incomplete_turns", 0) or 0),
            ) + 1
            active_step = plan.next_open_step() or step
            feedback = (
                "The previous goal turn ended incomplete and did not complete this row: "
                f"{first_line}. Continue from the same live taskboard, do not repeat the "
                "failed response or tool pattern, and choose the next smallest evidence-producing action."
            )
            if plan.consecutive_incomplete_turns == 2:
                revised = self._replan_via_no_tools(plan, active_step, feedback)
                plan.replans_run = max(0, int(getattr(plan, "replans_run", 0) or 0)) + 1
                plan.last_replan_reason = feedback
                feedback = f"{feedback} Revised approach: {revised}"
            active_step.status = "active"
            active_step.blocker = feedback
            active_step.blocker_kind = "missing_evidence"
            plan.auditor_feedback = feedback
            if plan.consecutive_incomplete_turns >= GOAL_MAX_CONSECUTIVE_INCOMPLETE_TURNS:
                return self._finish(
                    plan,
                    "paused",
                    "goal paused after 3 consecutive incomplete turns without evidence-producing progress",
                )
            return self._check_progress_or_stop(plan)

        plan.consecutive_provider_errors = 0
        plan.consecutive_incomplete_turns = 0

        if not getattr(task_board, "tasks", None):
            feedback = (
                "Materialize the acceptance-aware goal plan with set_plan before "
                "using work tools; every required AC id must be covered."
            )
            step.status = "active"
            step.blocker = feedback
            step.blocker_kind = "missing_evidence"
            plan.auditor_feedback = feedback
            if plan.iterations_run >= 3:
                return self._finish(
                    plan,
                    "paused",
                    "goal taskboard was not materialized after 3 iterations; no work was claimed complete",
                )
            return self._check_progress_or_stop(plan)

        # Collect this turn's tool-audit evidence into the live task board so the
        # acceptance gate and staleness check see real tool work, not just prose.
        self._append_goal_tool_evidence(plan, task_board, since_ts=evidence_since)

        self._sync_plan_from_task_board(plan, task_board)
        plan.auditor_feedback = ""
        completed_now = {
            step.id for step in plan.steps if step.status == "completed"
        }
        auditor = GoalAuditor(self.agent.profile)
        for completed_id in sorted(completed_now - completed_before):
            completed_step = next(
                (candidate for candidate in plan.steps if candidate.id == completed_id),
                None,
            )
            if completed_step is None:
                continue
            verdict = auditor.review_iteration(completed_step, result)
            if monitor:
                monitor.emit("goal_auditor", {
                    "run_id": plan.run_id,
                    "step_id": completed_step.id,
                    "approved": verdict.approved,
                    "findings": verdict.findings[:3],
                })
            if verdict.approved:
                continue
            feedback = "; ".join(verdict.findings[:3]) or "auditor requires stronger evidence"
            self._reopen_step(plan, completed_step, feedback)
            break

        active_step = plan.next_open_step()
        if active_step is not None and not plan.auditor_feedback:
            active_verdict = auditor.review_iteration(active_step, result)
            if not active_verdict.approved and self._should_replan_after_rejection(
                plan, active_step, active_verdict
            ):
                reason = "; ".join(active_verdict.findings[:3]) or "approach not converging"
                revised = self._replan_via_no_tools(plan, active_step, reason)
                plan.replans_run = max(0, int(getattr(plan, "replans_run", 0) or 0)) + 1
                plan.last_replan_reason = reason
                self._reopen_step(
                    plan,
                    active_step,
                    f"Approach re-plan required: {revised}",
                )

        if self._requires_tool_backed_progress(plan) and not self._has_tool_backed_evidence(plan):
            limit = max(5, len(plan.steps) * 2)
            if plan.iterations_run >= limit:
                return self._finish(plan, "paused", f"stale goal: no tool-backed evidence after {plan.iterations_run} iterations")
        return self._check_progress_or_stop(plan)

    def _record_goal_learning(
        self,
        plan: GoalPlan,
        auditor: GoalAuditor,
        findings: list[str],
        *,
        reason: str = "",
    ) -> None:
        """Persist high-signal auditor findings to profile for future goals.

        Only writes when findings are meaningful and not already recorded.
        Deduplication is handled by profile.append_profile_learning's source marker.
        """
        if not findings:
            return
        profile = getattr(self.agent, "profile", None)
        if not profile or not hasattr(profile, "append_profile_learning"):
            return
        insights = auditor.extract_learnings(
            findings,
            objective=getattr(plan, "objective", ""),
            iterations_run=int(getattr(plan, "iterations_run", 0) or 0),
            reason=reason,
        )
        if not insights:
            return
        source = f"goal-auditor:{getattr(plan, 'run_id', '') or 'unknown'}"
        try:
            profile.append_profile_learning(source, insights)
        except Exception:
            traceback.print_exc()

    def _auditor_approval_can_complete(self, step: GoalStep) -> bool:
        """Return True when an approved active step already has completion-grade evidence."""
        evidence = list(getattr(step, "evidence", []) or [])
        if not evidence:
            return False
        if _step_title_is_broad_repair(getattr(step, "title", "")) and not _has_scoped_write_evidence(evidence):
            return False
        if task_evidence.is_verification_step(getattr(step, "title", ""), kind=step.kind):
            return task_evidence.has_verification_tool_evidence(evidence) and task_evidence.has_passing_verification("", evidence)
        return any(task_evidence.evidence_item_is_tool_backed(str(item)) for item in evidence) or any(str(item).startswith("content:") for item in evidence)

    def _should_replan_after_rejection(self, plan: GoalPlan, step: GoalStep, verdict: Any) -> bool:
        """Return True when repeated rejection/stale evidence suggests approach drift."""
        if max(0, int(getattr(plan, "replans_run", 0) or 0)) >= 2:
            return False
        findings = " ".join(str(item or "").lower() for item in (getattr(verdict, "findings", []) or []))
        if "re-plan needed" in findings or "approach not converging" in findings or "stale approach" in findings:
            return True
        return max(0, int(getattr(step, "reopened_count", 0) or 0)) + 1 >= 3

    def _replan_via_no_tools(self, plan: GoalPlan, step: GoalStep, reason: str) -> str:
        """Ask a no-tools provider for a revised goal-step approach, with deterministic fallback."""
        reason = " ".join(str(reason or "approach is not converging").split())[:700]
        fallback = (
            f"For `{step.title}`, re-check the objective and current repo evidence, avoid repeating the same method, "
            "choose a smaller/direct approach, then continue only with tool-backed evidence."
        )
        complete = getattr(self.agent, "complete_no_tools", None)
        if not callable(complete):
            return fallback
        system = (
            "You are MO's goal re-plan assistant. No tools. "
            "Given a stalled goal step and evidence-backed reason, propose one concise revised approach. "
            "Do not claim work is done. Do not create taskboard truth. Return 1-3 direct sentences."
        )
        user = (
            f"Objective: {getattr(plan, 'objective', '')}\n"
            f"Current step: {getattr(step, 'title', '')}\n"
            f"Why current approach is not working: {reason}\n"
            "Propose a revised approach before more edits."
        )
        monitor = getattr(self.agent, "_goal_monitor", None) or getattr(getattr(self.agent, "gateway", None), "monitor", None)
        try:
            response, _provider = complete(
                surface="goal_replan",
                request=f"goal-replan-{getattr(plan, 'run_id', '') or 'active'}-{getattr(step, 'id', '')}",
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=1200,
                monitor=monitor,
            )
            text = " ".join(str(getattr(response, "content", "") or "").split())[:900]
            return text or fallback
        except Exception:
            return fallback

    def _run_agent_turn(
        self,
        prompt: str,
        monitor: Any | None,
        *,
        task_board: TaskBoard | None = None,
    ) -> str:
        """Call Agent.run_turn with the goal's authoritative taskboard."""
        run_turn = self.agent.run_turn
        try:
            signature = inspect.signature(run_turn)
        except (TypeError, ValueError):
            return run_turn(prompt)
        accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
        kwargs: dict[str, Any] = {}
        if monitor is not None and ("monitor" in signature.parameters or accepts_kwargs):
            kwargs["monitor"] = monitor
        if task_board is not None and ("task_board" in signature.parameters or accepts_kwargs):
            kwargs["task_board"] = task_board
        if self.on_board_event is not None and ("on_board_event" in signature.parameters or accepts_kwargs):
            kwargs["on_board_event"] = self.on_board_event
        if "cancel_event" in signature.parameters or accepts_kwargs:
            kwargs["cancel_event"] = self._cancel_event
        return run_turn(prompt, **kwargs)

    def _handle_provider_failure(self, plan: GoalPlan, step: GoalStep, message: str) -> str:
        """Retry transient provider failures without claiming goal progress."""
        plan.consecutive_provider_errors += 1
        step.status = "active"
        step.blocker = message
        step.blocker_kind = _goal_blocker_kind(message)
        plan.auditor_feedback = message
        if plan.consecutive_provider_errors >= 3:
            return self._finish(plan, "paused", "provider unavailable after 3 consecutive errors; paused without claiming progress")
        return self._check_progress_or_stop(plan)

    def _try_complete(self, plan: GoalPlan) -> str:
        """Final auditor gate before marking goal complete."""
        auditor = GoalAuditor(self.agent.profile)
        verdict = auditor.review_completion(plan)

        if verdict.approved:
            return self._finish(plan, "completed", "all steps done and auditor approved")

        # Record durable learnings from completion rejection.
        self._record_goal_learning(plan, auditor, verdict.findings, reason="completion-rejected")

        # Auditor wants more. If the original plan is missing a required verify
        # phase, repair the plan once instead of reopening the same non-verify
        # step forever. Otherwise reopen the existing matching phase.
        feedback = "; ".join(verdict.findings[:3]) or "auditor requires stronger evidence"
        if "tool-backed evidence" in feedback.lower() and self._requires_tool_backed_progress(plan) and not self._has_tool_backed_evidence(plan):
            limit = max(5, len(plan.steps) * 2)
            if plan.iterations_run >= limit:
                return self._finish(plan, "paused", f"stale goal: no tool-backed evidence after {plan.iterations_run} iterations")
        if "verification step" in feedback.lower() and not self._plan_has_verification_step(plan):
            self._add_missing_verification_step(plan, feedback)
            self._persist(plan)
            return self._format_progress(plan)
        target = self._step_for_auditor_feedback(plan, feedback)
        if max(0, int(getattr(target, "reopened_count", 0) or 0)) >= _MAX_COMPLETION_REPAIRS:
            # The same final rejection after repeated repairs will not converge
            # by trying again; stop with the auditor's reason instead of looping
            # until the wall-clock budget runs out.
            return self._finish(
                plan, "paused",
                f"final audit still rejects after {_MAX_COMPLETION_REPAIRS} repairs: {feedback}",
            )
        self._reopen_step(plan, target, feedback)
        self._persist(plan)
        return self._format_progress(plan)

    @staticmethod
    def _has_tool_backed_evidence(plan: GoalPlan) -> bool:
        return any(task_evidence.evidence_item_is_tool_backed(str(item)) for step in plan.steps for item in step.evidence)

    @staticmethod
    def _requires_tool_backed_progress(plan: GoalPlan) -> bool:
        """All goals require tool-backed progress by default."""
        _ = plan
        return True

    @staticmethod
    def _plan_has_verification_step(plan: GoalPlan) -> bool:
        return any(task_evidence.is_verification_step(step.title, kind=step.kind) for step in plan.steps)

    def _add_missing_verification_step(self, plan: GoalPlan, feedback: str) -> GoalStep:
        """Append one verification phase when the plan lacks it."""
        for step in plan.steps:
            if step.status == "active":
                step.status = "pending"
        title = "Verify result"
        numeric_ids = [int(step.id) for step in plan.steps if str(step.id).isdigit()]
        step_id = str((max(numeric_ids) if numeric_ids else len(plan.steps)) + 1)
        step = GoalStep(step_id, title, status="active", blocker=str(feedback or "verification step required").strip())
        step.blocker_kind = _goal_blocker_kind(step.blocker)
        plan.steps.append(step)
        plan.auditor_feedback = step.blocker
        return step

    def _reopen_step(self, plan: GoalPlan, step: GoalStep, feedback: str) -> None:
        """Reopen the real failed step after auditor rejection."""
        board = getattr(self.agent, "_goal_task_board", None)
        if board is not None and board.reopen(step.id, feedback):
            prior_reopens = max(0, int(getattr(step, "reopened_count", 0) or 0))
            self._sync_plan_from_task_board(plan, board)
            reopened = next((candidate for candidate in plan.steps if candidate.id == step.id), None)
            if reopened is not None:
                reopened.reopened_count = prior_reopens + 1
                reopened.blocker_kind = _goal_blocker_kind(feedback)
            plan.auditor_feedback = str(feedback or "auditor requires stronger evidence").strip()
            return
        for other in plan.steps:
            if other is not step and other.status == "active":
                other.status = "pending"
        step.status = "active"
        step.blocker = str(feedback or "auditor requires stronger evidence").strip()
        step.blocker_kind = _goal_blocker_kind(step.blocker)
        step.reopened_count = max(0, int(getattr(step, "reopened_count", 0) or 0)) + 1
        plan.auditor_feedback = step.blocker

    @staticmethod
    def _step_for_auditor_feedback(plan: GoalPlan, feedback: str) -> GoalStep:
        """Pick the existing phase the auditor is asking to repair."""
        candidates = list(plan.steps)
        for step in candidates:
            if step.status != "completed":
                return step
        if "verif" in str(feedback or "").lower():
            # Missing verification proof is repaired by verifying, not by
            # reopening whatever row happens to be last (often the report).
            verification = [
                step for step in candidates
                if task_evidence.is_verification_step(step.title, kind=step.kind)
            ]
            if verification:
                return verification[-1]
        return candidates[-1]

    def _check_progress_or_stop(self, plan: GoalPlan) -> str:
        """After an iteration, continue unless complete or wall-clock budget stops it."""
        if plan.all_done():
            return self._try_complete(plan)

        self._persist(plan)
        return self._format_progress(plan)

    def _finish(self, plan: GoalPlan, state: str, reason: str) -> str:
        """Finalize the goal."""
        plan.state = state
        plan.stop_reason = reason
        plan.stop_kind = _goal_blocker_kind(reason, state=state, feedback=getattr(plan, "auditor_feedback", ""))
        plan.finished_at = time.time()
        plan.context_savings_chars = max(0, self._agent_context_savings_chars() - int(getattr(plan, "context_savings_start_chars", 0) or 0))
        plan.context_savings_ops = max(0, self._agent_context_savings_ops() - int(getattr(plan, "context_savings_start_ops", 0) or 0))
        if state == "completed":
            for step in plan.steps:
                if step.status == "completed":
                    step.blocker = ""
                    step.blocker_kind = ""
            plan.auditor_feedback = ""
        else:
            # Record durable learnings when a goal is blocked or paused.
            findings = [reason]
            if plan.auditor_feedback:
                findings.append(plan.auditor_feedback)
            blocked_steps = [s for s in plan.steps if s.status == "blocked"]
            if blocked_steps:
                findings.append(f"{len(blocked_steps)} blocked step(s): " + ", ".join(s.title for s in blocked_steps[:3]))
            auditor = GoalAuditor(self.agent.profile)
            self._record_goal_learning(plan, auditor, findings, reason=state)
        self.agent._goal_active = False
        summary = f"Goal {state}: {plan.completed_count()}/{len(plan.steps)} done"
        if reason:
            summary += f" · {str(reason)[:180]}"
        evidence = [item for step in plan.steps for item in list(getattr(step, "evidence", []) or [])]
        ensure_worker_registry(self.agent).update(
            getattr(self.agent, "_goal_worker_id", ""),
            state,
            reason,
            result_summary=summary,
            evidence=evidence[:12],
        )
        board = getattr(self.agent, "_goal_task_board", None)
        if board is not None:
            board.state = (
                "completed" if state == "completed"
                else "blocked" if state == "blocked"
                else "paused" if state == "paused"
                else "active"
            )
            board.updated_at = time.time()
            record_snapshot(board, f"goal_{state}", source="goal", state=board.state)
        self._persist(plan)
        monitor = getattr(self.agent, "_goal_monitor", None) or getattr(getattr(self.agent, "gateway", None), "monitor", None)
        if monitor:
            monitor.emit("goal_finish", {
                "run_id": plan.run_id,
                "state": state,
                "reason": reason,
                "completed": plan.completed_count(),
                "total": len(plan.steps),
            })
        try:
            boundary = check_consistency_boundary("goal", agent=self.agent, goal_plan=plan, final_text=reason)
            setattr(self.agent, "_last_consistency_boundary_report", boundary)
            emit_consistency_boundary(boundary, monitor)
        except Exception:
            traceback.print_exc()

        label = {"completed": "✓ DONE", "blocked": "✗ BLOCKED", "paused": "PAUSED"}.get(state, "STOPPED")
        elapsed = self._elapsed_text(plan)
        return f"[{label}] Goal: {plan.completed_count()}/{len(plan.steps)} done · {elapsed}\n{reason}"

    def _new_goal_session(self):
        """Create a goal-private session with a snapshot of current context."""
        from ..session.session import Session

        system = getattr(self.agent, "system_message", "") or "You are MO. Evidence-first."
        current = getattr(self.agent, "session", None)
        max_history = getattr(current, "max_history", 500)
        goal_session = Session(system, max_history=max_history)
        goal_session.messages = [dict(m) for m in list(getattr(current, "messages", []) or [])]
        goal_session.total_tokens = int(getattr(current, "total_tokens", 0) or 0)
        goal_session.output_tokens = int(getattr(current, "output_tokens", 0) or 0)
        goal_session.token_log = list(getattr(current, "token_log", []) or [])
        return goal_session

    def _build_turn_prompt(self, plan: GoalPlan, step: GoalStep) -> str:
        """Build the prompt injected as user message for this iteration."""
        plan_text = self._render_plan_text(plan)
        time_left = max(0, plan.budget.max_wall_seconds - (time.time() - plan.started_at))
        feedback = f"\nAuditor feedback: {plan.auditor_feedback}" if plan.auditor_feedback else ""
        task_board = getattr(self.agent, "_goal_task_board", None)
        needs_plan = task_board is not None and not list(getattr(task_board, "tasks", []) or [])
        acceptance = "\n".join(
            f"  - {criterion}"
            for criterion in list(getattr(plan, "acceptance_criteria", []) or [])
        )
        verification_ids = ", ".join(list(getattr(plan, "verification_criteria", []) or [])) or "none"
        planning_instruction = ""
        if needs_plan:
            planning_instruction = (
                "Before any work tool, call set_plan mode=start. Map every AC id below to at least one "
                "concrete inspect/edit/execute row, and map each required verification AC to a verify row. "
                "Do not collapse distinct surfaces or device/runtime acceptance into one generic row.\n"
            )

        return (
            f"[GOAL iteration {plan.iterations_run}]\n"
            f"Objective: {plan.objective}\n"
            f"Time left: {int(time_left)}s\n"
            f"Progress: {plan.completed_count()}/{len(plan.steps)} steps done\n"
            f"Current step: {step.title}\n"
            f"{feedback}\n"
            f"Acceptance criteria:\n{acceptance}\n"
            f"AC ids requiring verify rows: {verification_ids}\n"
            f"Plan:\n{plan_text}\n\n"
            f"{planning_instruction}"
            f"Work the current active row only. Use targeted tools and call complete_task only after that row's own gate passes; "
            f"do not exhaustively scan unrelated surfaces or borrow evidence from another row. "
            f"Stop and answer once the row has concrete evidence, or report the precise blocker/next check if more work is needed."
        )

    def _render_plan_text(self, plan: GoalPlan) -> str:
        """Compact plan rendering for the prompt."""
        from ..tasking.task_board import status_marker
        lines = []
        for step in plan.steps:
            blocker = _format_goal_blocker(getattr(step, "blocker_kind", ""), getattr(step, "blocker", ""))
            suffix = f" — {blocker}" if blocker else ""
            lines.append(f"  {status_marker(step.status)} {step.id}. {step.title}{suffix}")
        return "\n".join(lines)

    def _record_evidence(self, plan: GoalPlan, step: GoalStep, result: str, *, since_ts: float = 0.0) -> None:
        """Extract evidence from the turn result and tool audit."""
        evidence_before = set(step.evidence)
        # Check tool audit log for evidence of work done
        audit_path = _agent_tool_audit_path(self.agent)
        if audit_path and audit_path.exists():
            try:
                lines = audit_path.read_text(encoding="utf-8").splitlines()
                for line in lines:
                    try:
                        entry = json.loads(line)
                        if float(entry.get("ts") or 0.0) < float(since_ts or 0.0):
                            continue
                        entry_surface = str(entry.get("surface") or "")
                        entry_worker_id = str(entry.get("worker_id") or "")
                        goal_worker_id = str(getattr(self.agent, "_goal_worker_id", "") or getattr(plan, "run_id", "") or "")
                        if entry_surface and entry_surface != "goal":
                            continue
                        if goal_worker_id and entry_worker_id and entry_worker_id != goal_worker_id:
                            continue
                        if not entry.get("blocked"):
                            tool = entry.get("tool", "")
                            if tool in task_evidence.TOOL_BACKED_EVIDENCE_TOOLS:
                                item = task_evidence.tool_evidence_label(tool, entry.get("arguments", {}) or {}, max_detail_chars=100)
                                if item not in step.evidence:
                                    step.evidence.append(item)
                    except json.JSONDecodeError:
                        continue
            except Exception:
                traceback.print_exc()

        result_lower = str(result or "").lower()
        if task_evidence.is_verification_step(step.title, kind=step.kind):
            failed = task_evidence.has_failing_tests(result_lower)
            passed = task_evidence.has_passing_verification(result_lower, [])
            if failed:
                if "verification_result:failed" not in step.evidence:
                    step.evidence.append("verification_result:failed")
            elif passed:
                step.evidence = [item for item in step.evidence if item != "verification_result:failed"]
                if "verification_result:passed" not in step.evidence:
                    step.evidence.append("verification_result:passed")

        has_new_evidence = any(item not in evidence_before for item in step.evidence)
        if _step_title_is_broad_repair(step.title) and not _has_scoped_write_evidence(step.evidence):
            # Helper scripts/checkers and read probes are not the repair. Keep
            # the broad fix step active until a scoped write/edit lands.
            step.status = "active"
            return
        if has_new_evidence and not any(item == "verification_result:failed" for item in step.evidence):
            step.status = "completed"
            step.blocker = ""
            step.blocker_kind = ""
            # Activate next pending step
            for s in plan.steps:
                if s.status == "pending":
                    s.status = "active"
                    break
        elif any(item == "verification_result:failed" for item in step.evidence):
            step.status = "active"
        elif result and len(result.strip()) > 50:
            # Content-based evidence for analysis/report steps
            if any(word in step.title.lower() for word in ("analyze", "identify", "formulate", "report", "investigate")):
                content_item = f"content:{len(result)}chars"
                if content_item not in step.evidence:
                    step.evidence.append(content_item)
                    step.status = "completed"
                    step.blocker = ""
                    step.blocker_kind = ""
                    for s in plan.steps:
                        if s.status == "pending":
                            s.status = "active"
                            break

    def _append_goal_tool_evidence(
        self,
        plan: GoalPlan,
        task_board: TaskBoard,
        *,
        since_ts: float = 0.0,
    ) -> None:
        """Append this turn's tool-audit evidence to the goal's active board row.

        Goal rows are acceptance-gated and only complete with qualifying
        evidence. The taskboard runtime records some tool effects, but the
        goal's isolated session must also surface tool-audit entries
        (write_file, shell, test_runner, etc.) so real work counts as
        tool-backed progress instead of triggering a false "stale goal" pause.
        """
        if not getattr(task_board, "tasks", None):
            return
        active_id = task_board.active_task_id()
        if not active_id:
            return
        audit_path = _agent_tool_audit_path(self.agent)
        if not audit_path or not audit_path.exists():
            return
        goal_worker_id = str(
            getattr(self.agent, "_goal_worker_id", "")
            or getattr(plan, "run_id", "")
            or ""
        )
        items: list[str] = []
        try:
            for line in audit_path.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if float(entry.get("ts") or 0.0) < float(since_ts or 0.0):
                    continue
                entry_surface = str(entry.get("surface") or "")
                if entry_surface and entry_surface != "goal":
                    continue
                entry_worker_id = str(entry.get("worker_id") or "")
                if goal_worker_id and entry_worker_id and entry_worker_id != goal_worker_id:
                    continue
                if entry.get("blocked"):
                    continue
                tool = entry.get("tool", "")
                if tool not in task_evidence.TOOL_BACKED_EVIDENCE_TOOLS:
                    continue
                item = task_evidence.tool_evidence_label(
                    tool,
                    entry.get("arguments", {}) or {},
                    max_detail_chars=100,
                )
                if item and item not in items:
                    items.append(item)
        except Exception:
            traceback.print_exc()
        for item in items:
            task_board.append_evidence(active_id, item)

    def _format_progress(self, plan: GoalPlan) -> str:
        """Compact progress line."""
        elapsed = self._elapsed_text(plan)
        next_step = plan.next_open_step()
        next_text = f" · next: {next_step.title}" if next_step else ""
        if next_step:
            blocker = _format_goal_blocker(getattr(next_step, "blocker_kind", ""), getattr(next_step, "blocker", ""))
            if blocker:
                next_text += f" · blocker: {blocker}"
        return f"[GOAL] {plan.completed_count()}/{len(plan.steps)} done · iter {plan.iterations_run} · {elapsed}{next_text}"

    @staticmethod
    def _elapsed_text(plan: GoalPlan) -> str:
        elapsed = time.time() - plan.started_at
        if elapsed < 60:
            return f"{elapsed:.0f}s"
        if elapsed < 3600:
            return f"{elapsed / 60:.1f}m"
        return f"{elapsed / 3600:.1f}h"

    def _persist(self, plan: GoalPlan) -> None:
        """Save goal state to the private work/goals store."""
        try:
            if not str(getattr(plan, "run_id", "") or "").strip():
                plan.run_id = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
            from ..state.paths import GOAL_RUNS_DIR
            root = Path(resolve_state_path(GOAL_RUNS_DIR, getattr(self.agent, "config", {}) if self.agent is not None else None))
            root.mkdir(parents=True, exist_ok=True)
            path = root / f"{plan.run_id}.json"
            atomic_write_json(path, plan.as_dict(), indent=2, ensure_ascii=False)
            _prune_goal_runs(root)
        except Exception:
            traceback.print_exc()

    def to_task_board(self, plan: GoalPlan | None = None) -> TaskBoard:
        """Return the live goal taskboard, rebuilding only for restored state."""
        plan = plan or getattr(self.agent, "_goal_plan", None)
        session = getattr(self.agent, "session", None)
        session_id = str(getattr(session, "session_id", "") or "")
        if not plan:
            return TaskBoard(
                turn_id="goal-empty",
                title="Goal progress",
                tasks=[],
                objective="",
                session_id=session_id,
                source="goal",
                plan_owner="model",
            )

        live_board = getattr(self.agent, "_goal_task_board", None)
        if (
            live_board is not None
            and str(getattr(live_board, "turn_id", "") or "") == f"goal-{plan.run_id}"
        ):
            return live_board

        tasks = []
        for idx, step in enumerate(plan.steps):
            kind, completion_gate = self._goal_task_metadata(plan, step)
            depends_on = list(getattr(step, "depends_on", []) or [])
            if not depends_on and idx > 0:
                depends_on = [str(plan.steps[idx - 1].id)]
            tasks.append(TaskItem(
                id=step.id,
                title=step.title,
                status="completed" if step.status == "completed" else
                       "active" if step.status == "active" else
                       "blocked" if step.status == "blocked" else "pending",
                evidence=step.evidence,
                blocker=str(getattr(step, "blocker", "") or ""),
                kind=kind,
                completion_gate=completion_gate,
                depends_on=depends_on,
                acceptance_criteria=list(getattr(step, "acceptance_criteria", []) or []),
                expected_evidence=list(getattr(step, "expected_evidence", []) or []),
                test_strategy=str(getattr(step, "test_strategy", "") or ""),
            ))
        board = TaskBoard(
            turn_id=f"goal-{plan.run_id}",
            title="Goal progress",
            tasks=tasks,
            objective=plan.objective,
            session_id=session_id,
            source="goal",
            plan_owner="model",
            required_acceptance_criteria=list(getattr(plan, "acceptance_criteria", []) or []),
            required_verification_criteria=list(getattr(plan, "verification_criteria", []) or []),
        )
        self.agent._goal_task_board = board
        return board

    def _new_goal_task_board(self, plan: GoalPlan) -> TaskBoard:
        """Create the empty model-owned board that must be planned before work."""
        session = getattr(self.agent, "session", None)
        return TaskBoard(
            turn_id=f"goal-{plan.run_id}",
            title="Goal progress",
            tasks=[],
            objective=plan.objective,
            session_id=str(getattr(session, "session_id", "") or ""),
            source="goal",
            plan_owner="model",
            required_acceptance_criteria=list(plan.acceptance_criteria),
            required_verification_criteria=list(plan.verification_criteria),
        )

    @staticmethod
    def _sync_plan_from_task_board(plan: GoalPlan, board: TaskBoard) -> None:
        """Mirror the authoritative live board into durable GoalPlan state."""
        previous = {str(step.id): step for step in plan.steps}
        synced: list[GoalStep] = []
        for task in list(board.tasks or []):
            prior = previous.get(str(task.id))
            blocker = str(task.blocker or "")
            synced.append(GoalStep(
                id=str(task.id),
                title=str(task.title),
                status=str(task.status),
                evidence=list(task.evidence),
                blocker=blocker,
                reopened_count=max(0, int(getattr(prior, "reopened_count", 0) or 0)),
                iterations_run=max(0, int(getattr(prior, "iterations_run", 0) or 0)),
                blocker_kind=_goal_blocker_kind(blocker, state=str(task.status)),
                kind=str(task.kind or ""),
                completion_gate=str(task.completion_gate or ""),
                depends_on=list(task.depends_on),
                acceptance_criteria=list(task.acceptance_criteria),
                expected_evidence=list(task.expected_evidence),
                test_strategy=str(task.test_strategy or ""),
            ))
        plan.steps = synced

    @staticmethod
    def _goal_task_metadata(plan: GoalPlan, step: GoalStep) -> tuple[str, str]:
        """Best-effort internal metadata for goal taskboard rows."""
        explicit_kind = str(getattr(step, "kind", "") or "").strip().lower()
        explicit_gate = str(getattr(step, "completion_gate", "") or "").strip().lower()
        if explicit_kind or explicit_gate:
            return explicit_kind, explicit_gate
        title = str(getattr(step, "title", "") or "").lower()
        text = f"{title} {getattr(plan, 'objective', '')}".lower()
        if task_evidence.is_verification_step(title):
            return "verify", "verification"
        if any(word in title for word in ("report", "summarize", "answer", "respond")):
            return "report", "final"
        text_words = _words(text)
        if bool(text_words & {"ask", "confirm", "approval", "approve"}) or "operator input" in text:
            return "ask", "manual"
        if any(word in text for word in ("build", "fix", "edit", "write", "create", "implement", "generate", "add", "update", "repair")):
            return "edit", "tool"
        if any(word in text for word in ("inspect", "review", "audit", "investigate", "analyze", "map", "read", "check")):
            return "inspect", "tool"
        return "", ""


# ── Budget parsing ────────────────────────────────────────────────

def parse_goal_budget(tokens: list[str]) -> tuple[str, GoalBudget]:
    """Parse /goal arguments: extract objective and optional --timeout N, capped at 4h."""
    remaining: list[str] = []
    budget_kwargs: dict[str, Any] = {}
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token in ("--timeout", "--max-seconds") and idx + 1 < len(tokens):
            try:
                budget_kwargs["max_wall_seconds"] = float(tokens[idx + 1])
                idx += 2
                continue
            except ValueError:
                pass
        remaining.append(token)
        idx += 1
    return " ".join(remaining).strip(), GoalBudget(**budget_kwargs)
