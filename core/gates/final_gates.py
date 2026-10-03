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
- verify-edits affected-test gate (continues only for real failures and appends
  unavailable-coverage disclosures without another provider request);
- LSP-diagnostics edit-truth gate (blocks "fixed/clean" while a configured language
  server still reports errors in files edited this turn);
- task-owned scratch cleanup gate;
- unfulfilled short action promises;
- completion/cleanliness, current-state/version, and unsourced-external claim gates.

Claim gates share one correction opportunity per turn via the caller's ``fired``
set; counter-bearing gates thread their counters through explicit return values.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable

from .. import local_extensions
from .claim_verification import (
    detect_completion_claim,
    detect_material_work_completion_claim,
    structured_execution_claim_gap,
    turn_relative_tool_unavailability_signal,
    unfulfilled_action_promise_signal,
    unsourced_external_claim_signal,
    unverified_claim_signal,
    unverified_completion_claim_signal,
    unverified_material_work_completion_claim_signal,
)
from ..runtime.turn_intent import looks_like_trivial_greeting
from ..tasking.task_evidence import execution_actions_from_text
from ..tasking.results import tool_execution_records
from ..tasking.contract import enforce_contract_gate, load_persisted_tasks_for_contract
from ..tooling.scratch import unrequested_live_scratch_findings


@dataclass
class ContractGateResult:
    """Result from the closing-board contract gate."""

    instruction: str | None
    count: int
    blocked_text: str | None = None
    settlement: "ContractGateSettlement | None" = None

    @property
    def blocked(self) -> bool:
        return bool(self.blocked_text)


@dataclass(frozen=True)
class ContractGateSettlement:
    """One canonical row transition requested after bounded recovery ends."""

    task_id: str
    reason: str


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
class VerifyEditsGateResult:
    """Actual test failure instruction plus non-blocking coverage disclosure."""

    instruction: str | None = None
    disclosure: str | None = None


@dataclass(frozen=True)
class ClaimGateResult:
    """One correction request or a terminal, user-safe verification failure."""

    instruction: str | None = None
    blocked_text: str | None = None


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


