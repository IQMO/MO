from __future__ import annotations

import os
import queue
import threading
from typing import Any

from .formatting import format_agent_status
from .slash_commands import slash_command_with_desc, SLASH_COMMANDS, SLASH_ALIASES, SLASH_SUBCOMMANDS

_STOP_INPUT = object()

try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.completion import Completer, Completion, PathCompleter
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.input import create_input
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout.containers import HSplit, Window, FloatContainer, Float
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.layout.layout import Layout
    from prompt_toolkit.layout.menus import CompletionsMenu
    from prompt_toolkit.layout.processors import BeforeInput

    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False

    class Completer:
        def get_completions(self, document, complete_event):
            return iter(())

    class Completion:
        def __init__(self, text: str, start_position: int = 0, display_meta: str = ""):
            self.text = text
            self.start_position = start_position
            self.display_meta = display_meta

    class PathCompleter:
        def __init__(self, *args, **_kwargs):
            pass

        def get_completions(self, document, complete_event):
            return iter(())

    def create_input():
        raise RuntimeError("prompt_toolkit is not available")


def terminal_columns() -> int:
    try:
        return os.get_terminal_size(1).columns
    except Exception:
        return 80


def terminal_separator(cols: int | None = None) -> str:
    width = terminal_columns() if cols is None else cols
    return "─" * min(width - 2, 100)


class SlashAndPathCompleter(Completer):
    def __init__(self):
        self.path_completer = PathCompleter(expanduser=True)

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/"):
            if " " in text:
                cmd, arg_prefix = text.split(" ", 1)
                subs = SLASH_SUBCOMMANDS.get(cmd, [])
                current = arg_prefix.strip()
                for sub, desc in subs:
                    if sub.startswith(current):
                        yield Completion(sub, start_position=-len(current), display_meta=desc)
                if not subs:
                    yield from self.path_completer.get_completions(document, complete_event)
                return
            for cmd, desc in slash_command_with_desc():
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text), display_meta=desc)
            for alias, target in SLASH_ALIASES.items():
                if alias.startswith(text) and alias != text:
                    desc = SLASH_COMMANDS.get(target, "")
                    yield Completion(alias, start_position=-len(text), display_meta=f"→ {target}" if not desc else desc)
            return
        yield from self.path_completer.get_completions(document, complete_event)


def prompt_toolkit_input(agent: Any) -> str:
    if not HAS_PROMPT_TOOLKIT:
        raise RuntimeError("prompt_toolkit is not available")
    cols = terminal_columns()

    kb = KeyBindings()
    buf = Buffer(completer=SlashAndPathCompleter(), complete_while_typing=False)

    @kb.add("enter")
    def _(event):
        b = event.app.current_buffer
        if b.complete_state:
            # Insert selected completion, don't submit
            b.apply_completion(b.complete_state.current_completion)
            b.cancel_completion()
        else:
            event.app.exit(result=buf.text)

    @kb.add("c-c")
    def _(event):
        event.app.exit(result=_STOP_INPUT)

    @kb.add("c-d")
    def _(event):
        event.app.exit(result=_STOP_INPUT)

    @kb.add("c-l")
    def _(event):
        """Ctrl+L: redraw the terminal screen (convention from readline/shell)."""
        event.app.renderer.clear()
        event.app.invalidate()

    @kb.add("tab")
    def _(event):
        b = event.app.current_buffer
        if b.complete_state:
            b.complete_next()
        else:
            b.start_completion()

    @kb.add("s-tab")
    def _(event):
        b = event.app.current_buffer
        if b.complete_state:
            b.complete_previous()

    def get_footer_html():
        status = format_agent_status(agent)
        padding = cols - (len(status) + 3)
        return HTML(f"<ansidim>{status}</ansidim>{' ' * max(0, padding)}<b><ansicyan>MO</ansicyan></b>")

    sep = terminal_separator(cols)
    # Use the canonical prompt glyph so the plain path matches the full TUI
    # (was a hardcoded ">" — inconsistent brand between the two input paths).
    from .layout import PlaceholderProcessor, prompt_prefix
    prefix = prompt_prefix()

    body = HSplit([
        Window(height=1, content=FormattedTextControl(HTML(f"<ansidim>{sep}</ansidim>")), dont_extend_height=True),
        Window(height=1, content=BufferControl(buffer=buf, input_processors=[BeforeInput(prefix), PlaceholderProcessor()]), dont_extend_height=True),
        Window(height=1, content=FormattedTextControl(get_footer_html), dont_extend_height=True),
    ])

    root = FloatContainer(
        content=body,
        floats=[Float(xcursor=True, ycursor=True, content=CompletionsMenu(max_height=10))],
    )

    app = Application(layout=Layout(root, focused_element=buf), key_bindings=kb, erase_when_done=True)
    text = app.run()

    if text is _STOP_INPUT:
        raise EOFError

    print(f"\033[2m>\033[0m {text}")

    return text


def drain_queued_inputs(input_queue: queue.Queue) -> list[str | object]:
    values = []
    while True:
        try:
            value = input_queue.get_nowait()
        except queue.Empty:
            break
        if value is _STOP_INPUT:
            values.append(_STOP_INPUT)
        else:
            text = str(value).strip()
            if text:
                values.append(text)
    return values


def live_key_worker(input_queue: queue.Queue, stop_event: threading.Event, buffer: list[str] | None = None) -> None:
    if buffer is None:
        buffer = []
    try:
        with create_input() as inp:
            while not stop_event.is_set():
                key_presses = inp.read_keys()
                for key_press in key_presses:
                    data = key_press.data
                    if data in ("\x03", "\x04"):
                        input_queue.put(_STOP_INPUT)
                        stop_event.set()
                        return
                    if data in ("\r", "\n"):
                        text = "".join(buffer).strip()
                        buffer.clear()
                        if text:
                            input_queue.put(text)
                        continue
                    if data in ("\x7f", "\b"):
                        if buffer:
                            buffer.pop()
                        continue
                    if data and data.isprintable():
                        buffer.append(data)
    except Exception:
        return

