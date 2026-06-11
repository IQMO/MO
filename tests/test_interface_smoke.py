import json
import threading
from types import SimpleNamespace

from prompt_toolkit.utils import get_cwidth

from core.review.diff_review import ReviewFinding, ReviewReport
from core.ghost.ghost_routing import GhostRouteSuggestion, route_prt_report
from core.tasking.task_board import TaskBoard, TaskItem
from core.workers import ensure_worker_registry
from interface.main_terminal import MoTui
from interface.command_palette import PaletteItem
from interface.ghost import sanitize_proposal_for_context


class _FakeOutput:
    def __init__(self, rows=10, columns=80):
        self._size = SimpleNamespace(rows=rows, columns=columns)

    def get_size(self):
        return self._size


class _FakeApp:
    def __init__(self, rows=10, columns=80):
        self.output = _FakeOutput(rows, columns)
        self.invalidated = 0

    def invalidate(self):
        self.invalidated += 1


def _plain(fragments):
    return "".join(fragment[1] for fragment in fragments)


def _make_tui(rows=10, columns=80):
    agent = SimpleNamespace(session=SimpleNamespace(token_log=[]), provider_name="mock", model="mock", config={})
    gateway = SimpleNamespace()
    tui = MoTui(agent, gateway)
    tui._app = _FakeApp(rows=rows, columns=columns)
    return tui


def test_transcript_smoke_renders_tail_by_default():
    tui = _make_tui(rows=8, columns=80)
    for index in range(30):
        tui._add("class:mo-response", f"line {index}")

    rendered = _plain(tui._get_transcript())

    assert "line 29" in rendered
    assert "line 0" not in rendered


def test_transcript_status_bar_does_not_show_scroll_debug_text():
    tui = _make_tui(rows=8, columns=80)
    for index in range(30):
        tui._add("class:mo-response", f"line {index}")

    tui._scroll_transcript(10)
    rendered = _plain(tui._get_status_bar_fragments())

    assert "scroll" not in rendered
    assert "F6" not in rendered


def test_transcript_smoke_scrolls_up_and_down():
    tui = _make_tui(rows=8, columns=80)
    for index in range(30):
        tui._add("class:mo-response", f"line {index}")

    tui._scroll_transcript(10)
    scrolled = _plain(tui._get_transcript())
    assert "line 29" not in scrolled
    assert any(f"line {index}" in scrolled for index in range(10, 24))

    tui._scroll_transcript(-10)
    tail = _plain(tui._get_transcript())
    assert "line 29" in tail


def test_transcript_append_preserves_manual_scroll_position():
    tui = _make_tui(rows=8, columns=80)
    for index in range(30):
        tui._add("class:mo-response", f"line {index}")
    tui._scroll_transcript(10)
    before = _plain(tui._get_transcript())

    tui._add("class:dim", "new background notice")
    after = _plain(tui._get_transcript())

    assert "new background notice" not in after
    assert "line 29" not in after
    assert before.splitlines()[0] == after.splitlines()[0]


def test_transcript_wraps_prose_without_splitting_words():
    tui = _make_tui(rows=8, columns=34)
    tui._add("class:mo-response", "The agent cannot issue refunds. It cannot spawn children.")

    rendered = _plain(tui._get_transcript())

    assert "refund\ns" not in rendered
    assert "refunds." in rendered
    assert "\n  " in rendered


def test_transcript_wraps_bullet_continuation_under_text():
    tui = _make_tui(rows=8, columns=45)
    tui._add_response_line("  - alpha beta gamma delta epsilon zeta eta theta")

    lines = _plain(tui._get_transcript()).splitlines()

    assert lines[0].startswith("  - ")
    assert len(lines) > 1
    assert lines[1].startswith("    ")


def test_transcript_smoke_wraps_long_lines_inside_viewport():
    tui = _make_tui(rows=8, columns=30)
    tui._add("class:mo-response", "x" * 90)

    rows = tui._visual_transcript_rows()

    assert len(rows) >= 3
    assert all(len(text) <= 30 for row in rows for _style, text in row)


def test_proposal_chat_text_hides_ghost_plan_labels():
    proposal = (
        "Proposal: I’ll build a small donation site.\n"
        "Assumptions: Use a static HTML/CSS page.\n"
        "Plan:\n"
        "- Inspect files\n"
        "- Write page"
    )

    visible = MoTui._proposal_chat_text(proposal)

    assert "I’ll build a small donation site." in visible
    assert "Assuming use a static HTML/CSS page." in visible
    assert "Proposal:" not in visible
    assert "Plan:" not in visible
    assert "Inspect files" not in visible


def test_intent_handoff_chat_text_strips_internal_labels_without_plan_tasks():
    proposal = (
        "Intent: Review MO mindset and taskboarding without broadening scope.\n"
        "Scope guardrails: Do not invent file-specific cleanup before tools.\n"
        "Evidence required: inspect code, verify, report blockers.\n"
        "Unknowns: none yet\n"
    )

    visible = MoTui._proposal_chat_text(proposal)

    assert "Review MO mindset and taskboarding" in visible
    assert "Scope: Do not invent file-specific cleanup" in visible
    assert "Evidence: inspect code" in visible
    assert "Unknowns:" not in visible
    assert "Intent:" not in visible



def test_proposal_chat_text_strips_leaked_dsml_markup():
    proposal = (
        "Let me peek at existing games first for style/pattern.\n"
        "\n"
        "<\uff5c\uff5cDSML\uff5c\uff5ctool_calls>\n"
        "<\uff5c\uff5cDSML\uff5c\uff5cinvoke name=\"shell\">\n"
        "</\uff5c\uff5cDSML\uff5c\uff5cinvoke>\n"
    )

    visible = MoTui._proposal_chat_text(proposal)

    assert "Let me peek" in visible
    assert "DSML" not in visible
    assert "invoke" not in visible
    assert "tool_calls" not in visible


