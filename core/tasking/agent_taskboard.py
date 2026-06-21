"""MO agent task-board management mixin — extracted from core/agent.py (DEVMODE05 Phase 2)."""

from .task_board import TaskBoard
from ..backend_monitor import BackendMonitor
from . import task_evidence


class AgentTaskBoard:
    """Task-board management methods for the MO Agent."""

    def _advance_task_board_after_tool(self, task_board: TaskBoard, tool_name: str, arguments: dict | None = None, *, monitor: BackendMonitor | None = None) -> bool:
        """Append tool evidence to the active row, and advance only on explicit complete_task.

        The taskboard is a user-visible progress contract. A successful tool
        execution is recorded as evidence, but the active task is only completed
        when the Agent explicitly calls the complete_task tool.
        """
        active = task_board.active_task_id()
        if not active:
            return False

        # 1. Always append evidence to the active task (unless it's complete_task itself)
        if tool_name != "complete_task":
            task_board.append_evidence(active, self._task_evidence_item_for_tool(tool_name, arguments or {}))

        # 2. Only advance the taskboard if the agent explicitly signals completion
        if tool_name != "complete_task":
            return False

        tasks = task_board.tasks
        try:
            idx = next(i for i, t in enumerate(tasks) if t.id == active)
        except StopIteration:
            return False

        # Mark current task complete
        task_board.complete(active)

        # Activate next ready task
        next_id = None
        for row in tasks:
            if row.status == "pending" and task_board.dependencies_satisfied(row.id):
                if task_board.activate(row.id):
                    next_id = row.id
                break

        if monitor:
            monitor.emit("board_advance", {
                "completed": active,
                "activated": next_id,
                "tool": tool_name,
                "idx": idx,
                "total": len(tasks),
            })
        return True

    @staticmethod
    def _task_evidence_item_for_tool(tool_name: str, arguments: dict | None = None) -> str:
        return task_evidence.taskboard_tool_evidence_item(tool_name, arguments or {})

    def _finalize_task_board_for_answer(self, task_board: TaskBoard) -> bool:
        """Reflect final-answer truth without turning unfinished work red.

        Final/report rows may complete from the final assistant answer.  Other
        active rows are not finished just because the model responded; leave
        them active so the board truth shows MO still owes that step instead of
        inventing a red blocker like "not completed before final answer".
        """
        active = task_board.active_task_id()
        if not active:
            return False
        task = task_board.task(active)
        if not self._final_should_complete_task(task):
            return False
        task_board.complete(active, evidence="final:assistant_response")
        ready_id = task_board.first_ready_pending_id()
        if ready_id and task_board.dependencies_satisfied(ready_id):
            task_board.activate(ready_id)
        return True

    def _finalize_self_protocol_task_board_for_answer(self, user_input: str, final_text: str, task_board: TaskBoard) -> bool:
        """Close self-protocol phase rows only after their terminal report gate passes.

        DEVMODE05 and VS05 own deterministic phase boards. Their phases are
        protocol checkpoints, not ordinary implementation rows, so a valid
        terminal report can close the remaining phase rows before the final
        consistency boundary reads task truth.
        """
        if not task_board or task_board.open_count() == 0:
            return False

        from ..self_capability_preflight import (
            devmode05_final_allows_stop,
            ifdev05_final_allows_stop,
            is_devmode05_activation,
            is_ifdev05_activation,
            is_vs05_activation,
            vs05_final_allows_stop,
        )

        if is_vs05_activation(user_input):
            if not vs05_final_allows_stop(user_input, final_text):
                return False
            evidence = "final:vs05_protocol_closeout"
        elif is_devmode05_activation(user_input):
            if not devmode05_final_allows_stop(user_input, final_text):
                return False
            evidence = "final:devmode05_protocol_closeout"
        elif is_ifdev05_activation(user_input):
            if not ifdev05_final_allows_stop(user_input, final_text):
                return False
            evidence = "final:ifdev05_protocol_closeout"
        else:
            return False

        # C1: the terminal report (gated above) closes the remaining phase rows.
        # Carry the turn's real gathered evidence onto each closed row so the board
        # reflects what actually happened instead of a hollow identical `final:`
        # token (phase rows were otherwise closed on the token alone, with no
        # per-phase evidence). Full per-phase attribution needs phase auto-advance.
        carried: list[str] = []
        for t in task_board.tasks:
            for e in (t.evidence or []):
                es = str(e)
                if not es.startswith("final:") and es not in carried:
                    carried.append(es)
        carried = carried[:8]

        changed = False
        for task in list(task_board.tasks):
            if task.status != "completed":
                row_evidence = [evidence, *carried] if carried else evidence
                task_board.complete(task.id, evidence=row_evidence)
                changed = changed or task.status == "completed"
        return changed

    @staticmethod
    def _final_should_complete_task(task: object) -> bool:
        return task_evidence.final_should_complete_task(task)

    @staticmethod
    def _final_report_task_id(task_board: TaskBoard) -> str:
        return task_evidence.final_report_task_id(task_board)