# Order matters: a routed-catalog denial must be corrected before it can escape
# as a blocker, then completion/current-state/path/source claims follow.
CLAIM_GATES: tuple[ClaimGate, ...] = (
    ClaimGate(
        name="tool_availability_denial",
        signal=turn_relative_tool_unavailability_signal,
        instruction_method="_turn_relative_tool_unavailability_instruction",
        monitor_event="turn_relative_tool_unavailability_claim",
        activity=lambda label: f"{label} - discovering the owning route before finishing...",
    ),
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
    material_work: bool = False,
    user_input: str = "",
    tool_sequence: Any = None,
    task_transitions: Any = None,
    turn_modified_files: Any = None,
    monitor: Any = None,
    on_activity: Callable[[str], None] | None = None,
) -> ClaimGateResult | None:
    """Check every candidate, allowing only one corrective provider request.

    Exhausting the shared correction opportunity does not establish evidence.
    An unsupported replacement ends with a user-safe failure without exposing
    the rejected draft or internal claim label. Semantic audit judgments remain
    evidence-backed reasoning, not keyword
    classifications or tool-count certificates of ownership and correctness.
    """
    claim_gate_names = {
        "structured_execution_claim",
        "material_completion_claim",
        "unfulfilled_action_promise",
        *(gate.name for gate in CLAIM_GATES),
    }
    correction_used = bool(fired.intersection(claim_gate_names))

    def result(label: str, instruction: str) -> ClaimGateResult:
        if correction_used:
            if monitor:
                monitor.emit("unverified_claim", {"label": label, "outcome": "blocked"})
            blocked_text = "I couldn't verify that result, so I can't confirm it."
            delivery_prefix = "delivery-completed claim lacks "
            if str(label or "").startswith(delivery_prefix):
                action_order = ("commit", "push", "deploy")
                observed = {
                    action
                    for record in tool_execution_records(tool_sequence)
                    if record.successful and record.tool == "shell"
                    for action in str(record.action or "").split(",")
                    if action in action_order
                }
                missing = [
                    action for action in action_order
                    if action in str(label)[len(delivery_prefix):].split(", ")
                ]
                if observed and missing:
                    verified_text = ", ".join(
                        action for action in action_order if action in observed
                    )
                    blocked_text = (
                        f"Verified this turn: {verified_text}. "
                        f"Not verified: {', '.join(missing)}. "
                        "The full delivery workflow is not confirmed complete."
                    )
            return ClaimGateResult(blocked_text=blocked_text)
        return ClaimGateResult(instruction=instruction)
    # A bare greeting/acknowledgement has no factual work claim to verify.  Reusing
    # the runtime's exact detector prevents friendly replies such as "Hi, I'm here"
    # from consuming a corrective provider pass while leaving every substantive
    # request on the normal evidence gates.
    if not material_work and looks_like_trivial_greeting(user_input):
        return None
    counts = tool_call_counts or {}
    if tool_sequence is not None:
        # An attempted or rejected tool is not evidence. Preserve only the
        # existing background-lifecycle markers from the counts projection.
        counts = {name: count for name, count in counts.items() if name.startswith("_background_")}
        for record in tool_execution_records(tool_sequence):
            if record.successful:
                key = f"git_status:{record.action}" if record.tool == "git_status" else record.tool
                counts[key] = counts.get(key, 0) + 1
    promise_label = unfulfilled_action_promise_signal(user_input, final_text, counts)
    if promise_label:
        fired.add("unfulfilled_action_promise")
        if monitor:
            monitor.emit("unfulfilled_action_promise", {"label": promise_label})
        if on_activity:
            on_activity("announced evidence work was not executed - continuing now...")
        return result(
            promise_label,
            agent._unfulfilled_action_promise_instruction(promise_label),
        )
    profile_delivery_claims = set()
    expand_terms = getattr(agent, "_operator_text_with_profile_terms", None)
    if callable(expand_terms):
        # A private delivery shorthand and its completion claim must occur in the
        # same clause. Mentioning an older delivery workflow elsewhere in a
        # report must not turn an unrelated "implemented" claim into deploy work.
        for clause in re.split(r"(?<=[.!?])\s+|[\r\n;]+", final_text):
            if not detect_material_work_completion_claim(clause):
                continue
            expanded_clause = str(expand_terms(clause) or clause)
            if expanded_clause != clause:
                profile_delivery_claims.update(
                    execution_actions_from_text(expanded_clause[len(clause):])
                )
    structured_label = structured_execution_claim_gap(
        final_text,
        tool_sequence,
        task_transitions,
        turn_modified_files=turn_modified_files,
        profile_delivery_claims=profile_delivery_claims,
    )
    if structured_label:
        fired.add("structured_execution_claim")
        if monitor:
            monitor.emit("structured_execution_claim", {"label": structured_label})
        if on_activity:
            on_activity(f"{structured_label} conflicts with execution chronology - correcting...")
        return result(structured_label, agent._unverified_completion_claim_instruction(structured_label))
    label = unverified_material_work_completion_claim_signal(
        final_text,
        counts,
        material_work=material_work,
    )
    if label:
        fired.add("material_completion_claim")
        if monitor:
            monitor.emit("unverified_material_completion_claim", {"label": label})
        if on_activity:
            on_activity(f"{label} made without a check - verifying before finishing...")
        return result(label, agent._unverified_completion_claim_instruction(label))
    for gate in CLAIM_GATES:
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
        return result(label, getattr(agent, gate.instruction_method)(label))
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
    verification_results: dict[str, str] | None = None,
    verification_selected_roots: set[str] | None = None,
    on_activity: Callable[[str], None] | None = None,
) -> VerifyEditsGateResult:
    """Run bounded affected-test coverage and continue only for real failures."""
    if not turn_modified_files:
        return VerifyEditsGateResult()
    if "verify_edits" in fired:
        return VerifyEditsGateResult()
    fired.add("verify_edits")
    if on_activity:
        on_activity("checking affected tests...")
    result = agent._affected_test_gate_result(
        turn_modified_files,
        verification_results=verification_results,
        verification_selected_roots=verification_selected_roots,
    )
    if result.instruction and on_activity:
        on_activity("changed-file tests failing - fixing before finishing...")
    return result


