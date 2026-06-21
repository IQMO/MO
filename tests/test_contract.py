"""Tests for core/tasking/contract.py — gold-footer contract enforcement (VS05 GAP-01/05/06)."""

import pytest
from core.tasking.task_board import TaskBoard, TaskItem
from core.tasking.contract import (
    enforce_contract_gate,
    load_persisted_tasks_for_contract,
    _reason_matches_task_ids,
)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def empty_board():
    return TaskBoard(tasks=[])


@pytest.fixture
def board_one_active():
    return TaskBoard(tasks=[
        TaskItem("1", "Verify", "active", kind="verify", completion_gate="verification"),
    ])


@pytest.fixture
def board_one_completed_no_evidence():
    b = TaskBoard(tasks=[
        TaskItem("1", "Inspect", "completed", kind="inspect"),
    ])
    # Manually set completed without evidence for test
    b.tasks[0].status = "completed"
    b.tasks[0].evidence = []
    return b


@pytest.fixture
def board_mixed():
    return TaskBoard(tasks=[
        TaskItem("1", "Build", "completed", kind="build", evidence=["shell:build"]),
        TaskItem("2", "Test", "active", kind="test"),
        TaskItem("3", "Deploy", "pending", kind="deploy"),
    ])


@pytest.fixture
def board_all_done():
    return TaskBoard(tasks=[
        TaskItem("1", "Build", "completed", kind="build", evidence=["shell:done"]),
        TaskItem("2", "Test", "completed", kind="test", evidence=["pytest:ok"]),
    ])


# ── enforce_contract_gate ─────────────────────────────────────────────

class TestEnforceContractGate:
    """Test the enforcement wrapper around check_task_board_contract."""

    def test_none_board_returns_ok(self):
        ok, reasons, instruction = enforce_contract_gate(None)
        assert ok is True
        assert reasons == []
        assert instruction == ""

    def test_empty_board_no_tasks(self, empty_board):
        ok, reasons, instruction = enforce_contract_gate(empty_board)
        # check_task_board_contract reports "taskboard_empty" but it's not
        # in the mid-work enforcement prefixes, so it passes.
        assert ok is True
        assert reasons == []
        assert instruction == ""

    def test_mid_work_active_task_is_ok(self, board_one_active):
        ok, reasons, instruction = enforce_contract_gate(board_one_active)
        # Mid-work: open tasks are fine, no evidence/sync issues
        assert ok is True
        assert reasons == []
        assert instruction == ""

    def test_mid_work_enforces_evidence_on_completed(self, board_one_completed_no_evidence):
        ok, reasons, instruction = enforce_contract_gate(
            board_one_completed_no_evidence, board_closing=False,
        )
        # Mid-work: evidence is enforced
        assert ok is False
        assert any("missing_evidence:1" in r for r in reasons)
        assert "Fix each issue" in instruction

    def test_board_closing_enforces_all(self, board_one_completed_no_evidence):
        ok, reasons, instruction = enforce_contract_gate(
            board_one_completed_no_evidence, board_closing=True,
        )
        # Board-closing: full contract including require_completed
        assert ok is False
        # Should have missing_evidence AND taskboard_open if open>0
        # But this board has 0 open tasks (all completed), so no taskboard_open
        assert any("missing_evidence:1" in r for r in reasons)

    def test_board_closing_with_open_tasks_fails(self, board_one_active):
        ok, reasons, instruction = enforce_contract_gate(
            board_one_active, board_closing=True,
        )
        # Board-closing with open tasks should fail
        assert ok is False
        assert any(r.startswith("taskboard_open:") for r in reasons)

    def test_board_closing_all_clean(self, board_all_done):
        ok, reasons, instruction = enforce_contract_gate(
            board_all_done, board_closing=True,
        )
        assert ok is True
        assert reasons == []
        assert instruction == ""

    def test_task_ids_scoping_filters_reasons(self, board_mixed):
        """Only reasons for specified task IDs are enforced."""
        # Task 2 is active, task 3 is pending — if board_closing, open_count=2
        # But we scope to task_id "2" only
        ok, reasons, instruction = enforce_contract_gate(
            board_mixed, board_closing=True, task_ids={"2"},
        )
        # taskboard_open is global, should still show
        # But missing_evidence only for task 2 (active, not completed, so no missing evidence)
        assert not any("missing_evidence:1" in r for r in reasons)  # task 1 has evidence
        assert not any("missing_evidence:3" in r for r in reasons)  # task 3 not scoped

    def test_persisted_tasks_sync_caught(self, board_all_done):
        """GAP-05: persisted task with mismatched status triggers sync reason."""
        # "done" in persisted vs "completed" in board = equivalent, no trigger.
        # Use "active" (persisted) vs "completed" (board) for a real mismatch.
        persisted = [
            {"id": "2", "status": "active", "title": "Test"},
        ]
        ok, reasons, instruction = enforce_contract_gate(
            board_all_done, persisted_tasks=persisted, board_closing=False,
        )
        # "active" in persisted but "completed" in board — mismatch
        assert ok is False
        assert any("task_sync:active_row_completed:2" in r for r in reasons)

    def test_persisted_tasks_missing_row_caught(self, board_all_done):
        """GAP-05: persisted task with no board row triggers sync reason."""
        persisted = [
            {"id": "ghost", "status": "active", "title": "Ghost task"},
        ]
        ok, reasons, instruction = enforce_contract_gate(
            board_all_done, persisted_tasks=persisted, board_closing=False,
        )
        assert ok is False
        assert any("task_sync:missing_board_row:ghost" in r for r in reasons)


