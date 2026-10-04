"""MO post-provider gate/action pipeline.

The product pipeline owns generic final-answer checks. Task rows close only
through explicit evidence-backed task transitions.
Profile-owned local extensions may add private stop gates through
``core.local_extensions`` without shipping those rules in the product checkout.
"""
from __future__ import annotations

from .. import local_extensions
from ..runtime.backend_monitor import monitor_phase
from .final_gates import (
    ContractGateSettlement,
    run_claim_gates,
    run_continuity_gate,
    run_contract_gate,
    run_done_claim_gate,
    run_lsp_diagnostics_gate,
    run_scratch_cleanup_gate,
    run_task_truth_gate,
    run_verify_edits_gate,
)
_CONTINUE = object()
GATE_CONTINUATION_MAX = 2


class _GateContext:
    """Mutable state threaded through the post-provider gate/action pipeline."""

    __slots__ = (
        "user_input", "content", "final_text", "reasoning", "notes",
        "task_board", "monitor", "on_activity", "on_board_event",
        "on_operator_visual", "continuation_gate", "continuation_tools",
        "final_gates_fired", "extension_gate_continuations",
        "verification_results", "verification_selected_roots", "verification_disclosure",
        "contract_gate_continuations", "task_truth_continuations",
        "turn_initial_completed_ids", "turn_modified_files",
        "tool_call_counts", "tool_error_counts", "total_tool_calls",
        "tool_sequence", "task_transitions", "desktop_action_continuations",
        "phone_action_continuations", "board_open_continuations",
        "boundary_report", "project_rule_disclosure", "response",
        "turn_id", "session_id", "instance_id", "route_source", "surface",
    )


def _run_post_provider_pipeline(agent, ctx: _GateContext) -> str:
    for _name, _kind, fn in _POST_PROVIDER_PIPELINE:
        if getattr(ctx.task_board, "state", "") == "cancelled" and _name in {
            "board_completion", "extrathink_reaudit", "desktop_completion",
            "phone_completion", "contract_gate", "verify_edits", "lsp_diagnostics",
            "capture_gate", "board_close_hooks",
        }:
            # Cancellation must not restart work via completion recovery.
            # Text policy, claim truth, cleanup and persistence still run.
            continue
        monitor = getattr(ctx, "monitor", None)
        with monitor_phase("post_provider", monitor=monitor, operation=_name):
            result = fn(agent, ctx)
        if result is _CONTINUE:
            ctx.continuation_gate = _name
            if monitor:
                monitor.emit("session_event", {
                    "kind": "post_provider_auto_continuation",
                    "gate": _name,
                    "turn_id": ctx.turn_id,
                    "user_turn_id": ctx.turn_id,
                    "session_id": ctx.session_id,
                    "surface": ctx.surface,
                })
            return _CONTINUE
        if isinstance(result, str):
            # Terminal rejection must not reach later extensions or learning.
            ctx.final_text = result
            break
    _emit_final_gates(ctx)
    return ctx.final_text


def _emit_final_gates(ctx: _GateContext) -> None:
    """Record which final gates fired once the pipeline completes.

    Turn-context flags are monitored at turn start, but gate outcomes were
    invisible, so gate driven-ness (extrathink re-audit, capture nudge, claim
    gates) could not be proven from production logs. Observability only —
    never breaks the turn."""
    try:
        from ..runtime.backend_monitor import get_monitor

        monitor = get_monitor()
        if monitor:
            monitor.emit("final_gates", {"fired": sorted(ctx.final_gates_fired)})
    except Exception:
        pass


def _apply_extension_result(agent, ctx, result) -> object | None:
    if not result:
        return None
    if isinstance(result, str):
        agent.session.add_assistant(result)
        return _CONTINUE
    if not isinstance(result, dict):
        return None
    if "allowed_tools" in result:
        raw_tools = result.get("allowed_tools")
        ctx.continuation_tools = tuple(dict.fromkeys(
            str(name).strip()
            for name in (raw_tools if isinstance(raw_tools, (list, tuple, set, frozenset)) else ())
            if str(name).strip()
        ))
    activity = result.get("activity")
    if activity and ctx.on_activity:
        ctx.on_activity(str(activity))
    if result.get("content") is not None:
        ctx.content = str(result.get("content") or "")
    if result.get("final_text") is not None:
        ctx.final_text = str(result.get("final_text") or "")
    if result.get("blocked_text"):
        ctx.final_text = str(result.get("blocked_text") or "")
        return ctx.final_text
    instruction = result.get("instruction")
    if instruction:
        agent.session.add_assistant(str(instruction))
        return _CONTINUE
    return _CONTINUE if result.get("continue") else None


