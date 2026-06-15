"""Deterministic self-capability preflight for MO-internal work.

This module does not mutate state. It builds a compact, code-backed reminder
that MO must inventory existing capabilities before diagnosing or changing MO
itself, especially during DEVMODE05 sessions.
"""
from __future__ import annotations

from pathlib import Path
import os
import re

from .path_defaults import mo_home


_SELF_ACTION_WORDS = {
    "audit",
    "auditing",
    "capability",
    "capabilities",
    "debug",
    "diagnose",
    "diagnosis",
    "diagnostic",
    "forensic",
    "learn",
    "learning",
    "reasoning",
    "rootcause",
    "root-cause",
    "root cause",
    "self",
    "skip",
    "skipped",
    "trace",
    "workflow",
}

_SELF_SCOPE_MARKERS = {
    "devmode",
    "devmode05",
    "mo",
    "vs05",
    "versus mode",
    "versus-mode",
    "your behavior",
    "your capabilities",
    "your capability",
    "your codebase",
    "your own",
    "your reasoning",
    "your source",
    "your workflow",
    "yourself",
}

_RELEVANT_COMMANDS = {"/structural-graph", "/learning", "/profile", "/status", "/prt"}

# Keep this list small, explicit, and non-overlapping. It is the operator-auditable
# discovery contract for MO self/DEVMODE05 preflight.
REQUIRED_DISCOVERY_AREAS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("slash commands", ("interface/command_registry.py", "/structural-graph", "/learning", "/profile")),
    ("turn/runtime hooks", ("core/agent/agent_turn.py", "core/agent/agent.py", "_record_turn_memory_and_learning", "_maybe_handle_workflow_control_turn")),
    ("graph/code map", ("core/graph/code_graph.py", "core/graph/structural_graph.py", "core/graph/search.py", "core/graph/callgraph.py", "memory/structural_graph")),
    ("learning/profile/workflow", ("core/learning/proactive_learning.py", "core/learning/workflow_learning.py", "learning_suggestions.jsonl", "workflow_candidates.jsonl")),
    ("trace/session logs", ("memory/sessions", "session_closeouts", "heartbeats.jsonl", "tool_audit.jsonl", "provider_audit.jsonl", "logs/monitor/backend_monitor-*.jsonl", "operator/mo_trace.py")),
    ("taskboard/evidence", ("core/tasking/agent_taskboard.py", "complete_task", "core/tasking/task_evidence.py")),
    ("tests/docs", ("tests/", "docs/devmode", "operator/devmode/DEVMODE05.md", "operator/devmode/DEVMODE05/")),
    ("duplication/stale/legacy", ("git grep", "rg", "dead code", "duplicate paths", "retention proof")),
)

_CAPABILITY_FILES = (
    ("code graph", "core/graph/code_graph.py", "build_code_graph_context() injects orientation for non-greeting work"),
    ("structural graph", "core/graph/structural_graph.py", "community graph selected by code_graph when present"),
    ("graph fuzzy search", "core/graph/search.py", "BM25 symbol/file search — `python -c \"from core.graph.search import search; print(search('query'))\"` before broad grep"),
    ("call graph", "core/graph/callgraph.py", "get_callers()/get_callees() answer who-calls-X cheaply — use before manual reference hunting"),
    ("slash command registry", "interface/command_registry.py", "lists runtime commands such as /structural-graph and /learning"),
    ("learning mining", "core/learning/proactive_learning.py", "/learning suggestions and /profile mine review safe learning updates"),
    ("workflow learning", "core/learning/workflow_learning.py", "stages/promotes workflow candidates; never auto-executes them"),
    ("turn learning hook", "core/agent/agent.py", "_record_turn_memory_and_learning() records feedback/terms/workflow results"),
    ("turn workflow control", "core/agent/agent_turn.py", "_maybe_handle_workflow_control_turn() handles explicit workflow adoption"),
    ("provider audit", "core/provider/provider_audit.py", "logs provider requests/responses for trace review"),
    ("tool audit", "core/agent/agent_turn_dispatch.py", "_write_tool_audit() writes redacted logs/tool_audit.jsonl"),
    ("session closeout", "core/session/session_closeout.py", "captures dirty workspace, taskboard state, logs, and unresolved work"),
    ("heartbeat", "core/heartbeat.py", "records live taskboard/git/session continuity"),
    ("taskboard truth", "core/tasking/agent_taskboard.py", "task rows advance only via explicit complete_task evidence"),
    ("live trace", "operator/mo_trace.py", "session recorder and behavior validator; replay recent actions to see what MO actually did"),
)


