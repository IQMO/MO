from types import SimpleNamespace

from prompt_toolkit.buffer import Buffer
from prompt_toolkit.layout.containers import ConditionalContainer, FloatContainer, HSplit, Window
from prompt_toolkit.layout.controls import BufferControl

from interface.layout import (
    INPUT_PLACEHOLDER,
    PlaceholderProcessor,
    build_tui_root,
    input_visual_height,
    prompt_prefix,
)


class FakeTui:
    busy = False
    _goal_worker_active = False
    _goal_backgrounded = False
    _ghost_panel_open = False
    board_text = ""
    _palette = SimpleNamespace(open=False, get_fragments=lambda: [("", "")])

    def __init__(self):
        self._input_buf = SimpleNamespace(text="")
        self._app = SimpleNamespace(output=SimpleNamespace(get_size=lambda: SimpleNamespace(rows=20, columns=40)))

    def _get_transcript(self):
        return [("class:mo-response", "transcript")]

    def _get_ghost_panel_fragments(self):
        return [("class:ghost-response", "ghost")]

    def _get_activity_fragments(self):
        return [("class:activity", "activity")]

    def _get_status_bar_fragments(self):
        return [("class:activity", "status")]

    def _visible_goal_board_text(self):
        return ""

    def _get_goal_board_fragments(self):
        return [("class:task-active", "goal")]

    def _get_board_fragments(self):
        return [("class:task-active", "board")]

    def _get_separator_fragments(self):
        return [("class:separator", "─")]

    def _get_footer_fragments(self):
        return [("class:footer", "footer")]

    def _board_max_height(self):
        return 8


def test_prompt_prefix_preserves_current_marker_html():
    assert prompt_prefix().value == "<ansicyan><b>*</b></ansicyan> "


def test_build_tui_root_preserves_panel_order_and_height_caps():
    input_buffer = Buffer()
    root = build_tui_root(FakeTui(), input_buffer, prompt_prefix())

    assert isinstance(root, FloatContainer)
    assert isinstance(root.content, HSplit)
    assert len(root.floats) == 1

    children = root.content.children
    assert len(children) == 12
    assert isinstance(children[0], Window)  # transcript viewport
    assert isinstance(children[1], Window)  # spacer
    assert all(isinstance(children[index], ConditionalContainer) for index in (2, 3, 4, 5, 6))
    assert isinstance(children[7], Window)  # separator
    assert isinstance(children[8], ConditionalContainer)  # palette
    assert isinstance(children[9], Window)  # input
    assert isinstance(children[10], ConditionalContainer)  # Ctrl+E enhance hint
    assert isinstance(children[11], Window)  # footer

    assert children[2].content.height.max == 14  # Ghost surface
    assert children[3].content.height == 1  # active MO lane stays above task boards
    goal_board_height = children[4].content.height
    if callable(goal_board_height):
        goal_board_height = goal_board_height()
    assert goal_board_height.max == 8  # goal board
    main_board_height = children[5].content.height
    if callable(main_board_height):
        main_board_height = main_board_height()
    assert main_board_height.max == 8  # main board
    assert children[8].content.height.max == 12  # command palette
    assert isinstance(children[9].content, BufferControl)
    assert children[9].content.buffer is input_buffer


def test_main_board_hides_while_foreground_goal_is_running():
    input_buffer = Buffer()
    tui = FakeTui()
    tui.board_text = "3 tasks (0 done, 3 open)\n→ Main work"
    root = build_tui_root(tui, input_buffer, prompt_prefix())
    main_board = root.content.children[5]

    assert main_board.filter() is True

    tui._goal_worker_active = True
    tui._goal_backgrounded = False
    assert main_board.filter() is False

    tui._goal_backgrounded = True
    assert main_board.filter() is True


def test_input_window_wires_placeholder_processor():
    input_buffer = Buffer()
    root = build_tui_root(FakeTui(), input_buffer, prompt_prefix())
    input_window = root.content.children[9]

    processors = input_window.content.input_processors
    assert any(isinstance(proc, PlaceholderProcessor) for proc in processors)


def test_placeholder_shows_only_when_buffer_empty_on_first_line():
    proc = PlaceholderProcessor()
    buf = Buffer()

    def transformation_input(lineno, fragments):
        return SimpleNamespace(
            buffer_control=SimpleNamespace(buffer=buf),
            lineno=lineno,
            fragments=fragments,
        )

    # Empty buffer, first line -> placeholder rendered.
    out = proc.apply_transformation(transformation_input(0, [("", "")]))
    assert out.fragments == [("class:input-placeholder", INPUT_PLACEHOLDER)]

    # Empty buffer, non-first line -> untouched.
    original = [("", "second")]
    assert proc.apply_transformation(transformation_input(1, original)).fragments == original

    # Non-empty buffer -> never overrides typed text.
    buf.text = "hello"
    typed = [("", "hello")]
    assert proc.apply_transformation(transformation_input(0, typed)).fragments == typed


def test_input_visual_height_expands_and_caps_for_multiline_text():
    tui = FakeTui()

    assert input_visual_height(tui) == 1

    tui._input_buf.text = "one\ntwo\nthree"
    assert input_visual_height(tui) == 3

    tui._input_buf.text = "line\n" * 12
    assert input_visual_height(tui) == 5