def _pipeline_local_extension_raw_stop(agent, ctx):
    result = local_extensions.post_provider(agent, ctx)
    return _apply_extension_result(agent, ctx, result)


def _apply_completion_result(agent, ctx, result, attr_name):
    """Apply one completion gate's bounded result to the pipeline context.

    Every completion gate returns the same (count, instruction, blocked_text)
    shape; this is the single place that advances the gate's continuation ledger
    and turns an instruction/block into a continuation/terminal text.
    """
    setattr(ctx, attr_name, result.count)
    if result.instruction:
        agent.session.add_assistant(result.instruction)
        return _CONTINUE
    if result.blocked_text:
        ctx.final_text = result.blocked_text
    return None


def _pipeline_project_rules(agent, ctx):
    """Recheck every reviewed project-rule chain before accepting output."""
    primary = getattr(agent, "_project_rule_snapshot", None)
    snapshot_map = getattr(agent, "_project_rule_snapshots", None)
    snapshots = dict(snapshot_map) if isinstance(snapshot_map, dict) else {}
    if primary is not None:
        from ..context.project_context import project_rule_snapshot_key
        snapshots.setdefault(project_rule_snapshot_key(primary), primary)
    if not snapshots:
        return None

    from dataclasses import replace
    from ..context.project_context import (
        project_rule_snapshot_key, project_rules_changed,
        render_project_context_files, render_project_rule_status,
        resolve_project_rules,
    )

    updated_snapshots = {}
    changed_snapshots = []
    disclosures = []
    incomplete = []
    for old_key, snapshot in snapshots.items():
        current = resolve_project_rules(snapshot.scope_path)
        changed = project_rules_changed(snapshot, current)
        updated = snapshot
        if changed:
            updated = replace(
                current,
                created=snapshot.created,
                creation_error=snapshot.creation_error,
                rechecks=snapshot.rechecks + 1,
            )
            changed_snapshots.append(updated)
        updated_snapshots[project_rule_snapshot_key(updated)] = updated
        if primary is snapshot or (
            primary is not None and project_rule_snapshot_key(primary) == old_key
        ):
            agent._project_rule_snapshot = updated
        if updated.rechecks > GATE_CONTINUATION_MAX:
            incomplete.append(updated)
        elif updated.rechecks or updated.created or updated.creation_error or updated.unreadable:
            status = render_project_rule_status(updated)
            disclosures.append(
                f"{updated.project_root}: {status}" if len(snapshots) > 1 else status
            )

    agent._project_rule_snapshots = updated_snapshots
    ctx.project_rule_disclosure = "\n".join(disclosures)
    if incomplete:
        roots = ", ".join(str(snapshot.project_root) for snapshot in incomplete)
        ctx.project_rule_disclosure = (
            "Project rules: kept changing; final rule review is incomplete "
            f"for {roots}."
        )
        ctx.final_text = ctx.project_rule_disclosure
        return None
    if changed_snapshots:
        ctx.final_gates_fired.add("project_rules")
        blocks = []
        for snapshot in changed_snapshots:
            blocks.append(
                f"### Target project: {snapshot.project_root}\n"
                + (
                    render_project_context_files(snapshot.contents)
                    or "No readable project rules remain."
                )
            )
        agent.session.add_assistant(
            "[PROJECT RULE RECHECK] An applicable AGENTS.md contract changed during this work. "
            "The current sources follow; they supersede the earlier snapshot. Reconcile the "
            "work and final report with them, read any omitted files, and assess whether the "
            "requested work requires a rule update. Do not edit rules without authorization.\n\n"
            + "\n\n".join(blocks)
            + ("\n\n" + ctx.project_rule_disclosure if ctx.project_rule_disclosure else "")
        )
        if ctx.on_activity:
            ctx.on_activity("project rules changed: rechecking before finalizing...")
        return _CONTINUE
    return None


def _pipeline_board_completion(agent, ctx):
    from .board_completion import run_board_completion_gate

    result = run_board_completion_gate(
        agent,
        ctx.user_input,
        getattr(ctx, "tool_sequence", []),
        count=getattr(ctx, "board_open_continuations", 0),
        on_activity=ctx.on_activity,
        monitor=ctx.monitor,
    )
    return _apply_completion_result(agent, ctx, result, "board_open_continuations")


