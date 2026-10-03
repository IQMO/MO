"""Prompt-toolkit layout construction for MO TUI."""
from __future__ import annotations

import unicodedata
from typing import Any

from prompt_toolkit.layout.containers import ConditionalContainer, DynamicContainer, Float, FloatContainer, HSplit, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.layout.processors import BeforeInput, Processor, Transformation
from prompt_toolkit.filters import Condition
from prompt_toolkit.utils import get_cwidth

from .activity import status_line_mode
from .transcript import terminal_display_indexed, terminal_text_is_rtl

INPUT_MAX_ROWS = 5
PROMPT_GLYPH = "❯"
INPUT_PLACEHOLDER = "Type a message"
APP_BG_STYLE = "class:app-bg"
SURFACE_BG_STYLE = "class:surface-bg"
INPUT_BG_STYLE = "class:input-bg"
BG_FILL_CHAR = " "
INPUT_FRAME_STYLE = "class:separator"
INPUT_FRAME_HORIZONTAL = "─"


def prompt_prefix() -> list[tuple[str, str]]:
    return [("class:mo-marker", PROMPT_GLYPH), ("", " ")]


class BidiInputProcessor(Processor):
    """Display RTL input visually while leaving the editable buffer logical."""

    @staticmethod
    def _char_is_rtl(text: str, index: int) -> bool:
        for char in reversed(text[:index + 1]):
            bidi_class = unicodedata.bidirectional(char)
            if bidi_class in {"R", "AL"}:
                return True
            if bidi_class in {"L", "EN", "AN"}:
                return False
        return terminal_text_is_rtl(text)

    def apply_transformation(self, transformation_input):
        fragments = list(transformation_input.fragments)
        text = "".join(value for _style, value in fragments)
        if not any(unicodedata.bidirectional(char) in {"R", "AL"} for char in text):
            return Transformation(fragments)

        styled = [(style, char) for style, value in fragments for char in value]
        indexed = terminal_display_indexed(text)
        display_fragments = [(styled[source][0], char) for source, char in indexed]
        display_positions = [0] * len(text)
        for display_index, (source_index, _char) in enumerate(indexed):
            display_positions[source_index] = display_index

        boundaries: list[int] = []
        for source_index in range(len(text) + 1):
            if source_index < len(text):
                display_index = display_positions[source_index]
                boundaries.append(display_index + int(self._char_is_rtl(text, source_index)))
            elif text:
                display_index = display_positions[-1]
                boundaries.append(display_index + int(not self._char_is_rtl(text, len(text) - 1)))
            else:
                boundaries.append(0)

        inverse = [
            min(range(len(boundaries)), key=lambda source: (abs(boundaries[source] - display), source))
            for display in range(len(text) + 1)
        ]
        return Transformation(
            display_fragments,
            source_to_display=lambda index: boundaries[max(0, min(len(text), index))],
            display_to_source=lambda index: inverse[max(0, min(len(text), index))],
        )


class PlaceholderProcessor(Processor):
    """Render dim placeholder text on the first line while the input is empty.

    Keeps the protected `*` prompt marker untouched; only fills the otherwise
    blank editor line so an empty composer reads as an input field, not as a
    missing/absent section.
    """

    def __init__(self, text: str = INPUT_PLACEHOLDER, tui: Any | None = None) -> None:
        self.text = text
        self.tui = tui

    def apply_transformation(self, transformation_input):
        buffer = transformation_input.buffer_control.buffer
        if buffer.text == "" and transformation_input.lineno == 0:
            text = self.text
            controller = getattr(self.tui, "_workspace", None)
            if controller is not None and controller.split_active:
                try:
                    text = controller.input_placeholder()
                except Exception:
                    text = self.text
            # Keep the ❯ prompt prefix (added by BeforeInput) and append the dim
            # placeholder after it. In split mode the label names the exact pane
            # that owns this one composer, rather than repeating a generic MO box.
            return Transformation(
                list(transformation_input.fragments) + [("class:input-placeholder", text)]
            )
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


class ShineTriggerProcessor(Processor):
    """Apply a static white gradient to typed ``extrathink``/``mapthis`` words.

    Recolours only the matched characters, preserving their exact glyphs and
    width. Prompt Toolkit already repaints on keypress, so no animation clock is
    needed while the operator types.
    """

    def __init__(self, tui: Any) -> None:
        self.tui = tui

    def apply_transformation(self, transformation_input):
        fragments = transformation_input.fragments
        text = "".join(t for _, t in fragments)
        from .visual_effects import gradient_fragments, has_shine_trigger, shine_trigger_matches

        if not has_shine_trigger(text):
            return Transformation(fragments)
        chars: list[list] = []
        for style, run in fragments:
            for ch in run:
                chars.append([style, ch])
        for match in shine_trigger_matches(text):
            gradient = gradient_fragments(text[match.start():match.end()])
            for offset, (style, _char) in enumerate(gradient):
                index = match.start() + offset
                if 0 <= index < len(chars):
                    chars[index][0] = style
        return Transformation([(style, char) for style, char in chars])


