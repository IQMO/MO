"""Verify-before-claiming detectors.

MO's system prompt requires evidence before asserting stale-prone current facts,
completion/cleanliness, or finalizing a short announcement that evidence work
will happen later. This module gives MO runtime-observable signals for those
claim classes:

- stale-prone current-state/version assertions such as "latest version";
- completion/cleanliness assertions such as "all tests pass" or "no issues";
- unfulfilled first-person evidence-work promises such as "I'll inspect it."

When one appears without the required successful tool evidence, the agent turn
loop can force one bounded verify-or-soften continuation.

Deliberately conservative — high-precision patterns only — so ordinary coding
answers are never flagged.
"""
from __future__ import annotations

import re

from ..runtime.capability_routing import capability_hints_for
from ..tasking.results import (
    TaskTransitionResult,
    tool_execution_records,
)

# Tools that constitute "I checked something this turn" — any read/search/web
# pull counts as verification evidence for a current-state claim.
VERIFYING_TOOLS = frozenset({
    "read_file", "grep", "find_files", "code_search",
    "find_callers", "find_callees", "web_fetch", "web_search",
    "inspect_repo", "use_repo",
})
PROFILE_VERIFYING_TOOLS = frozenset({"read_file", "grep", "find_files"})

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
    (r"\b(?:not\s+)?(?:clearly\s+)?documented\b", "documentation-support claim"),
    (r"\b(?:docs?|documentation|readme)\s+(?:(?:do|does|did)\s+not|don'?t|doesn'?t|didn'?t|is|are|was|were)?\s*(?:not\s+)?(?:mentions?|documents?|says?|states?|lists?|includes?)\b", "documentation-support claim"),
    (r"\b(?:reads?|loads?|supports?|recognizes?)\s+`?[A-Z][A-Z0-9_.-]+\.md`?\b", "tool-support claim"),
    (r"\b(?:don'?t|do\s+not)\s+have\b[^.\n]{0,100}\b(?:in|inside)\s+(?:your\s+|the\s+)?(?:profile|\.mo|mo\s+home|memory)\b", "profile-absence claim"),
    (r"\bno\s+(?:profile\s+)?(?:entries|terms|project\s+configs?|memory\s+files)\b", "profile-absence claim"),
    (r"\b(?:checked|read|searched|looked\s+(?:in|through))\b[^.\n]{0,120}\b(?:profile|\.mo|mo\s+home)\b[^.\n]{0,160}\b(?:not|no|aren'?t|isn'?t)\b", "profile-absence claim"),
)
_COMPILED = tuple((re.compile(p, re.IGNORECASE), label) for p, label in _CLAIM_PATTERNS)

# A provider request receives a routed working catalog. Treating that subset as
# proof that MO lacks a tool/connection is a category error: ``tool_search`` or
# the owning status route must be tried first. This detector targets the exact
# turn-relative denial shape without rejecting evidence-backed reports such as
# ``phone_capabilities returned unavailable: phone locked``.
_TURN_RELATIVE_CATALOG_RE = re.compile(
    r"\b(?:this|that|the\s+current)\s+(?:turn|request|session)\b",
    re.IGNORECASE,
)
_CATALOG_UNAVAILABILITY_RE = re.compile(
    r"\b(?:can(?:not|'t)|could(?:not|n't)|unable\s+to|"
    r"do(?:\s+not|n't)\s+have|(?:is|are|was|were)(?:\s+not|n't)\s+available|"
    r"unavailable|not\s+exposed)\b",
    re.IGNORECASE,
)
_ROUTE_NOUN_RE = re.compile(
    r"\b(?:tools?|capabilit(?:y|ies)|catalog|schemas?|query|access|connection|"
    r"connectors?|mcp|server|account)\b",
    re.IGNORECASE,
)
_FIRST_PERSON_NO_HAVE_RE = re.compile(
    r"\b(?:i|we)\s+(?:do\s+not|don?'t)\s+have\b",
    re.IGNORECASE,
)
_ACTION_PROMISE_RE = re.compile(
    r"\b(?:i(?:['’]ll|\s+will)|i(?:['’]m|\s+am)\s+going\s+to|let\s+me)\s+"
    r"(?:(?:quickly|first|now|next)\s+)?"
    r"(?:inspect|investigate|check|verify|fetch|browse|search|look\s+up|"
    r"read(?!\s+(?:this|that)\s+as\b)|review|analy[sz]e|"
    r"run(?!\s+(?:through|over)\b)|test|open(?!\s+(?:with|by)\b))\b",
    re.IGNORECASE,
)
_CONDITIONAL_PROMISE_RE = re.compile(
    r"\b(?:if|when)\s+you\b|\bif\s+(?:needed|required|useful|helpful)\b",
    re.IGNORECASE,
)
_NON_EXECUTION_TOOL_NAMES = frozenset({"tool_search", "set_plan", "complete_task"})


