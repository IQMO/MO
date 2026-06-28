"""MO post-provider gate/action pipeline.

The product pipeline owns generic final-answer checks and taskboard closure.
Profile-owned local extensions may add private stop gates through
``core.local_extensions`` without shipping those rules in the product checkout.
"""
from __future__ import annotations

from .. import local_extensions
from .final_gates import (
    run_claim_gates,
    run_continuity_gate,
    run_contract_gate,
    run_done_claim_gate,
    run_lsp_diagnostics_gate,
    run_task_truth_gate,
    run_verify_edits_gate,
)
from ..tasking.task_board import record_snapshot
from ..agent.agent_utils import _emit_task_board_update

_CONTINUE = object()
GATE_CONTINUATION_MAX = 2


class _GateContext:
    """Mutable state threaded through the post-provider gate/action pipeline."""

    __slots__ = (
        "user_input", "content", "final_text", "reasoning", "notes",
        "task_board", "monitor", "on_activity", "on_board_update", "on_board_event",
        "final_gates_fired", "extension_gate_continuations",
        "contract_gate_continuations", "task_truth_continuations",
        "turn_initial_completed_ids", "turn_modified_files",
        "tool_call_counts", "tool_error_counts", "total_tool_calls",
        "boundary_report", "response",
        "turn_id", "session_id", "instance_id", "route_source", "surface",
    )


def _run_post_provider_pipeline(agent, ctx: _GateContext) -> str:
    for _name, _kind, fn in _POST_PROVIDER_PIPELINE:
        result = fn(agent, ctx)
        if result is _CONTINUE:
            return _CONTINUE
    return ctx.final_text


def _apply_extension_result(agent, ctx, result) -> object | None:
    if not result:
        return None
    if isinstance(result, str):
        agent.session.add_assistant(result)
        return _CONTINUE
    if not isinstance(result, dict):
        return None
    activity = result.get("activity")
    if activity and ctx.on_activity:
        ctx.on_activity(str(activity))
    if result.get("content") is not None:
        ctx.content = str(result.get("content") or "")
    if result.get("final_text") is not None:
        ctx.final_text = str(result.get("final_text") or "")
    if result.get("blocked_text"):
        ctx.final_text = str(result.get("blocked_text") or "")
        if ctx.task_board:
            record_snapshot(ctx.task_board, "blocked", state="blocked")
            _emit_task_board_update(
                ctx.task_board,
                update="blocked",
                on_board_update=ctx.on_board_update,
                on_board_event=ctx.on_board_event,
            )
        return None
    instruction = result.get("instruction")
    if instruction:
        agent.session.add_assistant(str(instruction))
        return _CONTINUE
    return _CONTINUE if result.get("continue") else None


def _pipeline_local_extension_raw_stop(agent, ctx):
    result = local_extensions.post_provider(agent, ctx)
    return _apply_extension_result(agent, ctx, result)


def _pipeline_critique(agent, ctx):
    if ctx.final_text:
        return None
    if ctx.on_activity:
        ctx.on_activity("finalizing response...")
    critique_result = agent._review_final_answer(ctx.content, monitor=ctx.monitor)
    ctx.final_text = critique_result.text
    ctx.reasoning = getattr(ctx.response, "reasoning_content", None) or getattr(ctx.response, "reasoning", None)
    return None


def _pipeline_memory_index(agent, ctx):
    """Always record turn memory and learning, regardless of pipeline path.

    Previously this was inside _pipeline_critique, which is skipped when
    ctx.final_text is already set (e.g. by a local extension). Moving it
    to its own post-critique pipeline step ensures foreground turns are
    indexed after final text exists.
    """
    ctx.notes = agent._record_turn_memory_and_learning(ctx.user_input, ctx.final_text)
    append_notes = getattr(agent, "_maybe_append_after_turn_notes", agent._append_after_turn_notes)
    ctx.final_text = append_notes(ctx.final_text, ctx.notes)
    return None


def _pipeline_board_finalization(agent, ctx):
    agent._activate_final_report_row(
        ctx.task_board,
        on_board_update=ctx.on_board_update,
        on_board_event=ctx.on_board_event,
    )
    if ctx.task_board and ctx.task_board.tasks:
        extension_decision = local_extensions.final_allows_task_close(
            agent,
            ctx.user_input,
            ctx.final_text,
        )
        result = _apply_extension_result(agent, ctx, extension_decision)
        if result is _CONTINUE:
            return _CONTINUE
        if not (isinstance(extension_decision, dict) and extension_decision.get("allow") is False):
            if agent._finalize_task_board_for_answer(ctx.task_board):
                local_extensions.after_task_board_close(agent, ctx.user_input, ctx.task_board, ctx.final_text)
                record_snapshot(ctx.task_board, "completed" if ctx.task_board.open_count() == 0 else "updated")
                _emit_task_board_update(
                    ctx.task_board,
                    update="completed" if ctx.task_board.open_count() == 0 else "updated",
                    on_board_update=ctx.on_board_update,
                    on_board_event=ctx.on_board_event,
                )
    return None


