from __future__ import annotations

import os
import queue
import threading
from typing import Any

from .activity import footer_fragments, footer_left_fragments, workspace_active_fragments
from .command_registry import (
    slash_aliases,
    slash_command_description,
    slash_command_with_desc,
    subcommands_for,
)

_STOP_INPUT = object()

try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.completion import Completer, Completion, PathCompleter
    from prompt_toolkit.input import create_input
    from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
    from prompt_toolkit.key_binding.defaults import load_key_bindings
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


def submitted_prompt_echo(text: str) -> str:
    from .layout import PROMPT_GLYPH

    return f"\033[2m{PROMPT_GLYPH}\033[0m {text}"


def prompt_enhance_replacement(agent: Any, text: str) -> str:
    """Return the local Ctrl+E replacement text for the plain input surface."""
    original = str(text or "")
    stripped = original.strip()
    if not stripped or stripped.startswith("/"):
        return ""
    try:
        fn = getattr(agent, "enhance_prompt_local", None)
        if callable(fn):
            enhanced = str(fn(original) or "").strip()
        else:
            from core.context.prompt_enhancer import enhance_prompt

            enhanced = str(enhance_prompt(original, getattr(agent, "profile", None)) or "").strip()
    except Exception:
        return ""
    return enhanced if enhanced and enhanced != stripped else ""


def apply_plain_prompt_enhance(agent: Any, buffer: Any) -> str:
    """Apply Ctrl+E enhancement to a prompt-toolkit buffer; return original text."""
    original = str(getattr(buffer, "text", "") or "")
    replacement = prompt_enhance_replacement(agent, original)
    if not replacement:
        return ""
    buffer.text = replacement
    buffer.cursor_position = len(replacement)
    return original


def build_plain_input_key_bindings(agent: Any, buf: Any):
    if not HAS_PROMPT_TOOLKIT:
        raise RuntimeError("prompt_toolkit is not available")
    custom_kb = KeyBindings()
    pre_enhance_text = ""
    enhance_holder_active = False

    def clear_enhance_holder() -> None:
        nonlocal pre_enhance_text, enhance_holder_active
        pre_enhance_text = ""
        enhance_holder_active = False

    @custom_kb.add("enter")
    def _(event):
        b = event.app.current_buffer
        if b.complete_state:
            # Insert selected completion, don't submit
            b.apply_completion(b.complete_state.current_completion)
            b.cancel_completion()
        else:
            clear_enhance_holder()
            event.app.exit(result=buf.text)

    @custom_kb.add("c-c")
    def _(event):
        event.app.exit(result=_STOP_INPUT)

    @custom_kb.add("c-d")
    def _(event):
        event.app.exit(result=_STOP_INPUT)

    @custom_kb.add("c-l")
    def _(event):
        """Ctrl+L: redraw the terminal screen (convention from readline/shell)."""
        event.app.renderer.clear()
        event.app.invalidate()

    @custom_kb.add("c-e", eager=True)
    def _(event):
        """Ctrl+E: replace the typed message with the fast local enhancement."""
        nonlocal pre_enhance_text, enhance_holder_active
        b = event.app.current_buffer
        if b is not buf:
            return
        original = apply_plain_prompt_enhance(agent, b)
        if not original:
            return
        pre_enhance_text = original
        enhance_holder_active = True
        event.app.invalidate()

    @custom_kb.add("tab")
    def _(event):
        b = event.app.current_buffer
        if b.complete_state:
            b.complete_next()
        else:
            b.start_completion()

    @custom_kb.add("s-tab")
    def _(event):
        b = event.app.current_buffer
        if b.complete_state:
            b.complete_previous()

    @custom_kb.add("escape")
    def _(event):
        nonlocal pre_enhance_text, enhance_holder_active
        b = event.app.current_buffer
        if enhance_holder_active:
            restore = pre_enhance_text
            clear_enhance_holder()
            buf.text = restore
            buf.cursor_position = len(restore)
            event.app.invalidate()
        elif b.complete_state:
            b.cancel_completion()

    return custom_kb


