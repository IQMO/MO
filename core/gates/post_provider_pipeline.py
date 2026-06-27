"""MO post-provider gate/action pipeline.

Extracted from agent_turn.py to reduce module size and isolate gate logic.
Each function is pure (agent, ctx) -> _CONTINUE | None.
"""
from __future__ import annotations

import traceback
from typing import Any

from ..final_gates import (
    run_claim_gates,
    run_contract_gate,
    run_done_claim_gate,
    run_lsp_diagnostics_gate,
    run_owner_integrity_audit_reporting_gate,
    run_self_protocol_truth_gate,
    run_verify_edits_gate,
)
from ..owner_protocols import is_owner_integrity_audit_activation
from ..self_maintenance.devmode_closeout import (
    owner_comparison_continuation_instruction,
    owner_comparison_final_allows_stop,
    owner_dedup_continuation_instruction,
    owner_dedup_final_allows_stop,
    owner_interface_audit_continuation_instruction,
    owner_interface_audit_final_allows_stop,
    owner_maintenance_continuation_instruction,
    owner_maintenance_final_allows_stop,
    _owner_maintenance_terminal_prefix_text,
)
from ..self_maintenance.owner_integrity_audit_ground_truth import (
    normalize_owner_integrity_audit_report_text,
    owner_integrity_audit_source_corpus_count,
    reconcile_latest_owner_integrity_audit_report,
)
from ..tasking.task_board import TaskBoard, record_snapshot
from ..agent.agent_utils import _emit_task_board_update

# ---------------------------------------------------------------------------
# Post-provider gate pipeline — declarative gate/action sequence
# ---------------------------------------------------------------------------
# Each pipeline entry is (name, kind, fn) where fn(agent, ctx) returns
# _CONTINUE (the gate fired — continue the provider loop) or None (pass).
# Actions always return None.

_CONTINUE = object()

# Maximum corrective re-prompts for owner-protocol terminal-stop gates and the
# contract/self-protocol-truth gates. Contract-gate exhaustion becomes a blocked
# final answer instead of allowing an unresolved contradiction to close as clean.
PROTOCOL_STOP_GATE_MAX = 2


class _GateContext:
    """Mutable state threaded through the post-provider gate/action pipeline.
    
    Holds all the variables the gates and actions read or mutate, so the
    pipeline entries are pure functions of (agent, ctx).
    """
    __slots__ = (
        "user_input", "content", "final_text", "reasoning", "notes",
        "task_board", "monitor", "on_activity", "on_board_update", "on_board_event",
        "final_gates_fired", "protocol_stop_gate_continuations",
        "contract_gate_continuations", "self_protocol_truth_continuations",
        "owner_integrity_audit_reporting_truth_continuations",
        "turn_initial_completed_ids", "turn_modified_files",
        "tool_call_counts", "tool_error_counts",
        "owner_maintenance_active", "owner_comparison_active",
        "owner_interface_audit_active", "owner_dedup_active", "no_evidence",
        "devmode_monitor_path", "devmode_run_ids",
        "devmode_frozen_errs", "devmode_frozen_economy", "devmode_session_dir",
        "protocol_closeout_text",
        "total_tool_calls", "boundary_report", "response",
    )


def _run_post_provider_pipeline(agent, ctx: _GateContext) -> str:
    """Run the post-provider gate/action pipeline. Returns ctx.final_text on pass,
    or _CONTINUE when a gate fires (caller must ``continue`` the provider loop).
    """
    for _name, _kind, fn in _POST_PROVIDER_PIPELINE:
        result = fn(agent, ctx)
        if result is _CONTINUE:
            return _CONTINUE
    return ctx.final_text


# -- Pipeline entry functions (pure: (agent, ctx) -> _CONTINUE | None) ----------

def _pipeline_no_tool_evidence(agent, ctx):
    if ctx.no_evidence is not None:
        if ctx.on_activity:
            ctx.on_activity(ctx.no_evidence[0])
        agent.session.add_assistant(ctx.no_evidence[1])
        return _CONTINUE
    return None


def _protocol_stop_blocked(label: str, ctx) -> None:
    ctx.content = (
        f"[{label} BLOCKED] Cannot honestly close this protocol turn because the "
        "protocol stop gate still rejects the final report after bounded recovery. "
        "Fix the missing closeout evidence or terminal marker and rerun verification."
    )
    ctx.final_text = ""


