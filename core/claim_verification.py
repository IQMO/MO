"""Verify-before-claiming detector (VS05 vs Fable 5, FB1).

Fable 5 forces a tool-check before asserting current-state/version facts; MO only
says so in prose (system.md). This module gives MO a *runtime, observable* signal:
when a turn asserts a stale-prone fact (a version, "the latest", or a
knowledge-cutoff hedge) while using NO verifying tools, emit a signal so the
behavior is visible to traces / DEVMODE05 self-diagnosis.

Deliberately conservative — high-precision patterns only — so ordinary coding
answers are never flagged. This is an observability signal, not a hard gate; a
visible note or a forced verify-continuation is a separate, opt-in escalation.
"""
from __future__ import annotations

import re

# Tools that constitute "I checked something this turn" — any read/search/web
# pull counts as verification evidence for a current-state claim.
VERIFYING_TOOLS = frozenset({
    "read_file", "grep", "find_files", "code_search",
    "find_callers", "find_callees", "web_fetch", "web_snapshot",
})

# High-precision stale-prone claim patterns. Each targets an assertion that is
# specifically about *current external state* — the class that goes stale and
# that recall gets wrong. Bare version numbers are intentionally NOT matched
# (too noisy); only versions asserted as current/latest, plus cutoff hedges.
_CLAIM_PATTERNS = (
    (r"\b(?:the\s+)?(?:latest|newest|current|most\s+recent)\s+(?:stable\s+)?(?:version|release)\b", "latest-version claim"),
    (r"\bas\s+of\s+(?:my\s+)?(?:knowledge|training|last\s+update|the\s+knowledge\s+cutoff)\b", "knowledge-cutoff hedge"),
    (r"\bas\s+of\s+(?:early\s+|mid\s+|late\s+)?\d{4}\b", "as-of-year claim"),
    (r"\bcurrent(?:ly)?\s+(?:on\s+)?version\s+v?\d", "current-version claim"),
    (r"\bversion\s+v?\d+\.\d+(?:\.\d+)?\s+is\s+(?:the\s+)?(?:latest|current|newest|most\s+recent)\b", "version-is-latest claim"),
)
_COMPILED = tuple((re.compile(p, re.IGNORECASE), label) for p, label in _CLAIM_PATTERNS)


def used_verifying_tools(tool_call_counts: dict | None) -> bool:
    """True if the turn used at least one read/search/web tool."""
    if not tool_call_counts:
        return False
    return any(tool_call_counts.get(name) for name in VERIFYING_TOOLS)


# Tools that verify a *completion/state* claim ("clean", "done", "tests pass",
# "synced"). Broader than the current-state set: running tests, a shell check
# (pytest/git/grep), and git_status are all valid completion evidence, on top of
# the read/search/web set. Editing is NOT verification (mirrors the FB1 rule).
COMPLETION_VERIFYING_TOOLS = VERIFYING_TOOLS | frozenset({
    "test_runner", "shell", "git_status",
})

# High-precision completion/cleanliness assertions — the class of claim the
# operator keeps catching MO making from assumption ("it's clean", "all pass",
# "no issues") without actually checking this turn. Bare "done"/"fixed" are
# intentionally NOT matched (far too noisy in ordinary coding chat); only
# confident state/verification assertions are.
_COMPLETION_PATTERNS = (
    (r"\b(?:it'?s|that'?s|everything'?s|all|we'?re|now)\s+(?:now\s+|all\s+)?clean\b", "clean claim"),
    (r"\b(?:verified|confirmed|looks?)\s+clean\b", "verified-clean claim"),
    (r"\ball\s+(?:tests?|checks?|suites?)\s+(?:pass|passing|green|succeed)\b", "tests-pass claim"),
    (r"\b(?:the\s+)?(?:suite|tests?)\s+(?:is|are)\s+green\b", "tests-green claim"),
    (r"\bno\s+(?:findings|issues|errors|problems|regressions|failures|leaks)\b", "no-issues claim"),
    (r"\b(?:fully\s+synced|in\s+sync|all\s+synced)\b", "synced claim"),
    (r"\b(?:everything|it\s+all)\s+(?:works|passes|checks\s+out)\b", "all-works claim"),
)
_COMPLETION_COMPILED = tuple((re.compile(p, re.IGNORECASE), label) for p, label in _COMPLETION_PATTERNS)


def used_completion_verifying_tools(tool_call_counts: dict | None) -> bool:
    """True if the turn used at least one tool that can substantiate a completion
    /cleanliness claim (read/search/web OR test_runner/shell/git_status)."""
    if not tool_call_counts:
        return False
    return any(tool_call_counts.get(name) for name in COMPLETION_VERIFYING_TOOLS)


def detect_completion_claim(text: str) -> str | None:
    """Return a short label if the text confidently asserts clean/passing/synced
    /no-issues state, else None. High-precision by design."""
    body = str(text or "")
    if not body.strip():
        return None
    for pattern, label in _COMPLETION_COMPILED:
        if pattern.search(body):
            return label
    return None


def unverified_completion_claim_signal(text: str, tool_call_counts: dict | None) -> str | None:
    """Return a label when the answer asserts a completion/cleanliness fact AND
    the turn used no completion-verifying tools; else None.

    This is the runtime backing for "verify before claiming clean/done": a
    confident clean/pass/synced/no-issues assertion shipped with zero checks this
    turn is the flaggable case the operator keeps having to re-correct by hand.
    """
    if used_completion_verifying_tools(tool_call_counts):
        return None
    return detect_completion_claim(text)


def detect_unverified_current_state_claim(text: str) -> str | None:
    """Return a short label if the text asserts a stale-prone current-state fact,
    else None. Conservative by design — see module docstring."""
    body = str(text or "")
    if not body.strip():
        return None
    for pattern, label in _COMPILED:
        if pattern.search(body):
            return label
    return None


def unverified_claim_signal(text: str, tool_call_counts: dict | None) -> str | None:
    """Return a claim label when the answer makes a stale-prone current-state
    claim AND the turn used no verifying tools; else None.

    This is the single decision FB1 enforces: a current-state claim with zero
    verification this turn is the flaggable case.
    """
    if used_verifying_tools(tool_call_counts):
        return None
    return detect_unverified_current_state_claim(text)