def input_visual_height(tui: Any, *, max_rows: int = INPUT_MAX_ROWS) -> int:
    """Return the visible input editor height, capped to keep transcript context."""
    width = _input_editor_columns(tui)
    text = str(getattr(getattr(tui, "_input_buf", None), "text", "") or "")
    row_cap = int(max_rows or INPUT_MAX_ROWS)
    cached = getattr(tui, "_input_visual_height_cache", None)
    if (
        isinstance(cached, tuple)
        and len(cached) == 4
        and cached[0] is text
        and cached[1] == width
        and cached[2] == row_cap
    ):
        return cached[3]
    rows = 0
    for line in (text.splitlines() or [""]):
        rows += max(1, (get_cwidth(line) + width - 1) // width)
    visible = max(1, min(row_cap, rows))
    # prompt_toolkit asks the three composer windows and their parent for the
    # same preferred height repeatedly during one paint. Retain only the exact
    # current draft/width result; a keypress, resize, or cap change misses this
    # one-entry cache immediately, so dynamic layout state stays fresh.
    tui._input_visual_height_cache = (text, width, row_cap, visible)
    return visible


def input_window_height(tui: Any) -> Dimension:
    rows = input_visual_height(tui)
    return Dimension.exact(rows)


def _terminal_columns(tui: Any) -> int:
    getter = getattr(tui, "_terminal_columns", None)
    if callable(getter):
        return max(20, int(getter() or 80))
    try:
        size = tui._app.output.get_size()
        return max(20, int(getattr(size, "columns", 80) or 80))
    except Exception:
        return 80


def _input_frame_columns(tui: Any) -> int:
    return max(8, _terminal_columns(tui))


def _input_editor_columns(tui: Any) -> int:
    return max(12, _input_frame_columns(tui) - 2)


def _transcript_window_height(tui: Any) -> Dimension:
    """Keep native-scrollback controls anchored to the terminal bottom.

    The managed transcript has a bounded height. Native scrollback has no
    managed transcript rows, so prefer zero height instead of counting its
    empty content as a blank row. Keep it flexible to consume terminal slack
    above the live panels and composer.
    """
    scrollback_enabled = getattr(tui, "_scrollback_transcript_enabled", None)
    if callable(scrollback_enabled) and scrollback_enabled():
        return Dimension(preferred=0, weight=1)
    return Dimension(weight=1, max=max(0, tui._visible_transcript_height()))


def input_frame_border_fragments(tui: Any, *, bottom: bool = False) -> list[tuple[str, str]]:
    cols = _input_frame_columns(tui)
    return [(INPUT_FRAME_STYLE, INPUT_FRAME_HORIZONTAL * cols)]


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


def _bg_window(
    *,
    style: str = APP_BG_STYLE,
    char: str = BG_FILL_CHAR,
    **kwargs: Any,
) -> Window:
    return Window(style=style, char=char, **kwargs)


def _input_frame(tui: Any, input_buffer: Any, prefix: Any) -> VSplit:
    height = lambda: input_window_height(tui)
    return VSplit([
        _bg_window(
            style=INPUT_BG_STYLE,
            width=1,
            height=height,
            dont_extend_height=True,
        ),
        _bg_window(
            style=INPUT_BG_STYLE,
            height=height,
            content=BufferControl(
                buffer=input_buffer,
                input_processors=[
                    BidiInputProcessor(),
                    BeforeInput(prefix),
                    PlaceholderProcessor(tui=tui),
                    EnhanceHintProcessor(tui),
                    ShineTriggerProcessor(tui),
                ],
            ),
            dont_extend_height=True,
            wrap_lines=True,
        ),
        _bg_window(
            style=INPUT_BG_STYLE,
            width=1,
            height=height,
            dont_extend_height=True,
        ),
    ], height=height, style=APP_BG_STYLE)


def build_tui_root(tui: Any, input_buffer: Any, prefix: Any | None = None) -> FloatContainer:
    """Build the protected nonfullscreen TUI with truthful panel heights."""
    if prefix is None:
        prefix = prompt_prefix()

    from .workspace_panel import workspace_grid_container

    # Reuse one BufferControl so focus cannot remain on a hidden composer copy.
    input_frame = _input_frame(tui, input_buffer, prefix)
    workspace_input = input_frame
    workspace_grid = workspace_grid_container(
        tui, workspace_input, lambda: input_visual_height(tui)
    )
    inactive_workspace = _bg_window(content=FormattedTextControl(text=""))
    workspace_surface = DynamicContainer(
        lambda: workspace_grid
        if tui._session_workspace_active()
        else inactive_workspace
    )
    compact_input = input_frame.children[1]
    palette_window = _bg_window(
        style=SURFACE_BG_STYLE,
        content=FormattedTextControl(
            lambda: tui._palette.get_fragments(columns=tui._terminal_columns())
        ),
        dont_extend_height=True,
        height=Dimension(max=12),
    )
    tui._palette_window = palette_window
    body = HSplit([
        ConditionalContainer(
            _bg_window(
                content=FormattedTextControl(
                    lambda: tui._get_transcript(),
                    show_cursor=False,
                ),
                height=lambda: _transcript_window_height(tui),
                wrap_lines=False,
            ),
            filter=Condition(lambda: not tui._session_workspace_active()),
        ),
        ConditionalContainer(
            workspace_surface,
            filter=Condition(lambda: tui._session_workspace_active()),
        ),
        ConditionalContainer(
            _bg_window(height=1, dont_extend_height=True),
            filter=Condition(
                lambda: not tui._session_workspace_active()
                and (
                    tui._activity_panel_active()
                    or tui.busy
                    or (tui._goal_worker_active and not tui._goal_backgrounded)
                    or bool(tui._visible_goal_board_text())
                    or bool(tui.board_text)
                )
            ),
        ),
        ConditionalContainer(
            _bg_window(
                content=FormattedTextControl(lambda: tui._get_activity_panel_fragments()),
                dont_extend_height=True,
                height=Dimension(max=8),
            ),
            filter=Condition(lambda: tui._activity_panel_active()),
        ),
        ConditionalContainer(
            _bg_window(
                height=lambda: 1 + int(getattr(tui, "_live_tool_row_count", lambda: 0)() or 0),
                content=FormattedTextControl(lambda: tui._get_activity_fragments()),
                dont_extend_height=True,
                wrap_lines=False,
            ),
            filter=Condition(
                lambda: not tui._session_workspace_active()
                and (tui.busy or (tui._goal_worker_active and not tui._goal_backgrounded))
            ),
        ),
        ConditionalContainer(
            _bg_window(
                content=FormattedTextControl(lambda: tui._get_goal_board_fragments()),
                dont_extend_height=True,
                height=lambda: Dimension(max=tui._board_max_height()),
            ),
            filter=Condition(lambda: not tui._session_workspace_active() and bool(tui._visible_goal_board_text())),
        ),
        ConditionalContainer(
            _bg_window(
                content=FormattedTextControl(lambda: tui._get_board_fragments()),
                dont_extend_height=True,
                height=lambda: Dimension(max=tui._board_max_height()),
            ),
            filter=Condition(
                lambda: not tui._session_workspace_active()
                and bool(tui.board_text)
                and not (tui._goal_worker_active and not tui._goal_backgrounded)
            ),
        ),
        ConditionalContainer(
            _bg_window(
                height=1,
                content=FormattedTextControl(lambda: tui._get_status_bar_fragments()),
                dont_extend_height=True,
            ),
            filter=Condition(
                lambda: not tui._session_workspace_active()
                and status_line_mode(tui) != "hidden"
            ),
        ),
        ConditionalContainer(
            palette_window,
            filter=Condition(lambda: tui._palette.open),
        ),
        ConditionalContainer(
            _bg_window(
                width=lambda: Dimension.exact(_input_frame_columns(tui)),
                height=1,
                content=FormattedTextControl(lambda: input_frame_border_fragments(tui)),
                dont_extend_height=True,
            ),
            filter=Condition(lambda: not tui._session_workspace_active()),
        ),
        ConditionalContainer(
            input_frame,
            filter=Condition(lambda: not tui._session_workspace_active()),
        ),
        # Keep the live composer visually separate from the footer stats line.
        ConditionalContainer(
            _bg_window(
                width=lambda: Dimension.exact(_input_frame_columns(tui)),
                height=1,
                content=FormattedTextControl(lambda: input_frame_border_fragments(tui, bottom=True)),
                dont_extend_height=True,
            ),
            filter=Condition(lambda: not tui._session_workspace_active()),
        ),
        ConditionalContainer(
            _bg_window(
                height=1,
                content=FormattedTextControl(lambda: tui._get_footer_fragments()),
                dont_extend_height=True,
            ),
            filter=Condition(lambda: not tui._session_workspace_active()),
        ),
    ], window_too_small=compact_input, style=APP_BG_STYLE)

    return FloatContainer(
        content=body,
        floats=[Float(
            xcursor=True,
            ycursor=True,
            content=CompletionsMenu(max_height=10),
            transparent=False,
        )],
        style=APP_BG_STYLE,
    )
