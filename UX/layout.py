"""Rich renderers for the isolated UX surface."""
from __future__ import annotations

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .models import BoardRow, LaneSnapshot, SessionSnapshot, TranscriptItem
from .theme import DEFAULT_THEME, LANE_STYLE, STATUS_STYLE, UxTheme

STATUS_MARKERS = {
    "completed": "[x]",
    "active": ">",
    "blocked": "!",
    "pending": "[ ]",
}


def _style(theme: UxTheme, token: str) -> str:
    return getattr(theme, token, theme.text)


def _trim(value: str, limit: int) -> str:
    text = str(value or "").replace("\r", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _panel(renderable: object, title: str, theme: UxTheme) -> Panel:
    return Panel(
        renderable,
        title=title,
        title_align="left",
        border_style=_style(theme, "border"),
        padding=(0, 1),
        box=box.SQUARE,
    )


def header(snapshot: SessionSnapshot, theme: UxTheme = DEFAULT_THEME) -> Panel:
    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    grid.add_column(justify="right", ratio=1)

    left = Text()
    left.append("MO", style=f"bold {_style(theme, 'brand')}")
    left.append("  ")
    left.append(snapshot.project or "project not set", style=_style(theme, "text"))
    left.append("\n")
    left.append(snapshot.model_label, style=_style(theme, "muted"))

    right = Text()
    right.append("BUSY" if snapshot.busy else "READY", style=_style(theme, "amber" if snapshot.busy else "green"))
    if snapshot.runtime:
        right.append("\n")
        right.append(_trim(snapshot.runtime, 72), style=_style(theme, "muted"))

    grid.add_row(left, right)
    return _panel(grid, "Session", theme)


def lanes_panel(lanes: tuple[LaneSnapshot, ...], theme: UxTheme = DEFAULT_THEME) -> Panel:
    table = Table.grid(expand=True)
    table.add_column(ratio=1)
    table.add_column(ratio=1)
    table.add_column(ratio=2)
    table.add_column(ratio=1)
    for lane in lanes:
        style = _style(theme, LANE_STYLE.get(lane.status, "muted"))
        table.add_row(
            Text(lane.name.upper(), style=f"bold {style}"),
            Text(lane.status, style=style),
            Text(_trim(lane.detail, 72), style=_style(theme, "text")),
            Text(lane.model, style=_style(theme, "muted")),
        )
    if not lanes:
        table.add_row(Text("NO LANES", style=_style(theme, "muted")), Text("idle"), Text(""), Text(""))
    return _panel(table, "Agent Lanes", theme)


def task_board_panel(rows: tuple[BoardRow, ...], theme: UxTheme = DEFAULT_THEME) -> Panel:
    table = Table.grid(expand=True)
    table.add_column(width=5)
    table.add_column(ratio=3)
    table.add_column(ratio=1)
    for row in rows:
        style = _style(theme, STATUS_STYLE.get(row.status, "muted"))
        detail = row.blocker if row.status == "blocked" and row.blocker else row.kind
        table.add_row(
            Text(STATUS_MARKERS.get(row.status, "[ ]"), style=f"bold {style}"),
            Text(_trim(row.title, 92), style=style if row.status == "active" else _style(theme, "text")),
            Text(_trim(detail, 36), style=_style(theme, "muted")),
        )
    if not rows:
        table.add_row(Text("[ ]", style=_style(theme, "muted")), Text("No task board from runtime"), Text(""))
    return _panel(table, "Task Board", theme)


def transcript_panel(items: tuple[TranscriptItem, ...], theme: UxTheme = DEFAULT_THEME, *, limit: int = 8) -> Panel:
    text = Text()
    selected = items[-limit:]
    for index, item in enumerate(selected):
        speaker = item.speaker.strip().lower() or "system"
        speaker_style = _style(theme, "brand" if speaker in {"mo", "assistant"} else "blue")
        text.append(speaker.upper(), style=f"bold {speaker_style}")
        text.append("  ")
        text.append(_trim(item.text, 160), style=_style(theme, "text"))
        if index < len(selected) - 1:
            text.append("\n")
    if not selected:
        text.append("No transcript yet", style=_style(theme, "muted"))
    return _panel(text, "Transcript", theme)


def composer_panel(snapshot: SessionSnapshot, theme: UxTheme = DEFAULT_THEME) -> Panel:
    text = Text()
    if snapshot.notice:
        text.append(_trim(snapshot.notice, 120), style=_style(theme, "amber"))
        text.append("\n")
    text.append("> ", style=f"bold {_style(theme, 'brand')}")
    text.append(snapshot.composer_placeholder, style=_style(theme, "muted"))
    if snapshot.composer_hint:
        text.append("    ")
        text.append(snapshot.composer_hint, style=_style(theme, "muted"))
    return _panel(text, "Composer", theme)


def build_screen(snapshot: SessionSnapshot, theme: UxTheme = DEFAULT_THEME) -> Group:
    return Group(
        header(snapshot, theme),
        lanes_panel(snapshot.lanes, theme),
        task_board_panel(snapshot.board, theme),
        transcript_panel(snapshot.transcript, theme),
        composer_panel(snapshot, theme),
    )


def render_text(snapshot: SessionSnapshot, *, width: int = 110, theme: UxTheme = DEFAULT_THEME) -> str:
    console = Console(record=True, width=max(60, int(width or 110)), color_system=None)
    console.print(build_screen(snapshot, theme))
    return console.export_text(clear=False)
