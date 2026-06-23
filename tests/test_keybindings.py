from types import SimpleNamespace

from interface.keybindings import build_tui_key_bindings


class FakeBuffer:
    def __init__(self, text=""):
        self.text = text
        self.complete_state = None
        self.inserted = ""
        self.cursor_position = 0

    def insert_text(self, text):
        self.inserted += text
        self.text += text

    def start_completion(self):
        self.started_completion = True

    def complete_next(self):
        self.next_completion = True

    def complete_previous(self):
        self.previous_completion = True

    def cursor_left(self, count=1):
        self.left = count

    def cursor_right(self, count=1):
        self.right = count

    def cursor_up(self, count=1):
        self.up = count

    def cursor_down(self, count=1):
        self.down = count

    @property
    def document(self):
        from prompt_toolkit.document import Document
        return Document(self.text, self.cursor_position)


class FakePalette:
    def __init__(self):
        self.open = False
        self.category_moves = []

    def close(self):
        self.open = False
        self.closed = True

    def record_command(self, command):
        self.recorded = command

    def move_category(self, delta):
        self.category_moves.append(delta)


class FakeTui:
    def __init__(self):
        self._input_buf = FakeBuffer()
        self._palette = FakePalette()
        self.busy = False
        self._ghost_panel_open = False
        self._ghost_enabled = False
        self._ghost_scroll_from_bottom = 0
        self._ghost_expanded = False
        self._ghost_input_mode = False
        self._ghost_panel_lines = []
        self._ghost_unread_count = 0
        self._prt_done_unread = False
        self._paste_holder_text = ""
        self._paste_holder_active = False
        self._pre_paste_buffer_text = ""
        self.notices = []

    def _handle_input(self, text):
        self.handled = text

    def _advance_queued_input_intent(self):
        self.advanced = True

    def _cancel_last_queued_input(self):
        self.cancelled = True

    def _toggle_goal_background(self):
        self.goal_toggled = True

    def _show_ghost_history(self, delta):
        self.ghost_history_delta = delta

    def _apply_ghost_on(self):
        self._ghost_enabled = True
        self._ghost_input_mode = False
        self._ghost_unread_count = 0
        self._prt_done_unread = False
        self._ghost_panel_open = True
        self._ghost_panel_lines = self._ghost_history_panel_lines()

    def _apply_ghost_off(self):
        self._ghost_enabled = False
        self._ghost_input_mode = False
        self._ghost_panel_open = False
        self._ghost_expanded = False
        self._ghost_scroll_from_bottom = 0
        self._ghost_panel_lines = []
        self._ghost_pending_route = None

    def _scroll_transcript(self, delta):
        self.transcript_delta = delta

    def _scroll_ghost(self, delta):
        self.ghost_delta = delta

    def _ghost_history_panel_lines(self):
        return [("class:ghost-hint", "Ghost history")]

    def _max_ghost_scroll(self):
        return 0

    def _transcript_top(self):
        self.top = True

    def _transcript_bottom(self):
        self.bottom = True

    def _set_notice(self, text):
        self.notices.append(text)


class FakeApp:
    def __init__(self, buffer):
        self.current_buffer = buffer
        self.invalidated = False
        self.exited = False

    def invalidate(self):
        self.invalidated = True

    def exit(self):
        self.exited = True


def _binding_by_key(kb, key_name):
    for binding in kb.bindings:
        if [str(key) for key in binding.keys] == [key_name]:
            return binding
    raise AssertionError(f"missing keybinding {key_name}")


