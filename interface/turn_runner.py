"""Gateway turn runner mixin for the MO TUI."""
from __future__ import annotations

import inspect
import re
import threading
import time
import traceback

from core.provider.provider import clean_provider_error
from core.worker import ensure_worker_registry


def _strip_rich_tags(text: str) -> str:
    """Strip rich markup tags like [dim], [/dim], [green], etc. for plain text display."""
    return re.sub(r"\[/?[a-z_]+(?:\d+)?\]", "", text)


_DIFFSTAT_RE = re.compile(r"^(.*) (\+\d+) (-\d+)$")


def _tool_label_from_activity(act: str) -> str:
    """Extract the tool label from a ``tooling (<label>)...`` activity string.

    The label is everything between the FIRST ``(`` and the trailing ``)...``.
    Splitting on ``(`` (the old behaviour) broke on any summary containing a
    paren — every shell ``python -c`` one-liner rendered as a garbage tail like
    ``).st_size for…`` because ``split("(")[-1]`` grabbed the last inner group.
    """
    if "(" not in act:
        return act
    inner = act[act.index("(") + 1:]
    if inner.endswith(")..."):
        return inner[:-4].rstrip()
    return inner.removesuffix("...").removesuffix(")").rstrip()


_CHIP_SHORTEN = {
    "read_file": "read", "edit_file": "edit", "write_file": "write",
    "find_files": "find", "code_search": "search", "find_callers": "callers",
    "find_callees": "callees", "git_status": "git", "tool_search": "search",
    "test_runner": "test",
}


def _shorten_target(target: str, limit: int = 52) -> str:
    """Boundary-aware truncation of a tool target for display only (never mid-token).

    The real command/args are unchanged — this only trims the *shown* text so a long
    path or one-liner doesn't garble the line.
    """
    target = target.strip()
    if len(target) <= limit:
        return target
    head = target[:limit]
    for sep in ("/", "\\", " "):
        idx = head.rfind(sep)
        if idx > limit // 2:
            return head[:idx].rstrip() + "…"
    return head.rstrip() + "…"


def _reasoning_gist(text: str) -> str:
    """Collapse a reasoning chunk to one line: the model's own first sentence.

    Display-only — no summarization pass. Keeps the collapsed view honest (it's
    literally the start of what the model reasoned) while the full chain stays
    behind /show reasoning.
    """
    body = text.lstrip("💭").strip()
    line = body.splitlines()[0] if body else ""
    for end in (". ", "? ", "! "):
        i = line.find(end)
        if 0 < i < 100:
            line = line[: i + 1]
            break
    if len(line) > 100:
        line = line[:99].rstrip() + "…"
    return f"💭 {line}  · /show reasoning" if line else "💭 thinking…  · /show reasoning"