def _pack_present() -> bool:
    """True when the untracked operator protocol pack is on disk."""
    try:
        root = Path(__file__).resolve().parents[1]
        return (root / "operator" / "devmode" / "DEVMODE05.md").exists() or (
            root / "operator" / "devmode" / "VS05.md"
        ).exists()
    except Exception:
        return False


def _owner_token_present() -> bool:
    """True when the operator's private owner token exists in the runtime home.

    The token (``~/.mo/operator.token``) lives only in the operator's private
    runtime home — never in any repo, never shipped. Copying the public repo, or
    even the protocol pack files, does not grant it; a fresh user clone's ``~/.mo``
    has no such token. This is what makes operator mode owner-bound rather than
    unlocked by mere file presence.
    """
    try:
        token = mo_home() / "operator.token"
        return token.is_file() and bool(token.read_text(encoding="utf-8").strip())
    except Exception:
        return False


def operator_protocols_installed() -> bool:
    """True only for the real operator: the private pack AND the owner token.

    DEVMODE05/VS05 are personal operator protocols, not product features. They
    require BOTH the untracked ``operator/devmode/`` pack AND a private owner
    token in ``~/.mo`` (``operator.token``) that a user clone never has — so the
    copyable pack files alone cannot fake operator mode. On a user clone both are
    absent, so the activation terms are inert by absence — no config, nothing to
    leak. ``MO_OPERATOR_PROTOCOLS=1`` forces installed-state for tests.
    """
    if os.environ.get("MO_OPERATOR_PROTOCOLS") == "1":
        return True
    return _pack_present() and _owner_token_present()


def is_devmode05_activation(user_input: str) -> bool:
    """Return True when the operator has activated DEVMODE05."""
    text = " ".join(str(user_input or "").strip().lower().split())
    if not text:
        return False
    if not re.search(r"\b(?:start\s+)?devmode\s*05\b", text):
        return False
    return operator_protocols_installed()


def is_vs05_activation(user_input: str) -> bool:
    """Return True when the operator has activated VS05 comparison mode."""
    text = " ".join(str(user_input or "").strip().lower().split())
    if not text:
        return False
    if not re.search(r"\b(?:start\s+)?vs\s*05\b", text):
        return False
    return operator_protocols_installed()


def vs05_readonly_source_roots(user_input: str) -> list[str]:
    """Return existing local source roots explicitly supplied to a VS05 turn.

    VS05 compares MO against operator-named references. Those references may
    live outside the active project root, but they are source-intake roots only:
    callers must still keep mutating tools on the normal project sandbox roots.
    """
    if not is_vs05_activation(user_input):
        return []
    tokens = re.findall(r'"([^"]+)"|\'([^\']+)\'|([^\s,;]+)', str(user_input or ""))
    roots: list[str] = []
    seen: set[str] = set()
    for groups in tokens:
        raw = next((value for value in groups if value), "").strip()
        if not raw:
            continue
        lowered = raw.lower().lstrip("/")
        if lowered in {"start", "vs05", "vs", "05"}:
            continue
        windows_abs = bool(re.match(r"^[A-Za-z]:[\\/]", raw)) or raw.startswith("\\\\")
        candidate = Path(raw).expanduser()
        if not (windows_abs or candidate.is_absolute()):
            continue
        try:
            resolved = candidate.resolve(strict=False)
            if resolved.is_file():
                resolved = resolved.parent
            if not resolved.is_dir():
                continue
        except OSError:
            continue
        key = str(resolved).casefold()
        if key in seen:
            continue
        roots.append(str(resolved))
        seen.add(key)
    return roots


def _marker_in_text(marker: str, text: str) -> bool:
    """Whole-word marker match.

    Substring matching let the 2-char scope marker ``mo`` fire on ordinary words
    like *re**mo**ve* / *me**mo**ry* / *modal*, injecting the self-preflight on
    unrelated work. Word-boundary matching keeps real mentions (``audit mo``,
    ``devmode``, ``your codebase``) while dropping incidental substrings.
    """
    return re.search(r"\b" + re.escape(marker) + r"\b", text) is not None


