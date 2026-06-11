"""Taskboard lifecycle confirmations from the Gateway perspective."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.backend_monitor import BackendMonitor
from core.gateway import Gateway
from core.path_defaults import ENV_TASKBOARD_LEDGER_PATH
from core.tasking.task_board import TaskBoard, TaskItem, read_recent_snapshots


class _NoToolAgent:
    provider_name = "mock"
    model = "mock"

    def __init__(self, session_id: str = "session-gateway"):
        self.session = SimpleNamespace(session_id=session_id, messages=[], turn_count=0)
        self.calls: list[str] = []

    def run_turn(self, user_input, monitor=None, on_first_tool=None, on_board_update=None, **_kwargs):
        self.calls.append(user_input)
        return "no tools"


class _ToolAgent(_NoToolAgent):
    def run_turn(self, user_input, monitor=None, on_first_tool=None, on_board_update=None, **_kwargs):
        self.calls.append(user_input)
        self.first_board = on_first_tool() if on_first_tool else None
        self.second_board = on_first_tool() if on_first_tool else None
        return "used tools"


class _SimpleToolAgent(_NoToolAgent):
    def run_turn(self, user_input, monitor=None, on_first_tool=None, on_board_update=None, **_kwargs):
        self.calls.append(user_input)
        self.first_board = on_first_tool("find_files", {"root": "."}) if on_first_tool else None
        return "used simple tool"


class _RuntimeSignalAgent(_NoToolAgent):
    def __init__(self, tool_name: str, arguments: dict | None = None, session_id: str = "session-runtime"):
        super().__init__(session_id=session_id)
        self.tool_name = tool_name
        self.arguments = arguments or {}
        self.first_board = None

    def run_turn(self, user_input, monitor=None, on_first_tool=None, on_board_update=None, **_kwargs):
        self.calls.append(user_input)
        self.first_board = on_first_tool(self.tool_name, self.arguments) if on_first_tool else None
        return "used runtime signal"


class _ClearingResumeSignalAgent(_RuntimeSignalAgent):
    def run_turn(self, user_input, monitor=None, on_first_tool=None, on_board_update=None, **_kwargs):
        self.calls.append(user_input)
        self._pending_interrupted_work = {}
        self.first_board = on_first_tool(self.tool_name, self.arguments) if on_first_tool else None
        return "used runtime signal"


class _PrivateResumeRaisesAgent(_RuntimeSignalAgent):
    def _looks_like_interrupted_resume_request(self, _user_input):  # pragma: no cover - must not be called by Gateway
        raise AssertionError("Gateway must use shared resume helper, not Agent private policy")


class _GhostCoupledToolAgent(_ToolAgent):
    def __init__(self, session_id: str = "session-gateway"):
        super().__init__(session_id=session_id)
        self.proposal_calls = 0

    def propose_work(self, *_args, **_kwargs):
        self.proposal_calls += 1
        return "intent guidance"

    def complete_ghost_no_tools(self, **_kwargs):  # pragma: no cover - must not be called
        raise AssertionError("Ghost must not create taskboard rows")


class _GhostRowsToolAgent(_ToolAgent):
    def __init__(self, session_id: str = "session-gateway"):
        super().__init__(session_id=session_id)
        self.proposal_calls = 0

    def propose_work(self, *_args, **_kwargs):
        self.proposal_calls += 1
        return """intent guidance