def test_sanitize_proposal_strips_dsml_from_agent_context():
    proposal = (
        "Proposal: Build a dungeon crawler.\n"
        "Assumptions: curses available.\n"
        "Plan:\n"
        "- Inspect files\n"
        "- Write game.py\n"
        "\n"
        "<\uff5c\uff5cDSML\uff5c\uff5ctool_calls>\n"
        "<\uff5c\uff5cDSML\uff5c\uff5cinvoke name=\"shell\">\n"
        "<\uff5c\uff5cDSML\uff5c\uff5cparameter name=\"command\">ls</\uff5c\uff5cDSML\uff5c\uff5cparameter>\n"
        "</\uff5c\uff5cDSML\uff5c\uff5cinvoke>\n"
    )

    sanitized = sanitize_proposal_for_context(proposal)

    assert "Build a dungeon crawler" in sanitized
    assert "Inspect files" in sanitized
    assert "DSML" not in sanitized
    assert "invoke" not in sanitized
    assert "tool_calls" not in sanitized


def test_activity_label_has_preparing_and_finalizing_stages():
    from interface.formatting import activity_label
    assert activity_label("preparing proposal") == "Preparing…"
    assert activity_label("finalizing response") == "Finalizing…"
    assert activity_label("critiquing final response") == "Finalizing…"



def test_response_report_typography_styles_heading_and_bullet():
    tui = _make_tui(rows=8, columns=80)

    tui._add_response_line("What you get:")
    tui._add_response_line("  - Full green pitch with nets")
    fragments = tui._get_transcript()
    styles = [fragment[0] for fragment in fragments]
    rendered = _plain(fragments)

    assert "What you get:" in rendered
    assert "  - Full green pitch with nets" in rendered
    assert "class:response-heading" in styles
    assert "class:response-bullet-marker" in styles
    assert "class:response-bullet-head" in styles
    assert "class:response-bullet-rest" in styles


def test_response_report_typography_preserves_code_blocks():
    tui = _make_tui(rows=12, columns=80)

    tui._add_response_block("Here:\n```python\ndef hi():\n    return 'ok'\n```")
    fragments = tui._get_transcript()
    styles = [fragment[0] for fragment in fragments]
    rendered = _plain(fragments)

    assert "```" not in rendered
    assert "def hi():" in rendered
    assert "class:response-code" in styles


def test_main_taskboard_does_not_render_root_triangle_marker():
    tui = _make_tui(rows=12, columns=80)
    tui.busy = True
    tui.board_text = "2 tasks (0 done, 2 open)\n→ Inspect\n□ Report"

    rendered = _plain(tui._get_board_fragments())

    assert "⏿" not in rendered
    assert "→ Inspect" in rendered


def test_activity_lane_keeps_live_task_board_summary():
    tui = _make_tui(rows=12, columns=80)
    tui.busy = True
    tui.activity_text = "thinking"
    tui.activity_started_at = 1
    tui.board_text = "4 tasks (3 done, 1 open)\n→ Report"

    rendered = _plain(tui._get_activity_fragments())

    assert "4 tasks (3 done, 1 open)" in rendered
    assert "→ Report" not in rendered


def test_task_board_live_area_keeps_count_line_visible():
    tui = _make_tui(rows=12, columns=80)
    tui.board_text = "2 tasks (1 done, 1 open)\n√ Inspect\n→ Report"

    rendered = _plain(tui._get_board_fragments())

    assert "2 tasks (1 done, 1 open)" in rendered
    assert "√ Inspect" in rendered


def test_busy_task_board_omits_duplicate_count_line_below_activity():
    tui = _make_tui(rows=12, columns=80)
    tui.busy = True
    tui.board_text = "2 tasks (1 done, 1 open)\n√ Inspect\n→ Report"

    activity = _plain(tui._get_activity_fragments())
    board = _plain(tui._get_board_fragments())

    assert "2 tasks (1 done, 1 open)" in activity
    assert "2 tasks (1 done, 1 open)" not in board
    assert "√ Inspect" in board
    assert "→ Report" in board


def test_tui_board_update_uses_gateway_board_not_callback_markup():
    class FakeGateway:
        def __init__(self):
            self.last_task_board = None

        def should_show_task_board(self, _text):
            return True

        def run_turn(self, _text, on_board_update=None, **_kwargs):
            self.last_task_board = TaskBoard(
                "turn-1",
                "build_create",
                [
                    TaskItem("1", "Inspect real files", "completed", ["read_file:real.py"]),
                    TaskItem("2", "Verify real behavior", "active"),
                ],
            )
            on_board_update("999 tasks (999 done, 0 open)\n√ forged callback markup")
            return "Blocked: required work is not complete"

    tui = _make_tui(rows=12, columns=80)
    tui.gateway = FakeGateway()
    tui.agent.autosave_session = lambda: None

    tui._run_turn_thread("build real thing")

    assert "Inspect real files" in tui.board_text
    assert "Verify real behavior" in tui.board_text
    assert "forged callback markup" not in tui.board_text
    assert "999 tasks" not in tui.board_text


def test_ghost_route_confirmation_to_main_shows_sent_receipt(monkeypatch):
    tui = _make_tui()
    tui._ghost_pending_route = GhostRouteSuggestion("main", "review UI", "idle")
    started = []
    transitions = []
    monkeypatch.setattr(tui, "_handle_input", lambda objective: started.append(objective))
    monkeypatch.setattr(tui, "_start_ghost_route_transition", lambda user_text, response: transitions.append((user_text, response)))

    handled = tui._handle_ghost_route_reply("yes")

    assert handled is True
    assert started == ["review UI"]
    assert transitions and transitions[0][0] == "yes"
    assert transitions[0][1] == "MO routed"


