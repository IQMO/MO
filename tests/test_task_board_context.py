from core.tasking.task_board import TaskBoard, TaskItem, snapshot_dict
from core.tasking.task_board_context import compile_board_context, compile_board_context_from_snapshot


def test_compile_board_context_includes_active_gate_evidence_and_graph_without_mutation():
    board = TaskBoard(tasks=[
        TaskItem("1", "Inspect", "completed", evidence=["read_file:a.py"], kind="inspect", completion_gate="tool"),
        TaskItem(
            "2",
            "Verify",
            "active",
            kind="verify",
            completion_gate="verification",
            depends_on=["1"],
            parent_id="missing-parent",
            expected_evidence=["shell:pytest"],
            acceptance_criteria=["tests pass"],
            test_strategy="python -m pytest -q",
        ),
    ])
    before = [(row.id, row.status, list(row.depends_on)) for row in board.tasks]

    context = compile_board_context(board)

    assert context["present"] is True
    assert context["active_task_id"] == "2"
    assert context["graph"]["valid"] is False
    assert "Verify" in context["text"]
    assert "gate=verification" in context["text"]
    assert "expected evidence: shell:pytest" in context["text"]
    assert "acceptance: tests pass" in context["text"]
    assert "graph diagnostics: missing_parent task=2" in context["text"]
    assert [(row.id, row.status, list(row.depends_on)) for row in board.tasks] == before


def test_compile_board_context_from_snapshot_is_read_only_orientation():
    board = TaskBoard(session_id="s1", tasks=[
        TaskItem("1", "Inspect", "completed", evidence=["read_file:a.py"]),
        TaskItem("2", "Report", "active"),
    ])
    snapshot = snapshot_dict(board, event="updated")

    context = compile_board_context_from_snapshot(snapshot)

    assert context["present"] is True
    assert context["session_id"] == "s1"
    assert context["active_task_id"] == "2"
    assert "Last recorded board" in context["text"]
    assert "read_file:a.py" in context["text"]
