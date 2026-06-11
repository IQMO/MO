"""Gateway turn runner mixin for the MO TUI."""
from __future__ import annotations

import inspect
import re
import threading
import time

from core.provider.provider import clean_provider_error
from core.workers import ensure_worker_registry


def _strip_rich_tags(text: str) -> str:
    """Strip rich markup tags like [dim], [/dim], [green], etc. for plain text display."""
    return re.sub(r"\[/?[a-z_]+(?:\d+)?\]", "", text)


class TurnRunnerMixin:
    def _gateway_board_finished(self) -> bool:
        board = getattr(self.gateway, "last_task_board", None)
        if not board:
            return False
        try:
            return int(board.open_count()) == 0
        except Exception:
            tasks = list(getattr(board, "tasks", []) or [])
            return bool(tasks) and not any(str(getattr(task, "status", "") or "") in {"pending", "active", "blocked"} for task in tasks)

    def _run_turn_thread(self, user_input: str):
        cancel_event = threading.Event()
        self._current_turn_cancel_event = cancel_event
        main_worker_id = getattr(self, "_active_main_worker_id", "") or ""
        route_source = "user"
        if main_worker_id:
            registry = ensure_worker_registry(self.agent)
            record = registry.get(main_worker_id)
            route_source = str(getattr(record, "source", "") or "user")
            registry.update(main_worker_id, "running", "main MO turn running")
        self.busy = True
        self.activity_text = "preparing..."
        self.activity_started_at = time.time()
        self.board_text = ""
        if self._app:
            self._app.invalidate()

        try:
            show_proposal = route_source == "ghost" and self.gateway.should_show_task_board(user_input)

            def on_board_update(_rich_markup: str):
                # Gateway already called render_rich(board); the rich_markup
                # argument is informational (for monitor consumers).  We read
                # board.render() directly as the single source of display truth
                # so forged callback markup can't inject fake board text.
                board = self.gateway.last_task_board
                if board:
                    self.board_text = board.render()
                    if self._app:
                        self._app.invalidate()

            def on_board_event(event: dict):
                # Structured event — use board.render() as the single source
                # of display truth.  The event payload is for monitoring.
                board = self.gateway.last_task_board
                if board:
                    self.board_text = board.render()
                    if self._app:
                        self._app.invalidate()

            def on_token(token: str):
                self.activity_text = "receiving answer"
                if self._app:
                    self._app.invalidate()

            def on_activity(act: str):
                self.activity_text = act
                if self._show_tool_activity and "tooling" in act:
                    tool_name = act.split("(")[-1].removesuffix(")...") if "(" in act else act
                    self._add("class:dim", f"    ▸ {tool_name}")
                if self._app:
                    self._app.invalidate()

            def on_proposal(proposal: str):
                # Ghost proposal text is turn guidance, not a user-facing report.
                # Keep it silent; Gateway/main tools own visible truth.
                _ = proposal

            interim_seen: list[str] = []

            def on_assistant_text(text: str):
                # Interim prose that came alongside a tool call. This is a direct
                # answer to the user that would otherwise reach only the livelog.
                # Render it into the main transcript immediately.
                clean = str(text or "").strip()
                if not clean:
                    return
                interim_seen.append(clean)
                self._add_response_block(clean)
                if self._app:
                    self._app.invalidate()

            try:
                gateway_kwargs = {
                    "on_board_update": on_board_update,
                    "on_board_event": on_board_event,
                    "on_token": on_token,
                    "on_activity": on_activity,
                    "on_proposal": on_proposal if show_proposal else None,
                    "cancel_event": cancel_event,
                    "on_assistant_text": on_assistant_text,
                }
                gateway_sig = inspect.signature(self.gateway.run_turn)
                if "route_source" in gateway_sig.parameters or any(p.kind == p.VAR_KEYWORD for p in gateway_sig.parameters.values()):
                    gateway_kwargs["route_source"] = route_source
                result = self.gateway.run_turn(user_input, **gateway_kwargs)
            except Exception as exc:
                detail = clean_provider_error(str(exc))
                result = "\n".join([
                    "MO interface error: turn failed",
                    "  where: TUI turn runner",
                    "Fix: try again or run /status; check monitor if this repeats.",
                    f"  detail: {detail}",
                ])

            if hasattr(self.agent, "autosave_session"):
                self.agent.autosave_session()
            if hasattr(self.agent, "consume_quarantine_notice"):
                q_notice = self.agent.consume_quarantine_notice()
                if q_notice:
                    self._add("class:activity", f"  {q_notice}")
            if hasattr(self.agent, "consume_handoff_notice"):
                notice = self.agent.consume_handoff_notice()
                if notice:
                    self._add("class:activity", f"  {notice}")
            self._gateway_board_finished()
            result_clean = str(result or "").strip()
            already_shown = bool(result_clean) and result_clean in interim_seen
            if result and not str(result).startswith("[ABORTED]") and not already_shown:
                self._add_response_block(result)
            # Board stays visible until next turn (cleared at turn start).
            # The agent already completed the last active task, so the
            # board shows honest finished state — no visual gap.
            elif str(result or "").startswith("[ABORTED]"):
                self._add("class:dim", "  stopped current turn")
            if main_worker_id:
                from core.worker_runtime import summarize_worker_result

                result_text = str(result or "")
                if result_text.startswith("[ABORTED]"):
                    state = "cancelled"
                    note = "main MO turn stopped"
                else:
                    state = "blocked" if result_text.startswith(("Error:", "MO provider error:", "[MAX")) else "completed"
                    note = "main MO turn finished"
                result_summary, evidence = summarize_worker_result(result)
                ensure_worker_registry(self.agent).update(
                    main_worker_id,
                    state,
                    note,
                    result_summary=result_summary,
                    evidence=evidence,
                )
                self._active_main_worker_id = ""
        finally:
            if self._current_turn_cancel_event is cancel_event:
                self._current_turn_cancel_event = None
            self.busy = False
            self._busy_escape_count = 0
            self.activity_text = ""
            self.activity_started_at = 0.0
            # Completed taskboards leave the final MO report in transcript; incomplete
            # boards stay visible so unresolved work remains clear.
            if self._app:
                self._app.invalidate()
            self._process_next_queued_input()