def test_ghost_route_confirmation_starts_background_worker_without_new_command():
    tui = _make_tui(rows=12, columns=80)
    calls = []
    tui._ghost_pending_route = GhostRouteSuggestion("background", "scan docs", "independent")

    def fake_start(objective, worker_id=None):
        calls.append((objective, worker_id))
        tui.agent.workers.update(worker_id, "running", "fake worker running")
        return tui.agent.workers.get(worker_id)

    transitions = []
    tui._start_background_worker_from_ghost = fake_start
    tui._start_ghost_route_transition = lambda user_text, response: transitions.append((user_text, response))

    handled = tui._handle_ghost_route_reply("yes")

    assert handled is True
    assert calls and calls[0][0] == "scan docs"
    assert transitions[0][1] == "Worker routed"
    assert tui._ghost_pending_route is None
    assert tui._ghost_history[-1]["kind"] == "route_confirm"


def test_ghost_route_transition_uses_one_truth_glyph_at_a_time():
    assert MoTui._ghost_route_transition_glyph("↯") == "↯"
    assert MoTui._ghost_route_transition_glyph("→") == "→"
    assert MoTui._ghost_route_transition_glyph("✓") == "✓"
    assert MoTui._ghost_route_transition_glyph("!") == "!"
    assert MoTui._ghost_route_transition_glyphs("MO routed") == ["↯", "→", "✓"]
    assert MoTui._ghost_route_transition_glyphs("Worker unavailable · conflict") == ["!"]


def test_ghost_route_receipt_lines_render_as_cyan_status_rows():
    tui = _make_tui(rows=12, columns=80)
    tui._ghost_panel_lines = [("class:ghost-response", "MO routed")]

    rows = tui._ghost_panel_content_rows()

    assert rows[0][0][0] == "class:ghost-route"
    assert "MO routed" in rows[0][0][1]


def test_ghost_blocked_route_receipt_does_not_show_success_check():
    tui = _make_tui(rows=12, columns=80)
    record = ensure_worker_registry(tui.agent).create(kind="worker", source="ghost", route="background", objective="edit core/agent.py", state="blocked", note="workspace conflict")

    receipt = tui._ghost_route_receipt(record)

    assert "Worker unavailable" in receipt
    assert "workspace conflict" in receipt
    assert "✓" not in receipt


def test_ghost_implicit_yes_routes_last_suggested_main_ask(monkeypatch):
    tui = _make_tui(rows=12, columns=80)
    routed = []
    tui._record_ghost_history(
        "reply",
        "can you ask mo?",
        "Suggested ask:\n- “Open the Wraith site files and verify what got built.”",
    )
    monkeypatch.setattr(tui, "_execute_ghost_route", lambda suggestion: routed.append(suggestion) or "sent")

    handled = tui._handle_ghost_route_reply("yes ask it")

    assert handled is True
    assert routed[0].route == "main"
    assert routed[0].objective == "Open the Wraith site files and verify what got built."


def test_ghost_yes_route_it_uses_latest_markdown_suggested_ask(monkeypatch):
    tui = _make_tui(rows=12, columns=80)
    routed = []
    tui._record_ghost_history(
        "reply",
        "I want angry cow 3d running game.",
        "Got it.\n\n**Suggested ask:** Build an angry cow 3D runner web game in a standalone file.",
    )
    monkeypatch.setattr(tui, "_execute_ghost_route", lambda suggestion: routed.append(suggestion) or "sent")

    handled = tui._handle_ghost_route_reply("yes route it")

    assert handled is True
    assert routed[0].route == "main"
    assert routed[0].objective == "Build an angry cow 3D runner web game in a standalone file."


def test_ghost_bare_yes_routes_recent_mo_can_handle_next_step(monkeypatch):
    tui = _make_tui(rows=12, columns=80)
    routed = []
    tui._record_ghost_history(
        "reply",
        "what we should focus on ?",
        "Focus on **finishing this Ghost panel tweak cleanly**.\n\nNext step: **run the full gate**.\n\nMO can handle that now.",
    )
    monkeypatch.setattr(tui, "_execute_ghost_route", lambda suggestion: routed.append(suggestion) or "MO routed")
    monkeypatch.setattr(tui, "_start_ghost_route_transition", lambda *_args: None)

    handled = tui._handle_ghost_route_reply("yes")

    assert handled is True
    assert routed[0].route == "main"
    assert "run the full gate" in routed[0].objective.lower()
    assert "ghost panel tweak" in routed[0].objective.lower()


def test_ghost_provider_reply_that_offers_mo_sets_pending_route():
    tui = _make_tui(rows=12, columns=80)

    suggestion = tui._route_suggestion_from_ghost_response(
        "what we should focus on ?",
        "Focus on **finishing this Ghost panel tweak cleanly**.\nNext step: **run the full gate**.\nMO can handle that now.",
    )

    assert suggestion is not None
    assert suggestion.route == "main"
    assert "run the full gate" in suggestion.objective.lower()


def test_busy_ghost_provider_route_offer_without_suggested_ask_becomes_live_steer():
    tui = _make_tui(rows=12, columns=80)
    tui.busy = True

    suggestion = tui._route_suggestion_from_ghost_response(
        "the game is unplayable without mouse; enhance it for keyboard only",
        "Let me route this to MO — it should fix keyboard controls first.",
    )

    assert suggestion is not None
    assert suggestion.route == "steer"
    assert "keyboard" in suggestion.objective.lower()


