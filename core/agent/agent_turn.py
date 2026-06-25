"""MO agent turn-execution mixin.

Core turn loop, provider calls, and per-turn context assembly. The per-tool-call
dispatch phase and provider error/turn-limit recovery live in
agent_turn_dispatch.py and agent_turn_recovery.py.
"""

import json
import os
import traceback
from datetime import datetime

from ..provider.provider import (
    clean_provider_error,
    fallback_reason,
    is_context_overflow_error,
    is_rate_limit_error,
)
from ..provider.provider_capacity import get_capacity
from ..provider.provider_audit import append_provider_audit
from ..sandbox import guard_tool_call
from ..tasking.task_board import TaskBoard, record_snapshot
from ..backend_monitor import (
    BackendMonitor,
    get_monitor,
    preview_provider_messages,
    preview_provider_response,
)
from ..session.handoff import context_pressure
from ..tool_compress import compress as tool_compress
from ..final_gates import (
    run_claim_gates,
    run_contract_gate,
    run_done_claim_gate,
    run_owner_integrity_audit_reporting_gate,
    run_self_protocol_truth_gate,
    run_verify_edits_gate,
)
from ..learning.feedback_learning import record_feedback_learning
from .agent_turn_dispatch import AgentTurnDispatchMixin
from .agent_turn_recovery import AgentTurnRecoveryMixin
from .agent_utils import (
    TurnCancelled,
    _call_on_first_tool,
    _code_graph_age,
    _emit_task_board_update,
    _looks_like_trivial_greeting,
    _truncate_recall,
    _usage_tokens,
)
from ..graph.code_graph import build_code_graph_context, should_include_code_graph_context
from ..context_bridge import ContextSource, build_active_context_bridge
from ..coordination_state import build_main_coordination_context
from ..mo_control_context import build_mo_control_context, should_include_mo_control_context
from ..owner_protocols import (
    is_owner_maintenance_activation,
    is_owner_interface_audit_activation,
    is_owner_comparison_activation,
    is_owner_integrity_audit_activation,
)
from ..self_maintenance.owner_integrity_audit_ground_truth import (
    normalize_owner_integrity_audit_report_text,
    owner_integrity_audit_source_corpus_count,
    reconcile_latest_owner_integrity_audit_report,
)
from ..self_maintenance.devmode_closeout import (
    owner_maintenance_continuation_instruction,
    owner_maintenance_final_allows_stop,
    owner_interface_audit_continuation_instruction,
    owner_interface_audit_final_allows_stop,
    owner_comparison_continuation_instruction,
    owner_comparison_final_allows_stop,
)
from ..self_maintenance.preflight import (
    build_self_capability_preflight_context,
    should_include_self_capability_preflight,
)
from ..project_context import build_project_context
from ..work_patterns import build_work_pattern_context
from ..workspace_awareness import build_workspace_awareness, should_include_workspace_awareness
from ..security_check import run_turn_security_check
from interface.ghost import sanitize_proposal_for_context
from interface.task_board_view import render_plain

# ---------------------------------------------------------------------------
# Post-provider gate pipeline — declarative gate/action sequence
# ---------------------------------------------------------------------------
# Each pipeline entry is (name, kind, fn) where fn(agent, ctx) returns
# _CONTINUE (the gate fired — continue the provider loop) or None (pass).
# Actions always return None.

_CONTINUE = object()

# Maximum corrective re-prompts for owner-protocol terminal-stop gates and the
# contract/self-protocol-truth gates. After the cap, allow the stop with a logged
# disagreement note — must not loop to max_provider_requests.
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
        "owner_interface_audit_active", "no_evidence",
        "devmode_monitor_path", "devmode_run_ids",
        "devmode_frozen_errs", "devmode_session_dir",
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


def _pipeline_owner_interface_audit_stop(agent, ctx):
    if not owner_interface_audit_final_allows_stop(ctx.user_input, ctx.content):
        if ctx.protocol_stop_gate_continuations.get("owner_interface_audit", 0) < PROTOCOL_STOP_GATE_MAX:
            ctx.protocol_stop_gate_continuations["owner_interface_audit"] = ctx.protocol_stop_gate_continuations.get("owner_interface_audit", 0) + 1
            if ctx.on_activity:
                ctx.on_activity("continuing OWNER_INTERFACE_AUDIT...")
            agent.session.add_assistant(owner_interface_audit_continuation_instruction(ctx.user_input, ctx.content))
            return _CONTINUE
        if ctx.on_activity:
            ctx.on_activity("OWNER_INTERFACE_AUDIT stop-gate disagreement — allowing stop after cap")
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
            ctx.on_activity("OWNER_COMPARISON stop-gate disagreement — allowing stop after cap")
    return None


def _pipeline_devmode_economy(agent, ctx):
    """Write the economy ledger BEFORE the OWNER_MAINTENANCE closeout gate evaluates."""
    if ctx.owner_maintenance_active:
        agent._write_devmode_economy_record()
        agent._reconcile_devmode_summary_marker(ctx.content)
    return None


def _pipeline_owner_maintenance_stop(agent, ctx):
    if not owner_maintenance_final_allows_stop(
        ctx.user_input, ctx.content,
        monitor_path=ctx.devmode_monitor_path,
        session_ids=ctx.devmode_run_ids or None,
        frozen_error_count=ctx.devmode_frozen_errs,
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
                session_dir=ctx.devmode_session_dir,
            ))
            return _CONTINUE
        if ctx.on_activity:
            ctx.on_activity("OWNER_MAINTENANCE stop-gate disagreement — allowing stop after cap")
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
        protocol_closed = agent._finalize_self_protocol_task_board_for_answer(ctx.user_input, ctx.final_text, ctx.task_board)
        if protocol_closed or (not protocol_closed and agent._finalize_task_board_for_answer(ctx.task_board)):
            record_snapshot(ctx.task_board, "completed" if ctx.task_board.open_count() == 0 else "updated")
            _emit_task_board_update(ctx.task_board, update="completed" if ctx.task_board.open_count() == 0 else "updated", on_board_update=ctx.on_board_update, on_board_event=ctx.on_board_event)
    return None


def _pipeline_contract_gate(agent, ctx):
    _contract_instr, new_count = run_contract_gate(
        agent, ctx.task_board, ctx.user_input, ctx.turn_initial_completed_ids,
        count=ctx.contract_gate_continuations, max_continuations=PROTOCOL_STOP_GATE_MAX,
        on_activity=ctx.on_activity,
    )
    ctx.contract_gate_continuations = new_count
    if _contract_instr:
        agent.session.add_assistant(_contract_instr)
        return _CONTINUE
    return None


def _pipeline_consistency_boundary(agent, ctx):
    ctx.boundary_report = agent._run_consistency_boundary(
        "turn_final", user_text=ctx.user_input, final_text=ctx.final_text,
        learning_notes=ctx.notes, task_board=ctx.task_board,
    )
    return None


def _pipeline_self_protocol_truth(agent, ctx):
    _sp_instr, new_count = run_self_protocol_truth_gate(
        agent, ctx.user_input, ctx.final_text, ctx.boundary_report,
        count=ctx.self_protocol_truth_continuations, max_continuations=PROTOCOL_STOP_GATE_MAX,
        on_activity=ctx.on_activity,
    )
    ctx.self_protocol_truth_continuations = new_count
    if _sp_instr:
        agent.session.add_assistant(_sp_instr)
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


# -- The pipeline: ordered list of (name, kind, entry_fn) -----------------------