def run_lsp_diagnostics_gate(
    agent: Any,
    turn_modified_files: Any,
    *,
    fired: set,
    on_activity: Callable[[str], None] | None = None,
    monitor: Any = None,
) -> str | None:
    """Re-prompt when a language server still reports ERRORS in files edited this
    turn, so the model fixes them before claiming the work is clean — else None.

    Disabled/unsupported languages are intentional no-ops. Configured server,
    timeout, and read failures are typed missing evidence and cannot be called
    clean. A successful mutation rearms the gate for the changed candidate.
    """
    if not turn_modified_files or "lsp_diagnostics" in fired:
        return None
    fired.add("lsp_diagnostics")
    mgr = getattr(agent, "lsp_manager", None)
    root = agent._effective_project_cwd() if hasattr(agent, "_effective_project_cwd") else None
    if mgr is None:
        return None
    if not mgr.enabled_for(root):
        if root is not None:
            mgr.stop_all(root)
        return None
    if on_activity:
        on_activity("checking edited files with the language server...")
    flagged: list[tuple[str, int]] = []
    unavailable: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for entry in turn_modified_files:
        path = str(entry[0] if isinstance(entry, (list, tuple)) else entry)
        if path in seen:
            continue
        seen.add(path)
        result = mgr.file_diagnostics(path, root_path=root)
        if monitor:
            monitor.emit("lsp_diagnostics", {
                "status": result.status,
                "reason": result.reason,
                "language": result.language,
                "server": result.server,
                "errors": int(result.counts.get("error", 0)),
            })
        if result.status in {"disabled", "unsupported"}:
            continue
        if result.status in {"unavailable", "timeout", "read_error"}:
            unavailable.append((path, result.status, result.reason))
            continue
        errs = int(result.counts.get("error", 0))
        if errs:
            flagged.append((path, errs))
    if not flagged and not unavailable:
        return None
    if on_activity and flagged:
        on_activity("language server reports errors - fixing before finishing...")
    elif on_activity:
        on_activity("language-server evidence unavailable - correcting the completion claim...")
    detail = "; ".join(f"{p}: {n} error(s)" for p, n in flagged[:6])
    unavailable_detail = "; ".join(
        f"{path}: {status} ({reason})"
        for path, status, reason in unavailable[:6]
    )
    if unavailable_detail:
        prefix = f"Language-server evidence is unavailable for edited files: {unavailable_detail}. "
        if detail:
            prefix += f"Other diagnostics still report: {detail}. "
        return (
            prefix
            + "Do not call the files LSP-clean. Use other verified checks and state the missing "
            "LSP evidence explicitly; do not retry the same failed server in this turn."
        )
    return (
        "The language server reports unresolved error(s) in files you edited this turn: "
        f"{detail}. Inspect and fix them, or state explicitly why they are acceptable, "
        "before claiming the work is clean or done."
    )