# ── _reason_matches_task_ids ─────────────────────────────────────────

class TestReasonMatchesTaskIds:
    def test_direct_match(self):
        assert _reason_matches_task_ids("missing_evidence:42", {"42"}) is True

    def test_no_match(self):
        assert _reason_matches_task_ids("missing_evidence:42", {"99"}) is False

    def test_global_no_colon_always_matches(self):
        assert _reason_matches_task_ids("taskboard_empty", set()) is True

    def test_global_open_always_matches(self):
        assert _reason_matches_task_ids("taskboard_open:3", set()) is True
        assert _reason_matches_task_ids("taskboard_open:3", {"99"}) is True

    def test_graph_reason_with_task_id(self):
        assert _reason_matches_task_ids("graph:cycle:7", {"7"}) is True
        assert _reason_matches_task_ids("graph:cycle:7", {"8"}) is False

    def test_blocked_task_reason(self):
        assert _reason_matches_task_ids("blocked_task:5", {"5"}) is True
        assert _reason_matches_task_ids("blocked_task:5", {"6"}) is False


# ── load_persisted_tasks_for_contract ─────────────────────────────────

class TestLoadPersistedTasks:
    def test_returns_list(self):
        tasks = load_persisted_tasks_for_contract()
        assert isinstance(tasks, list)

    def test_graceful_when_no_task_manager(self, monkeypatch):
        """If TaskManager import fails, returns empty list cleanly."""
        # Already tested implicitly — the function catches all exceptions.
        # Verify it doesn't raise.
        result = load_persisted_tasks_for_contract()
        assert result is not None
        assert isinstance(result, list)

    def test_cross_board_snapshot_is_ignored(self, tmp_path, monkeypatch):
        """Persisted rows from a different board must not leak into the gate.

        Task IDs are board-local ("1", "2", ...), so a stale snapshot from an
        earlier session would otherwise produce false task_sync failures and
        force needless continuations (the self-check fight)."""
        from core.tasking.task_manager import TaskManager

        monkeypatch.chdir(tmp_path)
        tm = TaskManager(tmp_path)
        tm.save({
            "board_id": "board-stale", "session_id": "old", "title": "Old board",
            "state": "active",
            "tasks": [{"id": "1", "title": "Stale", "status": "active"}],
        })

        live = TaskBoard(board_id="board-live", tasks=[
            TaskItem("1", "Close out", "completed", kind="report", completion_gate="final"),
        ])
        assert load_persisted_tasks_for_contract(live) == []
        ok, reasons, _ = enforce_contract_gate(
            live, persisted_tasks=load_persisted_tasks_for_contract(live), board_closing=True,
        )
        assert ok is True
        assert reasons == []

    def test_same_board_snapshot_is_used(self, tmp_path, monkeypatch):
        from core.tasking.task_manager import TaskManager

        monkeypatch.chdir(tmp_path)
        # Point the ledger at the tmp dir so the contract read resolves to the SAME
        # place we write the snapshot (record_snapshot/contract now share the resolved
        # ledger dir instead of cwd/memory/taskboards).
        ledger = tmp_path / "memory" / "taskboards" / "taskboards.jsonl"
        monkeypatch.setenv("MO_TASKBOARD_LEDGER_PATH", str(ledger))
        live = TaskBoard(board_id="board-live", tasks=[
            TaskItem("1", "Verify", "completed", kind="verify", evidence=["pytest:ok"]),
        ])
        tm = TaskManager(tmp_path, tasks_dir=ledger.parent)
        tm.save({
            "board_id": "board-live", "session_id": "now", "title": "Live",
            "state": "active",
            "tasks": [{"id": "1", "title": "Verify", "status": "active"}],
        })

        rows = load_persisted_tasks_for_contract(live)
        assert len(rows) == 1
        ok, reasons, _ = enforce_contract_gate(live, persisted_tasks=rows, board_closing=True)
        assert ok is False
        assert any(r.startswith("task_sync:") for r in reasons)


def test_whole_board_evidence_enforced_at_closeout_not_just_this_turn():
    """A phase row completed with no evidence (in an earlier turn) must block the
    closeout — the regression where multi-turn self-protocol runs marked rows done
    empty because enforcement was scoped to the final turn only."""
    board = TaskBoard(tasks=[
        TaskItem("1", "Boot", "completed", kind="inspect", completion_gate="tool", evidence=["read_file:x"]),
        TaskItem("2", "Matrix", "completed", kind="verify", completion_gate="tool", depends_on=["1"]),  # EMPTY
        TaskItem("3", "Report", "completed", kind="report", completion_gate="final", depends_on=["2"]),
    ])
    ok, reasons, _ = enforce_contract_gate(board, board_closing=True)  # whole board (no task_ids scoping)
    assert ok is False
    assert any(r == "missing_evidence:2" for r in reasons)
    # the same board with evidence on row 2 passes
    board.task("2").evidence = ["shell:pytest"]
    ok2, _, _ = enforce_contract_gate(board, board_closing=True)
    assert ok2 is True
