"""Canonical Goal UI lifecycle mixin for the MO TUI."""
from __future__ import annotations

import threading
import time
import traceback
from typing import Any

from core.provider.provider import clean_provider_error
from core.workers import ensure_worker_registry
from .formatting import format_k


def _goal_interface_error(action: str, exc: Exception) -> str:
    detail = clean_provider_error(str(exc))
    return "\n".join([
        f"MO goal error: {action} failed",
        "  where: TUI goal runner",
        "Fix: try /goal status, then resume or restart the goal if needed.",
        f"  detail: {detail}",
    ])


class GoalUiMixin:
    """Goal background/foreground, queue promotion, and visual lifecycle methods."""

    def _show_active_goal(self):
        if not self._goal_running and getattr(self.agent, "_goal_active", False):
            self._resume_goal_thread()
            return
        if not self._goal_running:
            self._add("class:dim", "  no active goal to continue")
            return
        self._goal_backgrounded = False
        self._goal_done_unread = False
        self._restore_goal_board_text()
        runner = getattr(self.agent, "_goal_runner", None)
        if runner:
            try:
                status = runner.status()
            except Exception:
                status = f"[GOAL] running · {self._goal_elapsed_text()}"
            for line in status.splitlines():
                self._add("class:activity", f"  {line}")
        else:
            self._add("class:activity", f"  goal running · {self._goal_elapsed_text()}")
        if self._app:
            self._app.invalidate()

    def _toggle_goal_background(self):
        """Ctrl+G: toggle goal progress board visibility."""
        if not self._goal_running and not self._goal_worker_active and getattr(self.agent, "_goal_active", False):
            self._resume_goal_thread()
            return
        if not self._goal_running and not self._goal_worker_active:
            has_goal_board = bool(self._goal_board_text) or bool(getattr(self.agent, "_goal_plan", None) and self._goal_backgrounded)
            if has_goal_board:
                self._goal_backgrounded = not self._goal_backgrounded
                if self._goal_backgrounded:
                    self._goal_board_text = ""
                else:
                    self._goal_done_unread = False
                    self._restore_goal_board_text()
                if self._app:
                    self._app.invalidate()
                return
            if self._goal_queued:
                objective = getattr(self.agent, "_goal_pending_objective", "")
                self._add("class:activity", f"  goal queued · {objective[:80]}")
                self._add("class:dim", "  waiting for current MO turn to finish")
                return
            self._add("class:dim", "  no active goal · use /goal <task> to start")
            return
        self._goal_backgrounded = not self._goal_backgrounded
        if self._goal_backgrounded:
            self._goal_board_text = ""
        else:
            self._goal_done_unread = False
            self._restore_goal_board_text()
        if self._app:
            self._app.invalidate()

    def _start_goal_thread(self):
        """Start a new goal in a background thread."""
        objective = getattr(self.agent, "_goal_pending_objective", "")
        budget = getattr(self.agent, "_goal_pending_budget", None)
        if not objective:
            self._add("class:dim", "  no objective set")
            return

        registry = ensure_worker_registry(self.agent)
        worker_id = getattr(self.agent, "_goal_worker_id", "") or ""
        if not worker_id or not registry.get(worker_id):
            record = registry.create(kind="goal", source="user", route="background", objective=objective, state="accepted", note="goal accepted")
            self.agent._goal_worker_id = record.id

        self._goal_running = True
        self._goal_worker_active = True
        self._goal_queued = False
        self._goal_backgrounded = False
        self._goal_started_at = time.time()
        self._goal_stage = "starting"
        self._goal_board_text = ""

        threading.Thread(
            target=self._run_goal_loop,
            args=(objective, budget),
            daemon=True,
        ).start()

    def _resume_goal_thread(self):
        """Resume a paused backend goal without creating a new plan."""
        plan = getattr(self.agent, "_goal_plan", None)
        registry = ensure_worker_registry(self.agent)
        worker_id = getattr(self.agent, "_goal_worker_id", "") or ""
        if not worker_id or not registry.get(worker_id):
            objective = getattr(plan, "objective", "resumed goal") if plan else "resumed goal"
            record = registry.create(kind="goal", source="user", route="background", objective=objective, state="accepted", note="goal resumed")
            self.agent._goal_worker_id = record.id
        self._goal_running = True
        self._goal_worker_active = True
        self._goal_queued = False
        self._goal_backgrounded = False
        self._goal_started_at = getattr(plan, "started_at", 0.0) or time.time()
        self._goal_stage = "resuming"
        self._goal_board_text = ""
        self._add("class:activity", f"  goal resumed · {self._goal_elapsed_text()}")
        threading.Thread(target=self._run_existing_goal_loop, daemon=True).start()

    def _run_existing_goal_loop(self):
        """Continue an already-active backend goal until it reaches a terminal state."""
        try:
            runner = getattr(self.agent, "_goal_runner", None)
            if not runner:
                self._goal_finish("No goal runner.")
                return
            while getattr(self.agent, "_goal_active", False):
                if not self._goal_running:
                    break
                self._goal_stage = "iterating"
                try:
                    result = runner.continue_goal()
                except Exception as exc:
                    self._goal_finish(_goal_interface_error("continue", exc))
                    return
                if any(result.startswith(m) for m in ("[✓ DONE]", "[✗ BLOCKED]", "[PAUSED]", "[GOAL STOPPED]")):
                    self._goal_finish(result)
                    return
                self._goal_show_progress(result)
                time.sleep(0.5)
            if self._goal_running:
                self._goal_finish("Goal loop ended.")
        finally:
            self._goal_running = False
            self._goal_worker_active = False
            if self._app:
                self._app.invalidate()
            self._process_next_queued_input()

    def _run_goal_loop(self, objective: str, budget):
        """Run the full goal lifecycle in a background thread."""
        from core.goal import GoalRunner

        try:
            if not self.agent._goal_runner:
                self.agent._goal_runner = GoalRunner(self.agent)
            runner = self.agent._goal_runner

            # Start goal (runs first iteration)
            try:
                result = runner.start(objective, budget=budget)
            except Exception as exc:
                self._goal_finish(_goal_interface_error("start", exc))
                return

            if any(result.startswith(m) for m in ("[✓ DONE]", "[✗ BLOCKED]", "[PAUSED]", "[GOAL STOPPED]")):
                self._goal_finish(result)
                return
            self._goal_show_progress(result)

            # Continue iterations while goal is active
            while getattr(self.agent, "_goal_active", False):
                # Check if user stopped
                if not self._goal_running:
                    break

                self._goal_stage = "iterating"
                if self._app:
                    self._app.invalidate()

                try:
                    result = runner.continue_goal()
                except Exception as exc:
                    self._goal_finish(_goal_interface_error("continue", exc))
                    return

                # If result starts with a terminal marker, we're done
                if any(result.startswith(m) for m in ("[✓ DONE]", "[✗ BLOCKED]", "[PAUSED]", "[GOAL STOPPED]")):
                    self._goal_finish(result)
                    return
                self._goal_show_progress(result)

                # Small delay between iterations
                time.sleep(0.5)

            # If we exit the loop without finishing
            if self._goal_running:
                self._goal_finish("Goal loop ended.")
        finally:
            self._goal_running = False
            self._goal_worker_active = False
            if self._app:
                self._app.invalidate()
            self._process_next_queued_input()

    def _goal_show_progress(self, text: str):
        """Refresh live goal board without appending every progress tick."""
        runner = self.agent._goal_runner
        if runner:
            plan = getattr(self.agent, "_goal_plan", None)
            if plan:
                try:
                    from core.tasking.task_board import record_snapshot
                    from interface.task_board_view import render_plain
                    board = runner.to_task_board(plan)
                    record_snapshot(board, "updated", source="goal")
                    self._goal_board_text = "" if self._goal_backgrounded else render_plain(board)
                except Exception as exc:
                    # UI rendering must not crash the background goal thread or
                    # dump a traceback into the live terminal. Keep a visible,
                    # conservative status row instead.
                    self._goal_board_text = "" if self._goal_backgrounded else f"Goal progress unavailable — {type(exc).__name__}"
                    self._set_notice("Goal progress display error; goal worker still running")
        elif self._goal_backgrounded:
            self._goal_board_text = ""
        if self._app:
            self._app.invalidate()

    def _restore_goal_board_text(self):
        runner = getattr(self.agent, "_goal_runner", None)
        plan = getattr(self.agent, "_goal_plan", None)
        if runner and plan:
            try:
                from interface.task_board_view import render_plain
                self._goal_board_text = render_plain(runner.to_task_board(plan))
            except Exception:
                traceback.print_exc()

    @staticmethod
    def _goal_finish_summary(text: str) -> str:
        raw = str(text or "").strip()
        first = raw.splitlines()[0].strip() if raw else "Goal finished"
        if first.startswith("[✓ DONE]"):
            return first.replace("[✓ DONE]", "Goal finished", 1)
        if first.startswith("[✗ BLOCKED]"):
            return first.replace("[✗ BLOCKED]", "Goal blocked", 1)
        if first.startswith("[PAUSED]"):
            return first.replace("[PAUSED]", "Goal paused", 1)
        if first.startswith("[GOAL STOPPED]"):
            return first.replace("[GOAL STOPPED]", "Goal stopped", 1)
        return first or "Goal finished"

    def _goal_finish_line(self, summary: str, plan: Any | None = None) -> str:
        return f"{summary} · {self._goal_saved_tokens_text(plan)} · complexity {self._goal_complexity_text(plan)}"

    def _goal_saved_tokens_text(self, plan: Any | None = None) -> str:
        saved = 0
        plan_chars = int(getattr(plan, "context_savings_chars", 0) or 0) if plan is not None else 0
        if plan_chars > 0:
            saved = max(0, round(plan_chars / 4))
        else:
            estimator = getattr(self.agent, "_compression_saved_tokens_estimate", None)
            if callable(estimator):
                try:
                    saved = int(estimator() or 0)
                except Exception:
                    saved = 0
            if saved <= 0:
                try:
                    compression = int(getattr(self.agent, "compression_total_saved", 0) or 0)
                    truncation = int(getattr(self.agent, "truncation_total_saved", 0) or 0)
                    saved = max(0, round((compression + truncation) / 4))
                except Exception:
                    saved = 0
        marker = "~" if saved else ""
        return f"saved {marker}{format_k(saved)} tokens"

    @staticmethod
    def _goal_complexity_text(plan: Any | None) -> str:
        objective = str(getattr(plan, "objective", "") or "")
        try:
            from core.work_patterns import estimate_work_complexity, select_work_pattern

            estimated = estimate_work_complexity(objective)
            if estimated != "simple":
                return estimated
            pattern = select_work_pattern(objective)
            complexity = str(getattr(pattern, "complexity", "") or "").strip()
            if complexity:
                return complexity
        except Exception:
            traceback.print_exc()
        try:
            step_count = len(getattr(plan, "steps", []) or [])
        except Exception:
            step_count = 0
        return "moderate" if step_count > 3 else "simple"

    @staticmethod
    def _goal_finish_reason(text: str) -> str:
        lines = [line.strip() for line in str(text or "").splitlines()[1:] if line.strip()]
        return "; ".join(lines[:2])

    @staticmethod
    def _goal_evidence_prefix(item: str) -> str:
        return str(item or "").split(":", 1)[0].strip()

    @staticmethod
    def _goal_shorten_evidence_detail(prefix: str, detail: str) -> str:
        clean = " ".join(str(detail or "").split())
        lowered = clean.lower()
        if prefix in {"shell", "test_runner"}:
            if "pytest" in lowered:
                return "pytest"
            if "py_compile" in lowered or "compileall" in lowered:
                return "python compile check"
            if "import " in lowered and "print(" in lowered and len(clean) > 80:
                return "python smoke check"
        if len(clean) > 92:
            return clean[:89].rstrip() + "…"
        return clean

    @staticmethod
    def _goal_format_evidence(item: str) -> str:
        raw = str(item or "").strip()
        if not raw:
            return ""
        if raw == "verification_result:passed":
            return "verification passed"
        if raw == "verification_result:failed":
            return "verification failed"
        if ":" not in raw:
            return raw
        prefix, detail = raw.split(":", 1)
        detail = GoalUiMixin._goal_shorten_evidence_detail(prefix, detail.strip())
        labels = {
            "read_file": "read",
            "write_file": "wrote",
            "edit_file": "edited",
            "grep": "searched",
            "find_files": "found",
            "project_bridge": "checked",
            "git_status": "checked git status",
            "shell": "ran",
            "test_runner": "ran",
            "web_fetch": "fetched",
            "web_snapshot": "snapshotted",
            "web_search": "searched web",
        }
        label = labels.get(prefix, prefix.replace("_", " "))
        return f"{label} {detail}".strip()

    @staticmethod
    def _goal_unique(items: list[str], *, limit: int = 4) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            clean = " ".join(str(item or "").split())
            if not clean:
                continue
            key = clean.lower()
            if key in seen:
                continue
            seen.add(key)
            result.append(clean)
            if len(result) >= limit:
                break
        return result

    def _goal_report_sections(self, text: str, plan: Any | None, summary: str) -> list[tuple[str, str]]:
        steps = list(getattr(plan, "steps", []) or [])
        completed = [step for step in steps if str(getattr(step, "status", "") or "") == "completed"]
        work_words = ("build", "write", "implement", "fix", "apply", "update", "change", "create", "report", "produce", "formulate")
        work_done = [str(getattr(step, "title", "") or "").strip() for step in completed if any(word in str(getattr(step, "title", "") or "").lower() for word in work_words)]
        if not work_done:
            work_done = [str(getattr(step, "title", "") or "").strip() for step in completed]

        reference_prefixes = {"read_file", "write_file", "edit_file", "grep", "find_files", "project_bridge", "git_status", "web_fetch", "web_snapshot", "web_search"}
        check_prefixes = {"shell", "test_runner"}
        references: list[str] = []
        checks: list[str] = []
        caveats: list[str] = []
        for step in steps:
            title = str(getattr(step, "title", "") or "")
            title_lower = title.lower()
            for evidence in list(getattr(step, "evidence", []) or []):
                raw = str(evidence or "").strip()
                prefix = self._goal_evidence_prefix(raw)
                formatted = self._goal_format_evidence(raw)
                if prefix in reference_prefixes:
                    references.append(formatted)
                if prefix in check_prefixes or raw.startswith("verification_result:") or "verify" in title_lower:
                    checks.append(formatted)
                if "failed" in raw.lower():
                    caveats.append(formatted)
            blocker = str(getattr(step, "blocker", "") or "").strip()
            if blocker:
                caveats.append(blocker)

        reason = self._goal_finish_reason(text)
        if reason and not summary.startswith("Goal finished"):
            caveats.insert(0, reason)
        feedback = str(getattr(plan, "auditor_feedback", "") or "").strip()
        if feedback:
            caveats.append(feedback)

        sections: list[tuple[str, str]] = []
        flow = self._goal_flow_text(steps)
        if flow:
            sections.append(("Flow", flow))
        did = self._goal_unique(work_done, limit=3)
        if did:
            sections.append(("Did", "; ".join(did)))
        refs = self._goal_unique(references, limit=4)
        if refs:
            sections.append(("References", "; ".join(refs)))
        check_items = self._goal_unique(checks, limit=3)
        if check_items:
            sections.append(("Checks", "; ".join(check_items)))
        caveat_items = self._goal_unique(caveats, limit=4)
        if caveat_items:
            sections.append(("Caveats", "; ".join(caveat_items)))
        return sections

    @staticmethod
    def _goal_flow_text(steps: list[Any]) -> str:
        if len(steps) <= 1:
            return ""
        from core.tasking.task_board import status_marker
        parts: list[str] = []
        for step in steps[:4]:
            status = str(getattr(step, "status", "") or "pending")
            title = " ".join(str(getattr(step, "title", "") or "task").split())
            if len(title) > 34:
                title = title[:31].rstrip() + "…"
            parts.append(f"{status_marker(status)} {title}")
        if len(steps) > 4:
            parts.append(f"… {len(steps) - 4} more")
        return " → ".join(parts)

    def _add_goal_report_to_transcript(self, text: str, plan: Any | None, summary: str) -> None:
        self._add("class:goal-detail", "  Goal report")
        for label, value in self._goal_report_sections(text, plan, summary):
            self._add_fragments_line([
                ("class:goal-detail", f"  {label}: "),
                ("class:mo-response", value),
            ])

    def _goal_finish(self, text: str):
        """Mark goal as finished and show final result."""
        was_backgrounded = self._goal_backgrounded
        self._goal_running = False
        self._goal_backgrounded = False
        self._goal_stage = ""
        self._goal_board_text = ""

        terminal = any(str(text or "").startswith(m) for m in ("[✓ DONE]", "[✗ BLOCKED]", "[PAUSED]", "[GOAL STOPPED]"))
        summary = self._goal_finish_summary(text)
        plan = getattr(self.agent, "_goal_plan", None)
        try:
            runner = getattr(self.agent, "_goal_runner", None)
            if runner and plan:
                from core.gateway import record_terminal_snapshot
                board = runner.to_task_board(plan)
                event = "completed" if str(getattr(plan, "state", "") or "") == "completed" else "blocked" if str(text or "").startswith("[✗ BLOCKED]") else "abandoned"
                state = "completed" if event == "completed" else "blocked" if event == "blocked" else "abandoned"
                record_terminal_snapshot(board, event, source="goal", state=state)
        except Exception:
            traceback.print_exc()
        final_line = self._goal_finish_line(summary, plan)
        if was_backgrounded:
            notification = final_line
            self._goal_done_unread = True
            self._record_ghost_history("notification", "", notification)
            self._ghost_panel_lines = [("class:ghost-hint", notification)]
        elif terminal:
            self._add_goal_report_to_transcript(text, plan, summary)
            self._add("class:goal-detail", f"  {final_line}")
        elif text:
            for line in text.splitlines():
                self._add("class:mo-response", f"  {line}")

        # Terminal finish intentionally hides the live goal board; the transcript
        # report owns the final user-facing summary while backend goal truth stays
        # in the GoalPlan/worker registry.
        self._goal_board_text = ""

        if not terminal:
            self._add("", "")
        if self._app:
            self._app.invalidate()
        self._process_next_queued_input()