class SlashAndPathCompleter(Completer):
    def __init__(self, agent: Any = None):
        self.agent = agent
        self.path_completer = PathCompleter(expanduser=True)

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if text.startswith("/"):
            if " " in text:
                _cmd, arg_prefix = text.split(" ", 1)
                subs = subcommands_for(text, agent=self.agent)
                current = arg_prefix.strip()
                replace_len = len(arg_prefix)
                for sub, desc in subs:
                    if sub.startswith(current):
                        prefix, required, _tail = sub.partition("<")
                        value = prefix.rstrip() + " " if required else sub
                        yield Completion(value, start_position=-replace_len, display=sub, display_meta=desc)
                if not subs:
                    yield from self.path_completer.get_completions(document, complete_event)
                return
            for cmd, desc in slash_command_with_desc():
                if cmd.startswith(text):
                    yield Completion(cmd, start_position=-len(text), display_meta=desc)
            for alias, target in slash_aliases().items():
                if alias.startswith(text) and alias != text:
                    desc = slash_command_description(target)
                    yield Completion(alias, start_position=-len(text), display_meta=f"→ {target}" if not desc else desc)
            return
        yield from self.path_completer.get_completions(document, complete_event)


def prompt_toolkit_input(agent: Any) -> str:
    if not HAS_PROMPT_TOOLKIT:
        raise RuntimeError("prompt_toolkit is not available")
    cols = terminal_columns()

    buf = Buffer(completer=SlashAndPathCompleter(agent), complete_while_typing=False)
    custom_kb = build_plain_input_key_bindings(agent, buf)

    kb = merge_key_bindings([load_key_bindings(), custom_kb])

    def get_footer_fragments():
        left_fragments = footer_left_fragments(agent)
        worker_fragments = workspace_active_fragments(agent)
        if worker_fragments:
            return footer_fragments(
                left_fragments,
                columns=max(20, cols),
                right_fragments=worker_fragments,
            )
        return footer_fragments(
            left_fragments,
            columns=max(20, cols),
            right="MO",
            right_style="class:mo-marker",
        )

    sep = terminal_separator(cols)
    # Use the canonical prompt glyph so the plain path matches the full TUI
    # (was a hardcoded ">" — inconsistent brand between the two input paths).
    from .layout import (
        APP_BG_STYLE,
        BG_FILL_CHAR,
        INPUT_BG_STYLE,
        PlaceholderProcessor,
        prompt_prefix,
    )
    prefix = prompt_prefix()

    body = HSplit([
        Window(
            height=1,
            content=FormattedTextControl([("class:separator", sep)]),
            dont_extend_height=True,
            style=APP_BG_STYLE,
            char=BG_FILL_CHAR,
        ),
        Window(
            height=1,
            content=BufferControl(
                buffer=buf,
                input_processors=[BeforeInput(prefix), PlaceholderProcessor()],
            ),
            dont_extend_height=True,
            style=INPUT_BG_STYLE,
            char=BG_FILL_CHAR,
        ),
        Window(
            height=1,
            content=FormattedTextControl(get_footer_fragments),
            dont_extend_height=True,
            style=APP_BG_STYLE,
            char=BG_FILL_CHAR,
        ),
    ], style=APP_BG_STYLE)

    root = FloatContainer(
        content=body,
        floats=[Float(xcursor=True, ycursor=True, content=CompletionsMenu(max_height=10))],
        style=APP_BG_STYLE,
    )

    from .theme import build_tui_color_depth, build_tui_style

    app = Application(
        layout=Layout(root, focused_element=buf),
        key_bindings=kb,
        erase_when_done=True,
        style=build_tui_style(),
        color_depth=build_tui_color_depth(),
    )
    text = app.run()

    if text is _STOP_INPUT:
        raise EOFError

    print(submitted_prompt_echo(text))

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