def test_busy_ghost_provider_route_offer_for_non_current_work_queues():
    tui = _make_tui(rows=12, columns=80)
    tui.busy = True

    suggestion = tui._route_suggestion_from_ghost_response(
        "what should we focus on next?",
        "MO can handle running the full gate next.",
    )

    assert suggestion is not None
    assert suggestion.route == "queue"


def test_ghost_steer_route_injects_current_turn_update(monkeypatch):
    tui = _make_tui(rows=12, columns=80)
    tui.busy = True
    injected = []
    tui.agent.add_live_steer = lambda objective, **kwargs: injected.append((objective, kwargs)) or kwargs.get("worker_id", "")

    text = tui._execute_ghost_route(GhostRouteSuggestion("steer", "make the game keyboard only", "current adjustment"))

    assert text == "MO update injected"
    assert injected and injected[0][0] == "make the game keyboard only"
    assert injected[0][1]["worker_id"]
    assert tui.agent.workers.recent()[-1].note == "live steer queued for current MO turn"


def test_ghost_short_game_followup_infers_route_from_history():
    tui = _make_tui(rows=12, columns=80)
    tui._record_ghost_history(
        "reply",
        "what about new simple game to test Mo ?",
        "Pick a game and I’ll route it to MO to write, test, and surface the run command.",
    )

    suggestion = tui._infer_followup_route_suggestion("wordle")

    assert suggestion is not None
    assert suggestion.route == "main"
    # After simplification: objective is passed through as-is (no longer stamps "Build X game")
    assert "wordle" in suggestion.objective.lower()


def test_ghost_go_confirms_pending_route(monkeypatch):
    tui = _make_tui(rows=12, columns=80)
    routed = []
    tui._ghost_pending_route = GhostRouteSuggestion("main", "Build angry cow runner", "routeable work")
    monkeypatch.setattr(tui, "_execute_ghost_route", lambda suggestion: routed.append(suggestion) or "sent")

    handled = tui._handle_ghost_route_reply("go")

    assert handled is True
    assert routed[0].objective == "Build angry cow runner"


def test_ghost_stop_request_sets_current_turn_cancel_event():
    tui = _make_tui(rows=12, columns=80)
    tui.busy = True
    tui._current_turn_cancel_event = threading.Event()

    handled = tui._handle_ghost_control_reply("stop MO now")
    rendered = _plain(tui._get_ghost_panel_fragments())

    assert handled is True
    assert tui._current_turn_cancel_event.is_set() is True
    assert "Stop requested" in rendered
    assert tui._ghost_history[-1]["kind"] == "control_stop"


def test_ghost_conditional_stop_stops_before_visible_edits(tmp_path):
    monitor_path = tmp_path / "backend.jsonl"
    monitor_path.write_text(
        json.dumps({"ts": 101.0, "type": "tool_call", "payload": {"tool": "read_file"}}) + "\n",
        encoding="utf-8",
    )
    tui = _make_tui(rows=12, columns=80)
    tui.gateway.monitor = SimpleNamespace(path=monitor_path)
    tui.busy = True
    tui.activity_started_at = 100.0
    tui._current_turn_cancel_event = threading.Event()

    handled = tui._handle_ghost_control_reply("did mo start working and editing files? if not stop it")
    rendered = _plain(tui._get_ghost_panel_fragments())

    assert handled is True
    assert tui._current_turn_cancel_event.is_set() is True
    assert "No visible edit/write activity yet" in rendered


def test_ghost_conditional_stop_does_not_stop_after_visible_edit(tmp_path):
    monitor_path = tmp_path / "backend.jsonl"
    monitor_path.write_text(
        "\n".join([
            json.dumps({"ts": 99.0, "type": "tool_call", "payload": {"tool": "edit_file"}}),
            json.dumps({"ts": 101.0, "type": "tool_call", "payload": {"tool": "read_file"}}),
            json.dumps({"ts": 102.0, "type": "tool_result", "payload": {"tool": "edit_file"}}),
        ]),
        encoding="utf-8",
    )
    tui = _make_tui(rows=12, columns=80)
    tui.gateway.monitor = SimpleNamespace(path=monitor_path)
    tui.busy = True
    tui.activity_started_at = 100.0
    tui._current_turn_cancel_event = threading.Event()

    handled = tui._handle_ghost_control_reply("did mo start working and editing files? if not stop it")
    rendered = _plain(tui._get_ghost_panel_fragments())

    assert handled is True
    assert tui._current_turn_cancel_event.is_set() is False
    assert "current-turn edit/write activity" in rendered


def test_ghost_stop_confirmation_from_prior_stop_guidance_stops_current_turn():
    tui = _make_tui(rows=12, columns=80)
    tui.busy = True
    tui._current_turn_cancel_event = threading.Event()
    tui._record_ghost_history(
        "reply",
        "did it start editing? if not stop it",
        "No visible edit/write tools yet. Interrupt/cancel the main MO run if you want it stopped.",
    )

    handled = tui._handle_ghost_control_reply("yes i just asked you to do so")

    assert handled is True
    assert tui._current_turn_cancel_event.is_set() is True


def test_ghost_route_rejection_clears_pending_route():
    tui = _make_tui(rows=12, columns=80)
    tui._ghost_pending_route = GhostRouteSuggestion("queue", "fix checkout", "busy")

    handled = tui._handle_ghost_route_reply("no")
    rendered = _plain(tui._get_ghost_panel_fragments())

    assert handled is True
    assert "will not route" in rendered
    assert tui._ghost_pending_route is None
    assert tui._ghost_history[-1]["kind"] == "route_reject"