def unfulfilled_action_promise_signal(
    user_input: str,
    text: str,
    tool_call_counts: dict | None = None,
) -> str | None:
    """Detect a short future-action placeholder that finalized before doing work."""
    body = str(text or "").strip()
    if not body or len(body.split()) > 55 or not capability_hints_for(user_input):
        return None
    if any(
        count
        for name, count in (tool_call_counts or {}).items()
        if name not in _NON_EXECUTION_TOOL_NAMES and not str(name).startswith("_background_")
    ):
        return None
    match = _ACTION_PROMISE_RE.search(body)
    if not match or _CONDITIONAL_PROMISE_RE.search(body[match.start():]):
        return None
    return "future-action promise without execution"


def turn_relative_tool_unavailability_signal(
    text: str,
    tool_call_counts: dict | None = None,
) -> str | None:
    """Reject catalog-relative capability denials regardless of tool counts.

    An actual route rejection may establish a concrete blocker, but it must be
    reported as that rejection rather than as something MO lacks for one turn.
    """
    del tool_call_counts
    body = str(text or "").replace("’", "'").replace("‘", "'")
    if not _TURN_RELATIVE_CATALOG_RE.search(body):
        return None
    if not _CATALOG_UNAVAILABILITY_RE.search(body):
        return None
    if not (_ROUTE_NOUN_RE.search(body) or _FIRST_PERSON_NO_HAVE_RE.search(body)):
        return None
    return "turn-relative tool-unavailability claim"


def used_verifying_tools(tool_call_counts: dict | None, *, tools: frozenset[str] | None = None) -> bool:
    """True if the turn used at least one read/search/web tool."""
    if not tool_call_counts:
        return False
    return any(tool_call_counts.get(name) for name in tools or VERIFYING_TOOLS)


# Tools that verify a *completion/state* claim ("clean", "done", "tests pass",
# "synced"). Broader than the current-state set: running tests, a shell check
# (pytest/git/grep), and git_status are all valid completion evidence, on top of
# the read/search/web set. Editing is NOT verification (mirrors the FB1 rule).
COMPLETION_VERIFYING_TOOLS = VERIFYING_TOOLS | frozenset({
    "test_runner", "shell", "git_status:status",
})
_BACKGROUND_COMPLETION_TOOL_KEYS = {
    "shell": "_background_shell",
    "test_runner": "_background_test_runner",
}

# High-precision completion/cleanliness assertions — the class of claim the
# operator keeps catching MO making from assumption ("it's clean", "all pass",
# "no issues") without actually checking this turn. Bare "done"/"fixed" are
# intentionally NOT matched (far too noisy in ordinary coding chat); only
# confident state/verification assertions are.
_COMPLETION_PATTERNS = (
    # Strong claims come first so a broad "clean/no issues" match cannot hide
    # the specific evidence the answer actually needs.
    (r"\ball\s+(?:tests?|checks?|suites?)\s+(?:pass|passing|green|succeed)\b", "tests-pass claim"),
    (r"\b(?:the\s+)?(?:suite|tests?)\s+(?:is|are)\s+green\b", "tests-green claim"),
    (r"\bno\s+(?:new\s+)?regressions\b", "regression-free claim"),
    (r"\bno\s+(?:dead|unused|unreachable)\s+code\b", "dead-code claim"),
    (r"\b(?:fully\s+synced|in\s+sync|all\s+synced)\b", "synced claim"),
    (r"\b(?:source|repo(?:sitory)?|checkout|worktree)\s+(?:is|looks?|appears?)\s+(?:fully\s+)?clean\b", "clean claim"),
    (r"\b(?:it'?s|that'?s|everything'?s|all|we'?re|now)\s+(?:now\s+|all\s+)?clean\b", "clean claim"),
    (r"\b(?:verified|confirmed|looks?)\s+clean\b", "verified-clean claim"),
    (r"\bno\s+(?:findings|issues|errors|problems|failures|leaks)\b(?![\"'`]*\s+claim\b)", "no-issues claim"),
    (r"\b(?:everything|it\s+all)\s+(?:works|passes|checks\s+out)\b", "all-works claim"),
)
_COMPLETION_COMPILED = tuple((re.compile(p, re.IGNORECASE), label) for p, label in _COMPLETION_PATTERNS)
_COMPLETION_REQUIRED_TOOLS = {
    "tests-pass claim": frozenset({"test_runner", "shell"}),
    "tests-green claim": frozenset({"test_runner", "shell"}),
    "regression-free claim": frozenset({"test_runner", "shell"}),
    "dead-code claim": frozenset({"code_search", "find_callers", "find_callees", "shell"}),
    "synced claim": frozenset({"git_status:status", "shell"}),
}
_MATERIAL_COMPLETION_PATTERNS = (
    (r"\b(?:done|fixed|ready|complete|completed|finished)\b", "material-work completion claim"),
)
_MATERIAL_COMPLETION_COMPILED = tuple((re.compile(p, re.IGNORECASE), label) for p, label in _MATERIAL_COMPLETION_PATTERNS)

