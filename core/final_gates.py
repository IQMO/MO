"""Final-phase claim gates — declarative verify-before-claiming registry.

The turn-FINAL counterpart to ``behavior_gates`` (which owns the INPUT phase). Where
input gates BLOCK a turn before any provider call, these gates run on the finished
answer and force ONE bounded *continuation*: a corrective re-prompt that makes the model
verify or soften a claim, then answer again. Each gate is once-per-turn (tracked by the
caller's ``fired`` set), so the loop can never spin on them; combined with the other
inline final gates and the ``max_provider_requests`` turn cap, total continuations stay
bounded.

This is the first increment of folding ``run_turn``'s scattered inline final gates into a
single declarative registry. It starts with the three structurally-identical claim gates
(completion/cleanliness, current-state/version, unsourced-external) because they share one
exact shape: ``signal(final_text, tool_call_counts) -> label`` → emit → re-prompt. The
remaining final gates (contract, self-protocol truth, done-claim, verify-edits) carry
per-gate counters and board/test logic and fold in as later increments.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .claim_verification import (
    unsourced_external_claim_signal,
    unverified_claim_signal,
    unverified_completion_claim_signal,
)
from .owner_protocols import is_devmode05_activation, is_ifdev05_activation
from .tasking.contract import enforce_contract_gate, load_persisted_tasks_for_contract


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
    (tasks present AND ``open_count() == 0``); the DEVMODE05/IFDEV05 *whole-board*
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
    if is_devmode05_activation(user_input) or is_ifdev05_activation(user_input):
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
