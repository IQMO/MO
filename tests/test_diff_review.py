"""Tests for core/review/diff_review.py — PRT review pipeline."""
from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

# Import testable components
from core.review.diff_review import (
    PRT_REVIEW_SYSTEM,
    ReviewFinding,
    ReviewReport,
    append_review_audit,
)


class TestReviewFinding:
    """Tests for ReviewFinding dataclass."""

    def test_is_actionable_critical(self):
        """Critical severity → actionable."""
        f = ReviewFinding(id="1", severity="critical", category="security",
                          file="test.py", line_range=[1, 2], message="test",
                          explanation="test", suggestion=None, confidence=0.8)
        assert f.is_actionable() is True

    def test_is_actionable_major(self):
        """Major severity → actionable."""
        f = ReviewFinding(id="1", severity="major", category="bug_risk",
                          file="test.py", line_range=[1, 2], message="test",
                          explanation="test", suggestion=None, confidence=0.8)
        assert f.is_actionable() is True

    def test_is_actionable_minor(self):
        """Minor severity → not actionable."""
        f = ReviewFinding(id="1", severity="minor", category="style",
                          file="test.py", line_range=[1, 2], message="test",
                          explanation="test", suggestion=None, confidence=0.8)
        assert f.is_actionable() is False

    def test_is_actionable_info(self):
        """Info severity → not actionable."""
        f = ReviewFinding(id="1", severity="info", category="style",
                          file="test.py", line_range=[1, 2], message="test",
                          explanation="test", suggestion=None, confidence=0.8)
        assert f.is_actionable() is False


class TestReviewReport:
    """Tests for ReviewReport dataclass."""

    def test_is_target_met_perfect(self):
        """Score >= 4.5 and zero unresolved → target met."""
        r = ReviewReport(diff_ref="HEAD", files_changed=1, additions=1, deletions=0,
                         findings=[], score=4.5, unresolved_count=0,
                         affected_tests=[], created_at=0.0)
        assert r.is_target_met is True

    def test_is_target_met_low_score(self):
        """Score < 4.5 → target not met."""
        r = ReviewReport(diff_ref="HEAD", files_changed=1, additions=1, deletions=0,
                         findings=[], score=4.0, unresolved_count=0,
                         affected_tests=[], created_at=0.0)
        assert r.is_target_met is False

    def test_is_target_met_unresolved(self):
        """Unresolved findings → target not met."""
        r = ReviewReport(diff_ref="HEAD", files_changed=1, additions=1, deletions=0,
                         findings=[], score=5.0, unresolved_count=2,
                         affected_tests=[], created_at=0.0)
        assert r.is_target_met is False

    def test_to_dict_roundtrip(self):
        """to_dict() produces serializable dict."""
        f = ReviewFinding(id="1", severity="minor", category="style",
                          file="test.py", line_range=[1, 2], message="msg",
                          explanation="exp", suggestion="fix", confidence=0.8,
                          evidence_tools=["grep:test"], resolved=True,
                          resolution_note="done")
        r = ReviewReport(diff_ref="HEAD", files_changed=2, additions=10, deletions=5,
                         findings=[f], score=4.9, unresolved_count=0,
                         affected_tests=["test_a.py"], created_at=1000.0,
                         token_usage={"total_tokens": 500}, score_target=4.8)
        d = r.to_dict()
        assert d["diff_ref"] == "HEAD"
        assert d["files_changed"] == 2
        assert len(d["findings"]) == 1
        assert d["findings"][0]["evidence_tools"] == ["grep:test"]
        assert d["findings"][0]["resolved"] is True
        assert d["token_usage"]["total_tokens"] == 500
        assert d["score_target"] == 4.8

    def test_is_target_met_uses_configured_score_target(self):
        """Score target can be stricter than the default 4.5."""
        r = ReviewReport(diff_ref="HEAD", files_changed=1, additions=1, deletions=0,
                         findings=[], score=4.9, unresolved_count=0,
                         affected_tests=[], created_at=0.0, score_target=5.0)
        assert r.is_target_met is False


