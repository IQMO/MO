import ast
from pathlib import Path


GOAL_METHODS = {
    "_toggle_goal_background",
    "_start_goal_thread",
    "_resume_goal_thread",
    "_run_existing_goal_loop",
    "_run_goal_loop",
    "_goal_show_progress",
    "_goal_finish",
}


def _class_methods(path: str, class_name: str) -> set[str]:
    module = ast.parse(Path(path).read_text(encoding="utf-8"))
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {item.name for item in node.body if isinstance(item, ast.FunctionDef)}
    return set()


def test_goal_ui_lifecycle_methods_have_single_canonical_owner():
    main_methods = _class_methods("interface/main_terminal.py", "MoTui")
    goal_methods = _class_methods("interface/tui_goal.py", "GoalUiMixin")

    assert GOAL_METHODS <= goal_methods
    assert main_methods & GOAL_METHODS == set()


def test_worker_status_has_single_tui_owner():
    main_methods = _class_methods("interface/main_terminal.py", "MoTui")
    worker_methods = _class_methods("interface/worker_status.py", "WorkerStatusMixin")

    assert "_workers_status_text" in worker_methods
    assert "_workers_status_text" not in main_methods


def test_palette_wrappers_have_single_tui_owner():
    palette_methods = {"_palette_children_for_item", "_model_palette_items"}
    main_methods = _class_methods("interface/main_terminal.py", "MoTui")
    mixin_methods = _class_methods("interface/palette_mixin.py", "PaletteMixin")

    assert palette_methods <= mixin_methods
    assert main_methods & palette_methods == set()


def test_tui_app_has_single_run_owner():
    main_methods = _class_methods("interface/main_terminal.py", "MoTui")
    mixin_methods = _class_methods("interface/tui_app.py", "TuiAppMixin")

    assert "run" in mixin_methods
    assert "run" not in main_methods


def test_turn_runner_has_single_tui_owner():
    main_methods = _class_methods("interface/main_terminal.py", "MoTui")
    mixin_methods = _class_methods("interface/turn_runner.py", "TurnRunnerMixin")

    assert "_run_turn_thread" in mixin_methods
    assert "_run_turn_thread" not in main_methods


def test_display_delegates_have_single_tui_owner():
    display_methods = {
        "_ghost_panel_dimensions",
        "_ghost_panel_content_rows",
        "_max_ghost_scroll",
        "_get_activity_fragments",
        "_visible_goal_board_text",
        "_get_goal_board_fragments",
        "_get_board_fragments",
        "_get_task_board_fragments",
        "_get_footer_fragments",
        "_get_status_bar_fragments",
        "_get_ghost_panel_fragments",
        "_scroll_ghost",
        "_get_separator_fragments",
    }
    main_methods = _class_methods("interface/main_terminal.py", "MoTui")
    mixin_methods = _class_methods("interface/display_delegates.py", "DisplayDelegatesMixin")

    assert display_methods <= mixin_methods
    assert main_methods & display_methods == set()


def test_response_helpers_have_single_tui_owner():
    response_methods = {"_proposal_chat_text", "_add_response_line", "_add_response_block"}
    main_methods = _class_methods("interface/main_terminal.py", "MoTui")
    mixin_methods = _class_methods("interface/response_mixin.py", "ResponseMixin")

    assert response_methods <= mixin_methods
    assert main_methods & response_methods == set()


def test_transcript_state_has_single_tui_owner():
    transcript_methods = {
        "_add",
        "_add_fragments_line",
        "_append_transcript_fragments",
        "_clear_transcript",
        "_get_transcript",
        "_logical_transcript_lines",
        "_visual_transcript_rows",
        "_transcript_line_count",
        "_visible_transcript_height",
        "_scroll_transcript",
        "_transcript_top",
        "_transcript_bottom",
    }
    main_methods = _class_methods("interface/main_terminal.py", "MoTui")
    mixin_methods = _class_methods("interface/transcript_state.py", "TranscriptStateMixin")

    assert transcript_methods <= mixin_methods
    assert main_methods & transcript_methods == set()


def test_ghost_controller_has_single_tui_owner():
    ghost_methods = {
        "_ghost_context_snapshot",
        "_ghost_panel_ask",
        "_ghost_provider_messages",
        "_ghost_visible_response",
        "_ghost_response_incomplete",
        "_handle_ghost_route_reply",
        "_looks_like_implicit_route_confirmation",
        "_implicit_ghost_route_from_history",
        "_extract_suggested_main_ask",
        "_execute_ghost_route",
        "_ghost_route_receiver_line",
        "_ghost_route_state_line",
        "_ghost_route_receipt",
        "_ghost_route_transition_glyph",
        "_start_background_worker_from_ghost",
    }
    main_methods = _class_methods("interface/main_terminal.py", "MoTui")
    mixin_methods = _class_methods("interface/ghost_controller.py", "GhostControllerMixin")

    assert ghost_methods <= mixin_methods
    assert main_methods & ghost_methods == set()


def test_input_dispatch_has_single_tui_owner():
    dispatch_methods = {
        "_on_input_changed",
        "_handle_palette_selection",
        "_run_palette_command",
        "_dispatch_slash_command_result",
        "_handle_input",
    }
    main_methods = _class_methods("interface/main_terminal.py", "MoTui")
    mixin_methods = _class_methods("interface/input_dispatch.py", "InputDispatchMixin")

    assert dispatch_methods <= mixin_methods
    assert main_methods & dispatch_methods == set()


def test_queueing_has_single_tui_owner():
    queue_methods = {
        "_queue_input",
        "_drain_pending_inputs",
        "_restore_pending_inputs",
        "_advance_queued_input_intent",
        "_promote_last_queued_input_to_steer",
        "_request_current_turn_stop_for_steer",
        "_cancel_last_queued_input",
        "_queue_goal_command",
        "_process_next_queued_input",
    }
    main_methods = _class_methods("interface/main_terminal.py", "MoTui")
    mixin_methods = _class_methods("interface/queueing.py", "QueueingMixin")

    assert queue_methods <= mixin_methods
    assert main_methods & queue_methods == set()


def test_legacy_slash_commands_file_is_only_registry_exports():
    text = Path("interface/slash_commands.py").read_text(encoding="utf-8")

    assert "from .command_registry import" in text
    assert "SLASH_COMMANDS: dict" not in text
    assert "SLASH_ALIASES: dict" not in text
    assert "SLASH_SUBCOMMANDS: dict" not in text
    assert "SLASH_COMMAND_HELP =" not in text


def test_command_palette_does_not_reintroduce_hardcoded_categories():
    text = Path("interface/command_palette.py").read_text(encoding="utf-8")

    assert "from .command_registry import" in text
    assert "PALETTE_CATEGORIES: list" not in text
    assert "(\"Tasks\", [" not in text


def test_ghost_thinking_labels_stay_professional():
    text = Path("interface/ghost_controller.py").read_text(encoding="utf-8")

    assert "Replying" in text
    assert "Replying.." not in text
    assert "Answering.." not in text
    assert "Kitchening" not in text
    assert "GHOSTING" not in text
