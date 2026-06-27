"""Provider error/retry/fallback recovery, turn-limit diagnostics, and turn-health handling for the MO agent turn."""

import json
import re
import traceback
from pathlib import Path
from typing import Any

from ..provider.provider_audit import append_provider_audit
from ..tasking.task_board import TaskBoard, record_snapshot
from ..session.session_momentum import maybe_compact_session
from ..state.paths import repo_root
from .agent_utils import _emit_task_board_update


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
    def _tool_call_batch_signature(tool_calls_data: list[dict]) -> str:
        rows: list[dict[str, Any]] = []
        for tc_data in tool_calls_data or []:
            fn = tc_data.get("function") if isinstance(tc_data, dict) else {}
            if not isinstance(fn, dict):
                fn = {}
            name = str(fn.get("name") or "").strip()
            raw_args = str(fn.get("arguments") or "{}")
            try:
                parsed_args = json.loads(raw_args or "{}")
            except json.JSONDecodeError:
                parsed_args = raw_args
            rows.append({"name": name, "arguments": parsed_args})
        if not rows:
            return ""
        return json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @staticmethod
    def _doom_loop_block_text(tool_calls_data: list[dict], repeat_count: int) -> str:
        names: list[str] = []
        for tc_data in tool_calls_data or []:
            fn = tc_data.get("function") if isinstance(tc_data, dict) else {}
            if not isinstance(fn, dict):
                fn = {}
            name = str(fn.get("name") or "tool").strip() or "tool"
            if name not in names:
                names.append(name)
        tool_list = ", ".join(names) if names else "tool call"
        return (
            "[DOOM LOOP BLOCKED] The provider requested the exact same tool batch "
            f"{repeat_count} times in a row ({tool_list}). I stopped before burning the "
            "turn budget. Change approach, use the evidence already returned, or ask for "
            "a smaller next step."
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
            self._activate_final_report_row(task_board, on_board_update=on_board_update, on_board_event=on_board_event)
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
        work_continuation_active = self._has_open_runtime_work()

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
                            "Return a work continuation capsule now — do NOT call more tools."
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
                    "Return a work continuation capsule NOW."
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

    def _capsule_ground_truth(self) -> str:
        """Runtime-computed facts for a continuation/blocked capsule.

        Under a budget-exhausted or handed-off context the model often cannot
        recall its own dirty files / open taskboard rows — it then writes a
        capsule that admits it "cannot reliably state" them, orphaning in-flight
        work (observed live mo-1782383361). Compute them here so the capsule
        carries ground truth regardless of remaining context. Fail-open: returns
        "" on any error so a capsule is never blocked by this.
        """
        parts: list[str] = []
        try:
            gateway = getattr(self, "gateway", None)
            seen: set[int] = set()
            rows: list[str] = []
            for board in (getattr(self, "_active_task_board", None), getattr(gateway, "last_task_board", None)):
                if not board or id(board) in seen:
                    continue
                seen.add(id(board))
                for task in list(getattr(board, "tasks", []) or []):
                    if str(getattr(task, "status", "")) not in ("completed", "skipped"):
                        rows.append(f"#{getattr(task, 'id', '?')} {str(getattr(task, 'text', '') or '')[:60]}")
                if rows:
                    break
            if rows:
                parts.append("open taskboard rows -> " + "; ".join(rows[:12]))
        except Exception:
            traceback.print_exc()
        try:
            import subprocess
            out = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo_root(), capture_output=True, text=True, timeout=10,
            ).stdout
            files = [line[3:].strip() for line in out.splitlines() if line.strip()]
            if files:
                shown = ", ".join(files[:30])
                more = f" (+{len(files) - 30} more)" if len(files) > 30 else ""
                parts.append(f"dirty files [{len(files)}] -> {shown}{more}")
        except Exception:
            traceback.print_exc()
        if not parts:
            return ""
        return (
            " RUNTIME GROUND TRUTH (computed now — copy verbatim into the capsule, do not "
            "guess or omit): " + " | ".join(parts) + "."
        )

    def _force_tool_budget_handoff(self, tool_rounds: int, max_tools: int, *, monitor: Any = None) -> None:
        """Force a context handoff with a strong conclusion mandate.

        Called when tool budget is critically low (≤ handoff_at remaining).
        The handoff document carries an explicit [TOOL BUDGET CRITICAL] focus
        so the model wakes up in a fresh session knowing it must conclude.
        """
        if self._has_open_runtime_work():
            focus = (
                f"[TOOL BUDGET CRITICAL] {tool_rounds}/{max_tools} tool rounds used. "
                "Active work is not complete. Return [WORK BLOCKED] with a continuation capsule now: "
                "completed work, unresolved taskboard rows, dirty files, tests run, and the exact next action. "
                "Do NOT call any more tools in this exhausted turn. The next fresh work/resume turn must continue from this capsule without re-asking or redoing completed discovery."
                + self._capsule_ground_truth()
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
        ground_truth = ""
        if user_input is None:
            # Degenerate static-text form: called with the user text as ``self``
            # (no real agent), so runtime ground truth is unavailable here.
            user_input = str(self or "")
        else:
            active_work = self._has_open_runtime_work()
            ground_truth = self._capsule_ground_truth()
        if active_work:
            return (
                "[TURN HEALTH] Tool calls blocked — turn budget exhausted. "
                "Do not call more tools. Your next response must start exactly with "
                "`[WORK BLOCKED]` and contain a continuation capsule: completed work, "
                "unresolved taskboard rows, dirty files, tests run, and the exact next action."
                + ground_truth
            )
        return (
            "[TURN HEALTH] Tool calls blocked — turn budget exhausted. "
            "Produce your final answer now WITHOUT calling any more tools. "
            "Summarise what you've accomplished and what remains."
        )

    def _turn_health_persistent_block_text(self, user_input: str | None = None) -> str:
        active_work = False
        if user_input is None:
            user_input = str(self or "")
        else:
            active_work = self._has_open_runtime_work()
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