def _pipeline_extrathink_reaudit(agent, ctx):
    """One-shot re-audit pass when the operator explicitly used ``extrathink``.

    Fires once (before critique/finalization): inject a re-audit challenge and force
    another provider pass so MO re-verifies its work against live state instead of
    finalizing on first answer. Bounded to a single pass — no loop — to keep cost
    predictable. No-op for every turn that wasn't armed.
    """
    if not bool(getattr(agent, "_extrathink_active", False)):
        return None
    if "extrathink_reaudit" in ctx.final_gates_fired:
        return None
    ctx.final_gates_fired.add("extrathink_reaudit")
    agent.session.add_assistant(
        "[EXTRATHINK RE-AUDIT] Before finalizing, re-audit this turn against live state: "
        "re-verify every claim you made (files, tests, runtime — not memory), re-check the "
        "conditions, edge-cases, and tests you may have skipped, and confirm nothing the "
        "request implied was missed. If anything is unverified or wrong, fix it and continue. "
        "Only finalize once everything is verified."
    )
    if ctx.on_activity:
        ctx.on_activity("extrathink: re-auditing…")
    return _CONTINUE


def _pipeline_desktop_completion(agent, ctx):
    from .desktop_completion import run_desktop_completion_gate

    result = run_desktop_completion_gate(
        ctx.route_source,
        getattr(ctx, "tool_sequence", []),
        assistant_text=str(getattr(ctx, "content", "") or ""),
        count=getattr(ctx, "desktop_action_continuations", 0),
        monitor=ctx.monitor,
    )
    return _apply_completion_result(agent, ctx, result, "desktop_action_continuations")


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
    """Record memory and learning for the accepted terminal answer only.

    This step stays separate from critique so extension-provided terminal text
    is included, but it runs after every continuation gate. Rejected drafts must
    not become recall evidence or trigger learning side effects. Blocked terminal
    answers still reach this step because they do not request continuation.
    """
    disclosures = [
        str(getattr(ctx, "verification_disclosure", "") or "").strip(),
        str(getattr(ctx, "project_rule_disclosure", "") or "").strip(),
    ]
    for disclosure in disclosures:
        if disclosure and disclosure not in ctx.final_text:
            separator = "\n\n" if str(ctx.final_text or "").strip() else ""
            ctx.final_text = str(ctx.final_text or "").rstrip() + separator + disclosure
    ctx.notes = agent._record_turn_memory_and_learning(ctx.user_input, ctx.final_text, on_activity=ctx.on_activity)
    append_notes = getattr(agent, "_maybe_append_after_turn_notes", agent._append_after_turn_notes)
    ctx.final_text = append_notes(ctx.final_text, ctx.notes)
    return None


def _pipeline_board_close_hooks(agent, ctx):
    """Notify profile extensions after real work closed in this turn.

    Generic product code never mutates task truth from final prose. The retained
    hooks observe an already evidence-closed board and may still request a
    bounded provider continuation for extension-owned closeout checks.
    """
    board = ctx.task_board
    if not (board and board.tasks) or int(board.open_count()) != 0:
        return None
    initial_completed = set(ctx.turn_initial_completed_ids or ())
    closed_now = any(
        str(getattr(row, "id", "") or "") not in initial_completed
        and str(getattr(row, "status", "") or "") == "completed"
        for row in board.tasks
    )
    if not closed_now:
        return None
    extension_decision = local_extensions.final_allows_task_close(
        agent,
        ctx.user_input,
        ctx.final_text,
    )
    result = _apply_extension_result(agent, ctx, extension_decision)
    if result is not None:
        return result
    if not (isinstance(extension_decision, dict) and extension_decision.get("allow") is False):
        local_extensions.after_task_board_close(agent, ctx.user_input, board, ctx.final_text)
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
            settlement = getattr(result, "settlement", None)
            if isinstance(settlement, ContractGateSettlement):
                ctx.task_board.block(settlement.task_id, settlement.reason)
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
    already_checked = "verify_edits" in ctx.final_gates_fired
    result = run_verify_edits_gate(
        agent,
        ctx.turn_modified_files,
        fired=ctx.final_gates_fired,
        verification_results=getattr(ctx, "verification_results", None),
        verification_selected_roots=getattr(ctx, "verification_selected_roots", None),
        on_activity=ctx.on_activity,
    )
    if not already_checked:
        ctx.verification_disclosure = result.disclosure or ""
    if result.instruction:
        agent.session.add_assistant(result.instruction)
        return _CONTINUE
    return None


