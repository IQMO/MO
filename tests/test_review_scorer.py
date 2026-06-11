"""Tests for core/review/review_scorer.py — evidence-weighted scoring engine."""
from __future__ import annotations

from core.review.review_scorer import ReviewScorer


class MockFinding:
    """Minimal ReviewFinding stand-in for scoring tests."""
    def __init__(self, severity: str = "info", evidence_tools: list[str] | None = None,
                 file: str = "test.py", resolved: bool = False):
        self.severity = severity
        self.evidence_tools = evidence_tools or []
        self.file = file
        self.resolved = resolved


class MockReport:
    """Minimal ReviewReport stand-in for scoring tests."""
    def __init__(self, findings: list | None = None, files_changed: int = 1,
                 token_usage: dict | None = None):
        self.findings = findings or []
        self.files_changed = files_changed
        self.token_usage = token_usage or {}


class TestReviewScorer:
    """Tests for the evidence-weighted scoring engine."""

    def setup_method(self):
        self.scorer = ReviewScorer()

    def test_perfect_score_empty_findings(self):
        """Empty findings → perfect 5.0."""
        report = MockReport(findings=[])
        assert self.scorer.report_score(report) == 5.0

    def test_perfect_score_resolved_findings(self):
        """All findings resolved → no deductions."""
        report = MockReport(findings=[
            MockFinding(severity="critical", resolved=True),
            MockFinding(severity="major", resolved=True),
        ])
        assert self.scorer.report_score(report) == 5.0

    def test_critical_deduction(self):
        """One unresolved critical → deduct 1.0 from 5.0."""
        report = MockReport(findings=[
            MockFinding(severity="critical", evidence_tools=["grep:test"]),
        ])
        assert self.scorer.report_score(report) == 4.0

    def test_major_deduction(self):
        """One unresolved major → deduct 0.5."""
        report = MockReport(findings=[
            MockFinding(severity="major", evidence_tools=["grep:test"]),
        ])
        assert self.scorer.report_score(report) == 4.5

    def test_minor_deduction(self):
        """One unresolved minor → deduct 0.1."""
        report = MockReport(findings=[
            MockFinding(severity="minor", evidence_tools=["grep:test"]),
        ])
        assert self.scorer.report_score(report) == 4.9

    def test_info_deduction_rounded(self):
        """One unresolved info → deduct 0.05 → 4.95 rounds to 5.0."""
        report = MockReport(findings=[
            MockFinding(severity="info", evidence_tools=["grep:test"]),
        ])
        score = self.scorer.report_score(report)
        # 5.0 - 0.05 = 4.95, rounds to 5.0 due to round(..., 1)
        assert score == 5.0

    def test_cumulative_minor_deductions(self):
        """10 minor findings = 10 * 0.1 = 1.0 deduction → 4.0."""
        report = MockReport(findings=[
            MockFinding(severity="minor", evidence_tools=["grep:x"]) for _ in range(10)
        ])
        assert self.scorer.report_score(report) == 4.0

    def test_score_floor_at_zero(self):
        """Multiple criticals cannot push score below 0.0."""
        report = MockReport(findings=[
            MockFinding(severity="critical") for _ in range(10)
        ])
        assert self.scorer.report_score(report) >= 0.0

    def test_score_ceiling_at_five(self):
        """Score can never exceed 5.0."""
        report = MockReport(findings=[])
        assert self.scorer.report_score(report) <= 5.0

    def test_mixed_severities(self):
        """Mixed critical + major + minor + info."""
        report = MockReport(findings=[
            MockFinding(severity="critical", evidence_tools=["grep:x"]),
            MockFinding(severity="major", evidence_tools=["grep:y"]),
            MockFinding(severity="minor", evidence_tools=["grep:z"]),
        ])
        score = self.scorer.report_score(report)
        # 5.0 - 1.0 - 0.5 - 0.1 = 3.4
        assert score == 3.4

    def test_evidence_coverage_improves_deduction_behavior(self):
        """Finding with evidence tools gets full weight; without gets lower confidence path."""
        with_evidence = MockFinding(severity="major", evidence_tools=["grep:test"])
        assert self.scorer.finding_confidence(with_evidence) >= 0.5

    def test_no_evidence_low_confidence(self):
        """Finding with zero evidence tools → confidence 0.2."""
        finding = MockFinding(severity="major", evidence_tools=[])
        assert self.scorer.finding_confidence(finding) == 0.2

    def test_unverified_finding_penalizes_less_than_tool_backed(self):
        """Evidence-weighted: an unverified major must dent the score less than a
        tool-backed major (the score reflects verified problems)."""
        verified = MockReport(findings=[MockFinding(severity="major", evidence_tools=["grep:x"])])
        unverified = MockReport(findings=[MockFinding(severity="major", evidence_tools=[])])
        assert self.scorer.report_score(unverified) > self.scorer.report_score(verified)

    def test_single_tool_evidence_confidence(self):
        """One tool-backed evidence → 0.8."""
        finding = MockFinding(severity="major", evidence_tools=["grep:test"])
        assert self.scorer.finding_confidence(finding) == 0.8

    def test_multi_tool_evidence_confidence(self):
        """Two or more tool-backed evidence → 1.0."""
        finding = MockFinding(severity="major", evidence_tools=["grep:test", "read_file:test.py"])
        assert self.scorer.finding_confidence(finding) == 1.0

    def test_fake_evidence_tool_ignored(self):
        """Fake evidence like 'diff_review' is not tool-backed → low confidence."""
        finding = MockFinding(severity="major", evidence_tools=["diff_review"])
        assert self.scorer.finding_confidence(finding) == 0.4

    def test_compression_bonus(self):
        """token_usage with compression_saved adds 0.1."""
        report = MockReport(
            findings=[MockFinding(severity="minor", evidence_tools=["grep:x"])],
            token_usage={"total_tokens": 1000, "compression_saved": 200}
        )
        # 5.0 - 0.1 (minor) + 0.1 (bonus) = 5.0, capped at 5.0
        score = self.scorer.report_score(report)
        assert score == 5.0

    def test_files_changed_zero(self):
        """When files_changed is 0, score remains bounded and valid."""
        report = MockReport(
            findings=[MockFinding(severity="info", evidence_tools=["grep:x"])],
            files_changed=0
        )
        score = self.scorer.report_score(report)
        # 4.95 rounds to 5.0
        assert score == 5.0
