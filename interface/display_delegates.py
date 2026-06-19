"""Display delegate mixin for `MoTui` visual/status fragments."""
from __future__ import annotations

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
)
from .ghost_panel import (
    content_rows as ghost_content_rows,
    max_scroll as ghost_max_scroll,
    panel_dimensions as ghost_panel_dimensions,
    panel_fragments as ghost_panel_fragments,
)
from .task_board_view import task_board_fragments_from_text
from .moon_visuals import calculate_moon_glow
from .terminal_metrics import TerminalMetricsMixin
from .transcript import _board_max_height as _transcript_board_max_height


class DisplayDelegatesMixin(TerminalMetricsMixin):
    def _board_max_height(self) -> int:
        """Dynamic max rows a board window may occupy.

        Delegates to the canonical implementation in transcript.py.
        """
        return _transcript_board_max_height(self._terminal_rows())

    def _ghost_panel_dimensions(self) -> tuple[int, int]:
        cols = self._terminal_columns()
        return ghost_panel_dimensions(max(1, cols - 1))

    def _ghost_panel_content_rows(self) -> list[list[tuple[str, str]]]:
        _total_width, inner = self._ghost_panel_dimensions()
        return ghost_content_rows(self._ghost_panel_lines, inner)

    def _max_ghost_scroll(self) -> int:
        _total_width, inner = self._ghost_panel_dimensions()
        return ghost_max_scroll(self._ghost_panel_open, self._ghost_panel_lines, inner, expanded=bool(getattr(self, "_ghost_expanded", False)))

    def _ghost_route_bridge_text(self, now: float | None = None) -> str:
        text = str(getattr(self, "_ghost_route_flash_text", "") or "").strip()
        return text or "Route accepted"

    def _get_activity_fragments(self):
        if bool(self.busy) and time.time() <= float(getattr(self, "_ghost_route_flash_until", 0.0) or 0.0):
            return [("class:ghost-route", f" {self._ghost_route_bridge_text()} ")]
            
        moon_style = ""
        if getattr(self.agent, "_moon_mode_active", False):
            moon_style = calculate_moon_glow(time.time())
            
        return activity_fragments(
            busy=bool(self.busy),
            goal_worker_active=bool(self._goal_worker_active),
            goal_backgrounded=bool(self._goal_backgrounded),
            activity_text=self.activity_text,
            activity_started_at=self.activity_started_at,
            board_text=self.board_text,
            goal_board_text=self._goal_board_text,
            goal_started_at=self._goal_started_at,
            moon_style=moon_style,
        )

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
        return self._get_task_board_fragments(
            self.board_text,
            root_prefix="     ",
            skip_summary=bool(self.busy),
            scroll_from_bottom=self._board_scroll_from_bottom,
            visible_rows=self._board_max_height(),
        )

    def _get_task_board_fragments(self, board_text: str, *, root_prefix: str = "     ", skip_summary: bool = False, scroll_from_bottom: int = 0, visible_rows: int = 0):
        return task_board_fragments_from_text(
            board_text,
            root_prefix=root_prefix,
            skip_summary=skip_summary,
            scroll_from_bottom=scroll_from_bottom,
            visible_rows=visible_rows,
        )

    def _get_footer_fragments(self):
        cols = max(20, self._terminal_columns())
        
        moon_style = ""
        if getattr(self.agent, "_moon_mode_active", False):
            moon_style = calculate_moon_glow(time.time())
            
        return footer_fragments(self._footer_left_fragments(), columns=cols, right=self._workers_status_text(), right_style=moon_style)

    def _footer_left_fragments(self) -> list[tuple[str, str]]:
        return footer_left_fragments(self.agent, notice_frag=self._footer_notification_fragment())

    def _footer_notification_fragment(self) -> tuple[str, str] | None:
        return footer_notification_fragment(self._notification_items())

    def _notification_items(self) -> list[tuple[str, str]]:
        try:
            pending = self._pending_inputs.qsize()
        except Exception:
            pending = 0
        return notification_items(
            ghost_unread_count=self._ghost_unread_count,
            goal_worker_active=bool(self._goal_worker_active),
            goal_done_unread=bool(self._goal_done_unread),
            pending_count=pending,
            prt_done_unread=bool(getattr(self, "_prt_done_unread", False)),
            goal_progress=goal_progress_text(getattr(self, "agent", None)),
        )

    def _goal_elapsed_text(self) -> str:
        return goal_elapsed_text(self._goal_started_at)

    def _set_notice(self, text: str, ttl: float = 4.0):
        self._notice_text = str(text or "")
        self._notice_until = time.time() + max(0.5, float(ttl or 4.0))
        if self._app:
            self._app.invalidate()

    def _get_status_bar_fragments(self):
        if self.busy:
            return [("", "")]
        if self._goal_worker_active and not self._goal_backgrounded:
            return [("", "")]  # activity spinner handles foreground goal
        cols = self._terminal_columns()
            
        idle_style = "class:notification-idle"
        notifications = self._notification_items()
        for style, _ in notifications:
            if "notification-prt" in style:
                idle_style = "class:notification-prt"
                break
            elif "notification-goal" in style:
                idle_style = "class:notification-goal"

        # Hints: show rotating hint when enabled and no notice is active
        hint_text = ""
        hints_enabled = getattr(self.agent, "_hints_enabled", True)
        if hints_enabled and not (self._notice_text and time.time() <= self._notice_until):
            try:
                from .hints import current_hint
                hint_text = current_hint()
            except Exception:
                pass

        left_frags, notice_active = status_left_fragments(
            notice_text=self._notice_text,
            notice_until=self._notice_until,
            idle_style=idle_style,
            hint_text=hint_text,
        )
        if not notice_active:
            self._notice_text = ""
        return status_bar_fragments(left_frags, "", columns=max(1, cols - 1))

    def _get_ghost_panel_fragments(self):
        total_width, inner = self._ghost_panel_dimensions()
        fragments, self._ghost_scroll_from_bottom = ghost_panel_fragments(
            panel_open=self._ghost_panel_open,
            panel_lines=self._ghost_panel_lines,
            total_width=total_width,
            inner=inner,
            scroll_from_bottom=self._ghost_scroll_from_bottom,
            expanded=bool(getattr(self, "_ghost_expanded", False)),
        )
        return fragments

    def _scroll_ghost(self, delta_from_bottom: int):
        self._ghost_scroll_from_bottom = max(0, min(self._max_ghost_scroll(), self._ghost_scroll_from_bottom + delta_from_bottom))
        if self._app:
            self._app.invalidate()

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
