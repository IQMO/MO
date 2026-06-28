"""Prompt-toolkit fullscreen TUI for the isolated UX surface."""
from __future__ import annotations

from dataclasses import dataclass
import threading
import textwrap

from prompt_toolkit.application import Application, get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from UX.runtime.adapters import build_key_bindings
from prompt_toolkit.layout.containers import ConditionalContainer, HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.layout.processors import BeforeInput, Processor, Transformation
from prompt_toolkit.styles import Style

from UX.state.controller import UxController
from UX.state.models import BoardRow, LaneSnapshot, TranscriptItem

Fragment = tuple[str, str]
ANIMATION_INTERVAL_SECONDS = 0.10
SIGNAL_FIELD_HEIGHT = 9
SIGNAL_FIELD_MAX_WIDTH = 86
SIGNAL_FIELD_MIN_WIDTH = 52
SPINNER_FRAMES: tuple[str, ...] = ("◜", "◠", "◝", "◞", "◡", "◟")
COMMAND_HINTS: tuple[tuple[str, str], ...] = (
    ("/help", "show available commands"),
    ("/status", "show UX/runtime status"),
    ("/model", "show model/provider status"),
    ("/exit", "close this UX"),
    ("@path", "attach path text to the next prompt"),
)

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


@dataclass
class TuiSessionState:
    turn_running: bool = False
    notice: str = ""
    palette_open: bool = False
    plan_lens: bool = False


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


def _one_line(value: str) -> str:
    return " ".join(str(value or "").replace("\r", " ").split())


def _wrap_lines(value: str, width: int) -> list[str]:
    text = _one_line(value)
    if not text:
        return [""]
    return textwrap.wrap(text, width=max(16, width), break_long_words=True, break_on_hyphens=False) or [text[:width]]


def _status_style(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"completed", "done", "ready"}:
        return "class:green"
    if normalized in {"active", "running", "busy"}:
        return "class:amber"
    if normalized == "blocked":
        return "class:red"
    return "class:muted"


def _status_marker(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"completed", "done"}:
        return "x"
    if normalized in {"active", "running"}:
        return ">"
    if normalized == "blocked":
        return "!"
    return "."


def _centered_lines(lines: tuple[str, ...], width: int, style: str) -> list[Fragment]:
    fragments: list[Fragment] = []
    for line in lines:
        fragments.append((style, _center_text(line, width)))
        fragments.append(("", "\n"))
    return fragments


def _spinner(frame: int) -> str:
    return SPINNER_FRAMES[frame % len(SPINNER_FRAMES)]


def _activity_glyph(active: bool, frame: int) -> str:
    return _spinner(frame) if active else " "


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
    title = f"MO UX  {snapshot.model_label}"
    hints = "/help   |   /model   |   Shift+Tab plan mode   |   @file context"
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


def _lane_lines(lanes: tuple[LaneSnapshot, ...], width: int) -> list[list[Fragment]]:
    lines: list[list[Fragment]] = [[("class:section", "AGENTS")]]
    if not lanes:
        lines.append([("class:muted", "no runtime lanes reported")])
        return lines
    for lane in lanes[:4]:
        status_style = _status_style(lane.status)
        label = _trim(lane.name.upper(), 11).ljust(11)
        detail = _trim(lane.detail or lane.model or lane.status, max(18, width - 24))
        lines.append(
            [
                (status_style, _status_marker(lane.status)),
                ("", " "),
                ("class:text", label),
                ("", " "),
                (status_style, _trim(lane.status, 9).ljust(9)),
                ("class:muted", detail),
            ]
        )
    return lines


def _board_lines(rows: tuple[BoardRow, ...], width: int) -> list[list[Fragment]]:
    lines: list[list[Fragment]] = [[("class:section", "TASKS")]]
    if not rows:
        lines.append([("class:muted", "idle - no taskboard from runtime")])
        return lines
    for row in rows[:6]:
        status_style = _status_style(row.status)
        detail = row.blocker if row.status == "blocked" and row.blocker else row.kind
        title_limit = max(18, width - 12 - (len(detail) if detail else 0))
        line: list[Fragment] = [
            (status_style, _status_marker(row.status)),
            ("", " "),
            (status_style if row.status == "active" else "class:text", _trim(row.title, title_limit)),
        ]
        if detail:
            line.extend([("class:muted", "  "), ("class:muted", _trim(detail, 18))])
        lines.append(line)
    if len(rows) > 6:
        lines.append([("class:muted", f"+{len(rows) - 6} more")])
    return lines