def _pipeline_owner_interface_audit_stop(agent, ctx):
    if not owner_interface_audit_final_allows_stop(ctx.user_input, ctx.content):
        if ctx.protocol_stop_gate_continuations.get("owner_interface_audit", 0) < PROTOCOL_STOP_GATE_MAX:
            ctx.protocol_stop_gate_continuations["owner_interface_audit"] = ctx.protocol_stop_gate_continuations.get("owner_interface_audit", 0) + 1
            if ctx.on_activity:
                ctx.on_activity("continuing OWNER_INTERFACE_AUDIT...")
            agent.session.add_assistant(owner_interface_audit_continuation_instruction(ctx.user_input, ctx.content))
            return _CONTINUE
        if ctx.on_activity:
            ctx.on_activity("OWNER_INTERFACE_AUDIT stop-gate blocked after cap")
        _protocol_stop_blocked("OWNER_INTERFACE_AUDIT", ctx)
    elif ctx.owner_interface_audit_active:
        ctx.protocol_closeout_text = ctx.content
    return None


def _pipeline_owner_comparison_stop(agent, ctx):
    if not owner_comparison_final_allows_stop(ctx.user_input, ctx.content):
        if ctx.protocol_stop_gate_continuations.get("owner_comparison", 0) < PROTOCOL_STOP_GATE_MAX:
            ctx.protocol_stop_gate_continuations["owner_comparison"] = ctx.protocol_stop_gate_continuations.get("owner_comparison", 0) + 1
            if ctx.on_activity:
                ctx.on_activity("continuing OWNER_COMPARISON...")
            agent.session.add_assistant(owner_comparison_continuation_instruction(ctx.user_input, ctx.content))
            return _CONTINUE
        if ctx.on_activity:
            ctx.on_activity("OWNER_COMPARISON stop-gate blocked after cap")
        _protocol_stop_blocked("OWNER_COMPARISON", ctx)
    elif ctx.owner_comparison_active:
        ctx.protocol_closeout_text = ctx.content
    return None


def _pipeline_owner_dedup_stop(agent, ctx):
    if not owner_dedup_final_allows_stop(ctx.user_input, ctx.content):
        if ctx.protocol_stop_gate_continuations.get("owner_dedup", 0) < PROTOCOL_STOP_GATE_MAX:
            ctx.protocol_stop_gate_continuations["owner_dedup"] = ctx.protocol_stop_gate_continuations.get("owner_dedup", 0) + 1
            if ctx.on_activity:
                ctx.on_activity("continuing OWNER_DEDUP...")
            agent.session.add_assistant(owner_dedup_continuation_instruction(ctx.user_input, ctx.content))
            return _CONTINUE
        if ctx.on_activity:
            ctx.on_activity("OWNER_DEDUP stop-gate blocked after cap")
        _protocol_stop_blocked("OWNER_DEDUP", ctx)
    elif ctx.owner_dedup_active:
        ctx.protocol_closeout_text = ctx.content
    return None


def _pipeline_devmode_economy(agent, ctx):
    """Write the economy ledger BEFORE the OWNER_MAINTENANCE closeout gate evaluates."""
    if ctx.owner_maintenance_active:
        agent._write_devmode_economy_record()
        agent._reconcile_devmode_summary_marker(ctx.content)
        ctx.devmode_run_ids = set(getattr(agent, "_devmode_run_session_ids", None) or set())
        ctx.devmode_frozen_errs = getattr(agent, "_devmode_closeout_frozen_errors", ctx.devmode_frozen_errs)
        ctx.devmode_frozen_economy = getattr(agent, "_devmode_closeout_frozen_economy", ctx.devmode_frozen_economy)
        ctx.devmode_session_dir = getattr(agent, "_active_devmode_session_dir", ctx.devmode_session_dir)
    return None


def _pipeline_owner_maintenance_stop(agent, ctx):
    if not owner_maintenance_final_allows_stop(
        ctx.user_input, ctx.content,
        monitor_path=ctx.devmode_monitor_path,
        session_ids=ctx.devmode_run_ids or None,
        frozen_error_count=ctx.devmode_frozen_errs,
        frozen_economy=ctx.devmode_frozen_economy,
        session_dir=ctx.devmode_session_dir,
    ):
        if ctx.protocol_stop_gate_continuations.get("owner_maintenance", 0) < PROTOCOL_STOP_GATE_MAX:
            ctx.protocol_stop_gate_continuations["owner_maintenance"] = ctx.protocol_stop_gate_continuations.get("owner_maintenance", 0) + 1
            if ctx.on_activity:
                ctx.on_activity("continuing OWNER_MAINTENANCE...")
            agent.session.add_assistant(owner_maintenance_continuation_instruction(
                ctx.user_input, ctx.content,
                monitor_path=ctx.devmode_monitor_path,
                session_ids=ctx.devmode_run_ids or None,
                frozen_error_count=ctx.devmode_frozen_errs,
                frozen_economy=ctx.devmode_frozen_economy,
                session_dir=ctx.devmode_session_dir,
            ))
            return _CONTINUE
        if ctx.on_activity:
            ctx.on_activity("OWNER_MAINTENANCE stop-gate blocked after cap")
        _protocol_stop_blocked("OWNER_MAINTENANCE", ctx)
    elif ctx.owner_maintenance_active:
        ctx.protocol_closeout_text = ctx.content
    return None


