"""Display delegate mixin for `MoTui` visual/status fragments."""
from __future__ import annotations

import threading
import time

from .activity import (
    activity_fragments,
    footer_fragments,
    footer_left_fragments,
    footer_notification_fragment,
    goal_elapsed_text,
    goal_progress_text,
    notification_items,
    status_bar_fragments,
    status_left_fragments,
    status_line_mode,
    workspace_active_fragments,
)
from .formatting import explainer_activity_lines, is_mo_method_activity
from .task_board_view import completed_board_duration_text, task_board_fragments_from_text
from .visual_effects import calculate_method_glow
from .terminal_metrics import TerminalMetricsMixin
from .transcript import _board_max_height as _transcript_board_max_height


_COMMIT_REMINDER_PREFIX = "💡 Remember to commit and push"
_COMMIT_IDLE_HINT = "Commit and push verified changes."


class DisplayDelegatesMixin(TerminalMetricsMixin):
    def _activity_is_own_method(self) -> bool:
        """Return whether the executing tool or runtime phase is a MO method."""
        snapshot = getattr(self, "_live_tool_process_snapshot", lambda: {})()
        if explainer_activity_lines(str(snapshot.get("output_tail") or "")):
            return True
        return is_mo_method_activity(str(getattr(self, "activity_text", "") or ""))

    def _method_signal_style(self) -> str:
        """Return the warm pulse only while a foreground MO method is active."""
        foreground_work = bool(
            getattr(self, "busy", False)
            or (
                getattr(self, "_goal_worker_active", False)
                and not getattr(self, "_goal_backgrounded", False)
            )
        )
        if foreground_work and self._activity_is_own_method():
            return calculate_method_glow(time.time())
        return ""

    def _board_max_height(self) -> int:
        """Dynamic max rows a board window may occupy.

        Delegates to the canonical implementation in transcript.py.
        """
        return _transcript_board_max_height(self._terminal_rows())

    def _get_activity_fragments(self):
        live = list(getattr(self, "_get_live_tool_fragments", lambda: [])() or [])
        method_style = self._method_signal_style()
        snapshot = getattr(self, "_live_tool_process_snapshot", lambda: {})()
        progress = explainer_activity_lines(str(snapshot.get("output_tail") or ""))

        frags = activity_fragments(
            busy=bool(self.busy),
            goal_worker_active=bool(self._goal_worker_active),
            goal_backgrounded=bool(self._goal_backgrounded),
            activity_text=progress[0] if progress else self.activity_text,
            activity_started_at=self.activity_started_at,
            board_text=self.board_text,
            goal_board_text=self._goal_board_text,
            goal_started_at=self._goal_started_at,
            edit_additions=int(getattr(self, "_turn_edit_additions", 0) or 0),
            edit_deletions=int(getattr(self, "_turn_edit_deletions", 0) or 0),
            method_style=method_style,
        )
        return live + ([("", "\n")] if live else []) + frags

    def _activity_panel_on(self) -> bool:
        """Runtime override for the true-worker activity panel wins over config."""
        override = getattr(self.agent, "_activity_enabled_override", None)
        if override is not None:
            return bool(override)
        try:
            from .workspace import activity_enabled
            return activity_enabled(getattr(self.agent, "config", None))
        except Exception:
            return False

    def _activity_panel_active(self) -> bool:
        """True only while the full canonical worker panel is visible."""
        return (
            not self._session_workspace_active()
            and self._activity_panel_row_count() > 0
        )

    def _session_workspace_active(self) -> bool:
        """True only after this window owns at least one child MO session."""
        controller = getattr(self, "_workspace", None)
        return bool(controller is not None and controller.active)

    def _activity_panel_row_count(self) -> int:
        try:
            if not self._activity_panel_on():
                return 0
            from .workspace_panel import activity_row_count
            return int(activity_row_count(self.agent))
        except Exception:
            return 0

    def _get_activity_panel_fragments(self):
        try:
            from .workspace_panel import activity_panel_fragments
            return activity_panel_fragments(self.agent, columns=self._terminal_columns()) or [("", "")]
        except Exception:
            return [("", "")]

    def _visible_goal_board_text(self) -> str:
        if self._goal_backgrounded:
            return ""
        return self._goal_board_text

    def _get_goal_board_fragments(self):
        skip_summary = bool(self._goal_worker_active and not self._goal_backgrounded)
        return self._get_task_board_fragments(
            self._visible_goal_board_text(),
            root_prefix="  ⌞  ",
            skip_summary=skip_summary,
            scroll_from_bottom=self._goal_board_scroll_from_bottom,
            visible_rows=self._board_max_height(),
        )

    def _get_board_fragments(self):
        board = getattr(getattr(self, "gateway", None), "last_task_board", None)
        completion_duration = "" if self.busy else completed_board_duration_text(board)
        return self._get_task_board_fragments(
            self.board_text,
            root_prefix="     ",
            skip_summary=bool(self.busy),
            scroll_from_bottom=self._board_scroll_from_bottom,
            visible_rows=self._board_max_height(),
            completion_duration=completion_duration,
            edit_additions=(
                int(getattr(self, "_turn_edit_additions", 0) or 0)
                if completion_duration else 0
            ),
            edit_deletions=(
                int(getattr(self, "_turn_edit_deletions", 0) or 0)
                if completion_duration else 0
            ),
        )

    def _get_task_board_fragments(
        self,
        board_text: str,
        *,
        root_prefix: str = "     ",
        skip_summary: bool = False,
        scroll_from_bottom: int = 0,
        visible_rows: int = 0,
        completion_duration: str = "",
        edit_additions: int = 0,
        edit_deletions: int = 0,
    ):
        return task_board_fragments_from_text(
            board_text,
            root_prefix=root_prefix,
            skip_summary=skip_summary,
            scroll_from_bottom=scroll_from_bottom,
            visible_rows=visible_rows,
            completion_duration=completion_duration,
            edit_additions=edit_additions,
            edit_deletions=edit_deletions,
            columns=max(1, self._terminal_columns() - 1),
        )

    def _get_footer_fragments(self, *, columns: int | None = None):
        cols = (
            max(1, int(columns))
            if columns is not None
            else max(20, self._terminal_columns())
        )

        panel_active = self._activity_panel_active()
        try:
            worker_fragments = (
                workspace_active_fragments(self.agent)
                if self._activity_panel_on() and not panel_active
                else []
            )
        except Exception:
            worker_fragments = []
        left_fragments = self._footer_left_fragments()
        if worker_fragments:
            return footer_fragments(
                left_fragments, columns=cols, right_fragments=worker_fragments
            )
        return footer_fragments(
            left_fragments,
            columns=cols,
            right="" if panel_active else self._workers_status_text(),
            right_style="class:activity",
        )

    def _footer_left_fragments(self) -> list[tuple[str, str]]:
        return footer_left_fragments(self.agent, notice_frag=self._footer_notification_fragment())

    def _footer_notification_fragment(self) -> tuple[str, str] | None:
        return footer_notification_fragment(self._notification_items())

    def _notification_items(self) -> list[tuple[str, str]]:
        try:
            pending = self._pending_inputs.qsize()
        except Exception:
            pending = 0
        # The footer owns compact operational notifications only. Learning
        # lifecycle events are rendered by the transcript activity lane; the
        # marker filter also prevents fallback-buffer duplication here.
        agent = getattr(self, "agent", None)
        transient_notes: tuple[str, ...] = ()
        if agent is not None:
            try:
                getter = getattr(agent, "recent_status_notes", None)
                recent_notes = tuple(
                    str(note or "").strip()
                    for note in (getter() or ())
                    if str(note or "").strip()
                ) if callable(getter) else ()
                commit_reminder = next(
                    (
                        note
                        for note in recent_notes
                        if note.casefold().startswith(
                            _COMMIT_REMINDER_PREFIX.casefold()
                        )
                    ),
                    "",
                )
                transient_notes = tuple(
                    note for note in recent_notes
                    if not note.startswith("◈")
                    and not note.casefold().startswith(
                        _COMMIT_REMINDER_PREFIX.casefold()
                    )
                )
                if commit_reminder:
                    # Keep this TUI-only presentation state out of Agent and
                    # show the shorter reminder in the idle status line.
                    self._contextual_idle_hint = _COMMIT_IDLE_HINT
                    self._contextual_idle_hint_until = time.time() + 20.0
            except Exception:
                pass
        steer_count = 0
        steer_counter = getattr(agent, "pending_live_steer_count", None)
        if callable(steer_counter):
            try:
                steer_count = int(steer_counter())
            except Exception:
                steer_count = 0
        items = notification_items(
            goal_worker_active=bool(self._goal_worker_active),
            goal_done_unread=bool(self._goal_done_unread),
            pending_count=pending,
            steer_count=steer_count,
            prt_done_unread=getattr(self, "_prt_done_unread", False),
            goal_progress=goal_progress_text(getattr(self, "agent", None)),
            transient_notes=transient_notes,
        )
        transfer_status = str(
            getattr(self.agent, "_file_transfer_status", "") or ""
        ).strip()
        if transfer_status and time.time() <= float(
            getattr(self.agent, "_file_transfer_status_until", 0.0) or 0.0
        ):
            items.insert(0, ("class:notification-learning", transfer_status))
        cached_until = float(
            getattr(self.agent, "_file_transfer_notice_until", 0.0) or 0.0
        )
        cached_notice = str(
            getattr(self.agent, "_file_transfer_notice", "") or ""
        ).strip()
        if cached_notice and time.time() <= cached_until:
            items.insert(0, ("class:notification-learning", cached_notice))
        elif (
            agent is not None
            and isinstance(
                (getattr(agent, "config", {}) or {}).get("file_transfer"),
                dict,
            )
            and (
                (getattr(agent, "config", {}) or {})
                .get("file_transfer", {})
                .get("enabled")
                is True
            )
            and time.time()
            >= float(getattr(agent, "_file_transfer_notice_poll_at", 0.0) or 0.0)
            and not bool(
                getattr(agent, "_file_transfer_notice_poll_in_flight", False)
            )
        ):
            agent._file_transfer_notice_poll_at = time.time() + 2.0
            agent._file_transfer_notice_poll_in_flight = True

            def poll_transfer_notices() -> None:
                try:
                    from core.runtime.instance import get_instance_id
                    from core.transfer.presence import claim_transfer_notices

                    notices = claim_transfer_notices(
                        getattr(agent, "config", {}) or {},
                        surface="terminal",
                        instance_id=get_instance_id(),
                    )
                    if notices:
                        first = notices[0]
                        more = len(notices) - 1
                        text = f"Received {first['name']}"
                        if more:
                            text += f" +{more}"
                        agent._file_transfer_notice = text
                        agent._file_transfer_notice_until = time.time() + 20.0
                        app = getattr(self, "_app", None)
                        if app is not None:
                            app.invalidate()
                except Exception:
                    pass
                finally:
                    agent._file_transfer_notice_poll_in_flight = False

            try:
                threading.Thread(
                    target=poll_transfer_notices,
                    name="mo-transfer-notices",
                    daemon=True,
                ).start()
            except Exception:
                agent._file_transfer_notice_poll_in_flight = False
        return items

    def _goal_elapsed_text(self) -> str:
        return goal_elapsed_text(self._goal_started_at)

    def _set_notice(self, text: str, ttl: float = 4.0):
        self._notice_text = str(text or "")
        self._notice_until = time.time() + max(0.5, float(ttl or 4.0))
        if self._app:
            self._app.invalidate()

    def _get_status_bar_fragments(self, *, columns: int | None = None):
        current = time.time()
        line_mode = status_line_mode(self, now=current)
        notice_active_now = line_mode == "notice"
        if line_mode == "hidden":
            return [("", "")]
        cols = (
            max(1, int(columns))
            if columns is not None
            else self._terminal_columns()
        )
            
        idle_style = "class:notification-idle"
        notifications = self._notification_items()
        for style, _ in notifications:
            if "notification-prt" in style:
                idle_style = "class:notification-prt"
                break
            if "notification-goal" in style:
                idle_style = "class:notification-goal"

        hint_text = ""
        hints_enabled = getattr(self.agent, "_hints_enabled", True)
        line_count = getattr(self, "_transcript_line_count", None)
        visible_height = getattr(self, "_visible_transcript_height", None)
        scrollback_enabled = getattr(self, "_scrollback_transcript_enabled", None)
        native_scrollback = callable(scrollback_enabled) and scrollback_enabled()
        transcript_overflows = (
            not native_scrollback
            and not getattr(self, "_transcript_scroll_from_bottom", 0)
            and callable(line_count) and callable(visible_height)
            and line_count() > visible_height()
        )
        if transcript_overflows and not notice_active_now:
            # Session history sits above the fold and you're at the bottom — surface
            # the keyboard scroll keys (the managed viewport is keyboard-scrolled).
            hint_text = "↑ PageUp / PageDown to scroll the session"
        elif not notice_active_now:
            contextual_hint = str(getattr(self, "_contextual_idle_hint", "") or "")
            contextual_until = float(
                getattr(self, "_contextual_idle_hint_until", 0.0) or 0.0
            )
            if contextual_hint and current <= contextual_until:
                hint_text = contextual_hint
            elif hints_enabled:
                try:
                    from .hints import current_hint
                    hint_text = current_hint(now=current)
                except Exception:
                    pass

        left_frags, notice_active = status_left_fragments(
            notice_text=self._notice_text,
            notice_until=self._notice_until,
            idle_style=idle_style,
            hint_text=hint_text,
            now=current,
        )
        if not notice_active:
            self._notice_text = ""
        return status_bar_fragments(left_frags, "", columns=max(1, cols - 1))

    def _max_goal_board_scroll(self) -> int:
        text = self._visible_goal_board_text()
        if not text:
            return 0
        lines = text.splitlines()
        visible = self._board_max_height()
        return max(0, len(lines) - visible)

    def _max_board_scroll(self) -> int:
        text = self.board_text
        if not text:
            return 0
        lines = text.splitlines()
        visible = self._board_max_height()
        return max(0, len(lines) - visible)

    def _scroll_goal_board(self, delta_from_bottom: int):
        max_scroll = self._max_goal_board_scroll()
        self._goal_board_scroll_from_bottom = max(0, min(max_scroll, self._goal_board_scroll_from_bottom + delta_from_bottom))
        if self._app:
            self._app.invalidate()

    def _scroll_board(self, delta_from_bottom: int):
        max_scroll = self._max_board_scroll()
        self._board_scroll_from_bottom = max(0, min(max_scroll, self._board_scroll_from_bottom + delta_from_bottom))
        if self._app:
            self._app.invalidate()

    def _scroll_boards(self, delta_from_bottom: int):
        """Scroll both boards (only one is typically visible at a time)."""
        self._scroll_goal_board(delta_from_bottom)
        self._scroll_board(delta_from_bottom)

    def _get_separator_fragments(self):
        cols = max(20, self._terminal_columns())
        return [("class:separator", "─" * cols)]