def _pipeline_contract_gate(agent, ctx):
    result = run_contract_gate(
        agent,
        ctx.task_board,
        ctx.user_input,
        ctx.turn_initial_completed_ids,
        count=ctx.contract_gate_continuations,
        max_continuations=GATE_CONTINUATION_MAX,
        on_activity=ctx.on_activity,
    )
    ctx.contract_gate_continuations = result.count
    if result.blocked_text:
        ctx.final_text = result.blocked_text
        if ctx.task_board:
            record_snapshot(ctx.task_board, "blocked", state="blocked")
            _emit_task_board_update(
                ctx.task_board,
                update="blocked",
                on_board_update=ctx.on_board_update,
                on_board_event=ctx.on_board_event,
            )
        return None
    if result.instruction:
        agent.session.add_assistant(result.instruction)
        return _CONTINUE
    return None


def _pipeline_consistency_boundary(agent, ctx):
    ctx.boundary_report = agent._run_consistency_boundary(
        "turn_final",
        user_text=ctx.user_input,
        final_text=ctx.final_text,
        learning_notes=ctx.notes,
        task_board=ctx.task_board,
    )
    return None


def _pipeline_task_truth(agent, ctx):
    result = run_task_truth_gate(
        agent,
        ctx.user_input,
        ctx.final_text,
        ctx.boundary_report,
        count=ctx.task_truth_continuations,
        max_continuations=GATE_CONTINUATION_MAX,
        on_activity=ctx.on_activity,
    )
    ctx.task_truth_continuations = result.count
    if result.blocked_text:
        ctx.final_text = result.blocked_text
        if ctx.task_board:
            record_snapshot(ctx.task_board, "blocked", state="blocked")
            _emit_task_board_update(
                ctx.task_board,
                update="blocked",
                on_board_update=ctx.on_board_update,
                on_board_event=ctx.on_board_event,
            )
        return None
    if result.instruction:
        agent.session.add_assistant(result.instruction)
        return _CONTINUE
    return None


def _pipeline_done_claim(agent, ctx):
    instruction = run_done_claim_gate(
        agent,
        ctx.boundary_report,
        fired=ctx.final_gates_fired,
        on_activity=ctx.on_activity,
    )
    if instruction:
        agent.session.add_assistant(instruction)
        return _CONTINUE
    return None


def _pipeline_verify_edits(agent, ctx):
    instruction = run_verify_edits_gate(
        agent,
        ctx.turn_modified_files,
        fired=ctx.final_gates_fired,
        on_activity=ctx.on_activity,
    )
    if instruction:
        agent.session.add_assistant(instruction)
        return _CONTINUE
    return None


def _pipeline_lsp_diagnostics(agent, ctx):
    instruction = run_lsp_diagnostics_gate(
        agent,
        ctx.turn_modified_files,
        fired=ctx.final_gates_fired,
        on_activity=ctx.on_activity,
    )
    if instruction:
        agent.session.add_assistant(instruction)
        return _CONTINUE
    return None


def _pipeline_continuity_gate(agent, ctx):
    instruction = run_continuity_gate(
        agent,
        ctx.user_input,
        ctx.final_text,
        fired=ctx.final_gates_fired,
        monitor=ctx.monitor,
        on_activity=ctx.on_activity,
    )
    if instruction:
        agent.session.add_assistant(instruction)
        return _CONTINUE
    return None


def _pipeline_claim_gates(agent, ctx):
    instruction = run_claim_gates(
        agent,
        ctx.final_text,
        ctx.tool_call_counts,
        fired=ctx.final_gates_fired,
        monitor=ctx.monitor,
        on_activity=ctx.on_activity,
    )
    if instruction:
        agent.session.add_assistant(instruction)
        return _CONTINUE
    return None


def _pipeline_local_extension_final(agent, ctx):
    result = local_extensions.final_gate(agent, ctx)
    return _apply_extension_result(agent, ctx, result)


_POST_PROVIDER_PIPELINE = [
    ("local_extension_raw_stop", "gate", _pipeline_local_extension_raw_stop),
    ("critique", "action", _pipeline_critique),
    ("continuity_gate", "gate", _pipeline_continuity_gate),
    ("memory_index", "action", _pipeline_memory_index),
    ("board_finalization", "action", _pipeline_board_finalization),
    ("contract_gate", "gate", _pipeline_contract_gate),
    ("consistency_boundary", "action", _pipeline_consistency_boundary),
    ("task_truth", "gate", _pipeline_task_truth),
    ("done_claim", "gate", _pipeline_done_claim),
    ("verify_edits", "gate", _pipeline_verify_edits),
    ("lsp_diagnostics", "gate", _pipeline_lsp_diagnostics),
    ("claim_gates", "gate", _pipeline_claim_gates),
    ("local_extension_final", "gate", _pipeline_local_extension_final),
]
