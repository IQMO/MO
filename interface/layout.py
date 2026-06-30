"""Prompt-toolkit layout construction for MO TUI."""
from __future__ import annotations

import re
import time
from typing import Any

from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.layout.containers import ConditionalContainer, Float, FloatContainer, HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import BeforeInput, Processor, Transformation
from prompt_toolkit.filters import Condition
from prompt_toolkit.utils import get_cwidth

INPUT_MAX_ROWS = 5
INPUT_PLACEHOLDER = "Type a message · /help for commands"
# Shared startup-hint line for both the full TUI banner and the native loop.
STARTUP_HINT = "Type /help for commands, /status for details"


def prompt_prefix() -> HTML:
    return HTML("<ansicyan><b>*</b></ansicyan> ")


class PlaceholderProcessor(Processor):
    """Render dim placeholder text on the first line while the input is empty.

    Keeps the protected `*` prompt marker untouched; only fills the otherwise
    blank editor line so an empty composer reads as an input field, not as a
    missing/absent section.
    """

    def __init__(self, text: str = INPUT_PLACEHOLDER) -> None:
        self.text = text

    def apply_transformation(self, transformation_input):
        buffer = transformation_input.buffer_control.buffer
        if buffer.text == "" and transformation_input.lineno == 0:
            return Transformation([("class:input-placeholder", self.text)])
        return Transformation(transformation_input.fragments)


class EnhanceHintProcessor(Processor):
    """Append the Ctrl+E enhance hint inline, trailing the typed message.

    Renders at the end of the last input line so the hint sits at the end of the
    sentence instead of on a separate row below the composer. Visibility/threshold
    are owned by ``enhance_hint_fragments``; this processor only places it.
    """

    def __init__(self, tui: Any) -> None:
        self.tui = tui

    def apply_transformation(self, transformation_input):
        fragments = transformation_input.fragments
        last_line = max(0, transformation_input.document.line_count - 1)
        if transformation_input.lineno != last_line:
            return Transformation(fragments)
        hint = enhance_hint_fragments(self.tui)
        if not hint:
            return Transformation(fragments)
        return Transformation(list(fragments) + list(hint))


_EXTRATHINK_RE = re.compile(r"\bextrathink\b", re.IGNORECASE)


class ExtrathinkShineProcessor(Processor):
    """Live per-frame shine on the ``extrathink`` trigger as it's typed.

    Recolours only the matched characters (one fragment per char, no spacing or
    width change — bold is never toggled), so the word shimmers in the composer
    without resizing. The refresh loop invalidates while the buffer holds the word.
    """

    def __init__(self, tui: Any) -> None:
        self.tui = tui

    def apply_transformation(self, transformation_input):
        fragments = transformation_input.fragments
        text = "".join(t for _, t in fragments)
        if "extrathink" not in text.lower():
            return Transformation(fragments)
        from .moon_visuals import shine_fragments
        chars: list[list] = []
        for style, run in fragments:
            for ch in run:
                chars.append([style, ch])
        ts = time.time()
        for m in _EXTRATHINK_RE.finditer(text):
            shine = shine_fragments(text[m.start():m.end()], ts)
            for offset, (st, _ch) in enumerate(shine):
                idx = m.start() + offset
                if 0 <= idx < len(chars):
                    chars[idx][0] = st
        return Transformation([(st, ch) for st, ch in chars])