_TEST_EXECUTION_CLAIM_RE = re.compile(
    r"\b(?:i|we|i['’]?ve|we['’]?ve)\s+(?:have\s+)?"
    r"(?:ran|run|executed)\b[^.\n;]{0,45}\b(?:tests?|pytest|suite|checks?)\b|"
    r"\b(?:tests?|pytest|suite|checks?)\b[^.\n;]{0,45}\b"
    r"(?:i|we)\s+(?:have\s+)?(?:ran|run|executed)\b",
    re.I,
)
_NEGATED_TEST_EXECUTION_CLAIM_RE = re.compile(
    r"\b(?:did\s+not|didn['’]?t|have\s+not|haven['’]?t|has\s+not|hasn['’]?t|"
    r"was\s+not|wasn['’]?t|were\s+not|weren['’]?t|not|never|without|no)\b"
    r"[^.\n;]{0,55}\b(?:run|ran|executed|passed|passing|green|tests?|pytest|suite|checks?)\b",
    re.I,
)
_FILE_CHANGE_CLAIM_RE = re.compile(
    r"\b(?:i|we|i['’]?ve|we['’]?ve)\s+(?:have\s+)?"
    r"(?:changed|updated|edited|wrote|written|created|modified)\b[^.\n;]{0,80}"
    r"\b(?:files?|source|code|modules?|configs?|documents?|tests?|readme)\b|"
    r"\b(?:files?|source|code|modules?|configs?|documents?|tests?|readme)\b"
    r"[^.\n;]{0,45}\b(?:was|were|has\s+been|have\s+been)\s+"
    r"(?:changed|updated|edited|written|created|modified)\b|"
    r"\b(?:changes|edits|updates)\s+(?:were\s+)?(?:made|applied)\s+(?:to|in)\b",
    re.I,
)
_NEGATED_FILE_CHANGE_CLAIM_RE = re.compile(
    r"\b(?:did\s+not|didn['’]?t|have\s+not|haven['’]?t|has\s+not|hasn['’]?t|"
    r"was\s+not|wasn['’]?t|were\s+not|weren['’]?t|not|never|without|no)\b"
    r"[^.\n;]{0,70}\b(?:changed|updated|edited|wrote|written|created|modified|"
    r"changes|edits|updates|files?|source|code|modules?|configs?|documents?|tests?|readme)\b",
    re.I,
)
_TASK_COMPLETED_CLAIM_RE = re.compile(
    r"\b(?:task|step|plan row)\s+[A-Za-z0-9_.-]+\b[^.\n]{0,45}"
    r"\b(?:complete|completed|finished|done)\b",
    re.I,
)
_FINAL_PHASE_BLOCK_CLAIM_RE = re.compile(
    r"\b(?:blocked|could(?:n['’]?t|\s+not)|unable)\b[^.\n]{0,120}"
    r"\b(?:final|report)(?:[- ]phase|\s+row)\b|"
    r"\b(?:final|report)(?:[- ]phase|\s+row)\b[^.\n]{0,120}\bblocked\b",
    re.I,
)
_SCREEN_OBSERVATION_DENIAL_RE = re.compile(
    r"\b(?:i|we)\s+(?:did\s+not|didn['’]?t|never)\s+"
    r"(?:view|capture|inspect|access|observe|see|look\s+at)\b"
    r"[^.\n;]{0,55}\bscreen\b",
    re.I,
)
_DELIVERY_CLAIM_PATTERNS = {
    "commit": re.compile(
        r"\b(?:i|we|i['’]?ve|we['’]?ve)\s+(?:have\s+)?committed\b|"
        r"\bcommit\b[^.\n;]{0,45}\b(?:complete|completed|done|successful)\b",
        re.I,
    ),
    "push": re.compile(
        r"\b(?:i|we|i['’]?ve|we['’]?ve)\s+(?:have\s+)?pushed\b|"
        r"\bpush\b[^.\n;]{0,45}\b(?:complete|completed|done|successful)\b",
        re.I,
    ),
    "deploy": re.compile(
        r"\b(?:i|we|i['’]?ve|we['’]?ve)\s+(?:have\s+)?(?:deployed|published)\b|"
        r"\b(?:deploy|publish)\b[^.\n;]{0,45}\b(?:complete|completed|done|successful)\b",
        re.I,
    ),
}
_NEGATED_DELIVERY_CLAIM_RE = re.compile(
    r"\b(?:did\s+not|didn['’]?t|not|never|failed|blocked|pending|remaining|skipped|without|no)\b",
    re.I,
)


