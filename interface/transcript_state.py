"""Transcript storage and viewport mixin for the MO TUI."""
from __future__ import annotations

from .input import terminal_columns
from .transcript import (
    adjusted_scroll_from_bottom,
    logical_lines_from_snapshot,
    transcript_fragments_for_viewport,
    visible_transcript_height,
    visual_rows,
)


class TranscriptStateMixin:
    def _add(self, style: str, text: str):
        """Append a styled line while preserving manual scroll position."""
        self._append_transcript_fragments([(style, text)])

    def _add_fragments_line(self, fragments: list[tuple[str, str]]):
        """Append one logical transcript line made from multiple styled fragments."""
        self._append_transcript_fragments(fragments)

    def _append_transcript_fragments(self, fragments: list[tuple[str, str]]):
        scrolled = self._transcript_scroll_from_bottom > 0
        before_rows = self._transcript_line_count() if scrolled else 0
        with self._ui_lock:
            if self._lines:
                self._lines.append(("", "\n"))
            self._lines.extend(fragments)
            self._trim_transcript_buffer()
            self._dirty = True
        if scrolled:
            after_rows = self._transcript_line_count()
            self._transcript_scroll_from_bottom += max(1, after_rows - before_rows)
        else:
            self._transcript_scroll_from_bottom = 0
        if self._app:
            self._app.invalidate()

    _TRANSCRIPT_MAX_FRAGMENTS = 12000

    def _trim_transcript_buffer(self) -> None:
        """Bound the transcript buffer so a very long session stays light on
        memory. Trims the oldest fragments down to a logical-line boundary; the
        cap is far above one screen, so the viewport and recent scrollback are
        untouched. Caller already holds self._ui_lock."""
        lines = self._lines
        cap = self._TRANSCRIPT_MAX_FRAGMENTS
        if len(lines) <= cap:
            return
        drop = len(lines) - int(cap * 0.8)
        while drop < len(lines) and lines[drop] != ("", "\n"):
            drop += 1
        if drop < len(lines):
            del lines[:drop + 1]

    def _clear_transcript(self):
        """Clear visible transcript to match a cleared backend session."""
        with self._ui_lock:
            self._lines.clear()
            self._snapshot = (("class:dim", ""),)
            self._dirty = True
        if self._app:
            self._app.invalidate()

    def _get_transcript(self):
        """Return only the visible transcript rows for a deterministic viewport."""
        fragments, self._transcript_scroll_from_bottom = transcript_fragments_for_viewport(
            self._visual_transcript_rows(),
            visible=self._visible_transcript_height(),
            scroll_from_bottom=self._transcript_scroll_from_bottom,
        )
        return fragments

    def _logical_transcript_lines(self) -> list[list[tuple[str, str]]]:
        with self._ui_lock:
            if self._dirty:
                self._snapshot = tuple(self._lines) if self._lines else (("class:dim", ""),)
                self._dirty = False
            return logical_lines_from_snapshot(self._snapshot)

    def _visual_transcript_rows(self) -> list[list[tuple[str, str]]]:
        try:
            width = self._app.output.get_size().columns if self._app else terminal_columns()
        except Exception:
            width = terminal_columns()
        wrap_width = max(20, width - 1)
        logical = self._logical_transcript_lines()
        # Word-wrapping the whole transcript is the dominant per-frame cost.
        # self._snapshot only changes when the transcript is dirtied (append/
        # clear/trim), so reuse the wrap result while snapshot + width are
        # unchanged — keeps redraw cost O(visible) instead of O(session length).
        cache = getattr(self, "_wrap_cache", None)
        if cache is not None and cache[0] is self._snapshot and cache[1] == wrap_width:
            return cache[2]
        rows = visual_rows(logical, wrap_width)
        self._wrap_cache = (self._snapshot, wrap_width, rows)
        return rows

    def _transcript_line_count(self) -> int:
        return len(self._visual_transcript_rows())

    def _visible_transcript_height(self) -> int:
        try:
            rows = self._app.output.get_size().rows if self._app else 24
        except Exception:
            rows = 24
        from .layout import input_visual_height
        from .ghost_panel import content_rows as _ghost_content_rows
        ghost_lines = getattr(self, "_ghost_panel_lines", None) or []
        ghost_inner = max(1, (rows or 80) - 4)
        ghost_content = len(_ghost_content_rows(ghost_lines, ghost_inner))
        return visible_transcript_height(
            terminal_rows=max(1, rows - 1),
            busy=bool(self.busy),
            goal_worker_active=bool(self._goal_worker_active),
            visible_goal_board_text=self._visible_goal_board_text(),
            board_text=self.board_text,
            palette_open=bool(self._palette.open),
            palette_item_count=len(self._palette._current_items()) if self._palette.open else 0,
            ghost_panel_open=bool(self._ghost_panel_open),
            ghost_expanded=bool(getattr(self, "_ghost_expanded", False)),
            ghost_content_rows=ghost_content,
            input_rows=input_visual_height(self),
        )

    def _scroll_transcript(self, delta_from_bottom: int):
        self._transcript_scroll_from_bottom = adjusted_scroll_from_bottom(
            line_count=self._transcript_line_count(),
            visible=self._visible_transcript_height(),
            current_scroll=self._transcript_scroll_from_bottom,
            delta_from_bottom=delta_from_bottom,
        )
        if self._app:
            self._app.invalidate()

    def _transcript_top(self):
        self._scroll_transcript(self._transcript_line_count())

    def _transcript_bottom(self):
        self._transcript_scroll_from_bottom = 0
        if self._app:
            self._app.invalidate()