def should_include_self_capability_preflight(user_input: str) -> bool:
    """Return True for MO self-work where capability discovery must precede action."""
    text = " ".join(str(user_input or "").strip().lower().split())
    if not text:
        return False
    if is_devmode05_activation(text) or is_vs05_activation(text):
        return True
    scope_hit = any(_marker_in_text(marker, text) for marker in _SELF_SCOPE_MARKERS)
    action_hit = any(_marker_in_text(word, text) for word in _SELF_ACTION_WORDS)
    if scope_hit and action_hit:
        return True
    # Angry/corrective operator feedback often says "you" rather than "MO".
    # Require both self-action language and code/runtime nouns to avoid matching
    # ordinary "can you fix this bug" work.
    if "you" in text and action_hit and any(noun in text for noun in ("codebase", "feature", "graph", "skill", "tool", "trace", "workflow")):
        return True
    return False


def devmode05_final_allows_stop(user_input: str, final_text: str) -> bool:
    """Return True only when a DEVMODE05 final answer is a real stop boundary."""
    if not is_devmode05_activation(user_input):
        return True
    text = _devmode05_terminal_prefix_text(final_text)
    if not text:
        return False
    # Don't block the other protocol's completions — VS05 gate is responsible for those
    if text.startswith("[VS05 COMPLETE]") or text.startswith("[VS05 BLOCKED]"):
        return True
    if text.startswith("[DEVMODE05 BLOCKED]"):
        return _devmode05_blocked_has_hard_boundary(text)
    if text.startswith("[DEVMODE05 COMPLETE]"):
        if _devmode05_completion_reports_open_work(text):
            return False
        return True
    allowed_prefixes = (
        "[MAX PROVIDER REQUESTS]",
        "[MAX TOOL ROUNDS]",
        "MO provider error:",
        "MO interface error:",
        "Provider returned no visible answer",
        "Provider repeatedly produced malformed",
    )
    return text.startswith(allowed_prefixes)


def vs05_final_allows_stop(user_input: str, final_text: str) -> bool:
    """Return True only when a VS05 answer is a terminal comparison boundary."""
    if not is_vs05_activation(user_input):
        return True
    text = _devmode05_terminal_prefix_text(final_text)
    if not text:
        return False
    # Don't block the other protocol's completions — DEVMODE05 gate is responsible for those
    if text.startswith("[DEVMODE05 COMPLETE]") or text.startswith("[DEVMODE05 BLOCKED]"):
        return True
    if text.startswith("[VS05 BLOCKED]"):
        return _devmode05_blocked_has_hard_boundary(text)
    if text.startswith("[VS05 COMPLETE]"):
        if _devmode05_completion_reports_open_work(text):
            return False
        if _vs05_reports_default_target_drift(user_input, text):
            return False
        if _vs05_missing_closeout_terms(text):
            return False
        return True
    allowed_prefixes = (
        "[MAX PROVIDER REQUESTS]",
        "[MAX TOOL ROUNDS]",
        "MO provider error:",
        "MO interface error:",
        "Provider returned no visible answer",
        "Provider repeatedly produced malformed",
    )
    return text.startswith(allowed_prefixes)


def devmode05_continuation_instruction(user_input: str, final_text: str) -> str:
    """Explain why a DEVMODE05 stop claim was rejected and what must happen next."""
    base = (
        "[DEVMODE05 AUTONOMY] Do not stop at a checkpoint, report, or approval question. "
        "Continue with the next evidence-backed action. Finalize only with [DEVMODE05 COMPLETE] "
        "when the protocol is complete or [DEVMODE05 BLOCKED] for a real "
        "tool/provider/timeout/sandbox/permission/safety boundary."
    )
    if not is_devmode05_activation(user_input):
        return base
    text = _devmode05_terminal_prefix_text(final_text)
    if text.startswith("[DEVMODE05 COMPLETE]") and _devmode05_completion_reports_open_work(text):
        return (
            "[DEVMODE05 AUTONOMY] Your last answer claimed [DEVMODE05 COMPLETE] while also "
            "reporting deferred, remaining, open, carried-forward, or failed work. That is not a "
            "terminal state. Do not repeat the same completion report. Continue from the named "
            "open items now: resolve them, verify and close them as explicitly no-action, or update "
            "the artifacts so active deferred work is zero. Finalize only when the report truth says "
            "Remaining: none, Deferred active work: none, Next: none, and there are no failed checks."
        )
    if text.startswith("[DEVMODE05 BLOCKED]") and not _devmode05_blocked_has_hard_boundary(text):
        return (
            "[DEVMODE05 AUTONOMY] Your last answer used [DEVMODE05 BLOCKED] without a current hard "
            "tool/provider/timeout/sandbox/permission/safety boundary. Work remaining is not a "
            "blocker. Continue from the continuation capsule or next unresolved action now."
        )
    return base