_POST_PROVIDER_PIPELINE = [
    ("no_tool_evidence", "gate", _pipeline_no_tool_evidence),
    ("owner_interface_audit_stop", "gate", _pipeline_owner_interface_audit_stop),
    ("owner_comparison_stop", "gate", _pipeline_owner_comparison_stop),
    ("devmode_economy", "action", _pipeline_devmode_economy),
    ("owner_maintenance_stop", "gate", _pipeline_owner_maintenance_stop),
    ("critique", "action", _pipeline_critique),
    ("board_finalization", "action", _pipeline_board_finalization),
    ("contract_gate", "gate", _pipeline_contract_gate),
    ("consistency_boundary", "action", _pipeline_consistency_boundary),
    ("self_protocol_truth", "gate", _pipeline_self_protocol_truth),
    ("done_claim", "gate", _pipeline_done_claim),
    ("verify_edits", "gate", _pipeline_verify_edits),
    ("iam_normalize", "action", _pipeline_iam_normalize),
    ("iam_reporting", "gate", _pipeline_iam_reporting),
    ("claim_gates", "gate", _pipeline_claim_gates),
]


def _task_board_change_fingerprint(task_board: TaskBoard | None) -> str:
    if not task_board:
        return ""
    try:
        summary = task_board.summary()
        comparable = {
            "state": summary.get("state"),
            "open": summary.get("open"),
            "done": summary.get("done"),
            "active_task_id": summary.get("active_task_id"),
            "ready_task_id": summary.get("ready_task_id"),
            "tasks": summary.get("tasks"),
        }
        return json.dumps(comparable, sort_keys=True)
    except Exception:
        traceback.print_exc()
        return ""


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


