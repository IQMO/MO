"""Tests for TaskBoard state/rendering behavior."""
from core.tasking.task_board import TaskBoard, TaskItem, board_update_event, check_task_board_contract, snapshot_dict
from interface.task_board_view import render_plain, render_rich, task_board_fragments_from_text


def test_render_counts_and_symbols():
    board = TaskBoard(
        turn_id="turn-1",
        tasks=[
            TaskItem("1", "Inspect current files", "completed", evidence=["read_file:a.py"]),
            TaskItem("2", "Extract shared helper", "active"),
            TaskItem("3", "Run tests", "pending"),
            TaskItem("4", "Verify browser behavior", "blocked", blocker="dev server failed to start"),
        ],
    )

    expected = (
        "4 tasks (1 done, 3 open)\n"
        "  √ Inspect current files\n"
        "  → Extract shared helper\n"
        "  □ Run tests\n"
        "  ! Verify browser behavior — dev server failed to start"
    )
    assert render_plain(board) == expected
    assert board.render() == expected


def test_board_update_event_is_structured_render_snapshot_not_truth_mutation():
    board = TaskBoard(turn_id="turn-1", session_id="session-1", tasks=[
        TaskItem("1", "Inspect", "completed", evidence=["read_file:a.py"]),
        TaskItem("2", "Report", "active", kind="report", completion_gate="final"),
    ])

    event = board_update_event(board, update="updated")

    assert event["type"] == "taskboard_update"
    assert event["update"] == "updated"
    assert event["turn_id"] == "turn-1"
    assert event["session_id"] == "session-1"
    assert event["state"] == "active"
    assert event["active_task_id"] == "2"
    assert event["done_count"] == 1
    assert event["open_count"] == 1
    assert event["contract_ok"] is True
    assert "Inspect" in event["rendered"]
    assert "[orange1]→[/orange1] Report" in event["rich"]
    assert board.task("2").status == "active"


def test_check_task_board_contract_reports_completion_and_evidence_gaps():
    board = TaskBoard(tasks=[
        TaskItem("1", "Inspect", "completed", kind="inspect", completion_gate="tool"),
        TaskItem("2", "Report", "active", completion_gate="final"),
    ])

    result = check_task_board_contract(board, require_completed=True, require_evidence=True)

    assert result.ok is False
    assert "taskboard_open:1" in result.reasons
    assert "missing_evidence:1" in result.reasons


def test_snapshot_dict_includes_contract_diagnostics():
    board = TaskBoard(tasks=[TaskItem("1", "Verify", "completed", evidence=["shell:pytest"])])

    snap = snapshot_dict(board, event="completed", state="completed")

    assert snap["contract"]["ok"] is True
    assert snap["contract"]["reasons"] == []
    assert "tasks" not in snap["contract"]["summary"]


def test_board_complete_mutates_status_without_render_side_effects():
    """Completion changes row state; separate owners decide when to call it."""
    board = TaskBoard(tasks=[TaskItem("1", "Inspect files", "active")])
    result = board.complete("1")
    assert result.ok is True
    assert board.task("1").status == "completed"


def test_board_complete_can_record_evidence_once():
    board = TaskBoard(tasks=[TaskItem("1", "Inspect files", "active")])

    result = board.complete("1", evidence="read_file:core/task_board.py")
    board.append_evidence("1", "read_file:core/task_board.py")

    assert result.ok is True
    assert board.task("1").status == "completed"
    assert board.task("1").evidence == ["read_file:core/task_board.py"]


def test_board_complete_rejects_evidence_required_task_without_evidence():
    board = TaskBoard(tasks=[TaskItem("1", "Inspect files", "active", kind="inspect", completion_gate="tool")])

    result = board.complete("1")

    assert result.ok is False
    assert result.reason == "missing_required_evidence"
    assert board.task("1").status == "active"


def test_board_complete_rejects_final_only_evidence_for_tool_gated_task():
    board = TaskBoard(tasks=[TaskItem("1", "Verify fix", "active", kind="verify", completion_gate="verification")])

    result = board.complete("1", evidence="final:assistant_response")

    assert result.ok is False
    assert result.reason == "missing_required_evidence"
    assert board.task("1").status == "active"
    assert board.task("1").evidence == ["final:assistant_response"]


def test_board_append_evidence_returns_false_for_unknown_task():
    board = TaskBoard(tasks=[TaskItem("1", "Inspect files", "active")])

    assert board.append_evidence("missing", "grep:needle") is False


def test_board_block_mutates_status_without_render_side_effects():
    """Blocking changes row state; separate owners decide blocker policy."""
    board = TaskBoard(tasks=[TaskItem("1", "Verify change", "active")])
    board.block("1", "")
    assert board.task("1").status == "blocked"


