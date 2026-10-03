"""MO Keybindings — single source of truth for full TUI keyboard shortcuts.

Surfaces
    * Main TUI       — ``build_tui_key_bindings(tui)``
    * Plain input    — ``interface/input.py`` (Enter, Ctrl+C, Ctrl+D,
                       Ctrl+L, Ctrl+E, Tab, Shift+Tab, Esc)
    * Sandbox guard  — ``core/tooling/sandbox.py`` blocks Win+R / Win+X in actuation

Do not add another full-TUI ``KeyBindings()`` factory outside this module.
The plain prompt-toolkit fallback stays in ``interface/input.py`` because it
has a smaller surface and must keep working without importing the full TUI.
"""
from __future__ import annotations

from typing import Any

from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

from .command_registry import slash_command_exists
from .formatting import format_k

PASTE_INLINE_CHAR_LIMIT = 2_000
PASTE_MAX_CHARS = 12_000
PASTE_HOLDER_LINE_LIMIT = 5


def normalize_paste_text(data: str, *, max_chars: int = PASTE_MAX_CHARS) -> tuple[str, bool]:
    """Normalize a terminal paste and cap it before it can enter/send from the UI."""
    cleaned = "\n".join(line.rstrip() for line in str(data or "").replace("\r\n", "\n").replace("\r", "\n").splitlines())
    if len(cleaned) <= max_chars:
        return cleaned, False
    return cleaned[:max_chars].rstrip(), True


def paste_holder_label(text: str, *, truncated: bool = False) -> str:
    lines = max(1, str(text or "").count("\n") + 1)
    prefix = "first " if truncated else ""
    return f"[paste held: {prefix}{format_k(len(text))} chars, {lines} lines — Enter sends, Esc clears]"