def test_bare_ghost_command_routes_through_slash_handler():
    tui = _make_tui(rows=12, columns=100)
    called = []
    tui.agent.process_slash_command = lambda text: called.append(text) or "Ghost controller should run"

    tui._handle_input("/ghost")
    transcript = _plain(tui._get_transcript())

    assert called == ["/ghost"]
    assert "Ghost controller" in transcript


def test_bare_ghost_command_does_not_block_reply_or_silently_replace_panel():
    tui = _make_tui(rows=12, columns=100)
    called = []
    tui._ghost_panel_open = False
    tui._ghost_unread_count = 1
    tui._ghost_panel_lines = [("class:ghost-user", "q"), ("class:ghost-response", "saved reply")]
    tui.agent.process_slash_command = lambda text: called.append(text) or "Ghost help should not replace reply"

    # After UX-225, _handle_ghost_slash_input was removed. The method no longer exists.
    assert not hasattr(tui, "_handle_ghost_slash_input")
    assert called == []
    panel = _plain(tui._get_ghost_panel_fragments())
    assert "saved reply" not in panel  # panel is still closed, reply hidden
    assert tui._ghost_unread_count == 1  # unchanged
    assert "saved reply" in "".join(style + text for style, text in tui._ghost_panel_lines)  # data intact


def test_ghost_provider_error_is_sanitized_without_raw_internalservererror():
    tui = _make_tui(rows=12, columns=100)
    exc = RuntimeError("InternalServerError: Error code: 500 - {'type': 'error', 'error': {'message': 'Internal server error'}}")

    text = tui._ghost_provider_unavailable_response("help?", exc)

    assert "Ghost provider is unavailable" in text
    assert "Internal server error" in text
    assert "InternalServerError" not in text
    assert "{'type':" not in text


def test_ghost_provider_error_keeps_route_offer_available():
    tui = _make_tui(rows=12, columns=100)
    exc = RuntimeError("Error code: 500 - {'error': {'message': 'Internal server error'}}")
    route = GhostRouteSuggestion("main", "fix the failing build", "work request", False)

    text = tui._ghost_provider_unavailable_response("can you route this?", exc, route)

    assert "Suggested ask: fix the failing build" in text


def test_ghost_history_up_down_shows_previous_messages():
    tui = _make_tui(rows=12, columns=100)
    tui._record_ghost_history("reply", "first?", "first answer")
    tui._record_ghost_history("reply", "second?", "second answer")
    tui._ghost_panel_open = True

    assert tui._show_ghost_history(-1) is True
    older = _plain(tui._get_ghost_panel_fragments())
    assert "first?" in older
    assert "first answer" in older
    assert "second answer" not in older

    assert tui._show_ghost_history(1) is True
    newer = _plain(tui._get_ghost_panel_fragments())
    assert "second?" in newer
    assert "second answer" in newer


def test_ghost_internal_reopen_restores_previous_panel_messages_without_slash_prefix():
    tui = _make_tui(rows=12, columns=100)
    tui._record_ghost_history("reply", "first?", "first answer")
    tui._record_ghost_history("reply", "second?", "second answer")
    tui._ghost_panel_open = False

    tui._apply_ghost_on()
    rendered = _plain(tui._get_ghost_panel_fragments())

    assert "second?" in rendered
    assert "second answer" in rendered
    input_buf = getattr(tui, "_input_buf", None)
    if input_buf is not None:
        assert input_buf.text == ""
        assert "/ghost" not in input_buf.text


def test_second_enter_promotes_last_queued_input_to_steer():
    tui = _make_tui(rows=12, columns=100)
    tui.busy = True

    tui._handle_input("tell mo to review this")
    promoted = tui._promote_last_queued_input_to_steer()
    item = tui._last_queued_input
    rendered = _plain(tui._get_transcript())

    assert promoted is True
    assert item and item["steer"] is True
    assert "type your message" not in rendered
    assert "Queued request selected" in rendered


def test_empty_enter_without_queued_input_does_not_latch_future_steer():
    tui = _make_tui(rows=12, columns=100)
    tui.busy = True

    assert tui._promote_last_queued_input_to_steer() is False
    tui._handle_input("later message")
    item = tui._last_queued_input
    rendered = _plain(tui._get_transcript())

    assert item and item["steer"] is False
    assert "type your message" not in rendered
    assert "steered" not in rendered


def test_escape_cancels_last_queued_input():
    tui = _make_tui(rows=12, columns=100)
    tui.busy = True
    tui._handle_input("cancel me")

    cancelled = tui._cancel_last_queued_input()
    rendered = _plain(tui._get_transcript())

    assert cancelled is True
    assert tui._pending_inputs.qsize() == 0
    assert "Queue canceled" in rendered


def test_slash_command_result_does_not_echo_raw_command_as_user_message():
    tui = _make_tui(rows=12, columns=80)
    tui.agent.process_slash_command = lambda text: "Status: OK"

    tui._handle_input("/status")
    rendered = _plain(tui._get_transcript())

    assert "* /status" not in rendered
    assert "Status: OK" in rendered


def test_palette_model_drills_into_dynamic_model_choices_and_executes_without_echo():
    tui = _make_tui(rows=12, columns=100)
    tui.agent.providers = [
        SimpleNamespace(name="opencode", model="deepseek-v4-pro"),
        SimpleNamespace(name="gemini", model="gemini-flash"),
    ]
    tui.agent.provider_index = 0
    calls = []
    tui.agent.process_slash_command = lambda text: calls.append(text) or "Switched to model: gemini / gemini-flash"
    tui._palette.enter_submenu("root", [PaletteItem("/model", "/model", "show or switch model")])

    tui._handle_palette_selection()
    assert tui._palette.in_submenu
    assert "gemini-flash" in _plain(tui._palette.get_fragments())

    tui._palette.selected_idx = 1
    tui._handle_palette_selection()
    rendered = _plain(tui._get_transcript())

    assert calls == ["/model 2"]
    assert "* /model" not in rendered
    assert "Switched to model" not in rendered
    assert tui._notice_text.startswith("Switched to model")