def test_tui_keybindings_register_protected_control_keys():
    kb = build_tui_key_bindings(FakeTui())
    keys = {tuple(str(key) for key in binding.keys) for binding in kb.bindings}

    assert keys == {
        ("Keys.ControlM",),
        ("Keys.ControlJ",),
        ("Keys.F4",),
        ("Keys.Left",),
        ("Keys.Right",),
        ("Keys.ControlI",),
        ("Keys.BackTab",),
        ("Keys.ControlC",),
        ("Keys.Escape", "g"),
        ("Keys.Escape", "G"),
        ("Keys.ControlO",),
        ("Keys.ControlD",),
        ("Keys.ControlL",),
        ("Keys.Escape",),
        ("Keys.ControlG",),
        ("Keys.Up",),
        ("Keys.Down",),
        ("Keys.ControlUp",),
        ("Keys.ControlDown",),
        ("Keys.ControlShiftUp",),
        ("Keys.ControlShiftDown",),
        ("Keys.PageUp",),
        ("Keys.PageDown",),
        ("Keys.ScrollUp",),
        ("Keys.ScrollDown",),
        ("Keys.Home",),
        ("Keys.End",),
        ("Keys.BracketedPaste",),
    }


def test_enter_key_submits_input_without_echoing_or_leaking_text():
    tui = FakeTui()
    tui._input_buf.text = "/status"
    kb = build_tui_key_bindings(tui)
    event = SimpleNamespace(app=FakeApp(tui._input_buf))

    _binding_by_key(kb, "Keys.ControlM").handler(event)

    assert tui._input_buf.text == ""
    assert tui._palette.recorded == "/status"
    assert tui.handled == "/status"


def test_enter_key_submits_exact_slash_command_even_when_palette_is_open():
    tui = FakeTui()
    tui._palette.open = True
    tui._input_buf.text = "/status"
    kb = build_tui_key_bindings(tui)
    event = SimpleNamespace(app=FakeApp(tui._input_buf))

    _binding_by_key(kb, "Keys.ControlM").handler(event)

    assert tui._palette.open is False
    assert tui._palette.recorded == "/status"
    assert tui.handled == "/status"


def test_palette_tab_keys_move_palette_categories_without_disabling_completions():
    tui = FakeTui()
    tui._palette.open = True
    kb = build_tui_key_bindings(tui)
    event = SimpleNamespace(app=FakeApp(tui._input_buf))

    _binding_by_key(kb, "Keys.ControlI").handler(event)
    _binding_by_key(kb, "Keys.BackTab").handler(event)

    assert tui._palette.category_moves == [1, -1]
    assert not hasattr(tui._input_buf, "started_completion")

    tui._palette.open = False
    _binding_by_key(kb, "Keys.ControlI").handler(event)
    assert tui._input_buf.started_completion is True


def test_bracketed_paste_normalizes_without_submitting_small_text():
    tui = FakeTui()
    kb = build_tui_key_bindings(tui)
    event = SimpleNamespace(app=FakeApp(tui._input_buf), data="alpha  \r\nbeta\t \r")

    _binding_by_key(kb, "Keys.BracketedPaste").handler(event)

    assert tui._input_buf.inserted == "alpha\nbeta"
    assert tui._input_buf.text == "alpha\nbeta"
    assert not hasattr(tui, "handled")


def test_large_paste_goes_to_holder_and_enter_sends_held_text_not_label():
    tui = FakeTui()
    kb = build_tui_key_bindings(tui)
    pasted = "line\n" * 20
    event = SimpleNamespace(app=FakeApp(tui._input_buf), data=pasted)

    _binding_by_key(kb, "Keys.BracketedPaste").handler(event)

    assert tui._input_buf.text.startswith("[paste held:")
    assert tui._paste_holder_text == pasted.rstrip()
    assert tui.notices == ["Paste held; Enter sends, Esc clears"]
    assert not hasattr(tui, "handled")

    _binding_by_key(kb, "Keys.ControlM").handler(SimpleNamespace(app=FakeApp(tui._input_buf)))

    assert tui.handled == pasted.rstrip()
    assert tui._input_buf.text == ""
    assert tui._paste_holder_text == ""