def run_scratch_cleanup_gate(
    agent: Any,
    user_input: str,
    final_text: str,
    turn_modified_files: Any,
    *,
    fired: set,
    on_activity: Callable[[str], None] | None = None,
) -> str | None:
    """Re-prompt a completion claim that leaves this turn's scratch behind.

    The edit ledger is already scoped to successful writes in the current turn.
    We inspect only those paths, never the shared ``tmp/`` tree, and never delete
    anything automatically.  An exact operator-requested scratch destination is
    intentional output and therefore exempt.
    """
    if "scratch_cleanup" in fired or not turn_modified_files:
        return None
    if not (
        detect_completion_claim(final_text)
        or detect_material_work_completion_claim(final_text)
    ):
        return None
    project_root = getattr(agent, "project_cwd", None)
    if not project_root:
        return None
    remaining = unrequested_live_scratch_findings(
        project_root,
        turn_modified_files,
        user_text=user_input,
    )
    if not remaining:
        return None
    fired.add("scratch_cleanup")
    if on_activity:
        on_activity("task-owned scratch remains - cleaning before finishing...")
    shown = ", ".join(f"`{finding.path}` ({finding.kind})" for finding in remaining[:8])
    return (
        "[SCRATCH CLEANUP] This turn created scratch that still exists: "
        f"{shown}. Inspect only these task-owned paths. Remove disposable files, "
        "or move durable output to its real product/private authority before "
        "claiming completion. Do not touch another session's scratch. If a listed "
        "file must remain for an active follow-up, state that reason explicitly."
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

    ``instruction`` is a corrective re-prompt when a closed board fails the full
    contract or when a model/procedure board tries to finalize with
    work still open. Manual gates, explicitly blocked work, and extension/goal
    boards keep their existing wait/terminal semantics. ``blocked_text`` is a
    terminal blocked answer when bounded recovery is exhausted.

    The counter is threaded through (``count`` in, updated count out) instead of held
    here, so it stays a ``run_turn`` local and this gate stays decoupled from the other
    counter-bearing gates.
    """
    if not (task_board and task_board.tasks):
        return ContractGateResult(None, count)
    if task_board.state == "cancelled":
        return ContractGateResult(None, count)
    open_rows = [
        row for row in list(getattr(task_board, "tasks", []) or [])
        if bool(getattr(row, "is_open", False))
    ]
    recovery_row = _recoverable_open_row(task_board, open_rows)
    open_recovery = recovery_row is not None
    if open_rows and not open_recovery:
        return ContractGateResult(None, count)
    persisted = load_persisted_tasks_for_contract(task_board)
    # A closing decision is a whole-board gold footer. Scoping to rows completed
    # in only this turn can hide evidence gaps left by an earlier continuation.
    _ = turn_initial_completed_ids
    contract_ok, contract_reasons, contract_instruction = enforce_contract_gate(
        task_board,
        persisted_tasks=persisted,
        board_closing=True,
    )
    if contract_ok:
        return ContractGateResult(None, count)
    if count < max_continuations:
        if on_activity:
            on_activity(f"contract gate blocked: {'; '.join(contract_reasons[:3])}")
        if open_recovery:
            contract_instruction = (
                "[TASKBOARD OPEN] The answer attempted to end while model/procedure "
                "rows are still open. Continue the actual active work, record real evidence, "
                "and call complete_task for each satisfied row before reporting. "
                "If missing user input prevents progress, use set_plan mode=pause with "
                "the blocker, then ask the question. If the user cancelled this work, "
                "use set_plan mode=cancel; cancellation is not task completion. "
                + contract_instruction
            )
        return ContractGateResult(contract_instruction, count + 1)
    settlement = None
    if recovery_row is not None:
        open_count = len(open_rows)
        row_word = "row" if open_count == 1 else "rows"
        settlement = ContractGateSettlement(
            task_id=str(getattr(recovery_row, "id", "") or ""),
            reason=(
                f"Bounded completion recovery ended with {open_count} taskboard {row_word} "
                "still open. This row remains incomplete and resumable."
            ),
        )
    blocked_text = _contract_gate_blocked_text(open_count=len(open_rows) if settlement else 0)
    if on_activity:
        on_activity(f"contract gate blocked after cap: {'; '.join(contract_reasons[:3])}")
    return ContractGateResult(None, count, blocked_text, settlement)


def _recoverable_open_row(task_board: Any, open_rows: list[Any]) -> Any | None:
    """Return the active/ready row eligible for model/procedure recovery.

    A current manual row must ask and wait, and a blocked row must be reportable
    instead of being forced through impossible completion. Private extension and
    goal owners keep their dedicated lifecycle gates.
    """
    if str(getattr(task_board, "plan_owner", "") or "") not in {"model", "procedure"}:
        return None
    if not open_rows or any(getattr(row, "status", "") == "blocked" for row in open_rows):
        return None
    current = getattr(task_board, "next_ready_task", lambda: None)()
    if current is None or str(getattr(current, "status", "") or "") not in {"active", "pending"}:
        return None
    if (
        str(getattr(current, "completion_gate", "") or "") == "manual"
        or str(getattr(current, "kind", "") or "") == "ask"
    ):
        return None
    return current


def _contract_gate_blocked_text(*, open_count: int = 0) -> str:
    if open_count:
        row_word = "row" if open_count == 1 else "rows"
        remain_word = "remains" if open_count == 1 else "remain"
        return (
            "I couldn't complete the active task within the bounded recovery attempts. "
            "It remains incomplete and resumable from its existing evidence; no task was "
            f"marked complete. Reason: {open_count} taskboard {row_word} {remain_word} open. "
            "Resume the blocked task in the next turn."
        )
    return (
        "I couldn't complete this work because its task evidence still failed the "
        "completion contract after bounded recovery. The work remains incomplete and "
        "resumable; no task was marked complete. Correct the saved task evidence or "
        "state, then continue."
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
