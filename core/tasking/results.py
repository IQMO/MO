"""Typed in-memory truth for task transitions and tool execution chronology."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


def taskboard_position(task_board: object | None) -> tuple[str, str]:
    """Return the active row and its semantic phase without mutating the board."""
    if task_board is None:
        return "", ""
    try:
        task_id = str(task_board.active_task_id() or "")
        row = task_board.task(task_id) if task_id else None
    except Exception:
        return "", ""
    phase = str(
        getattr(row, "kind", "")
        or getattr(row, "completion_gate", "")
        or getattr(row, "status", "")
        or ""
    ).strip().lower()
    return task_id, phase


@dataclass(frozen=True)
class TaskTransitionResult:
    """One authoritative task plan/advance/rejection/finalization result."""

    operation: str
    ok: bool
    reason: str = ""
    mode: str = ""
    task_id: str = ""
    requested_task_id: str = ""
    activated: str = ""
    title: str = ""
    kind: str = ""
    completion_gate: str = ""
    expected_evidence: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    rows: int = 0
    preserved: int = 0
    active_task_before: str = ""
    active_task_after: str = ""
    phase_before: str = ""
    phase_after: str = ""

    @property
    def outcome(self) -> str:
        return "success" if self.ok else "rejected"

    def render(self) -> str:
        if self.operation == "set_plan":
            return self._render_set_plan()
        if self.operation in {"complete_task", "finalize_task"}:
            return self._render_complete_task()
        return f"{self.operation} {'completed' if self.ok else 'rejected'}: {self.reason or 'unknown'}."

    def _render_set_plan(self) -> str:
        if self.ok:
            if self.mode == "cancel":
                return "Unfinished tasks cancelled. Completed work and recorded evidence were preserved; no further confirmation is needed. This closes the plan only: verify cleanup of any owned running processes separately."
            if self.mode == "pause":
                return f"Plan paused: {self.reason}. Ask for the missing input and wait; resume by revising the unfinished plan after the answer. This is not an approval request."
            if self.mode == "revise":
                active = f" Active task: {self.active_task_after}." if self.active_task_after else " No unfinished tasks remain."
                return (
                    f"Remaining plan {'unchanged' if self.reason == 'unchanged' else 'revised'} with {self.rows} step(s); "
                    f"{self.preserved} completed step(s) and their evidence were preserved. "
                    "Unchanged unfinished rows retain their IDs and evidence." + active
                )
            return (
                f"Plan set with {self.rows} step(s), numbered 1-{self.rows} in order. "
                "Advance each row with complete_task after its evidence gate passes; "
                'task_id is the row number ("1", "2", ...), never a label like "task-1".'
            )
        messages = {
            "invalid_mode": "set_plan rejected: mode must be start, revise, pause or cancel.",
            "board_closed": "set_plan rejected: this board is closed; cancelled work is not resumable.",
            "no_owned_plan": "set_plan rejected: pause/cancel requires an existing model/procedure plan.",
            "lifecycle_reason_required": "set_plan rejected: give the missing input/blocker for pause or the user's cancellation instruction for cancel.",
            "lifecycle_tasks_not_allowed": "set_plan rejected: pause/cancel keeps the existing rows; omit tasks or supply an empty list.",
            "disabled": "set_plan rejected: model-owned taskboards are disabled.",
            "board_locked": "set_plan rejected: this taskboard is owned by an extension or goal and cannot be rewritten.",
            "plan_already_exists": "set_plan rejected: a plan already exists. Use mode=revise only when evidence invalidates its remaining steps.",
            "revision_not_owned": "set_plan revision rejected: only model-owned or procedure boards may revise their remaining steps.",
            "no_plan_to_revise": "set_plan revision rejected: there is no active plan to revise.",
            "revision_reason_required": "set_plan revision rejected: include the evidence-based reason the remaining plan changed.",
            "tasks_required": "set_plan rejected: provide a non-empty tasks list.",
            "acceptance_delivery_missing": (
                "set_plan rejected: every required AC id must appear on at least "
                "one concrete inspect, edit, or execute row."
            ),
            "acceptance_verification_missing": (
                "set_plan rejected: every required verification AC id must also "
                "appear on a verify row."
            ),
            "stale_plan_target": (
                "set_plan rejected: the plan reused a path from an earlier user turn "
                "while omitting every explicit path in the latest request. Rebuild the "
                "plan from the latest request's exact identifiers."
            ),
        }
        return messages.get(self.reason, f"set_plan rejected: {self.reason or 'not_applied'}.")

    def _render_complete_task(self) -> str:
        task_id = self.task_id or "active task"
        if self.ok:
            suffix = f" Next active task: {self.activated}." if self.activated else ""
            return f"Task {task_id} marked as complete.{suffix}"
        if self.reason == "task_id_mismatch":
            return f"complete_task rejected: requested task {self.requested_task_id}, but the active task is {task_id}. Complete only the active task after its evidence gate passes."
        if self.reason == "manual_approval_required":
            return (
                f"complete_task rejected: task {task_id} is a manual gate. "
                "Ask the user for the exact approval in the final answer and leave this row open; "
                "only an explicit approval in a later user turn can complete it."
            )
        if self.reason == "dependencies_unsatisfied":
            return f"complete_task rejected: task {task_id} still has unmet dependencies."
        if self.reason == "verification_pending":
            return (
                f"complete_task rejected: task {task_id} has verification still running. "
                "Continue independent work; MO will receive the terminal result through the existing "
                "background-verification receipt before finalization. Do not launch a duplicate suite."
            )
        if self.reason == "verification_failed":
            return (
                f"complete_task rejected: task {task_id} has a current failed or timed-out verification. "
                "Inspect the terminal result, fix the cause, and rerun verification after the mutation."
            )
        if self.reason == "verification_inconclusive":
            return (
                f"complete_task rejected: task {task_id} has a terminal verification result without "
                "passing evidence. Inspect it or run a suitable check before completing the task."
            )
        if self.reason == "missing_required_evidence":
            expected = "; ".join(self.expected_evidence[:3])
            current = "; ".join(self.evidence[:3])
            meta = f"kind={self.kind or '-'} gate={self.completion_gate or '-'}"
            expected_text = f" Expected evidence: {expected}." if expected else ""
            current_text = f" Current evidence: {current}." if current else ""
            return f"complete_task rejected: task {task_id} lacks matching required evidence ({meta}).{expected_text}{current_text} Gather the required evidence, then call complete_task again."
        return f"complete_task rejected for task {task_id}: {self.reason or 'completion_rejected'}."


@dataclass(frozen=True)
class ToolExecutionRecord:
    """One ordered tool outcome; executor payloads remain unchanged."""

    tool: str
    action: str = ""
    view: str = ""
    presentation_only: bool = False
    blocked: bool = False
    block_reason: str = ""
    error: bool = False
    verification: bool = False
    semantic_rejection: bool = False
    semantic_reason: str = ""
    computer_events: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    task_transition: TaskTransitionResult | None = None
    active_task_before: str = ""
    active_task_after: str = ""
    phase_before: str = ""
    phase_after: str = ""

    @property
    def successful(self) -> bool:
        return not (self.blocked or self.error or self.semantic_rejection)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ToolExecutionRecord":
        transition = value.get("task_transition")
        return cls(
            tool=str(value.get("tool") or ""),
            action=str(value.get("action") or ""),
            view=str(value.get("view") or ""),
            presentation_only=bool(value.get("presentation_only")),
            blocked=bool(value.get("blocked")),
            block_reason=str(value.get("block_reason") or ""),
            error=bool(value.get("error")),
            verification=bool(value.get("verification")),
            semantic_rejection=bool(value.get("semantic_rejection")),
            semantic_reason=str(value.get("semantic_reason") or ""),
            computer_events=tuple(
                dict(item) for item in (value.get("computer_events") or ())
                if isinstance(item, Mapping)
            ),
            task_transition=(transition if isinstance(transition, TaskTransitionResult) else None),
            active_task_before=str(value.get("active_task_before") or ""),
            active_task_after=str(value.get("active_task_after") or ""),
            phase_before=str(value.get("phase_before") or ""),
            phase_after=str(value.get("phase_after") or ""),
        )


def tool_execution_record(value: object) -> ToolExecutionRecord:
    if isinstance(value, ToolExecutionRecord):
        return value
    if isinstance(value, Mapping):
        return ToolExecutionRecord.from_mapping(value)
    raise TypeError("tool execution rows must be ToolExecutionRecord or mappings")


def tool_execution_records(values: object) -> list[ToolExecutionRecord]:
    if not isinstance(values, (list, tuple)):
        return []
    records: list[ToolExecutionRecord] = []
    for value in values:
        try:
            records.append(tool_execution_record(value))
        except TypeError:
            continue
    return records


__all__ = [
    "TaskTransitionResult",
    "ToolExecutionRecord",
    "taskboard_position",
    "tool_execution_record",
    "tool_execution_records",
]