class AgentTurn(AgentTurnDispatchMixin, AgentTurnRecoveryMixin):
    """Turn-execution mixin: run loop, provider dispatch, tool handling."""

    def _activate_final_report_row(self, task_board: TaskBoard, *, on_board_update: object = None, on_board_event: object = None) -> None:
        """Activate the final report row and emit board update.

        Shared by run_turn() and AgentTurnRecoveryMixin._finalize_turn_for_answer()
        so both finalization paths show the same report row on the taskboard.
        """
        if task_board and task_board.tasks:
            report_id = self._final_report_task_id(task_board)
            if report_id and task_board.activate(report_id):
                record_snapshot(task_board, "updated")
                _emit_task_board_update(task_board, update="updated", on_board_update=on_board_update, on_board_event=on_board_event)

    def run_turn(self, user_input: str, task_board: TaskBoard | None = None, monitor: BackendMonitor | None = None, on_board_update: object = None, on_token: object = None, on_activity: object = None, on_first_tool: object = None, cancel_event: object = None, on_assistant_text: object = None, on_board_event: object = None, on_action: object = None) -> str:
        """Execute a single turn: detect lane, call provider with full tools, dispatch, critique.

        Returns the final display text.
        """
        if task_board is not None:
            self._active_task_board = task_board
        start = self._prepare_turn_start(user_input, monitor=monitor, cancel_event=cancel_event)
        user_input = str(start.get("user_input") or "")
        if start.get("final_text") is not None:
            return str(start.get("final_text") or "")
        pre_handoff = bool(start.get("pre_handoff"))

        # B2. PRT Feedback Learning Hook
        if hasattr(self, "_prt_last_report") and self._prt_last_report:
            report = self._prt_last_report
            from core.learning.feedback_learning import extract_feedback_learning
            insights = extract_feedback_learning(user_input, "PRT Review")
            if insights and hasattr(self, "profile"):
                record_feedback_learning(self.profile, user_input, "PRT Review")
                from core.review.finding_patterns import FindingPatterns
                patterns_mgr = FindingPatterns()
                # If user corrects or dismisses, record the first finding as ignored
                if report.findings:
                    patterns_mgr.record_finding(report.findings[0], "ignored")
            delattr(self, "_prt_last_report")

        extra_context = self._build_extra_context(user_input)
        if not pre_handoff and self._maybe_context_handoff(user_input, extra_context=extra_context) and on_activity:
            on_activity("preparing...")

        # 2. Provider loop — model may call tools, we dispatch, repeat
        provider_requests = 0
        tool_rounds = 0
        empty_response_prompts = 0
        malformed_tool_prompts = 0
        raw_tool_payload_prompts = 0
        raw_tool_payload_fallback_attempted = False
        empty_response_fallback_attempted = False
        context_overflow_retry_attempted = False
        final_gates_fired: set = set()  # once-per-turn guards for final-phase gates
        self_protocol_truth_continuations = 0  # bound the self-protocol completion-truth gate (mirror PROTOCOL_STOP_GATE_MAX)
        contract_gate_continuations = 0  # bound the closeout contract gate so it can never loop unbounded (mirror PROTOCOL_STOP_GATE_MAX)
        owner_integrity_audit_reporting_truth_continuations = 0  # OWNER_INTEGRITY_AUDIT reports may need recounting after corrective artifact edits.
        # Bound the owner-only protocol terminal-stop gates: each may re-prompt a
        # few times to push for a clean closeout, but must not loop to
        # max_provider_requests when a near-terminal completion keeps tripping the
        # regex. After the cap, allow the stop with a logged disagreement note.
        protocol_stop_gate_continuations: dict[str, int] = {}
        # Snapshot rows already completed at TURN START so the closing contract
        # gate audits every task completed during THIS turn (across all provider
        # rounds), while still excluding rows completed in prior turns. Using a
        # finalisation-time snapshot missed rows completed in an earlier round.
        turn_initial_completed_ids = (
            {t.id for t in task_board.tasks if t.status == "completed"}
            if task_board and getattr(task_board, "tasks", None) else set()
        )
        provider_limit_grace = False
        tool_limit_grace_used = False
        tool_call_counts: dict[str, int] = {}
        tool_error_counts: dict[str, int] = {}
        turn_provider_errors = 0
        turn_provider_fallbacks = 0
        turn_files_modified = False
        turn_modified_files: list[tuple[str, str]] = []  # (path, content/new_text) for security check
        self._turn_health_compacted = False
        self._turn_health_handed_off = False
        self._turn_health_tools_blocked = False
        self._turn_health_tools_blocked_count = 0
        self._owner_maintenance_completed_board_tool_blocked_count = 0
        sanitize_meta = self.session.sanitize_for_provider(
            max_chars=None
            if getattr(self, "context_handoff_enabled", True)
            else (
                self._provider_context_max_chars()
                if getattr(self, "context_summary_enabled", False)
                else None
            )
        )
        self._emit_sanitize_event(monitor, sanitize_meta, stage="pre_provider_loop")

        # === Phase 2: Provider request loop ===
        while provider_requests < self.max_provider_requests or (provider_limit_grace and provider_requests == self.max_provider_requests):
            if getattr(cancel_event, "is_set", lambda: False)():
                return "[ABORTED] Current turn stopped."
            live_steer_context = self._consume_live_steer_context(monitor=monitor)
            if live_steer_context:
                extra_context = (extra_context or "") + "\n\n" + live_steer_context
                if on_activity:
                    on_activity("applying operator steer...")
            provider_requests += 1
            if provider_requests >= self.max_provider_requests and not provider_limit_grace:
                provider_limit_grace = True
                self._grant_provider_request_grace(
                    tool_rounds=tool_rounds, provider_requests=provider_requests, tool_call_counts=tool_call_counts,
                    turn_files_modified=turn_files_modified, turn_provider_errors=turn_provider_errors,
                    turn_provider_fallbacks=turn_provider_fallbacks, monitor=monitor,
                )
                continue
            # === Early turn health guard: compact, handoff, or warn before budget runs out ===
            extra_context = self._check_turn_health(tool_rounds, extra_context, monitor=monitor)
            if on_activity:
                on_activity(f"thinking (request #{provider_requests})...")
            append_provider_audit(
                "provider_request",
                surface=self._provider_surface(),
                provider=self.provider_name,
                model=self.model,
                request=provider_requests,
                session_id=getattr(self.session, "session_id", ""),
                worker_id=self._provider_worker_id(),
            )
            if monitor:
                request_messages = self.session.get_messages(extra_context=extra_context, consume_handoff=False)
                monitor.emit("provider_request", {
                    "request": provider_requests,
                    "provider": self.provider_name,
                    "model": self.model,
                    "messages": len(self.session.messages),
                    "tools": len(self.tool_definitions),
                    "preview": preview_provider_messages(request_messages),
                })

            def checked_on_token(token: str):
                if getattr(cancel_event, "is_set", lambda: False)():
                    raise TurnCancelled()
                if on_token:
                    on_token(token)

            # Pre-call capacity gate: skip exhausted providers proactively
            cap = get_capacity()
            if not cap.can_accept(self.provider_name):
                cap_reason = "primary provider rate/concurrency limit (pre-call capacity check)"
                if self._next_provider(cap_reason):
                    if on_activity:
                        on_activity(f"capacity-aware fallback to {self.provider_name}/{self.model}")
                    if monitor:
                        monitor.emit("provider_fallback", {"request": provider_requests, "provider": self.provider_name, "model": self.model, "reason": cap_reason})
                    turn_provider_fallbacks += 1
                    continue

            try:
                response = self._call_provider(on_token=checked_on_token if on_token else None, extra_context=extra_context)
            except TurnCancelled:
                return "[ABORTED] Current turn stopped."
            except Exception as exc:
                # === Phase 2a: provider error → classify, audit, recover, or fallback ===
                raw_error = str(exc)
                if is_rate_limit_error(raw_error) or fallback_reason(raw_error):
                    try:
                        get_capacity().record_error(self.provider_name, raw_error)
                    except Exception:
                        pass
                err_msg = clean_provider_error(raw_error)
                is_context_overflow = is_context_overflow_error(raw_error)
                reason = "provider_context_overflow" if is_context_overflow else fallback_reason(raw_error)
                if on_activity:
                    on_activity(f"MO provider error: {reason or err_msg[:40]}")
                append_provider_audit(
                    "provider_error",
                    surface=self._provider_surface(),
                    provider=self.provider_name,
                    model=self.model,
                    request=provider_requests,
                    session_id=getattr(self.session, "session_id", ""),
                    worker_id=self._provider_worker_id(),
                    reason=reason or "error",
                    ok=False,
                )
                if monitor:
                    monitor.emit("provider_error", {"request": provider_requests, "provider": self.provider_name, "reason": reason or "error", "error": err_msg[:300]})
                turn_provider_errors += 1
                if is_context_overflow and not context_overflow_retry_attempted:
                    context_overflow_retry_attempted = True
                    if self._recover_from_provider_context_overflow(
                        latest_user=user_input,
                        extra_context=extra_context,
                        monitor=monitor,
                        request=provider_requests,
                        error_msg=raw_error,
                    ):
                        if on_activity:
                            on_activity("context recovered, retrying provider request")
                        continue
                if reason and reason != "provider_context_overflow" and self._next_provider(reason):
                    if on_activity:
                        on_activity(f"fallback to {self.provider_name}/{self.model}")
                    if monitor:
                        monitor.emit("provider_fallback", {"request": provider_requests, "provider": self.provider_name, "model": self.model, "reason": reason})
                    turn_provider_fallbacks += 1
                    continue
                return f"MO provider error: {err_msg}"

            if getattr(cancel_event, "is_set", lambda: False)():
                return "[ABORTED] Current turn stopped."

            # === Phase 2b: track usage tokens & audit response ===
            # Track usage
            usage = getattr(response, "usage", None)
            usage_in, usage_out, usage_total = _usage_tokens(usage)
            if usage:
                self.session.record_usage(
                    provider=self.provider_name,
                    model=self.model,
                    input_tokens=usage_in,
                    output_tokens=usage_out,
                    total_tokens=usage_total,
                )

            finish_reason = getattr(response, "finish_reason", "") or ""
            append_provider_audit(
                "provider_response",
                surface=self._provider_surface(),
                provider=self.provider_name,
                model=self.model,
                request=provider_requests,
                session_id=getattr(self.session, "session_id", ""),
                worker_id=self._provider_worker_id(),
                input_tokens=usage_in,
                output_tokens=usage_out,
                total_tokens=usage_total,
                ok=True,
            )
            if monitor:
                monitor.emit("provider_response", {
                    "request": provider_requests,
                    "provider": self.provider_name,
                    "finish_reason": finish_reason,
                    "tool_calls": len(response.tool_calls or []),
                    "content_chars": len(response.content or ""),
                    "preview": preview_provider_response(response.content or "", response.tool_calls or []),
                })

            # === Phase 2c: Handle tool calls ===
            # === Phase 2c: dispatch tool calls or finalize text ===
            # Check for tool calls
            if response.tool_calls:
                # A completed OWNER_MAINTENANCE board must not reopen broad discovery, but it
                # STILL needs its closeout tools: owning economy.md, writing the session
                # artifacts, running the final pytest. Exempt those — blocking them
                # deadlocked legitimate closeouts to [OWNER_MAINTENANCE BLOCKED] (mo-1782077188).
                if (self._owner_maintenance_completed_taskboard_should_stop_tools(user_input, task_board)
                        and not self._owner_maintenance_tool_calls_are_closeout_only(response.tool_calls)):
                    self._owner_maintenance_completed_board_tool_blocked_count += 1
                    if monitor:
                        monitor.emit("turn_health", {
                            "tool_rounds": tool_rounds,
                            "max_tool_rounds": self.max_tool_rounds,
                            "level": "blocked",
                            "action": "owner_maintenance_completed_board_tool_blocked",
                            "blocked_count": self._owner_maintenance_completed_board_tool_blocked_count,
                        })
                    if self._owner_maintenance_completed_board_tool_blocked_count <= 2:
                        self.session.add_assistant(self._owner_maintenance_completed_taskboard_tool_instruction())
                        continue
                    return self._owner_maintenance_completed_taskboard_persistent_tool_text()
                # Adaptive turn management: block tools after handoff budget exhausted
                if getattr(self, '_turn_health_tools_blocked', False):
                    self._turn_health_tools_blocked_count += 1
                    if self._turn_health_tools_blocked_count <= 2:
                        if monitor:
                            monitor.emit("turn_health", {
                                "tool_rounds": tool_rounds, "max_tool_rounds": self.max_tool_rounds,
                                "level": "blocked",
                                "action": "tool_blocked",
                                "blocked_count": self._turn_health_tools_blocked_count,
                            })
                        self.session.add_assistant(
                            self._turn_health_tool_blocked_instruction(user_input)
                        )
                        continue
                    # After 2 blocked attempts, hard stop
                    return self._turn_health_persistent_block_text(user_input)
                tool_rounds += 1
                if tool_rounds > self.max_tool_rounds:
                    action, tool_limit_grace_used = self._handle_tool_round_limit(
                        tool_rounds=tool_rounds, tool_limit_grace_used=tool_limit_grace_used,
                        provider_requests=provider_requests, tool_call_counts=tool_call_counts,
                        turn_files_modified=turn_files_modified, turn_modified_files=turn_modified_files,
                        turn_provider_errors=turn_provider_errors, turn_provider_fallbacks=turn_provider_fallbacks,
                        monitor=monitor,
                    )
                    if action == "grace":
                        continue
                    return self._finalize_limit_exhaustion(
                        kind="max_tool_rounds",
                        limit=self.max_tool_rounds,
                        tool_rounds=tool_rounds,
                        provider_requests=provider_requests,
                        tool_call_counts=tool_call_counts,
                        turn_files_modified=turn_files_modified,
                        turn_provider_errors=turn_provider_errors,
                        turn_provider_fallbacks=turn_provider_fallbacks,
                        user_input=user_input,
                        task_board=task_board,
                        on_board_update=on_board_update,
                        on_board_event=on_board_event,
                    )

                # Record the assistant's tool call request
                tool_calls_data = []
                for tc in response.tool_calls:
                    if isinstance(tc, dict):
                        fn = tc.get("function") or {}
                        name = fn.get("name", "") if isinstance(fn, dict) else ""
                        args = fn.get("arguments", "{}") if isinstance(fn, dict) else "{}"
                        tid = tc.get("id", "")
                    else:
                        fn = getattr(tc, "function", tc)
                        name = getattr(fn, "name", "") if hasattr(fn, "name") else fn.get("name", "")
                        args = getattr(fn, "arguments", "{}") if hasattr(fn, "arguments") else fn.get("arguments", "{}")
                        tid = getattr(tc, "id", "") if hasattr(tc, "id") else tc.get("id", "")
                    tool_calls_data.append({
                        "id": tid,
                        "type": "function",
                        "function": {"name": name, "arguments": args},
                    })

                argument_block = self._tool_call_argument_block_reason(tool_calls_data, finish_reason)
                if argument_block:
                    action, malformed_tool_prompts = self._malformed_tool_action(
                        argument_block=argument_block, malformed_tool_prompts=malformed_tool_prompts,
                        finish_reason=finish_reason, provider_requests=provider_requests,
                        monitor=monitor, on_activity=on_activity,
                    )
                    if action == "retry":
                        continue
                    final_text = "Provider repeatedly produced malformed/truncated tool calls; stopped before changing files. Try a smaller edit or switch model."
                    notes = self._record_turn_memory_and_learning(user_input, final_text)
                    final_text = self._append_after_turn_notes(final_text, notes)
                    self.session.add_assistant(final_text)
                    # Turn-end security check on modified files
                    if turn_modified_files and monitor:
                        sec_result = run_turn_security_check(turn_modified_files)
                        if sec_result.findings:
                            monitor.emit("security_check", sec_result.as_dict())
                    return final_text

                # Surface interim prose that accompanies tool calls. Providers
                # often answer the user's question in prose AND call a tool in
                # the same response. That prose used to reach only the livelog
                # (monitor preview), never the main transcript, so direct
                # answers were silently lost. Emit it to the UI now.
                interim_text = str(response.content or "").strip()
                if interim_text and on_assistant_text and not self._looks_like_raw_tool_payload(interim_text):
                    try:
                        on_assistant_text(interim_text)
                    except Exception:
                        traceback.print_exc()
                    if monitor:
                        monitor.emit("assistant_text", {
                            "request": provider_requests,
                            "surface": self._provider_surface(),
                            "chars": len(interim_text),
                            "with_tool_calls": True,
                        })

                msg = {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": tool_calls_data,
                }
                reasoning = getattr(response, "reasoning_content", None) or getattr(response, "reasoning", None)
                if reasoning:
                    msg["reasoning_content"] = reasoning
                self.session.add_message(msg)

                # Pre-execute independent read-only calls concurrently (no-op
                # unless this response carried >=2 reads). The loop below stays
                # the single authority for gating/board/audit/ordering.
                prefetched_reads = self._prefetch_read_family_results(tool_calls_data, user_input)

                # Dispatch each tool call through the sandbox
                for idx, tc_data in enumerate(tool_calls_data):
                    if getattr(cancel_event, "is_set", lambda: False)():
                        return "[ABORTED] Current turn stopped."
                    name = tc_data["function"]["name"]
                    arguments = self._project_scoped_tool_arguments(name, self._parsed_tool_arguments(tc_data))
                    if on_activity:
                        on_activity(f"tooling ({name})...")

                    if monitor:
                        monitor.emit("tool_call", {"request": provider_requests, "surface": self._provider_surface(), "worker_id": self._provider_worker_id(), "tool": name, "summary": self._safe_tool_summary(name, arguments)})
                    tool_call_counts[name] = tool_call_counts.get(name, 0) + 1

                    # Tool abuse detection — track consecutive same-tool/same-arg calls
                    _abuse_warning = self._detect_tool_abuse(name, arguments)

                    # === Phase 2d: dispatch each tool call through the gate ===
                    # THE SINGLE GATE
                    operator_ok = self._operator_approved(user_input, name, arguments)
                    effective_roots = self._effective_allowed_roots_for_tool(user_input, name, arguments)
                    block_reason = self._devmode_output_path_block_reason(user_input, name, arguments) or \
                        self._self_mutation_block_reason(user_input, name, arguments) or guard_tool_call(
                        name, arguments,
                        lane=self._effective_lane(),
                        allowed_roots=effective_roots,
                        sandbox_config=self.sandbox_config,
                        operator_override=operator_ok,
                    )

                    screen_image_uri = None
                    if block_reason:
                        result = block_reason
                    else:
                        # Lazy board creation happens after sandbox approval so
                        # blocked tool attempts do not appear as active work.
                        if not task_board and on_first_tool:
                            task_board = _call_on_first_tool(on_first_tool, name, arguments)
                        # Reuse the concurrently-prefetched read result when present
                        # (gate already passed identically here); else execute inline.
                        result = prefetched_reads[idx] if idx in prefetched_reads else self._dispatch_tool(name, arguments)
                        # Computer-use vision: lift a capture_screen screenshot out of
                        # the temp file into an image part for the model to SEE.
                        if name == "capture_screen":
                            from tools.screen import SCREEN_IMAGE_MARKER, load_image_data_uri
                            if SCREEN_IMAGE_MARKER in result:
                                text_part, _, path = result.partition(f"{SCREEN_IMAGE_MARKER}:")
                                screen_image_uri = load_image_data_uri(path.strip())
                                result = text_part.strip() or "[screen captured]"
                                # A screenshot is useless to a text-only provider.
                                # Route the continuation to a vision-capable one
                                # (e.g. openai-codex) so MO can actually see it.
                                if screen_image_uri and not getattr(self.active_provider, "supports_vision", False):
                                    self.switch_to_vision_provider()
                        # Auto-advance only when the tool plausibly satisfies the active row.
                        # Final/report rows wait for the actual final answer.
                        if task_board and task_board.tasks and not self._tool_result_is_error(result):
                            before_board = _task_board_change_fingerprint(task_board)
                            advanced = self._advance_task_board_after_tool(task_board, name, arguments, monitor=monitor)
                            if advanced or _task_board_change_fingerprint(task_board) != before_board:
                                record_snapshot(task_board, "updated")
                        if on_board_update and task_board:
                            rendered = render_plain(task_board)
                            if rendered != getattr(self, '_last_rendered_board', None):
                                self._last_rendered_board = rendered
                                _emit_task_board_update(task_board, update="updated", on_board_update=on_board_update, on_board_event=on_board_event)

                    # Write tool audit log (blocked or executed)
                    # Prepend abuse warning so the model sees it in the tool result
                    if _abuse_warning and not block_reason:
                        result = _abuse_warning + "\n" + result
                    tool_is_error = self._tool_result_is_error(result)
                    if tool_is_error:
                        tool_error_counts[name] = tool_error_counts.get(name, 0) + 1
                    self._write_tool_audit(name, arguments, result, block_reason)

                    # Per-action hook: surfaces (e.g. the desktop Companion action
                    # log) get every tool MO runs, with a sanitized arg summary —
                    # so the audit reflects what MO actually DID, not just the
                    # request and the final reply. Best-effort; never breaks a turn.
                    if on_action:
                        try:
                            on_action({
                                "tool": name,
                                "summary": self._safe_tool_summary(name, arguments),
                                "blocked": bool(block_reason),
                                "error": tool_is_error,
                            })
                        except Exception:
                            pass

                    if monitor:
                        monitor.emit("tool_result", {"request": provider_requests, "surface": self._provider_surface(), "worker_id": self._provider_worker_id(), "tool": name, "blocked": bool(block_reason), "error": tool_is_error, "chars": len(result)})
                    if not block_reason and not tool_is_error and name in {"write_file", "edit_file"}:
                        turn_files_modified = True
                        file_path = arguments.get("path", "")
                        file_content = arguments.get("new_text" if name == "edit_file" else "content", "")
                        if file_path:
                            turn_modified_files.append((file_path, file_content))
                    # Completing a task is the model's semantic "this work is
                    # resolved" signal; hint the next compaction to
                    # free old resolved tool chains proactively. Runtime still
                    # decides (old completed chains only, never recent/prefix, and
                    # only when freed bytes justify the cache miss).
                    if not block_reason and not tool_is_error and name == "complete_task":
                        self._work_resolved_hint = True

                    # Compress tool output (MO Agent native: lossless structural compression)
                    if not block_reason and getattr(self, 'tool_compress_enabled', True):
                        current_pressure = context_pressure(self).get("pressure", 0.0)
                        compressed, stats = tool_compress(
                            result,
                            min_bytes=self._adaptive_compress_min_bytes(),
                            pressure=current_pressure,
                        )
                        if stats:
                            result = compressed
                            self.compression_total_saved += stats["saved_chars"]
                            self.compression_total_ops += 1
                            self.compression_last_pct = stats["saved_pct"]
                            if monitor:
                                monitor.emit("tool_compress", {**stats, "tool": name})

                    result = self._cap_tool_result_for_context(result, monitor=monitor, tool_name=name)

                    if screen_image_uri:
                        self.session.add_tool_result(tc_data["id"], result, image_data_uri=screen_image_uri)
                    else:
                        self.session.add_tool_result(tc_data["id"], result)
                if getattr(cancel_event, "is_set", lambda: False)():
                    return "[ABORTED] Current turn stopped."

                continue

            if getattr(cancel_event, "is_set", lambda: False)():
                return "[ABORTED] Current turn stopped."

            # === Phase 2e: text response (no tool calls) → process, gate, finalize ===
            # Text response — no tool calls
            content = response.content or ""

            if self._looks_like_raw_tool_payload(content):
                raw_tool_payload_prompts += 1
                if monitor:
                    monitor.emit("provider_error", {"request": provider_requests, "provider": self.provider_name, "reason": "raw_tool_payload", "error": "Provider returned raw tool-call JSON/text as assistant content; suppressing and requesting a real tool call."})
                if raw_tool_payload_prompts >= 2 and not raw_tool_payload_fallback_attempted and getattr(self, "providers", None):
                    raw_tool_payload_fallback_attempted = True
                    if self._next_provider("raw_tool_payload"):
                        if monitor:
                            monitor.emit("provider_fallback", {"request": provider_requests, "provider": self.provider_name, "model": self.model, "reason": "raw_tool_payload"})
                        continue
                self._audit_provider_retry_guidance("raw_tool_payload", request=provider_requests, ok=False)
                self.session.add_assistant(self._raw_tool_payload_retry_message())
                continue

            # Handle empty/no-visible response regardless of finish reason. Some
            # providers have returned finish_reason=stop with zero content and no
            # tool calls; storing that as an assistant turn makes MO look stuck.
            if not content.strip():
                action, empty_response_prompts, empty_response_fallback_attempted = self._empty_response_action(
                    empty_response_prompts=empty_response_prompts,
                    empty_response_fallback_attempted=empty_response_fallback_attempted,
                    finish_reason=finish_reason, provider_requests=provider_requests,
                    monitor=monitor, on_activity=on_activity,
                )
                if action == "retry":
                    continue
                final_text = "Provider returned no visible answer after retry; try again or switch model."
                notes = self._record_turn_memory_and_learning(user_input, final_text)
                final_text = self._append_after_turn_notes(final_text, notes)
                self.session.add_assistant(final_text)
                # Turn-end security check on modified files
                if turn_modified_files and monitor:
                    sec_result = run_turn_security_check(turn_modified_files)
                    if sec_result.findings:
                        monitor.emit("security_check", sec_result.as_dict())
                return final_text


            # --- Post-provider gate pipeline ---
            # Assemble the gate context from run_turn locals, then run the
            # declarative pipeline (gates fire continuations; actions advance state).
            owner_maintenance_active = is_owner_maintenance_activation(user_input)
            owner_comparison_active = is_owner_comparison_activation(user_input)
            owner_interface_audit_active = is_owner_interface_audit_activation(user_input)
            total_tool_calls = sum(tool_call_counts.values())
            no_evidence = _no_tool_evidence_continuation(
                owner_maintenance_active=owner_maintenance_active,
                owner_comparison_active=owner_comparison_active,
                owner_interface_audit_active=owner_interface_audit_active,
                total_tool_calls=total_tool_calls,
                devmode_taskboard_completed=self._owner_maintenance_taskboard_completed(task_board),
            )
            devmode_monitor_path = getattr(monitor, "path", None) if monitor is not None else None
            devmode_run_ids = set(getattr(self, "_devmode_run_session_ids", None) or set())
            devmode_frozen_errs = getattr(self, "_devmode_closeout_frozen_errors", None)
            devmode_session_dir = getattr(self, "_active_devmode_session_dir", None)

            ctx = _GateContext()
            ctx.user_input = user_input
            ctx.content = content
            ctx.final_text = ""
            ctx.reasoning = None
            ctx.notes = None
            ctx.task_board = task_board
            ctx.monitor = monitor
            ctx.on_activity = on_activity
            ctx.on_board_update = on_board_update
            ctx.on_board_event = on_board_event
            ctx.final_gates_fired = final_gates_fired
            ctx.protocol_stop_gate_continuations = protocol_stop_gate_continuations
            ctx.contract_gate_continuations = contract_gate_continuations
            ctx.self_protocol_truth_continuations = self_protocol_truth_continuations
            ctx.owner_integrity_audit_reporting_truth_continuations = owner_integrity_audit_reporting_truth_continuations
            ctx.turn_initial_completed_ids = turn_initial_completed_ids
            ctx.turn_modified_files = turn_modified_files
            ctx.tool_call_counts = tool_call_counts
            ctx.tool_error_counts = tool_error_counts
            ctx.owner_maintenance_active = owner_maintenance_active
            ctx.owner_comparison_active = owner_comparison_active
            ctx.owner_interface_audit_active = owner_interface_audit_active
            ctx.no_evidence = no_evidence
            ctx.devmode_monitor_path = devmode_monitor_path
            ctx.devmode_run_ids = devmode_run_ids
            ctx.devmode_frozen_errs = devmode_frozen_errs
            ctx.devmode_session_dir = devmode_session_dir
            ctx.total_tool_calls = total_tool_calls
            ctx.response = response

            result = _run_post_provider_pipeline(self, ctx)
            if result is _CONTINUE:
                # Thread mutable counters back to run_turn locals for the next iteration.
                protocol_stop_gate_continuations = ctx.protocol_stop_gate_continuations
                contract_gate_continuations = ctx.contract_gate_continuations
                self_protocol_truth_continuations = ctx.self_protocol_truth_continuations
                owner_integrity_audit_reporting_truth_continuations = ctx.owner_integrity_audit_reporting_truth_continuations
                continue

            final_text = ctx.final_text
            reasoning = ctx.reasoning
            self.session.add_assistant(final_text, reasoning_content=str(reasoning) if reasoning else None)
            # Turn-end security check on modified files and response text
            if turn_modified_files:
                sec_result = run_turn_security_check(turn_modified_files, final_text)
                if sec_result.findings and monitor:
                    monitor.emit("security_check", sec_result.as_dict())
            return final_text

        if monitor:
            diag = self._build_turn_limit_diagnostics(tool_rounds, provider_requests, tool_call_counts, turn_files_modified, turn_provider_errors, turn_provider_fallbacks)
            monitor.emit("turn_limit", {"kind": "max_provider_requests", "limit": self.max_provider_requests, "diagnostics": diag})
        # Turn-end security check on modified files before limit-exhaustion return
        if turn_modified_files and monitor:
            sec_result = run_turn_security_check(turn_modified_files)
            if sec_result.findings:
                monitor.emit("security_check", sec_result.as_dict())
        return self._finalize_limit_exhaustion(
            kind="max_provider_requests",
            limit=self.max_provider_requests,
            tool_rounds=tool_rounds,
            provider_requests=provider_requests,
            tool_call_counts=tool_call_counts,
            turn_files_modified=turn_files_modified,
            turn_provider_errors=turn_provider_errors,
            turn_provider_fallbacks=turn_provider_fallbacks,
            user_input=user_input,
            task_board=task_board,
            on_board_update=on_board_update,
            on_board_event=on_board_event,
        )

    def _affected_test_failure_instruction(self, turn_modified_files: list) -> str | None:
        """Run the affected tests for code files this turn changed;
        return a self-heal instruction if they fail, else None.

        Fail-open: any error returns None so the verifier never blocks a turn.
        Reuses PRT's bounded/recursion-guarded/config-gated affected-test runner
        (``prt.run_affected_tests``) — no duplicate test machinery. No-op for
        doc-only turns or when no affected tests exist.
        """
        py_paths = [p for p, _ in (turn_modified_files or []) if str(p).endswith(".py")]
        if not py_paths:
            return None
        try:
            import subprocess
            from pathlib import Path
            from ..path_defaults import repo_root
            from ..graph.code_graph import affected_tests
            from ..review.diff_review import _run_affected_tests

            root = Path(repo_root())
            diff = subprocess.run(
                ["git", "diff", "--", *py_paths],
                cwd=str(root), text=True, capture_output=True, timeout=10,
            ).stdout
            if not diff.strip():
                return None
            tests = affected_tests(diff, str(root))
            if not tests:
                return None
            findings, _summary = _run_affected_tests(self, tests, root)
            if not findings:
                return None
            detail = getattr(findings[0], "explanation", "") or getattr(findings[0], "message", "")
            return (
                "[VERIFY] The affected tests for the files you changed this turn are FAILING — "
                "do not finish yet.\n\n"
                f"{detail}\n\n"
                "Fix the code or the tests, re-verify, then give your answer."
            )
        except Exception:
            return None  # fail-open — never block a turn on the verifier's own error

    def _build_extra_context(self, user_input: str) -> str:
        """Assemble the dynamic context block injected into the system message each turn.

        Includes: operator profile, episodic memory recall, Ghost intent guardrails,
        work pattern guidance, unified local skills, workspace awareness, code graph slice,
        and reasoning level preference.  Used by run_turn.

        Profile context is gated: simple_chat / greeting turns skip the full profile
        read to save tokens and disk I/O.
        """
        profile_context = ""
        # Pure greetings/acks ("hi", "thanks") need no profile/recall/project read.
        # EVERY other real turn loads the operator profile: it is the SOLE home of
        # operator + project/deploy/ownership knowledge since the mo_control bridge
        # was retired, so the old greeting/identity-only gate made MO guess project
        # facts it actually had. Same "not a trivial greeting" bar as project_context
        # below, so operator and project context load together and consistently.
        trivial_greeting = _looks_like_trivial_greeting(user_input)
        mo_control_needed = should_include_mo_control_context(user_input, getattr(self, "config", {}))
        include_profile = not trivial_greeting
        if include_profile:
            profile = getattr(self, "profile", None)
            if profile:
                profile_context = profile.build_profile_context()
        recalled_context = ""
        memory = getattr(self, "memory", None)
        if memory and not trivial_greeting:
            try:
                recalled = memory.recall(user_input, limit=3)
                if recalled:
                    recalled_context = (
                        "### Recalled Past Interactions - orientation only\n"
                        "These are conversation memories, not tool receipts or current proof. "
                        "Do not claim file facts, line counts, tests, or past verification from them; "
                        "read files/run tools again before factual claims.\n"
                    ) + "\n".join(
                        f"- Past user query: {_truncate_recall(r['user'], 500)}\n"
                        f"  MO's past response: {_truncate_recall(r['assistant'], 800)}"
                        for r in recalled
                    )
                elif hasattr(memory, "record_miss"):
                    memory.record_miss(user_input)
            except Exception:
                traceback.print_exc()
        pending_proposal = sanitize_proposal_for_context(getattr(self, "_pending_turn_proposal", ""))
        proposal_context = f"### Ghost Intent Guardrails For This Turn\n{pending_proposal}" if pending_proposal else ""
        coordination_context = build_main_coordination_context(self, user_input)
        work_pattern_context = build_work_pattern_context(user_input)
        workspace_needed = should_include_workspace_awareness(user_input)
        if not workspace_needed:
            try:
                registry = getattr(self, "workers", None)
                workspace_needed = bool((registry and registry.active()) or getattr(self, "_goal_active", False))
            except Exception:
                workspace_needed = bool(getattr(self, "_goal_active", False))
        workspace_context = ""
        if workspace_needed:
            try:
                workspace_context = build_workspace_awareness(self, cwd=getattr(self, "project_cwd", None))
            except TypeError:
                workspace_context = build_workspace_awareness(self)
        project_context = "" if trivial_greeting else build_project_context(getattr(self, "project_cwd", os.getcwd()))
        mo_control_context = build_mo_control_context(user_input=user_input, config=getattr(self, "config", {})) if mo_control_needed else ""
        self_capability_context = ""
        if should_include_self_capability_preflight(user_input):
            self_capability_context = build_self_capability_preflight_context(user_input, cwd=getattr(self, "project_cwd", None))
        devmode_output_context = self._devmode_runtime_output_context(user_input)
        code_graph_context = ""
        if should_include_code_graph_context(user_input):
            try:
                code_graph_context = build_code_graph_context(user_input, cwd=getattr(self, "project_cwd", None), profile=getattr(self, "profile", None))
            except TypeError:
                code_graph_context = build_code_graph_context(user_input)
        pending_interrupted_context = self._pending_interrupted_work_context(user_input)
        try:
            from ..heartbeat import build_surface_continuity_context, build_surface_environment_context
            heartbeat_context = build_surface_continuity_context(self, current_surface=getattr(self, "_current_route_source", "terminal"))
            environment_context = build_surface_environment_context(self, current_surface=getattr(self, "_current_route_source", "terminal"))
        except Exception:
            heartbeat_context = ""
            environment_context = ""
        # Mark graph as orientation-only if it predates current session
        if code_graph_context and hasattr(self, 'session'):
            session_age = getattr(self.session, 'created_at', 0) or 0
            graph_age = _code_graph_age()
            if graph_age and session_age and graph_age < session_age:
                code_graph_context = code_graph_context.replace(
                    "### MO Internal Code Map - orientation only",
                    "### MO Internal Code Map — may be stale (graph older than session), verify with tools",
                )
        reasoning_context = self._reasoning_context(user_input)
        # Current date in the dynamic (non-cached) layer so MO can reason about
        # recency/versions/"latest" without assuming a stale training cutoff.
        try:
            datetime_context = f"Current date: {datetime.now():%A, %Y-%m-%d}."
        except Exception:
            datetime_context = ""
        # Skills: read the relevant best-practice pack(s) before acting.
        skills_context = ""
        try:
            cfg = getattr(self, "config", {}) if isinstance(getattr(self, "config", {}), dict) else {}
            skills_cfg = cfg.get("skills", {}) if isinstance(cfg.get("skills", {}), dict) else {}
            if skills_cfg.get("enabled", True):
                from ..skills import select_skills_context, default_skill_roots
                skills_context = select_skills_context(
                    user_input,
                    default_skill_roots(
                        getattr(self, "project_cwd", None),
                        getattr(self, "runtime_home", None),
                        profile=getattr(self, "profile", None),
                        config=cfg,
                    ),
                    profile=getattr(self, "profile", None),
                    config=cfg,
                )
        except Exception:
            traceback.print_exc()
        conventions_context = ""
        try:
            cfg = getattr(self, "config", {}) if isinstance(getattr(self, "config", {}), dict) else {}
            skills_cfg = cfg.get("skills", {}) if isinstance(cfg.get("skills", {}), dict) else {}
            if skills_cfg.get("enabled", True):
                from ..skills import select_conventions_context, default_skill_roots
                from ..graph.code_graph import relevant_node_paths
                node_paths = relevant_node_paths(
                    user_input,
                    cwd=getattr(self, "project_cwd", None),
                    profile=getattr(self, "profile", None),
                )
                conventions_context = select_conventions_context(
                    user_input,
                    default_skill_roots(
                        getattr(self, "project_cwd", None),
                        getattr(self, "runtime_home", None),
                        profile=getattr(self, "profile", None),
                        config=cfg,
                    ),
                    node_paths,
                    profile=getattr(self, "profile", None),
                    config=cfg,
                )
        except Exception:
            traceback.print_exc()
        self._last_turn_context_flags = {
            "profile": bool(profile_context),
            "memory": bool(recalled_context),
            "ghost_proposal": bool(proposal_context),
            "coordination": bool(coordination_context),
            "work_pattern": bool(work_pattern_context),
            "workflow_learning": False,
            "proactive_learning": False,
            "skills": bool(skills_context),
            "conventions": bool(conventions_context),
            "workspace": bool(workspace_context),
            "project_context": bool(project_context),
            "mo_control": bool(mo_control_context),
            "self_capability": bool(self_capability_context),
            "devmode_output": bool(devmode_output_context),
            "code_graph": bool(code_graph_context),
            "pending_interrupted": bool(pending_interrupted_context),
            "heartbeat": bool(heartbeat_context),
            "environment": bool(environment_context),
            "reasoning": bool(reasoning_context),
        }
        self._pending_turn_proposal = ""
        bridge = build_active_context_bridge(
            user_input,
            (
                ContextSource("coordination", "Active worker coordination warning", coordination_context, 1, "runtime coordination warning; avoid conflicting edits and verify current state", max_chars=1200),
                ContextSource("datetime", "Current date", datetime_context, 1, "today's actual date; use it for recency/version reasoning, not a training cutoff", max_chars=80),
                ContextSource("environment", "Active surface environment", environment_context, 1, "current surface, OS, CWD, and shell; always verify live state", max_chars=300),
                ContextSource("heartbeat", "Surface heartbeat continuity", heartbeat_context, 1, "surface continuity; re-check live state before claims", max_chars=900),
                ContextSource("profile", "Current operator profile", profile_context, 2, "profile guidance; current user request, system contract, and evidence requirements win", max_chars=3000),
                ContextSource("ghost_proposal", "Ghost intent guardrails for this turn", proposal_context, 3, "scope guardrail only; not proof of completion", max_chars=1400),
                ContextSource("work_pattern", "Active work pattern", work_pattern_context, 3, "process guidance for this turn; verify before claims", max_chars=1800),
                ContextSource("skills", "Relevant MO skills", skills_context, 3, "authored, promoted, and confirmed local skill guidance for this task; follow before acting and verify with tools", max_chars=2600),
                ContextSource("conventions", "MO conventions for the code in scope", conventions_context, 2, "location-scoped rules/conventions for the files in scope this turn; follow where they apply, verify with tools", max_chars=2000),
                ContextSource("workspace", "Workspace / worker awareness", workspace_context, 3, "coordination context only; not proof of code correctness", max_chars=1600),
                ContextSource("project_context", "Project-local instructions (current working directory)", project_context, 3, "instructions from the CURRENT cwd, which may not be the operator's named project; verify this is the right project and check current files before factual claims", max_chars=3200),
                ContextSource("mo_control", "MO control workspace authority", mo_control_context, 3, "active policy/orientation for cross-repo/server work; live checks still win", max_chars=2600),
                ContextSource("self_capability", "MO self-capability preflight", self_capability_context, 1, "hard gate for MO self/OWNER_MAINTENANCE work; inventory existing capabilities before edits/builds", max_chars=7200),
                ContextSource("devmode_output", "OWNER_MAINTENANCE runtime-owned output directory", devmode_output_context, 1, "authoritative private artifact directory for this OWNER_MAINTENANCE run; never hand-roll a memory/devmode timestamp", max_chars=1200),
                ContextSource("pending_interrupted", "Paused interrupted work", pending_interrupted_context, 3, "continuity context only; do not resume unless relevant to current request", max_chars=1100),
                ContextSource("reasoning", "Runtime reasoning preference", reasoning_context, 4, "runtime preference only; evidence and current task still win", max_chars=400),
                ContextSource("memory", "Recalled past interactions", recalled_context, 5, "orientation only; not tool receipts or current proof", max_chars=2400),
                ContextSource("code_graph", "Code map", code_graph_context, 5, "orientation only; graph hints must be verified with files/tools/tests", max_chars=1800),
            ),
        )
        extra_context = bridge.text
        monitor = get_monitor()
        if monitor:
            monitor.emit("turn_context", {
                "flags": dict(self._last_turn_context_flags),
                "extra_context_chars": len(extra_context),
                "context_bridge_chars": len(extra_context),
                "context_bridge_sources": list(bridge.included_keys),
                "profile_chars": len(profile_context),
                "memory_chars": len(recalled_context),
                "proposal_chars": len(proposal_context),
                "coordination_chars": len(coordination_context),
                "work_pattern_chars": len(work_pattern_context),
                "skills_chars": len(skills_context),
                "workspace_chars": len(workspace_context),
                "project_context_chars": len(project_context),
                "mo_control_chars": len(mo_control_context),
                "self_capability_chars": len(self_capability_context),
                "code_graph_chars": len(code_graph_context),
                "pending_interrupted_chars": len(pending_interrupted_context),
                "heartbeat_chars": len(heartbeat_context),
                "environment_chars": len(environment_context),
                "reasoning_chars": len(reasoning_context),
            })
        return extra_context

    def _call_provider(self, on_token: object = None, extra_context: str | None = None):
        """Call the active provider with current session messages and full tools."""
        messages = self.session.get_messages(extra_context=extra_context, consume_handoff=False)
        handoff_seed = str(getattr(self.session, "_handoff_context", "") or "")
        p = self.active_provider

        response = p.complete(
            messages=messages,
            tools=self.tool_definitions,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            on_token=on_token,
        )
        if handoff_seed and str(getattr(self.session, "_handoff_context", "") or "") == handoff_seed:
            self.session._handoff_context = ""
        return response

    def _empty_response_action(self, *, empty_response_prompts: int, empty_response_fallback_attempted: bool,
                               finish_reason: str, provider_requests: int, monitor=None, on_activity=None) -> tuple[str, int, bool]:
        """Shared empty/no-visible-content handling for both turn loops.

        Performs the side effects (monitor emit, retry/failover seed messages) and
        returns ``(action, empty_response_prompts, empty_response_fallback_attempted)``
        where action is ``"retry"`` (caller should continue its loop) or
        ``"give_up"`` (caller should finalize with the standard no-answer message).
        """
        empty_response_prompts += 1
        reason = "empty_length" if finish_reason == "length" else "empty_response"
        if on_activity:
            on_activity(f"empty response (retry {empty_response_prompts}/2)")
        if monitor:
            monitor.emit("provider_error", {"request": provider_requests, "provider": self.provider_name, "reason": reason, "error": "Provider returned no visible content. Retrying."})
        if empty_response_prompts <= 2:
            self.session.add_assistant(
                "[PROVIDER EMPTY] Response had no visible text and no tool calls. "
                "Answer the user directly and concisely."
            )
            return "retry", empty_response_prompts, empty_response_fallback_attempted
        # Same-provider retries exhausted. Fail over once to the next healthy
        # provider before giving up, so a single provider's empty-response blip
        # does not fail the whole turn when another provider could answer.
        if not empty_response_fallback_attempted and getattr(self, "providers", None):
            empty_response_fallback_attempted = True
            if self._next_provider("empty_response"):
                empty_response_prompts = 0
                if monitor:
                    monitor.emit("provider_fallback", {"request": provider_requests, "provider": self.provider_name, "model": self.model, "reason": "empty_response"})
                self.session.add_assistant(
                    "[PROVIDER EMPTY] Previous provider returned no visible text. "
                    "Answer the user directly and concisely."
                )
                return "retry", empty_response_prompts, empty_response_fallback_attempted
        return "give_up", empty_response_prompts, empty_response_fallback_attempted

    def _malformed_tool_action(self, *, argument_block: str, malformed_tool_prompts: int,
                               finish_reason: str, provider_requests: int, monitor=None, on_activity=None) -> tuple[str, int]:
        """Shared malformed/truncated tool-call handling for both turn loops.

        Returns ``(action, malformed_tool_prompts)`` where action is ``"retry"``
        (caller continues its loop) or ``"give_up"`` (caller finalizes with the
        standard malformed-tool message).
        """
        malformed_tool_prompts += 1
        is_length_truncation = str(finish_reason or "").lower() == "length"
        if monitor:
            monitor.emit("provider_error", {
                "request": provider_requests,
                "provider": self.provider_name,
                "reason": "truncated_tool_call" if is_length_truncation else "invalid_tool_arguments",
                "error": argument_block[:300],
            })
        if is_length_truncation:
            # Don't stop — inject retry guidance so the model uses edit_file instead
            if on_activity:
                on_activity("output truncated, retrying with edit_file guidance")
            self._audit_provider_retry_guidance("truncated_tool_call", request=provider_requests, ok=False)
            self.session.add_assistant(argument_block)
            return "retry", malformed_tool_prompts
        if malformed_tool_prompts <= 2:
            self._audit_provider_retry_guidance("invalid_tool_arguments", request=provider_requests, ok=False)
            self.session.add_assistant(argument_block)
            return "retry", malformed_tool_prompts
        return "give_up", malformed_tool_prompts

    def _grant_provider_request_grace(self, *, tool_rounds: int, provider_requests: int, tool_call_counts: dict,
                                      turn_files_modified: bool, turn_provider_errors: int, turn_provider_fallbacks: int,
                                      monitor=None) -> None:
        """One-shot grace at the provider-request limit (shared by both turn loops):
        emit the turn_limit diagnostics and seed a final-answer ask, then fall through
        to one last provider call."""
        if monitor:
            diag = self._build_turn_limit_diagnostics(tool_rounds, provider_requests, tool_call_counts, turn_files_modified, turn_provider_errors, turn_provider_fallbacks)
            monitor.emit("turn_limit", {"kind": "max_provider_requests", "limit": self.max_provider_requests, "diagnostics": diag})
        self.session.add_assistant(
            "[MAX PROVIDER REQUESTS] Reached provider request limit. "
            "Provide your final answer based on evidence gathered so far."
        )

    def _handle_tool_round_limit(self, *, tool_rounds: int, tool_limit_grace_used: bool, provider_requests: int,
                                 tool_call_counts: dict, turn_files_modified: bool, turn_modified_files: list,
                                 turn_provider_errors: int, turn_provider_fallbacks: int, monitor=None) -> tuple[str, bool]:
        """Shared max-tool-rounds handling. Returns ``(action, tool_limit_grace_used)``
        where action is ``"grace"`` (caller continues, one more round granted) or
        ``"exhausted"`` (caller finalizes via _finalize_limit_exhaustion). Keeps the
        grace + diagnostics + security-check identical across both turn loops."""
        if not tool_limit_grace_used:
            tool_limit_grace_used = True
            if monitor:
                diag = self._build_turn_limit_diagnostics(tool_rounds, provider_requests, tool_call_counts, turn_files_modified, turn_provider_errors, turn_provider_fallbacks)
                monitor.emit("turn_limit", {"kind": "max_tool_rounds", "limit": self.max_tool_rounds, "diagnostics": diag})
            self.session.add_assistant(
                "[MAX TOOL ROUNDS] Reached tool round limit. "
                "Provide your final answer based on evidence gathered so far."
            )
            return "grace", tool_limit_grace_used
        # Grace consumed — model still tried tool calls; hard stop
        if monitor:
            diag = self._build_turn_limit_diagnostics(tool_rounds, provider_requests, tool_call_counts, turn_files_modified, turn_provider_errors, turn_provider_fallbacks)
            monitor.emit("turn_limit", {"kind": "max_tool_rounds", "limit": self.max_tool_rounds, "diagnostics": diag})
        if turn_modified_files and monitor:
            sec_result = run_turn_security_check(turn_modified_files)
            if sec_result.findings:
                monitor.emit("security_check", sec_result.as_dict())
        return "exhausted", tool_limit_grace_used
