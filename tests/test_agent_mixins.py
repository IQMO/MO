"""Focused seam tests for extracted Agent mixins."""
from __future__ import annotations

from types import SimpleNamespace

from core.agent.agent import Agent
from core.backend_monitor import BackendMonitor
from core.tasking.task_board import TaskBoard, TaskItem


def _agent() -> Agent:
    return object.__new__(Agent)


def test_taskboard_mixin_complete_task_advances_next_ready_and_emits_monitor(tmp_path):
    board = TaskBoard(tasks=[
        TaskItem("1", "Inspect", "active", evidence=["grep:core"], kind="inspect", completion_gate="tool"),
        TaskItem("2", "Report", "pending", kind="report", completion_gate="final", depends_on=["1"]),
    ])
    monitor = BackendMonitor(tmp_path / "monitor.jsonl")

    assert _agent()._advance_task_board_after_tool(board, "complete_task", {}, monitor=monitor) is True

    assert board.task("1").status == "completed"
    assert board.task("2").status == "active"
    text = (tmp_path / "monitor.jsonl").read_text(encoding="utf-8")
    assert "board_advance" in text
    assert "\"completed\": \"1\"" in text


def test_taskboard_mixin_finalize_leaves_unfinished_tool_gate_active():
    board = TaskBoard(tasks=[
        TaskItem("1", "Verify", "active", kind="verify", completion_gate="verification"),
        TaskItem("2", "Report", "pending", kind="report", completion_gate="final"),
    ])

    assert _agent()._finalize_task_board_for_answer(board) is False

    assert board.task("1").status == "active"
    assert board.task("1").blocker == ""
    assert board.task("2").status == "pending"


def test_prt_mixin_claimed_paths_accepts_existing_path(tmp_path):
    existing = tmp_path / "core" / "agent.py"
    existing.parent.mkdir()
    existing.write_text("# source\n", encoding="utf-8")
    agent = _agent()
    agent.project_cwd = str(tmp_path)
    agent.workspace = str(tmp_path)

    assert agent._prt_claimed_paths("core/agent.py") == ["core/agent.py"]


def test_prt_mixin_claimed_paths_returns_empty_for_invalid_git_ref(tmp_path):
    agent = _agent()
    agent.project_cwd = str(tmp_path)
    agent.workspace = str(tmp_path)

    assert agent._prt_claimed_paths("not-a-ref") == []


def test_slash_mixin_dispatches_help_alias_without_provider():
    agent = _agent()

    result = agent.process_slash_command("/h")

    assert isinstance(result, str)
    assert "/help" in result or "Commands" in result


def test_slash_mixin_unknown_command_falls_through():
    assert _agent().process_slash_command("/definitely-not-real") is None


def test_owner_comparison_slash_routes_to_normal_turn_prompt():
    agent = _agent()

    result = agent.process_slash_command("/OWNER_COMPARISON E:\\ref-a E:\\ref-b")

    assert result == "[RUN_TURN]"
    assert agent._slash_pending_input == "start OWNER_COMPARISON E:\\ref-a E:\\ref-b"


def test_turn_mixin_identity_answer_uses_runtime_provider_and_model():
    agent = _agent()
    agent.provider_name = "mock-provider"
    agent.model = "mock-model"

    result = agent._maybe_handle_identity_turn("what model are you using?")

    assert result is not None
    assert "MO" in result
    assert "mock-provider/mock-model" in result


def test_turn_mixin_identity_intercept_ignores_task_questions():
    """'what/who are you <verb>' is real work, not an identity question."""
    agent = _agent()
    agent.provider_name = "mock-provider"
    agent.model = "mock-model"
    for text in (
        "if i say deploy what are you going to do ?",
        "what are you working on",
        "who are you deploying for",
    ):
        assert agent._maybe_handle_identity_turn(text) is None, text
    # Genuine identity questions still answer.
    for text in ("who are you", "what are you?", "what is mo"):
        assert agent._maybe_handle_identity_turn(text) is not None, text


def test_turn_mixin_init_ignores_non_init_text():
    assert _agent()._maybe_handle_init_turn("initialize the plan") is None


def test_turn_mixin_prepare_start_handles_identity_intercept_without_provider():
    agent = _agent()
    agent.provider_name = "mock-provider"
    agent.model = "mock-model"
    agent.session = SimpleNamespace(messages=[], turn_count=0, add_user=lambda text: agent.session.messages.append({"role": "user", "content": text}), add_assistant=lambda text, **_kwargs: agent.session.messages.append({"role": "assistant", "content": text}))
    agent._scan_user_input = lambda _text: None
    agent._quarantine_unfinished_tail_before_turn = lambda _text, monitor=None: {}
    agent._pause_interrupted_work_for_return = lambda _text, _meta, monitor=None: None
    agent._pre_turn_context_handoff = lambda _text: False
    agent._record_turn_memory_only = lambda _user, _assistant: None

    result = agent._prepare_turn_start("who are you and what model are you using?")

    assert result["kind"] == "identity"
    assert "mock-provider/mock-model" in str(result["final_text"])
    assert agent.session.turn_count == 1
    assert [item["role"] for item in agent.session.messages] == ["user", "assistant"]


def test_quarantine_drop_surfaces_user_notice():
    """A dropped session tail must be surfaced to the user, not only the monitor."""
    agent = _agent()
    agent.last_quarantine_notice = ""
    agent._looks_like_return_greeting = lambda _t: False
    agent.session = SimpleNamespace(
        quarantine_unfinished_tail=lambda drop_unanswered_user=False: {
            "changed": True, "dropped_messages": 2, "reason": "unfinished_tool_turn",
        }
    )

    meta = agent._quarantine_unfinished_tail_before_turn("continue the build")

    assert meta.get("changed")
    notice = agent.consume_quarantine_notice()
    assert "dropped 2 stale message" in notice
    assert agent.consume_quarantine_notice() == ""  # consumed once, then cleared


def test_quarantine_parks_dropped_objective_as_anchor():
    """Drift fix: dropping an unfinished tail parks the objective so the next
    (often vague) continuation turn re-anchors instead of free-associating."""
    agent = _agent()
    agent.last_quarantine_notice = ""
    agent._looks_like_return_greeting = lambda _t: False
    agent._pending_interrupted_work = {}
    agent.session = SimpleNamespace(
        quarantine_unfinished_tail=lambda drop_unanswered_user=False: {
            "changed": True, "dropped_messages": 3, "reason": "unfinished_tool_turn",
            "user": "build the companion tray",
        }
    )

    agent._quarantine_unfinished_tail_before_turn("try again")

    assert agent._pending_interrupted_work.get("user") == "build the companion tray"


def test_lane_scope_thread_local_override():
    """Guide-mode lane override is thread-local and restored on exit."""
    agent = _agent()
    agent._active_lane = None
    assert agent._effective_lane() is None
    with agent.lane_scope("companion-guide"):
        assert agent._effective_lane() == "companion-guide"
    assert agent._effective_lane() is None  # restored, no leak


def test_quarantine_without_objective_parks_nothing():
    agent = _agent()
    agent.last_quarantine_notice = ""
    agent._looks_like_return_greeting = lambda _t: False
    agent._pending_interrupted_work = {}
    agent.session = SimpleNamespace(
        quarantine_unfinished_tail=lambda drop_unanswered_user=False: {
            "changed": True, "dropped_messages": 1, "reason": "unfinished_tool_turn",
        }
    )

    agent._quarantine_unfinished_tail_before_turn("hello")

    assert not agent._pending_interrupted_work.get("user")
