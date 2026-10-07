"""TUI input, palette action, and slash-command dispatch mixin."""
from __future__ import annotations

import threading
import time

from core.agent.slash_result import SlashCommandResult, command_failure, command_result

from .command_palette import PaletteItem, palette_children_for_item
from .queueing import direct_interrupt_action


class InputDispatchMixin:
    def _host_command_terminal(self):
        controller = getattr(self, "_workspace", None)
        terminal = getattr(controller, "focused_terminal", None)
        return terminal if callable(getattr(terminal, "request_command", None)) else None

    def _toggle_command_palette(self) -> None:
        if self._palette.open:
            self._palette.close()
            self._remote_command_pending = None
        elif self._host_command_terminal() is not None:
            self._request_host_command("/", "query")
        else:
            self._palette.show()

    def _request_host_command(self, value: str, action: str) -> bool:
        terminal = self._host_command_terminal()
        if terminal is None:
            return False
        pending = object()
        self._remote_command_pending = pending
        if not self._palette.open or action == "query":
            self._palette.show()
            self._palette.remote = True
            self._palette.enter_submenu("MO host · commands", [])

        def receive(result):
            def apply():
                if (
                    self._host_command_terminal() is not terminal
                    or getattr(self, "_remote_command_pending", None) is not pending
                    or not self._palette.open
                ):
                    return
                self._remote_command_pending = None
                mode = result["mode"]
                title = "MO host · " + result["title"]
                if mode == "menu":
                    rows = [PaletteItem(*row) for row in result["items"]]
                    if action == "query":
                        self._palette.show_command_args(title, rows)
                    else:
                        self._palette.enter_submenu(title, rows)
                elif mode == "input":
                    self._palette.close()
                elif mode == "notice":
                    self._clear_palette_query_input(value)
                    self._palette.close()
                    self._set_notice("MO host: " + result["text"])
                else:
                    self._clear_palette_query_input(value)
                    self._palette.show_result(title, result["text"], kind=mode, actions=[PaletteItem(*row) for row in result["items"]])
                if self._app:
                    self._app.invalidate()

            loop = getattr(self._app, "loop", None)
            if loop is not None:
                loop.call_soon_threadsafe(apply)

        if not terminal.request_command(value, action, receive):
            self._remote_command_pending = None
            self._palette.show_result(
                "MO host · commands", "Host command menu unavailable. Update the host and hub, then reconnect this pane.",
                kind="error",
            )
        if self._app:
            self._app.invalidate()
        return True

    def _palette_children_for_item(self, item: PaletteItem) -> list[PaletteItem]:
        return palette_children_for_item(item, self.agent)

    def _mo_command_palette_available(self) -> bool:
        """Keep MO commands out of terminal-owned workspace panes."""
        controller = getattr(self, "_workspace", None)
        return not (
            controller is not None
            and getattr(controller, "active", False)
            and getattr(controller, "focused_terminal", None) is not None
        )

    def _add_user_echo(self, text: str) -> None:
        """Echo a submitted user message through the unified ``user`` lane (❯ gutter)."""
        from .visual_effects import gradient_line, has_shine_trigger

        if has_shine_trigger(text):
            content = list(gradient_line(text, "class:user-msg"))
        else:
            content = [("class:user-msg", text)]
        self._add_line("user", content)
        # Reserve the turn boundary now so live status, interim prose, tools, and
        # the final answer all begin one blank row below the operator message.
        # Later response formatting calls the same deduplicating seam.
        self._ensure_blank_line()

    def _on_input_changed(self, buff):
        # Buffer edits already invalidate Prompt Toolkit. Record only their latest
        # time so the existing frame loop can yield decorative work animation;
        # transcript, tool-output, and status redraws are never deferred here.
        self._last_input_change_at = time.monotonic()
        text = buff.text
        if not text:
            self._flush_background_updates()
        workspace_command = text.strip().split(maxsplit=1)[:1] == ["/workspace"]
        if self._host_command_terminal() is not None:
            if getattr(self, "_host_command_prefill", False):
                return
            if workspace_command:
                self._remote_command_pending = None
                self._palette.show()
                self._palette.remote = True
                if not self._maybe_show_command_args(text):
                    self._palette.close()
                if self._app:
                    self._app.invalidate()
                return
            if text.startswith("/") and "\n" not in text:
                self._request_host_command(text, "query")
            else:
                self._remote_command_pending = None
                self._palette.close()
            return
        if not self._mo_command_palette_available():
            if self._palette.open:
                self._palette.close()
                if self._app:
                    self._app.invalidate()
            return
        has_whitespace = any(ch.isspace() for ch in text)
        if text.startswith("/") and not has_whitespace:
            if not self._palette.open:
                self._palette.show()
            self._palette.set_query(text)
            if buff.complete_state:
                buff.cancel_completion()
            if self._app:
                self._app.invalidate()
        elif self._palette.open and (not text or not text.startswith("/")):
            self._palette.close()
            if self._app:
                self._app.invalidate()
        elif self._palette.open and text.startswith("/") and has_whitespace:
            # "/cmd arg" on ONE line -> keep the palette on the command's discrete
            # arg choices, filtered by the typed arg, so Enter selects instead of
            # running (and printing) the raw command. A newline means you're
            # composing a multi-line message, and free-text args (/goal, /role) have
            # no discrete choices — both fall through to close.
            if "\n" in text or not self._maybe_show_command_args(text):
                self._palette.close()
            if self._app:
                self._app.invalidate()

    def _maybe_show_command_args(self, text: str, *, palette=None) -> bool:
        """Filter a command's discrete arg choices by the typed arg and keep the
        palette open on them. Returns False when the command has no discrete choices
        (or none match the arg — a free-text command), so the caller closes.

        Resolve dynamic rows at input time, not render time, so session/catalog
        changes cannot leave stale selectable actions in a process-long cache."""
        parts = text.strip()[1:].split(None, 1)
        root = f"/{parts[0]}" if parts and parts[0] else ""
        from .command_registry import resolve_slash_command

        root = resolve_slash_command(root.lower())
        arg = parts[1].strip().lower() if len(parts) > 1 else ""
        if not root:
            return False
        discrete = self._palette_children_for_item(PaletteItem(root, root))
        # A root-level free-text starter is not a discrete argument choice.
        discrete = [c for c in discrete if c.value.strip() != root or c.kind == "command"]
        if arg:
            # Match the arg against the choice name/value only (not its description),
            # so an exact subcommand surfaces itself rather than every row that
            # happens to mention the same word.
            discrete = [c for c in discrete if arg in c.label.lower() or arg in c.value.lower()]
        if not discrete:
            return False
        (self._palette if palette is None else palette).show_command_args(root, discrete)
        return True

    def _handle_palette_selection(self):
        if self._palette.result_active and not self._palette._result_actions:
            self._palette.back()
            return
        item = self._palette.selected_item()
        if not item:
            remote = self._palette.remote
            self._palette.close()
            draft = str(getattr(self._input_buf, "text", "") or "").strip()
            if draft.startswith("/") and not remote:
                self._run_palette_command(draft)
            return
        value = item.value
        if self._host_command_terminal() is not None and self._palette.remote and value.split()[0] != "/workspace":
            if getattr(self, "_remote_command_pending", None) is not None:
                return
            if item.kind == "insert" or value.endswith(" "):
                self._host_command_prefill = True
                try:
                    self._input_buf.text = value
                    self._input_buf.cursor_position = len(value)
                finally:
                    self._host_command_prefill = False
                self._palette.close()
            else:
                self._request_host_command(value, "run" if item.kind == "run" else "select")
            return
        children = [] if item.kind == "insert" else self._palette_children_for_item(item)
        # Guard the self-recursion loop: a command with subcommands lists itself as a
        # "run the command" row inside its OWN submenu, so selecting it re-computes the
        # identical menu and re-opens it — the operator-reported "select /dashboard, hit
        # enter, nothing happens" loop. Detect it PRECISELY: only collapse to a run when the
        # would-be children equal the menu already on screen (same command's submenu). A
        # genuine navigation into a DIFFERENT submenu — e.g. selecting /goal from a parent
        # menu, whose first row prefills "/goal " — has different children and still opens.
        if children and self._palette.in_submenu:
            current = self._palette._current_items()
            if current and [c.value for c in children] == [c.value for c in current]:
                children = []
        if children:
            self._palette.enter_submenu(item.label, children)
            return
        if item.kind == "insert" or value.endswith(" "):
            controller = getattr(self, "_workspace", None)
            if not self._mo_command_palette_available() and controller is not None:
                controller.focus(1)
            if self._input_buf:
                self._input_buf.text = value
                self._input_buf.cursor_position = len(value)
            self._palette.close()
            return
        if value.startswith("/"):
            self._run_palette_command(value)

    def _clear_palette_query_input(self, command_text: str) -> None:
        """Clear a slash-filter query after a palette command is executed.

        Opening the palette by typing `/mo` or `/model` leaves that text in the
        input buffer while the operator drills through rows. Once a command row
        runs, that text is no longer input; leaving it there makes the next Enter
        re-run the root command and can look like the palette choice did not
        apply. Preserve ordinary drafted prose when the palette was opened with
        F4.
        """
        if not self._input_buf:
            return
        current = str(getattr(self._input_buf, "text", "") or "").strip()
        if not current.startswith("/"):
            return
        command_text = str(command_text or "").strip()
        root = command_text.split()[0] if command_text else ""
        current_root = current.split()[0] if current else ""
        from .command_registry import resolve_slash_command

        canonical_current = resolve_slash_command(current_root.lower())
        canonical_root = resolve_slash_command(root.lower())
        # Clear when the buffer holds the command we just ran: a partial root
        # ("/sk" before running "/skin") or a full "/cmd arg" after selecting an arg
        # ("/skin dr" after running "/skin dracula"). Leaving the arg text lets the
        # next Enter re-run it (e.g. the invalid "/skin dr", which then prints).
        # Unrelated drafted prose (doesn't start with "/") is preserved above.
        if root and (root.startswith(current) or canonical_current == canonical_root):
            self._host_command_prefill = True
            try:
                self._input_buf.text = ""
                self._input_buf.cursor_position = 0
            finally:
                self._host_command_prefill = False

    def _palette_exact_command_should_submit(self, typed: str) -> bool:
        """Whether Enter should submit an exact slash root while the palette is open.

        Exact roots without children keep the fast `/status` style path. Roots
        with palette children must stay inside the palette so dynamic menus such
        as `/model` and `/skin` can be navigated before their root action runs.
        """
        if self._palette.remote:
            return False
        value = str(typed or "").strip()
        if not value or " " in value:
            return False
        item = self._palette.selected_item()
        if not item:
            return True
        selected_root = str(item.value or "").strip().split()[0] if item.value else ""
        from .command_registry import resolve_slash_command

        if resolve_slash_command(selected_root.lower()) != resolve_slash_command(value.lower()):
            return True
        if item.kind == "insert":
            return False
        return not bool(self._palette_children_for_item(item))

    def _run_palette_command(self, text: str, *, bypass_work_gate: bool = False) -> bool:
        """Execute every interactive command through the command-list owner."""
        normalized = str(text or "").strip()
        root = normalized.split()[0].lower() if normalized else ""
        if root in {"/help", "/h"}:
            self._clear_palette_query_input(normalized)
            self._palette.show()
            if self._app:
                self._app.invalidate()
            return True
        if self._work_active() and not bypass_work_gate and not self._command_allowed_while_working(normalized):
            if root in {"/goal", "/g"}:
                if not (self._goal_worker_active or getattr(self.agent, "_goal_active", False)):
                    self._run_goal_command_now(normalized)
                else:
                    self._queue_goal_command(normalized)
                return True
            if normalized.lower().startswith("/model "):
                # The active request keeps its original provider. Keep only the
                # latest visual selection and apply it atomically at turn end,
                # before any queued user message starts its next turn.
                self._pending_palette_model_selection = normalized
                feedback = "Model selection queued for the next turn"
            else:
                # Never dropped: the command waits in the input queue and runs when the answer ends,
                # where the gate no longer applies (it used to be refused with a notice and lost).
                self._pending_inputs.put({"text": normalized, "steer": False, "source": "command", "echo": False})
                feedback = f"{normalized.split()[0]} runs when this answer ends"
            self._clear_palette_query_input(normalized)
            self._palette.close()
            self._set_notice(feedback)
            if self._app:
                self._app.invalidate()
            return True
        # A palette query is UI state, not a per-pane draft. Clear it before the
        # command mutates focus or creates a pane; workspace transitions save the
        # current composer synchronously and would otherwise retain the executed
        # slash command as the old pane's draft.
        self._clear_palette_query_input(normalized)
        try:
            if root == "/learning" and normalized in {
                "/learning", "/learning review", "/learning pending", "/learning list", "/learning active",
                "/learning details", "/learning confirm", "/learning dismiss",
                "/learning more",
            }:
                rows = self._palette_children_for_item(PaletteItem(normalized, normalized))
                title = "More learning actions" if normalized.endswith(" more") else (
                    "Active learning" if normalized.endswith(" active") else "Learning · select an item to review"
                )
                self._palette.show_command_args(title, rows)
                if self._app:
                    self._app.invalidate()
                return True
            cmd_result = self.agent.process_slash_command(normalized)
        except Exception as exc:  # a local command must never crash the TUI loop
            cmd_result = command_failure(normalized, exc)
        if cmd_result is None:
            cmd_result = SlashCommandResult(
                f"Unknown command: {normalized.split()[0]}",
                kind="error",
            )
        self._palette.record_command(normalized.split()[0])
        self._dispatch_slash_command_result(cmd_result, command_text=normalized)
        self._clear_palette_query_input(normalized)
        if self._app:
            self._app.invalidate()
        return True

    def _apply_pending_palette_model_selection(self) -> bool:
        """Apply the latest busy-time model choice before the next queued turn."""
        text = str(getattr(self, "_pending_palette_model_selection", "") or "").strip()
        if not text or self._work_active():
            return False
        self._pending_palette_model_selection = ""
        return self._run_palette_command(text)

    def _sync_task_board_display(self) -> None:
        """Mirror Gateway task truth after a local command mutates session state."""
        gateway = getattr(self, "gateway", None)
        board = getattr(gateway, "last_task_board", None) if gateway is not None else None
        self.board_text = board.render() if board is not None else ""
        self._board_scroll_from_bottom = 0

    @staticmethod
    def _session_message_text(message: object) -> str:
        """Extract only user-visible text from one persisted conversation row."""
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if str(item.get("type") or "").lower() not in {"text", "input_text", "output_text"}:
                continue
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
        return "\n".join(parts).strip()

    def _restore_loaded_session_transcript(self) -> int:
        """Hydrate the TUI after a session switch from persisted visible messages."""
        from core.session.session import INTERNAL_CONTINUATION_KEY, is_runtime_owned_session_summary

        session = getattr(self.agent, "session", None)
        restored = 0
        for message in list(getattr(session, "messages", None) or []):
            if (
                not isinstance(message, dict)
                or is_runtime_owned_session_summary(message)
                or message.get(INTERNAL_CONTINUATION_KEY)
                or message.get("tool_calls")
            ):
                continue
            role = str(message.get("role") or "")
            text = self._session_message_text(message)
            if not text:
                continue
            if role == "user":
                self._add_user_echo(text)
                self._last_speaker = "user"
            elif role == "assistant":
                self._add_response_block(text)
            else:
                continue
            restored += 1
        return restored

    def _dispatch_slash_command_result(
        self,
        cmd_result: SlashCommandResult | str,
        *,
        command_text: str = "",
    ) -> bool:
        """Apply one semantic result; command feedback stays in the command list."""
        result = command_result(cmd_result)
        self._sync_task_board_display()
        action = result.action
        if action == "exit":
            if self._app:
                self._app.exit()
            return True
        if action == "terminal":
            self._palette.close()
            self._open_internal_terminal(str(getattr(self.agent, "_terminal_pending_command", "") or ""))
            self.agent._terminal_pending_command = ""
            return True
        if action == "goal_start":
            self._palette.close()
            self._start_goal_thread()
            return True
        if action == "goal_continue":
            self._palette.close()
            self._show_active_goal()
            return True
        if action == "retry":
            retry_input = getattr(self.agent, "_retry_pending_input", "")
            self.agent._retry_pending_input = ""
            self._palette.close()
            if retry_input:
                self._start_turn_thread(retry_input)
            return True
        if action == "run_turn":
            pending_input = getattr(self.agent, "_slash_pending_input", "")
            self.agent._slash_pending_input = ""
            self._palette.close()
            if pending_input:
                self._start_turn_thread(pending_input)
            return True

        goal_stopped = action == "goal_stopped"
        if goal_stopped:
            self._goal_running = False
            self._goal_backgrounded = False
            self._goal_stage = ""
            self._goal_board_text = ""
            self._goal_last_transcript_progress_key = None
            self._add_line(
                "system",
                [
                    ("class:notification-goal", "Goal stopped"),
                    ("class:dim", " · paused/resumable"),
                ],
            )
            self._trace_goal_progress("finished", state="paused")
        if action == "clear_transcript":
            self._clear_transcript()
            self._restore_loaded_session_transcript()

        if result.kind in {"notice", "control"}:
            self._palette.close()
            if result.text:
                if action == "prt_started":
                    self._add_line("notice", [("class:notification-prt", result.text)])
                self._set_notice(result.text.splitlines()[0])
        elif result.text:
            self._notice_text = ""
            self._notice_until = 0.0
            self._palette.show_result(
                result.title or command_text or "command", result.text, kind=result.kind,
                actions=[PaletteItem(value, label) for value, label in result.choices],
            )

        if goal_stopped:
            self._process_next_queued_input()
        return True

    def _dashboard_action(self, kind: str, value: str, *, project: str = "",
                          expected_slot: str = "", roles=None) -> dict:
        """Navigate the exact hosting MO TUI; never run a second terminal UI."""
        from .terminal_host import focus_terminal
        from core.runtime.instance import get_instance_id

        app = getattr(self, "_app", None)
        if app is None or getattr(app, "loop", None) is None:
            raise RuntimeError("This MO terminal is not ready")
        completed = threading.Event()
        result = {}
        model_request = {}
        if kind == "model":
            import json
            model_request = json.loads(value)
            if not isinstance(model_request, dict) or not isinstance(model_request.get("request_id"), str):
                raise ValueError("Invalid model request")

        def apply():
            try:
                if expected_slot:
                    current_slot = str(getattr(getattr(self.agent, "_sessions", None), "current_name", ""))
                    if current_slot != expected_slot:
                        raise RuntimeError("Terminal changed conversation; choose its current entry again")
                if project:
                    from pathlib import Path
                    if Path(project).resolve() != Path(self.agent._effective_project_cwd()).resolve():
                        raise RuntimeError("Terminal changed project; refresh the list and choose again")
                focused = focus_terminal(get_instance_id()) if kind != "model" else False
                workspace = getattr(self, "_workspace", None)
                if kind in {"command", "request", "steer"} and workspace is not None and workspace.active:
                    workspace.focus_pane("main")
                if kind == "model":
                    if self._work_active():
                        raise RuntimeError("Terminal is working; its current model was kept. Try again when idle.")
                    from core.provider.model_catalog import activate_model_selection
                    activate_model_selection(self.agent, model_request.get("selection"),
                                             surface="terminal", reason="Settings live instance selection")
                    result["message"] = "Model changed in this terminal"
                elif kind == "stop":
                    handled = self._handle_busy_interrupt()
                    result["message"] = str(getattr(self, "_last_busy_escape_notice", "")) if handled else "Nothing to stop in this terminal"
                elif kind == "command":
                    children = self._palette_children_for_item(PaletteItem(value, value))
                    if children:
                        self._palette.show_command_args(value, children)
                        result["message"] = "Opened in this MO terminal"
                    elif " " not in value and value not in {"/now", "/status", "/knowledge"}:
                        if self._input_buf.text:
                            raise RuntimeError("Your terminal draft is preserved; use its composer for this command")
                        self._input_buf.text = value
                        self._input_buf.cursor_position = len(value)
                        result["message"] = "Command prepared in the normal MO terminal; press Enter there to run"
                    else:
                        self._run_palette_command(value)
                        result["message"] = "Opened in this MO terminal"
                elif kind == "request":
                    if self._input_buf.text and not expected_slot:
                        raise RuntimeError("Your terminal draft is preserved; use its composer for this request")
                    draft = str(self._input_buf.text or "").rstrip()
                    prepared = (
                        f"{draft}\n{value}"
                        if draft and value not in draft.splitlines()
                        else draft or value
                    )
                    self._input_buf.text = prepared
                    self._input_buf.cursor_position = len(prepared)
                    result["message"] = "Request prepared in the normal MO terminal; press Enter there to send"
                else:
                    result["message"] = "MO terminal opened" if focused else "Terminal is running; your window manager did not bring it forward"
                if not focused and kind in {"command", "request", "stop"}:
                    result["message"] += " · switch to the existing terminal window"
                app.invalidate()
            except Exception as exc:
                result["error"] = str(exc)
            finally:
                if kind == "model":
                    from core.runtime.heartbeat import record_heartbeat
                    self.agent._settings_model_control = {
                        "request_id": model_request["request_id"][:64],
                        "ok": not bool(result.get("error")),
                        "message": result.get("error") or result.get("message", ""),
                    }
                    record_heartbeat(self.agent, surface="terminal", event="settings_model")
                completed.set()

        import asyncio
        try:
            on_ui_loop = asyncio.get_running_loop() is app.loop
        except RuntimeError:
            on_ui_loop = False
        if on_ui_loop:
            apply()
        else:
            app.loop.call_soon_threadsafe(apply)
        if not completed.wait(5):
            return {"message": "Requested in the hosting terminal; acknowledgement is still pending"}
        if result.get("error"):
            raise RuntimeError(result["error"])
        return result

    def _handle_input(self, text: str, *, owner_bound: bool = False, echo: bool = True):
        """Dispatch composer input, or a normal turn bound to this MO instance."""
        if (
            not owner_bound
            and self._host_command_terminal() is not None
            and text.strip().startswith("/")
            and text.strip().split()[0].lower() != "/workspace"
        ):
            self._request_host_command(text, "select")
            return
        controller = getattr(self, "_workspace", None)
        if not owner_bound and controller is not None and controller.active:
            # Only the exact /workspace command root belongs to the parent. A
            # similarly prefixed shell command is ordinary terminal input.
            stripped = str(text or "").strip()
            root = stripped.split(maxsplit=1)[0].lower() if stripped else ""
            if root != "/workspace" and controller.route_input(text):
                return
        if not owner_bound and direct_interrupt_action(text):
            if self._work_active():
                self._handle_busy_interrupt()
            else:
                self._set_notice("Nothing to stop")
            return
        if not owner_bound and text.startswith("/"):
            self._run_palette_command(text)
            return

        self._start_turn_thread(text, echo=echo, owner_bound=owner_bound)

    def _start_turn_thread(self, text: str, *, echo: bool = False, owner_bound: bool = False) -> None:
        with self._ui_lock:
            if self._work_active():
                if owner_bound:
                    self._queue_input(text, source="handoff", owner_bound=True, echo=echo)
                else:
                    self._queue_input(text, echo=echo)
                return
            self.busy = True
            self._current_turn_cancel_event = threading.Event()
            if echo:
                self._add_user_echo(text)
            self._last_speaker = "user"
            self._turn_thread = threading.Thread(target=self._run_turn_thread, args=(text,), daemon=True)
        try:
            self._turn_thread.start()
        except Exception:
            with self._ui_lock:
                self.busy = False
                self._current_turn_cancel_event = None
                self._turn_thread = None
            raise

    def _open_internal_terminal(self, command: str = "") -> None:
        """Suspend MO and run a real terminal/command on the actual console, then
        return to MO. Light by design: one foreground terminal, no background
        processes, no new deps — codex/claude/any command run at full fidelity."""
        if getattr(self, "busy", False):
            self._set_notice("Finish the current turn before opening a terminal")
            return
        if getattr(self, "_app", None) is None:
            self._set_notice("Internal terminal needs the full TUI")
            return
        from prompt_toolkit.application import run_in_terminal
        from .internal_terminal import resolve_command, run_terminal_command

        spec, label = resolve_command(command)

        def _run() -> None:
            code = run_terminal_command(spec)
            self._set_notice(f"{label} exited (code {code})")

        try:
            run_in_terminal(_run)
        except Exception as exc:
            self._set_notice(f"Could not open terminal: {type(exc).__name__}")


