"""Dedicated tests for core/agent_taskboard.py — P2-TESTGAP."""

from unittest.mock import MagicMock, patch


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_taskboard(tasks=None):
    """Build a mock TaskBoard with the given task list."""
    from core.tasking.task_board import TaskBoard
    tb = TaskBoard()
    if tasks:
        tb.tasks = tasks
    return tb


def _mock_task(task_id, status="active", title="test"):
    """Create a mock task row."""
    m = MagicMock()
    m.id = task_id
    m.status = status
    m.title = title
    return m


# ── _task_evidence_item_for_tool ─────────────────────────────────────────────

class TestTaskEvidenceItemForTool:
    def test_basic(self):
        from core.tasking.agent_taskboard import AgentTaskBoard
        result = AgentTaskBoard._task_evidence_item_for_tool("read_file", {"path": "test.py"})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_string_for_all_tool_types(self):
        from core.tasking.agent_taskboard import AgentTaskBoard
        for tool in ["read_file", "write_file", "edit_file", "shell", "grep", "test_runner"]:
            result = AgentTaskBoard._task_evidence_item_for_tool(tool, {})
            assert isinstance(result, str), f"Expected str for {tool}, got {type(result)}"

    def test_none_arguments_treated_as_empty(self):
        from core.tasking.agent_taskboard import AgentTaskBoard
        result = AgentTaskBoard._task_evidence_item_for_tool("read_file", None)
        assert isinstance(result, str)


# ── _advance_task_board_after_tool ───────────────────────────────────────────

class TestAdvanceTaskBoardAfterTool:
    def test_no_active_task_returns_false(self):
        from core.tasking.agent_taskboard import AgentTaskBoard
        atb = AgentTaskBoard()
        tb = _make_taskboard()
        result = atb._advance_task_board_after_tool(tb, "read_file", {})
        assert result is False

    def test_append_evidence_but_no_advance_for_non_complete(self):
        from core.tasking.agent_taskboard import AgentTaskBoard
        atb = AgentTaskBoard()
        task = _mock_task("task-1", "active")
        tb = _make_taskboard([task])
        with patch.object(tb, 'append_evidence', wraps=tb.append_evidence) as mock_append:
            result = atb._advance_task_board_after_tool(tb, "grep", {"pattern": "test"})
            assert result is False  # didn't advance (not complete_task)
            # Evidence should have been appended
            mock_append.assert_called_once()

    def test_complete_task_advances_board(self):
        from core.tasking.agent_taskboard import AgentTaskBoard
        atb = AgentTaskBoard()
        task1 = _mock_task("task-1", "active")
        task2 = _mock_task("task-2", "pending")
        tb = _make_taskboard([task1, task2])

        # Mock dependencies_satisfied to return True
        with patch.object(tb, 'dependencies_satisfied', return_value=True):
            with patch.object(tb, 'activate', return_value=True):
                with patch.object(tb, 'complete', wraps=tb.complete) as mock_complete:
                    result = atb._advance_task_board_after_tool(tb, "complete_task", {})
                    assert result is True
                    mock_complete.assert_called_once_with("task-1", evidence=None)

    def test_complete_task_does_not_append_its_own_evidence(self):
        from core.tasking.agent_taskboard import AgentTaskBoard
        atb = AgentTaskBoard()
        task = _mock_task("task-1", "active")
        tb = _make_taskboard([task])

        with patch.object(tb, 'dependencies_satisfied', return_value=False):
            with patch.object(tb, 'append_evidence', wraps=tb.append_evidence) as mock_append:
                atb._advance_task_board_after_tool(tb, "complete_task", {})
                # complete_task should NOT append evidence for itself
                mock_append.assert_not_called()

    def test_complete_task_no_next_pending_returns_true(self):
        from core.tasking.agent_taskboard import AgentTaskBoard
        atb = AgentTaskBoard()
        task1 = _mock_task("task-1", "active")
        tb = _make_taskboard([task1])
        # No pending tasks after this one
        with patch.object(tb, 'dependencies_satisfied', return_value=True):
            result = atb._advance_task_board_after_tool(tb, "complete_task", {})
            assert result is True

    def test_with_monitor_emits_board_advance(self):
        from core.tasking.agent_taskboard import AgentTaskBoard
        atb = AgentTaskBoard()
        task1 = _mock_task("task-1", "active")
        task2 = _mock_task("task-2", "pending")
        tb = _make_taskboard([task1, task2])
        monitor = MagicMock()

        with patch.object(tb, 'dependencies_satisfied', return_value=True):
            with patch.object(tb, 'activate', return_value=True):
                atb._advance_task_board_after_tool(tb, "complete_task", {}, monitor=monitor)
                monitor.emit.assert_called_once()
                call_args = monitor.emit.call_args[0]
                assert call_args[0] == "board_advance"
                assert call_args[1]["completed"] == "task-1"