def test_paste_holder_enter_sends_held_text_even_when_buffer_does_not_match_label():
    """When _paste_holder_active is True, submit_input_text returns the held
    text regardless of what the buffer currently contains (defense against
    the buffer text drifting from the label in edge cases)."""
    tui = FakeTui()
    kb = build_tui_key_bindings(tui)
    pasted = "important\n" * 10
    event = SimpleNamespace(app=FakeApp(tui._input_buf), data=pasted)
    _binding_by_key(kb, "Keys.BracketedPaste").handler(event)
    assert tui._paste_holder_text == pasted.rstrip()
    assert tui._paste_holder_active is True

    # Simulate buffer text drift after paste holder is set
    tui._input_buf.text = "something completely different"

    _binding_by_key(kb, "Keys.ControlM").handler(SimpleNamespace(app=FakeApp(tui._input_buf)))
    assert tui.handled == pasted.rstrip()
    assert tui._input_buf.text == ""
    assert tui._paste_holder_active is False


def test_large_paste_escape_restores_pre_paste_text():
    """Escape from a paste holder must restore the buffer to its pre-paste
    contents, not clear it entirely."""
    tui = FakeTui()
    kb = build_tui_key_bindings(tui)
    tui._input_buf.text = "my original text"
    tui._input_buf.cursor_position = len("my original text")
    pasted = "big paste\n" * 20
    event = SimpleNamespace(app=FakeApp(tui._input_buf), data=pasted)
    _binding_by_key(kb, "Keys.BracketedPaste").handler(event)
    assert tui._paste_holder_active is True

    _binding_by_key(kb, "Keys.Escape").handler(SimpleNamespace(app=FakeApp(tui._input_buf)))
    assert tui._input_buf.text == "my original text"
    assert tui._paste_holder_active is False
    assert tui.notices[-1] == "Paste cleared"


def test_large_paste_is_capped_and_escape_clears_holder():
    tui = FakeTui()
    kb = build_tui_key_bindings(tui)
    event = SimpleNamespace(app=FakeApp(tui._input_buf), data="x" * 13_000)

    _binding_by_key(kb, "Keys.BracketedPaste").handler(event)

    assert len(tui._paste_holder_text) == 12_000
    assert "first 12.0k chars" in tui._input_buf.text

    _binding_by_key(kb, "Keys.Escape").handler(SimpleNamespace(app=FakeApp(tui._input_buf)))

    assert tui._paste_holder_text == ""
    assert tui._input_buf.text == ""
    assert tui.notices[-1] == "Paste cleared"


def test_alt_g_toggles_ghost_mode_panel_and_routing():
    tui = FakeTui()
    tui._prt_done_unread = True
    kb = build_tui_key_bindings(tui)
    event = SimpleNamespace(app=FakeApp(tui._input_buf))

    _binding_by_key(kb, "Keys.Escape").handler(event)  # single Escape does not toggle Alt+G path
    assert tui._ghost_panel_open is False

    for binding in kb.bindings:
        if tuple(str(key) for key in binding.keys) == ("Keys.Escape", "g"):
            binding.handler(event)
            break
    else:
        raise AssertionError("missing Alt+G Ghost binding")

    # Alt+G now toggles both panel AND routing
    assert tui._ghost_panel_open is True
    assert tui._ghost_enabled is True
    assert tui._ghost_unread_count == 0
    assert tui._prt_done_unread is False
    assert "Ghost on" in tui.notices[-1]

    for binding in kb.bindings:
        if tuple(str(key) for key in binding.keys) == ("Keys.Escape", "g"):
            binding.handler(event)
            break
    assert tui._ghost_panel_open is False
    assert tui._ghost_enabled is False
    assert "Ghost off" in tui.notices[-1]

    # Some terminals preserve shifted Alt+G as Escape, uppercase G.
    for binding in kb.bindings:
        if tuple(str(key) for key in binding.keys) == ("Keys.Escape", "G"):
            binding.handler(event)
            break
    else:
        raise AssertionError("missing Alt+Shift+G Ghost binding")
    assert tui._ghost_panel_open is True
    assert tui._ghost_enabled is True


