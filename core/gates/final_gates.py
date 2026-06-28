"""Final-phase answer enforcement gates.

The turn-FINAL counterpart to ``behavior_gates`` (which owns the INPUT phase). Where
input gates BLOCK a turn before any provider call, these gates run on the finished
answer and may force a bounded *continuation*: a corrective re-prompt that makes
the model satisfy task truth, verify/soften a claim, own failing affected tests,
or reconcile task truth. The final answer-enforcement sequence
that used to live inline before ``session.add_assistant(final_text)`` now routes
through this module:

- contract gate;
- task-truth gate;
- done-claim task-truth gate;
- verify-edits affected-test gate;
- LSP-diagnostics edit-truth gate (blocks "fixed/clean" while a configured language
  server still reports errors in files edited this turn);
- completion/cleanliness, current-state/version, and unsourced-external claim gates.

Some gates are once-per-turn via the caller's shared ``fired`` set; counter-bearing gates
thread their counters through explicit return values.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .. import local_extensions
from .claim_verification import (
    unsourced_external_claim_signal,
    unverified_claim_signal,
    unverified_completion_claim_signal,
)
from ..tasking.contract import enforce_contract_gate, load_persisted_tasks_for_contract


@dataclass
class ContractGateResult:
    """Result from the closing-board contract gate."""

    instruction: str | None
    count: int
    blocked_text: str | None = None

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_text)


@dataclass
class TaskTruthGateResult:
    """Result from the task-truth gate."""

    instruction: str | None
    count: int
    blocked_text: str | None = None

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_text)


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


def run_continuity_gate(
    agent: Any,
    user_input: str,
    final_text: str,
    *,
    fired: set,
    monitor: Any = None,
    on_activity: Callable[[str], None] | None = None,
) -> str | None:
    """Re-prompt stale continuity answers that skipped runtime work state."""
    if "continuity_claim" in fired:
        return None
    try:
        from ..runtime.continuity import continuity_gate_instruction

        instruction = continuity_gate_instruction(
            user_input,
            final_text,
            getattr(agent, "_last_continuity_snapshot", None),
        )
    except Exception:
        return None
    if not instruction:
        return None
    fired.add("continuity_claim")
    if monitor:
        monitor.emit("continuity_gate", {"reason": "runtime_snapshot_required"})
    if on_activity:
        on_activity("continuity answer skipped runtime state - correcting...")
    return instruction


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


def run_lsp_diagnostics_gate(
    agent: Any,
    turn_modified_files: Any,
    *,
    fired: set,
    on_activity: Callable[[str], None] | None = None,
) -> str | None:
    """Re-prompt when a language server still reports ERRORS in files edited this
    turn, so the model fixes them before claiming the work is clean — else None.

    This is the LSP evidence gate: diagnostics become blocking evidence instead of passive context.
    Fail-open by design — a no-op when no `lsp.servers` is configured (the default),
    when nothing was edited, or when no errors remain. Once-per-turn (marked only
    after it fires), so it nudges at most once and can never loop.
    """
    if "lsp_diagnostics" in fired:
        return None
    mgr = getattr(agent, "lsp_manager", None)
    if mgr is None or not getattr(mgr, "enabled", False) or not turn_modified_files:
        return None
    from ..lsp import summarize_diagnostics  # local: keep LSP off the default gate path

    flagged: list[tuple[str, int]] = []
    seen: set[str] = set()
    for entry in turn_modified_files:
        path = str(entry[0] if isinstance(entry, (list, tuple)) else entry)
        if path in seen:
            continue
        seen.add(path)
        try:
            errs = int(summarize_diagnostics(mgr.file_diagnostics(path)).get("error", 0))
        except Exception:
            continue
        if errs:
            flagged.append((path, errs))
    if not flagged:
        return None
    fired.add("lsp_diagnostics")
    if on_activity:
        on_activity("language server reports errors - fixing before finishing...")
    detail = "; ".join(f"{p}: {n} error(s)" for p, n in flagged[:6])
    return (
        "The language server reports unresolved error(s) in files you edited this turn: "
        f"{detail}. Inspect and fix them, or state explicitly why they are acceptable, "
        "before claiming the work is clean or done."
    )


def run_contract_gate(
    agent: Any,
    task_board: Any,
    user_input: str,
    turn_initial_completed_ids: set,
    *,
    count: int,
    max_continuations: int,
    on_activity: Callable[[str], None] | None = None,
) -> ContractGateResult:
    """Return the closing-board contract gate result.

    ``instruction`` is a corrective re-prompt when a board with no open rows fails the
    contract gate (closed rows lack evidence), else None. ``blocked_text`` is a terminal
    blocked answer when the bounded continuation cap is exhausted and the contradiction
    still remains. The gate preserves the board-closing condition
    (tasks present AND ``open_count() == 0``), the ``enforce_contract_gate``
    call, the counter bounded by ``max_continuations``, and the
    disagreement-after-cap behavior.

    The counter is threaded through (``count`` in, updated count out) instead of held
    here, so it stays a ``run_turn`` local and this gate stays decoupled from the other
    counter-bearing gates.
    """
    if not (task_board and task_board.tasks and task_board.open_count() == 0):
        return ContractGateResult(None, count)
    persisted = load_persisted_tasks_for_contract(task_board)
    completed_now = {t.id for t in task_board.tasks if t.status == "completed"} - turn_initial_completed_ids
    contract_task_ids = completed_now or None
    contract_ok, contract_reasons, contract_instruction = enforce_contract_gate(
        task_board, persisted_tasks=persisted, board_closing=True, task_ids=contract_task_ids,
    )
    if contract_ok:
        return ContractGateResult(None, count)
    if count < max_continuations:
        if on_activity:
            on_activity(f"contract gate blocked: {'; '.join(contract_reasons[:3])}")
        return ContractGateResult(contract_instruction, count + 1)
    blocked_text = _contract_gate_blocked_text(contract_reasons)
    if on_activity:
        on_activity(f"contract gate blocked after cap: {'; '.join(contract_reasons[:3])}")
    return ContractGateResult(None, count, blocked_text)


def _contract_gate_blocked_text(reasons: list[str]) -> str:
    shown = "; ".join(str(reason) for reason in (reasons or [])[:5] if str(reason or "").strip())
    if not shown:
        shown = "taskboard contract failed"
    return (
        "[TASKBOARD CONTRACT BLOCKED] Cannot honestly close this turn because the "
        f"taskboard contract still fails after bounded recovery: {shown}. "
        "Fix the task evidence/state and rerun verification."
    )


def run_task_truth_gate(
    agent: Any,
    user_input: str,
    final_text: str,
    boundary_report: Any,
    *,
    count: int,
    max_continuations: int,
    on_activity: Callable[[str], None] | None = None,
) -> TaskTruthGateResult:
    """Return the extension task-truth gate result."""
    _ = boundary_report
    instruction = local_extensions.task_truth_continuation(
        agent,
        user_input,
        final_text,
        getattr(getattr(agent, "gateway", None), "last_task_board", None),
    )
    if not instruction:
        return TaskTruthGateResult(None, count)
    if count >= max_continuations:
        if on_activity:
            on_activity("task truth blocked after cap")
        return TaskTruthGateResult(None, count, _task_truth_blocked_text())
    if on_activity:
        on_activity("completion conflicted with task truth - continuing...")
    return TaskTruthGateResult(instruction, count + 1)


def _task_truth_blocked_text() -> str:
    return (
        "[TASK TRUTH BLOCKED] Cannot honestly close this turn because the final "
        "answer still conflicts with task truth after bounded "
        "recovery. Fix the open-work/evidence contradiction and rerun verification."
    )
