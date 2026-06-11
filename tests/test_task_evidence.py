"""Tests for core/task_evidence.py — tool-backed evidence labels."""
from __future__ import annotations

from core.tasking.task_board import TaskBoard, TaskItem
from core.tasking.task_evidence import (
    TOOL_BACKED_EVIDENCE_TOOLS,
    evidence_item_is_tool_backed,
    tool_evidence_label,
    is_verification_step,
    has_failing_tests,
    has_passing_verification,
    has_passing_after_failure,
    has_verification_tool_evidence,
    has_concrete_evidence,
    final_report_task_id,
    final_should_complete_task,
    taskboard_tool_evidence_item,
    tool_should_advance_task,
)


class TestToolBackedEvidenceTools:
    """TOOL_BACKED_EVIDENCE_TOOLS must not contain fake labels."""

    def test_no_fake_diff_review(self):
        """'diff_review' must NOT be in TOOL_BACKED_EVIDENCE_TOOLS."""
        assert "diff_review" not in TOOL_BACKED_EVIDENCE_TOOLS

    def test_no_fake_review_scorer(self):
        """'review_scorer' must NOT be in TOOL_BACKED_EVIDENCE_TOOLS."""
        assert "review_scorer" not in TOOL_BACKED_EVIDENCE_TOOLS

    def test_contains_real_tools(self):
        """Set contains only real operational tools."""
        assert "read_file" in TOOL_BACKED_EVIDENCE_TOOLS
        assert "write_file" in TOOL_BACKED_EVIDENCE_TOOLS
        assert "edit_file" in TOOL_BACKED_EVIDENCE_TOOLS
        assert "shell" in TOOL_BACKED_EVIDENCE_TOOLS
        assert "grep" in TOOL_BACKED_EVIDENCE_TOOLS
        assert "find_files" in TOOL_BACKED_EVIDENCE_TOOLS
        assert "git_status" in TOOL_BACKED_EVIDENCE_TOOLS
        assert "test_runner" in TOOL_BACKED_EVIDENCE_TOOLS
        assert "web_fetch" in TOOL_BACKED_EVIDENCE_TOOLS
        assert "web_snapshot" in TOOL_BACKED_EVIDENCE_TOOLS

    def test_correct_count(self):
        """Only the 10 real tools should be present."""
        assert len(TOOL_BACKED_EVIDENCE_TOOLS) == 10


class TestEvidenceItemIsToolBacked:
    """Tests for evidence_item_is_tool_backed() predicate."""

    def test_read_file(self):
        assert evidence_item_is_tool_backed("read_file:test.py") is True

    def test_grep(self):
        assert evidence_item_is_tool_backed("grep:keyword") is True

    def test_shell(self):
        assert evidence_item_is_tool_backed("shell:command") is True

    def test_write_file(self):
        assert evidence_item_is_tool_backed("write_file:test.py") is True

    def test_edit_file(self):
        assert evidence_item_is_tool_backed("edit_file:test.py") is True

    def test_find_files(self):
        assert evidence_item_is_tool_backed("find_files:*.py") is True

    def test_git_status_with_colon(self):
        """git_status needs the colon to register as tool-backed."""
        assert evidence_item_is_tool_backed("git_status") is False
        assert evidence_item_is_tool_backed("git_status:") is True

    def test_test_runner(self):
        assert evidence_item_is_tool_backed("test_runner:pytest") is True

    def test_web_fetch(self):
        assert evidence_item_is_tool_backed("web_fetch:url") is True

    def test_web_snapshot(self):
        assert evidence_item_is_tool_backed("web_snapshot:url") is True

    def test_fake_diff_review(self):
        """Fake label 'diff_review' is NOT tool-backed."""
        assert evidence_item_is_tool_backed("diff_review") is False

    def test_fake_review_scorer(self):
        """Fake label 'review_scorer' is NOT tool-backed."""
        assert evidence_item_is_tool_backed("review_scorer") is False

    def test_empty_string(self):
        assert evidence_item_is_tool_backed("") is False

    def test_random_string(self):
        assert evidence_item_is_tool_backed("random_label") is False

    def test_none(self):
        assert evidence_item_is_tool_backed(None) is False


