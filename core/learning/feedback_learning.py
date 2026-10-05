"""Deterministic feedback-to-learning extraction for MO.

Keeps self-improvement local and evidence-backed: explicit operator correction can
update profile learning, but normal chat does not create durable memory noise.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any


# Markers must signal the operator is correcting MO's behaviour — not just that a
# common word appears. Bare "feedback"/"stop"/"didn't" fire on normal technical chat
# (e.g. "the test didn't verify the audit, stop") and would silently auto-bake wrong
# learning, so they are intentionally excluded in favour of MO-directed phrases.
# Forward-looking rules ("from now on", "next time") are workflow signals handled by
# workflow_learning as confirm-gated candidates, not auto-applied here.
# Talking about MO's learning is not a correction by itself ("what did you learn
# today?" wrote two rules). It counts only alongside an instruction.
_LEARNING_TALK_MARKERS = (
    "what did you learn",
    "you learned",
    "improve yourself",
    "self-improvement",
    "self improvement",
)
_INSTRUCTION_RE = re.compile(
    r"(?:^|[.!?:;]\s*|\b(?:and|but|so)\s+)(?:stop|don'?t|do not|never|always|check|verify|test|"
    r"make sure|avoid|keep|remember|use|follow|leave|ask)\b"
    r"|\b(?:this|that|it)\s+is\s+(?:\S+\s+){0,2}feedback\b",
    re.IGNORECASE,
)
FEEDBACK_MARKERS = (
    "when corrected",
    "not what i asked",
    "you did not",
    "you didn't",
    "you keep",
    "i told you",
    "you were not asked",
    "you ignored",
    "you keep asking",
    "how many times",
)
_DIRECTED_CORRECTION_RE = re.compile(
    r"\b(?:you|mo)\b.{0,180}\b(?:did not|does not|failed to|fails to|keeps?|ignored?|assum(?:e|ed|ing)|"
    r"were not asked|not supposed|never (?:checks?|updates?|learns?|follows?))\b",
    re.IGNORECASE | re.DOTALL,
)
_DIRECTED_WRONG_REPORTING_RE = re.compile(
    r"\b(?:you|mo)\s+(?:(?:were|are|have\s+been|been)\s+wrong\b|"
    r"(?:reported|report)\b.{0,80}\bwrong\b|misreported\b)",
    re.IGNORECASE | re.DOTALL,
)
_DIRECT_METHOD_CORRECTION_RE = re.compile(
    r"^\s*(?:please\s+)?(?:do\s+not|don'?t|stop)\s+(?:assum\w*|rush\w*|guess\w*|"
    r"broaden\w*|substitut\w*|refram\w*|ask(?:ing)?\s+me\b|use\s+(?:a\s+)?fallback\b)",
    re.IGNORECASE,
)
_POSITIVE_FEEDBACK_RE = re.compile(
    r"\b(?:that|this|it)\s+(?:worked|works|is\s+(?:correct|right))\b|"
    r"\b(?:exactly\s+right|you\s+got\s+it\s+right|looks?\s+good|well\s+done|perfect)\b",
    re.IGNORECASE,
)
_QUALIFIED_FEEDBACK_RE = re.compile(r"\b(?:but|although|though|however|except|still)\b", re.IGNORECASE)


def _normalized_feedback_text(value: str) -> str:
    """Normalize a few common compact spellings without fuzzy-matching chat."""
    low = " ".join(str(value or "").lower().split())
    for old, new in (
        ("doesnot", "does not"),
        ("didnt", "did not"),
        ("dont", "do not"),
        ("selfimprovement", "self improvement"),
        ("selfimprovment", "self improvement"),
        ("personlizing", "personalizing"),
        ("suppsoed", "supposed"),
        ("implamanteitons", "implementations"),
        ("rootcuas", "root cause"),
        ("uncierercly", "unnecessarily"),
        ("atotumaticlly", "automatically"),
    ):
        low = low.replace(old, new)
    return low


def is_explicit_feedback(user_text: str) -> bool:
    """Return whether the operator is explicitly correcting MO's behavior."""
    low = _normalized_feedback_text(user_text)
    if not low:
        return False
    if any(marker in low for marker in FEEDBACK_MARKERS):
        return True
    if any(marker in low for marker in _LEARNING_TALK_MARKERS) and _INSTRUCTION_RE.search(low):
        return True
    if (_DIRECTED_CORRECTION_RE.search(low) or _DIRECTED_WRONG_REPORTING_RE.search(low)
            or _DIRECT_METHOD_CORRECTION_RE.search(low)):
        return True
    personalization = any(
        marker in low
        for marker in ("profile", "personaliz", "self improvement", "what it learned")
    )
    gap = any(
        marker in low
        for marker in ("not updated", "never updated", "stale", "not wired", "missing", "does not check", "does not learn")
    )
    return personalization and gap


def is_explicit_positive_feedback(user_text: str) -> bool:
    """Return only an unqualified operator signal that the prior result worked.

    Silence or a next instruction is not success evidence. Qualified praise such
    as "looks good, but..." is also not a clean outcome and stays uncounted.
    """
    low = _normalized_feedback_text(user_text)
    if not low or is_explicit_feedback(low) or _QUALIFIED_FEEDBACK_RE.search(low):
        return False
    return bool(_POSITIVE_FEEDBACK_RE.search(low))


