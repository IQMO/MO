"""Minimal template selector for routing decisions.

select_template() is a deterministic helper used for routing (should we show
a taskboard, inject workspace context, etc.). It does NOT generate task rows.
"""
from __future__ import annotations

import re

from .text_utils import words


WORKFLOW_GATING_RE = re.compile(
    r"\b(?:adopt|learn|approve|promote|activate|use)\b.{0,80}\b(?:workflow|workflow-candidate|skill|style|process|method)\b"
    r"|\bworkflow-candidate:[a-f0-9]{8,40}\b",
    re.I | re.S,
)


def is_workflow_control_request(text: str) -> bool:
    """Return True for workflow adoption/promotion control turns."""
    return bool(WORKFLOW_GATING_RE.search(str(text or "")))


BUILD_TRIGGERS = {"build", "rebuild", "remake", "rework", "create", "implement", "add", "new", "make", "write", "design"}
REVIEW_TRIGGERS = {
    "review", "research", "investigate", "investigating", "audit", "analyze", "analyse", "deep",
    "inspect", "search", "scan", "find", "codebase", "repo", "repository", "entire", "entirely",
    "diff", "patch", "changes",
}
PROBLEM_TRIGGERS = {"fix", "debug", "solve", "bug", "problem", "broken"}
# Interrogative / investigative diagnosis of a failure or symptom is problem-solving,
# not chat — "figure out why X crashes", "look into the slow startup". Kept distinct
# from analytical review words (audit/investigate/diagnose) so pure audits still map
# to deep_review.
_DIAGNOSIS_RE = re.compile(
    r"\b(?:figure out|look into|find out|get to the bottom of|track down|trace down)\b"
    r"|\bwhy\b.{0,40}\b(?:fail(?:s|ing|ed)?|broken|errors?|crash(?:es|ing|ed)?|"
    r"hang(?:s|ing)?|slow|stuck|wrong|leak(?:s|ing)?|bug)\b",
    re.I,
)


def select_template(user_input: str) -> str:
    """Select a task template from user input. Returns template name."""
    if is_workflow_control_request(user_input):
        return "simple_chat"
    text = str(user_input or "").lower()
    terms = words(text)
    if terms & PROBLEM_TRIGGERS or _DIAGNOSIS_RE.search(text):
        return "problem_solving"
    STRONG_BUILD_TRIGGERS = BUILD_TRIGGERS - {"new"}
    if terms & STRONG_BUILD_TRIGGERS:
        return "build_create"
    if terms & REVIEW_TRIGGERS:
        return "deep_review"
    if terms & BUILD_TRIGGERS:
        return "build_create"
    return "simple_chat"
