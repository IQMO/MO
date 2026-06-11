from core.tasking.task_board import TaskBoard, TaskItem
from core.tasking.task_board_loop import run_board_loop


def test_board_loop_completes_tool_rows_with_tool_evidence_only():
    board = TaskBoard(tasks=[
        TaskItem("1", "Inspect", "active", kind="inspect", completion_gate="tool"),
        TaskItem("2", "Verify", "pending", kind="verify", completion_gate="verification", depends_on=["1"]),
        TaskItem("3", "Report", "pending", kind="report", completion_gate="final", depends_on=["2"]),
    ])

    def runner(task, _board):
        if task.id == "1":
            return {"action": "completed", "evidence": "read_file:a.py"}
        if task.id == "2":
            return {"action": "completed", "evidence": "shell:python -m pytest -q"}
        raise AssertionError("report row must not be loop-completed")

    result = run_board_loop(board, runner, max_steps=5)

    assert result.completed == ["1", "2"]
    assert result.stopped_reason == "awaiting_final_or_manual"
    assert board.task("1").status == "completed"
    assert board.task("2").status == "completed"
    assert board.task("3").status == "active"


def test_board_loop_refuses_provider_marker_without_tool_evidence():
    board = TaskBoard(tasks=[TaskItem("1", "Inspect", "active", kind="inspect", completion_gate="tool")])

    result = run_board_loop(board, lambda *_args: {"action": "completed", "evidence": "<loop-complete>"})

    assert result.completed == []
    assert result.stopped_reason == "missing_or_invalid_evidence"
    assert board.task("1").status == "active"


def test_board_loop_blocks_with_reason():
    board = TaskBoard(tasks=[TaskItem("1", "Verify", "active", kind="verify", completion_gate="verification")])

    result = run_board_loop(board, lambda *_args: {"action": "blocked", "blocker": "tests failed"})

    assert result.blocked == ["1"]
    assert result.stopped_reason == "blocked"
    assert board.task("1").status == "blocked"
    assert board.task("1").blocker == "tests failed"
