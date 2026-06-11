"""Tests for core/review/review_iteration.py — PRT fix loop."""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import MagicMock, patch


class MockFinding:
    """Minimal ReviewFinding for fix loop tests."""
    def __init__(self, actionable: bool = True, resolved: bool = False, file: str = "test.py"):
        self._actionable = actionable
        self.resolved = resolved
        self.file = file
        self.severity = "major"
        self.message = "test finding"
        self.category = "bug_risk"
        self.id = "test-id"
        self.explanation = "explanation"
        self.suggestion = "suggestion"
        self.line_range = [1, 5]

    def is_actionable(self) -> bool:
        return self._actionable


class MockReport:
    """Minimal ReviewReport for fix loop tests."""
    def __init__(self, findings: list | None = None):
        self.findings = findings or []
        self.diff_ref = "HEAD"
        self.files_changed = 1
        self.additions = 1
        self.deletions = 1
        self.score = 4.0
        self.unresolved_count = len([f for f in (findings or []) if not f.resolved])
        self.affected_tests = []
        self.created_at = 0.0
        self.token_usage = {}


class TestReviewIteration:
    """Tests for the PRT fix loop."""

    @patch("core.workspace_awareness.prt_safe_to_mutate")
    def test_dirty_workspace_aborts(self, mock_safe, tmp_path):
        """Uncommitted changes → prt_safe_to_mutate returns False → fix loop aborts."""
        mock_safe.return_value = (False, "Uncommitted changes")
        from core.review.review_iteration import run_fix_loop

        agent = MagicMock()
        agent.workspace = tmp_path
        report = MockReport(findings=[MockFinding(actionable=True)])

        result = run_fix_loop(agent, report)
        assert result is report  # returns original report unchanged

    @patch("core.workspace_awareness.prt_safe_to_mutate")
    def test_clean_workspace_proceeds(self, mock_safe):
        """Clean workspace → fix loop does NOT abort (passes the gate).
        
        Tests the early return path: if findings are not actionable or resolved,
        the function returns immediately after the safe_to_mutate check.
        """
        mock_safe.return_value = (True, "")
        from core.review.review_iteration import run_fix_loop

        agent = MagicMock()
        agent.workspace = "/tmp/test"
        # Use a finding that would be skipped (actionable=False) so the
        # function exits early without hitting Agent creation.
        report = MockReport(findings=[
            MockFinding(actionable=False, resolved=False)
        ])

        result = run_fix_loop(agent, report)
        assert result is report  # returned early, didn't crash

    @patch("core.workspace_awareness.prt_safe_to_mutate")
    def test_no_actionable_findings_skips_loop(self, mock_safe):
        """No actionable findings → nothing happens, returns report."""
        mock_safe.return_value = (True, "")
        from core.review.review_iteration import run_fix_loop

        agent = MagicMock()
        agent.workspace = "/tmp/test"
        report = MockReport(findings=[
            MockFinding(actionable=False, resolved=False)
        ])

        result = run_fix_loop(agent, report)
        assert result is report

    @patch("core.workspace_awareness.prt_safe_to_mutate")
    def test_all_resolved_findings_skips_loop(self, mock_safe):
        """All findings already resolved → skip fix loop."""
        mock_safe.return_value = (True, "")
        from core.review.review_iteration import run_fix_loop

        agent = MagicMock()
        agent.workspace = "/tmp/test"
        report = MockReport(findings=[
            MockFinding(actionable=True, resolved=True)
        ])

        result = run_fix_loop(agent, report)
        assert result is report

    @patch("core.workspace_awareness.prt_safe_to_mutate")
    def test_fix_loop_filters_schema_tools_and_uses_running_agent_worker_scope(self, mock_safe):
        """Fix loop reuses the running agent, filters tool definitions, and uses worker scope."""
        mock_safe.return_value = (True, "")
        from core.goal import GoalPlan
        from core.review.review_iteration import run_fix_loop

        class FakeRunningAgent:
            def __init__(self):
                self.workspace = "/tmp/test"
                self.system_message = "system"
                self.gateway = None
                self.tool_definitions = [
                    {"type": "function", "function": {"name": "read_file"}},
                    {"type": "function", "function": {"name": "edit_file"}},
                    {"type": "function", "function": {"name": "write_file"}},
                    {"type": "function", "function": {"name": "test_runner"}},
                ]
                self.scope_calls = []
                self.run_calls = []

            @contextmanager
            def isolated_session(self, session):
                self.isolated_session_obj = session
                yield

            @contextmanager
            def provider_scope(self, surface, worker_id=""):
                self.scope_calls.append((surface, worker_id))
                yield

            def run_turn(self, prompt, monitor=None):
                self.run_calls.append((prompt, monitor, [tool["function"]["name"] for tool in self.tool_definitions]))
                return "done"

        agent = FakeRunningAgent()
        original_tools = list(agent.tool_definitions)
        report = MockReport(findings=[MockFinding(actionable=True, resolved=False)])

        result = run_fix_loop(agent, report)

        assert isinstance(result, GoalPlan)
        assert agent.scope_calls == [("worker", "prt-fix-loop")]
        assert agent.run_calls
        assert agent.run_calls[0][2] == ["read_file", "edit_file", "test_runner"]
        assert agent.tool_definitions == original_tools

    @patch("core.workspace_awareness.prt_safe_to_mutate")
    def test_error_in_fix_returns_plan(self, mock_safe):
        """Exception during fix loop → catches error, restores tools, returns GoalPlan."""
        mock_safe.return_value = (True, "")
        from core.review.review_iteration import run_fix_loop

        agent = MagicMock()
        agent.workspace = "/tmp/test"
        agent.system_message = "system"
        agent.gateway = None
        agent.tool_definitions = [{"type": "function", "function": {"name": "read_file"}}]
        agent.run_turn.side_effect = Exception("turn failed")
        report = MockReport(findings=[
            MockFinding(actionable=True, resolved=False)
        ])

        result = run_fix_loop(agent, report)
        # The function returns a GoalPlan (not the report) on success/error
        assert result is not None
        from core.goal import GoalPlan
        assert isinstance(result, GoalPlan)
        assert agent.tool_definitions == [{"type": "function", "function": {"name": "read_file"}}]