def input_visual_height(tui: Any, *, max_rows: int = INPUT_MAX_ROWS) -> int:
    """Return the visible input editor height, capped to keep transcript context."""
    if hasattr(tui, "_terminal_columns"):
        cols = tui._terminal_columns()
    else:
        cols = 80
    width = max(12, int(cols or 80) - 4)
    text = str(getattr(getattr(tui, "_input_buf", None), "text", "") or "")
    rows = 0
    for line in (text.splitlines() or [""]):
        rows += max(1, (get_cwidth(line) + width - 1) // width)
    return max(1, min(int(max_rows or INPUT_MAX_ROWS), rows))


def input_window_height(tui: Any) -> Dimension:
    rows = input_visual_height(tui)
    return Dimension(min=1, preferred=rows, max=INPUT_MAX_ROWS)


ENHANCE_HINT_MIN_WORDS = 25
"""Only suggest Ctrl+E once the message is substantial enough to benefit from a
rewrite — short asks don't need it, and the hint shouldn't flash on every keystroke."""


def enhance_hint_fragments(tui: Any) -> list:
    """Contextual hint trailing the typed message at the end of the input line.

    "Ctrl+E enhance message" once a real message of at least
    ``ENHANCE_HINT_MIN_WORDS`` words is typed; after Ctrl+E applies, "Esc to
    revert back". Hidden when busy, empty, or on a slash command.
    """
    if getattr(tui, "busy", False):
        return []
    if getattr(tui, "_enhance_holder_active", False):
        return [("class:input-placeholder", "  Esc to revert back")]
    text = str(getattr(getattr(tui, "_input_buf", None), "text", "") or "").strip()
    if text and not text.startswith("/") and len(text.split()) >= ENHANCE_HINT_MIN_WORDS:
        return [("class:input-placeholder", "  Ctrl+E enhance message")]
    return []


def build_tui_root(tui: Any, input_buffer: Any, prefix: HTML | None = None) -> FloatContainer:
    """Build the protected TUI layout without changing panel order or caps."""
    if prefix is None:
        prefix = prompt_prefix()

    body = HSplit([
        Window(
            content=FormattedTextControl(
                lambda: tui._get_transcript(),
                show_cursor=False,
            ),
            height=lambda: Dimension(weight=1, max=max(1, tui._visible_transcript_height())),
            wrap_lines=False,
        ),
        Window(height=1, content=FormattedTextControl(lambda: [("", "")]), dont_extend_height=True),
        ConditionalContainer(
            Window(content=FormattedTextControl(lambda: tui._get_ghost_panel_fragments()), dont_extend_height=True, height=Dimension(max=14)),
            filter=Condition(lambda: tui._ghost_panel_open),
        ),
        ConditionalContainer(
            Window(height=1, content=FormattedTextControl(lambda: tui._get_activity_fragments()), dont_extend_height=True),
            filter=Condition(lambda: tui.busy or (tui._goal_worker_active and not tui._goal_backgrounded)),
        ),
        ConditionalContainer(
            Window(content=FormattedTextControl(lambda: tui._get_goal_board_fragments()), dont_extend_height=True, height=lambda: Dimension(max=tui._board_max_height())),
            filter=Condition(lambda: bool(tui._visible_goal_board_text())),
        ),
        ConditionalContainer(
            Window(content=FormattedTextControl(lambda: tui._get_board_fragments()), dont_extend_height=True, height=lambda: Dimension(max=tui._board_max_height())),
            filter=Condition(lambda: bool(tui.board_text) and not (tui._goal_worker_active and not tui._goal_backgrounded)),
        ),
        ConditionalContainer(
            Window(height=1, content=FormattedTextControl(lambda: tui._get_status_bar_fragments()), dont_extend_height=True),
            filter=Condition(lambda: not tui.busy and (not tui._goal_worker_active or tui._goal_backgrounded)),
        ),
        Window(height=1, content=FormattedTextControl(lambda: tui._get_separator_fragments()), dont_extend_height=True),
        ConditionalContainer(
            Window(content=FormattedTextControl(lambda: tui._palette.get_fragments()), dont_extend_height=True, height=Dimension(max=12)),
            filter=Condition(lambda: tui._palette.open),
        ),
        Window(height=lambda: input_window_height(tui), content=BufferControl(buffer=input_buffer, input_processors=[BeforeInput(prefix), PlaceholderProcessor(), EnhanceHintProcessor(tui), ExtrathinkShineProcessor(tui)]), dont_extend_height=True, wrap_lines=True),
        Window(height=1, content=FormattedTextControl(lambda: tui._get_footer_fragments()), dont_extend_height=True),
    ])

    return FloatContainer(
        content=body,
        style="class:body",  # skin-painted backdrop (resolves live on /skin)
        floats=[Float(
            xcursor=True,
            ycursor=True,
            content=CompletionsMenu(max_height=10),
            transparent=False,
        )],
    )
