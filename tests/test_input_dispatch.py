from types import SimpleNamespace

from interface.command_palette import CommandPalette, PaletteItem
from interface.input_dispatch import InputDispatchMixin


class FakeCompletionBuffer:
    def __init__(self, text="/", complete_state=True):
        self.text = text
        self.complete_state = complete_state
        self.cancelled = False

    def cancel_completion(self):
        self.cancelled = True


class DispatchHarness(InputDispatchMixin):
    def __init__(self):
        self.agent = SimpleNamespace(
            ghost_enabled=False,
            _retry_pending_input="",
            process_slash_command=lambda _text: None,
        )
        self._palette = CommandPalette()
        self._input_buf = SimpleNamespace(text="", cursor_position=0)
        self._app = SimpleNamespace(exited=False, invalidated=False, exit=lambda: setattr(self._app, "exited", True), invalidate=lambda: setattr(self._app, "invalidated", True))
        self._ghost_enabled = False
        self._ghost_input_mode = False
        self._ghost_unread_count = 0
        self._prt_done_unread = False
        self._ghost_panel_open = False
        self._ghost_panel_lines = []
        self._ghost_pending_route = None
        self._ghost_scroll_from_bottom = 0
        self._goal_running = False
        self._goal_backgrounded = False
        self._goal_stage = ""
        self._goal_board_text = ""
        self.busy = False
        self._goal_worker_active = False
        self.lines = []
        self.notices = []
        self.turns = []
        self.ghost_questions = []

    def _add(self, style, text):
        self.lines.append((style, text))

    def _set_notice(self, text, ttl=4.0):
        self.notices.append(text)

    def _start_goal_thread(self):
        self.goal_started = True

    def _apply_ghost_on(self):
        self._ghost_enabled = True
        self._ghost_input_mode = False
        self._ghost_unread_count = 0
        self.agent.ghost_enabled = True
        self._ghost_panel_open = True

    def _apply_ghost_off(self):
        self._ghost_enabled = False
        self._ghost_input_mode = False
        self.agent.ghost_enabled = False
        self._ghost_panel_open = False
        self._ghost_pending_route = None
        if self._input_buf and self._input_buf.text.startswith(("/ghost", "/gh")):
            self._input_buf.text = ""

    def _show_active_goal(self):
        self.goal_shown = True

    def _goal_finish_summary(self, text):
        return f"summary: {text}"

    def _process_next_queued_input(self):
        self.processed_queue = True

    def _clear_transcript(self):
        self.cleared = True

    def _run_turn_thread(self, text):
        self.turns.append(text)

    def _work_active(self):
        return self.busy

    def _command_allowed_while_working(self, _text):
        return False

    def _run_goal_command_now(self, text):
        self.goal_now = text

    def _queue_goal_command(self, text):
        self.queued_goal = text

    def _queue_input(self, text):
        self.queued = text

    def _ghost_history_panel_lines(self):
        return [("class:ghost-hint", "history")]

    def _ghost_panel_ask(self, question):
        self.ghost_questions.append(question)

    def _palette_children_for_item(self, item):
        if item.value == "/goal":
            return [PaletteItem("/goal ", "new goal…", "type autonomous goal", "insert")]
        return []


def test_on_input_changed_opens_and_closes_palette_without_submitting():
    harness = DispatchHarness()
    buff = FakeCompletionBuffer("/")

    harness._on_input_changed(buff)

    assert harness._palette.open is True
    assert buff.cancelled is True
    assert harness._app.invalidated is True

    harness._app.invalidated = False
    buff = FakeCompletionBuffer("normal", complete_state=False)
    harness._on_input_changed(buff)

    assert harness._palette.open is False
    assert harness._app.invalidated is True


def test_ghost_mode_swallows_plain_text_but_lets_slash_commands_through():
    # Regression: while Ghost mode is on, plain messages go to Ghost, but slash
    # commands (e.g. /ghost off) must still reach slash dispatch instead of being
    # swallowed as a Ghost side-question.
    harness = DispatchHarness()
    dispatched = []
    harness.agent.process_slash_command = lambda text: dispatched.append(text) or "[GHOST_OFF]"
    harness._apply_ghost_on()
    assert harness._ghost_enabled is True

    harness._handle_input("what is the plan")
    assert harness.ghost_questions == ["what is the plan"]

    harness._handle_input("/ghost off")
    assert dispatched == ["/ghost off"]
    assert harness.ghost_questions == ["what is the plan"]  # not routed to Ghost
    assert harness._ghost_enabled is False  # /ghost off actually took effect


