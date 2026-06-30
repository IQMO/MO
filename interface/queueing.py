"""Pending-input queue and steer behavior for the MO TUI."""
from __future__ import annotations

import queue
import time
import traceback
from typing import Any

from core.worker import ensure_worker_registry


class QueueingMixin:
    def _work_active(self) -> bool:
        """True when the main chat turn is busy and normal chat should queue.

        Goal work is a side-channel worker; it must not hijack main MO chat.
        """
        return self.busy

    @staticmethod
    def _command_allowed_while_working(text: str) -> bool:
        lowered = text.strip().lower()
        if lowered in {"/exit", "/quit", "/q"}:
            return True
        if lowered in {"/goal", "/g", "/goal status", "/g status", "/goal info", "/g info"}:
            return True
        return lowered in {"/goal stop", "/g stop", "/goal cancel", "/g cancel", "/goal abort", "/g abort"}

    def _run_goal_command_now(self, text: str):
        cmd_result = self.agent.process_slash_command(text)
        if cmd_result == "[GOAL_START]":
            self._start_goal_thread()
            return
        if cmd_result:
            for line in str(cmd_result).splitlines():
                self._add("", f"  {line}")
            return
        self._queue_input(text)

    def _queue_input(self, text: str, *, worker_id: str | None = None, source: str = "user", note: str = "queued for MO", notice: str | None = None):
        # Coalesce a rapid burst of submits into ONE queued message. A multi-line
        # paste on a terminal without bracketed paste (e.g. Windows Terminal)
        # arrives as separate Enter keys -- one per line -- which would otherwise
        # become N queued items (and N worker records). When plain user inputs
        # land within a short window, append to the last queued item in place
        # (the same dict is held by the pending queue), so the paste stays one
        # message and one queue entry.
        now = time.monotonic()
        last = self._last_queued_input
        if (
            notice is None and worker_id is None and source == "user"
            and last is not None and last.get("source") == "user"
            and not last.get("steer")
            and (now - getattr(self, "_last_queue_at", 0.0)) < 0.18
        ):
            last["text"] = (str(last.get("text") or "") + "\n" + text).strip()
            self._last_queue_at = now
            return
        registry = ensure_worker_registry(self.agent)
        if not worker_id:
            record = registry.create(kind="queue", source=source, route="queue", objective=text, state="accepted", note=note)
            worker_id = record.id
        item = {"text": text, "worker_id": worker_id, "steer": False, "enter_count": 1, "source": source}
        self._last_queued_input = item
        self._last_queue_at = now
        self._pending_inputs.put(item)
        self._busy_escape_count = 0
        self._add("class:dim" if notice is None else "class:activity", notice or "  Queued next · Enter to steer · Esc to cancel")

    def _drain_pending_inputs(self) -> list[Any]:
        items: list[Any] = []
        while True:
            try:
                items.append(self._pending_inputs.get_nowait())
            except queue.Empty:
                break
        return items

    def _restore_pending_inputs(self, items: list[Any]) -> None:
        for item in items:
            self._pending_inputs.put(item)

    def _advance_queued_input_intent(self) -> bool:
        item = self._last_queued_input
        if not item:
            return False
        if not item.get("steer"):
            return self._promote_last_queued_input_to_steer()
        item["enter_count"] = max(3, int(item.get("enter_count") or 2) + 1)
        return self._request_current_turn_stop_for_steer()

    def _promote_last_queued_input_to_steer(self) -> bool:
        item = self._last_queued_input
        if not item or not self._work_active():
            return False
        text = str(item.get("text") or "").strip()
        if not text or text == "[GOAL_START]":
            return False
        items = self._drain_pending_inputs()
        found = any(candidate is item for candidate in items)
        if not found:
            self._restore_pending_inputs(items)
            return False
        item["steer"] = True
        item["enter_count"] = max(2, int(item.get("enter_count") or 1) + 1)
        # Steering means the selected queued input runs next, not after older follow-ups.
        self._restore_pending_inputs([item] + [candidate for candidate in items if candidate is not item])
        worker_id = str(item.get("worker_id") or "")
        if worker_id:
            ensure_worker_registry(self.agent).update(worker_id, "accepted", "steered next input")
        self._busy_escape_count = 0
        self._add("class:activity", "  Queued request selected · Enter to stop & send now · Esc to cancel")
        return True

    def _request_current_turn_stop(self) -> bool:
        event = self._current_turn_cancel_event
        if not event or event.is_set():
            return False
        event.set()
        try:
            from core.tooling.shell_processes import cleanup_shell_processes

            cleanup_shell_processes()
        except Exception:
            traceback.print_exc()
        return True

    def _request_current_turn_stop_for_steer(self) -> bool:
        stopped = self._request_current_turn_stop()
        if stopped:
            self._add("class:activity", "  Stopping MO · queued request runs next")
        return stopped

    def _handle_busy_escape(self) -> bool:
        if not self.busy:
            self._busy_escape_count = 0
            return False
        self._busy_escape_count = min(3, int(getattr(self, "_busy_escape_count", 0) or 0) + 1)
        if self._busy_escape_count == 1 and self._cancel_last_queued_input():
            return True
        if self._busy_escape_count < 3:
            self._add("class:dim", f"  Esc {self._busy_escape_count}/3 · press Esc again to stop MO")
            return True
        stopped = self._request_current_turn_stop()
        self._busy_escape_count = 0
        self._add("class:activity", "  Stopping MO") if stopped else self._add("class:dim", "  Stop requested")
        return True

    def _restore_last_queued_input_to_editor(self) -> bool:
        """Pull the queued message back into the input editor and cancel the queue.

        Lets the operator recall a just-queued message with Up, edit/refine it,
        and re-send with Enter. No-op (returns False) when there is no queued
        item, when it's a goal start marker, or when the item isn't still queued.
        """
        item = self._last_queued_input
        if not item:
            return False
        text = str(item.get("text") or "")
        if not text or text == "[GOAL_START]":
            return False
        items = self._drain_pending_inputs()
        remaining = [candidate for candidate in items if candidate is not item]
        if len(remaining) == len(items):
            self._restore_pending_inputs(items)
            return False
        self._restore_pending_inputs(remaining)
        worker_id = str(item.get("worker_id") or "")
        if worker_id:
            ensure_worker_registry(self.agent).update(worker_id, "cancelled", "queued input pulled back to editor")
        self._last_queued_input = None
        self._busy_escape_count = 0
        self._input_buf.text = text
        self._input_buf.cursor_position = len(text)
        self._add("class:dim", "  Queued message restored — edit, Enter re-queues")
        return True

    def _cancel_last_queued_input(self) -> bool:
        item = self._last_queued_input
        if not item:
            return False
        items = self._drain_pending_inputs()
        remaining = [candidate for candidate in items if candidate is not item]
        removed = len(remaining) != len(items)
        self._restore_pending_inputs(remaining)
        if removed:
            worker_id = str(item.get("worker_id") or "")
            if worker_id:
                ensure_worker_registry(self.agent).update(worker_id, "cancelled", "queued input cancelled by user")
            self._last_queued_input = None
            self._add("class:dim", "  Queue canceled")
        return removed

    def _queue_goal_command(self, text: str):
        cmd_result = self.agent.process_slash_command(text)
        if cmd_result == "[GOAL_START]":
            self._goal_queued = True
            objective = getattr(self.agent, "_goal_pending_objective", "")
            record = ensure_worker_registry(self.agent).create(kind="goal", source="user", route="background", objective=objective, state="accepted", note="queued goal")
            self.agent._goal_worker_id = record.id
            self._pending_inputs.put({"text": "[GOAL_START]", "worker_id": record.id, "steer": False})
            self._add("class:activity", f"  goal queued: {objective[:80]}")
            self._add("class:dim", "  starts after current MO turn; Ctrl+G shows queued/running state")
            return
        self._queue_input(text)

    def _process_next_queued_input(self):
        if self._work_active():
            return
        try:
            item = self._pending_inputs.get_nowait()
        except queue.Empty:
            return
        if isinstance(item, dict):
            if item.get("cancelled"):
                self._process_next_queued_input()
                return
            queued = str(item.get("text") or "")
            worker_id = str(item.get("worker_id") or "")
            steered = bool(item.get("steer"))
            if item is self._last_queued_input:
                self._last_queued_input = None
        else:
            queued = str(item or "")
            worker_id = ""
            steered = False
        if queued == "[GOAL_START]":
            self._goal_queued = False
            if worker_id:
                self.agent._goal_worker_id = worker_id
                ensure_worker_registry(self.agent).update(worker_id, "running", "queued goal promoted to worker")
            self._add("class:dim", "  Starting queued goal")
            self._start_goal_thread()
            return
        if worker_id:
            ensure_worker_registry(self.agent).update(worker_id, "running", "queued item promoted to MO")
            self._active_main_worker_id = worker_id
        label = "Running selected request" if steered else "Running queued request"
        self._add("class:dim", f"  {label}")
        self._handle_input(queued)