# ── _finalize_task_board_for_answer ─────────────────────────────────────────

class TestFinalizeTaskBoardForAnswer:
    def test_no_active_task_returns_false(self):
        from core.tasking.agent_taskboard import AgentTaskBoard
        atb = AgentTaskBoard()
        tb = _make_taskboard()
        result = atb._finalize_task_board_for_answer(tb)
        assert result is False

    def test_non_final_task_returns_false(self):
        from core.tasking.agent_taskboard import AgentTaskBoard
        atb = AgentTaskBoard()
        task = _mock_task("task-1", "active")
        tb = _make_taskboard([task])
        # _final_should_complete_task returns False by default for non-final tasks
        with patch.object(atb, '_final_should_complete_task', return_value=False):
            with patch.object(tb, 'complete', wraps=tb.complete) as mock_complete:
                result = atb._finalize_task_board_for_answer(tb)
                assert result is False
                mock_complete.assert_not_called()

    def test_final_task_completes(self):
        from core.tasking.agent_taskboard import AgentTaskBoard
        atb = AgentTaskBoard()
        task = _mock_task("task-1", "active", title="final report")
        tb = _make_taskboard([task])
        with patch.object(atb, '_final_should_complete_task', return_value=True):
            with patch.object(tb, 'first_ready_pending_id', return_value=None):
                with patch.object(tb, 'complete', wraps=tb.complete) as mock_complete:
                    result = atb._finalize_task_board_for_answer(tb)
                    assert result is True
                    mock_complete.assert_called_once_with("task-1", evidence="final:assistant_response")

    def test_final_task_activates_next_if_ready(self):
        from core.tasking.agent_taskboard import AgentTaskBoard
        atb = AgentTaskBoard()
        task = _mock_task("task-1", "active")
        tb = _make_taskboard([task])
        with patch.object(atb, '_final_should_complete_task', return_value=True):
            with patch.object(tb, 'first_ready_pending_id', return_value="task-2"):
                with patch.object(tb, 'dependencies_satisfied', return_value=True):
                    with patch.object(tb, 'activate', return_value=True):
                        result = atb._finalize_task_board_for_answer(tb)
                        assert result is True
                        tb.activate.assert_called_once_with("task-2")


# ── _final_should_complete_task ──────────────────────────────────────────────

class TestFinalShouldCompleteTask:
    def test_delegates_to_task_evidence(self):
        from core.tasking.agent_taskboard import AgentTaskBoard
        task = _mock_task("task-1")
        # This just delegates to task_evidence.final_should_complete_task
        result = AgentTaskBoard._final_should_complete_task(task)
        assert isinstance(result, bool)


# ── _final_report_task_id ────────────────────────────────────────────────────

class TestFinalReportTaskId:
    def test_delegates_to_task_evidence(self):
        from core.tasking.agent_taskboard import AgentTaskBoard
        tb = _make_taskboard()
        result = AgentTaskBoard._final_report_task_id(tb)
        # Just verifies delegation works without error
        assert isinstance(result, (str, type(None)))
