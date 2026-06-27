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
from ..tooling.sandbox import guard_tool_call
from ..tasking.task_board import TaskBoard, record_snapshot
from ..runtime.backend_monitor import (
    BackendMonitor,
    get_monitor,
    preview_provider_messages,
    preview_provider_response,
)
from ..session.handoff import context_pressure
from ..tooling.tool_compress import compress as tool_compress
from ..context.context_bridge import ContextSource, build_active_context_bridge
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
from ..context.coordination_state import build_main_coordination_context
from ..context.mo_control_context import build_mo_control_context, should_include_mo_control_context
from .. import local_extensions
from ..context.project_context import build_project_context
from ..context.work_patterns import build_work_pattern_context
from ..context.workspace_awareness import build_workspace_awareness, should_include_workspace_awareness
from ..gates.security_check import run_turn_security_check
from interface.ghost import sanitize_proposal_for_context
from interface.task_board_view import render_plain

# ---------------------------------------------------------------------------
# Post-provider gate pipeline — extracted to core/gates/post_provider_pipeline.py
# ---------------------------------------------------------------------------
from ..gates.post_provider_pipeline import _CONTINUE, _GateContext, _run_post_provider_pipeline

def _emit_security_check(turn_modified_files, monitor, final_text=None):
    """Emit a turn-end security check on modified files (and optionally final_text)."""
    if not turn_modified_files:
        return
    sec_result = (run_turn_security_check(turn_modified_files, final_text)
                  if final_text is not None
                  else run_turn_security_check(turn_modified_files))
    if sec_result.findings and monitor:
        monitor.emit("security_check", sec_result.as_dict())


_CONTEXT_SOURCE_SPECS = (
    ("coordination", "Active worker coordination warning", 1, "runtime coordination warning; avoid conflicting edits and verify current state", 1200),
    ("datetime", "Current date", 1, "today's actual date; use it for recency/version reasoning, not a training cutoff", 80),
    ("environment", "Active surface environment", 1, "current surface, OS, CWD, and shell; always verify live state", 300),
    ("heartbeat", "Surface heartbeat continuity", 1, "surface continuity; re-check live state before claims", 900),
    ("profile", "Current operator profile", 2, "profile guidance; current user request, system contract, and evidence requirements win", 3000),
    ("ghost_proposal", "Ghost intent guardrails for this turn", 3, "scope guardrail only; not proof of completion", 1400),
    ("work_pattern", "Active work pattern", 3, "process guidance for this turn; verify before claims", 1800),
    ("skills", "Relevant MO skills", 3, "authored, promoted, and confirmed local skill guidance for this task; follow before acting and verify with tools", 2600),
    ("conventions", "MO conventions for the code in scope", 2, "location-scoped rules/conventions for the files in scope this turn; follow where they apply, verify with tools", 2000),
    ("workspace", "Workspace / worker awareness", 3, "coordination context only; not proof of code correctness", 1600),
    ("project_context", "Project-local instructions (current working directory)", 3, "instructions from the CURRENT cwd, which may not be the operator's named project; verify this is the right project and check current files before factual claims", 3200),
    ("mo_control", "MO control workspace authority", 3, "active policy/orientation for cross-repo/server work; live checks still win", 2600),
    ("local_extension", "Local extension context", 1, "profile-owned extension guidance for this turn; verify with tools before claims", 7200),
    ("pending_interrupted", "Paused interrupted work", 3, "continuity context only; do not resume unless relevant to current request", 1100),
    ("reasoning", "Runtime reasoning preference", 4, "runtime preference only; evidence and current task still win", 400),
    ("memory", "Recalled past interactions", 5, "orientation only; not tool receipts or current proof", 2400),
    ("code_graph", "Code map", 5, "orientation only; graph hints must be verified with files/tools/tests", 1800),
    ("learning", "MO Internal Learning Context — operator-confirmed, relevance-gated", 3, "apply only when the recommendation fits this turn; current user scope, sandbox, tools, and Gateway/taskboard evidence still win", 1200),
    ("workflow_learning", "MO Internal Local Skills — approved, relevance-gated", 3, "apply only when the trigger truly fits this turn; current user scope, sandbox, tools, and Gateway/taskboard evidence still win", 1400),
)


_CONTEXT_CHAR_FIELDS = {
    "profile": "profile_chars",
    "memory": "memory_chars",
    "ghost_proposal": "proposal_chars",
    "coordination": "coordination_chars",
    "work_pattern": "work_pattern_chars",
    "skills": "skills_chars",
    "conventions": "conventions_chars",
    "workspace": "workspace_chars",
    "project_context": "project_context_chars",
    "mo_control": "mo_control_chars",
    "local_extension": "local_extension_chars",
    "code_graph": "code_graph_chars",
    "pending_interrupted": "pending_interrupted_chars",
    "heartbeat": "heartbeat_chars",
    "environment": "environment_chars",
    "datetime": "datetime_chars",
    "reasoning": "reasoning_chars",
}


