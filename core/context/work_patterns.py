"""Compact internal work patterns for MO.

This is not a public learning surface. It is a small zero-token selector that
gives providers scoped build/design/fix guidance without imposing an MO-authored
persona or visual style.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..agent.work_guidance import (
    build_design_work_context,
    build_lean_build_context,
    build_prd_context,
)
from ..runtime.capability_routing import looks_like_computer_action_request
from ..runtime.work_signals import (
    looks_like_descriptive_implementation_question,
    looks_like_mo_trace_reference,
    looks_like_self_directed_action_explanation,
    normalize_work_action_words,
)
from .gateway_helpers import select_template, words


_BUILD_WORDS = {
    "build", "rebuild", "remake", "rework", "create", "implement", "make", "write",
    "add", "new", "simulate", "simulation", "commit", "push", "deploy", "merge",
    "release", "publish",
}
_DESIGN_ACTION_WORDS = {"design"}
_FIX_WORDS = {"fix", "debug", "repair", "solve", "broken", "bug"}
# Maintenance/change verbs. Without these, common engineering turns
# ("refactor the resolver", "consolidate the gates", "clean up dead code")
# classified as chat and got no scoped work guidance or verification reminder.
# They route through the build path so the same inspect->change->verify->report
# discipline is injected; board creation still goes through turn-intent policy.
_MODIFY_WORDS = {
    "change", "ensure", "refactor", "maintain", "rename", "extend", "integrate", "optimize", "update", "modify",
    "wire", "adjust", "tweak", "consolidate", "remove", "delete", "clean", "cleanup",
    "improve", "enhance", "replace", "migrate", "connect", "configure", "restructure",
    "dedupe", "tidy", "streamline", "revamp", "overhaul", "port", "edit", "append",
    "address", "simplify",
}
# Delivery actions are operational work even when no code-edit verb accompanies
# them. This also lets profile-expanded command aliases route through the same
# generic work path instead of hardcoding an operator's private vocabulary.
_DELIVERY_WORDS = {"commit", "push", "deploy"}
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
    "personalization", "memory", "token", "tokens", "compression", "gateway",
}
_LOCAL_PATH_RE = re.compile(
    r"(?<!\w)(?:[A-Za-z]:[\\/]|\\\\)[^\s]+|(?<!\w)/(?:[^/\s]+/)+[^\s]*"
)
# Clone-and-adopt: reproduce an EXTERNAL reference (a site/page/app), then adapt it
# to the user's request. Distinct from design_build (build new from local evidence) and
# reference_comparison (read-only benchmark). Needs a clone verb AND a concrete
# external target (a URL, or a site/page/app noun) so an internal "clone the auth
# module" stays out of the browser-recon pipeline.
_CLONE_VERBS = {"clone", "copy", "mimic", "recreate", "replicate", "reproduce"}
_CLONE_TARGET_WORDS = {
    "website", "site", "webpage", "page", "landing", "dashboard",
    "app", "webapp", "screen", "ui", "frontend", "homepage",
}
_URL_RE = re.compile(
    r"https?://|www\.\w|\b[\w-]+\.(?:com|io|dev|org|net|app|co|ai|xyz|me|so|design|store|shop|site|page)\b",
    re.I,
)
_FAILURE_QUESTION_RE = re.compile(r"\b(?:why|what)\b.*\b(?:wrong|fail(?:ing|ed)?|broken|error|bug)\b", re.I)
_ADDRESS_DISCUSSION_RE = re.compile(
    r"^\s*(?:so\s+)?what\s+(?:still\s+)?(?:need|needs|needed|is|are|was|were)\s+"
    r"(?:to\s+be\s+)?address\b",
    re.I,
)
_PRD_REQUEST_RE = re.compile(r"\bprd\b|\bproduct\s+requirements?\b|\brequirements?\s+(?:doc|document|brief|spec)\b|\bbuild\s+brief\b", re.I)
_PRD_CHAT_PREFIX_RE = re.compile(r"^\s*(?:what\s+is|what's|explain|tell\s+me\s+about|define)\b", re.I)
_RESEARCH_METHOD_RE = re.compile(
    r"\b(?:how|what|explain|describe)\b.{0,80}\b(?:research|investigate|diagnos\w*|understand|analy[sz]e|study|map)\b.{0,100}\b(?:codebase|repo|repository|project)\b"
    r"|\b(?:research|investigate|diagnos\w*|understand|analy[sz]e|study|map)\b.{0,80}\b(?:codebase|repo|repository|project)\b.{0,80}\b(?:how|method|approach|process)\b",
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
    r"|\b(?:against|vs\.?|versus)\b.{0,60}\b(?:reference|baseline|upstream|external|peer|competitor|framework|library)\b"
    r"|\bwhat\s+(?:should|could|can)\s+(?:i|we|it|my\s+\w+)\s+(?:learn|take)\s+from\b",
    re.I | re.S,
)
_BOUNDED_LOOKUP_SCOPE_RE = re.compile(
    r"\b(?:one|single|small|quick|brief|specific)\b|\bat\s+most\s+\d+\b|\bdo\s+not\s+(?:edit|change|modify|write)\b",
    re.I,
)
_BOUNDED_CODE_TARGET_RE = re.compile(
    r"\b(?:the|a|this|that)\s+(?:function|method|class|symbol|definition|file|path)\b"
    r"|(?<![\w.])[\w.-]+\.[a-z][a-z0-9]{0,11}\b",
    re.I,
)
_BOUNDED_LOOKUP_ACTIONS = {
    "check", "determine", "find", "inspect", "locate", "name", "search",
    "show", "status", "tell", "verify", "whether",
}
_DEEP_WORK_WORDS = {
    "analyze", "analyse", "audit", "bugs", "build", "change", "create", "debug", "entire",
    "everything", "fix", "implement", "investigate", "issues", "problems", "redesign", "refactor",
    "repair", "review", "update",
    # Mutation imperatives: a "read-only lookup" that asks to change a file is a
    # contradiction. Exact-term matching keeps participles ("was added",
    # "removed lines") from colliding.
    "edit", "append", "write", "add", "delete", "remove", "rename", "overwrite", "insert",
}
_OPERATIONAL_STATUS_RE = re.compile(
    r"\b(?:check|show|verify|tell|what(?:'s|\s+is)?|how(?:'s|\s+is)?)\b.{0,80}"
    r"\b(?:servers?|vps|services?|deployments?|endpoints?)\b.{0,48}"
    r"\b(?:health|healthy|status|running|online|up)\b"
    r"|\b(?:health|status)\b.{0,48}\b(?:servers?|vps|services?|deployments?|endpoints?)\b",
    re.I | re.S,
)

_OPERATIONAL_DIAGNOSIS_WORDS = {
    "audit", "bug", "bugs", "debug", "deep", "diagnose", "entire", "error", "errors",
    "failing", "failure", "fix", "full", "issue", "issues", "problem", "problems", "repair",
}


# Clone-and-adopt contract (faithful first, then adapt). It adds the
# recon->extract->build->diff method and ethics floor to neutral design guidance,
# reusing MO's real capabilities (browser DOM + computed styles, web_fetch, perceive).
_CLONE_CONTRACT = (
    " Clone-and-adopt: reproduce the external reference FAITHFULLY first, then apply only the "
    "adaptations the user asked for. Recon in the browser — open the target, capture the rendered page, "
    "and snapshot the DOM; read responsive breakpoints from its CSS/media queries. Extract its visual system from computed "
    "styles (colors, type, spacing, radii, shadows) and write a per-section spec of computed CSS, "
    "states, and breakpoints; download referenced assets. Choose the output stack by detecting it (the "
    "target's, plain HTML/CSS, or the user's own project) — never force a framework. Record the "
    "adopt-delta: what stays 1:1 versus what adapts to the request. Build against the spec, then verify "
    "by comparing your render to the original screenshots and closing the gaps. Refuse impersonation, "
    "phishing, or trademark abuse, and keep the source's provenance."
)


_DESIGN_CHECK_HINT = (
    " For frontend files, run python -m core.diagnostics.design_check <changed paths> "
    "as an advisory static gate; it catches deterministic anti-generic, motion, layering, "
    "and performance tells, but it does not replace render/screenshot evidence."
)

_INVESTIGATION_EVIDENCE_GUIDANCE = (
    " Choose evidence by the claim, not a fixed tool checklist: inspect current owners/source; use "
    "callers/dependencies, project history, redundancy diagnostics, tests/runtime, or external/live evidence "
    "when relevant. Use tool_search for capabilities not visible. Graph, history, maps, and "
    "similarity are orientation; verify material claims in source or matching tests/runtime. "
    "Before reporting a defect, establish required behavior and a concrete failure; check callers, "
    "existing error handling, and counterevidence. Missing optional features and diff statistics alone "
    "do not establish bugs. Reuse evidence while source is unchanged; report "
    "scope actually inspected, checks run, and uncertainty."
)

@dataclass(frozen=True)
class WorkPattern:
    name: str
    category: str
    complexity: str
    requires_design_guidance: bool = False
    requires_verification: bool = True


def estimate_work_complexity(user_input: str) -> str:
    """Return a compact complexity label shared by Gateway and goal UI."""
    # File locations are payload, not work scope. In particular, pytest-xdist
    # adds another path segment (``popen-gwN``), which must not turn an ordinary
    # one-file Desktop attachment into a complex task merely because its
    # absolute path is long.
    terms = words(_LOCAL_PATH_RE.sub(" path ", str(user_input or "")))
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


def _is_clone_request(user_input: str, terms: set[str]) -> bool:
    """True for "clone this external site/app and adapt it": a clone verb plus a
    concrete external target (a URL, or a site/page/app noun)."""
    if not (terms & _CLONE_VERBS):
        return False
    return bool(_URL_RE.search(str(user_input or "")) or (terms & _CLONE_TARGET_WORDS))


def _is_bounded_readonly_lookup(user_input: str, terms: set[str]) -> bool:
    """Keep explicit small lookups boardless even when they say search/find."""
    text = str(user_input or "")
    bounded_scope = bool(
        _BOUNDED_LOOKUP_SCOPE_RE.search(text)
        or _BOUNDED_CODE_TARGET_RE.search(text)
    )
    if not bounded_scope or not (terms & _BOUNDED_LOOKUP_ACTIONS):
        return False
    effective = words(re.sub(r"\b(?:do\s+not|don't|without)\s+(?:edit|change|modify|write|append|add|delete|remove|rename|touch)\b", "", text, flags=re.I))
    # Delivery remains work even when a filename and verification clause make
    # the surrounding text look like a bounded lookup.
    return not bool(effective & (_DEEP_WORK_WORDS | _DELIVERY_WORDS))


def _is_operational_status_lookup(user_input: str, terms: set[str]) -> bool:
    """Keep a direct live service-health question out of code-audit procedures."""
    return bool(
        _OPERATIONAL_STATUS_RE.search(str(user_input or ""))
        and not (terms & _OPERATIONAL_DIAGNOSIS_WORDS)
    )


# A migration-TO-MO turn: the operator wants to bring their whole setup from a
# peer agent into MO. Narrow on purpose — it needs a move verb near a NAMED
# source, or an explicit "…to/into MO", so an ordinary "migrate the database" /
# "move this file" never seeds a migration board. It RECOGNIZES several peer
# agents so MO can respond to the intent; the ones with a real adapter
# (KNOWN_SOURCES = openclaw, hermes) perform the move, and a recognized source
# without an adapter yet is guided to the supported ones rather than pretended.
_PLATFORM_MIGRATION_RE = re.compile(
    r"(?i)("
    r"\b(?:migrat\w*|move|switch|import|bring|coming|come|port)\b[^.\n]{0,40}\b(?:openclaw|open ?claw|hermes|opencode|codex|cursor|claude ?code)\b"
    r"|\b(?:openclaw|open ?claw|hermes|opencode|codex|cursor|claude ?code)\b[^.\n]{0,40}\b(?:in)?to\s+mo\b"
    # Generic "…migrate/switch … to MO" intent. Tempered so it does NOT fire for
    # an explicitly named non-adapter source (Copilot) that the operator excluded —
    # otherwise "migrate from copilot to MO" would slip through this branch.
    r"|\b(?:migrat\w*|switch)\b(?:(?!copilot)[^.\n]){0,20}\b(?:in)?to\s+mo\b"
    r")"
)


def select_work_pattern(user_input: str) -> WorkPattern | None:
    """Return the compact internal pattern for work turns, or None for chat."""
    if (
        looks_like_descriptive_implementation_question(user_input)
        or looks_like_self_directed_action_explanation(user_input)
    ):
        return None
    user_input = normalize_work_action_words(user_input)
    terms = words(user_input)
    if not terms:
        return None
    # ``address`` is a real maintenance imperative, but an interrogative such
    # as "what needs addressing?" asks about the prior report.  Keep every
    # other action signal in the sentence; only this descriptive occurrence is
    # prevented from manufacturing build work and a taskboard.
    if _ADDRESS_DISCUSSION_RE.search(user_input):
        terms.discard("address")
    complexity = estimate_work_complexity(user_input)
    if looks_like_mo_trace_reference(user_input):
        return WorkPattern(
            "fix_verify",
            "problem_solving",
            "moderate" if complexity == "simple" else complexity,
        )
    # Bounded read-only lookups still use the system's route/evidence rules;
    # they do not need a deep-review contract or a multi-row taskboard.
    if _is_bounded_readonly_lookup(user_input, terms) or _is_operational_status_lookup(user_input, terms):
        return None
    # Bringing a user in from a peer agent: inspect -> approve -> apply -> verify -> restart.
    if _PLATFORM_MIGRATION_RE.search(str(user_input or "")):
        return WorkPattern("platform_migration", "problem_solving", complexity)
    # Universalized self-maintenance mindset wins over the generic review
    # shapes: these intents carry the distilled audit/comparison discipline.
    if _PROJECT_AUDIT_RE.search(str(user_input or "")):
        return WorkPattern("project_audit", "problem_solving", complexity)
    if _REFERENCE_COMPARISON_RE.search(str(user_input or "")):
        return WorkPattern("reference_comparison", "planning", complexity, requires_verification=False)
    # A concrete desktop actuation ("open the X app", "click the taskbar icon", "type
    # into the search box on screen"): treat as work so it gets observe->act->verify
    # discipline instead of falling to chat/deep_review. The regex needs an ACTION verb
    # (never a review verb) + a concrete desktop target, so it does not hijack review or
    # build turns. No dedicated procedure (name unregistered) -> board stays
    # set_plan-gated, so a simple one-action task is never over-structured into rows.
    if looks_like_computer_action_request(user_input):
        return WorkPattern("desktop_task", "problem_solving", complexity)
    template = select_template(user_input)
    if template == "deep_review":
        return WorkPattern("review_evidence", "deep_review", complexity, requires_verification=False)
    if is_prd_request(user_input):
        return WorkPattern("prd_planning", "planning", complexity, requires_verification=False)
    # Clone-and-adopt: reproduce an external reference, then adapt it. Checked
    # before the generic build/design branch so a bare "clone this site" (no build
    # verb) is still recognized as work and routed to the recon->extract->verify
    # pipeline instead of falling through to chat.
    if _is_clone_request(user_input, terms):
        return WorkPattern("clone_adopt", "build_create", complexity, requires_design_guidance=True)
    design_action = bool(terms & _DESIGN_ACTION_WORDS)
    buildish = (
        bool(terms & _BUILD_WORDS)
        or bool(terms & _MODIFY_WORDS)
        or bool(terms & _DELIVERY_WORDS)
        or design_action
    )
    # A problem_solving template (explicit fix/debug OR interrogative-failure
    # diagnosis like "figure out why X crashes") is a fix/verify turn — without this
    # a diagnosis turn fell through to None and got no verify-before-claiming guidance.
    fixish = bool(terms & _FIX_WORDS) or template == "problem_solving"
    designish = design_action or bool(terms & _DESIGN_WORDS) or bool(terms & _REFERENCE_WORDS)
    if not (buildish or fixish):
        return None
    if fixish or (buildish and _FAILURE_QUESTION_RE.search(str(user_input or ""))):
        return WorkPattern("fix_verify", "problem_solving", complexity)
    if designish:
        return WorkPattern("design_build", "build_create", complexity, requires_design_guidance=True)
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
    from ..tasking.procedure import work_procedure_for

    return work_procedure_for(pattern.name)


def build_work_pattern_context(user_input: str) -> str:
    """Provider-facing compact pattern context, injected only for work turns."""
    if is_research_method_question(user_input):
        return (
            "### MO Internal Work Pattern — research method explanation\n"
            "Explain MO's actual current flow compactly without pretending work was executed. "
            "Use tools only if the operator asks to perform the research."
        )
    pattern = select_work_pattern(user_input)
    if not pattern:
        return ""
    if pattern.name == "clone_adopt":
        return build_design_work_context() + _CLONE_CONTRACT + _DESIGN_CHECK_HINT
    if pattern.requires_design_guidance:
        return build_design_work_context() + _DESIGN_CHECK_HINT
    if pattern.name == "prd_planning":
        return build_prd_context()
    if pattern.name == "fix_verify":
        return (
            "### MO Scoped Work\n"
            + build_lean_build_context()
            + " Verify the changed path before claiming the issue is fixed."
        )
    if pattern.name == "review_evidence":
        return (
            "### MO Scoped Review\n"
            "Review only the requested target with targeted current evidence. Separate verified facts "
            "from uncertainty, and do not expand the scope or edit unless the operator asked for fixes."
            + _INVESTIGATION_EVIDENCE_GUIDANCE
        )
    if pattern.name == "project_audit":
        return (
            "### MO Scoped Audit\n"
            "Inspect only the named or current scope with targeted evidence. Do not manufacture a generic "
            "checklist or expand the task. Fix confirmed issues only when requested, and verify changed paths."
            + _INVESTIGATION_EVIDENCE_GUIDANCE
            + build_lean_build_context()
        )
    if pattern.name == "reference_comparison":
        return (
            "### MO Scoped Comparison\n"
            "Compare only the requested dimensions using current evidence from both sides. Zero change is valid; "
            "do not edit until the operator asks to implement a specific result."
        )
    return "### MO Scoped Work\n" + build_lean_build_context()