def vs05_continuation_instruction(user_input: str, final_text: str) -> str:
    """Explain why a VS05 stop claim was rejected and what must happen next."""
    base = (
        "[VS05 CONTINUATION] Do not stop at initial capture or preliminary comparison. "
        "Continue the read-only VS05 protocol until source roles, structured evidence usage, "
        "comparison matrix, adoption/reject/defer dispositions, artifact path, and exact next "
        "approval decision are complete. Finalize only with [VS05 COMPLETE] or [VS05 BLOCKED] "
        "for a real tool/provider/timeout/sandbox/permission/safety boundary. Preferred final "
        "labels: Target, Matrix, Adoption, Reject, Defer/Recheck, Artifacts, Approval."
    )
    if not is_vs05_activation(user_input):
        return base
    text = _devmode05_terminal_prefix_text(final_text)
    if text.startswith("[VS05 COMPLETE]") and _devmode05_completion_reports_open_work(text):
        return (
            "[VS05 CONTINUATION] Your last answer claimed [VS05 COMPLETE] while still reporting "
            "remaining, deferred, open, failed, or carried-forward work. Continue from those named "
            "items now, or close them as reject/defer/no-action with evidence before completing."
        )
    if text.startswith("[VS05 COMPLETE]") and _vs05_reports_default_target_drift(user_input, text):
        return (
            "[VS05 CONTINUATION] Your VS05 closeout drifted from the default target. Current MO "
            "workspace is the adoption target; operator-supplied paths are read-only references "
            "unless the operator explicitly named another target. Rewrite/continue the matrix and "
            "adoption plan for current MO, not for a reference path. The closeout must include "
            "Target: current MO workspace."
        )
    if text.startswith("[VS05 COMPLETE]"):
        missing = _vs05_missing_closeout_terms(text)
        if missing:
            return (
                "[VS05 CONTINUATION] Your [VS05 COMPLETE] report is missing required closeout "
                f"terms: {', '.join(missing)}. Continue and produce the final report with these "
                "literal labels before final closeout: Target, Matrix, Adoption, Reject, Defer/Recheck, "
                "Artifacts, Approval. Do not repeat a summary-only closeout."
            )
    if text.startswith("[VS05 BLOCKED]") and not _devmode05_blocked_has_hard_boundary(text):
        return (
            "[VS05 CONTINUATION] Your last answer used [VS05 BLOCKED] without a current hard "
            "tool/provider/timeout/sandbox/permission/safety boundary. Work remaining is not a "
            "blocker. Continue the comparison from the next evidence-backed action."
        )
    return base


def devmode05_task_truth_continuation_instruction() -> str:
    """Tell DEVMODE05 how to recover from a terminal claim with open task truth."""
    return (
        "[DEVMODE05 AUTONOMY] Completion is not allowed while MO's task/protocol truth still "
        "has open work. Do not repeat the same completion report. Continue from the active "
        "taskboard/protocol row: run the next evidence-backed action, or if the active row is "
        "genuinely done, call `complete_task` and verify open task count is zero before the final "
        "[DEVMODE05 COMPLETE]. If the only rejection was `taskboard_done_claim_conflict`, do not "
        "inspect taskboard source, storage, or trace paths before that `complete_task` call; inspect "
        "implementation only if `complete_task` is unavailable or fails. Use [DEVMODE05 BLOCKED] "
        "only for a real hard runtime/tool/provider/safety boundary."
    )


