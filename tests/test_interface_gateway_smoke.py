from core.backend_monitor import BackendMonitor
from core.gateway import Gateway
from core.tasking.task_board import TaskBoard, TaskItem


class NoToolAgent:
    provider_name = "mock"
    model = "mock"

    def __init__(self):
        self.calls = []

    def run_turn(self, user_input, task_board=None, monitor=None, on_board_update=None, on_token=None, on_activity=None, on_first_tool=None):
        self.calls.append((user_input, task_board))
        if on_activity:
            on_activity("thinking (request #1)...")
        return "A long provider-side answer without tools."


class ProposalAgent(NoToolAgent):
    def propose_work(self, user_input, monitor=None):
        self.proposals = getattr(self, "proposals", 0) + 1
        return (
            "Proposal: Build a focused football game.\n"
            "Assumptions: Use stdlib terminal controls.\n"
            "Plan:\n"
            "- Inspect existing game files\n"
            "- Write football game code\n"
            "- Verify launch command"
        )


class ToolStartingAgent(NoToolAgent):
    def run_turn(self, user_input, task_board=None, monitor=None, on_board_update=None, on_token=None, on_activity=None, on_first_tool=None):
        self.calls.append((user_input, task_board))
        if on_first_tool:
            on_first_tool()
        return "used one tool"


class RuntimeSignalAgent(NoToolAgent):
    def __init__(self, tool_name, arguments=None):
        super().__init__()
        self.tool_name = tool_name
        self.arguments = arguments or {}

    def run_turn(self, user_input, task_board=None, monitor=None, on_board_update=None, on_token=None, on_activity=None, on_first_tool=None):
        self.calls.append((user_input, task_board))
        if on_first_tool:
            on_first_tool(self.tool_name, self.arguments)
        return "used runtime signal"