def _pipeline_critique(agent, ctx):
    """Finalize the response text (secrets-only critique) and record learning."""
    if ctx.on_activity:
        ctx.on_activity("finalizing response...")
    critique_result = agent._review_final_answer(ctx.content, monitor=ctx.monitor)
    ctx.final_text = critique_result.text
    ctx.reasoning = getattr(ctx.response, "reasoning_content", None) or getattr(ctx.response, "reasoning", None)
    ctx.notes = agent._record_turn_memory_and_learning(ctx.user_input, ctx.final_text)
    ctx.final_text = agent._append_after_turn_notes(ctx.final_text, ctx.notes)
    return None


def _pipeline_board_finalization(agent, ctx):
    """Activate final report row, finalize self-protocol and task boards."""
    agent._activate_final_report_row(ctx.task_board, on_board_update=ctx.on_board_update, on_board_event=ctx.on_board_event)
    if ctx.task_board and ctx.task_board.tasks:
        closeout_text = ctx.protocol_closeout_text or ctx.final_text
        protocol_closed = agent._finalize_self_protocol_task_board_for_answer(
            ctx.user_input,
            closeout_text,
            ctx.task_board,
            monitor_path=ctx.devmode_monitor_path,
            session_ids=ctx.devmode_run_ids or None,
            frozen_error_count=ctx.devmode_frozen_errs,
            frozen_economy=ctx.devmode_frozen_economy,
            session_dir=ctx.devmode_session_dir,
        )
        if protocol_closed or (not protocol_closed and agent._finalize_task_board_for_answer(ctx.task_board)):
            record_snapshot(ctx.task_board, "completed" if ctx.task_board.open_count() == 0 else "updated")
            _emit_task_board_update(ctx.task_board, update="completed" if ctx.task_board.open_count() == 0 else "updated", on_board_update=ctx.on_board_update, on_board_event=ctx.on_board_event)
    return None


def _pipeline_contract_gate(agent, ctx):
    result = run_contract_gate(
        agent, ctx.task_board, ctx.user_input, ctx.turn_initial_completed_ids,
        count=ctx.contract_gate_continuations, max_continuations=PROTOCOL_STOP_GATE_MAX,
        on_activity=ctx.on_activity,
    )
    ctx.contract_gate_continuations = result.count
    if result.blocked_text:
        ctx.final_text = result.blocked_text
        if ctx.task_board:
            record_snapshot(ctx.task_board, "blocked", state="blocked")
            _emit_task_board_update(ctx.task_board, update="blocked", on_board_update=ctx.on_board_update, on_board_event=ctx.on_board_event)
        return None
    if result.instruction:
        agent.session.add_assistant(result.instruction)
        return _CONTINUE
    return None


def _pipeline_consistency_boundary(agent, ctx):
    ctx.boundary_report = agent._run_consistency_boundary(
        "turn_final", user_text=ctx.user_input, final_text=ctx.final_text,
        learning_notes=ctx.notes, task_board=ctx.task_board,
    )
    return None


def _pipeline_self_protocol_truth(agent, ctx):
    result = run_self_protocol_truth_gate(
        agent, ctx.user_input, ctx.final_text, ctx.boundary_report,
        count=ctx.self_protocol_truth_continuations, max_continuations=PROTOCOL_STOP_GATE_MAX,
        on_activity=ctx.on_activity,
    )
    ctx.self_protocol_truth_continuations = result.count
    if result.blocked_text:
        ctx.final_text = result.blocked_text
        if ctx.task_board:
            record_snapshot(ctx.task_board, "blocked", state="blocked")
            _emit_task_board_update(ctx.task_board, update="blocked", on_board_update=ctx.on_board_update, on_board_event=ctx.on_board_event)
        return None
    if result.instruction:
        agent.session.add_assistant(result.instruction)
        return _CONTINUE
    return None