class TurnRunnerMixin:
    def _reanchor_render(self) -> None:
        """Force prompt-toolkit to re-anchor and fully repaint the inline region.

        With ``full_screen=False`` PTK pins its render to the cursor's start row
        and never repaints rows above it. Once the terminal scrolls (long output,
        or the layout briefly exceeding the screen) that anchor drifts and the
        orphaned rows pin at the top, swallowing the transcript — the only manual
        cure is a resize or Ctrl+L. ``renderer.clear()`` does exactly that: erase
        + home the cursor so the next draw is full. Marshalled onto the app loop
        because the renderer is not thread-safe and turns run on a worker thread.
        """
        app = getattr(self, "_app", None)
        if app is None:
            return

        def _do() -> None:
            try:
                renderer = getattr(app, "renderer", None)
                if renderer is not None:
                    renderer.clear()
            except Exception:
                pass
            try:
                app.invalidate()
            except Exception:
                pass

        loop = getattr(app, "loop", None)
        if loop is not None:
            try:
                loop.call_soon_threadsafe(_do)
                return
            except Exception:
                pass
        _do()

    def _diffstat_fragments(self, text: str, base_style: str) -> list[tuple[str, str]]:
        """Split a trailing ' +A -B' edit diffstat into green/red fragments.

        Returns a single base-styled fragment when no diffstat is present, so
        non-edit activity lines render exactly as before.
        """
        match = _DIFFSTAT_RE.match(text)
        if not match:
            return [(base_style, text)]
        head, added, removed = match.group(1), match.group(2), match.group(3)
        return [
            (base_style, head + " "),
            ("class:diff-add", added),
            (base_style, " "),
            ("class:diff-del", removed),
        ]

    def _tool_line_fragments(self, label: str) -> list[tuple[str, str]]:
        """Build '▸ [tool] target  +A -B' — tool name as a fg-only chip, target dim
        and boundary-truncated, trailing edit diffstat kept green/red."""
        diff: list[tuple[str, str]] = []
        match = _DIFFSTAT_RE.match(label)
        if match:
            label = match.group(1).rstrip()
            diff = [("class:dim", "  "), ("class:diff-add", match.group(2)),
                    ("class:dim", " "), ("class:diff-del", match.group(3))]
        parts = label.split(None, 1)
        verb = parts[0] if parts else label
        target = parts[1] if len(parts) > 1 else ""
        chip = _CHIP_SHORTEN.get(verb, verb)
        frags: list[tuple[str, str]] = [("class:dim", "    ▸ "), ("class:tool-chip", f"[{chip}]")]
        target = _shorten_target(target)
        if target:
            frags.append(("class:dim", f" {target}"))
        frags.extend(diff)
        return frags

    def _add_tool_activity_line(self, tool_name: str) -> None:
        """Render an indented '▸ [tool] target' activity line, colouring +A/-B."""
        self._add_fragments_line(self._tool_line_fragments(tool_name))

    def _gateway_board_finished(self) -> bool:
        board = getattr(self.gateway, "last_task_board", None)
        if not board:
            return False
        try:
            return int(board.open_count()) == 0
        except Exception:
            tasks = list(getattr(board, "tasks", []) or [])
            return bool(tasks) and not any(str(getattr(task, "status", "") or "") in {"pending", "active", "blocked"} for task in tasks)

    def _start_prompt_enhance(self, original: str) -> None:
        """Hybrid Ctrl+E: show the instant local enhancement now, refine in the bg.

        The deterministic local pass is instant (no provider call), so the input
        row updates with zero latency. The slower provider rewrite then runs on a
        daemon thread and only replaces the shown text if it's a real improvement
        AND the operator hasn't edited it meanwhile. The original is stashed so Esc
        reverts.
        """
        if getattr(self, "_enhance_in_flight", False):
            return
        original_stripped = str(original or "").strip()
        instant = ""
        try:
            fn = getattr(self.agent, "enhance_prompt_local", None)
            if callable(fn):
                instant = str(fn(original) or "").strip()
        except Exception:
            traceback.print_exc()
        if instant and instant != original_stripped:
            self._pre_enhance_text = original
            self._enhance_holder_active = True
            self._input_buf.text = instant
            self._input_buf.cursor_position = len(instant)
            self._set_notice("Enhanced · refining…")
        else:
            instant = ""
            self._set_notice("Refining…")
        self._enhance_in_flight = True
        self._enhance_shown_text = instant  # detect operator edits before the swap
        if self._app:
            self._app.invalidate()
        threading.Thread(target=self._run_enhance_thread, args=(original,), daemon=True).start()

    def _run_enhance_thread(self, original: str) -> None:
        refined = ""
        try:
            fn = getattr(self.agent, "enhance_prompt_for_input", None)
            if callable(fn):
                refined = str(fn(original) or "").strip()
        except Exception:
            traceback.print_exc()

        def _apply() -> None:
            self._enhance_in_flight = False
            shown = str(getattr(self, "_enhance_shown_text", "") or "")
            current = str(self._input_buf.text or "").strip()
            original_stripped = str(original or "").strip()
            # Swap to the provider refinement only if it's a genuine improvement
            # and the operator hasn't typed over the instant result meanwhile.
            untouched = (current == shown) or (not shown and current == original_stripped)
            if refined and refined != current and refined != original_stripped and untouched:
                self._pre_enhance_text = original
                self._enhance_holder_active = True
                self._input_buf.text = refined
                self._input_buf.cursor_position = len(refined)
                self._set_notice("Enhanced — Esc to revert")
            elif shown:
                self._set_notice("Enhanced — Esc to revert")
            else:
                self._set_notice("No change")
            if self._app:
                self._app.invalidate()

        loop = getattr(self._app, "loop", None)
        if loop is not None:
            try:
                loop.call_soon_threadsafe(_apply)
                return
            except Exception:
                traceback.print_exc()
        _apply()

    def _maybe_notify_model_change(self) -> None:
        """Surface a provider/model fallback the MOMENT it happens.

        The runtime auto-falls-through the provider chain on rate/route/balance
        blocks, which can silently land on a weaker model (e.g. big-pickle) — the
        operator had to ask "why did you change model". This is called live from
        on_activity (the agent fires on_activity at the fallback point) so the
        notice appears immediately, not buffered until the turn finishes
        reporting. It dedupes against the last-notified model so each real change
        shows exactly once, and a post-turn call acts as a backstop.
        """
        try:
            now = (getattr(self.agent, "provider_name", ""), getattr(self.agent, "model", ""))
            if not now[1]:
                return
            last = getattr(self, "_last_notified_model", None)
            if last is None or now == last:
                return
            reason = str(getattr(self.agent, "last_fallback_notice", "") or "").strip()
            tail = f" - {reason}" if reason else ""
            self._add("class:model-fallback", f"  ⚠ Model fallback: now on {now[0]}/{now[1]}{tail}")
            self._last_notified_model = now
        except Exception:
            traceback.print_exc()

    def _maybe_warn_low_balance(self, *, threshold: float = 2.00) -> None:
        """Drop a colored low-balance notice into the transcript, once per session.

        Reads the cached DeepSeek balance (kept fresh by the footer); fires only on
        the official DeepSeek API and only the first time it goes under threshold.
        """
        if getattr(self, "_low_balance_notified", False):
            return
        try:
            from core.provider.deepseek_balance import balance_amount
            amount = balance_amount(getattr(self.agent, "active_provider", None))
        except Exception:
            amount = None
        if amount is None or amount >= threshold:
            return
        self._low_balance_notified = True
        try:
            self._add(
                "class:low-balance",
                f"  ⚠ DeepSeek balance is low: ${amount:.2f} (below ${threshold:.2f}) - top up soon.",
            )
        except Exception:
            traceback.print_exc()

    def _run_turn_thread(self, user_input: str):
        cancel_event = threading.Event()
        self._current_turn_cancel_event = cancel_event
        # Snapshot the model so a mid-turn provider fallback is surfaced to the
        # operator (e.g. deepseek-v4-pro -> big-pickle on a rate/route block).
        model_at_start = (getattr(self.agent, "provider_name", ""), getattr(self.agent, "model", ""))
        # Baseline for the live model-fallback notice: any change away from this
        # is surfaced the instant on_activity fires at the fallback point.
        self._last_notified_model = model_at_start
        main_worker_id = getattr(self, "_active_main_worker_id", "") or ""
        route_source = "user"
        if main_worker_id:
            registry = ensure_worker_registry(self.agent)
            record = registry.get(main_worker_id)
            route_source = str(getattr(record, "source", "") or "user")
            registry.update(main_worker_id, "running", "main MO turn running")
        self.busy = True
        self.activity_text = "preparing..."
        self.activity_started_at = time.time()
        self.board_text = ""
        self._reasoning_gist_shown = False  # one collapsed-reasoning gist line per turn
        # Re-anchor at the turn boundary so any render drift accumulated since the
        # last turn (full_screen=False pins above the anchor) can't persist.
        self._reanchor_render()

        try:
            show_proposal = route_source == "ghost" and self.gateway.should_show_task_board(user_input)

            def on_board_update(_rich_markup: str):
                # Gateway already called render_rich(board); the rich_markup
                # argument is informational (for monitor consumers).  We read
                # board.render() directly as the single source of display truth
                # so forged callback markup can't inject fake board text.
                board = self.gateway.last_task_board
                if board:
                    self.board_text = board.render()
                    if self._app:
                        self._app.invalidate()

            def on_board_event(event: dict):
                # Structured event — use board.render() as the single source
                # of display truth.  The event payload is for monitoring.
                board = self.gateway.last_task_board
                if board:
                    self.board_text = board.render()
                    if self._app:
                        self._app.invalidate()

            def on_token(token: str):
                self.activity_text = "receiving answer"
                if self._app:
                    self._app.invalidate()

            def on_activity(act: str):
                self.activity_text = act
                # Surface a provider/model fallback the instant it happens — the
                # agent calls on_activity at the fallback point, so this no longer
                # waits for the turn to finish reporting.
                self._maybe_notify_model_change()
                if self._show_tool_activity and "tooling" in act:
                    self._add_tool_activity_line(_tool_label_from_activity(act))
                if self._app:
                    self._app.invalidate()

            def on_proposal(proposal: str):
                # Ghost proposal text is turn guidance, not a user-facing report.
                # Keep it silent; Gateway/main tools own visible truth.
                _ = proposal

            interim_seen: list[str] = []

            def on_assistant_text(text: str):
                # Interim prose that came alongside a tool call. This is a direct
                # answer to the user that would otherwise reach only the livelog.
                # Render it into the main transcript immediately. This is also the
                # funnel the autopilot worktree-child streams through, so the /show
                # toggles gate both live turns and the forwarded sweep uniformly.
                clean = str(text or "").strip()
                if not clean:
                    return
                is_reasoning = clean.startswith("💭")
                is_tool = clean.startswith("▸") or "tooling (" in clean
                if is_tool and not getattr(self, "_show_tool_activity", True):
                    return
                # Colour by content type so secondary chrome doesn't wear the answer
                # colour: reasoning -> dim italic, tool activity -> dim (same as the
                # live activity line), real prose -> the answer response block.
                if is_reasoning:
                    if getattr(self, "_show_reasoning", True):
                        interim_seen.append(clean)
                        self._add("class:reasoning", clean)
                    elif not getattr(self, "_reasoning_gist_shown", False):
                        # Collapsed view: one gist line for the turn, suppress the rest.
                        self._reasoning_gist_shown = True
                        self._add("class:reasoning", _reasoning_gist(clean))
                elif is_tool:
                    interim_seen.append(clean)
                    self._add_fragments_line(self._diffstat_fragments(clean, "class:dim"))
                else:
                    interim_seen.append(clean)
                    self._add_response_block(clean)
                if self._app:
                    self._app.invalidate()

            try:
                gateway_kwargs = {
                    "on_board_update": on_board_update,
                    "on_board_event": on_board_event,
                    "on_token": on_token,
                    "on_activity": on_activity,
                    "on_proposal": on_proposal if show_proposal else None,
                    "cancel_event": cancel_event,
                    "on_assistant_text": on_assistant_text,
                }
                gateway_sig = inspect.signature(self.gateway.run_turn)
                if "route_source" in gateway_sig.parameters or any(p.kind == p.VAR_KEYWORD for p in gateway_sig.parameters.values()):
                    gateway_kwargs["route_source"] = route_source
                result = self.gateway.run_turn(user_input, **gateway_kwargs)
            except Exception as exc:
                detail = clean_provider_error(str(exc))
                result = "\n".join([
                    "MO interface error: turn failed",
                    "  where: TUI turn runner",
                    "Fix: try again or run /status; check monitor if this repeats.",
                    f"  detail: {detail}",
                ])

            if hasattr(self.agent, "autosave_session"):
                self.agent.autosave_session()
            if hasattr(self.agent, "consume_quarantine_notice"):
                q_notice = self.agent.consume_quarantine_notice()
                if q_notice:
                    self._add("class:activity", f"  {q_notice}")
            if hasattr(self.agent, "consume_handoff_notice"):
                notice = self.agent.consume_handoff_notice()
                if notice:
                    self._add("class:activity", f"  {notice}")
            self._gateway_board_finished()
            result_clean = str(result or "").strip()
            already_shown = bool(result_clean) and result_clean in interim_seen
            if result and not str(result).startswith("[ABORTED]") and not already_shown:
                self._add_response_block(result)
            # Board stays visible until next turn (cleared at turn start).
            # The agent already completed the last active task, so the
            # board shows honest finished state — no visual gap.
            elif str(result or "").startswith("[ABORTED]"):
                self._add("class:dim", "  stopped current turn")
            if main_worker_id:
                from core.worker import summarize_worker_result

                result_text = str(result or "")
                if result_text.startswith("[ABORTED]"):
                    state = "cancelled"
                    note = "main MO turn stopped"
                else:
                    state = "blocked" if result_text.startswith(("Error:", "MO provider error:", "[MAX")) else "completed"
                    note = "main MO turn finished"
                result_summary, evidence = summarize_worker_result(result)
                ensure_worker_registry(self.agent).update(
                    main_worker_id,
                    state,
                    note,
                    result_summary=result_summary,
                    evidence=evidence,
                )
                self._active_main_worker_id = ""
        finally:
            if self._current_turn_cancel_event is cancel_event:
                self._current_turn_cancel_event = None
            self.busy = False
            self._busy_escape_count = 0
            self.activity_text = ""
            self.activity_started_at = 0.0
            self._maybe_notify_model_change()
            self._maybe_warn_low_balance()
            # extrathink confirmation is ambient, not a text banner: the activity lane
            # and footer "MO" glow gold while MO runs its own method (see display_delegates).
            # Completed taskboards leave the final MO report in transcript; incomplete
            # boards stay visible so unresolved work remains clear. Re-anchor here too
            # since the layout shrinks at turn end (board/activity lane removed), the
            # case most likely to orphan rows at the top.
            self._reanchor_render()
            self._process_next_queued_input()
