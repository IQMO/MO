"""Deterministic feedback-to-learning extraction for MO.

Keeps self-improvement local and evidence-backed: explicit operator correction can
update profile learning, but normal chat does not create durable memory noise.
"""
from __future__ import annotations

import hashlib
from typing import Any


# Markers must signal the operator is correcting MO's behaviour — not just that a
# common word appears. Bare "feedback"/"stop"/"didn't" fire on normal technical chat
# (e.g. "the test didn't verify the audit, stop") and would silently auto-bake wrong
# learning, so they are intentionally excluded in favour of MO-directed phrases.
# Forward-looking rules ("from now on", "next time") are workflow signals handled by
# workflow_learning as confirm-gated candidates, not auto-applied here.
FEEDBACK_MARKERS = (
    "what did you learn",
    "you learned",
    "improve yourself",
    "self-improvement",
    "self improvement",
    "when corrected",
    "not what i asked",
    "you did not",
    "you didn't",
    "you keep",
    "i told you",
)


def extract_feedback_learning(user_text: str, assistant_text: str = "") -> dict[str, Any]:
    """Return profile-learning insights from explicit correction/feedback text.

    The output intentionally uses the existing Profile.append_profile_learning
    schema so there is one source of truth for durable operator learning.
    """
    text = str(user_text or "").strip()
    low = text.lower()
    if not text or not any(marker in low for marker in FEEDBACK_MARKERS):
        return {}

    communication: list[str] = []
    evolution: list[str] = []
    current_focus: list[str] = []
    core_traits: list[str] = []

    if "crazy" in low or "tone" in low or "language" in low or "terms" in low:
        communication.append("Preserve the operator's wording and intent without reframing it as irrational or over-polished AI language")
    if "what did you learn" in low or "you learned" in low or "self-improvement" in low or "self improvement" in low or "improve yourself" in low:
        evolution.append("Treat explicit correction as operational self-improvement: update method, tests, and behavior instead of only replying")
    if "feedback" in low:
        evolution.append("Route auditor/user feedback back into the same work lane until the original issue is actually fixed")
    if "audit" in low or "auditor" in low:
        current_focus.append("Auditing must enforce exact completion, evidence, and verified reality before approving done")
    if "test" in low or "verified" in low or "evidence" in low:
        core_traits.append("Evidence-first correction handling: verify files, logs, tests, and runtime before claiming completion")
    if "legacy" in low or "dirty" in low or "left behind" in low:
        core_traits.append("Finish cleanly with no abandoned legacy paths, duplicate mechanisms, or dirty follow-up work")

    insights: dict[str, Any] = {}
    if core_traits:
        insights["core_traits"] = _unique(core_traits)
    if current_focus:
        insights["current_focus"] = _unique(current_focus)
    if communication:
        insights["communication_style"] = _unique(communication)
    if evolution:
        insights["evolution"] = _unique(evolution)
    return insights


def record_feedback_learning(profile: Any, user_text: str, assistant_text: str = "") -> bool:
    """Append durable profile learning when explicit feedback is present."""
    insights = extract_feedback_learning(user_text, assistant_text)
    if not insights or not profile or not hasattr(profile, "append_profile_learning"):
        return False
    source = "feedback:" + hashlib.sha1(str(user_text or "").encode("utf-8", errors="ignore")).hexdigest()[:12]
    try:
        profile.append_profile_learning(source, insights)
        _bridge_to_finding_patterns(insights)
        return True
    except Exception:
        return False


def _bridge_to_finding_patterns(insights: dict[str, Any]) -> None:
    """Mirror clear feedback preferences into review finding patterns."""
    try:
        from ..review.finding_patterns import FindingPatterns
        patterns = FindingPatterns()
        if insights.get("communication_style"):
            patterns.record_meta_preference("style", "ignored")
        if insights.get("core_traits") or insights.get("current_focus"):
            patterns.record_meta_preference("evidence", "fixed")
    except Exception:
        return


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = " ".join(str(value or "").split())
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            out.append(clean)
    return out