def _has_unnegated_claim(
    text: str,
    claim_pattern: re.Pattern[str],
    negated_pattern: re.Pattern[str],
) -> bool:
    """Match an asserted execution fact, never an explicit non-action report.

    Claim correction is an expensive provider continuation. Split at ordinary
    sentence/clause boundaries and fail open on a negated clause so reports such
    as ``No focused tests were run`` or ``No source changes were made`` can end
    normally instead of reopening tools.
    """
    for clause in re.split(r"(?<=[.!?])\s+|[\r\n;]+", str(text or "")):
        if claim_pattern.search(clause) and not negated_pattern.search(clause):
            return True
    return False


def structured_execution_claim_gap(
    text: str,
    tool_sequence: object,
    task_transitions: object = None,
    *,
    turn_modified_files: object = None,
    profile_delivery_claims: object = None,
) -> str | None:
    """Reconcile high-risk factual claims with ordered typed runtime truth."""
    body = str(text or "")
    records = tool_execution_records(tool_sequence)
    transitions = [
        item for item in (task_transitions or [])
        if isinstance(item, TaskTransitionResult)
    ]
    claimed_delivery = {
        action
        for action, pattern in _DELIVERY_CLAIM_PATTERNS.items()
        if any(
            pattern.search(clause) and not _NEGATED_DELIVERY_CLAIM_RE.search(clause)
            for clause in re.split(r"(?<=[.!?])\s+|[\r\n;]+", body)
        )
    }
    claimed_delivery.update(
        str(action or "").strip().lower()
        for action in (profile_delivery_claims or ())
        if str(action or "").strip().lower() in _DELIVERY_CLAIM_PATTERNS
    )
    observed_delivery = {
        action
        for record in records
        if record.successful and record.tool == "shell"
        for action in str(record.action or "").split(",")
        if action
    }
    missing_delivery = sorted(claimed_delivery - observed_delivery)
    if missing_delivery:
        return "delivery-completed claim lacks " + ", ".join(missing_delivery)
    if _SCREEN_OBSERVATION_DENIAL_RE.search(body) and any(
        record.successful
        and any(
            event.get("event") == "observation"
            and event.get("target_kind") == "screen"
            for event in record.computer_events
        )
        for record in records
    ):
        return "screen-observation denial"
    if _has_unnegated_claim(
        body,
        _TEST_EXECUTION_CLAIM_RE,
        _NEGATED_TEST_EXECUTION_CLAIM_RE,
    ) and not any(
        record.successful and record.verification
        for record in records
    ):
        return "tests-ran claim"
    if _has_unnegated_claim(
        body,
        _FILE_CHANGE_CLAIM_RE,
        _NEGATED_FILE_CHANGE_CLAIM_RE,
    ) and not (
        bool(turn_modified_files)
        or any(
            record.successful and record.tool in {"write_file", "edit_file"}
            for record in records
        )
    ):
        return "file-changed claim"
    if _TASK_COMPLETED_CLAIM_RE.search(body) and not any(
        transition.ok and transition.operation in {"complete_task", "finalize_task"}
        for transition in transitions
    ):
        return "task-completed claim"
    if _FINAL_PHASE_BLOCK_CLAIM_RE.search(body) and not any(
        record.blocked
        and (
            record.phase_before in {"report", "final"}
            or "final" in record.block_reason.casefold()
            or "report" in record.block_reason.casefold()
        )
        for record in records
    ):
        return "task-phase chronology claim"
    return None