def _pipeline_done_claim(agent, ctx):
    _done_claim_instruction = run_done_claim_gate(
        agent, ctx.boundary_report, fired=ctx.final_gates_fired, on_activity=ctx.on_activity,
    )
    if _done_claim_instruction:
        agent.session.add_assistant(_done_claim_instruction)
        return _CONTINUE
    return None


def _pipeline_verify_edits(agent, ctx):
    _verify_instr = run_verify_edits_gate(
        agent, ctx.turn_modified_files, fired=ctx.final_gates_fired, on_activity=ctx.on_activity,
    )
    if _verify_instr:
        agent.session.add_assistant(_verify_instr)
        return _CONTINUE
    return None


def _pipeline_lsp_diagnostics(agent, ctx):
    _lsp_instr = run_lsp_diagnostics_gate(
        agent, ctx.turn_modified_files, fired=ctx.final_gates_fired, on_activity=ctx.on_activity,
    )
    if _lsp_instr:
        agent.session.add_assistant(_lsp_instr)
        return _CONTINUE
    return None


def _pipeline_iam_normalize(agent, ctx):
    """Normalize quantitative truth BEFORE the IAM reporting gate evaluates."""
    if is_owner_integrity_audit_activation(ctx.user_input):
        _iam_tool_calls = sum((ctx.tool_call_counts or {}).values())
        _iam_tool_errors = sum((ctx.tool_error_counts or {}).values())
        _iam_corpus = owner_integrity_audit_source_corpus_count()
        ctx.final_text = normalize_owner_integrity_audit_report_text(
            ctx.final_text, tool_calls=_iam_tool_calls,
            tool_errors=_iam_tool_errors, corpus=_iam_corpus,
        )
        try:
            reconcile_latest_owner_integrity_audit_report(
                tool_calls=_iam_tool_calls, tool_errors=_iam_tool_errors,
                corpus=_iam_corpus, report_text=ctx.final_text,
            )
        except Exception:
            traceback.print_exc()
    return None


def _pipeline_iam_reporting(agent, ctx):
    _owner_integrity_audit_reporting_instruction = run_owner_integrity_audit_reporting_gate(
        ctx.user_input, ctx.final_text, ctx.tool_call_counts, ctx.tool_error_counts,
        fired=ctx.final_gates_fired,
        continuations=ctx.owner_integrity_audit_reporting_truth_continuations,
        max_continuations=3, monitor=ctx.monitor, on_activity=ctx.on_activity,
    )
    if _owner_integrity_audit_reporting_instruction:
        ctx.owner_integrity_audit_reporting_truth_continuations += 1
        agent.session.add_assistant(_owner_integrity_audit_reporting_instruction)
        return _CONTINUE
    return None


def _pipeline_claim_gates(agent, ctx):
    _claim_instruction = run_claim_gates(
        agent, ctx.final_text, ctx.tool_call_counts,
        fired=ctx.final_gates_fired, monitor=ctx.monitor, on_activity=ctx.on_activity,
    )
    if _claim_instruction:
        agent.session.add_assistant(_claim_instruction)
        return _CONTINUE
    return None


def _owner_maintenance_open_count(task_board) -> int:
    if not task_board:
        return 0
    try:
        return int(task_board.open_count())
    except Exception:
        tasks = list(getattr(task_board, "tasks", []) or [])
        return sum(1 for task in tasks if str(getattr(task, "status", "") or "") in {"pending", "active", "blocked"})


def _mark_owner_maintenance_blocked(agent, ctx) -> None:
    if ctx.task_board:
        try:
            ctx.task_board.state = "blocked"
        except Exception:
            pass
        record_snapshot(ctx.task_board, "blocked", state="blocked")
        _emit_task_board_update(
            ctx.task_board,
            update="blocked",
            on_board_update=ctx.on_board_update,
            on_board_event=ctx.on_board_event,
        )
    try:
        agent._reconcile_devmode_summary_marker(ctx.final_text)
    except Exception:
        traceback.print_exc()
    try:
        ctx.boundary_report = agent._run_consistency_boundary(
            "turn_final",
            user_text=ctx.user_input,
            final_text=ctx.final_text,
            learning_notes=ctx.notes,
            task_board=ctx.task_board,
        )
    except Exception:
        traceback.print_exc()


