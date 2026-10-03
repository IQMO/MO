"""Minimal template selector for routing decisions.

select_template() is a deterministic helper used for routing (should we show
a taskboard, inject workspace context, etc.). It does NOT generate task rows.
"""
from __future__ import annotations

import re

from ..runtime.capability_routing import looks_like_computer_action_request
from ..runtime.work_signals import (
    looks_like_descriptive_implementation_question,
    normalize_work_action_words,
)
from ..utils.text_utils import words


# Only explicit source-adoption commands take the local fast path. Applying an
# existing workflow, mentioning one inside a task, or discussing adoption belongs
# to the provider. Template selection, dispatch and promotion share this boundary.
WORKFLOW_ADOPTION_RE = re.compile(
    r"\A\s*(?:please\s+)?(?:adopt|learn|save|stage)\s+"
    r"(?:[\w-]+[ \t]+)*?(?:workflow|skill)[ \t]*"
    r"(?::[\s\S]+|from\s+(?:`[^`]+`|\"[^\"]+\"|'[^']+'|\S+))\s*\Z",
    re.I,
)
WORKFLOW_APPROVAL_RE = re.compile(
    r"\A\s*(?:please\s+)?(?:approve|promote|activate)\s+"
    r"(?:(?:this|the|that|my)\s+)?(?:latest\s+)?(?:workflow|skill)"
    r"(?:[-\s]+(?:candidate|learning))?"
    r"(?::[a-z0-9_-]+|\s+(?:workflow|skill)-candidate:[a-z0-9_-]+|\s+latest)?[.!]?\s*\Z",
    re.I,
)


def is_workflow_control_request(text: str) -> bool:
    """Return True for workflow adoption/promotion control turns."""
    value = str(text or "")
    return bool(WORKFLOW_ADOPTION_RE.search(value) or WORKFLOW_APPROVAL_RE.search(value))


BUILD_TRIGGERS = {"build", "rebuild", "remake", "rework", "create", "implement", "add", "new", "make", "write", "design"}
REVIEW_TRIGGERS = {
    "review", "research", "investigate", "investigating", "audit", "analyze", "analyse",
    "inspect", "search", "scan", "find", "diagnose",
}
VERIFICATION_TRIGGERS = {"check", "confirm", "test", "validate", "verify"}
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


_REVIEW_WORD_RE = re.compile(r"\b(?:" + "|".join(sorted(REVIEW_TRIGGERS)) + r")\b", re.I)
# A review word inside a negated or contrastive clause is describing what was
# NOT done, not requesting a review: "not check and search", "instead of
# searching", "you never investigate". Distinct from _NEGATED_WORK_ACTION_CLAUSE_RE
# in turn_intent, which strips "do not edit/modify" scope clauses from real work.
# A negator anywhere earlier in a clause negates every review word after it, so
# the clause is the unit rather than a span reaching for adjacent triggers. A
# span-based rule only reached immediately adjacent runs and still admitted
# "do not review the auth code or audit the logs", where an object separates
# the verbs. Contractions need their own branch: there is no word boundary
# inside "didn't", so a bare n't alternative never matched it.
_NEGATOR_RE = re.compile(
    r"(?:\b\w*n['’]t\b|\b(?:not|never|no|dont|doesnt|didnt|without|instead\s+of|"
    r"rather\s+than|fail(?:s|ed|ing)?\s+to|refus(?:e|es|ed)\s+to|forgot\s+to|"
    r"skip(?:s|ped)?)\b)",
    re.I,
)


# Commas do not normally end semantic negation: objects may sit between
# coordinated review verbs ("do not review X, audit Y, or search Z"). An
# explicit request introducer after a comma does start a fresh directive.
_CLAUSE_BOUNDARY_RE = re.compile(
    r"[.?!;\n]+|,\s*(?=(?:please\b|(?:can|could|would|will|should)\s+you\b|"
    r"i\s+(?:need|want)\s+you\s+to\b))|\b(?:but|however|although|though)\b",
    re.I,
)

