"""Optional evidence-gated taskboard loop runner.

The runner is deterministic glue for future automation. It never treats provider
text or loop markers as completion; step results must provide explicit evidence
or blockers, and final/manual/report rows are left for their normal owners.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from . import task_evidence
from .task_board import TaskBoard, TaskItem


@dataclass
class BoardLoopStepResult:
    task_id: str
    action: str = "noop"  # completed | blocked | noop
    evidence: list[str] = field(default_factory=list)
    blocker: str = ""


@dataclass
class BoardLoopResult:
    iterations: int = 0
    completed: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    stopped_reason: str = ""


def run_board_loop(
    board: TaskBoard,
    step_runner: Callable[[TaskItem, TaskBoard], BoardLoopStepResult | dict[str, Any]],
    *,
    max_steps: int = 20,
) -> BoardLoopResult:
    """Run ready rows with evidence-gated mutations and deterministic stopping."""
    result = BoardLoopResult()
    for _ in range(max(0, int(max_steps or 0))):
        task = board.next_ready_task()
        if not task:
            result.stopped_reason = "completed" if board.open_count() == 0 else "no_ready_task"
            return result
        if task.status != "active" and not board.activate(task.id):
            result.stopped_reason = "no_ready_task"
            return result
        if _final_or_manual(task):
            result.stopped_reason = "awaiting_final_or_manual"
            return result
        outcome = _coerce_result(step_runner(task, board), task.id)
        result.iterations += 1
        if outcome.action == "blocked" or outcome.blocker:
            board.block(task.id, outcome.blocker or "blocked by board loop")
            result.blocked.append(task.id)
            result.stopped_reason = "blocked"
            return result
        if outcome.action != "completed":
            result.stopped_reason = "no_progress"
            return result
        if not _evidence_allowed(task, outcome.evidence):
            result.stopped_reason = "missing_or_invalid_evidence"
            return result
        board.complete(task.id, evidence=outcome.evidence)
        result.completed.append(task.id)
        next_task = board.next_ready_task()
        if next_task and next_task.status == "pending":
            board.activate(next_task.id)
        if board.open_count() == 0:
            result.stopped_reason = "completed"
            return result
    result.stopped_reason = "max_steps"
    return result


def _coerce_result(value: BoardLoopStepResult | dict[str, Any], default_task_id: str) -> BoardLoopStepResult:
    if isinstance(value, BoardLoopStepResult):
        return value
    data = value if isinstance(value, dict) else {}
    raw_evidence = data.get("evidence") or []
    if isinstance(raw_evidence, str):
        evidence = [raw_evidence]
    else:
        try:
            evidence = [str(item) for item in list(raw_evidence) if str(item or "").strip()]
        except TypeError:
            evidence = []
    return BoardLoopStepResult(
        task_id=str(data.get("task_id") or default_task_id),
        action=str(data.get("action") or "noop"),
        evidence=evidence,
        blocker=str(data.get("blocker") or ""),
    )


def _final_or_manual(task: TaskItem) -> bool:
    return task.completion_gate in {"final", "manual"} or task.kind in {"report", "ask"}


def _evidence_allowed(task: TaskItem, evidence: list[str]) -> bool:
    if not evidence:
        return False
    if task.completion_gate == "verification" or task.kind == "verify":
        return task_evidence.has_verification_tool_evidence(evidence)
    return any(task_evidence.evidence_item_is_tool_backed(item) for item in evidence)
