"""Provider recovery and live-context health handling for the MO agent turn."""

import json
import re
import time
from typing import Any

from ..provider.provider_audit import append_provider_audit
from ..tooling.sandbox import redact_sensitive_text

# Fenced blocks and inline spans are how an answer *quotes* syntax rather than
# emitting it. Stripping them first is the same precision device the path-claim
# gate already uses for hypothetical filenames in example code.
_CODE_SPAN_RE = re.compile(r"```.*?```|~~~.*?~~~|`[^`\n]+`", re.DOTALL)
# Prose left after the quoted payload is removed. Below this the "answer" was
# only ever the payload with a sentence of framing, so it stays suppressed.
_QUOTING_PROSE_MIN_WORDS = 12


class AgentTurnRecoveryMixin:
    """Provider error/retry/fallback recovery and live-context handling."""

    def _record_runtime_diagnostic(
        self,
        kind: str,
        summary: str,
        *,
        detail: str = "",
        tool: str = "",
        request: int = 0,
    ) -> None:
        """Keep one safe, session-local fact for an immediate error follow-up.

        Provider/tool diagnostics already live in private audit and trace lanes,
        but those lanes are intentionally absent from normal conversation. This
        bounded fact prevents the next provider from guessing across generic
        retry prose. It adds no disk writer and no recurring context cost.
        """
        session = getattr(self, "session", None)
        if session is None:
            return
        session._last_runtime_diagnostic = {
            "kind": str(kind or "runtime_error")[:80],
            "summary": redact_sensitive_text(str(summary or ""))[:500],
            "detail": redact_sensitive_text(str(detail or ""))[:500],
            "tool": str(tool or "")[:80],
            "provider": str(getattr(self, "provider_name", "") or "")[:80],
            "model": str(getattr(self, "model", "") or "")[:120],
            "request": max(0, int(request or 0)),
            "turn_count": max(0, int(getattr(session, "turn_count", 0) or 0)),
        }

    def _report_provider_error(self, err_msg: str, reason: str | None, *,
                               provider_requests: str | int, monitor: Any, on_activity: Any,
                               route: tuple[str, str, str] | None = None) -> None:
        """Report one selected-route or explicit no-tools provider error.

        Recovery/fallback control flow stays with the caller. An explicit route
        avoids mutating or writing diagnostics against the selected route.
        """
        selected_route = route is None
        surface, provider_name, model_name = route or (
            self._provider_surface(), self.provider_name, self.model,
        )
        activity_reason = reason or err_msg[:40]
        reason = reason or "error"
        if on_activity:
            on_activity(f"MO provider error: {activity_reason}")
        append_provider_audit(
            "provider_error",
            surface=surface,
            provider=provider_name,
            model=model_name,
            request=provider_requests,
            session_id=getattr(self.session, "session_id", ""),
            worker_id=self._provider_worker_id(),
            reason=reason,
            ok=False,
        )
        if monitor:
            payload = {"request": provider_requests, "provider": provider_name, "reason": reason, "error": err_msg[:300]}
            if not selected_route:
                payload = {"request": provider_requests, "surface": surface, **payload}
            monitor.emit("provider_error", payload)
        if selected_route:
            self._record_runtime_diagnostic(
                "provider_error",
                f"a provider request error from {provider_name}/{model_name}",
                detail=err_msg,
                request=provider_requests,
            )

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

    @classmethod
    def _looks_like_raw_tool_payload(cls, content: str, tools_offered: bool = True) -> bool:
        """True when visible text *is* an internal tool payload rather than an answer.

        Emitting tool syntax and explaining tool syntax produce the same tokens,
        so matching tokens alone cannot tell them apart — and the answer MO most
        needs to deliver about this defect is the one that quotes it. Treating a
        quoted payload as an attempted call repeatedly destroyed a valid root-
        cause explanation and triggered a provider fallback that could not help.

        So syntax found only inside code fences/spans is a quotation, and a real
        answer survives without it. A payload dressed in one line of framing does
        not, and still suppresses.
        """
        text = str(content or "")
        if not cls._payload_syntax_present(text, tools_offered):
            return False
        visible = _CODE_SPAN_RE.sub(" ", text)
        if cls._payload_syntax_present(visible, tools_offered):
            return True  # bare in the prose: emitted, not quoted
        return len(visible.split()) < _QUOTING_PROSE_MIN_WORDS

    @staticmethod
    def _payload_syntax_present(content: str, tools_offered: bool = True) -> bool:
        """Whether internal tool-call syntax appears anywhere in ``content``.

        ``tools_offered`` is the number/presence of schemas actually sent for the
        request. When none were sent the model cannot have been attempting a real
        call, so only unambiguous machine syntax counts; the prose-shaped
        heuristics below are exactly what an ordinary answer *about* tools, paths
        or JSON looks like, and treating that as a payload discards a real reply.
        """
        text = str(content or "").strip()
        if not text:
            return False
        lowered = text.lower()

        # --- unambiguous machine syntax: never ordinary prose --------------------
        if "[tool calls requested]" in lowered:
            return True
        # DeepSeek can emit its internal DSML protocol as visible text while
        # reporting finish_reason=stop and zero structured tool calls.  Fail
        # closed into the existing retry path; narrated actions must never be
        # shown as an answer or parsed/executed optimistically.
        if re.search(r"<\s*[|｜]+\s*dsml\s*[|｜]+", text, re.IGNORECASE):
            return True
        tool_markers = ("<tool_call", "</tool_call", "<tool_use", "</tool_use")
        if any(marker in lowered for marker in tool_markers):
            return True
        if re.search(
            r"<\s*(?:tool_call|tool_use|read_file|write_file|edit_file|shell|test_runner|find_files|git_status|tool_search)\b",
            text,
            re.IGNORECASE,
        ):
            return True
        if re.search(r"<\s*tool(?:_call|_use)?\s+[^>]*\bname\s*=", text, re.IGNORECASE):
            return True
        if re.search(r'^\s*(?:\{|\[).*"tool_calls"\s*:', text, re.DOTALL):
            return True

        # --- prose-shaped heuristics: only meaningful when tools were offered ----
        if not tools_offered:
            # No schemas were sent for this request, so there was no tool to call
            # and nothing to suppress. MO is expected to discuss tools, paths and
            # JSON in plain prose here (observed: a 934-output-token answer thrown
            # away, then a provider switch that no tool-call failure could explain).
            return False
        if re.search(
            r"(?im)^\s*(?:i\s+will\s+|let\s+me\s+|now\s+)?"
            r"(?:edit_file|write_file|read_file|test_runner|project_bridge)\s*\(",
            text,
        ):
            return True
        # Text-only ReAct-style providers can narrate a tool request as two plain
        # text fields instead of returning a structured call.  Require both
        # adjacent field labels so ordinary prose about an "action" remains
        # user-facing while the unexecuted payload never reaches any surface.
        if re.search(
            r"(?im)^\s*action\s*:\s*[a-z_][\w.-]*\s*\r?\n"
            r"\s*action\s+input\s*:\s*\S",
            text,
        ):
            return True
        # Some chat providers retry XML-style tool syntax as a bracketed
        # narration. It is still an internal action payload, not user-facing
        # prose (observed: "[Tool call: set_plan with mode=start, steps: ...]").
        if re.search(r"(?im)^\s*\[\s*tool\s+calls?\s*:\s*[a-z_][\w.-]*\b", text):
            return True
        if re.search(r'^\s*\{\s*"(?:path|command|root|old_text|new_text)"\s*:', text, re.DOTALL):
            return True
        return re.search(r'"old_text"\s*:\s*.*"new_text"\s*:', text, re.DOTALL) is not None

    @staticmethod
    def _raw_tool_payload_retry_message() -> str:
        return (
            "[TOOL PAYLOAD RETRY] Previous response looked like internal tool syntax, not a user-facing answer. "
            "Use the actual tool interface when action is needed; otherwise answer in normal prose."
        )

    @staticmethod
    def _tool_call_batch_signature(
        tool_calls_data: list[dict], *, include_task_progress: bool = False,
    ) -> str:
        rows: list[dict[str, Any]] = []
        for tc_data in tool_calls_data or []:
            fn = tc_data.get("function") if isinstance(tc_data, dict) else {}
            if not isinstance(fn, dict):
                fn = {}
            name = str(fn.get("name") or "").strip()
            # Admitted task progress advances the board, not repetitive work.
            # Rejected calls cannot advance it and must still be compared.
            if name == "complete_task" and not include_task_progress:
                continue
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
    def _blocked_tool_recovery_instruction(tool_calls_data: list[dict], blocker: str) -> str:
        names: list[str] = []
        for tc_data in tool_calls_data or []:
            fn = tc_data.get("function") if isinstance(tc_data, dict) else {}
            if not isinstance(fn, dict):
                fn = {}
            name = str(fn.get("name") or "tool").strip() or "tool"
            if name not in names:
                names.append(name)
        tool_list = ", ".join(names) if names else "tool call"
        exact_blocker = str(blocker or "The tool batch was blocked before execution.").strip()
        return (
            "[BLOCKED TOOL RETRY] The previous identical batch did not execute "
            f"({tool_list}). Continue from the exact blocker: {exact_blocker} "
            "Do not request the same batch again. Use a genuinely authorized route or report "
            "the blocker plainly; never expose this internal instruction."
        )

    @staticmethod
    def _tool_batch_is_task_completion_progress(tool_calls_data: list[dict], task_board: object | None) -> bool:
        """True for repeated complete_task calls that can still advance open rows."""
        if not tool_calls_data or task_board is None:
            return False
        try:
            if int(task_board.open_count()) <= 0:
                return False
            active = task_board.active_task_id()
            task = task_board.task(active) if active else None
            if task is None:
                return False
            from ..tasking.task_board import _task_has_qualifying_completion_evidence, _task_requires_evidence
            if _task_requires_evidence(task) and not _task_has_qualifying_completion_evidence(task):
                return False
        except Exception:
            return False
        for tc_data in tool_calls_data:
            fn = tc_data.get("function") if isinstance(tc_data, dict) else {}
            if not isinstance(fn, dict) or str(fn.get("name") or "") != "complete_task":
                return False
        return True

    def _check_turn_health(
        self,
        tool_rounds: int,
        extra_context: str | None,
        *,
        provider_requests: int | None = None,
        monitor: Any = None,
        tools: list[dict] | None = None,
    ) -> str | None:
        """Compact observations and hand off only for current context pressure."""
        cfg = getattr(self, "config", {}) or {}
        agent_cfg = cfg.get("agent", {}) if isinstance(cfg, dict) else {}
        context_pressure_handoff_threshold = float(
            agent_cfg.get("turn_health_context_pressure_handoff_at", 0.90) or 0.90
        )
        context_pressure_critical_threshold = float(
            agent_cfg.get("turn_health_context_pressure_critical_at", 0.95) or 0.95
        )
        context_handoff_allowed = bool(getattr(self, "context_handoff_enabled", True))
        if context_handoff_allowed:
            is_foreground = getattr(self, "_is_foreground_session", None)
            if callable(is_foreground):
                try:
                    context_handoff_allowed = bool(is_foreground())
                except Exception:
                    context_handoff_allowed = False

        from ..session.handoff import context_pressure as _cp
        pressure_metrics = _cp(self, extra_context=extra_context or "", tools=tools)
        # Share this unchanged projection; the compactor owns its thresholds.
        # Consult it at every provider checkpoint so an active turn can archive
        # bulky reads before they are replayed on every later request.
        from ..session.session_momentum import maybe_compact_session

        compacted = maybe_compact_session(
            self, stage="turn_health", extra_context=extra_context or "",
            monitor=monitor, tools=tools, pressure_metrics=pressure_metrics,
        )
        if compacted.get("changed"):
            pressure_metrics = _cp(self, extra_context=extra_context or "", tools=tools)
        cp = float(pressure_metrics.get("pressure") or 0.0)
        pressure_source = str(pressure_metrics.get("pressure_source") or "unknown")
        char_ratio = float(pressure_metrics.get("char_ratio") or 0.0)
        token_ratio = float(pressure_metrics.get("token_ratio") or char_ratio)
        message_ratio = float(pressure_metrics.get("message_ratio") or 0.0)
        pressure_for_handoff = char_ratio if pressure_source.startswith("messages") else cp
        request_index = max(0, int(provider_requests or 0))
        if context_handoff_allowed:
            pressure_payload = {
                "pressure_source": pressure_source,
                "raw_pressure": float(pressure_metrics.get("raw_pressure") or cp or 0.0),
                "char_ratio": char_ratio,
                "estimated_tokens": pressure_metrics.get("estimated_tokens"),
                "budget_tokens": pressure_metrics.get("budget_tokens"),
                "token_ratio": token_ratio,
                "message_ratio": message_ratio,
                "message_count": pressure_metrics.get("message_count"),
                "max_history": pressure_metrics.get("max_history"),
                "provider_requests": request_index,
            }
            critical = pressure_for_handoff >= context_pressure_critical_threshold
            high = pressure_for_handoff >= context_pressure_handoff_threshold
            cooldown = getattr(self, "_handoff_cooldown_active", lambda: False)()
            budget_tokens = float(pressure_metrics.get("budget_tokens") or 0)
            if budget_tokens:
                seed_fits = float(getattr(self, "_handoff_seed_tokens", 0) or 0) < budget_tokens * context_pressure_critical_threshold
            else:
                budget = float(pressure_metrics.get("budget_chars") or 0)
                seed_fits = not budget or float(getattr(self, "_handoff_seed_chars", 0) or 0) < budget * context_pressure_critical_threshold
            # A critical handoff may skip the ordinary turn cooldown ONLY when the
            # prior required seed fits the estimate AND the critical path is not
            # itself cycling. The same-turn wall-clock rate limit below is the
            # backstop: a looping turn that re-enters critical pressure every few
            # seconds must not spawn a fresh handoff session each time.
            critical_rate_limited = bool(
                getattr(self, "_critical_handoff_rate_limited", lambda: False)()
            )
            # A critical handoff is also "high", so the rate limit must cover the
            # high branch too when a critical handoff already fired this turn.
            # Otherwise the loop just re-enters through the high path.
            if critical and seed_fits:
                fire = not critical_rate_limited
            elif high and not cooldown:
                fire = not critical_rate_limited
            else:
                fire = False
            if fire:
                self._force_context_pressure_handoff(
                    pressure_metrics,
                    critical=critical,
                    monitor=monitor,
                )
                builder = getattr(self, "_build_extra_context", None)
                if callable(builder):
                    extra_context = builder(str(getattr(self, "_current_user_input", "") or ""))
                if monitor:
                    monitor.emit("turn_health", {
                        "level": "critical" if critical else "handoff",
                        "action": "context_pressure_handoff",
                        "pressure": cp,
                        "threshold": (
                            context_pressure_critical_threshold
                            if critical
                            else context_pressure_handoff_threshold
                        ),
                        "chars": pressure_metrics.get("chars"),
                        "budget_chars": pressure_metrics.get("budget_chars"),
                        **pressure_payload,
                        "label": "orientation only, not proof",
                    })
                # If the required seed alone exceeds this estimate, honor the
                # existing cooldown instead of spinning on every request. The
                # provider owns its actual limit and existing overflow recovery.
                after = _cp(self, extra_context=extra_context or "", tools=tools)
                self._handoff_seed_chars = int(after.get("chars") or 0)
                self._handoff_seed_tokens = int(after.get("estimated_tokens") or 0)
                self._critical_handoff_armed_at = time.monotonic()
                return extra_context

        return extra_context

    def _critical_handoff_rate_limited(self) -> bool:
        """Rate-limit the critical handoff path within a single turn.

        The ordinary turn cooldown (2 turns) is intentionally skipped for a
        critical handoff whose seed fits, so that a genuinely overflowing
        context can recover immediately. But that same skip lets a looping turn
        re-trigger the critical path on every provider round — the handoff storm
        observed in long sessions. A wall-clock floor between critical handoffs
        keeps the first recovery while absorbing the loop.
        """
        raw_floor = (getattr(self, "config", {}) or {}).get("agent", {}).get(
            "turn_health_critical_handoff_min_interval_seconds", 60.0
        )
        floor = 60.0 if raw_floor is None else float(raw_floor)
        if floor <= 0.0:
            return False
        armed = float(getattr(self, "_critical_handoff_armed_at", 0.0) or 0.0)
        if armed <= 0.0:
            return False
        return time.monotonic() - armed < floor

    def _force_context_pressure_handoff(
        self,
        pressure_metrics: dict[str, Any],
        *,
        critical: bool,
        monitor: Any = None,
    ) -> None:
        """Force a context-pressure handoff without stopping useful work."""
        pressure = float((pressure_metrics or {}).get("pressure") or 0.0)
        chars = int((pressure_metrics or {}).get("chars") or 0)
        budget = int((pressure_metrics or {}).get("budget_chars") or 0)
        char_ratio = float((pressure_metrics or {}).get("char_ratio") or 0.0)
        tokens = int((pressure_metrics or {}).get("estimated_tokens") or 0)
        token_budget = int((pressure_metrics or {}).get("budget_tokens") or 0)
        token_ratio = float((pressure_metrics or {}).get("token_ratio") or char_ratio)
        message_count = int((pressure_metrics or {}).get("message_count") or 0)
        max_history = int((pressure_metrics or {}).get("max_history") or 0)
        message_ratio = float((pressure_metrics or {}).get("message_ratio") or 0.0)
        source = str((pressure_metrics or {}).get("pressure_source") or "unknown")
        label = "critical" if critical else "high"
        detail = (
            f"source={source}; estimated_tokens={tokens}/{token_budget} ({token_ratio:.0%}); "
            f"chars={chars}/{budget} ({char_ratio:.0%}); "
            f"messages={message_count}/{max_history} ({message_ratio:.0%})"
        )
        reason = f"context-pressure-{label} (pressure={pressure:.0%}; {detail})"
        self._perform_context_handoff(
            focus="",
            reason=reason,
            latest_user="",
            expose_notice=False,
        )

    # ── Error-reporting prompt ──────────────────────────────────────────────

    _ERROR_REPORT_PROMPT = (
        "\n\n⚠️  MO could not complete this turn. "
        'If you\'d like me to report it to IQMO anyway, just reply "yes" or "report".'
    )
    _PROVIDER_FAILURE_GUIDANCE = (
        "\n\nThe provider response did not complete. Retry the same request once; MO retries "
        "automatically only when no visible partial output could be duplicated. Use `/model` only if it repeats."
    )
    _PROVIDER_AUTH_GUIDANCE = (
        "\n\nThe selected provider rejected its authentication. Refresh or re-authenticate that "
        "provider (sign in again for OAuth) before retrying, or choose another model with `/model`."
    )
    _PROVIDER_QUOTA_GUIDANCE = (
        "\n\nThe selected provider's usage or billing allowance is exhausted. "
        "Work remains saved for continuation. Retry after the provider restores "
        "availability, or explicitly select an available provider with `/model`. "
        "Repeating the request immediately will not restore the allowance."
    )

    def _maybe_append_error_report_prompt(self, message: str, context: dict | None = None) -> str:
        """Render actionable provider failures; offer reporting for unknown defects."""
        details = dict(context or {})
        if details.get("kind") == "provider_error" and details.get("reason") not in {None, "", "error"}:
            if details.get("error_kind") == "auth":
                return message + self._PROVIDER_AUTH_GUIDANCE
            if details.get("error_kind") in {"quota", "balance"}:
                return message + self._PROVIDER_QUOTA_GUIDANCE
            return message + self._PROVIDER_FAILURE_GUIDANCE
        session = getattr(self, "session", None)
        if session is not None:
            session._pending_error_report = details
            session._pending_error_report.setdefault("session_id", getattr(session, "session_id", ""))
            session._pending_error_report.setdefault("timestamp", time.time())
        return message + self._ERROR_REPORT_PROMPT

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
        without claim-appropriate evidence. Driven enforcement of verify-before-claiming
        (the prompt-only rule the operator kept having to re-correct)."""
        return (
            f"[VERIFY BEFORE CLAIMING] Your answer makes a completion/cleanliness claim "
            f"({label}) but this turn's evidence does not establish it (use a claim-appropriate "
            "test_runner/shell/git_status/read/search check). Do not assert clean / done / "
            "passing / synced / no-issues from assumption or a prior turn. Either run the "
            "check now and cite the result, or rewrite the claim to state only what you "
            "actually verified this turn. For a composite delivery workflow, retain its verified "
            "subactions, state each omitted action as not performed or not verified, and do not "
            "repeat the workflow shorthand as complete. Remove or soften every other unsupported completion, "
            "current-state, or documentation claim in the same answer; the runtime allows one "
            "claim-correction continuation per turn. Then give your final answer."
        )

    @staticmethod
    def _turn_relative_tool_unavailability_instruction(label: str) -> str:
        """Correct a denial inferred from the provider's routed working catalog."""
        return (
            f"[ROUTE DISCOVERY REQUIRED] Your answer makes a {label}. The schemas visible "
            "to one provider request are a routed working subset, not proof that MO lacks "
            "the tool, connection, query, or capability. Do not tell the operator it is "
            "unavailable in this/that turn. Call tool_search with the needed capability or "
            "exact tool name, then use the surfaced owner. If discovery or the actual owner "
            "returns a concrete rejection, report that exact route/result; otherwise perform "
            "the requested check. Then give your final answer."
        )

    @staticmethod
    def _unfulfilled_action_promise_instruction(label: str) -> str:
        """Continue when a provider announced evidence work but executed none."""
        return (
            f"[ACTION NOT EXECUTED] Your response is only a {label}. Do not finish by "
            "announcing work for later. Use the already offered tool owner now, or call "
            "tool_search if its schema is not visible. Continue through the tool result and "
            "answer the operator in this turn; report only an actual returned blocker."
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
    def _unoffered_tool_instruction(unoffered: list[str]) -> str:
        """Correct a call to a tool outside the current request's admitted scope.

        The provider-visible catalog is recomputed per request from live state
        (turn intent, board, role, extension allowlist, catalog mode), so a tool
        the provider legitimately held in an earlier request can be absent from
        the current scope. Discovery already tried to activate it; when the scope
        still excludes it, that is a correctable condition — name the situation
        and the valid next step instead of terminating the turn.
        """
        names = ", ".join(f"`{name}`" for name in unoffered[:5])
        return (
            f"[TOOL NOT IN CURRENT SCOPE] This request did not offer {names}. The "
            "provider-visible catalog is a per-request working set, not a statement that "
            "the capability is missing: turn intent, the active board, the active role, "
            "and the catalog mode all narrow it. Do not repeat the same call. Use a tool "
            "that was offered in this request, or call tool_search with the capability or "
            "exact tool name to activate the owning route, then use the surfaced owner. If "
            "the current scope requires a different first step (for example materializing a "
            "goal plan with set_plan before work tools become available), take that step. "
            "Then continue the task."
        )

    @staticmethod
    def _uncaptured_operator_knowledge_instruction(label: str) -> str:
        """Fed back once when the operator shared durable personal/environment knowledge or a
        code convention this turn but the model recorded nothing. Input-side twin of the claim
        gates — driven enforcement of the record-durable-knowledge rule (record_profile_fact /
        record_convention are wired but almost never driven; behavior-rule learning has
        detectors, fact/convention capture did not until now)."""
        return (
            f"[CAPTURE OPERATOR KNOWLEDGE] The operator appears to have shared durable "
            f"personal/environment knowledge this turn ({label}) — the kind MO is meant to "
            "remember across sessions (preferences, servers, repos, deploy methods, project paths, "
            "credential LOCATIONS never values, host aliases) or a code convention for an area. "
            "You recorded nothing. If it is genuinely durable and reusable, record it now: "
            "record_profile_fact with category='preference' for a stated preference/dislike or the "
            "matching category for another operator/environment fact; use record_convention (a rule "
            "+ a file-glob scope) for a code convention. If it is transient, a question, or not "
            "actually durable, do "
            "nothing and give your final answer. One capture nudge per turn."
        )
