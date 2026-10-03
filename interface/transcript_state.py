"""Transcript storage and viewport mixin for the MO TUI."""
from __future__ import annotations

import os

from .terminal_metrics import TerminalMetricsMixin
from .transcript_grammar import TURN_KINDS, line_for
from .transcript import (
    adjusted_scroll_from_bottom,
    logical_lines_from_snapshot,
    terminal_display_fragments,
    transcript_fragments_for_viewport,
    visible_transcript_height,
    visual_rows,
)


class TranscriptStateMixin(TerminalMetricsMixin):
    def _add(self, style: str, text: str):
        """Append a styled line while preserving manual scroll position."""
        self._append_transcript_fragments([(style, text)])

    def _add_fragments_line(self, fragments: list[tuple[str, str]]):
        """Append one logical transcript line made from multiple styled fragments."""
        self._append_transcript_fragments(fragments)

    def _add_fragments_block(
        self,
        fragment_lines: list[list[tuple[str, str]]],
        *,
        blank_before: bool = False,
    ) -> None:
        """Append logical lines in one canonical native-output commit.

        Final response/report blocks use this seam so prompt-toolkit suspends and
        redraws the inline application once per block. Incremental tool and
        reasoning producers keep the line API above and remain immediately visible.
        """
        lines = [list(fragments) for fragments in fragment_lines]
        if not lines:
            return
        combined: list[tuple[str, str]] = []
        for index, fragments in enumerate(lines):
            if index:
                combined.append(("", "\n"))
            combined.extend(fragments or [("", "")])
        self._append_transcript_fragments(combined, blank_before=blank_before)

    def _add_line(self, kind: str, fragments: list[tuple[str, str]], *, blank_before: bool | None = None):
        """Append one transcript line under the unified line grammar.

        *kind* selects the gutter (and whether a blank line breathes before it);
        *fragments* are the content. This is the single seam every producer routes
        its left edge through so gutters cannot drift apart — see
        ``interface/transcript_grammar.py``."""
        if blank_before is None:
            blank_before = kind in TURN_KINDS
        if blank_before:
            self._ensure_blank_line()
        self._add_fragments_line(line_for(kind, list(fragments)))

    def _transcript_tail_is_blank_locked(self) -> bool:
        """Return whether the canonical tail is empty; caller holds `_ui_lock`."""
        if not self._lines:
            return True
        tail = ""
        index = len(self._lines) - 1
        while index >= 0 and self._lines[index] != ("", "\n"):
            tail = self._lines[index][1] + tail
            index -= 1
        return not tail.strip()

    def _ensure_blank_line(self):
        """Append one blank logical line unless the transcript tail is already blank.

        Used to give turns room (a blank before each user/MO block) without ever
        stacking two blanks. The canonical transcript owns this grammar in both
        native-scrollback and managed-viewport presentation modes."""
        with self._ui_lock:
            if self._transcript_tail_is_blank_locked():
                return
        self._add("", "")

    def _replace_last_transcript_fragments(self, fragments: list[tuple[str, str]], *, required_style: str = "") -> bool:
        """Replace the last logical transcript line when the managed viewport owns it."""
        if self._scrollback_transcript_enabled():
            return False
        with self._ui_lock:
            if not self._lines:
                return False
            start = len(self._lines) - 1
            while start > 0 and self._lines[start - 1] != ("", "\n"):
                start -= 1
            if required_style and not any(style == required_style for style, _text in self._lines[start:]):
                return False
            self._lines[start:] = list(fragments)
            self._dirty = True
        self._request_background_redraw()
        return True

    def _add_ansi_block(self, ansi: str):
        """Append a multi-line ANSI visual (image, chart, table, structure tree) to
        the transcript as fragment lines — rendered inline by prompt_toolkit, in the
        right place, instead of dumped to the raw terminal above the prompt."""
        if not ansi:
            return
        try:
            from prompt_toolkit.formatted_text import ANSI, to_formatted_text
        except Exception:
            return
        # visual kind: a full-bleed block with no gutter, given room to breathe
        # with one blank line before and after (unified transcript grammar).
        self._ensure_blank_line()
        line: list[tuple[str, str]] = []
        for style, text in to_formatted_text(ANSI(ansi)):
            segments = str(text).split("\n")
            for idx, segment in enumerate(segments):
                if idx:
                    self._add_fragments_line(line)
                    line = []
                if segment:
                    line.append((style, segment))
        if line:
            self._add_fragments_line(line)
        self._ensure_blank_line()

    def _scrollback_transcript_enabled(self) -> bool:
        """Return whether finalized lines use the terminal's native scrollback.

        Native scrollback is the normal presentation: it preserves MO's styled
        transcript while the terminal owns full-history wheel scrolling,
        Shift+PageUp/Shift+PageDown, selection, and copy. Set
        ``runtime.scrollback_transcript: false`` only for the compatibility
        managed viewport. MO Shell always uses that existing viewport because
        its native surface projects the current VT screen rather than exposing
        the host terminal's scrollback buffer.
        """
        if getattr(self, "_scrollback_flag_cache", None) is None:
            try:
                cfg = getattr(getattr(self, "agent", None), "config", None) or {}
                value = (cfg.get("runtime") or {}).get("scrollback_transcript", True)
                self._scrollback_flag_cache = (
                    bool(value) and os.environ.get("MO_SHELL_TERMINAL") != "1"
                )
            except Exception:
                self._scrollback_flag_cache = (
                    os.environ.get("MO_SHELL_TERMINAL") != "1"
                )
        return self._scrollback_flag_cache

    def _emit_to_scrollback(self, fragments: list[tuple[str, str]]) -> None:
        """Print styled transcript lines above the app (native scrollback).

        Uses print_formatted_text under the app's active patch_stdout, so it is
        thread-safe from the turn-runner thread and lands in the terminal's own
        scrollback — where the mouse wheel and click-drag selection work. Reuse
        the canonical transcript wrapper before printing so the host terminal
        never splits ordinary prose mid-word or loses hanging indentation."""
        try:
            from prompt_toolkit import print_formatted_text
            from prompt_toolkit.formatted_text import FormattedText
            rows = visual_rows(
                logical_lines_from_snapshot(tuple(fragments)),
                max(20, self._terminal_columns() - 1),
            )
            wrapped: list[tuple[str, str]] = []
            for index, row in enumerate(rows):
                if index:
                    wrapped.append(("", "\n"))
                wrapped.extend(terminal_display_fragments(row))
            # `/skin` replaces the live application's Style object. Read it for
            # every committed line so native scrollback follows the active skin
            # instead of freezing the style used by the startup header.
            style = getattr(getattr(self, "_app", None), "style", None)
            if style is None:
                from .theme import build_tui_style
                style = build_tui_style()
            from .theme import build_tui_color_depth
            # `print_formatted_text` schedules one run_in_terminal render on the
            # application's loop. Keep OSC 11 inside that same commit so it lands
            # after the transcript rows and before prompt-toolkit redraws the app;
            # a second callback could run before the asynchronous print itself.
            from .terminal_host import skin_terminal_background_sequence
            background_sequence = skin_terminal_background_sequence()
            if background_sequence:
                wrapped.append(("[ZeroWidthEscape]", background_sequence))
            print_formatted_text(
                FormattedText(wrapped),
                style=style,
                color_depth=build_tui_color_depth(),
            )
        except Exception:
            try:
                print("".join(str(t) for _s, t in fragments))
            except Exception:
                pass

    def _append_transcript_fragments(
        self,
        fragments: list[tuple[str, str]],
        *,
        blank_before: bool = False,
    ) -> None:
        native_scrollback = self._scrollback_transcript_enabled()
        defer_scrollback = native_scrollback and self._native_scrollback_deferred()
        scrolled = not native_scrollback and self._transcript_scroll_from_bottom > 0
        before_rows = self._transcript_line_count() if scrolled else 0
        with self._ui_lock:
            committed = list(fragments)
            if blank_before and not self._transcript_tail_is_blank_locked():
                committed.insert(0, ("", "\n"))
            if self._lines:
                self._lines.append(("", "\n"))
            self._lines.extend(committed)
            self._trim_transcript_buffer()
            self._dirty = True
            if defer_scrollback:
                pending = self._native_scrollback_pending
                pending.append(tuple(committed))
                pending_count = (
                    self._native_scrollback_pending_fragments + len(committed) + 1
                )
                if pending_count > self._TRANSCRIPT_MAX_FRAGMENTS:
                    target = int(self._TRANSCRIPT_MAX_FRAGMENTS * 0.8)
                    while pending and pending_count > target:
                        dropped = pending.pop(0)
                        pending_count -= len(dropped) + 1
                self._native_scrollback_pending_fragments = max(0, pending_count)
        if native_scrollback:
            # Printing is append-only, but the same bounded canonical fragments
            # remain available to Live Control and transcript snapshots.
            self._transcript_scroll_from_bottom = 0
            if not defer_scrollback:
                self._emit_to_scrollback(committed)
        elif scrolled:
            after_rows = self._transcript_line_count()
            self._transcript_scroll_from_bottom += max(1, after_rows - before_rows)
        else:
            self._transcript_scroll_from_bottom = 0
        if not native_scrollback or defer_scrollback:
            self._request_background_redraw()

    def _native_scrollback_deferred(self) -> bool:
        """Hold native lines only while a workspace surface owns the screen."""
        return self._workspace.active

    def _flush_deferred_scrollback(self) -> bool:
        """Commit deferred native lines once drafting/split presentation ends."""
        if self._native_scrollback_deferred():
            return False
        with self._ui_lock:
            pending = list(self._native_scrollback_pending)
            self._native_scrollback_pending = []
            self._native_scrollback_pending_fragments = 0
        if not pending:
            return False
        combined: list[tuple[str, str]] = []
        for fragments in pending:
            if combined:
                combined.append(("", "\n"))
            combined.extend(fragments)
        self._emit_to_scrollback(combined)
        return True

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
            self._native_scrollback_pending = []
            self._native_scrollback_pending_fragments = 0
            self._snapshot = (("class:dim", ""),)
            self._dirty = True
        if self._app:
            self._app.invalidate()

    def _get_transcript(self):
        """Return only the visible transcript rows for a deterministic viewport."""
        if self._scrollback_transcript_enabled():
            # The terminal already owns finalized transcript rendering. Keep the
            # flexible layout window empty so it acts only as the spacer that
            # anchors live controls to the bottom of the terminal.
            return [("class:dim", "")]
        fragments, self._transcript_scroll_from_bottom = transcript_fragments_for_viewport(
            self._visual_transcript_rows(),
            visible=self._visible_transcript_height(),
            scroll_from_bottom=self._transcript_scroll_from_bottom,
            anchor_to_live_panels=self._transcript_has_live_panel_below(),
        )
        return fragments

    def _transcript_has_live_panel_below(self) -> bool:
        """Whether short transcript content should meet a visible live panel."""
        foreground_goal = bool(getattr(self, "_goal_worker_active", False)) and not bool(
            getattr(self, "_goal_backgrounded", False)
        )
        return bool(
            getattr(self, "busy", False)
            or foreground_goal
            or self._visible_goal_board_text()
            or (getattr(self, "board_text", "") and not foreground_goal)
        )

    def _logical_transcript_lines(self) -> list[list[tuple[str, str]]]:
        with self._ui_lock:
            if self._dirty:
                self._snapshot = tuple(self._lines) if self._lines else (("class:dim", ""),)
                self._dirty = False
            return logical_lines_from_snapshot(self._snapshot)

    def _visual_transcript_rows(self) -> list[list[tuple[str, str]]]:
        wrap_width = max(20, self._terminal_columns() - 1)
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
        # Scrollback mode: finalized content lives in the terminal's native
        # scrollback, so the managed transcript window collapses to nothing and
        # the app is just the input + status strip.
        if self._scrollback_transcript_enabled():
            return 0
        rows = self._terminal_rows()
        from .layout import input_visual_height
        main_board_text = "" if (
            bool(getattr(self, "_goal_worker_active", False))
            and not bool(getattr(self, "_goal_backgrounded", False))
        ) else self.board_text
        activity_panel_rows = 0
        counter = getattr(self, "_activity_panel_row_count", None)
        if callable(counter):
            try:
                activity_panel_rows = int(counter())
            except Exception:
                activity_panel_rows = 0
        live_tool_rows = int(getattr(self, "_live_tool_row_count", lambda: 0)() or 0)
        inline_margin = (
            0 if bool(getattr(self, "_terminal_full_screen", False)) else 1
        )
        return visible_transcript_height(
            terminal_rows=max(1, rows - inline_margin - live_tool_rows),
            busy=bool(self.busy),
            goal_worker_active=bool(self._goal_worker_active),
            visible_goal_board_text=self._visible_goal_board_text(),
            board_text=main_board_text,
            palette_open=bool(self._palette.open),
            palette_item_count=len(self._palette._current_items()) if self._palette.open else 0,
            input_rows=input_visual_height(self),
            workspace_rows=activity_panel_rows,
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
