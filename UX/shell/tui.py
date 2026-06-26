"""Prompt-toolkit fullscreen TUI for the isolated UX surface."""
from __future__ import annotations

import threading

from prompt_toolkit.application import Application, get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.layout.processors import BeforeInput, Processor, Transformation
from prompt_toolkit.styles import Style

from UX.state.controller import UxController
from UX.state.models import TranscriptItem

Fragment = tuple[str, str]
ANIMATION_INTERVAL_SECONDS = 0.10
SIGNAL_FIELD_HEIGHT = 9
SIGNAL_FIELD_MAX_WIDTH = 86
SIGNAL_FIELD_MIN_WIDTH = 52
SPINNER_FRAMES: tuple[str, ...] = ("◜", "◠", "◝", "◞", "◡", "◟")

LOGO_LINES: tuple[str, ...] = (
    "        M         M      OOOOOOO       ",
    "        MM       MM     OO     OO      ",
    "        M M     M M     OO     OO      ",
    "        M  M   M  M     OO     OO      ",
    "        M   M M   M     OO     OO      ",
    "        M    M    M      OOOOOOO       ",
)


class TuiAnimation:
    def __init__(self) -> None:
        self.frame = 0

    def advance(self) -> None:
        self.frame += 1


class PlaceholderProcessor(Processor):
    def __init__(self, text: str) -> None:
        self.text = text

    def apply_transformation(self, transformation_input):
        buffer = transformation_input.buffer_control.buffer
        if buffer.text == "" and transformation_input.lineno == 0:
            return Transformation([("class:placeholder", self.text)])
        return Transformation(transformation_input.fragments)


def _terminal_width(default: int = 120) -> int:
    try:
        return max(60, int(get_app().output.get_size().columns or default))
    except Exception:
        return default