def test_dispatch_slash_command_result_handles_ghost_on_without_transcript_when_notice_mode():
    harness = DispatchHarness()

    assert harness._dispatch_slash_command_result("[GHOST_ON]", render_result=False) is True

    assert harness._ghost_enabled is True
    assert harness.agent.ghost_enabled is True
    assert harness._ghost_panel_open is True
    assert harness._input_buf.text == ""
    assert harness.notices == ["Ghost: on"]
    assert harness.lines == []


def test_prompt_enhancer_replaces_input_buffer_without_sending_or_echoing():
    harness = DispatchHarness()

    harness._handle_input("/gp mo investigate for me in the codebse")

    assert harness._input_buf.text.startswith("Investigate in the codebase")
    assert harness._input_buf.cursor_position == len(harness._input_buf.text)
    assert harness.notices == ["Prompt enhanced; Enter to send"]
    assert harness.lines == []
    assert harness.turns == []


def test_prompt_enhancer_pg_compat_alias_replaces_input_even_while_busy():
    harness = DispatchHarness()
    harness.busy = True

    harness._handle_input("/pg i want to check mo core instuctions")

    assert harness._input_buf.text.startswith("Check mo core instructions")
    assert harness.notices == ["Prompt enhanced; Enter to send"]
    assert harness.lines == []
    assert harness.turns == []
    assert not hasattr(harness, "queued")


def test_prompt_enhancer_uses_agent_profile_in_input_dispatch(tmp_path):
    harness = DispatchHarness()
    pdir = tmp_path / "profile"
    pdir.mkdir()
    (pdir / "operator.md").write_text("direct concise evidence-first ask only if blocked", encoding="utf-8")
    harness.agent.profile = SimpleNamespace(_path=str(tmp_path / "mo.db"))

    harness._handle_input("/gp fix Ghost route")

    assert "keep the answer direct and concise" in harness._input_buf.text
    assert "ask only if blocked or risk changes" in harness._input_buf.text
    assert harness.notices == ["Prompt enhanced; Enter to send"]


def test_handle_input_routes_busy_normal_text_to_queue_without_user_echo():
    harness = DispatchHarness()
    harness.busy = True

    harness._handle_input("finish later")

    assert harness.queued == "finish later"
    assert harness.lines == []
    assert harness.turns == []


def test_handle_input_spaces_user_message_from_transcript_neighbors():
    harness = DispatchHarness()

    harness._handle_input("review the footer")

    assert harness.lines == [("", ""), ("class:user-msg", "* review the footer"), ("", "")]
    assert harness.turns == ["review the footer"]


def test_prt_started_slash_result_uses_prt_style():
    harness = DispatchHarness()

    assert harness._dispatch_slash_command_result("[PRT STARTED] Reviewing HEAD in background...", render_result=True) is True

    assert harness.lines == [("class:notification-prt", "  [PRT STARTED] Reviewing HEAD in background...")]


def test_run_turn_slash_marker_routes_to_normal_turn_thread():
    harness = DispatchHarness()
    harness.agent._slash_pending_input = "start OWNER_COMPARISON E:\\ref-a E:\\ref-b"

    assert harness._dispatch_slash_command_result("[RUN_TURN]", render_result=True) is True

    assert harness.turns == ["start OWNER_COMPARISON E:\\ref-a E:\\ref-b"]
    assert harness.agent._slash_pending_input == ""
    assert ("class:user-msg", "* start OWNER_COMPARISON E:\\ref-a E:\\ref-b") in harness.lines


def test_ghost_slash_input_is_removed_from_public_api():
    harness = DispatchHarness()
    # _handle_ghost_slash_input was removed after UX-225 (public /ghost removed).
    # The method no longer exists on InputDispatchMixin.
    assert not hasattr(harness, "_handle_ghost_slash_input")


def test_ghost_slash_routes_through_normal_input_path():
    harness = DispatchHarness()
    calls = []
    harness.agent.process_slash_command = lambda text: calls.append(text) or "Ghost side-check reply"

    # After UX-225, _handle_ghost_slash_input was removed. Ghost slash commands
    # now flow through the normal _handle_input → process_slash_command path.
    harness._handle_input("/ghost compare options")
    assert calls == ["/ghost compare options"]
    assert harness.notices == []


def test_ghost_typo_slash_hits_unknown_command_notice():
    harness = DispatchHarness()

    harness._handle_input("/ghot why did he not show taskboarding ?")

    assert harness.ghost_questions == []
    assert harness.turns == []
    assert harness.notices == ["Unknown command: /ghot"]


def test_palette_selection_enters_submenu_then_inserts_prefix():
    harness = DispatchHarness()
    harness._palette.enter_submenu("root", [PaletteItem("/goal", "/goal", "autonomous goal mode")])

    harness._handle_palette_selection()
    assert harness._palette.in_submenu is True

    harness._handle_palette_selection()
    assert harness._input_buf.text == "/goal "
    assert harness._input_buf.cursor_position == len("/goal ")
