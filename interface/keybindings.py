"""Prompt-toolkit keybinding construction for MO TUI."""
from __future__ import annotations

from typing import Any

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys

from .command_registry import SLASH_ALIASES, SLASH_COMMANDS
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
    return f"[paste held: {prefix}{format_k(len(text))} chars, {lines} lines — Enter sends]"


def build_tui_key_bindings(tui: Any) -> KeyBindings:
    """Build protected TUI keybindings without changing control behavior."""
    kb = KeyBindings()

    def input_allows_transcript_scroll(buffer: Any) -> bool:
        if buffer != tui._input_buf:
            return False
        text = str(getattr(buffer, "text", "") or "")
        if not text:
            return True
        return False

    def clear_paste_holder() -> None:
        tui._paste_holder_active = False
        tui._paste_holder_text = ""
        tui._pre_paste_buffer_text = ""

    def submit_input_text() -> str:
        if getattr(tui, "_paste_holder_active", False):
            text = str(getattr(tui, "_paste_holder_text", "") or "").strip()
            clear_paste_holder()
            return text
        clear_paste_holder()
        return str(getattr(tui._input_buf, "text", "") or "").strip()

    def set_input_text(text: str) -> None:
        tui._input_buf.text = text
        tui._input_buf.cursor_position = len(text)

    def set_notice(text: str) -> None:
        notice = getattr(tui, "_set_notice", None)
        if callable(notice):
            notice(text)

    def toggle_ghost_panel(event) -> None:
        """Alt+G toggles Ghost mode: panel + routing on/off.

        Keyed on _ghost_enabled (the routing state = the actual "mode"), not
        panel visibility: the panel can be open with routing OFF (a one-off
        side-question / history view), and Alt+G should then turn Ghost ON
        rather than just close that transient panel.
        """
        if getattr(tui, "_ghost_enabled", False):
            tui._apply_ghost_off()
            set_notice("Ghost off — messages route to MO")
        else:
            tui._apply_ghost_on()
            set_notice("Ghost on — messages route to Ghost")
        event.app.invalidate()

    @kb.add("enter")
    def _(event):
        b = event.app.current_buffer
        if tui._palette.open:
            typed = str(getattr(tui._input_buf, "text", "") or "").strip()
            root = typed.split()[0] if typed else ""
            if typed and " " not in typed and (root in SLASH_COMMANDS or root in SLASH_ALIASES):
                tui._palette.close()
                tui._palette.record_command(root)
                tui._handle_input(typed)
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
                tui._input_buf.text = ""
                if text.startswith("/"):
                    tui._palette.record_command(text.split()[0])
                tui._handle_input(text)
            elif tui.busy:
                tui._advance_queued_input_intent()

    @kb.add("c-j")
    def _(event):
        """Ctrl+J inserts a newline for multi-line input."""
        event.app.current_buffer.insert_text("\n")

    @kb.add("f4", eager=True)
    def _(event):
        tui._palette.toggle()
        event.app.invalidate()

    @kb.add("left", eager=True)
    def _(event):
        if tui._palette.open:
            tui._palette.move_category(-1)
            event.app.invalidate()
        else:
            event.app.current_buffer.cursor_left(count=1)

    @kb.add("right", eager=True)
    def _(event):
        if tui._palette.open:
            tui._palette.move_category(1)
            event.app.invalidate()
        else:
            event.app.current_buffer.cursor_right(count=1)

    @kb.add("tab")
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

    @kb.add("s-tab")
    def _(event):
        if tui._palette.open:
            tui._palette.move_category(-1)
            event.app.invalidate()
            return
        b = event.app.current_buffer
        if b.complete_state:
            b.complete_previous()

    @kb.add("c-c")
    def _(event):
        """Ctrl+C: cancel current work when busy (terminal convention); exit when idle.
        Ctrl+D always exits."""
        if getattr(tui, "busy", False):
            tui._handle_busy_escape()
            event.app.invalidate()
            return
        event.app.exit()

    @kb.add("escape", "g", eager=True)
    def _(event):
        """Alt+G reveals/hides Ghost without using prompt-toolkit mouse capture."""
        toggle_ghost_panel(event)

    @kb.add("escape", "G", eager=True)
    def _(event):
        """Alt+Shift+G/uppercase fallback for terminals that preserve case."""
        toggle_ghost_panel(event)

    @kb.add("c-o", eager=True)
    def _(event):
        """Ctrl+O expands/collapses Ghost details only while Ghost is visible."""
        if tui._ghost_panel_open:
            tui._ghost_expanded = not bool(getattr(tui, "_ghost_expanded", False))
            tui._ghost_scroll_from_bottom = min(tui._ghost_scroll_from_bottom, tui._max_ghost_scroll())
            event.app.invalidate()

    @kb.add("c-d")
    def _(event):
        event.app.exit()

    @kb.add("escape")
    def _(event):
        b = event.app.current_buffer
        if tui._palette.open:
            if not tui._palette.back():
                tui._palette.close()
            event.app.invalidate()
        elif tui._ghost_panel_open:
            # Escape hides Ghost panel AND disables routing so the next
            # message doesn't auto-reopen it (consistent with Alt+G toggle).
            tui._apply_ghost_off()
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
        elif tui.busy:
            tui._handle_busy_escape()

    @kb.add("c-g", eager=True)
    def _(event):
        tui._toggle_goal_background()

    @kb.add("up", eager=True)
    def _(event):
        b = event.app.current_buffer
        if tui._palette.open:
            tui._palette.move_selection(-1)
            event.app.invalidate()
        elif b.complete_state:
            b.complete_previous()
        elif tui._ghost_panel_open:
            tui._show_ghost_history(-1)
        elif input_allows_transcript_scroll(b):
            tui._scroll_transcript(3)
        else:
            b.cursor_up(count=1)

    @kb.add("down", eager=True)
    def _(event):
        b = event.app.current_buffer
        if tui._palette.open:
            tui._palette.move_selection(1)
            event.app.invalidate()
        elif b.complete_state:
            b.complete_next()
        elif tui._ghost_panel_open:
            tui._show_ghost_history(1)
        elif input_allows_transcript_scroll(b):
            tui._scroll_transcript(-3)
        else:
            b.cursor_down(count=1)

    @kb.add("c-up", eager=True)
    def _(event):
        if tui._ghost_panel_open:
            tui._scroll_ghost(3)
        else:
            tui._scroll_transcript(10)

    @kb.add("c-down", eager=True)
    def _(event):
        if tui._ghost_panel_open:
            tui._scroll_ghost(-3)
        else:
            tui._scroll_transcript(-10)

    @kb.add("c-s-up", eager=True)
    def _(event):
        """Ctrl+Shift+Up: scroll visible boards when content exceeds viewport."""
        tui._scroll_boards(3)
        event.app.invalidate()

    @kb.add("c-s-down", eager=True)
    def _(event):
        """Ctrl+Shift+Down: scroll visible boards when content exceeds viewport."""
        tui._scroll_boards(-3)
        event.app.invalidate()

    @kb.add("pageup", eager=True)
    def _(event):
        if tui._palette.open:
            for _ in range(8):
                tui._palette.move_selection(-1)
            event.app.invalidate()
        else:
            tui._scroll_transcript(10)

    @kb.add("pagedown", eager=True)
    def _(event):
        if tui._palette.open:
            for _ in range(8):
                tui._palette.move_selection(1)
            event.app.invalidate()
        else:
            tui._scroll_transcript(-10)

    @kb.add(Keys.ScrollUp, eager=True)
    def _(event):
        if tui._ghost_panel_open:
            tui._scroll_ghost(3)
        else:
            tui._scroll_transcript(5)

    @kb.add(Keys.ScrollDown, eager=True)
    def _(event):
        if tui._ghost_panel_open:
            tui._scroll_ghost(-3)
        else:
            tui._scroll_transcript(-5)

    @kb.add("home", eager=True)
    def _(event):
        tui._transcript_top()

    @kb.add("end", eager=True)
    def _(event):
        tui._transcript_bottom()

    @kb.add(Keys.BracketedPaste)
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
            set_notice("Paste held; Enter sends, Esc clears")
            event.app.invalidate()
            return
        clear_paste_holder()
        tui._input_buf.insert_text(cleaned)
        event.app.invalidate()

    @kb.add("c-l")
    def _(event):
        """Ctrl+L: redraw the terminal screen (convention from readline/shell)."""
        event.app.renderer.clear()
        event.app.invalidate()

    return kb
