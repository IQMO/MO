"""Ghost side-panel controller and routing mixin for the MO TUI."""
from __future__ import annotations

import json
from pathlib import Path
import re as _re
import threading
import traceback

from core.backend_monitor import get_monitor
from core.ghost.ghost_context import build_ghost_context
from core.ghost.ghost_routing import GhostRouteSuggestion, enhance_route_objective, is_route_confirmation, is_route_rejection, recommend_ghost_route
from core.ghost.ghost_tool_context import build_ghost_tool_context
from core.provider.provider import clean_provider_error, fallback_reason
from core.worker_runtime import ensure_worker_runtime
from core.worker_scheduler import decide_worker_route
from core.workers import ensure_worker_registry
from core.gateway_helpers import words as _words

from .ghost import ghost_safe_messages


_GHOST_STOP_TARGET_RE = _re.compile(
    r"\b(?:stop|cancel|abort|interrupt|halt)\s+"
    r"(?:it|this|that|mo|main\s+mo|current(?:\s+(?:run|turn|work|task))?|"
    r"the\s+(?:run|turn|work|task)|mo'?s\s+(?:run|turn|work|task))\b",
    _re.I,
)
_GHOST_BARE_STOP_RE = _re.compile(r"^\s*(?:stop|cancel|abort|interrupt|halt)\s*[.!]*\s*$", _re.I)
_GHOST_CONDITIONAL_STOP_RE = _re.compile(
    r"\bif\s+(?:not|no|it\s+(?:hasn'?t|has\s+not)|mo\s+(?:hasn'?t|has\s+not))"
    r".{0,100}\b(?:stop|cancel|abort|interrupt|halt)\s+"
    r"(?:it|this|that|mo|main\s+mo|the\s+(?:run|turn|work|task))\b|"
    r"\b(?:stop|cancel|abort|interrupt|halt)\s+"
    r"(?:it|this|that|mo|main\s+mo|the\s+(?:run|turn|work|task))\b"
    r".{0,100}\bif\s+(?:not|no)\b",
    _re.I | _re.S,
)
_GHOST_STOP_CONFIRM_RE = _re.compile(r"^\s*(?:yes|yep|yeah|ok|okay|sure)(?:\b.*)?$", _re.I)
_GHOST_WRITE_TOOLS = {"edit_file", "write_file"}
_GHOST_MAIN_ROUTE_OFFER_RE = _re.compile(
    r"\b(?:main\s+mo|mo\s+can\s+handle|let\s+me\s+send|send\s+(?:it|this|that)?\s*(?:to\s+)?mo|"
    r"route\s+(?:it|this|that)?\s*(?:to\s+)?mo|i(?:'|’)ll\s+(?:send|route).{0,40}\bmo)\b",
    _re.I,
)
_GHOST_CURRENT_ADJUSTMENT_RE = _re.compile(
    r"\b(?:this|that|it|current|same|above|without|instead|keyboard|mouse|playable|unplayable|doesn'?t\s+work|not\s+work(?:ing)?)\b",
    _re.I,
)


