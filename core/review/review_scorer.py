"""
MO's own evidence-weighted scorer.

Starts at five and subtracts evidence-weighted unresolved-finding penalties.
Neither this score nor evidence support is a calibrated correctness probability.
No provider calls in scoring — pure deterministic math.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from core.tasking.task_evidence import evidence_item_is_tool_backed

if TYPE_CHECKING:
    from core.review.diff_review import ReviewReport, ReviewFinding


class ReviewScorer:
    """Scores findings and reports using MO's evidence system."""
    
    def finding_confidence(self, finding: "ReviewFinding") -> float:
        """Confidence in a single finding (0.0-1.0).
        
        Requires tool-backed evidence for full confidence.
        """
        if not finding.evidence_tools:
            return 0.2  # low confidence if no evidence
            
        tool_backed_count = sum(1 for e in finding.evidence_tools if evidence_item_is_tool_backed(e))
        
        if tool_backed_count == 0:
            return 0.4
        if tool_backed_count == 1:
            return 0.8
        return 1.0
    
    @staticmethod
    def _strongly_ignored_categories() -> set:
        """Categories the operator has FIRMLY taught PRT to ignore (>= 3 dismissals and
        more ignored than fixed). A thin/one-off dismissal is excluded so scores are not
        inflated by noise. Reuses the finding_patterns learning store."""
        try:
            from core.review.finding_patterns import FindingPatterns
            prefs = FindingPatterns().operator_preferences() or {}
        except Exception:
            return set()
        out = set()
        for cat, stats in prefs.items():
            try:
                ig, fx = int(stats.get("ignored", 0) or 0), int(stats.get("fixed", 0) or 0)
            except (TypeError, ValueError, AttributeError):
                continue
            if ig >= 3 and ig > fx:
                out.add(str(cat))
        return out

    def report_score(self, report: "ReviewReport", ignored_categories: "set | None" = None) -> float:
        """Overall score 0.0-5.0.

        Components:
        - Unresolved severity penalties: weighted by criticality of issues
        - Tool-evidence support, structural risk and established operator preferences
        - Clamp and round into the 0.0-5.0 range
        """
        if not report.findings:
            return 5.0

        # A finding from a category the operator has firmly taught PRT to ignore docks
        # far less — the learned "I don't care about X" signal reaches the SCORE, not just
        # the model's review context. Firm-signal-gated so it stays dormant on thin data.
        if ignored_categories is None:
            ignored_categories = self._strongly_ignored_categories()

        score = 5.0

        # Structural risk multiplier: when the graph says high-risk topology,
        # apply higher severity penalties so the score reflects architecture impact.
        structural_impact = getattr(report, "structural_impact", None) or {}
        risk_score = int(structural_impact.get("risk_score", 0) or 0)
        if risk_score >= 10:
            risk_mult = 1.5
        elif risk_score >= 5:
            risk_mult = 1.25
        else:
            risk_mult = 1.0

        severity_penalty = {"critical": 1.0, "major": 0.5, "minor": 0.1, "info": 0.05}
        for finding in report.findings:
            if finding.resolved:
                continue

            base = severity_penalty.get(finding.severity, 0.05)
            # Evidence-weighted: a finding backed by real tool evidence
            # (read_file/grep/callgraph/test_runner) penalizes at full weight; an
            # unverified model assertion is discounted so the score reflects
            # verified problems, not just the model's raw claim count.
            confidence = self.finding_confidence(finding)
            weight = 1.0 if confidence >= 0.8 else max(0.4, confidence)
            if str(getattr(finding, "category", "")) in ignored_categories:
                weight *= 0.25   # firmly-ignored category → docks a quarter, never zero
            score -= base * risk_mult * weight

        return max(0.0, min(5.0, round(score, 1)))