def test_palette_session_keeps_original_root_surface_without_added_submenu():
    tui = _make_tui(rows=12, columns=100)

    children = tui._palette_children_for_item(PaletteItem("/session", "/session", "manage sessions"))

    assert children == []


def test_palette_goal_new_inserts_prefix_without_transcript_echo():
    tui = _make_tui(rows=12, columns=100)
    tui._input_buf = SimpleNamespace(text="", cursor_position=0)
    tui._palette.enter_submenu("root", [PaletteItem("/goal", "/goal", "autonomous goal mode")])

    tui._handle_palette_selection()
    tui._handle_palette_selection()
    rendered = _plain(tui._get_transcript())

    assert tui._input_buf.text == "/goal "
    assert "* /goal" not in rendered


def test_goal_command_does_not_echo_slash_goal_as_user_message():
    tui = _make_tui(rows=12, columns=80)
    tui.agent.process_slash_command = lambda text: "[GOAL_START]"
    calls = []
    tui._start_goal_thread = lambda: calls.append("start")

    tui._handle_input("/goal check files")
    rendered = _plain(tui._get_transcript())

    assert calls == ["start"]
    assert "* /goal check files" not in rendered
    assert "Goal started" not in rendered


def test_goal_start_has_no_startup_ceremony():
    tui = _make_tui(rows=12, columns=80)
    tui.agent._goal_pending_objective = "check code files"
    tui.agent._goal_pending_budget = None
    tui.agent._goal_runner = object()
    calls = []
    import interface.main_terminal as main_terminal
    original_thread = main_terminal.threading.Thread
    main_terminal.threading.Thread = lambda *args, **kwargs: type("T", (), {"start": lambda self: calls.append("start")})()
    try:
        tui._start_goal_thread()
    finally:
        main_terminal.threading.Thread = original_thread
    rendered = _plain(tui._get_transcript())

    assert calls == ["start"]
    assert tui._goal_running is True
    assert tui._goal_worker_active is True
    assert "Goal started" not in rendered
    assert "check code files" not in rendered
    assert "Ctrl+G show/hide progress" not in rendered
    assert "/goal status" not in rendered


def test_goal_toggle_hides_finished_goal_board_without_no_active_noise():
    tui = _make_tui(rows=12, columns=80)
    tui.agent._goal_plan = object()
    tui._goal_board_text = "1 tasks (1 done, 0 open)\n√ Goal inspect"

    tui._toggle_goal_background()
    rendered = _plain(tui._get_transcript())

    assert tui._visible_goal_board_text() == ""
    assert "no active goal" not in rendered


def test_goal_toggle_background_hides_only_goal_board_but_leaves_main_board():
    tui = _make_tui(rows=12, columns=80)
    tui.agent._hints_enabled = False  # assert the plain idle line, not a rotating hint
    tui._goal_running = True
    tui.board_text = "3 tasks (0 done, 3 open)\n→ Main work"
    tui._goal_board_text = "1 tasks (0 done, 1 open)\n→ Goal inspect"

    tui._toggle_goal_background()
    status = _plain(tui._get_status_bar_fragments())

    assert tui.board_text == "3 tasks (0 done, 3 open)\n→ Main work"
    assert tui._visible_goal_board_text() == ""
    assert "idle" in status
    assert "Goal backgrounded" not in status
    assert "Ctrl+G show" not in status


def test_goal_board_renders_separately_from_main_board():
    tui = _make_tui(rows=16, columns=80)
    tui.board_text = "3 tasks (0 done, 3 open)\n→ Main work"
    tui._goal_board_text = "2 tasks (1 done, 1 open)\n√ Goal inspect\n→ Goal run"

    main_rendered = _plain(tui._get_board_fragments())
    goal_rendered = _plain(tui._get_goal_board_fragments())

    assert "Main work" in main_rendered
    assert "Goal run" not in main_rendered
    assert "Goal run" in goal_rendered
    assert "Main work" not in goal_rendered



def test_goal_background_idle_status_stays_compact_and_footer_reports_running():
    tui = _make_tui(rows=12, columns=80)
    tui.agent._hints_enabled = False  # assert the plain idle line, not a rotating hint
    tui._goal_worker_active = True
    tui._goal_backgrounded = True
    tui._goal_started_at = 1

    status = _plain(tui._get_status_bar_fragments())
    footer = _plain(tui._get_footer_fragments())

    assert "idle" in status
    assert "MO idle" not in status
    assert "Goal hidden" not in status
    assert "Goal running" in footer



def test_prt_report_routes_to_transcript_panel_and_footer_notification():
    tui = _make_tui(rows=12, columns=120)
    assert tui.agent.tui is tui
    report = ReviewReport(
        diff_ref="HEAD",
        files_changed=1,
        additions=2,
        deletions=1,
        findings=[ReviewFinding(
            id="f1",
            severity="minor",
            category="docs",
            file="docs/a.md",
            line_range=[3, 3],
            message="Docs finding",
            explanation="Needs evidence.",
            suggestion="Add link.",
            confidence=0.8,
            evidence_tools=["read_file:docs/a.md"],
        )],
        score=5.0,
        unresolved_count=1,
        affected_tests=[],
        created_at=0.0,
        token_usage={"total_tokens": 25},
    )

    route_prt_report(tui.agent, report)

    transcript = _plain(tui._lines)
    assert "PRT checked commit HEAD" in transcript
    assert "[MINOR] docs/a.md:3 - Docs finding" in transcript
    assert "1 finding(s), 1 unresolved" in transcript
    assert "Score: 5.0/5.0 [unresolved]" in transcript
    assert tui._ghost_unread_count == 0
    assert tui._prt_done_unread is True
    assert tui._ghost_panel_open is False
    assert _plain(tui._get_ghost_panel_fragments()) == ""
    assert any("PRT score: 5.0/5.0 [unresolved]" in text for _style, text in tui._ghost_panel_lines)
    assert "PRT ready" in _plain(tui._get_footer_fragments())
    assert "Alt+G" in _plain(tui._get_footer_fragments())
    assert "/ghost" not in _plain(tui._get_footer_fragments())