def _pipeline_lsp_diagnostics(agent, ctx):
    instruction = run_lsp_diagnostics_gate(
        agent,
        ctx.turn_modified_files,
        fired=ctx.final_gates_fired,
        on_activity=ctx.on_activity,
        monitor=ctx.monitor,
    )
    if instruction:
        agent.session.add_assistant(instruction)
        return _CONTINUE
    return None


def _pipeline_phone_completion(agent, ctx):
    from .phone_completion import run_phone_completion_gate

    result = run_phone_completion_gate(
        agent,
        ctx.user_input,
        ctx.route_source,
        getattr(ctx, "tool_sequence", []),
        count=getattr(ctx, "phone_action_continuations", 0),
        on_activity=ctx.on_activity,
    )
    return _apply_completion_result(agent, ctx, result, "phone_action_continuations")


def _pipeline_scratch_cleanup(agent, ctx):
    instruction = run_scratch_cleanup_gate(
        agent,
        ctx.user_input,
        ctx.final_text,
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
    result = run_claim_gates(
        agent,
        ctx.final_text,
        ctx.tool_call_counts,
        fired=ctx.final_gates_fired,
        material_work=bool(ctx.turn_modified_files) or bool(ctx.task_board and getattr(ctx.task_board, "tasks", None)),
        user_input=ctx.user_input,
        tool_sequence=ctx.tool_sequence,
        task_transitions=getattr(ctx, "task_transitions", None),
        turn_modified_files=ctx.turn_modified_files,
        monitor=ctx.monitor,
        on_activity=ctx.on_activity,
    )
    if result is None:
        return None
    if result.instruction:
        agent.session.add_assistant(result.instruction)
        return _CONTINUE
    return result.blocked_text


def _pipeline_capture_gate(agent, ctx):
    from .capture_detection import run_capture_gate
    result = run_capture_gate(
        agent,
        ctx.user_input,
        ctx.tool_call_counts,
        fired=ctx.final_gates_fired,
        monitor=ctx.monitor,
        on_activity=ctx.on_activity,
    )
    if result.instruction:
        agent.session.add_assistant(result.instruction)
        return _CONTINUE
    if result.blocked_text:
        ctx.final_text = result.blocked_text
    return None


def _pipeline_documentation_gate(agent, ctx):
    from .documentation_gate import run_documentation_gate

    result = run_documentation_gate(
        agent,
        ctx.turn_modified_files,
        fired=ctx.final_gates_fired,
        on_activity=ctx.on_activity,
        monitor=ctx.monitor,
    )
    if result.instruction:
        agent.session.add_assistant(result.instruction)
        return _CONTINUE
    return None


def _pipeline_local_extension_final(agent, ctx):
    result = local_extensions.final_gate(agent, ctx)
    return _apply_extension_result(agent, ctx, result)


_POST_PROVIDER_PIPELINE = [
    ("local_extension_raw_stop", "gate", _pipeline_local_extension_raw_stop),
    ("project_rules", "gate", _pipeline_project_rules),
    ("board_completion", "gate", _pipeline_board_completion),
    ("extrathink_reaudit", "gate", _pipeline_extrathink_reaudit),
    ("desktop_completion", "gate", _pipeline_desktop_completion),
    ("phone_completion", "gate", _pipeline_phone_completion),
    ("critique", "action", _pipeline_critique),
    ("continuity_gate", "gate", _pipeline_continuity_gate),
    ("scratch_cleanup", "gate", _pipeline_scratch_cleanup),
    ("contract_gate", "gate", _pipeline_contract_gate),
    ("consistency_boundary", "action", _pipeline_consistency_boundary),
    ("task_truth", "gate", _pipeline_task_truth),
    ("done_claim", "gate", _pipeline_done_claim),
    ("verify_edits", "gate", _pipeline_verify_edits),
    ("lsp_diagnostics", "gate", _pipeline_lsp_diagnostics),
    ("claim_gates", "gate", _pipeline_claim_gates),
    ("documentation_gate", "gate", _pipeline_documentation_gate),
    ("local_extension_final", "gate", _pipeline_local_extension_final),
    # Extension closeout checks observe an already evidence-closed board.
    ("board_close_hooks", "gate", _pipeline_board_close_hooks),
    ("memory_index", "action", _pipeline_memory_index),
]