def used_completion_verifying_tools(
    tool_call_counts: dict | None,
    *,
    tools: frozenset[str] | None = None,
) -> bool:
    """True if the turn used at least one tool that can substantiate a completion
    /cleanliness claim (read/search/web OR test_runner/shell/git_status)."""
    if not tool_call_counts:
        return False
    for name in tools or COMPLETION_VERIFYING_TOOLS:
        count = int(tool_call_counts.get(name) or 0)
        if not count:
            continue
        background_count = int(tool_call_counts.get(_BACKGROUND_COMPLETION_TOOL_KEYS.get(name, "")) or 0)
        if count - background_count > 0:
            return True
    return False


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


def detect_material_work_completion_claim(text: str) -> str | None:
    """Return a label for bare completion words that are only risky on material
    work turns. This deliberately stays separate from detect_completion_claim so
    ordinary chat like "done reading" does not trip global gates."""
    body = str(text or "")
    if not body.strip():
        return None
    for pattern, label in _MATERIAL_COMPLETION_COMPILED:
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
    label = detect_completion_claim(text)
    if not label:
        return None
    required_tools = _COMPLETION_REQUIRED_TOOLS.get(label)
    if used_completion_verifying_tools(tool_call_counts, tools=required_tools):
        return None
    return label


def unverified_material_work_completion_claim_signal(
    text: str,
    tool_call_counts: dict | None,
    *,
    material_work: bool = False,
) -> str | None:
    """Return a label when a material work turn claims bare completion without a
    verifying tool. This tightens "done/fixed" only where it matters: edited
    files or taskboard-backed work."""
    if not material_work:
        return None
    # Creating a requested file is itself observable completion evidence: the
    # write tool only returns after the exact payload is persisted. Keep edits
    # excluded, and keep stricter clean/tests/no-regressions claims on the
    # completion gate above.
    if int((tool_call_counts or {}).get("write_file") or 0) > 0:
        return None
    if used_completion_verifying_tools(tool_call_counts):
        return None
    return detect_material_work_completion_claim(text)


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
    label = detect_unverified_current_state_claim(text)
    if not label:
        return None
    required_tools = PROFILE_VERIFYING_TOOLS if label == "profile-absence claim" else None
    if used_verifying_tools(tool_call_counts, tools=required_tools):
        return None
    return label


# Tools that expose external web evidence. Kept distinct from VERIFYING_TOOLS so
# the source-naming kernel stays complementary to unverified_claim_signal: that
# gate fires when NOTHING was read this turn; this one fires when the turn DID
# use web evidence but the answer names no source.
EXTERNAL_PULL_TOOLS = frozenset({
    "web_fetch", "web_search", "inspect_repo", "use_repo",
})

# A plainly-named source: any URL or www host in the answer text counts as "cited".
_URL_RE = re.compile(r"https?://|\bwww\.", re.IGNORECASE)


def used_external_pull_tools(tool_call_counts: dict | None) -> bool:
    """True if the turn used external web evidence."""
    if not tool_call_counts:
        return False
    return any(tool_call_counts.get(name) for name in EXTERNAL_PULL_TOOLS)


def unsourced_external_claim_signal(text: str, tool_call_counts: dict | None) -> str | None:
    """Return a claim label when the turn used external web evidence AND the answer
    asserts a current-state fact but names no source (no URL); else None.

    The MO-native source-naming kernel: if MO searched/fetched the web and then
    states a current/latest fact, the wording should name the source it used.
    Conservative by design — requires external web evidence, a stale-prone claim, and zero URL token
    in the answer — so ordinary synthesis that already links its source never fires.
    """
    if not used_external_pull_tools(tool_call_counts):
        return None
    label = detect_unverified_current_state_claim(text)
    if not label:
        return None
    required_tools = PROFILE_VERIFYING_TOOLS if label == "profile-absence claim" else None
    if required_tools and used_verifying_tools(tool_call_counts, tools=required_tools):
        # Unrelated web activity does not require a URL for local profile evidence.
        return None
    if _URL_RE.search(str(text or "")):
        return None
    return label
