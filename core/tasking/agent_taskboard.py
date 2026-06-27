"""MO agent task-board management mixin."""

from .. import local_extensions
from ..backend_monitor import BackendMonitor
from . import task_evidence
from .task_board import TaskBoard


class AgentTaskBoard:
    """Task-board management methods for the MO Agent."""

    def _advance_task_board_after_tool(
        self,
        task_board: TaskBoard,
        tool_name: str,
        arguments: dict | None = None,
        *,
        monitor: BackendMonitor | None = None,
    ) -> bool:
        """Append tool evidence and advance only on explicit complete_task."""
        if tool_name == "set_plan":
            return self._apply_model_plan(task_board, arguments or {}, monitor=monitor)
        local_extensions.on_tool_arguments(self, tool_name, arguments or {})

        active = task_board.active_task_id()
        if not active:
            return False

        if tool_name != "complete_task":
            task_board.append_evidence(active, self._task_evidence_item_for_tool(tool_name, arguments or {}))

        if tool_name != "complete_task":
            return False

        tasks = task_board.tasks
        try:
            idx = next(i for i, t in enumerate(tasks) if t.id == active)
        except StopIteration:
            return False

        active_row = tasks[idx]
        active_gate = str(getattr(active_row, "completion_gate", "") or "").lower().strip()
        active_kind = str(getattr(active_row, "kind", "") or "").lower().strip()
        if active_gate == "final" or active_kind == "report":
            if monitor:
                monitor.emit("board_complete_rejected", {
                    "task": active,
                    "tool": tool_name,
                    "reason": "final_row_requires_final_answer",
                })
            return False

        row_has_real = any(not str(e).startswith("final:") for e in (active_row.evidence or []))
        if not row_has_real:
            carried = self._session_gathered_evidence(task_board)
            completed = task_board.complete(active, evidence=carried or None)
        else:
            completed = task_board.complete(active)
        if not completed:
            if monitor:
                monitor.emit("board_complete_rejected", {
                    "task": active,
                    "tool": tool_name,
                    "reason": getattr(completed, "reason", "completion_rejected"),
                })
            return False

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
    def _board_is_extension_owned(task_board: TaskBoard) -> bool:
        """True when a local extension supplied a final-gated board."""
        return any(getattr(t, "completion_gate", "") == "final" for t in (getattr(task_board, "tasks", None) or []))

    def _model_owned_taskboard_enabled(self) -> bool:
        cfg = getattr(self, "config", {}) or {}
        tb = cfg.get("taskboard", {}) if isinstance(cfg.get("taskboard", {}), dict) else {}
        return bool(tb.get("model_owned", False))

    def _apply_model_plan(
        self,
        task_board: TaskBoard,
        arguments: dict,
        *,
        monitor: BackendMonitor | None = None,
    ) -> bool:
        """Let MO own a board by replacing rows with its own plan when enabled."""
        if not self._model_owned_taskboard_enabled():
            return False
        if self._board_is_extension_owned(task_board):
            if monitor:
                monitor.emit("taskboard", {"update": "set_plan_skipped_extension_board"})
            return False
        raw = arguments.get("tasks") or arguments.get("plan") or []
        if not isinstance(raw, list):
            return False
        rows: list[dict] = []
        for item in raw:
            if isinstance(item, str):
                text, kind = item.strip(), ""
            elif isinstance(item, dict):
                text = str(item.get("text") or item.get("title") or "").strip()
                kind = str(item.get("kind") or "")
            else:
                continue
            if not text:
                continue
            rows.append({
                "id": str(len(rows) + 1),
                "text": text,
                "status": "active" if not rows else "pending",
                "kind": kind,
                "completion_gate": "tool",
                "depends_on": [str(len(rows))] if rows else [],
            })
        if not rows:
            return False
        task_board.set_rows("MO plan", rows, objective=str(getattr(task_board, "objective", "") or ""))
        if monitor:
            monitor.emit("taskboard", {"update": "model_plan_set", "rows": len(rows)})
        return True

    @staticmethod
    def _task_evidence_item_for_tool(tool_name: str, arguments: dict | None = None) -> str:
        return task_evidence.taskboard_tool_evidence_item(tool_name, arguments or {})

    @staticmethod
    def _session_gathered_evidence(task_board: TaskBoard, limit: int = 8) -> list[str]:
        """Non-final evidence gathered across all rows."""
        carried: list[str] = []
        for t in task_board.tasks:
            for e in (t.evidence or []):
                es = str(e)
                if not es.startswith("final:") and es not in carried:
                    carried.append(es)
        return carried[:limit]

    def _finalize_task_board_for_answer(self, task_board: TaskBoard) -> bool:
        """Reflect final-answer truth without turning unfinished work red."""
        active = task_board.active_task_id()
        if not active:
            return False
        task = task_board.task(active)
        if not self._final_should_complete_task(task):
            return False
        completed = task_board.complete(active, evidence="final:assistant_response")
        if not completed:
            return False
        ready_id = task_board.first_ready_pending_id()
        if ready_id and task_board.dependencies_satisfied(ready_id):
            task_board.activate(ready_id)
        return True

    @staticmethod
    def _final_should_complete_task(task: object) -> bool:
        return task_evidence.final_should_complete_task(task)

    @staticmethod
    def _final_report_task_id(task_board: TaskBoard) -> str:
        return task_evidence.final_report_task_id(task_board)
