"""Pending-input queue and steer behavior for the MO TUI."""
from __future__ import annotations

import queue
import re
import time
import traceback
from typing import Any

from core.agent.slash_result import command_result
from core.session.session import assistant_result_is_incomplete
from core.worker import ensure_worker_registry


_DIRECT_INTERRUPT_ACTIONS = {"wait", "stop", "pause", "cancel"}
_DIRECT_INTERRUPT_FILLERS = {"already", "fucking", "just", "now", "please", "really"}


def direct_interrupt_action(text: str) -> str:
    """Return a direct stop action without mistaking an ordinary request for one."""
    words = re.findall(r"[a-z]+", str(text or "").casefold())
    normalized: list[str] = []
    for word in words:
        if word == "jsut":
            word = "just"
        repeated = re.sub(r"(.)\1+", r"\1", word)
        normalized.append(repeated if repeated in _DIRECT_INTERRUPT_ACTIONS else word)
    actions = [word for word in normalized if word in _DIRECT_INTERRUPT_ACTIONS]
    if len(actions) != 1:
        return ""
    if any(
        word not in _DIRECT_INTERRUPT_ACTIONS and word not in _DIRECT_INTERRUPT_FILLERS
        for word in normalized
    ):
        return ""
    return actions[0]


class QueueingMixin:
    def _work_active(self) -> bool:
        """True when the main chat turn is busy and normal chat should queue.

        Goal work is a side-channel worker; it must not hijack main MO chat.
        """
        return self.busy

    @staticmethod
    def _is_queued_goal(item: Any) -> bool:
        return isinstance(item, dict) and item.get("kind") == "goal_start"

    @classmethod
    def _is_editable_queue_item(cls, item: Any) -> bool:
        return (
            isinstance(item, dict)
            and not cls._is_queued_goal(item)
            and bool(str(item.get("text") or "").strip())
        )

    @staticmethod
    def _command_allowed_while_working(text: str) -> bool:
        from fnmatch import fnmatchcase
        from .command_registry import slash_command_spec

        parts = text.strip().lower().split(maxsplit=1)
        spec = slash_command_spec(parts[0]) if parts else None
        args = parts[1] if len(parts) > 1 else ""
        return bool(spec and any(fnmatchcase(args, pattern) for pattern in spec.busy_args))

    def _run_goal_command_now(self, text: str):
        self._run_palette_command(text, bypass_work_gate=True)

    def _queue_input(self, text: str, *, worker_id: str | None = None, source: str = "user", note: str = "queued for MO", notice: str | None = None, owner_bound: bool = False, echo: bool = True):
        if source == "user" and self.busy and direct_interrupt_action(text):
            self._handle_busy_interrupt()
            return
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
            notice is None and worker_id is None and source == "user" and not owner_bound
            and last is not None and last.get("source") == "user"
            and not last.get("owner_bound")
            and not last.get("steer")
            and (now - getattr(self, "_last_queue_at", 0.0)) < 0.18
        ):
            last["text"] = (str(last.get("text") or "") + "\n" + text).strip()
            self._last_queue_at = now
            if echo:
                self._add_user_echo(text)
            return
        registry = ensure_worker_registry(self.agent)
        if not worker_id:
            record = registry.create(kind="queue", source=source, route="queue", objective=text, state="accepted", note=note)
            worker_id = record.id
        item = {"text": text, "worker_id": worker_id, "steer": False, "source": source, "echo": False}
        if owner_bound:
            item["owner_bound"] = True
        self._last_queued_input = item
        self._last_queue_at = now
        self._pending_inputs.put(item)
        if echo:
            self._add_user_echo(text)
        set_notice = getattr(self, "_set_notice", None)
        if callable(set_notice):
            live_tool = bool(getattr(self, "_live_tool_label", ""))
            set_notice(
                (notice or (
                    "Queued behind current command · live output below · Ctrl+C stops"
                    if live_tool else "Queued next · Enter steers · Esc cancels · Alt+Up or Up edits"
                )).lstrip(),
                ttl=8.0,
            )

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

    def _promote_last_queued_input_to_steer(self) -> bool:
        item = self._last_queued_input
        if not item or not self._work_active():
            return False
        text = str(item.get("text") or "").strip()
        if not self._is_editable_queue_item(item):
            return False
        items = self._drain_pending_inputs()
        found = any(candidate is item for candidate in items)
        if not found:
            self._restore_pending_inputs(items)
            return False
        add_live_steer = getattr(self.agent, "add_live_steer", None)
        live_id = str(add_live_steer(
            text,
            source=str(item.get("source") or "user"),
            worker_id=str(item.get("worker_id") or ""),
            urgent=True,
        ) or "") if callable(add_live_steer) else ""
        if not live_id:
            self._restore_pending_inputs(items)
            self._add_line("system", [("class:task-blocked", "Steer unavailable · request remains queued")])
            return False
        item["steer"] = True
        item["live_steer_id"] = live_id
        remaining = [candidate for candidate in items if candidate is not item]
        self._restore_pending_inputs(remaining)
        self._last_queued_input = next((
            candidate for candidate in reversed(remaining)
            if self._is_editable_queue_item(candidate)
        ), None)
        self._steered_inputs = list(getattr(self, "_steered_inputs", []) or []) + [item]
        worker_id = str(item.get("worker_id") or "")
        if worker_id:
            ensure_worker_registry(self.agent).update(worker_id, "running", "waiting for current MO provider checkpoint")
        set_notice = getattr(self, "_set_notice", None)
        if callable(set_notice):
            set_notice("Steer submitted · Alt+Up or Up edits", ttl=8.0)
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

    def _handle_busy_interrupt(self) -> bool:
        """Make Ctrl+C an immediate current-turn stop, independent of Esc staging."""
        if not self.busy:
            return False
        stopped = self._request_current_turn_stop()
        self._busy_escape_count = 0
        if not stopped and self._current_turn_cancel_event and self._current_turn_cancel_event.is_set():
            self._last_busy_escape_notice = "Stopping MO · queued follow-ups preserved"
            return True
        notice = "Stopping MO · queued follow-ups preserved" if stopped else "Stop already requested"
        self._last_busy_escape_notice = notice
        style = "class:activity" if stopped else "class:dim"
        self._add_line("system", [(style, notice)])
        return True

    def _handle_busy_escape(self) -> bool:
        if not self.busy:
            self._busy_escape_count = 0
            self._last_busy_escape_notice = "Nothing to stop"
            return False
        if self._current_turn_cancel_event and self._current_turn_cancel_event.is_set():
            self._busy_escape_count = 0
            queue_notice = "Queued input canceled · stopping MO"
            steer_notice = "Pending steer canceled · stopping MO"
            if self._cancel_last_queued_input(notice=queue_notice):
                self._last_busy_escape_notice = queue_notice
            elif self._cancel_last_live_steer(notice=steer_notice):
                self._last_busy_escape_notice = steer_notice
            else:
                self._last_busy_escape_notice = "Stopping MO"
            return True
        self._busy_escape_count = min(3, int(getattr(self, "_busy_escape_count", 0) or 0) + 1)
        if self._busy_escape_count == 1:
            queue_notice = "Queued input canceled · Esc 1/3 · press Esc twice more to stop MO"
            if self._cancel_last_queued_input(notice=queue_notice):
                self._last_busy_escape_notice = queue_notice
                return True
            steer_notice = "Pending steer canceled · Esc 1/3 · press Esc twice more to stop MO"
            if self._cancel_last_live_steer(notice=steer_notice):
                self._last_busy_escape_notice = steer_notice
                return True
        if self._busy_escape_count < 3:
            remaining = "twice more" if self._busy_escape_count == 1 else "again"
            notice = f"Esc {self._busy_escape_count}/3 · press Esc {remaining} to stop MO"
            self._last_busy_escape_notice = notice
            self._add_line("system", [("class:dim", notice)])
            return True
        stopped = self._request_current_turn_stop()
        self._busy_escape_count = 0
        notice = "Stopping MO" if stopped else "Stop already requested"
        self._last_busy_escape_notice = notice
        style = "class:activity" if stopped else "class:dim"
        self._add_line("system", [(style, notice)])
        return True

    def _restore_last_queued_input_to_editor(self) -> bool:
        """Restore the latest queued or steered message to the input editor.

        A steer that is still pending is removed from the Agent first. If the
        current turn already consumed it, copy the exact text for a corrective
        follow-up without claiming that the applied instruction was undone.
        """
        item = self._last_queued_input
        if not item:
            return self._restore_last_live_steer_to_editor()
        text = str(item.get("text") or "")
        if not self._is_editable_queue_item(item):
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
        self._last_queued_input = next((
            candidate for candidate in reversed(remaining)
            if self._is_editable_queue_item(candidate)
        ), None)
        self._busy_escape_count = 0
        self._input_buf.text = text
        self._input_buf.cursor_position = len(text)
        self._add_line("system", [("class:activity", "Queued message restored — edit, Enter re-queues")])
        return True

    def _restore_last_live_steer_to_editor(self) -> bool:
        steered = list(getattr(self, "_steered_inputs", []) or [])
        if not steered:
            return False
        item = steered[-1]
        text = str(item.get("text") or "")
        live_id = str(item.get("live_steer_id") or "")
        if not text or not live_id:
            return False
        remove_live_steer = getattr(self.agent, "remove_live_steer", None)
        removed = remove_live_steer(live_id) if callable(remove_live_steer) else None
        self._steered_inputs = steered[:-1]
        self._input_buf.text = text
        self._input_buf.cursor_position = len(text)
        self._busy_escape_count = 0
        worker_id = str(item.get("worker_id") or "")
        if removed is not None:
            if worker_id:
                ensure_worker_registry(self.agent).update(worker_id, "cancelled", "unconsumed steer pulled back to editor")
            notice = "Steered message restored before MO applied it — edit, Enter sends the correction"
        else:
            self._consumed_steer_workers = list(
                getattr(self, "_consumed_steer_workers", []) or []
            ) + [item]
            notice = "Steer was already applied · copied to editor for a corrective follow-up"
        self._add_line("system", [("class:activity", notice)])
        return True

    def _cancel_last_queued_input(self, *, notice: str = "Queue canceled") -> bool:
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
            self._last_queued_input = next((
                candidate for candidate in reversed(remaining)
                if self._is_editable_queue_item(candidate)
            ), None)
            self._add_line("system", [("class:task-blocked", notice)])
        return removed

    def _cancel_last_live_steer(self, *, notice: str = "Steer canceled") -> bool:
        steered = list(getattr(self, "_steered_inputs", []) or [])
        if not steered:
            return False
        item = steered[-1]
        remove_live_steer = getattr(self.agent, "remove_live_steer", None)
        removed = remove_live_steer(str(item.get("live_steer_id") or "")) if callable(remove_live_steer) else None
        if removed is None:
            return False
        self._steered_inputs = steered[:-1]
        worker_id = str(item.get("worker_id") or "")
        if worker_id:
            ensure_worker_registry(self.agent).update(worker_id, "cancelled", "live steer cancelled by user")
        self._add_line("system", [("class:task-blocked", notice)])
        return True

    def _requeue_unconsumed_live_steers(self, result: Any = "") -> None:
        """Promote missed steers and close records consumed by the finished turn."""
        steered = list(getattr(self, "_steered_inputs", []) or [])
        consumed = list(getattr(self, "_consumed_steer_workers", []) or [])
        if not steered and not consumed:
            return
        remove_live_steer = getattr(self.agent, "remove_live_steer", None)
        requeued: list[dict[str, Any]] = []
        for item in steered:
            removed = (
                remove_live_steer(str(item.get("live_steer_id") or ""))
                if callable(remove_live_steer) else None
            )
            if removed is None:
                consumed.append(item)
                continue
            item["steer"] = False
            item.pop("live_steer_id", None)
            worker_id = str(item.get("worker_id") or "")
            if worker_id:
                ensure_worker_registry(self.agent).update(worker_id, "accepted", "steer missed final checkpoint; queued next")
            requeued.append(item)
        self._steered_inputs = []
        self._consumed_steer_workers = []
        result_text = str(result or "")
        if result_text.startswith("[ABORTED]"):
            consumed_state, consumed_note = "cancelled", "current MO turn stopped after consuming steer"
        elif assistant_result_is_incomplete(result_text):
            consumed_state, consumed_note = "blocked", "current MO turn failed after consuming steer"
        else:
            consumed_state, consumed_note = "completed", "steer applied to current MO turn"
        closed: set[str] = set()
        for item in consumed:
            worker_id = str(item.get("worker_id") or "")
            if worker_id and worker_id not in closed:
                ensure_worker_registry(self.agent).update(
                    worker_id,
                    consumed_state,
                    consumed_note,
                )
                closed.add(worker_id)
        if not requeued:
            return
        pending = self._drain_pending_inputs()
        self._restore_pending_inputs(requeued + pending)
        if self._last_queued_input is None:
            self._last_queued_input = requeued[-1]
        set_notice = getattr(self, "_set_notice", None)
        if callable(set_notice):
            set_notice("Late steer preserved for the next request", ttl=4.0)

    def _queue_goal_command(self, text: str):
        raw_result = self.agent.process_slash_command(text)
        if raw_result is None:
            self._queue_input(text)
            return
        result = command_result(raw_result)
        if result.action == "goal_start":
            self._goal_queued = True
            objective = getattr(self.agent, "_goal_pending_objective", "")
            record = ensure_worker_registry(self.agent).create(kind="goal", source="user", route="background", objective=objective, state="accepted", note="queued goal")
            self.agent._goal_worker_id = record.id
            self._pending_inputs.put({"kind": "goal_start", "text": "", "worker_id": record.id, "steer": False})
            self._palette.show_result(
                text,
                f"Goal queued: {objective[:80]}\nStarts after the current MO turn.",
                kind="notice",
            )
            if self._app:
                self._app.invalidate()
            return
        self._dispatch_slash_command_result(result, command_text=text)

    def _process_next_queued_input(self):
        refresh_stop = getattr(self, "_refresh_stop", None)
        if self._work_active() or (refresh_stop is not None and refresh_stop.is_set()):
            return
        try:
            item = self._pending_inputs.get_nowait()
        except queue.Empty:
            return
        queued_goal = self._is_queued_goal(item)
        if isinstance(item, dict):
            if item.get("cancelled"):
                self._process_next_queued_input()
                return
            queued = str(item.get("text") or "")
            worker_id = str(item.get("worker_id") or "")
            if item is self._last_queued_input:
                self._last_queued_input = None
        else:
            queued = str(item or "")
            worker_id = ""
        if queued_goal:
            self._goal_queued = False
            if worker_id:
                self.agent._goal_worker_id = worker_id
                ensure_worker_registry(self.agent).update(worker_id, "running", "queued goal promoted to worker")
            self._add_line("system", [("class:dim", "Starting queued goal")])
            self._start_goal_thread()
            return
        if worker_id:
            ensure_worker_registry(self.agent).update(worker_id, "running", "queued item promoted to MO")
            self._active_main_worker_id = worker_id
        echo = bool(item.get("echo", True)) if isinstance(item, dict) else True
        if isinstance(item, dict) and item.get("owner_bound"):
            self._handle_input(queued, owner_bound=True, echo=echo)
        else:
            self._handle_input(queued, echo=echo)
