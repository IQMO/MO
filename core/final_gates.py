"""Final-phase answer enforcement gates.

The turn-FINAL counterpart to ``behavior_gates`` (which owns the INPUT phase). Where
input gates BLOCK a turn before any provider call, these gates run on the finished
answer and may force a bounded *continuation*: a corrective re-prompt that makes
the model satisfy task truth, verify/soften a claim, own failing affected tests,
or reconcile the self-protocol closeout. The final answer-enforcement sequence
that used to live inline before ``session.add_assistant(final_text)`` now routes
through this module:

- contract gate;
- self-protocol completion-truth gate;
- done-claim task-truth gate;
- verify-edits affected-test gate;
- OWNER_INTEGRITY_AUDIT report truth gate;
- completion/cleanliness, current-state/version, and unsourced-external claim gates.

Some gates are once-per-turn via the caller's shared ``fired`` set; counter-bearing gates
thread their counters through explicit return values. The owner-protocol terminal stop
gates that run earlier on raw ``content`` remain a separate mechanism in ``run_turn``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from .claim_verification import (
    unsourced_external_claim_signal,
    unverified_claim_signal,
    unverified_completion_claim_signal,
)
from .owner_protocols import is_owner_maintenance_activation, is_owner_integrity_audit_activation, is_owner_interface_audit_activation
from .path_defaults import repo_root
from .self_maintenance.owner_integrity_audit_ground_truth import owner_integrity_audit_function_span_index, owner_integrity_audit_source_corpus_count
from .tasking.contract import enforce_contract_gate, load_persisted_tasks_for_contract


_TOOL_CALL_CLAIM_RE = re.compile(
    r"\b(?:tool[- ]?calls?(?:\s+count)?\s*[:=]\s*(\d+)|(\d+)\s+tool calls?)\b",
    re.IGNORECASE,
)
_TOOL_ERROR_CLAIM_RE = re.compile(
    r"\b(?:tool[- ]?errors?(?:\s+count)?\s*[:=]\s*(\d+)|(\d+)\s+tool errors?|(\d+)\s+errors?)\b",
    re.IGNORECASE,
)
_SAMPLED_DENOMINATOR_RE = re.compile(r"\bsampled\s+\d+\s+of\s+(\d+)\b", re.IGNORECASE)
_DATE_ONLY_LEDGER_RE = re.compile(r"\bevidence[_-]ledger[_-]\d{8}\.md\b", re.IGNORECASE)
_LINE_SPAN_CLAIM_RE = re.compile(
    r"`?([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?)`?"
    r"(?:\s*\([^)]*\))?\s*(?:is|spans|at|=|:)?\s*~?(\d{2,5})\s*(?:L|lines?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ClaimGate:
    """One verify-before-claiming gate.

    ``signal`` returns a short label when the answer trips the gate; ``instruction_method``
    is the agent method that builds the corrective re-prompt for that label.
    """

    name: str
    signal: Callable[[str, "dict | None"], "str | None"]
    instruction_method: str
    monitor_event: str
    activity: Callable[[str], str]
    include_tool_calls: bool = False  # preserve the current-state event's tool_calls field


# Order matters: completion (the operator's #1 "reported clean from assumption" failure)
# is checked first, then current-state/version, then the unsourced-external nudge.
CLAIM_GATES: tuple[ClaimGate, ...] = (
    ClaimGate(
        name="completion_claim",
        signal=unverified_completion_claim_signal,
        instruction_method="_unverified_completion_claim_instruction",
        monitor_event="unverified_completion_claim",
        activity=lambda label: f"{label} made without a check - verifying before finishing...",
    ),
    ClaimGate(
        name="current_state_claim",
        signal=unverified_claim_signal,
        instruction_method="_unverified_current_state_claim_instruction",
        monitor_event="unverified_claim",
        activity=lambda label: f"{label} made without a check - verifying before finishing...",
        include_tool_calls=True,
    ),
    ClaimGate(
        name="unsourced_external_claim",
        signal=unsourced_external_claim_signal,
        instruction_method="_unsourced_external_claim_instruction",
        monitor_event="unsourced_external_claim",
        activity=lambda label: "external claim made without naming a source - citing before finishing...",
    ),
)


def run_claim_gates(
    agent: Any,
    final_text: str,
    tool_call_counts: "dict | None",
    *,
    fired: set,
    monitor: Any = None,
    on_activity: Callable[[str], None] | None = None,
) -> str | None:
    """Return a corrective re-prompt for the first claim gate that trips and has not
    already fired this turn, marking it fired; else None.

    Pure dispatch: the caller ``session.add_assistant(...)`` the returned instruction and
    ``continue``s the loop. ``fired`` is the per-turn once-only guard set (replaces the old
    per-gate booleans).
    """
    counts = tool_call_counts or {}
    for gate in CLAIM_GATES:
        if gate.name in fired:
            continue
        label = gate.signal(final_text, counts)
        if not label:
            continue
        fired.add(gate.name)
        if monitor:
            payload = {"label": label}
            if gate.include_tool_calls:
                payload["tool_calls"] = sum(counts.values())
            monitor.emit(gate.monitor_event, payload)
        if on_activity:
            on_activity(gate.activity(label))
        return getattr(agent, gate.instruction_method)(label)
    return None


def run_done_claim_gate(
    agent: Any,
    boundary_report: Any,
    *,
    fired: set,
    on_activity: Callable[[str], None] | None = None,
) -> str | None:
    """Return a corrective re-prompt when the answer claims "done" while board rows
    are still open (a consistency-boundary conflict), else None.

    Move 3 increment 2: the boundary-driven twin of the claim gates, migrated out of
    ``run_turn``'s inline block. Once-per-turn via the shared ``fired`` set (key
    ``"done_claim"``). It runs at its original position — before the verify-edits and
    claim gates — so ordering is unchanged; only the logic moved here.
    """
    if "done_claim" in fired:
        return None
    if not agent._boundary_has_done_claim_conflict(boundary_report):
        return None
    fired.add("done_claim")
    if on_activity:
        on_activity("done-claim conflicts with open tasks - continuing to resolve...")
    return agent._done_claim_task_truth_instruction()


def run_verify_edits_gate(
    agent: Any,
    turn_modified_files: Any,
    *,
    fired: set,
    on_activity: Callable[[str], None] | None = None,
) -> str | None:
    """Return a corrective re-prompt when a code-editing turn's affected tests fail,
    so the model fixes them before claiming done, else None.

    Move 3 increment 3: migrated from ``run_turn``'s inline block, same position (after
    done-claim, before the claim gates). The instruction is produced by
    ``agent._affected_test_failure_instruction``, which RUNS the affected tests — that
    side effect and its fail-open/bounded behavior are preserved exactly. Once-per-turn
    semantics match the original precisely: the ``"verify_edits"`` guard is set ONLY
    after a failure fires, so a passing check does NOT mark the gate and can re-run later
    this turn (e.g. after a claim-gate continuation).
    """
    if "verify_edits" in fired:
        return None
    instruction = agent._affected_test_failure_instruction(turn_modified_files)
    if not instruction:
        return None
    fired.add("verify_edits")
    if on_activity:
        on_activity("changed-file tests failing - fixing before finishing...")
    return instruction


def run_owner_integrity_audit_reporting_gate(
    user_input: str,
    final_text: str,
    tool_call_counts: "dict | None",
    tool_error_counts: "dict | None",
    *,
    fired: set,
    continuations: int = 0,
    max_continuations: int = 3,
    monitor: Any = None,
    on_activity: Callable[[str], None] | None = None,
) -> str | None:
    """Force OWNER_INTEGRITY_AUDIT reports to reconcile with runtime/code truth before finishing.

    The OWNER_INTEGRITY_AUDIT preflight gives the model the contract at turn start. This answer-time gate
    closes the hole observed live: the model can still *say* "18 tool calls" after running
    54 tools unless the runtime checks the final answer before accepting it.
    """
    if not is_owner_integrity_audit_activation(user_input):
        return None
    if continuations >= max_continuations:
        return None
    root = repo_root()
    actual_tool_calls = sum((tool_call_counts or {}).values())
    actual_tool_errors = sum((tool_error_counts or {}).values())
    corpus = owner_integrity_audit_source_corpus_count(cwd=root)
    violation = _owner_integrity_audit_reporting_violation(
        final_text,
        actual_tool_calls=actual_tool_calls,
        actual_tool_errors=actual_tool_errors,
        corpus=corpus,
        root=root,
    )
    if not violation:
        return None
    fired.add("owner_integrity_audit_reporting_truth")
    if monitor:
        monitor.emit(
            "owner_integrity_audit_reporting_truth",
            {
                "violation": violation,
                "tool_calls": actual_tool_calls,
                "tool_errors": actual_tool_errors,
                "corpus": corpus,
                "continuation": continuations + 1,
                "max_continuations": max_continuations,
            },
        )
    if on_activity:
        on_activity("OWNER_INTEGRITY_AUDIT report conflicts with runtime truth - reconciling before finishing...")
    return (
        "[OWNER_INTEGRITY_AUDIT REPORTING TRUTH] Your final OWNER_INTEGRITY_AUDIT report conflicts with runtime/code truth: "
        f"{violation}. Continue now and correct the report before finishing. Required facts: "
        f"exact tool calls = {actual_tool_calls}; exact tool errors = {actual_tool_errors}; "
        f"coverage must use 'sampled N of {corpus}'; the evidence ledger must be under "
        "~/.mo/memory/owner_integrity_audit/ with a session-unique filename, not repo-local memory/ and not "
        "date-only. If you use another tool, these counts change; recount from the updated "
        "runtime history before the next final answer. Any function/file line-count claim "
        "must be re-measured from the current tree or removed."
    )


def _owner_integrity_audit_reporting_violation(
    text: str,
    *,
    actual_tool_calls: int,
    actual_tool_errors: int,
    corpus: int,
    root: str,
) -> str | None:
    tool_claims = _claimed_ints(_TOOL_CALL_CLAIM_RE, text)
    if not tool_claims:
        return f"missing exact tool-call count (actual {actual_tool_calls})"
    if any(value != actual_tool_calls for value in tool_claims):
        return f"tool-call count mismatch (claimed {tool_claims}, actual {actual_tool_calls})"

    error_claims = _claimed_ints(_TOOL_ERROR_CLAIM_RE, text)
    if not error_claims:
        return f"missing exact tool-error count (actual {actual_tool_errors})"
    if any(value != actual_tool_errors for value in error_claims):
        return f"tool-error count mismatch (claimed {error_claims}, actual {actual_tool_errors})"

    denominators = {int(match.group(1)) for match in _SAMPLED_DENOMINATOR_RE.finditer(text or "")}
    if corpus not in denominators:
        return f"coverage denominator missing or wrong (must say sampled N of {corpus})"

    lowered = (text or "").replace("\\", "/").lower()
    if ".mo/memory/owner_integrity_audit" not in lowered and "~/.mo/memory/owner_integrity_audit" not in lowered:
        return "missing canonical ~/.mo/memory/owner_integrity_audit evidence ledger path"
    if _DATE_ONLY_LEDGER_RE.search(text or ""):
        return "evidence ledger path is date-only; use a session-unique filename"

    span_violation = _line_span_claim_violation(text, root=root)
    if span_violation:
        return span_violation
    return None


def _claimed_ints(pattern: re.Pattern[str], text: str) -> list[int]:
    values: list[int] = []
    for match in pattern.finditer(text or ""):
        raw = next((group for group in match.groups() if group), None)
        if raw is None:
            continue
        try:
            values.append(int(raw))
        except ValueError:
            continue
    return values


def _line_span_claim_violation(text: str, *, root: str) -> str | None:
    matches = list(_LINE_SPAN_CLAIM_RE.finditer(text or ""))
    if not matches:
        return None
    spans = owner_integrity_audit_function_span_index(cwd=root)
    for match in matches:
        name, raw_count = match.groups()
        known = spans.get(name)
        if known is None:
            continue
        if not known:
            return f"ambiguous line-count claim for {name}; use a qualified function name"
        claimed = int(raw_count)
        if claimed not in known:
            choices = ", ".join(str(v) for v in sorted(known)[:5])
            return f"line-count mismatch for {name} (claimed {claimed}, current span {choices})"
    return None


def run_contract_gate(
    agent: Any,
    task_board: Any,
    user_input: str,
    turn_initial_completed_ids: set,
    *,
    count: int,
    max_continuations: int,
    on_activity: Callable[[str], None] | None = None,
) -> tuple[str | None, int]:
    """Return ``(instruction, updated_count)`` for the closing-board contract gate.

    ``instruction`` is a corrective re-prompt when a board with no open rows fails the
    contract gate (closed rows lack evidence), else None. Migrated verbatim from
    ``run_turn``'s inline block, preserving exactly: the board-closing condition
    (tasks present AND ``open_count() == 0``); the OWNER_MAINTENANCE/OWNER_INTERFACE_AUDIT *whole-board*
    enforcement vs the normal *turn-scoped* (``task_ids`` = rows completed THIS turn)
    branch; the ``enforce_contract_gate`` call; the counter bounded by
    ``max_continuations``; and the disagreement-after-cap behavior (once the cap is hit
    it logs and allows the close, returning None).

    The counter is threaded through (``count`` in, updated count out) instead of held
    here, so it stays a ``run_turn`` local and this gate stays decoupled from the other
    counter-bearing gates.
    """
    if not (task_board and task_board.tasks and task_board.open_count() == 0):
        return None, count
    persisted = load_persisted_tasks_for_contract(task_board)
    if is_owner_maintenance_activation(user_input) or is_owner_interface_audit_activation(user_input):
        contract_task_ids = None  # enforce the whole board
    else:
        completed_now = {t.id for t in task_board.tasks if t.status == "completed"} - turn_initial_completed_ids
        contract_task_ids = completed_now or None
    contract_ok, contract_reasons, contract_instruction = enforce_contract_gate(
        task_board, persisted_tasks=persisted, board_closing=True, task_ids=contract_task_ids,
    )
    if contract_ok:
        return None, count
    if count < max_continuations:
        if on_activity:
            on_activity(f"contract gate blocked: {'; '.join(contract_reasons[:3])}")
        return contract_instruction, count + 1
    if on_activity:
        on_activity("contract gate disagreement — allowing close after cap")
    return None, count


def run_self_protocol_truth_gate(
    agent: Any,
    user_input: str,
    final_text: str,
    boundary_report: Any,
    *,
    count: int,
    max_continuations: int,
    on_activity: Callable[[str], None] | None = None,
) -> tuple[str | None, int]:
    """Return ``(instruction, updated_count)`` for the self-protocol completion-truth gate.

    Forces a continuation when an owner-protocol turn (OWNER_MAINTENANCE/OWNER_COMPARISON/OWNER_INTERFACE_AUDIT) emits its
    ``[…COMPLETE]`` marker while the consistency boundary still reports a task-truth
    conflict. Migrated verbatim from ``run_turn``'s inline block, same position (after the
    contract gate, before done-claim). Counter-bounded by ``max_continuations``; the count
    check **short-circuits** the boundary check exactly as the original
    ``count < MAX and requires(...)`` — when capped, the boundary predicate is NOT called
    and the gate falls through (returns ``(None, count)``). The instruction comes from the
    agent's protocol-specific dispatcher; the counter is threaded through, decoupled from
    the contract gate's counter.
    """
    if count >= max_continuations:
        return None, count
    if not agent._self_protocol_completion_boundary_requires_continuation(user_input, final_text, boundary_report):
        return None, count
    if on_activity:
        on_activity("self protocol: completion conflicted with open work - continuing...")
    return agent._self_protocol_task_truth_continuation_instruction(user_input), count + 1