def _context_sources(parts: dict[str, str]) -> tuple[ContextSource, ...]:
    return tuple(
        ContextSource(key, title, value, priority, guidance, max_chars=max_chars)
        for key, title, priority, guidance, max_chars in _CONTEXT_SOURCE_SPECS
        if (value := parts.get(key, ""))
    )


def _context_flags(parts: dict[str, str]) -> dict[str, bool]:
    return {key: bool(parts.get(key, "")) for key, *_ in _CONTEXT_SOURCE_SPECS}


def _context_char_counts(parts: dict[str, str]) -> dict[str, int]:
    return {field: len(parts.get(key, "")) for key, field in _CONTEXT_CHAR_FIELDS.items()}


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
        reset_deferred_tools = getattr(self, "_reset_deferred_tools_for_turn", None)
        if callable(reset_deferred_tools):
            reset_deferred_tools()

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
        last_tool_batch_signature = ""
        repeated_tool_batch_count = 0
        final_gates_fired: set = set()  # once-per-turn guards for final-phase gates
        task_truth_continuations = 0
        contract_gate_continuations = 0
        extension_gate_continuations: dict[str, int] = {}
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
        self._extension_completed_board_tool_blocked_count = 0
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
                request_messages = self.session.get_messages(
                    extra_context=extra_context,
                    consume_handoff=False,
                    include_reasoning_content=self._provider_requires_reasoning_content(),
                )
                provider_tools = (
                    self._provider_tool_definitions()
                    if hasattr(self, "_provider_tool_definitions")
                    else list(getattr(self, "tool_definitions", []) or [])
                )
                registry_snapshot = (
                    self._deferred_tool_registry_snapshot()
                    if hasattr(self, "_deferred_tool_registry_snapshot")
                    else {}
                )
                payload = {
                    "request": provider_requests,
                    "provider": self.provider_name,
                    "model": self.model,
                    "messages": len(self.session.messages),
                    "tools": len(provider_tools),
                    "preview": preview_provider_messages(request_messages),
                }
                if registry_snapshot:
                    payload.update({
                        "tool_catalog_total": registry_snapshot.get("total", 0),
                        "tool_catalog_active": registry_snapshot.get("active", 0),
                        "tool_catalog_active_tools": registry_snapshot.get("active_tools", []),
                        "tool_catalog_activated_tools": registry_snapshot.get("activated_tools", []),
                    })
                monitor.emit("provider_request", payload)

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
                append_provider_audit(
                    "provider_error",
                    surface=self._provider_surface(),
                    provider=self.provider_name,
                    model=self.model,
                    request=provider_requests,
                    session_id=getattr(self.session, "session_id", ""),
                    worker_id=self._provider_worker_id(),
                    reason="turn_cancelled",
                    ok=False,
                )
                if monitor:
                    monitor.emit("provider_error", {
                        "request": provider_requests,
                        "provider": self.provider_name,
                        "reason": "turn_cancelled",
                        "error": "Provider request cancelled before completion.",
                    })
                turn_provider_errors += 1
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

            cancelled_after_response = bool(getattr(cancel_event, "is_set", lambda: False)())

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
            if cancelled_after_response:
                return "[ABORTED] Current turn stopped."

            # === Phase 2c: Handle tool calls ===
            # === Phase 2c: dispatch tool calls or finalize text ===
            # Check for tool calls
            if response.tool_calls:
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
                    final_text = self._maybe_append_after_turn_notes(final_text, notes)
                    self.session.add_assistant(final_text)
                    # Turn-end security check on modified files
                    _emit_security_check(turn_modified_files, monitor)
                    return final_text

                batch_signature = self._tool_call_batch_signature(tool_calls_data)
                if batch_signature and batch_signature == last_tool_batch_signature:
                    repeated_tool_batch_count += 1
                else:
                    last_tool_batch_signature = batch_signature
                    repeated_tool_batch_count = 1 if batch_signature else 0
                doom_threshold = max(2, int(getattr(self, "doom_loop_tool_batch_threshold", 3) or 3))
                if batch_signature and repeated_tool_batch_count >= doom_threshold:
                    if monitor:
                        diag = self._build_turn_limit_diagnostics(
                            tool_rounds, provider_requests, tool_call_counts,
                            turn_files_modified, turn_provider_errors, turn_provider_fallbacks,
                        )
                        diag["repeat_count"] = repeated_tool_batch_count
                        diag["tool_batch_size"] = len(tool_calls_data)
                        monitor.emit("turn_limit", {
                            "kind": "tool_doom_loop",
                            "limit": doom_threshold,
                            "diagnostics": diag,
                        })
                    final_text = self._doom_loop_block_text(tool_calls_data, repeated_tool_batch_count)
                    extension_boundary = local_extensions.completion_boundary(self, user_input, final_text)
                    if extension_boundary:
                        final_text = extension_boundary
                    notes = self._record_turn_memory_and_learning(user_input, final_text)
                    final_text = self._maybe_append_after_turn_notes(final_text, notes)
                    self.session.add_assistant(final_text)
                    _emit_security_check(turn_modified_files, monitor)
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

                # Pre-execute independent read-only inspection calls concurrently.
                # The loop below stays the single authority for gating/board/
                # audit/ordering; this only removes avoidable wall-clock waits.
                prefetched_results = self._prefetch_read_family_results(tool_calls_data, user_input)

                # Dispatch each tool call through the sandbox
                for idx, tc_data in enumerate(tool_calls_data):
                    if getattr(cancel_event, "is_set", lambda: False)():
                        return "[ABORTED] Current turn stopped."
                    name = tc_data["function"]["name"]
                    arguments = self._project_scoped_tool_arguments(name, self._parsed_tool_arguments(tc_data))
                    if on_activity:
                        # Prettify MCP tool names (mcp__server__tool -> "server · tool")
                        # so the activity line clearly shows an MCP tool is running.
                        label = name
                        if name.startswith("mcp__"):
                            mcp_parts = name.split("__", 2)
                            if len(mcp_parts) == 3:
                                label = f"mcp:{mcp_parts[1]} · {mcp_parts[2]}"
                        on_activity(f"tooling ({label})...")

                    if monitor:
                        monitor.emit("tool_call", {"request": provider_requests, "surface": self._provider_surface(), "worker_id": self._provider_worker_id(), "tool": name, "summary": self._safe_tool_summary(name, arguments)})
                    tool_call_counts[name] = tool_call_counts.get(name, 0) + 1

                    # Tool abuse detection — track consecutive same-tool/same-arg calls
                    _abuse_warning = self._detect_tool_abuse(name, arguments)

                    # === Phase 2d: dispatch each tool call through the gate ===
                    # THE SINGLE GATE
                    operator_ok = self._operator_approved(user_input, name, arguments)
                    effective_roots = self._effective_allowed_roots_for_tool(user_input, name, arguments)
                    block_reason = local_extensions.tool_block_reason(self, user_input, name, arguments) or \
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
                        # Reuse the concurrently-prefetched inspection result when present
                        # (gate already passed identically here); else execute inline.
                        result = prefetched_results[idx] if idx in prefetched_results else self._dispatch_tool(name, arguments)
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
                        # set_plan must run even on an EMPTY board — it is what
                        # populates the board MO owns (model_owned). Every other
                        # tool only advances EXISTING rows, so they still require a
                        # non-empty board. (set_plan is only ever exposed when
                        # model_owned is on, so this is inert by default.)
                        if task_board and (task_board.tasks or name == "set_plan") and not self._tool_result_is_error(result):
                            before_board = _task_board_change_fingerprint(task_board)
                            advanced = self._advance_task_board_after_tool(task_board, name, arguments, monitor=monitor)
                            # Honest result: if set_plan was refused because a local
                            # extension owns the board, say so — otherwise execute_set_plan's
                            # "Plan set" confirmation misleads MO into retrying (observed
                            # live mo-1782437654: 3 wasted set_plan calls before fallback).
                            if name == "set_plan" and not advanced and self._board_is_extension_owned(task_board):
                                result = ("set_plan was not applied: this taskboard is managed by a local extension. "
                                          "Advance its rows with complete_task as you finish each phase; do not call set_plan here.")
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
                final_text = self._maybe_append_after_turn_notes(final_text, notes)
                self.session.add_assistant(final_text)
                # Turn-end security check on modified files
                _emit_security_check(turn_modified_files, monitor)
                return final_text


            total_tool_calls = sum(tool_call_counts.values())

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
            ctx.extension_gate_continuations = extension_gate_continuations
            ctx.contract_gate_continuations = contract_gate_continuations
            ctx.task_truth_continuations = task_truth_continuations
            ctx.turn_initial_completed_ids = turn_initial_completed_ids
            ctx.turn_modified_files = turn_modified_files
            ctx.tool_call_counts = tool_call_counts
            ctx.tool_error_counts = tool_error_counts
            ctx.total_tool_calls = total_tool_calls
            ctx.response = response

            result = _run_post_provider_pipeline(self, ctx)
            if result is _CONTINUE:
                # Thread mutable counters back to run_turn locals for the next iteration.
                extension_gate_continuations = ctx.extension_gate_continuations
                contract_gate_continuations = ctx.contract_gate_continuations
                task_truth_continuations = ctx.task_truth_continuations
                continue

            final_text = ctx.final_text
            reasoning = ctx.reasoning
            self.session.add_assistant(final_text, reasoning_content=str(reasoning) if reasoning else None)
            # Turn-end security check on modified files and response text
            _emit_security_check(turn_modified_files, monitor, final_text)
            return final_text

        if monitor:
            diag = self._build_turn_limit_diagnostics(tool_rounds, provider_requests, tool_call_counts, turn_files_modified, turn_provider_errors, turn_provider_fallbacks)
            monitor.emit("turn_limit", {"kind": "max_provider_requests", "limit": self.max_provider_requests, "diagnostics": diag})
        # Turn-end security check on modified files before limit-exhaustion return
        _emit_security_check(turn_modified_files, monitor)
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
            from ..state.paths import repo_root
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
        extension_blocks = local_extensions.context_blocks(
            self,
            user_input,
            cwd=str(getattr(self, "project_cwd", "") or ""),
        )
        local_extension_context = "\n\n".join(
            str(value) for value in extension_blocks.values() if str(value).strip()
        )
        code_graph_context = ""
        if should_include_code_graph_context(user_input):
            try:
                code_graph_context = build_code_graph_context(user_input, cwd=getattr(self, "project_cwd", None), profile=getattr(self, "profile", None))
            except TypeError:
                code_graph_context = build_code_graph_context(user_input)
        pending_interrupted_context = self._pending_interrupted_work_context(user_input)
        try:
            from ..runtime.heartbeat import build_surface_continuity_context, build_surface_environment_context
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
        learning_context = ""
        try:
            from ..learning.proactive_learning import build_learning_context
            learning_context = build_learning_context(user_input)
        except Exception:
            traceback.print_exc()
        workflow_learning_context = ""
        try:
            from ..learning.workflow_learning import build_workflow_learning_context
            profile = getattr(self, "profile", None)
            if profile is not None:
                workflow_learning_context = build_workflow_learning_context(profile, user_input)
        except Exception:
            traceback.print_exc()
        context_parts = {
            "profile": profile_context,
            "memory": recalled_context,
            "ghost_proposal": proposal_context,
            "coordination": coordination_context,
            "work_pattern": work_pattern_context,
            "skills": skills_context,
            "conventions": conventions_context,
            "workspace": workspace_context,
            "project_context": project_context,
            "mo_control": mo_control_context,
            "local_extension": local_extension_context,
            "code_graph": code_graph_context,
            "pending_interrupted": pending_interrupted_context,
            "heartbeat": heartbeat_context,
            "environment": environment_context,
            "datetime": datetime_context,
            "reasoning": reasoning_context,
            "learning": learning_context,
            "workflow_learning": workflow_learning_context,
        }
        self._last_turn_context_flags = _context_flags(context_parts)
        self._pending_turn_proposal = ""
        bridge = build_active_context_bridge(user_input, _context_sources(context_parts))
        extra_context = bridge.text
        monitor = get_monitor()
        if monitor:
            payload = {
                "flags": dict(self._last_turn_context_flags),
                "extra_context_chars": len(extra_context),
                "context_bridge_chars": len(extra_context),
                "context_bridge_sources": list(bridge.included_keys),
            }
            payload.update(_context_char_counts(context_parts))
            monitor.emit("turn_context", payload)
        return extra_context

    def _call_provider(self, on_token: object = None, extra_context: str | None = None):
        """Call the active provider with current session messages and active tools."""
        messages = self.session.get_messages(
            extra_context=extra_context,
            consume_handoff=False,
            include_reasoning_content=self._provider_requires_reasoning_content(),
        )
        handoff_seed = str(getattr(self.session, "_handoff_context", "") or "")
        p = self.active_provider
        provider_tools = (
            self._provider_tool_definitions()
            if hasattr(self, "_provider_tool_definitions")
            else list(getattr(self, "tool_definitions", []) or [])
        )

        response = p.complete(
            messages=messages,
            tools=provider_tools,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            on_token=on_token,
        )
        if handoff_seed and str(getattr(self.session, "_handoff_context", "") or "") == handoff_seed:
            self.session._handoff_context = ""
        return response

    def _provider_requires_reasoning_content(self) -> bool:
        """Whether the active chat payload must retain assistant reasoning_content."""
        values = [getattr(self, "model", "")]
        try:
            provider = self.active_provider
            values.append(getattr(provider, "model", ""))
        except Exception:
            pass
        text = " ".join(str(value or "").lower() for value in values)
        return "deepseek" in text and any(marker in text for marker in ("v4", "reason", "r1", "thinking"))

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