class GhostControllerMixin:
    # ── Ghost panel on/off (single source for all input paths) ──────────

    def _apply_ghost_on(self):
        """Activate Ghost panel internally without exposing a slash-command input mode."""
        self._ghost_enabled = True
        self._ghost_input_mode = False
        self._ghost_unread_count = 0
        self._prt_done_unread = False
        self._ghost_panel_open = True
        self._ghost_expanded = False
        self._ghost_panel_lines = self._ghost_history_panel_lines()

    def _apply_ghost_off(self):
        """Deactivate Ghost panel and clear related state."""
        self._ghost_enabled = False
        self._ghost_input_mode = False
        self._ghost_panel_open = False
        self._ghost_expanded = False
        self._ghost_scroll_from_bottom = 0
        self._ghost_panel_lines = []
        self._ghost_pending_route = None
        if self._input_buf and self._input_buf.text.startswith(("/ghost", "/gh")):
            self._input_buf.text = ""

    def _ghost_context_snapshot(self, question: str = "") -> str:
        ui_state = {
            "main_busy": self.busy,
            "activity": self.activity_text,
            "board_text": self.board_text,
            "goal_worker_active": self._goal_worker_active,
            "goal_backgrounded": self._goal_backgrounded,
            "goal_queued": self._goal_queued,
            "goal_stage": self._goal_stage,
            "goal_elapsed": self._goal_elapsed_text() if (self._goal_worker_active or self._goal_running) else "",
            "queued_count": self._pending_inputs.qsize(),
        }
        return build_ghost_context(self.agent, self.gateway, question=question, ui_state=ui_state)

    def _ghost_panel_ask(self, question: str):
        """Run a ghost side-question in background and show result in panel."""
        if self._handle_ghost_route_reply(question):
            return
        if self._handle_ghost_control_reply(question):
            return
        self._ghost_unread_count = 0

        spinner = "Replying"
        route_suggestion = recommend_ghost_route(
            question,
            main_busy=self.busy,
            goal_active=bool(self._goal_worker_active or getattr(self.agent, "_goal_active", False)),
        ) or self._infer_followup_route_suggestion(question)
        self._ghost_request_seq += 1
        request_id = self._ghost_request_seq
        self._ghost_active_request_id = request_id
        self._ghost_panel_lines = [
            ("class:ghost-user", question),
            ("class:ghost-thinking", spinner),
        ]
        self._ghost_scroll_from_bottom = 0
        self._ghost_panel_open = True
        monitor = get_monitor()
        if monitor:
            monitor.emit("ghost_event", {
                "kind": "ask",
                "request": f"ghost-panel-{request_id}",
                "route": str(getattr(route_suggestion, "route", "") or ""),
                "user_preview": question[:240],
            })
        if self._app:
            self._app.invalidate()

        def _run():
            live_context = ""
            monitor = get_monitor()
            registry = ensure_worker_registry(self.agent)
            ghost_record = registry.create(kind="ghost", source="user", route="main", objective=question, state="running", note="ghost panel side-chat")
            try:
                # Ghost is a separate side-agent: it can inspect visible MO context,
                # but must not merge identity with main MO or treat main claims as proof.
                live_context = self._ghost_context_snapshot(question)
                tool_context = build_ghost_tool_context(self.agent, question, route_suggestion=route_suggestion)
                prompt = f"Operator side-question for Ghost (brief answer):\n{question}"
                if route_suggestion:
                    enhanced_objective = enhance_route_objective(route_suggestion.objective)
                    prompt = (
                        f"{prompt}\n\nDefault MO route objective from local profile/prompt enhancer:\n"
                        f"Suggested ask: {enhanced_objective}\n\n"
                        "If this should be routed to MO, include one line starting 'Suggested ask:' that improves that objective using visible state and read-only tool scout facts. "
                        "Do not paste generic templates; make the ask specific to the operator's request, preserve intent, and do not broaden scope."
                    )
                context_parts = []
                if live_context:
                    context_parts.append("Current visible MO state:\n" + live_context)
                if tool_context:
                    context_parts.append(tool_context)
                if context_parts:
                    prompt = "\n\n".join(context_parts) + "\n\n" + prompt
                raw_messages = list(getattr(self.agent.session, "messages", []) or [])
                messages = self._ghost_provider_messages(raw_messages, prompt)
                if hasattr(self.agent, "provider_scope"):
                    with self.agent.provider_scope("ghost_panel", worker_id=ghost_record.id):
                        response, _provider = self.agent.complete_ghost_no_tools(
                            surface="ghost_panel",
                            request=f"ghost-panel-{request_id}",
                            messages=messages,
                            max_tokens=min(int(self.agent.max_tokens or 2000), 2000),
                            monitor=monitor,
                        )
                else:
                    response, _provider = self.agent.complete_ghost_no_tools(
                        surface="ghost_panel",
                        request=f"ghost-panel-{request_id}",
                        messages=messages,
                        max_tokens=min(int(self.agent.max_tokens or 2000), 2000),
                        monitor=monitor,
                    )
                result = self._ghost_visible_response(response, route_suggestion)
                finish_reason = str(getattr(response, "finish_reason", "") or "")
                if self._ghost_response_incomplete(result, finish_reason):
                    retry_messages = messages + [
                        {"role": "assistant", "content": result},
                        {"role": "user", "content": "Finish the Ghost answer in 3 concise bullets using visible final text only. Do not repeat earlier text."},
                    ]
                    if hasattr(self.agent, "provider_scope"):
                        with self.agent.provider_scope("ghost_panel_retry", worker_id=ghost_record.id):
                            retry, _retry_provider = self.agent.complete_ghost_no_tools(
                                surface="ghost_panel_retry",
                                request=f"ghost-panel-{request_id}-retry",
                                messages=retry_messages,
                                max_tokens=min(int(self.agent.max_tokens or 1200), 1200),
                                monitor=monitor,
                            )
                    else:
                        retry, _retry_provider = self.agent.complete_ghost_no_tools(
                            surface="ghost_panel_retry",
                            request=f"ghost-panel-{request_id}-retry",
                            messages=retry_messages,
                            max_tokens=min(int(self.agent.max_tokens or 1200), 1200),
                            monitor=monitor,
                        )
                    retry_text = str(getattr(retry, "content", "") or "").strip()
                    if retry_text:
                        result = f"{result.rstrip()}\n{retry_text}"
            except Exception as exc:
                result = self._ghost_provider_unavailable_response(question, exc, route_suggestion)
                registry.update(ghost_record.id, "blocked", f"ghost provider error: {type(exc).__name__}", result_summary=result[:240])
            if request_id != self._ghost_active_request_id:
                self._record_stale_ghost_reply(question, result, route_suggestion)
                return
            route_name = ""
            if route_suggestion:
                route_name = route_suggestion.route
                enhanced = enhance_route_objective(route_suggestion.objective, result)
                pending_suggestion = GhostRouteSuggestion(route_suggestion.route, enhanced, route_suggestion.reason, route_suggestion.risky)
                result = f"{result}\n\n{pending_suggestion.offer_text()}"
                self._ghost_pending_route = pending_suggestion
            else:
                inferred_route = self._route_suggestion_from_ghost_response(question, result)
                if inferred_route:
                    route_name = inferred_route.route
                    self._ghost_pending_route = inferred_route
                else:
                    self._ghost_pending_route = None
            if (registry.get(ghost_record.id) or ghost_record).state == "running":
                registry.update(ghost_record.id, "completed", "ghost panel replied", result_summary=result[:240])
            self._record_ghost_history("reply", question, result, route=route_name)
            was_hidden = not self._ghost_panel_open
            self._ghost_panel_lines = [
                ("class:ghost-user", question),
                ("class:ghost-response", result),
            ]
            if was_hidden:
                self._ghost_unread_count += 1
            self._ghost_scroll_from_bottom = 0
            if self._app:
                self._app.invalidate()

        threading.Thread(target=_run, daemon=True).start()

    def _record_stale_ghost_reply(self, question: str, result: str, route_suggestion: GhostRouteSuggestion | None = None) -> None:
        """Keep late Ghost replies in history without overwriting the active panel."""
        route_name = str(getattr(route_suggestion, "route", "") or "")
        text = str(result or "stale Ghost reply ignored").strip() or "stale Ghost reply ignored"
        self._record_ghost_history("reply_stale", question, text, route=route_name)

    @staticmethod
    def _ghost_provider_unavailable_response(question: str, exc: Exception, route_suggestion: GhostRouteSuggestion | None = None) -> str:
        """Return honest Ghost fallback text without leaking raw provider internals."""
        clean = clean_provider_error(str(exc))
        reason = fallback_reason(str(exc)) or "provider error"
        lines = [
            f"Ghost provider is unavailable ({reason}).",
            f"Provider said: {clean[:180]}",
            "Main MO is unaffected; retry Ghost later or route the request to MO.",
        ]
        if route_suggestion:
            objective = enhance_route_objective(route_suggestion.objective or question)
            lines.append(f"Suggested ask: {objective}")
        return "\n".join(lines)

    @staticmethod
    def _ghost_provider_messages(raw_messages: list[dict], prompt: str) -> list[dict]:
        return ghost_safe_messages(raw_messages, prompt)

    @staticmethod
    def _ghost_visible_response(response: object, route_suggestion: GhostRouteSuggestion | None = None) -> str:
        content = str(getattr(response, "content", "") or "").strip()
        if content:
            return content
        if route_suggestion:
            return "I did not get a visible Ghost reply, but MO can handle this if you want to route it."
        return "Ghost did not return visible text. Try once more or switch Ghost to another provider if it repeats."

    @staticmethod
    def _ghost_response_incomplete(text: str, finish_reason: str = "") -> bool:
        stripped = str(text or "").strip()
        if str(finish_reason or "").lower() == "length":
            return True
        if not stripped:
            return False
        if stripped.endswith((".", "!", "?", ")", "]", "`")):
            return False
        last = stripped.rsplit("\n", 1)[-1].strip()
        return len(last.split()) <= 5 and not last.endswith((':', ';'))

    def _handle_ghost_control_reply(self, text: str) -> bool:
        """Handle operator control requests locally instead of asking Ghost to improvise.

        Ghost can coordinate UI routing, but stopping the current main turn is a
        local UI control. Keeping this deterministic prevents Ghost from saying
        it cannot stop MO, then accidentally queueing more work on confirmation.
        """
        stop_intent = self._ghost_stop_intent(text)
        if stop_intent is None and self._ghost_stop_confirmation_from_history(text):
            stop_intent = "direct"
        if stop_intent is None:
            return False

        response = self._execute_ghost_stop_request(conditional=stop_intent == "conditional")
        self._ghost_pending_route = None
        self._ghost_unread_count = 0
        self._record_ghost_history("control_stop", text, response, route="main")
        self._ghost_panel_lines = [
            ("class:ghost-user", text),
            ("class:ghost-response", response),
        ]
        self._ghost_scroll_from_bottom = 0
        self._ghost_panel_open = True
        if self._app:
            self._app.invalidate()
        return True

    @staticmethod
    def _ghost_stop_intent(text: str) -> str | None:
        value = str(text or "").strip()
        if not value:
            return None
        if _GHOST_CONDITIONAL_STOP_RE.search(value):
            return "conditional"
        if _GHOST_STOP_TARGET_RE.search(value) or _GHOST_BARE_STOP_RE.match(value):
            return "direct"
        return None

    def _ghost_stop_confirmation_from_history(self, text: str) -> bool:
        if not _GHOST_STOP_CONFIRM_RE.match(str(text or "")):
            return False
        for item in reversed(self._ghost_history[-3:]):
            response = str(item.get("response") or "").lower()
            if not response:
                continue
            mentions_stop = any(word in response for word in ("stop", "cancel", "abort", "interrupt"))
            mentions_mo = any(phrase in response for phrase in ("main mo", "current mo", "mo run", "current turn", "current run"))
            if mentions_stop and mentions_mo:
                return True
        return False

    def _execute_ghost_stop_request(self, *, conditional: bool = False) -> str:
        if not self.busy:
            return "MO is not running right now, so there is nothing to stop."

        visible_write_tools = self._current_turn_visible_write_tools() if conditional else []
        if visible_write_tools:
            tools = ", ".join(visible_write_tools)
            return (
                f"I can see current-turn edit/write activity now ({tools}), so I did not stop MO. "
                "If you still want it stopped, say: stop MO now."
            )

        stopper = getattr(self, "_request_current_turn_stop", None)
        stopped = bool(stopper()) if callable(stopper) else False
        worker_id = str(getattr(self, "_active_main_worker_id", "") or "")
        if stopped and worker_id:
            try:
                ensure_worker_registry(self.agent).update(worker_id, "cancelled", "stop requested by Ghost")
            except Exception:
                traceback.print_exc()
        if stopped:
            if conditional:
                return "No visible edit/write activity yet; stop requested for the current MO turn."
            return "Stop requested for the current MO turn. If MO is mid-call, it will stop at the next safe checkpoint."
        return "Stop was already requested, or no active stop handle is available for the current MO turn."

    def _current_turn_visible_write_tools(self) -> list[str]:
        monitor = getattr(self.gateway, "monitor", None)
        path = getattr(monitor, "path", None)
        if not path:
            return []
        started_at = float(getattr(self, "activity_started_at", 0.0) or 0.0)
        try:
            raw_lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()[-240:]
        except Exception:
            return []
        tools: list[str] = []
        seen: set[str] = set()
        for raw in raw_lines:
            try:
                event = json.loads(raw)
            except Exception:
                continue
            try:
                ts = float(event.get("ts") or 0.0)
            except Exception:
                ts = 0.0
            if started_at and ts < started_at:
                continue
            if str(event.get("type") or "") not in {"tool_call", "tool_result"}:
                continue
            payload = event.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            tool = str(payload.get("tool") or "")
            if tool in _GHOST_WRITE_TOOLS and tool not in seen:
                seen.add(tool)
                tools.append(tool)
        return tools

    def _handle_ghost_route_reply(self, text: str) -> bool:
        pending = self._ghost_pending_route
        if not pending:
            pending = self._implicit_ghost_route_from_history(text)
            if not pending:
                return False
        if is_route_rejection(text):
            self._ghost_pending_route = None
            self._ghost_unread_count = 0
            response = "Okay — I will not route that."
            self._record_ghost_history("route_reject", text, response, route=pending.route)
            self._ghost_panel_lines = [
                ("class:ghost-user", text),
                ("class:ghost-response", response),
            ]
            self._ghost_scroll_from_bottom = 0
            self._ghost_panel_open = True
            if self._app:
                self._app.invalidate()
            return True
        if not is_route_confirmation(text) and not self._looks_like_implicit_route_confirmation(text):
            return False

        self._ghost_pending_route = None
        self._ghost_unread_count = 0
        enhanced = enhance_route_objective(pending.objective)
        pending = GhostRouteSuggestion(pending.route, enhanced, pending.reason, pending.risky)
        response = self._execute_ghost_route(pending)
        self._record_ghost_history("route_confirm", text, response, route=pending.route)
        self._start_ghost_route_transition(text, response)
        return True

    @staticmethod
    def _ghost_route_transition_glyph(line: str) -> str:
        stripped = str(line or "").strip()
        if not stripped:
            return "↯"
        first = stripped[0]
        return first if first in {"↯", "→", "✓", "!"} else "↯"

    @staticmethod
    def _ghost_route_transition_glyphs(response: str) -> list[str]:
        text = str(response or "").lower()
        if any(word in text for word in ("unavailable", "blocked", "conflict", "not routed", "failed", "error", "no receiver")):
            return ["!"]
        return ["↯", "→", "✓"]

    def _start_ghost_route_transition(self, user_text: str, response: str):
        glyphs = self._ghost_route_transition_glyphs(response)

        def _set_visible(count: int):
            self._ghost_panel_lines = [
                ("class:ghost-user", user_text),
                ("class:ghost-response", glyphs[min(max(1, count), len(glyphs)) - 1]),
            ]
            self._ghost_expanded = False
            self._ghost_scroll_from_bottom = 0
            self._ghost_panel_open = True
            if self._app:
                self._app.invalidate()

        _set_visible(1)

        def _run():
            import time

            for count in range(2, len(glyphs) + 1):
                time.sleep(0.14)
                _set_visible(count)
            time.sleep(0.45)
            self._ghost_panel_open = False
            self._ghost_expanded = False
            self._ghost_panel_lines = []
            now = time.time()
            self._ghost_route_flash_text = self._ghost_route_flash_label(response)
            self._ghost_route_flash_until = now + 1.6
            if hasattr(self, "_set_notice"):
                self._set_notice(self._ghost_route_flash_text, ttl=2.0)
            elif self._app:
                self._app.invalidate()

        threading.Thread(target=_run, daemon=True).start()

    @staticmethod
    def _ghost_route_flash_label(response: str) -> str:
        text = str(response or "").strip()
        if not text or text in {"↯", "→", "✓", "!"}:
            return "Route accepted"
        first = text.splitlines()[0].strip()
        return first[:80] if first else "Route accepted"

    def _infer_followup_route_suggestion(self, text: str) -> GhostRouteSuggestion | None:
        """Infer short follow-up choices like "wordle" after Ghost discussed games."""
        value = " ".join(str(text or "").strip().split())
        if not value or len(value.split()) > 5:
            return None
        lowered = value.lower()
        game_terms = {"wordle", "zombie", "runner", "cow", "game", "hangman", "snake", "tetris", "web"}
        if not (_words(lowered) & game_terms):
            return None
        for item in reversed(self._ghost_history[-5:]):
            combined = f"{item.get('user', '')}\n{item.get('response', '')}".lower()
            if "game" not in combined and "route" not in combined and "mo" not in combined:
                continue
            objective = value
            objective = enhance_route_objective(objective)
            return GhostRouteSuggestion("main", objective, "operator gave a short follow-up game choice")
        return None

    @staticmethod
    def _looks_like_implicit_route_confirmation(text: str) -> bool:
        lowered = " ".join(str(text or "").lower().split())
        return lowered in {
            "yes ask it", "ask it", "ask mo", "ask main mo",
            "route it", "yes route it", "send it", "yes send it",
            "do that", "yes do that",
        }

    def _implicit_ghost_route_from_history(self, text: str) -> GhostRouteSuggestion | None:
        if not (is_route_confirmation(text) or self._looks_like_implicit_route_confirmation(text)):
            return None
        for item in reversed(self._ghost_history[-5:]):
            if item.get("kind") != "reply":
                continue
            response = str(item.get("response") or "")
            suggestion = self._route_suggestion_from_ghost_response(str(item.get("user") or ""), response)
            if suggestion:
                return suggestion
        return None

    @staticmethod
    def _ghost_response_offers_main_route(response: str) -> bool:
        return bool(_GHOST_MAIN_ROUTE_OFFER_RE.search(str(response or ""))) or "suggested ask" in str(response or "").lower()

    def _route_suggestion_from_ghost_response(self, question: str, response: str) -> GhostRouteSuggestion | None:
        if not self._ghost_response_offers_main_route(response):
            return None
        objective = self._extract_suggested_main_ask(response)
        if not objective:
            objective = self._extract_next_step_route_objective(response)
        if not objective:
            objective = str(question or "").strip()
        if not objective:
            return None
        enhanced = enhance_route_objective(objective)
        route = "main"
        if bool(getattr(self, "busy", False)):
            combined = f"{question}\n{response}"
            route = "steer" if _GHOST_CURRENT_ADJUSTMENT_RE.search(combined) else "queue"
        reason = "operator confirmed Ghost's current-turn MO adjustment" if route == "steer" else "operator confirmed Ghost's suggested main MO ask"
        return GhostRouteSuggestion(route, enhanced, reason)

    @staticmethod
    def _clean_route_candidate(raw: str) -> str:
        value = str(raw or "").strip().lstrip("-• ").strip()
        value = value.replace("**", "").replace("__", "").replace("`", "").strip()
        value = _re.sub(r"\s+", " ", value).strip(" .")
        return value

    @classmethod
    def _extract_next_step_route_objective(cls, response: str) -> str:
        focus = ""
        for raw in str(response or "").splitlines():
            line = cls._clean_route_candidate(raw)
            lower = line.lower()
            if not line:
                continue
            if lower.startswith("focus on "):
                focus = line.split(" ", 2)[2].strip(" .") if len(line.split(" ", 2)) >= 3 else ""
                continue
            match = _re.match(r"(?i)^(?:best\s+)?next\s+(?:step|move)\s*:\s*(.+)$", line)
            if not match:
                match = _re.match(r"(?i)^next\s*:\s*(.+)$", line)
            if not match:
                continue
            objective = cls._clean_route_candidate(match.group(1))
            if not objective:
                continue
            if focus and focus.lower() not in objective.lower():
                objective = f"{objective} for {focus}"
            return objective[:240]
        return ""

    @staticmethod
    def _extract_suggested_main_ask(response: str) -> str:
        text = str(response or "")
        explicit = enhance_route_objective("", text)
        if explicit:
            return explicit[:240]
        quoted = _re.search(r"[“\"]([^”\"]{12,240})[”\"]", text)
        if quoted:
            return quoted.group(1).strip()
        lines = text.splitlines()
        for idx, line in enumerate(lines):
            if "suggested ask" in line.lower() or "good ask" in line.lower():
                candidates = lines[idx + 1: idx + 4]
                for candidate in candidates:
                    value = candidate.strip().lstrip("-• ").strip()
                    if value:
                        return value[:240]
        return ""

    def _execute_ghost_route(self, suggestion: GhostRouteSuggestion) -> str:
        objective = suggestion.objective
        registry = ensure_worker_registry(self.agent)
        runtime = ensure_worker_runtime(self.agent)
        decision = decide_worker_route(
            objective,
            requested_route=suggestion.route,
            main_busy=self.busy,
            risky=suggestion.risky,
            registry=registry,
            background_active_count=runtime.active_count(),
            background_limit=runtime.max_workers,
        )
        if decision.action == "run_worker":
            record = registry.create(kind="worker", source="ghost", route="background", objective=objective, state="offered", note="Ghost routed background work", claimed_paths=decision.claimed_paths)
            record = self._start_background_worker_from_ghost(objective, worker_id=record.id)
            return self._ghost_route_receipt(record)
        if decision.action in {"blocked_conflict", "blocked_capacity"}:
            record = registry.create(kind="worker", source="ghost", route="background", objective=objective, state="offered", note="Ghost routed background work", claimed_paths=decision.claimed_paths)
            record = registry.update(record.id, "blocked", decision.reason)
            return self._ghost_route_receipt(record)
        if suggestion.route == "steer" and self.busy:
            record = registry.create(kind="queue", source="ghost", route="queue", objective=objective, state="accepted", note="live steer queued for current MO turn")
            injector = getattr(self.agent, "add_live_steer", None)
            if callable(injector):
                injector(objective, source="ghost", worker_id=record.id)
                return "MO update injected"
            self._queue_input(objective, worker_id=record.id, source="ghost", note="queued for main MO")
            return "MO queued"
        if decision.action == "queue_main":
            record = registry.create(kind="queue", source="ghost", route="queue", objective=objective, state="accepted", note="queued for main MO")
            self._queue_input(objective, worker_id=record.id, source="ghost", note="queued for main MO")
            return self._ghost_route_receipt(registry.get(record.id) or record)
        record = registry.create(kind="main", source="ghost", route="main", objective=objective, state="accepted", note="main MO accepted handoff")
        self._active_main_worker_id = record.id
        try:
            self._handle_input(objective)
        except Exception as exc:
            record = registry.update(record.id, "blocked", f"main MO handoff failed: {type(exc).__name__}") or record
            self._active_main_worker_id = ""
            return self._ghost_route_receipt(record)
        current = registry.get(record.id) or record
        if current.state == "accepted":
            current = registry.update(record.id, "running", "main MO turn launched") or current
        return self._ghost_route_receipt(current)

    @staticmethod
    def _ghost_route_receiver_line(record) -> str:
        kind = str(getattr(record, "kind", "") or "")
        state = str(getattr(record, "state", "") or "")
        blocked = state in {"blocked", "cancelled", "paused"}
        if kind == "main":
            return "MO unavailable" if blocked else "MO routed"
        if kind == "queue":
            return "MO queue unavailable" if blocked else "MO queued"
        if kind in {"worker", "goal"}:
            return "Worker unavailable" if blocked else "Worker routed"
        return "Receiver unavailable" if blocked else "Receiver accepted"

    @staticmethod
    def _ghost_route_state_line(record) -> str:
        kind = str(getattr(record, "kind", "") or "")
        state = str(getattr(record, "state", "") or "")
        note = str(getattr(record, "note", "") or state or "not accepted")
        if state == "running":
            return "running" if kind == "main" else "running"
        if state == "accepted":
            return "queued" if kind == "queue" else "starting"
        if state == "completed":
            return "completed"
        if state == "offered":
            return "offered"
        return note

    def _ghost_route_receipt(self, record) -> str:
        if not record:
            return "Not routed"
        state = str(getattr(record, "state", "") or "")
        receiver = self._ghost_route_receiver_line(record)
        if state in {"blocked", "cancelled", "paused"}:
            detail = self._ghost_route_state_line(record)
            return f"{receiver} · {detail}" if detail else receiver
        return receiver

    def _start_background_worker_from_ghost(self, objective: str, worker_id: str | None = None):
        runtime = ensure_worker_runtime(self.agent)

        def _on_finish(record, result: str):
            summary = "✓ Worker finished" if record.state == "completed" else "! Worker blocked"
            detail = str(record.result_summary or record.objective or objective)[:90]
            notification = f"{summary} — {detail}"
            self._record_ghost_history("notification", "", notification, route="background")
            if self._ghost_panel_open:
                self._ghost_panel_lines = [("class:ghost-hint", notification)]
            self._add("class:activity", f"  {notification}")
            if self._app:
                self._app.invalidate()

        return runtime.start(objective, source="ghost", worker_id=worker_id, on_finish=_on_finish)
