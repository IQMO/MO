"""MO agent task-board management mixin — extracted from core/agent.py (DEVMODE05 Phase 2)."""

from pathlib import Path

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
        # Bind the active DEVMODE session dir to whatever dir THIS run actually writes
        # its artifacts into, so the economy writer targets it explicitly (never the
        # newest dir by mtime — see _write_devmode_economy_record).
        if tool_name in ("write_file", "edit_file"):
            self._bind_active_devmode_dir_from_write(arguments or {})

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

        # Authoritative economy record at this self-protocol closeout — written by
        # the runtime from the live monitor, so it can never be estimated, stale, or
        # hand-faked (observed: model wrote helper-format numbers without running it).
        self._write_devmode_economy_record()

        if not task_board:
            return False

        # C1: the terminal report (gated above) closes the remaining phase rows.
        # Carry the turn's real gathered evidence onto each closed row so the board
        # reflects what actually happened instead of a hollow identical `final:`
        # token. The rows carry the session's real evidence (not per-phase-specific):
        # precise per-phase attribution would require fragile phase auto-advance for
        # no honesty gain, so it is intentionally not done.
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
            else:
                # Backfill a row the model self-completed via complete_task that carries
                # no real evidence (only a `final:` token, or empty) — typically a
                # diagnostic/reasoning phase row with no tool of its own. Attach the
                # session's gathered evidence so the closeout contract gate sees real
                # per-row truth. Without this the row stays empty and the whole-board
                # contract gate rejects every turn (it has no circuit breaker) — an
                # unbounded CONTRACT GATE loop (observed live mo-1782079519). Backfills
                # only when the session actually gathered evidence, so a zero-work
                # closeout that marked rows done with nothing behind them is still caught.
                real = [str(e) for e in (task.evidence or []) if not str(e).startswith("final:")]
                if not real and carried:
                    task.evidence = [*(task.evidence or []), *carried]
                    changed = True
        return changed

    def _bind_active_devmode_dir_from_write(self, arguments: dict) -> None:
        """Bind the active DEVMODE session dir to the dir THIS run writes its artifacts
        into. Captured from each write_file/edit_file path under memory/devmode/<stamp>/
        so the economy writer can target the explicit active dir instead of guessing the
        newest dir by mtime (which let an aborted run overwrite a prior session). The
        protocol pack at operator/devmode/ is NOT a session dir and is skipped."""
        try:
            from ..path_defaults import mo_home
            path = str((arguments or {}).get("path") or (arguments or {}).get("file_path") or "")
            norm = path.replace("\\", "/")
            low = norm.lower()
            key = "memory/devmode/"
            i = low.find(key)
            if i == -1 or "operator/devmode" in low:
                return
            stamp = norm[i + len(key):].split("/", 1)[0]  # preserve case (Linux is case-sensitive)
            if not stamp or not stamp[:1].isdigit():
                return
            candidate = mo_home() / "memory" / "devmode" / stamp
            if candidate.is_dir():
                self._active_devmode_session_dir = candidate
        except Exception:
            pass

    def _write_devmode_economy_record(self) -> None:
        """Write the authoritative economy record (provider/tool/error/compression
        counts from the live monitor) into the EXPLICIT active DEVMODE session dir
        bound from this run's own artifact writes — NEVER the newest dir by mtime.

        The mtime heuristic let an aborted later run (which created no dir of its own)
        overwrite a PRIOR session's economy: it corrupted T2121's authentic 29/66 with
        a stray 23/43 sourced from the aborted run's monitor file. If no active dir is
        bound this run, REFUSE to write rather than guess. The model never authors these
        numbers. Best-effort: a failure here must never break closeout.

        Note (acceptable residual): the binding lives on the agent (per-process), so a
        fresh process that stalls at boot without writing any artifact has no binding and
        correctly refuses — fixing the observed cross-process corruption."""
        try:
            from ..backend_monitor import GHOST_SURFACES, economy_summary, format_economy_record
            target = getattr(self, "_active_devmode_session_dir", None)
            if target is None or not Path(target).is_dir():
                return  # no explicit binding this run → refuse; never fall back to mtime
            # Logical-run scoping: exclude interleaved Ghost/desktop turns that share this
            # process's monitor file so the run's economy isn't inflated by them. Handoff
            # segments keep route_source=user, so they remain counted (grouped together).
            summary = economy_summary(exclude_surfaces=GHOST_SURFACES)
            (Path(target) / "economy.md").write_text(
                format_economy_record(summary), encoding="utf-8"
            )
            # Reconcile the model-authored economy line in summary.md. The model
            # writes summary.md BEFORE the closeout completes, so its hand-counted
            # numbers go stale by the closeout delta (observed: summary 26/63 vs
            # authoritative economy.md 29/66). The economy fix made economy.md
            # runtime-owned; this extends the same single-source-of-truth to the
            # one place the model still restates counts, so the two files can never
            # disagree. Surgical: only the numeric counts on the economy line are
            # rewritten — any model narration on that line is preserved.
            AgentTaskBoard._reconcile_summary_economy_counts(Path(target) / "summary.md", summary)
        except Exception:
            pass

    @staticmethod
    def _reconcile_summary_economy_counts(summary_path, summary: dict) -> None:
        """Overwrite stale provider/tool/error/compression counts on the economy
        line of a DEVMODE05 summary.md with the authoritative monitor figures."""
        try:
            import re
            if not summary_path.exists():
                return
            text = summary_path.read_text(encoding="utf-8", errors="replace")
            subs = (
                (r"\d+(?=\s+provider request)", summary.get("provider_requests", 0)),
                (r"\d+(?=\s+tool calls)", summary.get("tool_calls", 0)),
                (r"\d+(?=\s+tool error)", summary.get("tool_errors", 0)),
                (r"\d+(?=\s+compression)", summary.get("compression_events", 0)),
            )

            def fix_line(line: str) -> str:
                if "provider request" not in line:  # the economy line is the only one with this phrase
                    return line
                for pat, val in subs:
                    line = re.sub(pat, str(val), line)
                return line

            new_text = "\n".join(fix_line(ln) for ln in text.split("\n"))
            if new_text != text:
                summary_path.write_text(new_text, encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _final_should_complete_task(task: object) -> bool:
        return task_evidence.final_should_complete_task(task)

    @staticmethod
    def _final_report_task_id(task_board: TaskBoard) -> str:
        return task_evidence.final_report_task_id(task_board)