def vs05_task_truth_continuation_instruction() -> str:
    """Tell VS05 how to recover from a terminal claim with open task truth."""
    return (
        "[VS05 CONTINUATION] Completion is not allowed while MO's task/protocol truth still "
        "has open work. Do not repeat the same completion report. Continue from the active "
        "VS05 taskboard row: run the next evidence-backed action, or if the active row is "
        "genuinely done, call `complete_task` and verify open task count is zero before the final "
        "[VS05 COMPLETE]. If the only rejection was `taskboard_done_claim_conflict`, do not "
        "inspect taskboard source, storage, or trace paths before that `complete_task` call; inspect "
        "implementation only if `complete_task` is unavailable or fails. Use [VS05 BLOCKED] "
        "only for a real hard runtime/tool/provider/safety boundary."
    )


def _vs05_missing_closeout_terms(text: str) -> list[str]:
    """Return missing VS05 terminal closeout concepts.

    The gate accepts the preferred literal label ``Matrix`` and the common
    semantic form ``Status: 7 MO-STRONGER ...`` because both are matrix-count
    evidence. It still requires explicit adoption and rejection disposition
    language before VS05 may stop.
    """
    lowered = str(text or "").lower()
    has_matrix = "matrix" in lowered or (
        "status" in lowered
        and any(
            marker in lowered
            for marker in (
                "mo-stronger",
                "reference-stronger",
                "existing-but-weak",
                "missing",
                "by-design",
                "unknown",
            )
        )
    )
    checks = (
        ("target", "target" in lowered or "current mo" in lowered),
        ("matrix", has_matrix),
        ("adoption", "adoption" in lowered or "adopt" in lowered),
        ("reject", "reject" in lowered or "by-design" in lowered),
    )
    return [name for name, present in checks if not present]


def _vs05_reports_default_target_drift(user_input: str, text: str) -> bool:
    """Detect VS05 closeouts that improve references instead of current MO."""
    if _vs05_user_named_non_current_target(user_input):
        return False
    lowered = str(text or "").lower()
    if "not a comparison target" in lowered and ("running mo" in lowered or "current mo" in lowered):
        return True
    if "current runtime instance; not a comparison target" in lowered:
        return True
    external_edit_plan = re.search(r"source edits?\s+in\s+[`\"']?[a-z]:\\", lowered)
    if external_edit_plan and "current mo" not in lowered:
        return True
    return False


def _vs05_user_named_non_current_target(user_input: str) -> bool:
    """Return True only for explicit operator target override wording."""
    lowered = str(user_input or "").lower()
    return bool(
        re.search(r"\btarget\s+[`\"']?[a-z]:\\", lowered)
        or "target repo" in lowered
        or "target path" in lowered
    )


def _devmode05_completion_reports_open_work(text: str) -> bool:
    """Detect self-reported DEVMODE05 leftovers that must continue, not close."""
    body = str(text or "")
    lowered = body.lower()
    if re.search(r"(?im)^\s*\[fail\]", body):
        return True
    if re.search(r"(?i)\[issues\]\s*[1-9]\d*\s+check\(s\)\s+failed", body):
        return True
    if re.search(r"(?i)\b[1-9]\d*\s+(?:fail|fails|failed)\b", body):
        return True
    if re.search(r"(?i)\b[1-9]\d*\s+(?:unresolved|deferred|open|carried forward)\b", body):
        return True
    if re.search(r"(?i)\b(?:unresolved|deferred|remaining|not addressed|carried forward)\b[^.\n]*\b[1-9]\d*\b", body):
        return True
    for match in re.finditer(r"(?im)^\s*(?:[-*]\s*)?(?:next|next targets?|remaining|deferred|unresolved)\s*:\s*(.+)$", body):
        value = match.group(1).strip().strip("`*_ ")
        if value and not re.fullmatch(r"(?i)(?:none|no(?:ne)?|n/a|0|zero|nothing|closed|complete|completed|clean)\.?", value):
            return True
    return any(marker in lowered for marker in (
        "highest-priority unresolved",
        "highest-value next target",
        "remaining (not addressed)",
    ))


def _devmode05_blocked_has_hard_boundary(text: str) -> bool:
    """Accept DEVMODE05 BLOCKED only for real external or deterministic limits."""
    body = str(text or "").lower()
    hard_markers = (
        "max provider",
        "max tool",
        "budget exhaustion",
        "tool budget",
        "tool rounds",
        "provider error",
        "provider timeout",
        "timeout",
        "sandbox block",
        "sandboxed",
        "permission denied",
        "approval required",
        "credential",
        "external boundary",
        "hard boundary",
        "safety boundary",
        "operator interrupt",
        "user stopped",
        "aborted",
    )
    return any(marker in body for marker in hard_markers)