def _plain_width(line: list[Fragment]) -> int:
    return sum(len(text) for _style, text in line)


def _rail_fragments(lanes: tuple[LaneSnapshot, ...], rows: tuple[BoardRow, ...], width: int) -> list[Fragment]:
    gap = 4
    column_width = max(28, (width - gap - 8) // 2)
    left_lines = _lane_lines(lanes, column_width)
    right_lines = _board_lines(rows, column_width)
    total = max(len(left_lines), len(right_lines))
    fragments: list[Fragment] = [("", "\n"), ("class:rule", " " * 3 + "─" * max(8, width - 6)), ("", "\n")]
    for index in range(total):
        left = left_lines[index] if index < len(left_lines) else []
        right = right_lines[index] if index < len(right_lines) else []
        fragments.append(("", "   "))
        fragments.extend(left)
        fragments.append(("", " " * max(gap, column_width - _plain_width(left) + gap)))
        fragments.extend(right)
        fragments.append(("", "\n"))
    fragments.append(("class:rule", " " * 3 + "─" * max(8, width - 6)))
    fragments.append(("", "\n"))
    return fragments


def _transcript_fragments(items: tuple[TranscriptItem, ...], *, limit: int = 10) -> list[Fragment]:
    if not items:
        return []
    width = _terminal_width()
    margin = " " * 5
    label_width = 7
    max_text = max(40, width - len(margin) - label_width - 4)
    fragments: list[Fragment] = [("", "\n"), ("class:section", f"{margin}TRANSCRIPT"), ("", "\n")]
    for item in items[-limit:]:
        speaker = item.speaker.strip().lower() or "system"
        if speaker in {"mo", "assistant"}:
            style = "class:mo"
            label = "MO"
        elif speaker in {"ux", "system"}:
            style = "class:ux"
            label = "UX"
        else:
            style = "class:user"
            label = "USER"
        wrapped = _wrap_lines(item.text, max_text)
        for index, line in enumerate(wrapped):
            fragments.append(("", margin))
            fragments.append((style, label.ljust(label_width) if index == 0 else " " * label_width))
            fragments.append(("class:text", line))
            fragments.append(("", "\n"))
    return fragments


def _stream_fragments(controller: UxController) -> list[Fragment]:
    chunks = controller.callbacks.assistant_chunks[-2:]
    if not chunks:
        return []
    width = _terminal_width()
    max_text = max(40, width - 14)
    fragments: list[Fragment] = [("", "\n"), ("class:section", "     STREAM"), ("", "\n")]
    for line in _wrap_lines(" ".join(chunks), max_text):
        fragments.extend([("", "     "), ("class:mo", "MO      "), ("class:text", line), ("", "\n")])
    return fragments


def _top_bar_fragments(controller: UxController, animation: TuiAnimation | None, ui_state: TuiSessionState) -> list[Fragment]:
    snapshot = controller.snapshot()
    width = _terminal_width()
    frame = animation.frame if animation else 0
    mode = "PLAN LENS" if ui_state.plan_lens else str(getattr(controller.backend, "name", "ux")).upper()
    active = snapshot.busy or ui_state.turn_running
    state = "BUSY" if active else "READY"
    project = _trim(snapshot.project or "project not set", max(18, width // 3))
    model = _trim(snapshot.model_label, max(20, width // 3))
    left = f" {_activity_glyph(active, frame)} MO  {project}"
    right = f"{mode}  {model}  {state} "
    middle = " " * max(1, width - len(left) - len(right))
    return [
        ("class:brand", left),
        ("", middle),
        ("class:green" if state == "READY" else "class:amber", right),
        ("", "\n"),
        ("class:rule", "─" * width),
        ("", "\n"),
    ]


def _work_fragments(controller: UxController, animation: TuiAnimation | None, ui_state: TuiSessionState) -> list[Fragment]:
    snapshot = controller.snapshot()
    width = _terminal_width()
    fragments = _top_bar_fragments(controller, animation, ui_state)
    if ui_state.plan_lens or snapshot.board or snapshot.lanes or snapshot.busy:
        fragments.extend(_rail_fragments(snapshot.lanes, snapshot.board, width))
    fragments.extend(_transcript_fragments(snapshot.transcript))
    fragments.extend(_stream_fragments(controller))
    return fragments


def _main_fragments(
    controller: UxController,
    animation: TuiAnimation | None = None,
    ui_state: TuiSessionState | None = None,
) -> list[Fragment]:
    snapshot = controller.snapshot()
    current_ui = ui_state or TuiSessionState()
    if snapshot.transcript or snapshot.board or snapshot.lanes or snapshot.busy or current_ui.plan_lens:
        return _work_fragments(controller, animation, current_ui)
    return _hero_fragments(controller, animation)


def _mode_line_fragments(
    controller: UxController,
    animation: TuiAnimation | None = None,
    ui_state: TuiSessionState | None = None,
) -> list[Fragment]:
    snapshot = controller.snapshot()
    width = _terminal_width()
    frame = animation.frame if animation else 0
    current_ui = ui_state or TuiSessionState()
    active = snapshot.busy or current_ui.turn_running
    state = "Busy" if active else "Normal"
    lens = "plan lens" if current_ui.plan_lens else str(getattr(controller.backend, "name", "ux"))
    marker = f"{_spinner(frame)} " if active else ""
    left = f" > {marker}{state} / {lens} / Ctrl+P commands / Shift+Tab lens"
    rule = "─" * max(1, width - len(left) - 2)
    return [("class:blue", left), ("class:rule", f" {rule}")]


def _status_fragments(
    controller: UxController,
    animation: TuiAnimation | None = None,
    ui_state: TuiSessionState | None = None,
) -> list[Fragment]:
    snapshot = controller.snapshot()
    frame = animation.frame if animation else 0
    current_ui = ui_state or TuiSessionState()
    model = snapshot.model or snapshot.provider or "model not configured"
    lane_state = "reported" if snapshot.lanes else "quiet"
    active = snapshot.busy or current_ui.turn_running
    context = "working" if active else "idle"
    activity = controller.callbacks.activity or current_ui.notice or snapshot.notice or "ready"
    brand = f" {_spinner(frame + 1)} {model} " if active else f" {model} "
    return [
        ("class:brand", brand),
        ("class:muted", "|"),
        ("class:muted", " Mode: "),
        ("class:amber" if current_ui.plan_lens else "class:blue", "Plan lens " if current_ui.plan_lens else f"{getattr(controller.backend, 'name', 'ux')} "),
        ("class:muted", "|"),
        ("class:muted", " Lanes: "),
        ("class:yellow", f"{lane_state} "),
        ("class:muted", "|"),
        ("class:muted", " Runtime "),
        ("class:green", context),
        ("class:muted", " | "),
        ("class:muted", _trim(activity, 44)),
    ]


def _command_palette_fragments(input_text: str, ui_state: TuiSessionState) -> list[Fragment]:
    query = str(input_text or "").strip().lower()
    width = _terminal_width()
    matches = [item for item in COMMAND_HINTS if not query or query in item[0].lower() or query in item[1].lower()]
    if not matches:
        matches = COMMAND_HINTS[:3]
    box_width = min(width - 4, 78)
    left = " " * max(0, (width - box_width) // 2)
    fragments: list[Fragment] = [
        ("class:border", f"{left}╭{'─' * (box_width - 2)}╮\n"),
        ("class:border", f"{left}│"),
        ("class:title", _center_field("Command Palette", box_width - 2)),
        ("class:border", "│\n"),
    ]
    for command, description in matches[:5]:
        row = f" {command:<12} {description}"
        fragments.extend(
            [
                ("class:border", f"{left}│"),
                ("class:hint", _trim(row, box_width - 4).ljust(box_width - 2)),
                ("class:border", "│\n"),
            ]
        )
    fragments.extend([("class:border", f"{left}╰{'─' * (box_width - 2)}╯\n")])
    return fragments


def _composer_hint_fragments(input_text: str, ui_state: TuiSessionState) -> list[Fragment]:
    chips = [part for part in str(input_text or "").split() if part.startswith("@") and len(part) > 1]
    fragments: list[Fragment] = []
    if ui_state.notice:
        fragments.extend([("class:amber", f" {_trim(ui_state.notice, 96)}"), ("", "\n")])
    if chips:
        fragments.append(("class:muted", " context "))
        for chip in chips[:4]:
            trimmed = _trim(chip, 24)
            if trimmed:
                fragments.append(("class:chip", f" {trimmed} "))
        fragments.append(("", "\n"))
    return fragments


def _palette_visible(input_text: str, ui_state: TuiSessionState) -> bool:
    query = str(input_text or "").strip()
    return ui_state.palette_open or query.startswith(("/", "@"))


def _composer_hint_visible(input_text: str, ui_state: TuiSessionState) -> bool:
    return bool(ui_state.notice or [part for part in str(input_text or "").split() if part.startswith("@") and len(part) > 1])


def _build_root(controller: UxController, input_buffer: Buffer, animation: TuiAnimation, ui_state: TuiSessionState) -> HSplit:
    return HSplit(
        [
            Window(content=FormattedTextControl(lambda: _main_fragments(controller, animation, ui_state)), wrap_lines=False),
            ConditionalContainer(
                Window(
                    height=6,
                    content=FormattedTextControl(lambda: _command_palette_fragments(input_buffer.text, ui_state)),
                    dont_extend_height=True,
                ),
                filter=Condition(lambda: _palette_visible(input_buffer.text, ui_state)),
            ),
            ConditionalContainer(
                Window(
                    height=2,
                    content=FormattedTextControl(lambda: _composer_hint_fragments(input_buffer.text, ui_state)),
                    dont_extend_height=True,
                ),
                filter=Condition(lambda: _composer_hint_visible(input_buffer.text, ui_state)),
            ),
            Window(
                height=1,
                content=FormattedTextControl(lambda: _mode_line_fragments(controller, animation, ui_state)),
                dont_extend_height=True,
            ),
            Window(
                height=3,
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
                content=FormattedTextControl(lambda: _status_fragments(controller, animation, ui_state)),
                dont_extend_height=True,
            ),
        ]
    )


def _style() -> Style:
    from UX.runtime.adapters import get_shell_style, get_skin

    s = get_skin()
    base = get_shell_style()
    # Extra entries not covered by the generic bridge
    base.update({
        "ux": f"{s.accent_amber} bold",
        "user": f"{s.accent_blue} bold",
        "text": s.text_primary,
        "muted": s.text_muted,
    })
    return Style.from_dict(base)


def _create_input_buffer(accept_handler, ui_state: TuiSessionState) -> Buffer:
    return Buffer(
        multiline=True,
        accept_handler=accept_handler,
        history=InMemoryHistory(),
        enable_history_search=True,
        read_only=Condition(lambda: ui_state.turn_running),
    )


def _submit_in_background(
    controller: UxController,
    ui_state: TuiSessionState,
    text: str,
    *,
    invalidate,
    exit_app,
) -> threading.Thread | None:
    clean = str(text or "").strip()
    if not clean:
        invalidate()
        return None
    if ui_state.turn_running:
        ui_state.notice = "Turn running"
        invalidate()
        return None

    ui_state.turn_running = True
    ui_state.notice = "Turn running"
    invalidate()

    def _worker() -> None:
        try:
            controller.handle_input(clean, on_change=invalidate)
            if controller.exit_requested:
                exit_app()
        except Exception:
            ui_state.notice = "UX submit failed"
        finally:
            ui_state.turn_running = False
            if not controller.exit_requested:
                ui_state.notice = ""
            invalidate()

    thread = threading.Thread(target=_worker, name="mo-ux-submit", daemon=True)
    thread.start()
    return thread


def run_tui(controller: UxController) -> None:
    ui_state = TuiSessionState()
    kb = build_key_bindings(ui_state)

    animation = TuiAnimation()
    app_ref: dict[str, Application] = {}

    def _invalidate() -> None:
        app = app_ref.get("app")
        if app is not None:
            app.invalidate()

    def _accept(buffer: Buffer) -> bool:
        text = buffer.text.strip()
        if ui_state.turn_running:
            ui_state.notice = "Turn running"
            _invalidate()
            return True
        if not text:
            _invalidate()
            return True
        buffer.reset()
        ui_state.palette_open = False
        _submit_in_background(controller, ui_state, text, invalidate=_invalidate, exit_app=lambda: get_app().exit())
        return True

    input_buffer = _create_input_buffer(_accept, ui_state)
    app = Application(
        layout=Layout(_build_root(controller, input_buffer, animation, ui_state), focused_element=input_buffer),
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
