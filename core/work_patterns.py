"""Compact internal work patterns for MO.

This is not a public learning surface. It is a small zero-token selector that gives
Ghost and MO a shared build/design/fix contract. The design/build branch
consumes the compact internal MO Agent DNA contract; it does not expose a
public skill/plugin surface.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .agent.agent_dna import build_dna_context, build_lean_build_context, build_prd_context
from .gateway_helpers import select_template, words


_BUILD_WORDS = {"build", "rebuild", "remake", "rework", "create", "implement", "make", "write", "add", "new", "simulate", "simulation"}
_DESIGN_ACTION_WORDS = {"design"}
_FIX_WORDS = {"fix", "debug", "repair", "solve", "broken", "bug"}
_REVIEW_REPAIR_WORDS = {"review", "audit", "scan", "inspect", "find", "test", "entire", "all", "folder", "games"}
_DESIGN_WORDS = {
    "ui", "ux", "visual", "visuals", "page", "html", "css", "canvas", "animation",
    "interface", "frontend", "front-end", "website", "site", "landing", "dashboard",
    "component", "game", "screen", "layout", "theme", "motion", "aesthetic", "dna",
}
_REFERENCE_WORDS = {"reference", "clone", "copy", "mimic", "recreate", "replicate", "similar", "inspired"}
_COMPLEX_WORDS = {"entire", "full", "production", "architecture", "multi", "system", "complex", "browser", "responsive"}
_COMPLEX_SURFACE_WORDS = {
    "codebase", "repo", "repository", "session", "logs", "performance", "goal", "taskboard",
    "taskboarding", "auditor", "worker", "workers", "pr", "complexity", "rating", "profile",
    "personalization", "memory", "token", "tokens", "compression", "gateway", "ghost",
}
_FAILURE_QUESTION_RE = re.compile(r"\b(?:why|what)\b.*\b(?:wrong|fail(?:ing|ed)?|broken|error|bug)\b", re.I)
_PRD_REQUEST_RE = re.compile(r"\bprd\b|\bproduct\s+requirements?\b|\brequirements?\s+(?:doc|document|brief|spec)\b|\bbuild\s+brief\b", re.I)
_PRD_CHAT_PREFIX_RE = re.compile(r"^\s*(?:what\s+is|what's|explain|tell\s+me\s+about|define)\b", re.I)
_RESEARCH_METHOD_RE = re.compile(
    r"\b(?:how|what|explain|describe)\b.{0,80}\b(?:research|investigate|understand|analy[sz]e|study|map)\b.{0,100}\b(?:codebase|repo|repository|project)\b"
    r"|\b(?:research|investigate|understand|analy[sz]e|study|map)\b.{0,80}\b(?:codebase|repo|repository|project)\b.{0,80}\b(?:how|method|approach|process)\b",
    re.I | re.S,
)
# Universalized self-maintenance mindset (adaptive, never gated): project-audit
# and reference-comparison requests get distilled discipline as orientation.
_PROJECT_AUDIT_RE = re.compile(
    r"\b(?:find|expose|hunt|diagnose|audit|uncover|detect|check)\b.{0,60}\b(?:issues?|problems?|bugs?|weak(?:ness(?:es)?)?|inconsistenc(?:y|ies)|smells?|health|drift)\b"
    r".{0,80}\b(?:project|codebase|repo|repository|code|app|system|setup|here)\b"
    r"|\b(?:find|expose|hunt|diagnose|audit|uncover|detect|check)\b.{0,60}\b(?:project|codebase|repo|repository|code|app)\b"
    r".{0,60}\b(?:issues?|problems?|bugs?|weak(?:ness(?:es)?)?|inconsistenc(?:y|ies)|smells?|health|drift)\b"
    r"|\b(?:health.?check|full\s+audit|deep\s+audit)\b.{0,60}\b(?:project|codebase|repo|repository)\b",
    re.I | re.S,
)
_REFERENCE_COMPARISON_RE = re.compile(
    r"\b(?:compare|comparison|benchmark)\b.{0,80}\b(?:against|with|to|vs\.?|versus)\b"
    r"|\b(?:against|vs\.?|versus)\b.{0,60}\b(?:reference|repo|repository|project|framework|library)\b"
    r"|\bwhat\s+(?:should|could|can)\s+(?:i|we|it|my\s+\w+)\s+(?:adopt|learn|take)\s+from\b",
    re.I | re.S,
)


@dataclass(frozen=True)
class WorkPattern:
    name: str
    category: str
    complexity: str
    requires_design_dna: bool = False
    requires_verification: bool = True


def estimate_work_complexity(user_input: str) -> str:
    """Return a compact complexity label shared by Gateway/Ghost/goal UI."""
    terms = words(user_input)
    if not terms:
        return "simple"
    surface_hits = terms & _COMPLEX_SURFACE_WORDS
    if len(terms) > 30 or len(surface_hits) >= 4 or ({"entire", "everything"} & terms and len(surface_hits) >= 2):
        return "complex"
    if len(terms) > 18 or terms & _COMPLEX_WORDS or terms & _REFERENCE_WORDS or len(surface_hits) >= 2:
        return "moderate"
    if surface_hits and terms & {"audit", "review", "investigate", "scan", "analyze", "analyse"}:
        return "moderate"
    return "simple"


def is_prd_request(user_input: str) -> bool:
    """Return True for PRD/alignment requests, not explain-what-is-PRD chat."""
    text = str(user_input or "")
    return bool(_PRD_REQUEST_RE.search(text) and not _PRD_CHAT_PREFIX_RE.search(text))


def is_research_method_question(user_input: str) -> bool:
    """Return True when the operator asks how MO researches a codebase."""
    return bool(_RESEARCH_METHOD_RE.search(str(user_input or "")))


def select_work_pattern(user_input: str) -> WorkPattern | None:
    """Return the compact internal pattern for work turns, or None for chat."""
    terms = words(user_input)
    if not terms:
        return None
    complexity = estimate_work_complexity(user_input)
    # Universalized self-maintenance mindset wins over the generic review
    # shapes: these intents carry the distilled audit/comparison discipline.
    if _PROJECT_AUDIT_RE.search(str(user_input or "")):
        return WorkPattern("project_audit", "problem_solving", complexity)
    if _REFERENCE_COMPARISON_RE.search(str(user_input or "")):
        return WorkPattern("reference_comparison", "planning", complexity, requires_verification=False)
    template = select_template(user_input)
    if template == "deep_review":
        return WorkPattern("review_evidence", "deep_review", complexity, requires_verification=False)
    if is_prd_request(user_input):
        return WorkPattern("prd_planning", "planning", complexity, requires_verification=False)
    design_action = bool(terms & _DESIGN_ACTION_WORDS)
    buildish = bool(terms & _BUILD_WORDS) or design_action
    # A problem_solving template (explicit fix/debug OR interrogative-failure
    # diagnosis like "figure out why X crashes") is a fix/verify turn — without this
    # a diagnosis turn fell through to None and got no verify-before-claiming guidance.
    fixish = bool(terms & _FIX_WORDS) or template == "problem_solving"
    designish = design_action or bool(terms & _DESIGN_WORDS) or bool(terms & _REFERENCE_WORDS)
    if not (buildish or fixish):
        return None
    if fixish or (buildish and _FAILURE_QUESTION_RE.search(str(user_input or ""))):
        if terms & _REVIEW_REPAIR_WORDS:
            return WorkPattern("review_repair", "problem_solving", complexity)
        return WorkPattern("fix_verify", "problem_solving", complexity)
    if designish:
        return WorkPattern("design_build", "build_create", complexity, requires_design_dna=True)
    return WorkPattern("build_verify", "build_create", complexity)


def procedure_for(user_input: str):
    """Return the crystallized WorkProcedure for this work turn, or None.

    The same classifier that selects the prose work pattern selects the
    structured, evidence-gated procedure used to seed the taskboard. Chat and
    unmatched turns return None (no procedure, no board seeding).
    """
    pattern = select_work_pattern(user_input)
    if not pattern:
        return None
    from .tasking.procedure import work_procedure_for

    return work_procedure_for(pattern.name)


def build_work_pattern_context(user_input: str) -> str:
    """Provider-facing compact pattern context, injected only for work turns."""
    if is_research_method_question(user_input):
        return (
            "### MO Internal Work Pattern — research method explanation\n"
            "When the operator asks how MO would research a codebase, explain MO's actual current flow without pretending work was executed: "
            "confirm project cwd and sandbox boundaries; read project instructions/docs such as AGENTS.md, CLAUDE.md, README, and config; "
            "inspect tree/search/read evidence with tools; use structural graph orientation when available; use the private/incremental code graph fallback when structural graph is missing or stale; "
            "check git history/churn and tests as behavior maps; create a taskboard only when doing real research work, not when only explaining the method; "
            "report findings clearly and distinguish between verified, inferred, absent, and uncertain. Graph context is orientation, not proof."
        )
    pattern = select_work_pattern(user_input)
    if not pattern:
        return ""
    if pattern.requires_design_dna:
        return build_dna_context(design=True)
    if pattern.name == "prd_planning":
        return build_prd_context()
    if pattern.name == "fix_verify":
        return (
            "### MO Internal Work Pattern — fix/verify\n"
            "Reproduce or inspect evidence first. "
            + build_lean_build_context()
            + " Apply the smallest safe fix, then verify resolution with a local/static/runtime check. "
            "Never claim fixed without verification evidence."
        )
    if pattern.name == "review_repair":
        return (
            "### MO Internal Work Pattern — review/repair/verify\n"
            "Inventory the scoped files/tests first, run focused checks to find confirmed failures/incompleteness, "
            + build_lean_build_context()
            + " Apply the smallest safe fixes for confirmed issues, then rerun the relevant checks. "
            "Do not stop after a verification probe while repair work is still open."
        )
    if pattern.name == "review_evidence":
        return (
            "### MO Internal Work Pattern — review/evidence\n"
            "Inspect scoped target/diff before reporting. Use read/grep/git/test evidence where useful. "
            "Separate claims: verified, inferred, verified-absent, uncertain. "
            "Label evidenced findings by urgency: launch-blocking, fast-follow, track, advisory; "
            "unevidenced concerns are paper-tigers. "
            "Output compactly: Findings | Blockers | Next. "
            "Risk labels are report language only; never taskboard truth."
        )
    if pattern.name == "project_audit":
        return (
            "### MO Internal Work Pattern — project audit\n"
            "Diagnose with evidence, never with guesses. Orientation first (structural graph/fuzzy search/tree), "
            "then targeted reads and scoped checks — not grep storms or serial file dumps. "
            "Catalog confirmed findings BEFORE fixing anything; each finding needs file:line evidence and a severity. "
            "Then fix one finding class at a time with the smallest safe change and verify each fix before the next. "
            + build_lean_build_context()
            + " "
            "Honest output: confirmed / suspected / clean — an empty catalog is a valid result, never invent findings. "
            "If the audit targets MO's own behavior, read the live trace/session evidence before theorizing."
        )
    if pattern.name == "reference_comparison":
        return (
            "### MO Internal Work Pattern — reference comparison\n"
            "Read-only first: gather real evidence about the reference (fetch/read it) and the current project before judging. "
            "Compare outcomes, not feature lists. Classify each dimension: stronger-here, equal, missing, "
            "reference-stronger, or different-by-design — with evidence per row. "
            "Recommend adopting ONLY what is proven better and fits the project's direction; translating an idea into the "
            "project's own patterns beats copying the reference's architecture. "
            "If adoption is approved, use MO's lean-build ladder instead of copying whole foreign structures. "
            "For token, cost, performance, or compression claims, run a project-local baseline-vs-adopt measurement before closeout; "
            "record current behavior, candidate behavior, recoverability/fallback, and the measured win or mark it as unproven. "
            "Zero-adoption is a valid, honest outcome. No source edits until the operator approves specific items."
        )
    return build_dna_context(design=False)


def build_ghost_work_guidance(user_input: str) -> str:
    """Ghost-facing compact guidance, injected only when Ghost shapes intent."""
    pattern = select_work_pattern(user_input)
    if not pattern:
        return ""
    if pattern.name == "design_build":
        return (
            "For this design/build request, shape intent and guard scope only. Respect internal build/design DNA: inspect context, "
            "detect local design system, set direction, adapt tokens/components/states/motion, verify evidence. "
            "Do not invent filenames/checks/taskboard items unless the operator named them. Do not suggest skipping verification, "
            "no-test-command, or report-instead-of-verify."
        )
    if pattern.name == "prd_planning":
        return (
            "For this PRD/alignment request, shape intent only. PRD is optional planning guidance, not a forced build gate. "
            "Ask only material natural questions; otherwise draft with assumptions, anti-goals, acceptance criteria, risks, and open questions. "
            "Do not invent taskboard truth or imply implementation is complete from a PRD."
        )
    if pattern.name == "review_repair":
        return (
            "For this review/repair request, shape intent and guard scope only. Require inventory, focused checks, fixes for confirmed issues, and rerun verification. "
            "Do not invent concrete taskboard items or claim completion from a probe while fixes remain open."
        )
    if pattern.name == "review_evidence":
        return (
            "For this review request, shape intent and guard scope only. Require real read/search/git/check evidence before findings. "
            "Do not invent filenames, findings, risks, or taskboard items."
        )
    if pattern.name == "project_audit":
        return (
            "For this project-audit request, shape intent and guard scope only. Require orientation-first evidence, a confirmed-findings "
            "catalog before fixes, and per-fix verification. An empty catalog is valid; do not invent findings or taskboard items."
        )
    if pattern.name == "reference_comparison":
        return (
            "For this comparison request, shape intent and guard scope only. Keep it read-only until the operator approves adoptions; "
            "require real reference evidence and per-dimension classification. Zero-adoption is a valid outcome."
        )
    return (
        "For this work request, require a real verify/check step as a process guardrail. Do not invent concrete taskboard items. "
        "Do not suggest skipping verification, no-test-command, or report-instead-of-verify."
    )