def test_prt_report_does_not_interleave_transcript_while_main_turn_busy():
    tui = _make_tui(rows=12, columns=120)
    tui.busy = True
    queued = []
    tui._queue_input = lambda text, **kwargs: queued.append((text, kwargs))
    report = ReviewReport(
        diff_ref="HEAD",
        files_changed=1,
        additions=1,
        deletions=0,
        findings=[],
        score=5.0,
        unresolved_count=0,
        affected_tests=[],
        created_at=0.0,
        token_usage={"total_tokens": 0},
    )

    before = list(tui._lines)
    route_prt_report(tui.agent, report)

    assert tui._lines == before
    assert tui._prt_done_unread is True
    assert tui._ghost_panel_open is False
    assert any("PRT score: 5.0/5.0 [clean]" in text for _style, text in tui._ghost_panel_lines)
    assert "Alt+G" in tui._notice_text
    assert "/ghost" not in tui._notice_text


def test_footer_notification_flips_only_when_multiple_items():
    tui = _make_tui(rows=12, columns=140)
    tui._ghost_unread_count = 2
    tui._goal_worker_active = True
    tui.agent.context_budget_tokens = 1000
    tui.agent.context_budget_source = "test"
    tui.agent.session.messages = [{"role": "user", "content": "x" * 400}]

    frag = tui._footer_notification_fragment()
    notice_text = frag[1] if frag else ""
    footer = _plain(tui._get_footer_fragments())

    assert notice_text in {"Ghost replied (2): Alt+G", "Goal running"}
    assert "ctx" not in footer
    assert "handoff" not in footer.lower()


def test_goal_progress_updates_live_board_without_transcript_spam():
    tui = _make_tui()
    tui._goal_backgrounded = False
    board = TaskBoard("goal", "goal", [TaskItem("1", "Inspect", "completed", ["read_file:x"]), TaskItem("2", "Verify", "active")])
    runner = SimpleNamespace(to_task_board=lambda plan: board)
    tui.agent._goal_runner = runner
    tui.agent._goal_plan = SimpleNamespace()

    before = len(tui._lines)
    tui._goal_show_progress("[GOAL] 1/2 done · iter 7 · next: Verify")

    assert len(tui._lines) == before
    assert "Verify" in tui._goal_board_text


def test_goal_finish_foreground_reports_summary_and_hides_taskboard():
    tui = _make_tui(rows=20, columns=80)
    board = TaskBoard("goal", "goal", [TaskItem("1", "Inspect", "completed", ["read_file:x"]), TaskItem("2", "Report", "completed", ["final"])])
    tui.agent._goal_plan = object()
    tui.agent._goal_runner = type("Runner", (), {"to_task_board": lambda self, plan: board})()

    tui._goal_finish("[✓ DONE] Goal: 2/2 done · 1s\nall steps done")
    rendered = _plain(tui._get_transcript())

    assert "Goal finished" in rendered
    assert "Goal report" in rendered
    assert "✓" not in rendered
    assert "2 tasks (2 done, 0 open)" not in rendered
    assert "√ Inspect" not in rendered
    assert _plain(tui._get_goal_board_fragments()) == ""


def test_goal_command_starts_immediately_while_main_busy_when_no_goal_active():
    tui = _make_tui(rows=12, columns=80)
    tui.busy = True
    started = []

    def fake_process(text):
        tui.agent._goal_pending_objective = "review visuals"
        return "[GOAL_START]"

    tui.agent.process_slash_command = fake_process
    tui._start_goal_thread = lambda: started.append(True)

    tui._handle_input("/goal review visuals")

    assert started == [True]
    assert tui._pending_inputs.qsize() == 0


def test_goal_pause_summary_uses_no_emoji_glyph():
    tui = _make_tui(rows=20, columns=80)

    tui._goal_finish("[PAUSED] Goal: 1/3 done · 5m\ntime budget reached")
    rendered = _plain(tui._get_transcript())

    assert "Goal paused Goal: 1/3 done" in rendered
    assert "⏸" not in rendered
    assert "▣" not in rendered


def test_background_goal_finish_uses_footer_notification_when_panel_hidden():
    tui = _make_tui(rows=12, columns=120)
    tui._goal_backgrounded = True
    tui.agent._goal_runner = None

    tui._goal_finish("[✓ DONE] Goal: 1/1 done")
    rendered = _plain(tui._get_transcript())
    footer = _plain(tui._get_footer_fragments())

    assert "Goal finished" not in rendered
    assert "Goal done" in footer
    assert "Goal finished" in tui._ghost_panel_lines[0][1]
    assert tui._ghost_history[-1]["kind"] == "notification"


def test_ghost_provider_messages_strip_tool_call_chains():
    raw = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "tool result"},
        {"role": "assistant", "content": "visible"},
    ]

    messages = MoTui._ghost_provider_messages(raw, "ask")

    assert {m["role"] for m in messages} <= {"system", "user", "assistant"}
    assert all("tool_calls" not in m for m in messages)
    assert "tool result" not in "\n".join(m["content"] for m in messages)
    assert messages[-1]["content"] == "ask"