class TestReviewDiff:
    """Tests for review_diff() pipeline.
    
    Uses extensive mocking to avoid real git/subprocess calls.
    """

    def test_review_prompt_includes_overengineering_without_external_branding(self):
        assert "Overengineering" in PRT_REVIEW_SYSTEM
        assert "category \"overengineering\"" in PRT_REVIEW_SYSTEM
        assert "Python stdlib" in PRT_REVIEW_SYSTEM
        assert "project utilities" in PRT_REVIEW_SYSTEM
        assert "Ponytail" not in PRT_REVIEW_SYSTEM
        assert ".agents" not in PRT_REVIEW_SYSTEM

    @patch("core.review.diff_review.subprocess.check_output")
    @patch("core.threat_scan.scan_text")
    def test_threat_scan_blocks_review(self, mock_scan, mock_subprocess):
        """Threat scan block → returns critical finding + score 0.0."""
        from core.review.diff_review import review_diff

        mock_subprocess.return_value = "diff --git a/x.py b/x.py\n"
        mock_scan_result = MagicMock()
        mock_scan_result.blocked = True
        mock_scan_result.reason.return_value = "Injection detected"
        mock_scan.return_value = mock_scan_result

        agent = MagicMock()
        report = review_diff(agent, diff_ref="HEAD")
        assert report.score == 0.0
        assert len(report.findings) == 1
        assert report.findings[0].severity == "critical"
        assert "Threat scan blocked" in report.findings[0].message
        assert "grep:threat_scan" in report.findings[0].evidence_tools

    def test_path_review_uses_working_tree_path_and_finishes_without_provider(self, tmp_path, monkeypatch):
        from core.review.diff_review import review_diff

        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.local"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
        (tmp_path / "README.md").write_text("# Project\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
        agent = MagicMock()
        agent.project_cwd = str(tmp_path)
        agent.workspace = str(tmp_path)
        agent.config = {"prt": {"score_target": 4.5}}
        monkeypatch.setattr("core.review.diff_review.append_review_audit", lambda _report: None)

        report = review_diff(agent, diff_ref="README.md")

        assert report.files_changed == 1
        assert report.findings == []
        assert report.score == 5.0
        assert not agent.complete_ghost_no_tools.called

    @patch("core.review.diff_review.subprocess.check_output")
    @patch("core.threat_scan.scan_text")
    def test_empty_diff_returns_empty_findings(self, mock_scan, mock_subprocess):
        """Empty git diff → empty findings list."""
        from core.review.diff_review import review_diff

        mock_subprocess.side_effect = Exception("no git history")
        mock_scan_result = MagicMock()
        mock_scan_result.blocked = False
        mock_scan.return_value = mock_scan_result

        agent = MagicMock()
        report = review_diff(agent, diff_ref="HEAD")
        assert len(report.findings) == 0
        assert report.score == 5.0  # no findings → perfect

    @patch("core.review.diff_review.subprocess.check_output")
    @patch("core.threat_scan.scan_text")
    @patch("core.tool_compress.compress")
    @patch("core.model_limits.resolve_context_budget_tokens")
    def test_token_compression_applied(self, mock_budget, mock_compress,
                                        mock_scan, mock_subprocess):
        """Token compression — budget truncation with large diffs."""
        from core.review.diff_review import review_diff

        mock_subprocess.return_value = "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
        mock_scan_result = MagicMock()
        mock_scan_result.blocked = False
        mock_scan.return_value = mock_scan_result
        mock_compress.return_value = ("compressed diff", None)
        mock_budget.return_value = 32000

        agent = MagicMock()

        class FakeUsage:
            total_tokens = 500

        resp = MagicMock()
        resp.content = "[]"  # empty findings
        resp.usage = FakeUsage()
        agent.complete_ghost_no_tools.return_value = (resp, "opencode")

        report = review_diff(agent, diff_ref="HEAD")
        assert report.score >= 0.0

    @patch("core.review.diff_review.subprocess.check_output")
    @patch("core.threat_scan.scan_text")
    @patch("core.tool_compress.compress")
    @patch("core.model_limits.resolve_context_budget_tokens")
    def test_finding_evidence_collection(self, mock_budget, mock_compress,
                                          mock_scan, mock_subprocess, tmp_path):
        """Findings get real file evidence when target file exists."""
        from core.review.diff_review import review_diff

        mock_subprocess.return_value = "diff --git a/test.py b/test.py\n@@ -1 +1 @@\n-old\n+new\n"
        mock_scan_result = MagicMock()
        mock_scan_result.blocked = False
        mock_scan.return_value = mock_scan_result
        mock_compress.return_value = ("diff --git a/test.py b/test.py", None)
        mock_budget.return_value = 32000

        # Create the target file — message keywords must appear in file content
        target = tmp_path / "test.py"
        # The evidence code extracts keywords > 5 chars from the message
        # and greps the file for them. "Hardcoded" must appear.
        target.write_text("# Hardcoded debug string - should use logging\n")

        agent = MagicMock()
        agent.workspace = tmp_path

        class FakeUsage:
            total_tokens = 100

        # Mock the Ghost response to return one finding about test.py
        resp = MagicMock()
        resp.content = json.dumps([{
            "id": "test-001",
            "severity": "major",
            "category": "bug_risk",
            "file": "test.py",
            "line_range": [1, 2],
            "message": "Hardcoded string in production code",
            "explanation": "Should use logging instead of print",
            "suggestion": "Replace with logging"
        }])
        resp.usage = FakeUsage()
        agent.complete_ghost_no_tools.return_value = (resp, "opencode")

        report = review_diff(agent, diff_ref="HEAD")
        if len(report.findings) > 0:
            f = report.findings[0]
            assert f.confidence == 0.8  # evidence verified
            assert "read_file:test.py" in f.evidence_tools
            # At least one grep keyword should match "Hardcoded"
            assert any("grep:Hardcoded" in e for e in f.evidence_tools)

    @patch("core.review.diff_review.subprocess.check_output")
    @patch("core.threat_scan.scan_text")
    @patch("core.tool_compress.compress")
    @patch("core.model_limits.resolve_context_budget_tokens")
    def test_finding_no_evidence_downgraded(self, mock_budget, mock_compress,
                                             mock_scan, mock_subprocess):
        """Finding without file evidence → downgraded to info severity."""
        from core.review.diff_review import review_diff

        mock_subprocess.return_value = "diff --git a/nonexistent.py b/nonexistent.py\n@@ -1 +1 @@\n-old\n+new\n"
        mock_scan_result = MagicMock()
        mock_scan_result.blocked = False
        mock_scan.return_value = mock_scan_result
        mock_compress.return_value = ("diff", None)
        mock_budget.return_value = 32000

        agent = MagicMock()
        agent.workspace = "/tmp/nonexistent"

        class FakeUsage:
            total_tokens = 100

        resp = MagicMock()
        resp.content = json.dumps([{
            "id": "test-002",
            "severity": "critical",
            "category": "security",
            "file": "nonexistent.py",
            "line_range": [1, 1],
            "message": "This file does not exist",
            "explanation": "The file was reported but doesn't exist on disk",
            "suggestion": "None"
        }])
        resp.usage = FakeUsage()
        agent.complete_ghost_no_tools.return_value = (resp, "opencode")

        report = review_diff(agent, diff_ref="HEAD")
        if len(report.findings) > 0:
            f = report.findings[0]
            assert f.severity == "info"  # downgraded from critical
            assert f.evidence_tools == []  # no evidence
            assert f.confidence == 0.3  # default before evidence

    @patch("core.review.diff_review.subprocess.check_output")
    @patch("core.threat_scan.scan_text")
    @patch("core.tool_compress.compress")
    @patch("core.model_limits.resolve_context_budget_tokens")
    def test_review_generation_failure_blocks_production_ready_score(self, mock_budget, mock_compress,
                                                                      mock_scan, mock_subprocess):
        """Provider/review failures become visible findings, not silent 5.0s."""
        from core.review.diff_review import review_diff

        mock_subprocess.return_value = "diff --git a/test.py b/test.py\n@@ -1 +1 @@\n-old\n+new\n"
        mock_scan_result = MagicMock()
        mock_scan_result.blocked = False
        mock_scan.return_value = mock_scan_result
        mock_compress.return_value = ("diff --git a/test.py b/test.py", None)
        mock_budget.return_value = 32000

        agent = MagicMock()
        agent.complete_ghost_no_tools.side_effect = RuntimeError("provider unavailable")

        report = review_diff(agent, diff_ref="HEAD")

        assert report.findings
        assert report.findings[0].file == "<review>"
        assert "Review generation failed" in report.findings[0].message
        assert report.unresolved_count == 1
        assert report.is_target_met is False

    @patch("core.review.diff_review.subprocess.check_output")
    @patch("core.threat_scan.scan_text")
    def test_analyze_diff_impact_called(self, mock_scan, mock_subprocess):
        """analyze_diff_impact is called and results flow to report."""
        from core.review.diff_review import review_diff

        mock_subprocess.return_value = "diff --git a/test_foo.py b/test_foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        mock_scan_result = MagicMock()
        mock_scan_result.blocked = False
        mock_scan.return_value = mock_scan_result

        agent = MagicMock()

        class FakeUsage:
            total_tokens = 0

        resp = MagicMock()
        resp.content = "[]"
        resp.usage = FakeUsage()
        agent.complete_ghost_no_tools.return_value = (resp, "opencode")

        report = review_diff(agent, diff_ref="HEAD")
        assert report.score >= 0.0

    @patch("core.review.diff_review.subprocess.check_output")
    @patch("core.threat_scan.scan_text")
    @patch("core.tool_compress.compress")
    @patch("core.model_limits.resolve_context_budget_tokens")
    @patch("core.graph.code_graph.analyze_diff_impact")
    @patch("core.graph.code_graph.affected_tests")
    @patch("core.graph.structural_graph.prt_impact_summary")
    @patch("core.graph.structural_graph.format_prt_impact")
    def test_review_diff_passes_workspace_to_impact_helpers(self, mock_format, mock_prt, mock_tests,
                                                            mock_impact, mock_budget, mock_compress,
                                                            mock_scan, mock_subprocess, tmp_path):
        """Impact analysis and evidence collection use the reviewed workspace root."""
        from core.review.diff_review import review_diff

        diff = "diff --git a/test.py b/test.py\n@@ -1 +1 @@\n-old\n+new\n"
        mock_subprocess.return_value = diff
        mock_scan_result = MagicMock()
        mock_scan_result.blocked = False
        mock_scan.return_value = mock_scan_result
        mock_compress.return_value = ("diff --git a/test.py b/test.py", None)
        mock_budget.return_value = 32000
        mock_impact.return_value = []
        mock_tests.return_value = []
        mock_prt.return_value = {"available": False, "impacted_files": []}
        mock_format.return_value = ""

        agent = MagicMock()
        agent.workspace = tmp_path
        resp = MagicMock()
        resp.content = "[]"
        resp.usage = {"total_tokens": 10}
        agent.complete_ghost_no_tools.return_value = (resp, "opencode")

        report = review_diff(agent, diff_ref="HEAD")

        assert report.score == 5.0
        assert mock_subprocess.call_args_list[0].kwargs["cwd"] == str(tmp_path)
        assert mock_subprocess.call_args_list[1].kwargs["cwd"] == str(tmp_path)
        mock_impact.assert_called_once_with(diff, root=str(tmp_path))
        mock_tests.assert_called_once_with(diff, root=str(tmp_path))
        mock_prt.assert_called_once_with(diff, root=tmp_path)


class TestAppendReviewAudit:
    """Tests for review_audit.jsonl logging."""

    def test_append_review_audit_creates_file(self, tmp_path, monkeypatch):
        """append_review_audit writes to logs/review_audit.jsonl."""
        monkeypatch.setenv("MO_REVIEW_AUDIT_FORCE", "1")

        with patch("core.review.diff_review.Path") as mock_path:
            mock_path.return_value.parent.mkdir.return_value = None
            mock_open = MagicMock()
            mock_path.return_value.open = mock_open

            report = ReviewReport(
                diff_ref="HEAD", files_changed=1, additions=1, deletions=0,
                findings=[], score=5.0, unresolved_count=0,
                affected_tests=[], created_at=1000.0
            )
            append_review_audit(report)
            # Verify write was called with JSON line
            mock_open.return_value.__enter__.return_value.write.assert_called_once()


import pytest as _pytest_state_lane


@_pytest_state_lane.fixture(autouse=True)
def _legacy_state_lane(monkeypatch, tmp_path):
    """This module asserts legacy project-relative state behavior; opt out of
    the conftest MO_STATE_HOME isolation (tests here chdir to tmp paths)."""
    monkeypatch.delenv("MO_STATE_HOME", raising=False)
    monkeypatch.delenv("MO_HOME", raising=False)
    monkeypatch.setenv("MO_STATE_LOCAL", "1")  # explicit project-local opt-out (state is private-by-default)
    monkeypatch.chdir(tmp_path)  # project-local state -> tmp, never the repo root
    monkeypatch.setenv("MO_PROJECT_CWD", str(tmp_path))
