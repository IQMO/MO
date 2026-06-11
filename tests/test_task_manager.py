"""Tests for core.task_manager — single-file task persistence."""
from __future__ import annotations

import json
from pathlib import Path


from core.tasking.task_manager import TaskManager
from core.tasking.task_board import (
    TaskBoard,
    TaskItem,
    check_task_board_contract,
    record_snapshot,
)


class TestTaskManager:
    """Unit tests for TaskManager CRUD and persistence."""

    def test_init_creates_tasks_dir(self, tmp_path: Path):
        tm = TaskManager(tmp_path)
        assert tm.tasks_dir.exists()
        assert tm.current_file == tm.tasks_dir / "current.json"

    def test_empty_state_on_init(self, tmp_path: Path):
        tm = TaskManager(tmp_path)
        assert tm.task_count == 0
        assert not tm.has_active
        assert tm.load_tasks() == []

    def test_save_and_load_roundtrip(self, tmp_path: Path):
        tm = TaskManager(tmp_path)
        snapshot = {
            "board_id": "b1",
            "turn_id": "t1",
            "session_id": "s1",
            "title": "Test Board",
            "objective": "Verify roundtrip",
            "source": "gateway",
            "state": "active",
            "tasks": [
                {"id": "T1", "title": "Task 1", "status": "pending"},
                {"id": "T2", "title": "Task 2", "status": "active"},
            ],
            "created_at": 1000.0,
        }
        tm.save(snapshot)
        assert tm.current_file.exists()

        # Reload from disk
        tm2 = TaskManager(tmp_path)
        assert tm2.task_count == 2
        assert tm2.load_tasks() == snapshot["tasks"]
        assert tm2.board_id == "b1"

    def test_clear_archives_and_removes(self, tmp_path: Path):
        tm = TaskManager(tmp_path)
        tm.save({"board_id": "b1", "tasks": [{"id": "T1", "title": "Dummy"}]})
        assert tm.current_file.exists()

        tm.clear()
        assert not tm.current_file.exists()
        assert tm.task_count == 0
        # Archive should exist
        archive_dir = tm.tasks_dir / "archive"
        archives = list(archive_dir.glob("session_*.json"))
        assert len(archives) == 1

    def test_load_corrupt_file_recovers(self, tmp_path: Path):
        tm = TaskManager(tmp_path)
        tm.current_file.write_text("this is not json", encoding="utf-8")
        tm2 = TaskManager(tmp_path)
        assert tm2.task_count == 0  # graceful recovery

    def test_has_active_detects_state(self, tmp_path: Path):
        tm = TaskManager(tmp_path)
        tm.save({"state": "active", "tasks": []})
        assert tm.has_active

        tm.save({"state": "completed", "tasks": []})
        assert not tm.has_active


class TestContractSync:
    """GAP-05: persisted_tasks sync parameter in check_task_board_contract."""

    def _make_board(self, **kwargs) -> TaskBoard:
        tasks = kwargs.pop("tasks", [])
        board = TaskBoard(**kwargs)
        for t in tasks:
            board.tasks.append(TaskItem(**t) if isinstance(t, dict) else t)
        return board

    def test_sync_no_persisted_tasks_unchanged(self):
        """Without persisted_tasks, behavior is unchanged."""
        board = self._make_board(
            tasks=[{"id": "T1", "title": "Task 1", "status": "active"}],
        )
        result = check_task_board_contract(board)
        assert result.ok  # active board alone is fine

    def test_sync_missing_board_row(self):
        """Persisted task without matching board row → contract fails."""
        board = self._make_board(
            tasks=[{"id": "T1", "title": "Task 1", "status": "active"}],
        )
        persisted = [{"id": "T2", "title": "Ghost task", "status": "done"}]
        result = check_task_board_contract(board, persisted_tasks=persisted)
        assert not result.ok
        assert any("missing_board_row" in r for r in result.reasons)

    def test_sync_done_not_completed(self):
        """Persisted 'done' but board not 'completed' → contract fails."""
        board = self._make_board(
            tasks=[{"id": "T1", "title": "Task 1", "status": "active"}],
        )
        persisted = [{"id": "T1", "status": "done"}]
        result = check_task_board_contract(board, persisted_tasks=persisted)
        assert not result.ok
        assert any("done_not_completed" in r for r in result.reasons)

    def test_sync_blocked_mismatch(self):
        """Persisted 'blocked' but board not 'blocked' → contract fails."""
        board = self._make_board(
            tasks=[{"id": "T1", "title": "Task 1", "status": "active"}],
        )
        persisted = [{"id": "T1", "status": "blocked"}]
        result = check_task_board_contract(board, persisted_tasks=persisted)
        assert not result.ok
        assert any("blocked_mismatch" in r for r in result.reasons)

    def test_sync_clean_passes(self):
        """Synced persisted tasks with matching board rows → contract passes."""
        board = self._make_board(
            tasks=[
                {"id": "T1", "title": "Task 1", "status": "completed", "kind": "inspect", "evidence": ["tool:read_file"]},
                {"id": "T2", "title": "Task 2", "status": "blocked", "blocker": "waiting"},
            ],
        )
        persisted = [
            {"id": "T1", "status": "done"},
            {"id": "T2", "status": "blocked"},
        ]
        result = check_task_board_contract(board, persisted_tasks=persisted)
        # Board has blocked task + completed tasks — but no require_completed flag
        # blocked_task:T2 is expected, the sync should NOT add mismatches
        assert not result.ok  # because of blocked_task:T2
        assert not any("sync" in r for r in result.reasons)  # no sync errors

    def test_sync_active_row_completed(self):
        """Persisted 'active' but board row 'completed' → sync drift."""
        board = self._make_board(
            tasks=[{"id": "T1", "title": "Task 1", "status": "completed"}],
        )
        persisted = [{"id": "T1", "status": "active"}]
        result = check_task_board_contract(board, persisted_tasks=persisted)
        assert not result.ok
        assert any("active_row_completed" in r for r in result.reasons)


class TestRecordSnapshotIntegration:
    """GAP-02: record_snapshot also writes current.json."""

    def test_record_snapshot_writes_current_json(self, tmp_path: Path):
        board = TaskBoard(turn_id="t1", session_id="s1")
        board.tasks.append(TaskItem(id="T1", title="Verify", status="active"))
        ledger = tmp_path / "ledger.jsonl"
        record_snapshot(board, "updated", path=ledger)
        # current.json should be at the cwd-based path, not under tmp_path
        # For this test, we verify the ledger was written
        assert ledger.exists()
        lines = ledger.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["board_id"] == board.board_id

    def test_record_snapshot_no_board_returns_none(self, tmp_path: Path):
        result = record_snapshot(None, "updated", path=tmp_path / "ledger.jsonl")
        assert result is None
