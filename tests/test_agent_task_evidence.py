"""Taskboard advancement confirmations from the Agent/tool perspective."""
from __future__ import annotations

from core.agent.agent import Agent
from core.tasking.task_board import TaskBoard, TaskItem
from core.tasking import task_evidence


def _agent() -> Agent:
    return object.__new__(Agent)


def _fix_board() -> TaskBoard:
    """Create a realistic fix-task board matching what Ghost would plan."""
    board = TaskBoard()
    board.set_rows(
        "login bug",
        [
            {"id": "1", "text": "Inspect login bug", "status": "active", "kind": "inspect", "completion_gate": "tool", "depends_on": []},
            {"id": "2", "text": "Fix login bug", "status": "pending", "kind": "edit", "completion_gate": "tool", "depends_on": ["1"]},
            {"id": "3", "text": "Verify login bug", "status": "pending", "kind": "verify", "completion_gate": "verification", "depends_on": ["2"]},
        ],
        objective="fix login bug",
    )
    return board


def test_agent_advances_fix_board_by_metadata_gate_sequence():
    board = _fix_board()
    agent = _agent()

    assert board.task("1").title == "Inspect login bug"
    assert agent._advance_task_board_after_tool(board, "read_file", {"path": "auth.py"}) is False
    assert board.task("1").status == "active"
    assert board.task("1").evidence == ["read_file:auth.py"]
    
    assert agent._advance_task_board_after_tool(board, "complete_task", {}) is True
    assert board.task("1").status == "completed"
    assert board.task("2").title == "Fix login bug"
    assert board.task("2").status == "active"

    assert agent._advance_task_board_after_tool(board, "edit_file", {"path": "auth.py"}) is False
    assert board.task("2").status == "active"
    assert board.task("2").evidence == ["edit_file:auth.py"]
    
    assert agent._advance_task_board_after_tool(board, "complete_task", {}) is True
    assert board.task("2").status == "completed"
    assert board.task("3").title == "Verify login bug"
    assert board.task("3").status == "active"

    assert agent._advance_task_board_after_tool(board, "shell", {"command": "python -m pytest -q"}) is False
    assert board.task("3").evidence == ["shell:python -m pytest -q"]
    assert agent._advance_task_board_after_tool(board, "complete_task", {}) is True
    assert board.done_count() == len(board.tasks)
    assert board.open_count() == 0


def test_agent_broad_review_inspect_row_waits_for_scope_evidence():
    board = TaskBoard()
    board.set_rows(
        "review",
        [
            {"id": "1", "text": "Inspect entire interface and taskboard code", "status": "active", "kind": "inspect", "completion_gate": "tool", "depends_on": []},
            {"id": "2", "text": "Report findings for interface review", "status": "pending", "kind": "report", "completion_gate": "final", "depends_on": ["1"]},
        ],
        objective="review entire codebase",
    )
    agent = _agent()

    assert board.task("1").title.startswith("Inspect entire interface")
    assert agent._advance_task_board_after_tool(board, "read_file", {"path": "interface/layout.py"}) is False
    assert board.task("1").status == "active"
    assert agent._advance_task_board_after_tool(board, "find_files", {"pattern": "*.py"}) is False
    assert board.task("1").status == "active"
    assert agent._advance_task_board_after_tool(board, "complete_task", {}) is True
    assert board.task("1").status == "completed"
    assert board.task("2").title.startswith("Report findings")
    assert board.task("2").status == "active"


def test_agent_does_not_advance_verify_rows_from_read_or_edit_tools():
    board = TaskBoard(tasks=[
        TaskItem("1", "Verify resolution", "active", kind="verify", completion_gate="verification"),
        TaskItem("2", "Report", "pending", kind="report", completion_gate="final", depends_on=["1"]),
    ])
    agent = _agent()

    assert agent._advance_task_board_after_tool(board, "read_file", {"path": "auth.py"}) is False
    assert agent._advance_task_board_after_tool(board, "edit_file", {"path": "auth.py"}) is False
    assert board.task("1").status == "active"
    assert board.task("2").status == "pending"


def test_agent_advances_execute_rows_from_shell_run():
    task = TaskItem("1", "Run taskboard simulation", "active", kind="execute", completion_gate="tool")

    assert task_evidence.tool_should_advance_task("shell", task, 0, 2, arguments={"command": "python tmp/taskboard_sim.py"}) is True


def test_agent_keeps_report_rows_for_final_answer_only():
    report = TaskItem("r", "Report result", "active", kind="report", completion_gate="final")

    assert task_evidence.tool_should_advance_task("shell", report, 1, 2, arguments={"command": "echo done"}) is False
    assert Agent._final_should_complete_task(report) is True


def test_agent_legacy_generic_rows_do_not_advance_on_read_only_probe():
    board = TaskBoard(tasks=[
        TaskItem("1", "Understand the request", "active"),
        TaskItem("2", "Report result", "pending"),
    ])

    assert _agent()._advance_task_board_after_tool(board, "read_file", {"path": "README.md"}) is False
    assert board.task("1").status == "active"