def test_ctrl_o_expands_and_collapses_visible_ghost_only():
    tui = FakeTui()
    kb = build_tui_key_bindings(tui)
    event = SimpleNamespace(app=FakeApp(tui._input_buf))

    _binding_by_key(kb, "Keys.ControlO").handler(event)
    assert tui._ghost_expanded is False

    tui._ghost_panel_open = True
    _binding_by_key(kb, "Keys.ControlO").handler(event)
    assert tui._ghost_expanded is True
    _binding_by_key(kb, "Keys.ControlO").handler(event)
    assert tui._ghost_expanded is False


def test_transcript_scroll_keys_work_after_ghost_panel_hide_with_empty_input():
    tui = FakeTui()
    tui._ghost_panel_open = True
    tui._ghost_scroll_from_bottom = 4
    tui._input_buf.text = ""
    kb = build_tui_key_bindings(tui)
    event = SimpleNamespace(app=FakeApp(tui._input_buf))

    _binding_by_key(kb, "Keys.Escape").handler(event)
    _binding_by_key(kb, "Keys.Up").handler(event)
    assert tui._ghost_panel_open is False
    assert tui._ghost_scroll_from_bottom == 0
    assert tui.transcript_delta == 3
    assert not hasattr(tui._input_buf, "up")

    _binding_by_key(kb, "Keys.Down").handler(event)
    assert tui.transcript_delta == -3

    _binding_by_key(kb, "Keys.PageUp").handler(event)
    assert tui.transcript_delta == 10
    _binding_by_key(kb, "Keys.PageDown").handler(event)
    assert tui.transcript_delta == -10

    _binding_by_key(kb, "Keys.Home").handler(event)
    _binding_by_key(kb, "Keys.End").handler(event)
    assert tui.top is True
    assert tui.bottom is True


def test_removed_ghost_slash_text_keeps_arrow_keys_in_editor():
    tui = FakeTui()
    tui._input_buf.text = "/ghost actual question"
    kb = build_tui_key_bindings(tui)
    event = SimpleNamespace(app=FakeApp(tui._input_buf))

    _binding_by_key(kb, "Keys.Up").handler(event)

    assert tui._input_buf.up == 1
    assert not hasattr(tui, "transcript_delta")


def test_arrows_move_cursor_in_multiline_draft_while_ghost_panel_open():
    """With the Ghost panel open, Up/Down inside a multi-line draft must move
    the cursor, not hijack into Ghost-history paging."""
    tui = FakeTui()
    tui._ghost_panel_open = True
    tui._input_buf.text = "line one\nline two\nline three"
    tui._input_buf.cursor_position = 13  # within "line two" -> not first/last line
    kb = build_tui_key_bindings(tui)
    event = SimpleNamespace(app=FakeApp(tui._input_buf))

    _binding_by_key(kb, "Keys.Up").handler(event)
    assert tui._input_buf.up == 1
    assert not hasattr(tui, "ghost_history_delta")  # not paged into history

    _binding_by_key(kb, "Keys.Down").handler(event)
    assert tui._input_buf.down == 1
    assert not hasattr(tui, "ghost_history_delta")


def test_arrows_page_ghost_history_at_draft_edge_when_panel_open():
    """At the top/bottom edge of the draft, including empty input, Up/Down
    still page Ghost history; the common case is preserved."""
    tui = FakeTui()
    tui._ghost_panel_open = True
    tui._input_buf.text = ""  # empty -> on_first_line and on_last_line both True
    kb = build_tui_key_bindings(tui)
    event = SimpleNamespace(app=FakeApp(tui._input_buf))

    _binding_by_key(kb, "Keys.Up").handler(event)
    assert tui.ghost_history_delta == -1
    _binding_by_key(kb, "Keys.Down").handler(event)
    assert tui.ghost_history_delta == 1
