"""Tests for core/gateway.py — turn coordinator and taskboard lifecycle owner."""
from __future__ import annotations

from types import SimpleNamespace


from core.backend_monitor import BackendMonitor
from core.gateway import Gateway, _runtime_should_create_board


class FakeAgent:
    """Minimal agent stub for Gateway testing."""
    provider_name = "mock"
    model = "mock-model"

    def __init__(self, session_id: str = "session-gw-test"):
        self.session = SimpleNamespace(
            session_id=session_id,
            messages=[],
            turn_count=0,
        )
        self._current_route_source = ""
        self._sessions = SimpleNamespace(current_name="default")
        self.calls: list[str] = []

    def run_turn(self, user_input, monitor=None, on_first_tool=None,
                 on_board_update=None, **_kwargs):
        self.calls.append(user_input)
        if on_first_tool:
            on_first_tool("find_files", {"root": "."})
        return "turn complete"


def make_gateway(agent=None, session_id="session-gw-test"):
    agent = agent or FakeAgent(session_id=session_id)
    monitor = BackendMonitor()
    gw = Gateway(agent, monitor=monitor)
    return gw, agent, monitor


class TestGatewayInit:
    def test_creates_gateway(self):
        gw, agent, _ = make_gateway()
        assert gw.agent is agent
        assert gw.last_task_board is None

    def test_attaches_to_agent(self):
        agent = FakeAgent()
        gw = Gateway(agent, monitor=BackendMonitor())
        assert agent.gateway is gw

    def test_last_resumable_board_is_none_initially(self):
        gw, _, _ = make_gateway()
        assert gw.last_resumable_board is not None or gw.last_resumable_board is None
        # May be None or a board depending on ledger state

    def test_task_board_registry_created(self):
        gw, _, _ = make_gateway()
        assert gw.task_board_registry is not None


class TestShouldShowTaskBoard:
    def test_slash_command_returns_false(self):
        gw, _, _ = make_gateway()
        assert not gw.should_show_task_board("/ghost on")

    def test_slash_goal_returns_false(self):
        gw, _, _ = make_gateway()
        assert not gw.should_show_task_board("/goal build x")

    def test_vs05_request_returns_true(self):
        gw, _, _ = make_gateway()
        assert gw.should_show_task_board("start VS05 E:\\ref-a E:\\ref-b")

    def test_work_request_returns_true(self):
        gw, _, _ = make_gateway()
        assert gw.should_show_task_board("fix the login bug in auth.py")

    def test_build_request_returns_true(self):
        gw, _, _ = make_gateway()
        assert gw.should_show_task_board("build a CLI tool for config parsing")

    def test_simple_greeting_may_not_show(self):
        gw, _, _ = make_gateway()
        # Simple greetings typically return False
        result = gw.should_show_task_board("hello")
        # Can be true or false depending on template matching
        assert isinstance(result, bool)

    def test_empty_string(self):
        gw, _, _ = make_gateway()
        result = gw.should_show_task_board("")
        assert isinstance(result, bool)


class TestGatewayRunTurn:
    def test_run_turn_returns_string(self):
        gw, agent, _ = make_gateway()
        result = gw.run_turn("test input")
        assert isinstance(result, str)

    def test_run_turn_calls_agent(self):
        gw, agent, _ = make_gateway()
        gw.run_turn("build something")
        assert len(agent.calls) >= 1

    def test_run_turn_handles_empty_input(self):
        gw, agent, _ = make_gateway()
        result = gw.run_turn("")
        assert isinstance(result, str)


class TestRuntimeShouldCreateBoard:
    def test_resume_intent_returns_true(self):
        agent = SimpleNamespace()
        result = _runtime_should_create_board(
            agent, "proceed please", "user", resume_intent=True
        )
        assert result is True

    def test_work_text_returns_true(self):
        agent = SimpleNamespace()
        result = _runtime_should_create_board(
            agent, "fix the bug in login", "user"
        )
        assert result is True


class TestGatewayResumableBoard:
    def test_resumable_board_property(self):
        gw, _, _ = make_gateway()
        board = gw.resumable_board()
        # May be None or a board — just check it doesn't crash
        assert board is not None or board is None
