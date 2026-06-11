"""
MO's own evidence-weighted scorer.

Every score point is earned through tool evidence.
No provider calls in scoring — pure deterministic math.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from core.tasking.task_evidence import evidence_item_is_tool_backed

if TYPE_CHECKING:
    from core.review.diff_review import ReviewReport, ReviewFinding


class ReviewScorer:
    """Scores findings and reports using MO's evidence system."""
    
    WEIGHTS = {
        "critical": 1.0,
        "major": 0.7,
        "minor": 0.3,
        "info": 0.1,
    }
    
    def finding_confidence(self, finding: "ReviewFinding") -> float:
        """Confidence in a single finding (0.0-1.0).
        
        Requires tool-backed evidence for full confidence.
        """
        if not finding.evidence_tools:
            return 0.2  # low confidence if no evidence
            
        tool_backed_count = sum(1 for e in finding.evidence_tools if evidence_item_is_tool_backed(e))
        
        if tool_backed_count == 0:
            return 0.4
        elif tool_backed_count == 1:
            return 0.8
        else:
            return 1.0
    
    def report_score(self, report: "ReviewReport") -> float:
        """Overall score 0.0-5.0.
        
        Components:
        - Unresolved severity penalties: weighted by criticality of issues
        - Token efficiency: small compression-savings bonus
        - Clamp and round into the 0.0-5.0 range
        """
        if not report.findings:
            return 5.0

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

        efficiency_bonus = 0.0
        if "compression_saved" in report.token_usage and "total_tokens" in report.token_usage:
            saved = report.token_usage["compression_saved"]
            total = report.token_usage["total_tokens"]
            if total > 0:
                efficiency_bonus = min(1.0, saved / total)

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
            score -= base * risk_mult * weight

        # Bonus from efficiency
        if efficiency_bonus > 0:
            score += 0.1

        return max(0.0, min(5.0, round(score, 1)))