def extract_feedback_learning(user_text: str, assistant_text: str = "") -> dict[str, Any]:
    """Return profile-learning insights from explicit correction/feedback text.

    The output intentionally uses the existing Profile.append_profile_learning
    schema so there is one source of truth for durable operator learning.
    """
    text = str(user_text or "").strip()
    low = _normalized_feedback_text(text)
    if not text or not is_explicit_feedback(text):
        return {}

    communication: list[str] = []
    evolution: list[str] = []
    current_focus: list[str] = []
    core_traits: list[str] = []

    if "crazy" in low or "tone" in low or "language" in low or "terms" in low:
        communication.append("Preserve the operator's wording and intent without reframing it as irrational or over-polished AI language")
    if any(marker in low for marker in ("concise", "brief", "short", "compact", "too long", "less words", "direct")):
        communication.append("Keep routine replies concise and direct unless the requested evidence or report needs more depth")
    self_improvement = any(
        marker in low
        for marker in (
            "what did you learn",
            "what it learned",
            "you learned",
            "self-improvement",
            "self improvement",
            "improve yourself",
            "profile",
            "personaliz",
        )
    )
    if self_improvement:
        evolution.append("Treat explicit correction as operational self-improvement: update method, tests, and behavior instead of only replying")
    if self_improvement and any(marker in low for marker in ("turn", "workflow", "profile", "learn", "update", "wired")):
        evolution.append("At the end of accepted turns, evaluate explicit operator corrections and verified workflow outcomes through the existing profile, learning, or skill owner")
    if "feedback" in low:
        evolution.append("Route auditor/user feedback back into the same work lane until the original issue is actually fixed")
    if "audit" in low or "auditor" in low:
        current_focus.append("Auditing must enforce exact completion, evidence, and verified reality before approving done")
    if "test" in low or "verified" in low or "evidence" in low or _DIRECTED_WRONG_REPORTING_RE.search(low):
        core_traits.append("Evidence-first correction handling: verify files, logs, tests, and runtime before claiming completion")
    if any(marker in low for marker in ("not what i asked", "not asked", "unrequested", "scope", "broaden", "do not add", "don't add", "stay on")):
        core_traits.append("Preserve the operator's requested scope and do not add unrelated work or mechanisms")
    if "assum" in low:
        core_traits.append("Do not assume causes or solutions; inspect current evidence and the existing owner before deciding or acting")
    if any(marker in low for marker in ("acceptance", "requirement", "how many times", "kept asking", "keep asking")):
        core_traits.append("Preserve the operator's exact acceptance criterion and never substitute a different interaction or call partial infrastructure work complete")
    if any(marker in low for marker in ("legacy", "dirty", "left behind", "duplicate", "dedup", "over-engineer", "overengineer")):
        core_traits.append("Finish cleanly with no abandoned legacy paths, duplicate mechanisms, or dirty follow-up work")
    if any(marker in low for marker in ("already implemented", "current codebase", "recent docs", "recent commits", "double check")):
        core_traits.append("Verify current source, tests, recent documentation, and recent commits before changing a mechanism; repair or deduplicate its existing owner instead of adding a parallel path")

    # Visual corrections belong to the same durable profile owner as other
    # feedback. They become private, approved visual preferences consumed by MO
    # Design, not a second prompt or a product-wide default.
    design_terms = (
        "design", "ui", "interface", "visual", "render", "premium", "quality",
        "radius", "rounded", "corner", "cloud", "glass", "codex", "icon",
    )
    # `edge` is an operator-defined trading term, so never classify it as UI
    # guidance on its own. It becomes design context only with visual language.
    design_context = any(term in low for term in design_terms) or any(
        phrase in low for phrase in ("rounded edge", "rounded edges", "border radius", "ui edge", "interface edge")
    )
    if design_context:
        if any(term in low for term in ("render", "premium", "quality")):
            core_traits.append("Visual preference: preserve a production-quality rendering bar with polished states and visual QA")
        if any(term in low for term in ("edge", "radius", "rounded", "corner", "cloud", "glass")):
            core_traits.append("Visual preference: use crisp, controlled rounded edges and a restrained radius scale; avoid cloudy, bubbly, or all-pill containers")
        if any(term in low for term in ("cloud", "glass", "codex", "generic")):
            core_traits.append("Visual preference: avoid stock glass and generic Cloud/Codex-like AI coding shells; keep the visual language authored")
        if not any(term in low for term in ("render", "premium", "quality", "edge", "radius", "rounded", "corner", "cloud", "glass", "codex", "generic")):
            core_traits.append("Visual preference: carry explicit operator visual preferences into future MO Design direction without copying profile prose into artifacts")

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
        result = profile.append_profile_learning(source, insights)
        return True if result is None else bool(result)
    except Exception as exc:
        try:
            from ..runtime.backend_monitor import get_monitor

            monitor = get_monitor()
            if monitor:
                monitor.emit("learning_write_error", {
                    "stage": "profile_feedback",
                    "error": type(exc).__name__,
                })
        except Exception:
            pass
        return False


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = " ".join(str(value or "").split())
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            out.append(clean)
    return out