def test_ghost_empty_provider_text_gets_visible_fallback_not_no_response():
    response = SimpleNamespace(content="")
    suggestion = GhostRouteSuggestion("main", "random task new game", "idle")

    result = MoTui._ghost_visible_response(response, suggestion)

    assert "No response" not in result
    assert "MO can handle" in result


def test_stale_ghost_reply_is_kept_in_history_without_overwriting_active_panel(monkeypatch):
    tui = _make_tui()
    monkeypatch.setattr("interface.ghost_history.append_ghost_audit", lambda *args, **kwargs: None)
    current_pending = GhostRouteSuggestion("main", "current ask", "idle")
    tui._ghost_pending_route = current_pending
    tui._ghost_panel_open = True
    tui._ghost_panel_lines = [("class:ghost-user", "new question"), ("class:ghost-response", "new answer")]
    before_panel = list(tui._ghost_panel_lines)

    tui._record_stale_ghost_reply("old question", "old answer", GhostRouteSuggestion("main", "old ask", "idle"))

    assert tui._ghost_panel_lines == before_panel
    assert tui._ghost_pending_route is current_pending
    assert tui._ghost_history[-1]["kind"] == "reply_stale"
    assert tui._ghost_history[-1]["user"] == "old question"
    assert tui._ghost_history[-1]["response"] == "old answer"


def test_one_off_ghost_question_does_not_keep_input_in_ghost_mode():
    tui = _make_tui()
    tui._ghost_input_mode = False
    tui._input_buf = SimpleNamespace(text="hello", cursor_position=5)
    tui._ghost_panel_lines = []
    tui._record_ghost_history = lambda *args, **kwargs: None
    tui._app = None

    # Simulate the post-reply input reset condition without starting a provider thread.
    if tui._ghost_input_mode and tui._input_buf:
        tui._input_buf.text = "/ghost "

    assert tui._input_buf.text == "hello"


def test_ghost_response_incomplete_detects_cutoff_text():
    assert MoTui._ghost_response_incomplete("- worker active\n- Two", "") is True
    assert MoTui._ghost_response_incomplete("All good.", "") is False
    assert MoTui._ghost_response_incomplete("complete enough", "length") is True


def test_transcript_and_ghost_fragments_do_not_capture_mouse_selection():
    tui = _make_tui(rows=14, columns=80)
    tui._add("class:mo-response", "line")
    tui._ghost_panel_open = True
    tui._ghost_panel_lines = [("class:ghost-response", "ghost line")]

    assert all(len(fragment) == 2 for fragment in tui._get_transcript())
    assert all(len(fragment) == 2 for fragment in tui._get_ghost_panel_fragments())


def test_ghost_hint_mentions_expand_by_default_and_history_when_expanded():
    tui = _make_tui(rows=12, columns=100)
    tui._ghost_panel_open = True
    tui._ghost_panel_lines = [("class:ghost-response", "hello")]

    compact = _plain(tui._get_ghost_panel_fragments())

    assert "Alt+G/Esc hide" in compact
    assert "Ctrl+O expand" in compact
    assert "hist ↑/↓" not in compact

    tui._ghost_expanded = True
    expanded = _plain(tui._get_ghost_panel_fragments())
    assert "Ctrl+O collapse" in expanded
    assert "hist ↑/↓" in expanded
    assert "scroll Ctrl+↑/↓" in expanded


def test_main_transcript_scrolls_after_ghost_panel_is_hidden():
    tui = _make_tui(rows=9, columns=80)
    for index in range(30):
        tui._add("class:mo-response", f"line {index}")
    tui._ghost_panel_open = True
    tui._ghost_panel_lines = [("class:ghost-response", "ghost answer")]

    tui._ghost_panel_open = False
    tui._scroll_transcript(8)
    rendered = _plain(tui._get_transcript())

    assert "line 29" not in rendered
    assert any(f"line {index}" in rendered for index in range(10, 24))


def test_ghost_ctrl_scroll_state_clamps_to_panel_rows():
    tui = _make_tui(rows=14, columns=80)
    tui._ghost_panel_open = True
    tui._ghost_panel_lines = [("class:ghost-response", "\n".join(f"ghost line {index}" for index in range(20)))]

    tui._scroll_ghost(999)

    assert tui._ghost_scroll_from_bottom == tui._max_ghost_scroll()

    tui._scroll_ghost(-999)

    assert tui._ghost_scroll_from_bottom == 0


def test_ghost_scroll_clamps_to_rendered_rows():
    tui = _make_tui(rows=14, columns=80)
    tui._ghost_panel_open = True
    tui._ghost_panel_lines = [("class:ghost-response", "\n".join(f"ghost line {index}" for index in range(8)))]

    tui._scroll_ghost(999)
    tui._get_ghost_panel_fragments()

    assert tui._ghost_scroll_from_bottom == tui._max_ghost_scroll()


def test_ghost_panel_surface_handles_newlines_and_wide_glyphs():
    tui = _make_tui(rows=20, columns=100)
    tui._ghost_panel_open = True
    tui._ghost_panel_lines = [
        ("class:ghost-response", "line one ✅\n**Two things** worth a look:\n`memory_index`: empty and that specific line should wrap cleanly"),
    ]

    rendered = _plain(tui._get_ghost_panel_fragments())
    lines = rendered.splitlines()
    widths = [get_cwidth(line) for line in lines]

    assert "BTW" not in rendered
    assert "Ghost" in rendered
    assert "Esc hide" in rendered
    assert "Ctrl+O" in rendered
    assert "**" not in rendered
    assert "sp\necific" not in rendered
    assert max(widths) <= 100
    assert lines[0].startswith("↯")
    assert not any(line.startswith(("╭", "╰")) for line in lines)