class TestToolEvidenceLabel:
    """Tests for tool_evidence_label() formatting."""

    def test_read_file(self):
        assert tool_evidence_label("read_file", {"path": "test.py"}) == "read_file:test.py"

    def test_grep(self):
        assert tool_evidence_label("grep", {"pattern": "def main"}) == "grep:def main"

    def test_shell(self):
        result = tool_evidence_label("shell", {"command": "pytest tests/"})
        assert result.startswith("shell:")

    def test_git_status(self):
        assert tool_evidence_label("git_status", {}) == "git_status"

    def test_unknown_tool(self):
        assert tool_evidence_label("unknown_tool", {}) == "unknown_tool:called"


class TestVerificationHelpers:
    """Tests for verification-related helper functions."""

    def test_is_verification_step_verify(self):
        assert is_verification_step("verify login flow") is True

    def test_is_verification_step_test(self):
        assert is_verification_step("run all tests") is True

    def test_is_verification_step_skipped(self):
        assert is_verification_step("skipped test") is False

    def test_has_failing_tests_failed(self):
        assert has_failing_tests("test failed") is True

    def test_has_failing_tests_clean(self):
        assert has_failing_tests("all passed") is False

    def test_has_passing_verification(self):
        assert has_passing_verification("tests passed", []) is True

    def test_has_passing_verification_with_failure(self):
        assert has_passing_verification("tests passed but one failed", []) is False

    def test_has_passing_after_failure(self):
        assert has_passing_after_failure("failed then passed exit code 0") is True

    def test_has_verification_tool_evidence(self):
        assert has_verification_tool_evidence(["test_runner:pytest"]) is True

    def test_has_concrete_evidence(self):
        assert has_concrete_evidence("read_file:test.py") is True


class TestTaskboardAdvancementPolicy:
    """Main Agent taskboard policy lives in core.task_evidence."""

    def test_broad_scope_inspect_requires_scope_evidence(self):
        task = TaskItem("1", "Identify evidence scope", "active", kind="inspect", completion_gate="tool")

        assert tool_should_advance_task("read_file", task, 0, 3, arguments={"path": "core/agent.py"}) is False
        assert tool_should_advance_task("find_files", task, 0, 3, arguments={"pattern": "*.py"}) is True

    def test_verify_and_report_gates_are_policy_driven(self):
        verify = TaskItem("1", "Verify resolution", "active", kind="verify", completion_gate="verification")
        report = TaskItem("2", "Report result", "active", kind="report", completion_gate="final")

        assert tool_should_advance_task("read_file", verify, 0, 2, arguments={"path": "auth.py"}) is False
        assert tool_should_advance_task("shell", verify, 0, 2, arguments={"command": "python -m pytest -q"}) is True
        assert tool_should_advance_task("shell", report, 1, 2, arguments={"command": "echo done"}) is False
        assert final_should_complete_task(report) is True

    def test_agent_compatible_taskboard_evidence_label(self):
        assert taskboard_tool_evidence_item("read_file", {"path": "auth.py"}) == "read_file:auth.py"
        assert taskboard_tool_evidence_item("shell", {"command": "python -m pytest -q"}) == "shell:python -m pytest -q"
        assert taskboard_tool_evidence_item("git_status", {}) == "git_status"

    def test_final_report_task_id_uses_metadata(self):
        board = TaskBoard(tasks=[
            TaskItem("inspect", "Inspect", "completed", kind="inspect", completion_gate="tool"),
            TaskItem("reply", "Report result", "pending", kind="report", completion_gate="final", depends_on=["inspect"]),
        ])

        assert final_report_task_id(board) == "reply"