def _devmode05_terminal_prefix_text(final_text: str) -> str:
    """Normalize harmless formatting before a DEVMODE05 terminal marker."""
    text = str(final_text or "").lstrip()
    if not text:
        return ""
    text = re.sub(r"^(?:[-*_]{3,}\s*)+", "", text).lstrip()
    text = _strip_leading_markdown_prefix(text)
    status = re.match(r"^(?:clean|done|complete|blocked|status)\.?\s*[:\-–—]?\s*", text, re.I)
    if status and status.end() <= 24:
        text = _strip_leading_markdown_prefix(text[status.end():])
    heading = re.search(
        r"(?im)^\s*(?:[-*_]{3,}\s*)?(?:#{1,6}\s*)?(?:[*_`]+\s*)?"
        r"(\[(?:DEVMODE05|VS05)\s+(?:COMPLETE|BLOCKED)\])",
        text[:480],
    )
    if heading:
        text = text[heading.start(1):]
    return text


def _strip_leading_markdown_prefix(text: str) -> str:
    return re.sub(r"^[\s#>*_`-]+", "", str(text or "")).lstrip()


def build_self_capability_preflight_context(user_input: str, *, cwd: str | None = None) -> str:
    """Build the mandatory preflight context for MO self/DEVMODE05 work."""
    if not should_include_self_capability_preflight(user_input):
        return ""
    root = Path(cwd or ".").resolve()
    commands = _relevant_command_lines()
    files = _capability_file_lines(root)
    return "\n".join(
        [
            "### MO Self-Capability Preflight — mandatory for this turn",
            "This request is about MO/DEVMODE05/VS05/self-behavior. Before any build, edit, or completion claim, produce a Capability Coverage Matrix from live evidence.",
            "If the operator said DEVMODE05 or start DEVMODE05, activation is already complete: do not ask what to investigate; immediately run the protocol preflight and diagnostics.",
            "If the operator said VS05 or start VS05, activation is comparison/adoption mode: read operator/devmode/VS05.md and its ordered modules, treat the current MO workspace as the default target, capture operator-supplied paths/links as read-only references unless the operator explicitly said `target <path>`, compare those references against current MO evidence, and stay read-only until the operator approves an adoption/implementation lane.",
            "STARTUP EVIDENCE ORDER: for DEVMODE05, read operator/devmode/DEVMODE05.md plus operator/devmode/DEVMODE05/00-activation-and-behavior.md first; for VS05, read operator/devmode/VS05.md plus operator/devmode/VS05/00-activation-and-boundaries.md first. Then run bounded live-trace rewind/orientation (`python operator/mo_trace.py list` plus replay/tail of the latest relevant trace), verify git cleanliness, read only the latest relevant summary/workflow/catalog plus longitudinal/comparison index when present, inspect runtime logs only by tail/targeted grep, read structural graph summary/context before broad grep, then build the Capability Coverage Matrix.",
            "REWIND FIRST (after loading this protocol): read your live trace — the latest memory/traces/trace_* directory or .trace file. This is what you actually did recently, not what you think you did. Find behavioral drift, convention violations, tool choice problems, and inefficiencies in your actual recent behavior. This is the first evidence step of diagnosis, not optional.",
            "GRAPH BEFORE BROAD SEARCH: for MO self-work, use the existing structural graph/code map to choose likely files and subsystems before broad grep/read sweeps. Graph hints are not proof; verify selected files with reads/tests before claims.",
            "CAPABILITY MATRIX BASELINE+DELTA RULE: do not rebuild the same matrix blindly every run. Read the latest docs/devmode summary/workflow/catalog plus longitudinal.md, reuse the previous matrix as the baseline, cheaply reconfirm unchanged stable capabilities, and spend deep probes on changed files, new trace anomalies, prior misses, and uncertainty. Current live trace wins over prior HEALTHY claims.",
            "Existing-capability rule: if MO already has the needed feature/hook/pattern, report it as EXISTING with source evidence and do not propose or build a duplicate enhancement.",
            "DEVMODE05 TASKBOARD PHASE RULE: the taskboard must represent real protocol phases, not a single generic 'Start DEVMODE05' wrapper after bootstrap. Keep rows aligned to boot/prior context, live trace + matrix, catalog, fixes, verification, and final closeout; update/complete rows only from real evidence.",
            "LIVE TRACE RULE: read your trace once at turn start. After every tool call, evaluate it from memory/recent context — was that the right tool? Match MO conventions? Any drift? Fix it right there. Do not re-read the full trace file after every call (that burns requests). Use targeted reads only when you detect specific drift.",
            "SHADOW SELF-AUDIT: DEVMODE05 progress is not raw tool telemetry. At meaningful boundaries, narrate the behavioral diagnosis: what action you took, why that was or was not the right MO-native action, whether a built-in feature (structural graph, code graph, learning/profile, trace replay, taskboard evidence, scoped tests) should have been used first, whether tools/tokens/context were wasted, and what instruction/routing/test/doc repair prevents the drift next time.",
            "OS-SHELL RULE: match commands to the active shell (see environment context above). Never use bash heredocs (`<<`). Do not use Unix `head`/`tail` on Windows; use bounded Python readers or confirmed shell-native commands such as PowerShell `Get-Content -Tail`. Use `python -c`, a temporary file under `tmp/`, or shell-native constructs. If you make an OS syntax mistake once, switch patterns immediately for the rest of the turn.",
            "SELF-CHECK every turn: did I miss a feature that already exists? Did I surface-level this instead of digging? Did I assume something without evidence? What would be a quick win here? Do not proceed without answering these four questions.",
            "ULTRA-REASON your own behavior before any conclusion: was this the most efficient approach? What cheaper alternative exists (graph, trace, batch tools, existing features)? Did I burn tokens unnecessarily? Am I over-engineering a band-aid instead of fixing the root cause? Did I fight my own tools instead of using them? Did I discover a behavioral misalignment or wrong implementation? Reason this for every action, not once at the end. If you find something wrong — fix it immediately, do not wait for approval. The operator already approved DEVMODE05, that covers self-fixes.\n\nPERSISTENT RULE (continuations): this applies every turn, including when you resume old work from memory/session closeout. Loading old unresolved tasks does not suspend these checks. They are active for the entire DEVMODE05 session, not just the first turn.",
            "Phase gate: produce the matrix and weakness/catalog diagnosis before source edits; then continue autonomously unless the operator explicitly interrupts or a hard safety boundary blocks the action. For DEVMODE05 activation, a final answer is allowed only as [DEVMODE05 COMPLETE] when complete or [DEVMODE05 BLOCKED] for a real tool/provider/timeout/sandbox/permission/safety boundary; otherwise continue with tools/evidence. Never use [DEVMODE05 BLOCKED] merely because work remains, files are dirty, tests passed, a continuation capsule exists, or budget pressure is rising while tools/provider responses are still available. Budget boundaries are continuity handoffs, not completion: preserve completed work, unresolved finding IDs, dirty files, verification, and the exact next action so the next DEVMODE05/resume turn continues without re-asking or redoing completed discovery.",
            "FINAL SELF-CLOSEOUT GATE: before [DEVMODE05 COMPLETE], verify live taskboard/trace truth: complete_task used if active, open=0, no later taskboard_done_claim_conflict, and no active deferred/open/failed work. After open=0/completed task truth, do not call more tools or reopen broad discovery in the same turn; produce [DEVMODE05 COMPLETE] from existing evidence. If not, fix it as a DEVMODE05 finding yourself.",
            "VS05 TARGET RULE: current MO workspace is the adoption target by default. Never describe the running MO workspace as only the runtime vehicle or `not a comparison target` unless the operator explicitly named another target. Reference paths are evidence inputs, not edit targets.",
            "VS05 SEMANTIC DELTA RULE: compare capabilities, not file names. Current MO already has taskboard ledger/resume surfaces, SQLite/profile/workflow learning surfaces, and structural/code graph caches. If a reference is stronger, name MO's existing mechanism first and classify only the exact delta (for example canonical current-task manager, categorized knowledge leaves, or unified map/query surface). Do not say tasks are lost on restart, no persistent knowledge exists, or no index exists without first disproving those current mechanisms.",
            "VS05 BEHAVIOR ECONOMY RULE: include provider-first smoothness, tasking truth, build/design DNA, Ghost/taskboard owner split, token/tool/compression/handoff cost, and structured-evidence reuse in the comparison. Do not propose replacing Ghost, taskboard, work patterns, or routing unless current consumers are mapped and trace/tests prove the replacement is cheaper, smoother, or more accurate.",
            "VS05 TERMINAL SHAPE: before [VS05 COMPLETE], verify taskboard/protocol truth is open=0 and the final answer contains these labels: Target, Matrix, Adoption, Reject, Defer/Recheck, Artifacts, Approval. Matrix may be literal matrix counts or status counts such as MO-STRONGER/REFERENCE-STRONGER, but do not use a summary-only closeout.",
            "Matrix columns required: capability, source path, invocation/runtime hook, applies?, used?, if not used why not.",
            "Matrix delta columns required: prior status, current status, changed evidence, new/changed risk, cost impact.",
            "Required discovery areas: " + "; ".join(name for name, _anchors in REQUIRED_DISCOVERY_AREAS) + ".",
            "Audit evidence requirement: provider audit and tool audit surfaces must be considered when diagnosing provider stability, tool errors, continuation, and trace truth.",
            "Must compare the exact request and prior tool/session trace against existing capabilities before proposing fixes.",
            "Approval rule: a verifier may help, but approval is invalid if any required discovery area is missing; model approval cannot override deterministic omissions.",
            "Deletion rule: 'we might need it later' is not evidence; stale, duplicate, legacy, or dead paths require caller/trace/test/operator proof to keep, otherwise remove or propose deletion.",
            "Relevant existing commands:",
            *commands,
            "Relevant code-backed capabilities to check:",
            *files,
            *_runtime_evidence_lines(root),
            "Verifier checklist: reject if the report lacks exact request replay, command inventory, graph/code-map check, learning/workflow check, trace/session evidence, taskboard evidence, tests/docs coverage, affected-method logic review, scoped verification plan, or explicit matrix-before-edit statement.",
            "Verification discipline: choose the smallest evidence that covers the touched behavior first; full-suite runs require a cross-cutting code/runtime change or explicit operator request, not habit.",
        ]
    )


