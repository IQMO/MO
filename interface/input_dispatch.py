"""TUI input, palette action, and slash-command dispatch mixin."""
from __future__ import annotations

import threading


class InputDispatchMixin:
    def _add_user_echo(self, text: str) -> None:
        """Echo a submitted user message, gradient-colouring any ``extrathink`` run.

        Plain ``class:user-msg`` for ordinary messages; a static colour gradient on
        the trigger word so it stands out in scrollback (the transcript can't animate
        per-frame, so history is static — colour only, glyphs/width unchanged)."""
        from .moon_visuals import _EXTRATHINK_RE, gradient_line
        line = f"* {text}"
        if _EXTRATHINK_RE.search(line):
            self._add_fragments_line(gradient_line(line, "class:user-msg"))
        else:
            self._add("class:user-msg", line)

    def _on_input_changed(self, buff):
        text = buff.text
        if text == "/" and not self._palette.open:
            self._palette.show()
            if buff.complete_state:
                buff.cancel_completion()
            if self._app:
                self._app.invalidate()
        elif self._palette.open and text and not text.startswith("/"):
            self._palette.close()
            if self._app:
                self._app.invalidate()
        elif self._palette.open and text.startswith("/") and " " in text:
            self._palette.close()
            if self._app:
                self._app.invalidate()

    def _handle_palette_selection(self):
        item = self._palette.selected_item()
        if not item:
            self._palette.close()
            return
        value = item.value
        children = [] if item.kind == "insert" else self._palette_children_for_item(item)
        if children:
            self._palette.enter_submenu(item.label, children)
            return
        if item.kind == "insert" or value.endswith(" "):
            if self._input_buf:
                self._input_buf.text = value
                self._input_buf.cursor_position = len(value)
            self._palette.close()
            return
        self._palette.close()
        if value.startswith("/"):
            self._run_palette_command(value)

    def _run_palette_command(self, text: str) -> bool:
        """Execute command-list actions as UI control, not transcript chat."""
        try:
            cmd_result = self.agent.process_slash_command(text)
        except Exception as exc:  # a local command must never crash the TUI loop
            self._set_notice(f"Command failed: {text.split()[0]} ({type(exc).__name__}: {exc})")
            return False
        if cmd_result is None:
            return False
        self._palette.record_command(text.split()[0])
        self._dispatch_slash_command_result(cmd_result, render_result=False)
        return True

    def _dispatch_slash_command_result(self, cmd_result: str, *, render_result: bool = True) -> bool:
        """Apply slash-command result without ever echoing the raw command."""
        if cmd_result == "[EXIT]":
            if self._app:
                self._app.exit()
            return True
        if cmd_result == "[GOAL_START]":
            self._start_goal_thread()
            return True
        if cmd_result == "[GOAL_CONTINUE]":
            self._show_active_goal()
            return True
        if cmd_result == "[RETRY]":
            retry_input = getattr(self.agent, "_retry_pending_input", "")
            self.agent._retry_pending_input = ""
            if retry_input:
                if render_result:
                    self._add("class:dim", f"  retrying: {retry_input[:80]}")
                else:
                    self._set_notice(f"retrying: {retry_input[:80]}")
                self._last_speaker = "user"
                threading.Thread(target=self._run_turn_thread, args=(retry_input,), daemon=True).start()
            elif render_result:
                self._add("class:dim", "  nothing to retry")
            else:
                self._set_notice("nothing to retry")
            return True
        if cmd_result == "[RUN_TURN]":
            pending_input = getattr(self.agent, "_slash_pending_input", "")
            self.agent._slash_pending_input = ""
            if pending_input:
                if render_result:
                    self._add("", "")
                    self._add_user_echo(pending_input)
                    self._add("", "")
                else:
                    self._set_notice(f"running: {pending_input[:80]}")
                self._last_speaker = "user"
                threading.Thread(target=self._run_turn_thread, args=(pending_input,), daemon=True).start()
            elif render_result:
                self._add("class:dim", "  nothing to run")
            else:
                self._set_notice("nothing to run")
            return True
        if cmd_result == "[GHOST_ON]":
            self._apply_ghost_on()
            if render_result:
                self._add("class:activity", "  Ghost: on")
            else:
                self._set_notice("Ghost: on")
            return True
        if cmd_result == "[GHOST_OFF]":
            self._apply_ghost_off()
            if render_result:
                self._add("class:activity", "  Ghost: off")
            else:
                self._set_notice("Ghost: off")
            return True
        if cmd_result.startswith("[GOAL STOPPED]"):
            self._goal_running = False
            self._goal_backgrounded = False
            self._goal_stage = ""
            self._goal_board_text = ""
            if render_result:
                for line in cmd_result.splitlines():
                    self._add("class:mo-response", f"  {line}")
            else:
                self._set_notice(self._goal_finish_summary(cmd_result))
            self._process_next_queued_input()
            return True
        if cmd_result == "Conversation cleared." or cmd_result.startswith("New session:"):
            self._clear_transcript()
        if render_result:
            style = "class:notification-prt" if str(cmd_result or "").startswith("[PRT STARTED]") else ""
            for line in cmd_result.splitlines():
                self._add(style, f"  {line}")
        else:
            first = str(cmd_result or "").strip().splitlines()[0] if str(cmd_result or "").strip() else "Command done"
            self._set_notice(first)
        return True

    def _handle_input(self, text: str, *, force_main: bool = False):
        # Ghost mode routing: messages go to Ghost, but slash commands stay control
        # input (so /ghost off, /exit, /status work while ghost is on), and an
        # explicit Ghost->main handoff (force_main) must reach the main agent rather
        # than re-entering Ghost.
        if getattr(self, "_ghost_enabled", False) and not force_main and not text.strip().startswith("/"):
            self._ghost_panel_ask(text)
            return
        if self._work_active() and not self._command_allowed_while_working(text):
            if text.strip().lower().startswith(("/goal ", "/g ")) and not (self._goal_worker_active or getattr(self.agent, "_goal_active", False)):
                self._run_goal_command_now(text)
            elif text.strip().lower().startswith(("/goal ", "/g ")):
                self._queue_goal_command(text)
            else:
                self._queue_input(text)
            return

        if text.startswith("/"):
            try:
                cmd_result = self.agent.process_slash_command(text)
            except Exception as exc:  # a local command must never crash the TUI loop
                self._set_notice(f"Command failed: {text.split()[0]} ({type(exc).__name__}: {exc})")
                return
            if cmd_result is not None:
                self._dispatch_slash_command_result(cmd_result, render_result=True)
                return
            self._set_notice(f"Unknown command: {text.split()[0]}")
            return

        self._add("", "")
        self._add_user_echo(text)
        self._add("", "")
        self._last_speaker = "user"
        threading.Thread(target=self._run_turn_thread, args=(text,), daemon=True).start()