def _center_text(text: str, width: int) -> str:
    clean = str(text or "")
    if len(clean) >= width:
        return clean[:width]
    return " " * max(0, (width - len(clean)) // 2) + clean


def _center_field(text: str, width: int) -> str:
    clean = str(text or "")
    if len(clean) >= width:
        return clean[:width]
    left = max(0, (width - len(clean)) // 2)
    right = max(0, width - len(clean) - left)
    return " " * left + clean + " " * right


def _trim(value: str, limit: int) -> str:
    text = str(value or "").replace("\r", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _centered_lines(lines: tuple[str, ...], width: int, style: str) -> list[Fragment]:
    fragments: list[Fragment] = []
    for line in lines:
        fragments.append((style, _center_text(line, width)))
        fragments.append(("", "\n"))
    return fragments


def _spinner(frame: int) -> str:
    return SPINNER_FRAMES[frame % len(SPINNER_FRAMES)]


def _signal_field_width(width: int) -> int:
    return min(SIGNAL_FIELD_MAX_WIDTH, max(SIGNAL_FIELD_MIN_WIDTH, width - 18))


def _signal_cell(row: int, column: int, frame: int, field_width: int) -> Fragment:
    center = field_width // 2
    left_sweep = (frame * 3 + row * 4) % field_width
    right_sweep = (field_width - 1 - ((frame * 2 + row * 5) % field_width))
    pulse = (column * 7 + row * 11 + frame * 5) % 41
    wake = abs(column - left_sweep) + abs(row - (frame + column // 6) % SIGNAL_FIELD_HEIGHT)

    if row == SIGNAL_FIELD_HEIGHT // 2 and abs(column - center) <= 2:
        return ("class:signal-core", "█")
    if column in {left_sweep, right_sweep}:
        return ("class:signal-hot", "█")
    if wake <= 1:
        return ("class:signal-hot", "▓")
    if pulse in {0, 1}:
        return ("class:signal-mid", "◆")
    if pulse in {2, 3, 4, 5}:
        return ("class:signal-dim", "·")
    if abs(column - center) <= 9 and abs(row - SIGNAL_FIELD_HEIGHT // 2) <= 2:
        return ("class:signal-mid", "▒")
    return ("class:signal-faint", " ")


def _signal_field_fragments(frame: int, width: int) -> list[Fragment]:
    field_width = _signal_field_width(width)
    left = " " * max(0, (width - field_width) // 2)
    fragments: list[Fragment] = []
    for row in range(SIGNAL_FIELD_HEIGHT):
        fragments.append(("", left))
        for column in range(field_width):
            fragments.append(_signal_cell(row, column, frame, field_width))
        fragments.append(("", "\n"))
    return fragments


def _hero_fragments(controller: UxController, animation: TuiAnimation | None = None) -> list[Fragment]:
    snapshot = controller.snapshot()
    width = _terminal_width()
    frame = animation.frame if animation else 0
    fragments: list[Fragment] = [("", "\n")]
    fragments.extend(_signal_field_fragments(frame, width))
    fragments.extend(_centered_lines(LOGO_LINES, width, "class:logo"))
    fragments.append(("", "\n"))
    title = f"{_spinner(frame)}  MO UX  {snapshot.model_label}  {_spinner(frame + 3)}"
    hints = "/help   |   /models   |   Shift+Tab plan mode   |   @file context"
    box_width = min(width - 10, max(72, len(hints) + 8))
    left = " " * max(0, (width - box_width) // 2)
    fragments.extend(
        [
            ("class:border", f"{left}╭{'─' * (box_width - 2)}╮\n"),
            ("class:border", f"{left}│"),
            ("class:title", _center_field(title, box_width - 2)),
            ("class:border", "│\n"),
            ("class:border", f"{left}│"),
            ("class:hint", _center_field(hints, box_width - 2)),
            ("class:border", "│\n"),
            ("class:border", f"{left}╰{'─' * (box_width - 2)}╯\n"),
        ]
    )
    return fragments


def _transcript_fragments(items: tuple[TranscriptItem, ...]) -> list[Fragment]:
    if not items:
        return []
    width = _terminal_width()
    margin = " " * 7
    max_text = max(40, width - 14)
    fragments: list[Fragment] = [("", "\n")]
    for item in items[-8:]:
        speaker = item.speaker.strip().lower() or "system"
        style = "class:mo" if speaker in {"mo", "assistant"} else "class:user"
        label = "MO" if speaker in {"mo", "assistant"} else "USER"
        fragments.extend(
            [
                ("", margin),
                (style, label),
                ("", "  "),
                ("class:text", _trim(item.text, max_text)),
                ("", "\n"),
            ]
        )
    return fragments


def _main_fragments(controller: UxController, animation: TuiAnimation | None = None) -> list[Fragment]:
    snapshot = controller.snapshot()
    if snapshot.transcript:
        return _transcript_fragments(snapshot.transcript)
    return _hero_fragments(controller, animation)


def _mode_line_fragments(controller: UxController, animation: TuiAnimation | None = None) -> list[Fragment]:
    snapshot = controller.snapshot()
    width = _terminal_width()
    frame = animation.frame if animation else 0
    state = "Busy" if snapshot.busy else "Normal"
    left = f" > {_spinner(frame)} {state} (Shift+Tab)"
    rule = "─" * max(1, width - len(left) - 2)
    return [("class:blue", left), ("class:rule", f" {rule}")]


def _status_fragments(controller: UxController, animation: TuiAnimation | None = None) -> list[Fragment]:
    snapshot = controller.snapshot()
    frame = animation.frame if animation else 0
    model = snapshot.model or snapshot.provider or "model not configured"
    thinking = "High" if snapshot.lanes else "Ready"
    context = "100.0%" if not snapshot.busy else "working"
    return [
        ("class:brand", f" {_spinner(frame + 1)} {model} "),
        ("class:muted", "|"),
        ("class:muted", " Autonomy: "),
        ("class:amber", "Manual "),
        ("class:muted", "(Ctrl+Shift+A) "),
        ("class:muted", "|"),
        ("class:muted", " Thinking: "),
        ("class:yellow", f"{thinking} "),
        ("class:muted", "(Ctrl+Shift+T) "),
        ("class:muted", "|"),
        ("class:muted", " Context left "),
        ("class:green", context),
    ]


def _build_root(controller: UxController, input_buffer: Buffer, animation: TuiAnimation) -> HSplit:
    return HSplit(
        [
            Window(content=FormattedTextControl(lambda: _main_fragments(controller, animation)), wrap_lines=False),
            Window(
                height=1,
                content=FormattedTextControl(lambda: _mode_line_fragments(controller, animation)),
                dont_extend_height=True,
            ),
            Window(
                height=1,
                content=BufferControl(
                    buffer=input_buffer,
                    input_processors=[
                        BeforeInput(HTML("<prompt>&gt;</prompt> ")),
                        PlaceholderProcessor("Type a message..."),
                    ],
                ),
                dont_extend_height=True,
                wrap_lines=False,
            ),
            Window(
                height=1,
                content=FormattedTextControl(lambda: _status_fragments(controller, animation)),
                dont_extend_height=True,
            ),
        ]
    )


def _style() -> Style:
    return Style.from_dict(
        {
            "logo": "#8ccfff bold",
            "signal-faint": "#06151d",
            "signal-dim": "#0a5d74",
            "signal-mid": "#1b8aa6",
            "signal-hot": "#8ccfff bold",
            "signal-core": "#f6ad55 bold",
            "border": "#255f9f",
            "title": "#42a5ff bold",
            "hint": "#2f8cff bold",
            "rule": "#7aa2ff",
            "prompt": "#39d0c8 bold",
            "placeholder": "#7d8996",
            "brand": "#26c6ff bold",
            "blue": "#7aa2ff bold",
            "green": "#8ee88e bold",
            "amber": "#f6ad55 bold",
            "yellow": "#ffe45c bold",
            "red": "#fc8181 bold",
            "mo": "#39d0c8 bold",
            "user": "#7aa2ff bold",
            "text": "#d7dee8",
            "muted": "#7d8996",
        }
    )


def run_tui(controller: UxController) -> None:
    kb = KeyBindings()

    @kb.add("c-q")
    @kb.add("c-c")
    def _exit(event) -> None:
        event.app.exit()

    animation = TuiAnimation()
    app_ref: dict[str, Application] = {}

    def _invalidate() -> None:
        app = app_ref.get("app")
        if app is not None:
            app.invalidate()

    def _accept(buffer: Buffer) -> bool:
        text = buffer.text.strip()
        buffer.reset()
        if not text:
            _invalidate()
            return True
        controller.handle_input(text, on_change=_invalidate)
        if controller.exit_requested:
            get_app().exit()
        _invalidate()
        return True

    input_buffer = Buffer(multiline=False, accept_handler=_accept)
    app = Application(
        layout=Layout(_build_root(controller, input_buffer, animation), focused_element=input_buffer),
        key_bindings=kb,
        full_screen=True,
        mouse_support=False,
        paste_mode=True,
        refresh_interval=ANIMATION_INTERVAL_SECONDS,
        style=_style(),
    )
    app_ref["app"] = app
    stop_animation = threading.Event()

    def _animate() -> None:
        while not stop_animation.wait(ANIMATION_INTERVAL_SECONDS):
            animation.advance()
            if hasattr(app, "invalidate"):
                app.invalidate()

    thread = threading.Thread(target=_animate, daemon=True)
    thread.start()
    try:
        app.run()
    finally:
        stop_animation.set()
        thread.join(timeout=1.0)