def test_activate_on_blocked_task():
    """Model resolved a blocker — can activate a previously blocked task."""
    board = TaskBoard(tasks=[
        TaskItem("1", "Verify", "blocked", blocker="tests failed"),
        TaskItem("2", "Report", "active"),
    ])
    board.activate("1")
    # Activating a blocked task means the blocker is resolved
    assert board.task("1").status == "active"
    # Previous active task moved to pending
    assert board.task("2").status == "pending"


def test_done_count_and_open_count():
    board = TaskBoard(tasks=[
        TaskItem("1", "A", "completed"),
        TaskItem("2", "B", "active"),
        TaskItem("3", "C", "blocked"),
        TaskItem("4", "D", "pending"),
    ])
    assert board.done_count() == 1
    assert board.open_count() == 3


def test_task_board_fragments_from_text_are_display_only():
    text = "2 tasks (1 done, 1 open)\n√ Inspect\n→ Verify"

    fragments = task_board_fragments_from_text(text, root_prefix="     ", skip_summary=True)

    rendered = "".join(fragment[1] for fragment in fragments)
    styles = [fragment[0] for fragment in fragments]
    assert "2 tasks" not in rendered
    assert "√ Inspect" in rendered
    assert "→ Verify" in rendered
    assert styles == ["class:task-done", "class:task-active"]


def test_render_rich_symbols():
    board = TaskBoard(tasks=[
        TaskItem("1", "Done", "completed", evidence=["evidence"]),
        TaskItem("2", "Active", "active"),
        TaskItem("3", "Pending", "pending"),
        TaskItem("4", "Blocked", "blocked", blocker="needs approval"),
    ])

    rendered = render_rich(board)
    assert board.render_rich() == rendered
    assert "[green]√[/green] [dim]Done[/dim]" in rendered
    assert "↳" not in rendered  # per-task evidence sub-line removed (clean one-line-per-task)
    assert "[orange1]→[/orange1] Active" in rendered
    assert "[dim]□ Pending[/dim]" in rendered
    assert "[red]![/red] Blocked [dim]— needs approval[/dim]" in rendered


def test_set_rows_from_model():
    """set_rows is the primary structured-row initialization path."""
    board = TaskBoard()
    board.set_rows("Build feature X", [
        {"id": "1", "text": "Inspect", "status": "active"},
        {"id": "2", "text": "Implement", "status": "pending"},
        {"id": "3", "text": "Verify", "status": "pending"},
    ], objective="the user asked for feature X")
    assert board.title == "Build feature X"
    assert board.objective == "the user asked for feature X"
    assert len(board.tasks) == 3
    assert board.tasks[0].status == "active"


def test_next_ready_task_returns_active_then_ready_pending():
    board = TaskBoard(tasks=[
        TaskItem("1", "Inspect", "completed"),
        TaskItem("2", "Build", "active", depends_on=["1"]),
        TaskItem("3", "Verify", "pending", depends_on=["2"]),
    ])

    assert board.next_ready_task().id == "2"
    board.complete("2", evidence="edit_file:x.py")
    assert board.next_ready_task().id == "3"


def test_taskitem_acceptance_metadata_and_parent_id_are_snapshotted():
    board = TaskBoard()
    board.set_rows("Build", [
        {
            "id": "1",
            "text": "Build checkout",
            "status": "active",
            "acceptance_criteria": ["opens checkout", "opens checkout"],
            "expected_evidence": ["edit_file:checkout.py", "shell:pytest"],
            "test_strategy": "pytest checkout tests",
        },
        {"id": "1.1", "text": "Wire button", "parent_id": "1", "status": "pending"},
    ])

    parent = board.task("1")
    child = board.task("1.1")
    assert parent.acceptance_criteria == ["opens checkout"]
    assert parent.expected_evidence == ["edit_file:checkout.py", "shell:pytest"]
    assert parent.test_strategy == "pytest checkout tests"
    assert child.parent_id == "1"
    assert [row.id for row in board.child_tasks("1")] == ["1.1"]
    graph = board.validate_graph()
    assert graph["valid"] is True
    snap = snapshot_dict(board, event="updated")
    assert snap["tasks"][0]["acceptance_criteria"] == ["opens checkout"]
    assert snap["tasks"][0]["expected_evidence"] == ["edit_file:checkout.py", "shell:pytest"]
    assert snap["tasks"][1]["parent_id"] == "1"


def test_validate_graph_reports_parent_issues_without_mutating():
    board = TaskBoard(tasks=[
        TaskItem("1", "Parent", "completed"),
        TaskItem("2", "Missing parent", "pending", parent_id="missing"),
        TaskItem("3", "Self parent", "pending", parent_id="3"),
        TaskItem("4", "Parent cycle A", "pending", parent_id="5"),
        TaskItem("5", "Parent cycle B", "pending", parent_id="4"),
    ])
    before = [(row.id, row.parent_id, row.status) for row in board.tasks]

    report = board.validate_graph()

    assert report["valid"] is False
    codes = {issue["code"] for issue in report["issues"]}
    assert {"missing_parent", "self_parent", "parent_cycle"} <= codes
    assert [(row.id, row.parent_id, row.status) for row in board.tasks] == before