def build_tui_key_bindings(tui: Any) -> KeyBindings:
    """Build protected TUI keybindings without changing control behavior."""
    kb = KeyBindings()

    workspace_split_active = Condition(
        lambda: bool(
            getattr(getattr(tui, "_workspace", None), "split_active", False)
        )
    )
    raw_terminal_active = Condition(
        lambda: bool(
            callable(getattr(getattr(tui, "_workspace", None), "raw_terminal_active", None))
            and tui._workspace.raw_terminal_active()
        )
    )
    mo_input_active = ~raw_terminal_active
    workspace_scroll_active = mo_input_active | workspace_split_active
    workspace_rail_open = Condition(
        lambda: bool(getattr(getattr(tui, "_workspace", None), "rail_open", False))
    )

    def send_terminal_input(text: str) -> None:
        controller = getattr(tui, "_workspace", None)
        if controller is not None:
            controller.send_raw_input(text)

    def managed_transcript() -> bool:
        enabled = getattr(tui, "_scrollback_transcript_enabled", None)
        return not (callable(enabled) and enabled())

    def input_allows_transcript_scroll(buffer: Any) -> bool:
        if not managed_transcript():
            return False
        if buffer != tui._input_buf:
            return False
        text = str(getattr(buffer, "text", "") or "")
        return not text

    def scroll_focused_workspace(buffer: Any, delta: int) -> bool:
        if buffer != tui._input_buf:
            return False
        controller = getattr(tui, "_workspace", None)
        scroll = getattr(controller, "scroll_focused", None) if controller is not None else None
        return bool(scroll(delta)) if callable(scroll) else False

    def scroll_focused_workspace_to_edge(buffer: Any, *, oldest: bool) -> bool:
        if buffer != tui._input_buf:
            return False
        controller = getattr(tui, "_workspace", None)
        scroll = (
            getattr(controller, "scroll_focused_to_edge", None)
            if controller is not None
            else None
        )
        return bool(scroll(oldest=oldest)) if callable(scroll) else False

    def clear_paste_holder() -> None:
        tui._paste_holder_active = False
        tui._paste_holder_text = ""
        tui._pre_paste_buffer_text = ""

    def clear_enhance_holder() -> None:
        tui._enhance_holder_active = False
        tui._pre_enhance_text = ""

    def set_input_text(text: str) -> None:
        tui._input_buf.text = text
        tui._input_buf.cursor_position = len(text)

    def submit_input_text() -> str:
        if getattr(tui, "_paste_holder_active", False):
            text = str(getattr(tui, "_paste_holder_text", "") or "").strip()
        else:
            text = str(getattr(tui._input_buf, "text", "") or "").strip()
        clear_paste_holder()
        clear_enhance_holder()
        set_input_text("")
        return text

    def set_notice(text: str) -> None:
        notice = getattr(tui, "_set_notice", None)
        if callable(notice):
            notice(text)

    @kb.add("enter")
    def _(event):
        controller = getattr(tui, "_workspace", None)
        if controller is not None and controller.rail_open:
            set_notice(controller.rail_accept())
            event.app.invalidate()
            return
        if controller is not None and controller.raw_terminal_active():
            draft = str(getattr(tui._input_buf, "text", "") or "")
            if draft:
                controller.send_raw_input(draft)
                set_input_text("")
            controller.send_raw_input("\r")
            event.app.invalidate()
            return
        b = event.app.current_buffer
        if tui._palette.open:
            typed = str(getattr(tui._input_buf, "text", "") or "").strip()
            root = typed.split()[0] if typed else ""
            should_submit = getattr(tui, "_palette_exact_command_should_submit", None)
            exact_should_submit = should_submit(typed) if callable(should_submit) else True
            # The bare-command fast-path only applies at the top level. While a
            # submenu / arg list is open, Enter must select the highlighted row —
            # otherwise "/skin " + arrow + Enter would run the bare "/skin" and
            # ignore the chosen skin (you'd have to type the full command).
            in_submenu = bool(getattr(tui._palette, "in_submenu", False))
            palette_available = getattr(tui, "_mo_command_palette_available", None)
            input_belongs_to_mo = not callable(palette_available) or palette_available()
            if (
                input_belongs_to_mo
                and typed
                and " " not in typed
                and not in_submenu
                and slash_command_exists(root)
                and exact_should_submit
            ):
                text = submit_input_text()
                tui._palette.close()
                tui._palette.record_command(root)
                tui._handle_input(text)
            else:
                tui._handle_palette_selection()
            event.app.invalidate()
            return
        if b.complete_state:
            b.apply_completion(b.complete_state.current_completion)
            b.cancel_completion()
        elif b == tui._input_buf:
            text = submit_input_text()
            if text:
                if text.startswith("/"):
                    tui._palette.record_command(text.split()[0])
                tui._handle_input(text)
            elif tui.busy:
                tui._promote_last_queued_input_to_steer()

    @kb.add("c-j", filter=~raw_terminal_active)
    def _(event):
        """Ctrl+J inserts a newline for multi-line input."""
        event.app.current_buffer.insert_text("\n")

    @kb.add("f4", eager=True, filter=~raw_terminal_active & ~workspace_rail_open)
    def _(event):
        tui._toggle_command_palette()
        event.app.invalidate()

    @kb.add("left", eager=True, filter=mo_input_active)
    def _(event):
        if tui._palette.open:
            tui._palette.move_category(-1)
            event.app.invalidate()
        else:
            event.app.current_buffer.cursor_left(count=1)

    @kb.add("right", eager=True, filter=mo_input_active)
    def _(event):
        if tui._palette.open:
            tui._palette.move_category(1)
            event.app.invalidate()
        else:
            event.app.current_buffer.cursor_right(count=1)

    @kb.add("tab", filter=mo_input_active)
    def _(event):
        if tui._palette.open:
            tui._palette.move_category(1)
            event.app.invalidate()
            return
        b = event.app.current_buffer
        if b.complete_state:
            b.complete_next()
        else:
            b.start_completion()

    @kb.add("s-tab", filter=mo_input_active)
    def _(event):
        if tui._palette.open:
            tui._palette.move_category(-1)
            event.app.invalidate()
            return
        b = event.app.current_buffer
        if b.complete_state:
            b.complete_previous()

    @kb.add("c-e", eager=True, filter=mo_input_active)
    def _(event):
        """Ctrl+E: rewrite the typed message into a sharper prompt (in place).

        Prose only — ignored on empty input or slash commands (so completion/control
        commands are untouched). Runs off-thread; Esc reverts to the original.
        """
        b = event.app.current_buffer
        if b is not tui._input_buf:
            return
        text = str(b.text or "").strip()
        if not text or text.startswith("/"):
            return
        if getattr(tui, "busy", False):
            set_notice("MO is busy — enhance after the current turn")
            return
        starter = getattr(tui, "_start_prompt_enhance", None)
        if callable(starter):
            starter(b.text)

    @kb.add("c-c")
    def _(event):
        """Ctrl+C interrupts only the focused local terminal or the active MO turn."""
        controller = getattr(tui, "_workspace", None)
        terminal = controller.focused_terminal if controller is not None and controller.active else None
        if terminal is not None:
            if controller.cancel_focused():
                set_notice(f"Interrupted terminal pane {controller.focused_position}")
            else:
                set_notice("Focused terminal is no longer running")
            event.app.invalidate()
            return
        if getattr(tui, "busy", False):
            tui._handle_busy_interrupt()
            event.app.invalidate()
            return
        event.app.exit()

    @kb.add("c-d", filter=mo_input_active)
    def _(event):
        event.app.exit()

    @kb.add("escape", filter=mo_input_active)
    def _(event):
        b = event.app.current_buffer
        controller = getattr(tui, "_workspace", None)
        launcher_was_open = bool(getattr(controller, "launcher_open", False))
        if controller is not None and controller.rail_cancel():
            set_notice("Returned to terminal panel" if launcher_was_open else "Terminal panel closed")
            event.app.invalidate()
        elif tui._palette.open:
            if not tui._palette.back():
                tui._palette.close()
            event.app.invalidate()
        elif b.complete_state:
            b.cancel_completion()
        elif getattr(tui, "_paste_holder_active", False):
            restore = str(getattr(tui, "_pre_paste_buffer_text", "") or "")
            clear_paste_holder()
            tui._input_buf.text = restore
            tui._input_buf.cursor_position = len(restore)
            set_notice("Paste cleared")
            event.app.invalidate()
        elif getattr(tui, "_enhance_holder_active", False):
            restore = str(getattr(tui, "_pre_enhance_text", "") or "")
            clear_enhance_holder()
            tui._input_buf.text = restore
            tui._input_buf.cursor_position = len(restore)
            set_notice("Reverted to your message")
            event.app.invalidate()
        elif tui.busy:
            tui._handle_busy_escape()

    @kb.add("escape", "up", eager=True, filter=workspace_scroll_active)
    def _(event):
        """Restore pending input first; otherwise scroll the selected workspace pane."""
        b = event.app.current_buffer
        if getattr(getattr(tui, "_workspace", None), "health_open", False):
            if scroll_focused_workspace(b, 1):
                event.app.invalidate()
        elif not str(getattr(b, "text", "") or "").strip() and tui._restore_last_queued_input_to_editor():
            event.app.invalidate()
        elif scroll_focused_workspace(b, 1):
            event.app.invalidate()

    @kb.add("escape", "down", eager=True, filter=workspace_scroll_active)
    def _(event):
        """Alt+Down scrolls the selected split MO pane toward newer output."""
        if scroll_focused_workspace(event.app.current_buffer, -1):
            event.app.invalidate()

    @kb.add("escape", "home", eager=True, filter=workspace_scroll_active)
    def _(event):
        """Alt+Home jumps the selected workspace pane to its oldest output."""
        if scroll_focused_workspace_to_edge(event.app.current_buffer, oldest=True):
            event.app.invalidate()

    @kb.add("escape", "end", eager=True, filter=workspace_scroll_active)
    def _(event):
        """Alt+End returns the selected workspace pane to its latest output."""
        if scroll_focused_workspace_to_edge(event.app.current_buffer, oldest=False):
            event.app.invalidate()

    @kb.add("c-g", eager=True, filter=mo_input_active)
    def _(event):
        tui._toggle_goal_background()

    @kb.add("up", eager=True, filter=mo_input_active)
    def _(event):
        b = event.app.current_buffer
        controller = getattr(tui, "_workspace", None)
        if controller is not None and controller.rail_open:
            if controller.launcher_open:
                controller.launcher_move(-1)
            else:
                controller.rail_move(-1)
            event.app.invalidate()
        elif tui._palette.open:
            tui._palette.move_selection(-1)
            event.app.invalidate()
        elif b.complete_state:
            b.complete_previous()
        elif not str(getattr(b, "text", "") or "").strip() and tui._restore_last_queued_input_to_editor():
            # With an empty editor and a queued message, Up recalls it for editing
            # (and cancels the queue) — like shell history for a pending send.
            event.app.invalidate()
        elif input_allows_transcript_scroll(b):
            tui._scroll_transcript(3)
        elif managed_transcript():
            b.cursor_up(count=1)
        else:
            b.auto_up(count=1)

    @kb.add("down", eager=True, filter=mo_input_active)
    def _(event):
        b = event.app.current_buffer
        controller = getattr(tui, "_workspace", None)
        if controller is not None and controller.rail_open:
            if controller.launcher_open:
                controller.launcher_move(1)
            else:
                controller.rail_move(1)
            event.app.invalidate()
        elif tui._palette.open:
            tui._palette.move_selection(1)
            event.app.invalidate()
        elif b.complete_state:
            b.complete_next()
        elif input_allows_transcript_scroll(b):
            tui._scroll_transcript(-3)
        elif managed_transcript():
            b.cursor_down(count=1)
        else:
            b.auto_down(count=1)

    @kb.add("c-up", eager=True, filter=mo_input_active)
    def _(event):
        if managed_transcript():
            tui._scroll_transcript(10)
        else:
            event.app.current_buffer.auto_up(count=10)

    @kb.add("c-down", eager=True, filter=mo_input_active)
    def _(event):
        if managed_transcript():
            tui._scroll_transcript(-10)
        else:
            event.app.current_buffer.auto_down(count=10)

    @kb.add("c-s-up", eager=True, filter=mo_input_active)
    def _(event):
        """Ctrl+Shift+Up: scroll visible boards when content exceeds viewport."""
        tui._scroll_boards(3)
        event.app.invalidate()

    @kb.add("c-s-down", eager=True, filter=mo_input_active)
    def _(event):
        """Ctrl+Shift+Down: scroll visible boards when content exceeds viewport."""
        tui._scroll_boards(-3)
        event.app.invalidate()

    @kb.add("pageup", eager=True, filter=mo_input_active)
    def _(event):
        if tui._palette.open:
            for _ in range(8):
                tui._palette.move_selection(-1)
            event.app.invalidate()
        elif managed_transcript():
            tui._scroll_transcript(10)
        else:
            event.app.current_buffer.auto_up(count=10)

    @kb.add("pagedown", eager=True, filter=mo_input_active)
    def _(event):
        if tui._palette.open:
            for _ in range(8):
                tui._palette.move_selection(1)
            event.app.invalidate()
        elif managed_transcript():
            tui._scroll_transcript(-10)
        else:
            event.app.current_buffer.auto_down(count=10)

    # Mouse-wheel and Shift+PageUp/Shift+PageDown are intentionally NOT bound:
    # with mouse_support=False the terminal owns full native scrollback, selection,
    # and copy. In split mode, plain Up/Down scroll the selected composer-owned pane;
    # local PTYs still own their raw arrows. Outside split mode, these keys retain
    # normal editor behavior or scroll MO's compatibility transcript viewport.

    # Home/End use prompt-toolkit's editor defaults on MO and emit terminal-native
    # sequences while a terminal pane owns input.

    @kb.add(Keys.BracketedPaste, filter=mo_input_active)
    def _(event):
        cleaned, truncated = normalize_paste_text(event.data or "")
        if not cleaned:
            return
        line_count = cleaned.count("\n") + 1
        if len(cleaned) > PASTE_INLINE_CHAR_LIMIT or line_count > PASTE_HOLDER_LINE_LIMIT:
            existing = str(getattr(tui._input_buf, "text", "") or "")
            held = (existing + cleaned)[:PASTE_MAX_CHARS].rstrip()
            truncated = truncated or len(existing + cleaned) > PASTE_MAX_CHARS
            label = paste_holder_label(held, truncated=truncated)
            tui._pre_paste_buffer_text = existing
            tui._paste_holder_text = held
            tui._paste_holder_active = True
            set_input_text(label)
            event.app.invalidate()
            return
        clear_paste_holder()
        tui._input_buf.insert_text(cleaned)
        event.app.invalidate()

    @kb.add("c-l", filter=mo_input_active)
    def _(event):
        """Ctrl+L: redraw the terminal screen (convention from readline/shell)."""
        event.app.renderer.clear()
        event.app.invalidate()

    @kb.add("c-t", eager=True, filter=mo_input_active)
    def _(event):
        """Ctrl+T: open a real terminal inside MO (a shell; use /terminal <cmd>
        for codex/claude/any command). Reliable across terminals, unlike
        Ctrl+Shift+N which most terminals don't send distinctly."""
        opener = getattr(tui, "_open_internal_terminal", None)
        if callable(opener):
            opener("")

    @kb.add("escape", "left", eager=True, filter=workspace_split_active)
    def _(event):
        """Alt+Left moves left; from the first pane it opens the terminal rail."""
        controller = getattr(tui, "_workspace", None)
        if controller is not None and controller.split_active:
            if bool(getattr(controller, "rail_open", False)):
                controller.rail_move(-1)
            elif int(getattr(controller, "focused_position", 2)) <= 1:
                set_notice(controller.open_rail())
            else:
                set_notice(controller.move_focus(-1))
            event.app.invalidate()

    @kb.add("escape", "right", eager=True, filter=workspace_split_active)
    def _(event):
        """Alt+Right moves right or accepts a terminal from the open rail."""
        controller = getattr(tui, "_workspace", None)
        if controller is not None and controller.split_active:
            if bool(getattr(controller, "rail_open", False)):
                set_notice(controller.rail_accept())
            else:
                set_notice(controller.move_focus(1))
            event.app.invalidate()

    @kb.add(
        "escape",
        "f",
        eager=True,
        filter=workspace_split_active & ~workspace_rail_open,
    )
    def _(event):
        """Alt+F toggles the focused pane across the full workspace window."""
        controller = getattr(tui, "_workspace", None)
        if controller is not None:
            set_notice(controller.toggle_pane_full_window())
            event.app.invalidate()

    @kb.add("c-b", eager=True)
    def _(event):
        """Ctrl+B toggles the workspace terminal rail from every input owner."""
        controller = getattr(tui, "_workspace", None)
        if controller is not None:
            if bool(getattr(controller, "rail_open", False)):
                if bool(getattr(controller, "launcher_open", False)):
                    controller.launcher_cancel()
                controller.rail_cancel()
                set_notice("Terminal panel closed")
            else:
                set_notice(controller.open_rail())
            event.app.invalidate()

    @kb.add("x", eager=True, filter=workspace_rail_open)
    @kb.add("escape", "x", eager=True, filter=workspace_split_active)
    def _(event):
        """X closes the rail selection; Alt+X closes the focused workspace terminal."""
        controller = getattr(tui, "_workspace", None)
        if controller is not None:
            close = controller.rail_close_selected if controller.rail_open else controller.close
            set_notice(close())
            event.app.invalidate()

    @kb.add("/", eager=True, filter=workspace_rail_open)
    def _(event):
        """Select the highlighted pane before delivering its slash key."""
        controller = getattr(tui, "_workspace", None)
        if controller is None:
            return
        set_notice(controller.rail_accept())
        # Selecting + New terminal keeps the rail open and transfers ownership
        # to its nested launcher. Do not leak the slash into the composer.
        if controller.rail_open:
            event.app.invalidate()
            return
        if controller.raw_terminal_active():
            controller.send_raw_input("/")
        else:
            event.app.current_buffer.insert_text("/")
        event.app.invalidate()

    def bind_terminal_key(key: Any, payload: str, *, eager: bool = True) -> None:
        @kb.add(key, eager=eager, filter=raw_terminal_active)
        def _(event):
            send_terminal_input(payload)

    for key, payload in (
        ("left", "\x1b[D"), ("right", "\x1b[C"),
        ("up", "\x1b[A"), ("down", "\x1b[B"),
        ("c-up", "\x1b[1;5A"), ("c-down", "\x1b[1;5B"),
        ("c-s-up", "\x1b[1;6A"), ("c-s-down", "\x1b[1;6B"),
        ("home", "\x1b[H"), ("end", "\x1b[F"),
        ("delete", "\x1b[3~"), ("pageup", "\x1b[5~"),
        ("pagedown", "\x1b[6~"), ("tab", "\t"),
        ("s-tab", "\x1b[Z"), ("backspace", "\x7f"),
        ("f4", "\x1bOS"), ("c-j", "\n"), ("c-l", "\x0c"),
        ("c-d", "\x04"), ("c-e", "\x05"), ("c-g", "\x07"),
        ("c-p", "\x10"), ("c-t", "\x14"),
    ):
        bind_terminal_key(key, payload)
    bind_terminal_key("escape", "\x1b", eager=False)

    @kb.add(Keys.BracketedPaste, eager=True, filter=raw_terminal_active)
    def _(event):
        send_terminal_input(event.data or "")

    # Keys.Any must stay non-eager: Escape is also the prefix of the Alt+Left /
    # Alt+Right pane-switch chords, and an eager Keys.Any swallows that Escape
    # before the chord can complete. Non-eager lets prompt_toolkit wait for the
    # chord (and fall back to a lone-Escape forward after the standard timeout).
    @kb.add(Keys.Any, filter=raw_terminal_active)
    def _(event):
        send_terminal_input(event.data or "")

    return kb