_VERIFICATION_DIRECTIVE_RE = re.compile(
    r"(?:^|[.?!;]\s+)"
    r"(?:(?:please|now)\s+|(?:can|could|would|will)\s+you\s+|"
    r"i\s+(?:want|need)\s+you\s+to\s+|(?:and|then)\s+)*"
    r"(?:check|confirm|test|validate|verify)\b",
    re.I,
)
_VERIFICATION_WORK_SCOPE_RE = re.compile(
    r"\b(?:all|everything|complete|completed|completion|done|finished|workflow|"
    r"implementation|behavior|behaviour|changes?|issues?|problems?|project|codebase|"
    r"source|files?|functions?|classes?|modules?|parser|tests?)\b",
    re.I,
)
_MUTATION_SCOPE_RE = re.compile(
    r"(?P<neg>\b(?:do\s+not|don['’]?t|dont|without)\b)\s+"
    r"(?:(?:make|making)\s+)?"
    r"(?:edits?|editing|edit|modifications?|modifying|modify|changes?|changing|change|"
    r"writes?|writing|write|creates?|creating|create|deletes?|deleting|delete|"
    r"touch(?:es|ing)?|alters?|altering|alter)\b"
    r"(?P<tail>[^.?!;\n]{0,120}?)(?=\b(?:" + "|".join(sorted(REVIEW_TRIGGERS)) + r")\b)",
    re.I,
)
_NO_MUTATION_SCOPE_RE = re.compile(
    r"(?P<neg>\bno\b)\s+(?:file\s+)?"
    r"(?:edits?|changes?|modifications?|writes?)\b"
    r"(?P<tail>[^.?!;\n]{0,120}?)(?=\b(?:" + "|".join(sorted(REVIEW_TRIGGERS)) + r")\b)",
    re.I,
)
_DIRECT_ACTION_LINK_RE = re.compile(r"\b(?:and|or|nor)\s*(?:please\s+)?$", re.I)
_COMMA_DIRECTIVE_TAIL_RE = re.compile(
    r"(?:\bplease\s*)?,\s*(?:(?:and|then|also)\s+)?(?:please\s+)?$",
    re.I,
)
_REQUEST_INTRO_TAIL_RE = re.compile(
    r"(?:\bplease|(?:can|could|would|will|should)\s+you|"
    r"i\s+(?:need|want)\s+you\s+to)\s*$",
    re.I,
)


def _neutralize_non_review_scope_negators(text: str) -> str:
    """Remove only negators that constrain mutation before a review request.

    ``without changing files, inspect X`` is a positive read-only review, while
    ``do not edit or review X`` negates both coordinated actions. Preserve the
    latter by keeping a direct conjunction when no comma separated the actions.
    """
    def replace(match: re.Match[str]) -> str:
        tail = str(match.group("tail") or "")
        if "," not in tail and _DIRECT_ACTION_LINK_RE.search(tail):
            return match.group(0)
        if not (
            _COMMA_DIRECTIVE_TAIL_RE.search(tail)
            or _REQUEST_INTRO_TAIL_RE.search(tail)
        ):
            return match.group(0)
        value = match.group(0)
        start = match.start("neg") - match.start()
        end = match.end("neg") - match.start()
        return value[:start] + (" " * (end - start)) + value[end:]

    scoped = _MUTATION_SCOPE_RE.sub(replace, str(text or ""))
    return _NO_MUTATION_SCOPE_RE.sub(replace, scoped)


def _review_request_is_directive(text: str) -> bool:
    """Whether a review word actually asks for a review.

    Template selection is a bag-of-words match, so a review word embedded in a
    complaint can otherwise seed a full evidence procedure accidentally. A
    review word counts only when at least one occurrence sits outside a negated
    clause, so a real review followed by a no-modification constraint still
    routes as a review.
    """
    for clause in _CLAUSE_BOUNDARY_RE.split(_neutralize_non_review_scope_negators(text)):
        if not clause or not clause.strip():
            continue
        negator = _NEGATOR_RE.search(clause)
        for match in _REVIEW_WORD_RE.finditer(clause):
            if negator is None or match.start() < negator.start():
                return True
    return False


def select_template(user_input: str) -> str:
    """Select a task template from user input. Returns template name."""
    if is_workflow_control_request(user_input):
        return "simple_chat"
    if looks_like_descriptive_implementation_question(user_input):
        return "simple_chat"
    text = normalize_work_action_words(user_input).lower()
    terms = words(text)
    if terms & PROBLEM_TRIGGERS or _DIAGNOSIS_RE.search(text):
        return "problem_solving"
    STRONG_BUILD_TRIGGERS = BUILD_TRIGGERS - {"new"}
    if terms & STRONG_BUILD_TRIGGERS:
        return "build_create"
    verification_directive = bool(
        terms & VERIFICATION_TRIGGERS
        and _VERIFICATION_DIRECTIVE_RE.search(text)
        and (
            terms & {"test", "validate"}
            or _VERIFICATION_WORK_SCOPE_RE.search(text)
        )
    )
    if verification_directive:
        return "deep_review"
    if terms & REVIEW_TRIGGERS and _review_request_is_directive(text):
        return "deep_review"
    # "New" alone must not turn an existing computer action into a build.
    # Explicit build, diagnosis and review requests already won above.
    if terms & BUILD_TRIGGERS and not looks_like_computer_action_request(text):
        return "build_create"
    return "simple_chat"
