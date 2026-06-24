"""Provider error/retry/fallback recovery, turn-limit diagnostics, and turn-health handling for the MO agent turn."""

import json
import re
import traceback
from pathlib import Path
from typing import Any

from ..provider.provider_audit import append_provider_audit
from ..tasking.task_board import TaskBoard, record_snapshot
from ..session.session_momentum import maybe_compact_session
from ..path_defaults import operator_pack_root
from .agent_utils import _emit_task_board_update
from ..owner_protocols import (
    is_devmode05_activation,
    is_ifdev05_activation,
    is_vs05_activation,
)
from ..self_maintenance.devmode_closeout import (
    devmode05_task_truth_continuation_instruction,
    ifdev05_task_truth_continuation_instruction,
    vs05_task_truth_continuation_instruction,
)


class AgentTurnRecoveryMixin:
    """Provider error/retry/fallback recovery and turn-limit/turn-health handling."""

    def _audit_provider_retry_guidance(self, reason: str, *, request: int, ok: bool | None = None) -> None:
        """Record deterministic retry guidance injected after provider drift."""
        append_provider_audit(
            "provider_retry_guidance",
            surface=self._provider_surface(),
            provider=self.provider_name,
            model=self.model,
            request=request,
            session_id=getattr(self.session, "session_id", ""),
            worker_id=self._provider_worker_id(),
            reason=reason,
            ok=ok,
        )

    @staticmethod
    def _tool_call_argument_block_reason(tool_calls_data: list[dict], finish_reason: str) -> str:
        """Return a provider-facing block reason for malformed/truncated tool calls."""
        if str(finish_reason or "").lower() == "length":
            return (
                "[TOOL ARGUMENTS TRUNCATED] Provider hit the output limit while emitting tool calls. "
                "Do not rewrite full existing files. Use targeted edit_file replacements in small chunks "
                "(roughly <=250 lines per mutation), or create a small new-file skeleton and extend it with exact edits."
            )
        for tc_data in tool_calls_data:
            raw = str(((tc_data.get("function") or {}).get("arguments")) or "")
            try:
                parsed = json.loads(raw or "{}")
            except json.JSONDecodeError:
                name = str(((tc_data.get("function") or {}).get("name")) or "tool")
                return (
                    f"[TOOL ARGUMENTS INVALID] {name} arguments were invalid or truncated JSON. "
                    "Do not retry the same giant tool call. Split the work: prefer edit_file exact replacements "
                    "for existing files, keep each mutation small (roughly <=250 lines), and verify after editing."
                )
            if not isinstance(parsed, dict):
                name = str(((tc_data.get("function") or {}).get("name")) or "tool")
                return f"[TOOL ARGUMENTS INVALID] {name} arguments must be a JSON object. Retry with a valid object."
        return ""

    @staticmethod
    def _looks_like_raw_tool_payload(content: str) -> bool:
        text = str(content or "").strip()
        if not text:
            return False
        lowered = text.lower()
        tool_markers = (
            "[tool calls requested]",
            "edit_file(",
            "write_file(",
            "read_file(",
            "test_runner(",
            "project_bridge(",
        )
        if any(marker in lowered for marker in tool_markers):
            return True
        if re.search(r'^\s*\{\s*"(?:path|command|root|old_text|new_text)"\s*:', text, re.DOTALL):
            return True
        if re.search(r'"old_text"\s*:\s*.*"new_text"\s*:', text, re.DOTALL):
            return True
        return False

    @staticmethod
    def _raw_tool_payload_retry_message() -> str:
        return (
            "[TOOL PAYLOAD RETRY] Previous response looked like internal tool syntax, not a user-facing answer. "
            "Use the actual tool interface when action is needed; otherwise answer in normal prose."
        )

    @staticmethod
    def _build_turn_limit_diagnostics(
        tool_rounds: int,
        provider_requests: int,
        tool_counts: dict[str, int],
        files_modified: bool,
        provider_errors: int,
        provider_fallbacks: int,
    ) -> dict:
        """Enrich turn_limit events with diagnostic context for trace analysis."""
        top_tools = dict(sorted(tool_counts.items(), key=lambda x: -x[1])[:5]) if tool_counts else {}
        return {
            "tool_rounds": tool_rounds,
            "provider_requests": provider_requests,
            "top_tools": top_tools,
            "files_modified": files_modified,
            "provider_errors": provider_errors,
            "provider_fallbacks": provider_fallbacks,
        }

    def _finalize_limit_exhaustion(
        self,
        kind: str,  # "max_tool_rounds" or "max_provider_requests"
        limit: int,
        tool_rounds: int,
        provider_requests: int,
        tool_call_counts: dict[str, int],
        turn_files_modified: bool,
        turn_provider_errors: int,
        turn_provider_fallbacks: int,
        user_input: str,
        task_board: object | None = None,
        *,
        on_board_update: object = None,
        on_board_event: object = None,
    ) -> str:
        """Finalize taskboard, record in session, and build a useful exhaustion message."""
        diag = self._build_turn_limit_diagnostics(
            tool_rounds, provider_requests, tool_call_counts,
            turn_files_modified, turn_provider_errors, turn_provider_fallbacks,
        )
        # Build a richer user-facing message
        top_tool_str = ""
        top_tools = diag.get("top_tools") or {}
        if top_tools:
            top_tool_str = "; top tools: " + ", ".join(
                f"{name} ×{count}" for name, count in top_tools.items()
            )
        files_note = " (files modified)" if turn_files_modified else ""
        error_note = f"; {turn_provider_errors} provider error(s)" if turn_provider_errors else ""
        fallback_note = f"; {turn_provider_fallbacks} fallback(s)" if turn_provider_fallbacks else ""
        if kind == "max_tool_rounds":
            label = "MAX TOOL ROUNDS"
            advice = "Use /goal for larger work or break the task into smaller turns."
        else:
            label = "MAX PROVIDER REQUESTS"
            advice = "Use /goal for larger work or break the task into smaller turns."
        message = (
            f"[{label}] Turn limit reached: {tool_rounds} tool round(s), "
            f"{provider_requests} provider request(s) of {limit} allowed{files_note}"
            f"{top_tool_str}{error_note}{fallback_note}. {advice}"
        )
        # Record in session
        self.session.add_assistant(message)
        # Finalize taskboard if present
        if task_board and task_board.tasks:
            report_id = self._final_report_task_id(task_board)
            if report_id and task_board.activate(report_id):
                record_snapshot(task_board, "updated")
                _emit_task_board_update(
                    task_board, update="updated",
                    on_board_update=on_board_update, on_board_event=on_board_event,
                )
            if self._finalize_task_board_for_answer(task_board):
                record_snapshot(
                    task_board,
                    "completed" if task_board.open_count() == 0 else "updated",
                )
                _emit_task_board_update(
                    task_board,
                    update="completed" if task_board.open_count() == 0 else "updated",
                    on_board_update=on_board_update, on_board_event=on_board_event,
                )
        return message

    def _check_turn_health(self, tool_rounds: int, extra_context: str | None, *, monitor: Any = None) -> str | None:
        """Integrated turn health guard: warn, compact, handoff.

        Connects three previously-independent systems:
          1. Turn budget tracking (this method)
          2. Momentum compaction (maybe_compact_session)
          3. Context handoff   (_perform_context_handoff)

        Thresholds (configurable via agent.turn_health_* in config.yaml):
          compact_at   — ratio of max_tool_rounds (default 0.60)
          handoff_at   — remaining tool rounds (default 5)

        Returns enriched extra_context or None.
        The existing tool-limit check (tool_rounds > max_tool_rounds) handles
        the hard stop with grace; this guard only warns/compacts/hands-off.
        """
        max_tools = max(1, getattr(self, "max_tool_rounds", 80))
        remaining = max_tools - tool_rounds
        ratio = tool_rounds / max_tools if max_tools else 0

        # Configurable thresholds
        cfg = getattr(self, "config", {}) or {}
        agent_cfg = cfg.get("agent", {}) if isinstance(cfg, dict) else {}
        compact_at = float(agent_cfg.get("turn_health_compact_at", 0.60) or 0.60)
        handoff_at = int(agent_cfg.get("turn_health_handoff_at", 5) or 5)
        latest_user = ""
        for message in reversed(list(getattr(getattr(self, "session", None), "messages", []) or [])):
            if message.get("role") == "user":
                latest_user = str(message.get("content") or "")
                break
        devmode05_active = is_devmode05_activation(latest_user)
        devmode05_completed = devmode05_active and self._devmode05_taskboard_completed()
        work_continuation_active = (not devmode05_active) and self._has_open_runtime_work()

        # One-shot flags per turn (reset each turn via reset below)
        if not hasattr(self, "_turn_health_compacted"):
            self._turn_health_compacted = False
        if not hasattr(self, "_turn_health_handed_off"):
            self._turn_health_handed_off = False

        # ── Actions (only fire once per turn) ──────────────────────
        warning: str | None = None
        level = "note"
        emitted_turn_health = False

        # Handoff: force a fresh context with a conclusion mandate
        # Guard: only handoff if the model has actually used some budget
        # (prevents instant handoff when max_tool_rounds is very small).
        if remaining <= handoff_at and tool_rounds > 0 and not self._turn_health_handed_off:
            if getattr(self, "context_handoff_enabled", True):
                self._turn_health_handed_off = True
                self._turn_health_compacted = True  # no point compacting after handoff
                self._turn_health_tools_blocked = True  # adaptive: block further tool calls
                try:
                    self._force_tool_budget_handoff(tool_rounds, max_tools, monitor=monitor)
                    level = "handoff"
                    warning = (
                        f"[TURN HEALTH HANDOFF] {tool_rounds}/{max_tools} tool rounds used. "
                        "Context handed off with continuation mandate. "
                        + (
                            "DEVMODE05 taskboard is already complete/open=0. Produce [DEVMODE05 COMPLETE] now — do NOT call more tools."
                            if devmode05_completed
                            else
                            "Return a DEVMODE05 continuation capsule now — do NOT call more tools."
                            if devmode05_active
                            else "Return a work continuation capsule now — do NOT call more tools."
                            if work_continuation_active
                            else "Produce your final answer now — do NOT call more tools."
                        )
                    )
                    if monitor:
                        monitor.emit("turn_health", {
                            "tool_rounds": tool_rounds, "max_tool_rounds": max_tools,
                            "remaining": remaining, "level": level,
                            "action": "handoff",
                            "label": "orientation only, not proof",
                        })
                        emitted_turn_health = True
                except Exception:
                    # Handoff failed — inject critical warning instead, still block tools
                    self._turn_health_tools_blocked = True
                    level = "critical"
                    warning = (
                        f"[TURN HEALTH CRITICAL] {tool_rounds}/{max_tools} tool rounds used — "
                        f"only {remaining} remain before hard stop. "
                        "You MUST produce your final answer now. Do NOT call any more tools."
                    )
            else:
                self._turn_health_tools_blocked = True
                level = "critical"
                warning = (
                    f"[TURN HEALTH CRITICAL] {tool_rounds}/{max_tools} tool rounds used — "
                    f"only {remaining} remain before hard stop. "
                    "You MUST produce your final answer now. Do NOT call any more tools."
                )

        # Compaction: reduce context pressure to help model focus
        elif ratio >= compact_at and not self._turn_health_compacted:
            self._turn_health_compacted = True
            try:
                compact_result = maybe_compact_session(
                    self, stage="turn_health", latest_user="", extra_context=extra_context or "",
                    monitor=monitor, force=True,
                )
                if compact_result.get("changed"):
                    level = "compact"
                    warning = (
                        f"[TURN HEALTH NOTE] {tool_rounds}/{max_tools} tool rounds used "
                        f"({ratio:.0%}). Context compacted to help you focus. "
                        "Aim to conclude within remaining budget."
                    )
                    if monitor:
                        monitor.emit("turn_health", {
                            "tool_rounds": tool_rounds, "max_tool_rounds": max_tools,
                            "remaining": remaining, "level": level,
                            "action": "compact",
                            "saved_chars": compact_result.get("saved_chars", 0),
                            "compacted_chains": compact_result.get("compacted_chains", 0),
                        })
                        emitted_turn_health = True
                else:
                    level = "note"
                    reason = str(compact_result.get("reason") or "no_change")
                    warning = (
                        f"[TURN HEALTH NOTE] {tool_rounds}/{max_tools} tool rounds used "
                        f"({ratio:.0%}). No eligible completed tool chains were available for session compaction "
                        f"({reason}). Aim to conclude within remaining budget."
                    )
                    if monitor:
                        monitor.emit("turn_health", {
                            "tool_rounds": tool_rounds, "max_tool_rounds": max_tools,
                            "remaining": remaining, "level": level,
                            "action": "compact_skipped",
                            "reason": reason,
                        })
                        emitted_turn_health = True
            except Exception:
                level = "note"
                warning = (
                    f"[TURN HEALTH NOTE] {tool_rounds}/{max_tools} tool rounds used. "
                    "Be mindful of remaining budget and aim to conclude soon."
                )

        # Warning-only tiers (when action already taken or not at threshold)
        elif remaining <= handoff_at:
            # Already handed off — reinforce
            level = "critical"
            warning = (
                f"[TURN HEALTH CRITICAL] {tool_rounds}/{max_tools} tool rounds used — "
                f"only {remaining} remain. "
                + (
                    "Produce [DEVMODE05 COMPLETE] NOW; taskboard is already complete/open=0."
                    if devmode05_completed
                    else
                    "Return a DEVMODE05 continuation capsule NOW."
                    if devmode05_active
                    else "Return a work continuation capsule NOW."
                    if work_continuation_active
                    else "Produce your final answer NOW."
                )
            )
        elif remaining <= max(handoff_at + 1, 12):
            level = "warning"
            warning = (
                f"[TURN HEALTH WARNING] {tool_rounds}/{max_tools} tool rounds used. "
                f"Wrap up and produce a final answer. Only call tools if essential."
            )
        elif ratio >= compact_at:
            level = "note"
            warning = (
                f"[TURN HEALTH NOTE] {tool_rounds}/{max_tools} tool rounds used. "
                "Be mindful of remaining budget and aim to conclude soon."
            )

        if warning:
            if monitor and not emitted_turn_health and level not in ("handoff", "compact"):
                monitor.emit("turn_health", {
                    "tool_rounds": tool_rounds, "max_tool_rounds": max_tools,
                    "remaining": remaining, "level": level,
                })
            extra_context = (extra_context or "") + "\n\n" + warning

        return extra_context

    def _devmode05_completed_taskboard_should_stop_tools(self, user_input: str, task_board: TaskBoard | None = None) -> bool:
        """Return True when completed DEVMODE05 task truth must close instead of probing."""
        return is_devmode05_activation(user_input) and self._devmode05_taskboard_completed(task_board)

    def _devmode05_taskboard_completed(self, task_board: TaskBoard | None = None) -> bool:
        """Check known live board references for completed, non-goal task truth."""
        gateway = getattr(self, "gateway", None)
        candidates = (
            task_board,
            getattr(self, "_active_task_board", None),
            getattr(gateway, "last_task_board", None),
        )
        seen: set[int] = set()
        for board in candidates:
            if not board:
                continue
            identity = id(board)
            if identity in seen:
                continue
            seen.add(identity)
            try:
                if getattr(board, "source", "gateway") == "goal":
                    continue
                tasks = list(getattr(board, "tasks", []) or [])
                if tasks and int(board.open_count() or 0) == 0:
                    return True
            except Exception:
                continue
        return False

    @staticmethod
    def _devmode05_completed_taskboard_tool_instruction() -> str:
        return (
            "[DEVMODE05 CLOSEOUT] The live taskboard is already complete (open=0). "
            "Do not call more tools or reopen broad discovery inside this completed turn. "
            "Produce the terminal [DEVMODE05 COMPLETE] report from existing evidence now. "
            "If a genuinely new issue exists, it must be tracked as a new finding before completion in a fresh continuation, "
            "not by silent post-completion probing."
        )

    @staticmethod
    def _devmode05_completed_taskboard_persistent_tool_text() -> str:
        return (
            "[DEVMODE05 BLOCKED]\n\n"
            "Provider kept requesting tools after the DEVMODE05 taskboard was complete/open=0. "
            "This is a provider tool-use boundary after completed task truth, not unfinished DEVMODE05 work. "
            "Existing evidence was preserved; restart only to investigate this provider noncompliance."
        )

    @staticmethod
    def _devmode05_tool_calls_are_closeout_only(tool_calls) -> bool:
        """True when every requested tool call is closeout work — writing/reading the
        DEVMODE05 session artifacts, owning the economy ledger, or running the final
        pytest — rather than reopening broad discovery.

        The completed-board tool guard must NOT block these. The error-ownership gate
        REQUIRES reading economy.md and the terminal report REQUIRES writing the session
        artifacts, but both happen AFTER the board reaches open=0 — so hard-blocking all
        post-completion tools deadlocked the closeout to [DEVMODE05 BLOCKED] even though
        the model was doing exactly what the truth gate demanded (observed live
        mo-1782077188: owned 5 errors, wrote artifacts, still blocked → BLOCKED). Broad
        re-discovery (grep/find_files/code_search/source reads/arbitrary shell) is still
        treated as probing and nudged toward closeout."""
        import json as _json
        if not tool_calls:
            return False
        artifacts = (
            "economy.md", "summary.md", "catalog.md", "capability-matrix.md",
            "workflow.md", "longitudinal.md", "adversarial-rotation.json",
        )
        for tc in tool_calls:
            if isinstance(tc, dict):
                fn = tc.get("function") or {}
                name = (fn.get("name", "") if isinstance(fn, dict) else "") or ""
                raw = fn.get("arguments", "{}") if isinstance(fn, dict) else "{}"
            else:
                fn = getattr(tc, "function", tc)
                name = (getattr(fn, "name", "") if hasattr(fn, "name") else fn.get("name", "")) or ""
                raw = getattr(fn, "arguments", "{}") if hasattr(fn, "arguments") else fn.get("arguments", "{}")
            try:
                args = _json.loads(raw) if isinstance(raw, str) else (raw or {})
            except Exception:
                args = {}
            if not isinstance(args, dict):
                args = {}
            if name == "complete_task":
                continue
            if name in ("read_file", "write_file", "edit_file"):
                # edit_file is a closeout write too — the model often EDITS existing
                # artifacts (summary/longitudinal/rotation) during closeout. Omitting it
                # let the completed-board guard block the closeout batch and end the turn
                # before economy.md/summary/manifest were written (live mo-1782208099).
                path = str(args.get("path") or args.get("file_path") or "").replace("\\", "/").lower()
                raw_path = str(args.get("path") or args.get("file_path") or "")
                is_operator_pack_path = False
                try:
                    candidate = Path(raw_path).expanduser().resolve(strict=False)
                    candidate.relative_to(operator_pack_root().resolve(strict=False))
                    is_operator_pack_path = True
                except (OSError, ValueError):
                    is_operator_pack_path = False
                if ("memory/devmode" in path or is_operator_pack_path
                        or any(path.endswith(a.lower()) for a in artifacts)):
                    continue
                return False
            if name == "shell":
                cmd = str(args.get("command") or args.get("cmd") or "").lower()
                if "pytest" in cmd:
                    continue
                return False
            return False
        return True

    def _force_tool_budget_handoff(self, tool_rounds: int, max_tools: int, *, monitor: Any = None) -> None:
        """Force a context handoff with a strong conclusion mandate.

        Called when tool budget is critically low (≤ handoff_at remaining).
        The handoff document carries an explicit [TOOL BUDGET CRITICAL] focus
        so the model wakes up in a fresh session knowing it must conclude.
        """
        latest_user = ""
        for message in reversed(list(getattr(getattr(self, "session", None), "messages", []) or [])):
            if message.get("role") == "user":
                latest_user = str(message.get("content") or "")
                break
        if is_devmode05_activation(latest_user):
            # Critical budget: do NOT re-seed a fresh session for a DEVMODE05 run. A fresh
            # context handoff makes the model re-orient ("I'll start DEVMODE05 by first
            # reading...") and burn the last rounds, hitting the hard stop (observed live
            # mo-1782179985: 75/80 reseed -> restart -> BLOCKED). Force the conclusion IN
            # PLACE in the current session — tools are already blocked by the caller, and
            # there is no budget left to use a relieved context anyway.
            if self._devmode05_taskboard_completed():
                self.session.add_assistant(
                    f"[DEVMODE05 TOOL BUDGET CRITICAL] {tool_rounds}/{max_tools} tool rounds used. "
                    "The taskboard is complete (open=0). Produce [DEVMODE05 COMPLETE] NOW from the "
                    "evidence already gathered. Do NOT call any tools. Do NOT re-read state. Do NOT "
                    "restart the protocol."
                )
                action = "force_complete_in_place"
            else:
                self.session.add_assistant(
                    f"[DEVMODE05 TOOL BUDGET CRITICAL] {tool_rounds}/{max_tools} tool rounds used. "
                    "STOP. Emit [DEVMODE05 BLOCKED] with a continuation capsule NOW, from the current "
                    "context only: completed work, unresolved finding IDs, dirty files, tests run, and "
                    "the exact next action. Do NOT call any tools. Do NOT re-read state. Do NOT restart "
                    "DEVMODE05. The next fresh/resume turn continues from this capsule."
                )
                action = "force_blocked_in_place"
            if monitor:
                monitor.emit("turn_health", {
                    "tool_rounds": tool_rounds, "max_tool_rounds": max_tools,
                    "action": action, "protocol": "devmode05", "reseed": False,
                })
            return
        if self._has_open_runtime_work():
            focus = (
                f"[TOOL BUDGET CRITICAL] {tool_rounds}/{max_tools} tool rounds used. "
                "Active work is not complete. Return [WORK BLOCKED] with a continuation capsule now: "
                "completed work, unresolved taskboard rows, dirty files, tests run, and the exact next action. "
                "Do NOT call any more tools in this exhausted turn. The next fresh work/resume turn must continue from this capsule without re-asking or redoing completed discovery."
            )
            reason = (
                f"work-tool-budget-continuation ({tool_rounds}/{max_tools} rounds)"
            )
            self._perform_context_handoff(
                focus=focus,
                reason=reason,
                latest_user="",
                expose_notice=False,
            )
            return
        focus = (
            f"[TOOL BUDGET CRITICAL] {tool_rounds}/{max_tools} tool rounds used. "
            "You MUST provide your final answer NOW. Do NOT call any more tools. "
            "Summarize your findings and deliver the answer directly."
        )
        reason = (
            f"tool-budget-exhaustion ({tool_rounds}/{max_tools} rounds)"
        )
        self._perform_context_handoff(
            focus=focus,
            reason=reason,
            latest_user="",
            expose_notice=False,
        )
        # Both flags stay True for the remainder of THIS turn.
        # The turn-start reset (run_turn) will clear
        # them for the next turn.

    def _turn_health_tool_blocked_instruction(self, user_input: str | None = None) -> str:
        active_work = False
        devmode05_completed = False
        if user_input is None:
            user_input = str(self or "")
        else:
            active_work = self._has_open_runtime_work()
            devmode05_completed = self._devmode05_taskboard_completed()
        if is_devmode05_activation(user_input):
            if devmode05_completed:
                return (
                    "[TURN HEALTH] Tool calls blocked because DEVMODE05 taskboard is already complete/open=0. "
                    "Do not call more tools. Your next response must start exactly with `[DEVMODE05 COMPLETE]` "
                    "and summarize the existing evidence."
                )
            return (
                "[TURN HEALTH] Tool calls blocked — turn budget exhausted. "
                "Do not call more tools. Your next response must start exactly with "
                "`[DEVMODE05 BLOCKED]` and contain a continuation capsule: completed work, "
                "unresolved finding IDs, dirty files, tests run, and the exact next action."
            )
        if active_work:
            return (
                "[TURN HEALTH] Tool calls blocked — turn budget exhausted. "
                "Do not call more tools. Your next response must start exactly with "
                "`[WORK BLOCKED]` and contain a continuation capsule: completed work, "
                "unresolved taskboard rows, dirty files, tests run, and the exact next action."
            )
        return (
            "[TURN HEALTH] Tool calls blocked — turn budget exhausted. "
            "Produce your final answer now WITHOUT calling any more tools. "
            "Summarise what you've accomplished and what remains."
        )

    def _turn_health_persistent_block_text(self, user_input: str | None = None) -> str:
        active_work = False
        devmode05_completed = False
        if user_input is None:
            user_input = str(self or "")
        else:
            active_work = self._has_open_runtime_work()
            devmode05_completed = self._devmode05_taskboard_completed()
        if is_devmode05_activation(user_input):
            if devmode05_completed:
                return self._devmode05_completed_taskboard_persistent_tool_text()
            return (
                "[DEVMODE05 BLOCKED]\n\n"
                "Tool calls persistently blocked after budget exhaustion. "
                "Continuation required in the next fresh turn from the preserved handoff capsule."
            )
        if active_work:
            return (
                "[WORK BLOCKED]\n\n"
                "Tool calls persistently blocked after budget exhaustion. "
                "Continuation required in the next fresh turn from the preserved handoff capsule."
            )
        return (
            "[TURN HEALTH] Tool calls persistently blocked after budget exhaustion. "
            "The work continues in the next turn. Use /goal for larger tasks."
        )

    def _has_open_runtime_work(self) -> bool:
        gateway = getattr(self, "gateway", None)
        candidates = (
            getattr(gateway, "last_task_board", None),
            getattr(self, "_active_task_board", None),
        )
        for board in candidates:
            if not board:
                continue
            try:
                if getattr(board, "source", "gateway") != "goal" and int(board.open_count() or 0) > 0:
                    return True
            except Exception:
                traceback.print_exc()
        return False

    @staticmethod
    def _self_protocol_completion_boundary_requires_continuation(user_input: str, final_text: str, boundary_report: object | None) -> bool:
        """Force self-protocol modes to continue when completion conflicts with task truth."""
        devmode05 = is_devmode05_activation(user_input)
        vs05 = is_vs05_activation(user_input)
        ifdev05 = is_ifdev05_activation(user_input)
        if not (devmode05 or vs05 or ifdev05):
            return False
        prefix = str(final_text or "").lstrip()[:240].lower()
        if devmode05:
            expected_marker = "[devmode05 complete]"
        elif vs05:
            expected_marker = "[vs05 complete]"
        else:
            expected_marker = "[ifdev05 complete]"
        if expected_marker not in prefix:
            return False
        findings = list(getattr(boundary_report, "findings", ()) or ())
        return any(
            str(getattr(finding, "kind", "") or "") == "taskboard_done_claim_conflict"
            for finding in findings
        )

    # Backward-compatible name used by older tests/callers.
    _devmode05_completion_boundary_requires_continuation = _self_protocol_completion_boundary_requires_continuation

    @staticmethod
    def _boundary_has_done_claim_conflict(boundary_report: object | None) -> bool:
        """True when the consistency boundary flagged a done-claim/open-board conflict."""
        return any(
            str(getattr(finding, "kind", "") or "") == "taskboard_done_claim_conflict"
            for finding in (getattr(boundary_report, "findings", ()) or ())
        )

    @staticmethod
    def _done_claim_task_truth_instruction() -> str:
        """Instruction fed back when an ordinary turn claims done with open rows."""
        return (
            "[TASK TRUTH] Your answer reported completion, but tasks on the board are still open. "
            "Do not claim done while work is open. For each finished task, call complete_task with the "
            "tool evidence that proves it; for anything you cannot finish, block it with a one-line reason. "
            "Then give your final answer."
        )

    @staticmethod
    def _unverified_completion_claim_instruction(label: str) -> str:
        """Fed back once when an answer asserts clean/passing/synced/no-issues state
        but the turn ran no verifying tool. Driven enforcement of verify-before-claiming
        (the prompt-only rule the operator kept having to re-correct)."""
        return (
            f"[VERIFY BEFORE CLAIMING] Your answer makes a completion/cleanliness claim "
            f"({label}) but this turn ran no verifying tool (no test_runner/shell/git_status/"
            "read/search). Do not assert clean / done / passing / synced / no-issues from "
            "assumption or a prior turn. Either run the check now and cite the result, or "
            "rewrite the claim to state only what you actually verified this turn. Then give "
            "your final answer."
        )

    @staticmethod
    def _unverified_current_state_claim_instruction(label: str) -> str:
        """Fed back once when an answer asserts a stale-prone current-state/version fact
        (latest/current version, knowledge-cutoff hedge) but the turn ran no verifying
        tool. The current-state twin of `_unverified_completion_claim_instruction` —
        driven enforcement of verify-before-claiming for world-truth, not just task-truth."""
        return (
            f"[VERIFY BEFORE CLAIMING] Your answer asserts a current-state/version fact "
            f"({label}) but this turn ran no verifying tool (no read/search/web). Recall "
            "goes stale and is often wrong about latest/current versions and releases. "
            "Either check it now with a read/search/web tool and cite what you found, or "
            "rewrite the claim to state only what you can stand behind without checking "
            "(drop 'latest/current', or attribute it as 'as of my training, which may be "
            "outdated'). Then give your final answer."
        )

    @staticmethod
    def _unsourced_external_claim_instruction(label: str) -> str:
        """Fed back once when the turn fetched external content but the answer states a
        current-state fact without naming the source. MO-native source-naming kernel —
        name the page used or say the fetch did not establish it (no citation markup)."""
        return (
            f"[NAME YOUR SOURCE] You fetched external content this turn and your answer "
            f"asserts a current-state fact ({label}) without naming where it came from. "
            "Name the source plainly — the URL or site you used — or, if the fetch did not "
            "actually establish the claim, say so and soften it. Plain references only; no "
            "citation markup. Then give your final answer."
        )

    @staticmethod
    def _self_protocol_task_truth_continuation_instruction(user_input: str) -> str:
        if is_vs05_activation(user_input):
            return vs05_task_truth_continuation_instruction()
        if is_ifdev05_activation(user_input):
            return ifdev05_task_truth_continuation_instruction()
        return devmode05_task_truth_continuation_instruction()
