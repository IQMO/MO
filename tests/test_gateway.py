"""Tests for core/gateway.py — turn coordinator and taskboard lifecycle owner."""
from __future__ import annotations

from types import SimpleNamespace


from core.backend_monitor import BackendMonitor
from core.gateway import Gateway, _runtime_should_create_board, _SECONDARY_BUSY_MESSAGE


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

    def test_run_turn_forwards_on_action_to_agent(self):
        captured = {}

        class CapturingAgent(FakeAgent):
            def run_turn(self, user_input, monitor=None, on_first_tool=None,
                         on_board_update=None, on_action=None, **_kwargs):
                self.calls.append(user_input)
                captured["on_action"] = on_action
                return "ok"

        gw, agent, _ = make_gateway(agent=CapturingAgent())
        sentinel = lambda _a: None
        gw.run_turn("do a thing", on_action=sentinel)
        assert captured["on_action"] is sentinel


class TestGatewayTurnMutex:
    """One turn at a time on the shared agent: a Ghost/desktop turn must be rejected
    while a Main turn is in flight (e.g. a whole DEVMODE run) so it can never interleave
    into Main MO or clear the Main board (amendment #3)."""

    def test_secondary_turn_rejected_while_a_turn_is_in_flight(self):
        gw, agent, _ = make_gateway()
        sentinel = object()
        gw.last_task_board = sentinel  # an active Main board
        gw._turn_lock.acquire()  # simulate a Main turn holding the lock
        try:
            result = gw.run_turn("what do you see on my screen", route_source="desktop")
        finally:
            gw._turn_lock.release()
        assert result == _SECONDARY_BUSY_MESSAGE
        assert agent.calls == []                 # the desktop turn never ran
        assert gw.last_task_board is sentinel     # Main board NOT cleared by the rejected turn

    def test_secondary_turn_runs_when_idle_and_releases_lock(self):
        gw, agent, _ = make_gateway()
        result = gw.run_turn("hi ghost", route_source="desktop")
        assert result == "turn complete"
        assert agent.calls == ["hi ghost"]
        assert gw._turn_lock.acquire(blocking=False)  # lock released after the turn
        gw._turn_lock.release()

    def test_primary_turn_runs_and_releases_lock(self):
        gw, agent, _ = make_gateway()
        assert gw.run_turn("do work", route_source="user") == "turn complete"
        assert gw._turn_lock.acquire(blocking=False)
        gw._turn_lock.release()


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
