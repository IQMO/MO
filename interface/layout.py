"""Prompt-toolkit layout construction for MO TUI."""
from __future__ import annotations

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
        Window(height=lambda: input_window_height(tui), content=BufferControl(buffer=input_buffer, input_processors=[BeforeInput(prefix), PlaceholderProcessor()]), dont_extend_height=True, wrap_lines=True),
        Window(height=1, content=FormattedTextControl(lambda: tui._get_footer_fragments()), dont_extend_height=True),
    ])

    return FloatContainer(
        content=body,
        floats=[Float(
            xcursor=True,
            ycursor=True,
            content=CompletionsMenu(max_height=10),
            transparent=False,
        )],
    )