def _pipeline_owner_maintenance_terminal_truth(agent, ctx):
    """Last invariant: owner-maintenance cannot leave a COMPLETE terminal over an open board."""
    if not ctx.owner_maintenance_active:
        return None
    terminal = _owner_maintenance_terminal_prefix_text(ctx.final_text) or ""
    open_count = _owner_maintenance_open_count(ctx.task_board)
    if terminal.startswith("[OWNER_MAINTENANCE COMPLETE]") and open_count > 0:
        ctx.final_text = (
            "[OWNER_MAINTENANCE BLOCKED] Runtime rejected an invalid completion: "
            f"the taskboard still has {open_count} open row(s). Continue from the active "
            "row; this run is not complete."
        )
        _mark_owner_maintenance_blocked(agent, ctx)
        return None
    if terminal.startswith("[OWNER_MAINTENANCE BLOCKED]"):
        _mark_owner_maintenance_blocked(agent, ctx)
        return None
    if ctx.final_text.lstrip().startswith(("[SELF PROTOCOL TRUTH BLOCKED]", "[TASKBOARD CONTRACT BLOCKED]")):
        ctx.final_text = (
            "[OWNER_MAINTENANCE BLOCKED] Runtime could not honestly close the owner-maintenance turn.\n\n"
            + ctx.final_text
        )
        _mark_owner_maintenance_blocked(agent, ctx)
    return None


# -- The pipeline: ordered list of (name, kind, entry_fn) -----------------------

_POST_PROVIDER_PIPELINE = [
    ("no_tool_evidence", "gate", _pipeline_no_tool_evidence),
    ("owner_interface_audit_stop", "gate", _pipeline_owner_interface_audit_stop),
    ("owner_comparison_stop", "gate", _pipeline_owner_comparison_stop),
    ("owner_dedup_stop", "gate", _pipeline_owner_dedup_stop),
    ("devmode_economy", "action", _pipeline_devmode_economy),
    ("owner_maintenance_stop", "gate", _pipeline_owner_maintenance_stop),
    ("critique", "action", _pipeline_critique),
    ("board_finalization", "action", _pipeline_board_finalization),
    ("contract_gate", "gate", _pipeline_contract_gate),
    ("consistency_boundary", "action", _pipeline_consistency_boundary),
    ("self_protocol_truth", "gate", _pipeline_self_protocol_truth),
    ("done_claim", "gate", _pipeline_done_claim),
    ("verify_edits", "gate", _pipeline_verify_edits),
    ("lsp_diagnostics", "gate", _pipeline_lsp_diagnostics),
    ("iam_normalize", "action", _pipeline_iam_normalize),
    ("iam_reporting", "gate", _pipeline_iam_reporting),
    ("claim_gates", "gate", _pipeline_claim_gates),
    ("owner_maintenance_terminal_truth", "action", _pipeline_owner_maintenance_terminal_truth),
]


# Re-exported for backward compat (test imports)
def _no_tool_evidence_continuation(
    *,
    owner_maintenance_active: bool,
    owner_comparison_active: bool,
    owner_interface_audit_active: bool,
    total_tool_calls: int,
    devmode_taskboard_completed: bool,
) -> tuple[str, str] | None:
    """Owner-protocol no-tool-evidence gate.

    When an owner protocol is active and the turn produced ZERO tool calls, even a
    correctly-prefixed completion is fabrication — there is no real evidence behind it.
    Returns ``(on_activity label, continuation message)`` to inject before re-looping,
    or ``None`` to proceed. OWNER_MAINTENANCE is exempt once its taskboard is already completed
    (a clean closeout turn legitimately needs no new tools). Precedence matches the
    original inline order: OWNER_MAINTENANCE, then OWNER_COMPARISON, then OWNER_INTERFACE_AUDIT.
    """
    if total_tool_calls != 0:
        return None
    if owner_maintenance_active and not devmode_taskboard_completed:
        return (
            "OWNER_MAINTENANCE: no tool evidence — continuing...",
            "[OWNER_MAINTENANCE AUTONOMY] No tool evidence gathered this turn. "
            "You must call tools (read_file, shell, grep, etc.) to produce real evidence before claiming completion. "
            "Do not fabricate reports. Run the protocol steps with actual tools.",
        )
    if owner_comparison_active:
        return (
            "OWNER_COMPARISON: no tool evidence - continuing...",
            "[OWNER_COMPARISON CONTINUATION] No tool evidence gathered this turn. "
            "Read the OWNER_COMPARISON protocol, capture source roles, inspect structured evidence surfaces, "
            "and build the comparison matrix from actual files/traces before any closeout.",
        )
    if owner_interface_audit_active:
        return (
            "OWNER_INTERFACE_AUDIT: no tool evidence - continuing...",
            "[OWNER_INTERFACE_AUDIT CONTINUATION] No tool evidence gathered this turn. "
            "Read the OWNER_INTERFACE_AUDIT protocol and inspect the real interface code (read_file on "
            "interface/*.py) and live UX behavior before any UX-audit closeout.",
        )
    return None