---
[
  {"id": "1", "text": "Generic VS05 wrapper", "status": "active", "kind": "inspect", "completion_gate": "tool"},
  {"id": "2", "text": "Verify folders loaded", "status": "pending", "kind": "verify", "completion_gate": "final", "depends_on": ["1"]}
]"""


class _BlockingAgent(_NoToolAgent):
    def run_turn(self, user_input, monitor=None, on_first_tool=None, on_board_update=None, **_kwargs):
        board = on_first_tool() if on_first_tool else None
        if board:
            board.block(board.active_task_id(), "needs approval")
        return "blocked"


class _ExplodingAfterBoardAgent(_NoToolAgent):
    def run_turn(self, user_input, monitor=None, on_first_tool=None, on_board_update=None, **_kwargs):
        if on_first_tool:
            on_first_tool()
        raise RuntimeError("boom")


class _ScriptedAgent(_NoToolAgent):
    def __init__(self, results: list[str], session_id: str = "session-scripted"):
        super().__init__(session_id=session_id)
        self.results = list(results)

    def run_turn(self, user_input, monitor=None, on_first_tool=None, on_board_update=None, **_kwargs):
        self.calls.append(user_input)
        if not self.results:
            return "[DEVMODE05 COMPLETE] done"
        return self.results.pop(0)


class _ScriptedWorkAgent(_ScriptedAgent):
    def run_turn(self, user_input, monitor=None, on_first_tool=None, on_board_update=None, **_kwargs):
        self.calls.append(user_input)
        if on_first_tool:
            on_first_tool("read_file", {"path": "README.md"})
        if not self.results:
            return "completed work"
        return self.results.pop(0)


def test_gateway_taskboard_visibility_gate_by_request_type(tmp_path):
    gateway = Gateway(_NoToolAgent(), monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    assert gateway.should_show_task_board("hi mo") is False
    assert gateway.should_show_task_board("/status") is False
    assert gateway.should_show_task_board("that is good news") is False
    assert gateway.should_show_task_board("build a football game") is True
    assert gateway.should_show_task_board("rebuild the UI with keyboard controls") is True
    assert gateway.should_show_task_board("review the taskboard implementation") is True
    assert gateway.should_show_task_board("check logs memory session performance and taskboard state") is False  # simple_chat per template
    assert gateway.should_show_task_board("how would you research this codebase?") is False
    # After simplification: simple_chat only is the gate
    assert gateway.should_show_task_board("find all test files and run them") is True
    assert gateway.should_show_task_board("scan for bugs") is True
    assert gateway.should_show_task_board("start VS05 E:\\ref-a E:\\ref-b") is True


def test_gateway_uses_vs05_phase_rows_when_ghost_has_no_plan(tmp_path):
    agent = _ToolAgent(session_id="session-vs05")
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    result = gateway.run_turn("start VS05 E:\\ref-a E:\\ref-b")

    assert result == "used tools"
    assert gateway.last_task_board is not None
    titles = [task.title for task in gateway.last_task_board.tasks]
    assert titles[0] == "Capture current-MO target, reference roles, scope, and read-only boundary"
    assert "comparison matrix" in titles[2]


def test_gateway_uses_vs05_phase_rows_even_when_ghost_has_generic_rows(tmp_path):
    agent = _GhostRowsToolAgent(session_id="session-vs05-ghost")
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))
    proposals: list[str] = []

    result = gateway.run_turn("start VS05 E:\\ref-a E:\\ref-b", on_proposal=proposals.append)

    assert result == "used tools"
    assert agent.proposal_calls == 0
    assert proposals == []
    assert gateway.last_task_board is not None
    titles = [task.title for task in gateway.last_task_board.tasks]
    assert len(titles) == 5
    assert "Generic VS05 wrapper" not in titles
    assert titles[0] == "Capture current-MO target, reference roles, scope, and read-only boundary"
    assert titles[-1] == "Write VS05 artifacts and approval-ready closeout"


def test_gateway_creates_one_owned_board_lazily_on_first_tool_signal(tmp_path):
    agent = _ToolAgent(session_id="session-owned")
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))
    updates: list[str] = []
    events: list[dict] = []

    result = gateway.run_turn("build a football game real quick", on_board_update=updates.append, on_board_event=events.append)

    assert result == "used tools"
    assert gateway.last_task_board is not None
    assert agent.first_board is gateway.last_task_board
    assert agent.second_board is gateway.last_task_board
    assert len(updates) == 1
    assert len(events) == 1
    board = gateway.last_task_board
    assert events[0]["type"] == "taskboard_update"
    assert events[0]["update"] == "created"
    assert events[0]["board_id"] == board.board_id
    assert events[0]["active_task_id"] == "1"
    assert gateway.task_board_registry.get_board("main") is board
    assert gateway.task_board_registry.recent_events(surface="main", limit=1)
    assert board.source == "gateway"
    assert board.session_id == "session-owned"
    assert board.active_task_id() == "1"
    # Fallback board (no Ghost on this agent): single-row
    assert len(board.tasks) == 1
    assert board.tasks[0].kind == "edit"
    assert board.tasks[0].completion_gate == "tool"
    assert "football game" in board.render()


def test_gateway_runs_ghost_planning_for_all_work_turns(tmp_path):
    """Ghost proposal now runs for all work turns, not just ghost-routed."""
    agent = _GhostCoupledToolAgent(session_id="session-ghost-plan")
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    gateway.run_turn("review entire interface taskboard code", on_proposal=lambda _text: None)

    # Ghost IS called now for all work turns
    assert agent.proposal_calls == 1
    assert gateway.last_task_board is not None
    assert gateway.last_task_board.source == "gateway"


def test_gateway_does_not_create_board_for_simple_tool_backed_chat(tmp_path):
    agent = _SimpleToolAgent()
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))
    updates: list[str] = []

    result = gateway.run_turn("what we cooking today?", on_board_update=updates.append)

    assert result == "used simple tool"
    assert agent.first_board is None
    assert gateway.last_task_board is None
    assert updates == []


def test_gateway_runtime_mutating_signal_can_create_board_for_ambiguous_work(tmp_path):
    """Board is created for runtime work signals; rows come from fallback."""
    agent = _RuntimeSignalAgent("edit_file", {"path": "app.py"})
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))
    updates: list[str] = []

    result = gateway.run_turn("do it", on_board_update=updates.append)

    assert result == "used runtime signal"
    assert agent.first_board is gateway.last_task_board
    assert gateway.last_task_board is not None
    assert updates
    board = gateway.last_task_board
    assert len(board.tasks) >= 1
    assert board.tasks[0].status == "active"


def test_gateway_resume_uses_parked_objective_for_board_rows(tmp_path):
    agent = _RuntimeSignalAgent("read_file", {"path": "game.py"})
    agent._pending_interrupted_work = {"user": "investigate all games in examples folder and fix missing/broken games"}
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    result = gateway.run_turn("yes proceed with them")

    assert result == "used runtime signal"
    assert gateway.last_task_board is not None
    rendered = gateway.last_task_board.render()
    assert "yes proceed" not in rendered.lower()
    assert "all games" in rendered.lower()


def test_gateway_resume_survives_agent_clearing_pending_before_first_tool(tmp_path):
    agent = _ClearingResumeSignalAgent("read_file", {"path": "game.py"})
    agent._pending_interrupted_work = {"user": "investigate all games in examples folder and fix missing/broken games"}
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    result = gateway.run_turn("yes proceed with them")

    assert result == "used runtime signal"
    assert gateway.last_task_board is not None
    rendered = gateway.last_task_board.render()
    assert "yes proceed" not in rendered.lower()
    assert "all games" in rendered.lower()


def test_gateway_resume_uses_shared_helper_not_agent_private_method(tmp_path):
    agent = _PrivateResumeRaisesAgent("read_file", {"path": "game.py"})
    agent._pending_interrupted_work = {"user": "investigate all games in examples folder and fix missing/broken games"}
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    result = gateway.run_turn("lets focus again on what was left")

    assert result == "used runtime signal"
    assert gateway.last_task_board is not None
    rendered = gateway.last_task_board.render()
    assert "focus again" not in rendered.lower()
    assert "all games" in rendered.lower()


def test_gateway_work_turn_always_creates_board_on_first_tool(tmp_path):
    agent = _RuntimeSignalAgent("read_file", {"path": "README.md"})
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    gateway.run_turn("review this", route_source="user")

    assert gateway.last_task_board is not None
    assert gateway.last_task_board.source == "gateway"


def test_gateway_keeps_no_tool_work_without_fake_board(tmp_path):
    agent = _NoToolAgent()
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))
    gateway.last_task_board = TaskBoard(tasks=[TaskItem("1", "stale", "completed")])
    updates: list[str] = []

    result = gateway.run_turn("build a football game", on_board_update=updates.append)

    assert result == "no tools"
    assert gateway.last_task_board is None
    assert updates == []


def test_gateway_terminal_snapshot_records_blocked_board_state(tmp_path, monkeypatch):
    ledger = tmp_path / "taskboards.jsonl"
    monkeypatch.setenv(ENV_TASKBOARD_LEDGER_PATH, str(ledger))
    gateway = Gateway(_BlockingAgent(), monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    gateway.run_turn("build approval gated change", on_board_update=lambda _board: None)

    recent = read_recent_snapshots(limit=5, path=ledger)
    assert recent[-1]["event"] == "blocked"
    assert recent[-1]["state"] == "blocked"
    assert recent[-1]["tasks"][0]["status"] == "blocked"
    assert recent[-1]["tasks"][0]["blocker"] == "needs approval"


def test_gateway_terminal_snapshot_records_abandoned_board_on_error(tmp_path, monkeypatch):
    ledger = tmp_path / "taskboards.jsonl"
    monkeypatch.setenv(ENV_TASKBOARD_LEDGER_PATH, str(ledger))
    gateway = Gateway(_ExplodingAfterBoardAgent(), monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    with pytest.raises(RuntimeError, match="boom"):
        gateway.run_turn("build then fail", on_board_update=lambda _board: None)

    recent = read_recent_snapshots(limit=5, path=ledger)
    assert recent[-1]["event"] == "abandoned"
    assert recent[-1]["state"] == "abandoned"
    assert gateway.last_task_board is not None


def test_gateway_auto_continues_devmode05_after_tool_budget_boundary(tmp_path):
    agent = _ScriptedAgent([
        "[DEVMODE05 BLOCKED]\n\nTool budget exhausted. Continuation required in the next fresh turn.",
        "[DEVMODE05 COMPLETE] done",
    ])
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    result = gateway.run_turn("start DEVMODE05")

    assert result == "[DEVMODE05 COMPLETE] done"
    assert len(agent.calls) == 2
    assert agent.calls[0] == "start DEVMODE05"
    assert agent.calls[1].startswith("DEVMODE05 CONTINUATION 1.")
    assert "Tool budget exhausted" in agent.calls[1]


def test_gateway_auto_continues_devmode05_after_provider_request_limit(tmp_path):
    agent = _ScriptedAgent([
        "[MAX PROVIDER REQUESTS] Turn limit reached.",
        "[DEVMODE05 COMPLETE] done",
    ])
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    result = gateway.run_turn("start DEVMODE05")

    assert result == "[DEVMODE05 COMPLETE] done"
    assert len(agent.calls) == 2
    assert agent.calls[1].startswith("DEVMODE05 CONTINUATION 1.")


def test_gateway_auto_continues_devmode05_after_continuation_capsule(tmp_path):
    agent = _ScriptedAgent([
        "[DEVMODE05 CONTINUATION CAPSULE]\n\nStatus: not complete. No more tools allowed this turn.",
        "[DEVMODE05 COMPLETE] done",
    ])
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    result = gateway.run_turn("start DEVMODE05")

    assert result == "[DEVMODE05 COMPLETE] done"
    assert len(agent.calls) == 2
    assert agent.calls[1].startswith("DEVMODE05 CONTINUATION 1.")
    assert "fresh continuation turn and tools are available again" in agent.calls[1]


def test_gateway_auto_continues_devmode05_after_stale_no_tool_block(tmp_path):
    agent = _ScriptedAgent([
        "[DEVMODE05 BLOCKED]\n\nHard boundary: current runtime instruction explicitly forbids further tool calls.",
        "[DEVMODE05 COMPLETE] done",
    ])
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    result = gateway.run_turn("start DEVMODE05")

    assert result == "[DEVMODE05 COMPLETE] done"
    assert len(agent.calls) == 2
    assert agent.calls[1].startswith("DEVMODE05 CONTINUATION 1.")
    assert "fresh continuation turn and tools are available again" in agent.calls[1]


def test_gateway_auto_continues_devmode05_after_markdown_tool_budget_boundary(tmp_path):
    agent = _ScriptedAgent([
        "# [DEVMODE05 BLOCKED] - Tool Budget Exhaustion\n\n75/80 tool rounds consumed.",
        "[DEVMODE05 COMPLETE] done",
    ])
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    result = gateway.run_turn("start DEVMODE05")

    assert result == "[DEVMODE05 COMPLETE] done"
    assert len(agent.calls) == 2
    assert agent.calls[1].startswith("DEVMODE05 CONTINUATION 1.")
    assert "75/80 tool rounds consumed" in agent.calls[1]


def test_gateway_does_not_auto_continue_devmode05_nonrecoverable_boundary(tmp_path):
    boundary = "[DEVMODE05 BLOCKED] sandbox block: command requires approval."
    agent = _ScriptedAgent([boundary, "[DEVMODE05 COMPLETE] should not run"])
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    result = gateway.run_turn("start DEVMODE05")

    assert result == boundary
    assert agent.calls == ["start DEVMODE05"]


def test_gateway_auto_continues_open_work_after_tool_budget_boundary(tmp_path):
    agent = _ScriptedWorkAgent([
        "[WORK BLOCKED]\n\nTool budget exhausted. Continuation required in the next fresh turn.",
        "completed work",
    ])
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    result = gateway.run_turn("fix the broken workflow")

    assert result == "completed work"
    assert len(agent.calls) == 2
    assert agent.calls[0] == "fix the broken workflow"
    assert agent.calls[1].startswith("WORK CONTINUATION 1.")
    assert "fix the broken workflow" in agent.calls[1]


def test_gateway_auto_continues_open_work_after_markdown_tool_budget_boundary(tmp_path):
    agent = _ScriptedWorkAgent([
        "## [WORK BLOCKED] - Tool Budget Exhaustion\n\n75/80 tool rounds consumed.",
        "completed work",
    ])
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    result = gateway.run_turn("fix the broken workflow")

    assert result == "completed work"
    assert len(agent.calls) == 2
    assert agent.calls[1].startswith("WORK CONTINUATION 1.")


def test_gateway_auto_continues_open_work_after_continuation_capsule(tmp_path):
    agent = _ScriptedWorkAgent([
        "[WORK CONTINUATION CAPSULE]\n\nStatus: not complete. No more tools allowed this turn.",
        "completed work",
    ])
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    result = gateway.run_turn("fix the broken workflow")

    assert result == "completed work"
    assert len(agent.calls) == 2
    assert agent.calls[1].startswith("WORK CONTINUATION 1.")
    assert "fresh continuation turn and tools are available again" in agent.calls[1]


def test_gateway_does_not_auto_continue_work_without_open_board(tmp_path):
    agent = _ScriptedAgent([
        "[WORK BLOCKED]\n\nTool budget exhausted. Continuation required in the next fresh turn.",
        "should not run",
    ])
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    result = gateway.run_turn("fix the broken workflow")

    assert result.startswith("[WORK BLOCKED]")
    assert agent.calls == ["fix the broken workflow"]


def test_gateway_does_not_auto_continue_work_nonrecoverable_boundary(tmp_path):
    boundary = "[WORK BLOCKED] sandbox block: command requires approval."
    agent = _ScriptedWorkAgent([boundary, "should not run"])
    gateway = Gateway(agent, monitor=BackendMonitor(tmp_path / "monitor.jsonl"))

    result = gateway.run_turn("fix the broken workflow")

    assert result == boundary
    assert agent.calls == ["fix the broken workflow"]