def test_should_show_task_board_is_work_gated_not_simple_status(tmp_path):
    gateway = Gateway(NoToolAgent(), monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    assert gateway.should_show_task_board("thats a good news") is False
    assert gateway.should_show_task_board("report the recent prt") is False  # simple_chat
    assert gateway.should_show_task_board("build a football game") is True
    assert gateway.should_show_task_board("review the interface taskboard code") is True
    assert gateway.should_show_task_board("check logs memory session performance and taskboard state") is False  # simple_chat per template
    assert gateway.should_show_task_board("how would you research this codebase?") is False


def test_deep_review_always_gets_board_now(tmp_path):
    """After simplification: deep_review always gets a board (not chat = board)."""
    gateway = Gateway(NoToolAgent(), monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    # All deep_review/investigation work gets a board now
    assert gateway.should_show_task_board("audit the codebase") is True
    assert gateway.should_show_task_board("investigate the taskboard rendering pipeline") is True
    assert gateway.should_show_task_board("review the entire gateway worker ghost context performance docs") is True
    assert gateway.should_show_task_board("audit the repo for dead code and stale tests") is True
    assert gateway.should_show_task_board("scan for bugs") is True
    assert gateway.should_show_task_board("find where logging is configured") is True
    assert gateway.should_show_task_board("inspect the config loader") is True

    # Only simple_chat (greetings/status) still gets no board
    assert gateway.should_show_task_board("quick check on that file") is False
    assert gateway.should_show_task_board("thats a good news") is False


def test_gateway_clears_stale_board_at_new_turn(tmp_path):
    monitor_path = tmp_path / "monitor.jsonl"
    gateway = Gateway(NoToolAgent(), monitor=BackendMonitor(monitor_path))
    gateway.last_task_board = TaskBoard(tasks=[TaskItem("1", "old", "completed")])

    gateway.run_turn("hello there")

    assert gateway.last_task_board is None


def test_gateway_board_created_for_complex_review_turn(tmp_path):
    """Complex review turns get a board (row count depends on Ghost plan or fallback)."""
    monitor_path = tmp_path / "monitor.jsonl"
    gateway = Gateway(ToolStartingAgent(), monitor=BackendMonitor(monitor_path))
    updates = []

    gateway.run_turn(
        "review entire interface taskboard gateway ghost worker context performance docs and tests",
        on_board_update=updates.append,
    )

    assert gateway.last_task_board is not None
    assert len(gateway.last_task_board.tasks) >= 1
    assert updates


def test_direct_build_request_sends_ghost_proposal_even_without_tools(tmp_path):
    """Ghost planning now runs for all work turns regardless of route source."""
    monitor_path = tmp_path / "monitor.jsonl"
    agent = ProposalAgent()
    gateway = Gateway(agent, monitor=BackendMonitor(monitor_path))
    events = []

    result = gateway.run_turn(
        "build a football game",
        on_proposal=lambda text: events.append(("proposal", text)),
        on_board_update=lambda board: events.append(("board", board)),
    )

    assert result == "A long provider-side answer without tools."
    # Ghost proposal fires for all work turns
    assert len(events) == 1
    assert events[0][0] == "proposal"
    assert "Proposal" in events[0][1]
    assert getattr(agent, "proposals", 0) == 1
    assert gateway.last_task_board is None  # No tools → no board
    assert "turn_start" in monitor_path.read_text(encoding="utf-8")


def test_routed_work_request_shows_proposal_without_task_board_until_tools(tmp_path):
    monitor_path = tmp_path / "monitor.jsonl"
    gateway = Gateway(ProposalAgent(), monitor=BackendMonitor(monitor_path))
    events = []

    result = gateway.run_turn(
        "build a football game",
        on_proposal=lambda text: events.append(("proposal", text)),
        on_board_update=lambda board: events.append(("board", board)),
        route_source="ghost",
    )

    assert result == "A long provider-side answer without tools."
    assert [kind for kind, _value in events] == ["proposal"]
    assert "Proposal: Build a focused football game." in events[0][1]
    assert getattr(gateway.agent, "proposals", 0) == 1
    assert gateway.last_task_board is None
    assert "taskboard" not in monitor_path.read_text(encoding="utf-8")


def test_simple_tool_backed_answer_does_not_create_task_board(tmp_path):
    monitor_path = tmp_path / "monitor.jsonl"
    gateway = Gateway(RuntimeSignalAgent("find_files", {"root": "."}), monitor=BackendMonitor(monitor_path))
    updates = []

    result = gateway.run_turn("what we cooking today?", on_board_update=updates.append)

    assert result == "used runtime signal"
    assert updates == []
    assert gateway.last_task_board is None
    assert "taskboard" not in monitor_path.read_text(encoding="utf-8")


def test_ambiguous_mutating_runtime_signal_creates_task_board(tmp_path):
    monitor_path = tmp_path / "monitor.jsonl"
    gateway = Gateway(RuntimeSignalAgent("edit_file", {"path": "app.py"}), monitor=BackendMonitor(monitor_path))
    updates = []

    result = gateway.run_turn("do it", on_board_update=updates.append)

    assert result == "used runtime signal"
    assert updates
    assert gateway.last_task_board is not None
    assert "taskboard" in monitor_path.read_text(encoding="utf-8")


def test_interactive_work_request_without_proposal_keeps_lazy_board_contract(tmp_path):
    monitor_path = tmp_path / "monitor.jsonl"
    gateway = Gateway(NoToolAgent(), monitor=BackendMonitor(monitor_path))
    updates = []

    result = gateway.run_turn("build a football game", on_board_update=updates.append, route_source="ghost")

    assert result == "A long provider-side answer without tools."
    assert updates == []
    assert gateway.last_task_board is None


def test_noninteractive_no_tool_request_keeps_lazy_board_contract(tmp_path):
    monitor_path = tmp_path / "monitor.jsonl"
    gateway = Gateway(NoToolAgent(), monitor=BackendMonitor(monitor_path))

    result = gateway.run_turn("build a football game", route_source="ghost")

    assert result == "A long provider-side answer without tools."
    assert gateway.last_task_board is None