def test_validate_graph_reports_dependency_issues_without_mutating():
    board = TaskBoard(tasks=[
        TaskItem("1", "Inspect", "completed"),
        TaskItem("2", "Build", "pending", depends_on=["1"]),
        TaskItem("3", "Verify", "pending", depends_on=["missing"]),
        TaskItem("4", "Cycle A", "pending", depends_on=["5"]),
        TaskItem("5", "Cycle B", "pending", depends_on=["4"]),
    ])
    board.task("2").depends_on = ["1", "1"]
    before = [(row.id, row.status, list(row.depends_on)) for row in board.tasks]

    report = board.validate_graph()

    assert report["valid"] is False
    codes = {issue["code"] for issue in report["issues"]}
    assert {"duplicate_dependency", "missing_dependency", "cycle"} <= codes
    assert [(row.id, row.status, list(row.depends_on)) for row in board.tasks] == before


def test_validate_graph_reports_zero_active_and_no_ready_as_warnings():
    board = TaskBoard(tasks=[
        TaskItem("1", "Blocked dependency", "blocked"),
        TaskItem("2", "Waiting task", "pending", depends_on=["1"]),
    ])

    report = board.validate_graph()

    assert report["valid"] is True
    assert {issue["code"] for issue in report["issues"]} == {"zero_active", "no_ready_task"}
    assert {issue["severity"] for issue in report["issues"]} == {"warning"}
    assert report["ready_task_id"] == ""


def test_validate_graph_reports_multiple_active_rows_if_state_is_manually_corrupted():
    board = TaskBoard(tasks=[
        TaskItem("1", "One", "active"),
        TaskItem("2", "Two", "pending"),
    ])
    board.task("2").status = "active"

    report = board.validate_graph()

    assert report["valid"] is False
    issue = next(issue for issue in report["issues"] if issue["code"] == "multiple_active")
    assert issue["active_task_ids"] == ["1", "2"]


def test_status_normalization():
    """Status aliases are normalized."""
    board = TaskBoard()
    board.set_rows("Test", [
        {"id": "1", "text": "T1", "status": "in_progress"},
        {"id": "2", "text": "T2", "status": "done"},
        {"id": "3", "text": "T3", "status": "working"},
        {"id": "4", "text": "T4", "status": "complete"},
    ])
    assert board.tasks[0].status == "active"      # in_progress → active
    assert board.tasks[1].status == "completed"    # done → completed
    assert board.tasks[2].status == "pending"       # working → pending (unknown status falls through to default)
    assert board.tasks[3].status == "completed"    # complete → completed


# ── Regression: block() must not overwrite completed ──────────────────

def test_block_guards_completed_tasks():
    """block() must not change a completed row back to blocked."""
    board = TaskBoard(tasks=[TaskItem("1", "Done", "completed", evidence=["shell:pytest"])])
    board.block("1", "should be ignored")
    assert board.task("1").status == "completed"
    assert board.task("1").blocker == ""


# ── Regression: complete() must guard unsatisfied dependencies ────────

def test_complete_guards_unsatisfied_dependencies():
    """complete() must not mark a task done while its deps are not satisfied."""
    board = TaskBoard(tasks=[
        TaskItem("1", "Setup", "completed"),
        TaskItem("2", "Build", "pending", depends_on=["3"]),
        TaskItem("3", "Design", "pending"),
    ])
    board.complete("2", evidence="edit_file:x.py")
    assert board.task("2").status == "pending"
    assert board.task("2").evidence == []


# ── Regression: _ensure_one_active promotes first ready pending ───────

def test_ensure_one_active_promotes_when_zero_active():
    """When no row is active, the first dependency-ready pending row gets activated."""
    board = TaskBoard(tasks=[
        TaskItem("1", "A", "pending"),
        TaskItem("2", "B", "pending"),
        TaskItem("3", "C", "pending"),
    ])
    assert board.active_task_id() == "1"
    assert board.task("1").status == "active"


def test_ensure_one_active_skips_blocked_and_dependency_locked():
    """Promotion must skip blocked rows and rows whose deps are not satisfied."""
    board = TaskBoard(tasks=[
        TaskItem("1", "Blocked", "blocked"),
        TaskItem("2", "Waiting", "pending", depends_on=["99"]),
        TaskItem("3", "Ready", "pending"),
    ])
    assert board.active_task_id() == "3"
    assert board.task("3").status == "active"