def _runtime_evidence_lines(root: Path) -> list[str]:
    """Return sandbox-friendly runtime evidence locations to inspect."""
    home = mo_home()
    repo_memory = root / "memory"
    lines = [
        "Runtime evidence paths to inspect when relevant:",
        f"- private runtime home: {home} (sessions, session_closeouts, heartbeat, tool/provider audit logs)",
        "- live trace: memory/traces/trace_* (directory-based from operator/mo_trace.py serve or .trace files; replay with `python operator/mo_trace.py replay <path>`; list with `python operator/mo_trace.py list`)",
        f"- backend monitor fallback: {home}/logs/monitor/backend_monitor-*.jsonl (when running mo.py directly)",
        "- mo_trace.py: operator/mo_trace.py serve <args> (launches mo.py wrapped with auto-tracing; traces saved to memory/traces/)",
        f"- repo-local fallback: {repo_memory} (legacy/dev checkout memory when private home is unavailable)",
        "- if private paths are sandbox-blocked, state that explicitly and inspect repo-local memory plus source hooks instead of claiming trace coverage.",
    ]
    return lines


def _relevant_command_lines() -> list[str]:
    try:
        from interface.command_registry import COMMANDS
    except Exception:
        return ["- command registry unavailable; verify interface/command_registry.py with file tools"]
    lines: list[str] = []
    for spec in COMMANDS:
        if spec.name not in _RELEVANT_COMMANDS:
            continue
        subs = ", ".join(name for name, _desc in spec.subcommands) or "status/default"
        aliases = f" aliases={','.join(spec.aliases)}" if spec.aliases else ""
        lines.append(f"- {spec.name}{aliases}: {spec.description}; subcommands: {subs}")
    return lines or ["- no relevant slash commands discovered; verify command registry"]


def _capability_file_lines(root: Path) -> list[str]:
    lines: list[str] = []
    for name, rel, note in _CAPABILITY_FILES:
        exists = (root / rel).exists()
        status = "exists" if exists else "missing"
        lines.append(f"- {name}: {rel} ({status}) — {note}")
    return lines
