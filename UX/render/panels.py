"""Panel-level render components for the isolated UX surface."""
from __future__ import annotations

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from UX.state.models import BoardRow, LaneSnapshot, SessionSnapshot, TranscriptItem
from .common import frame_panel, style, trim
from .theme import DEFAULT_THEME, LANE_STYLE, STATUS_STYLE, UxTheme

STATUS_MARKERS = {
    "completed": "[x]",
    "active": ">",
    "blocked": "!",
    "pending": "[ ]",
}


def header(snapshot: SessionSnapshot, theme: UxTheme = DEFAULT_THEME) -> Panel:
    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)
    grid.add_column(justify="right", ratio=1)

    left = Text()
    left.append("MO", style=f"bold {style(theme, 'brand')}")
    left.append("  ")
    left.append(snapshot.project or "project not set", style=style(theme, "text"))
    left.append("\n")
    left.append(snapshot.model_label, style=style(theme, "muted"))

    right = Text()
    right.append("BUSY" if snapshot.busy else "READY", style=style(theme, "amber" if snapshot.busy else "green"))
    if snapshot.runtime:
        right.append("\n")
        right.append(trim(snapshot.runtime, 72), style=style(theme, "muted"))

    grid.add_row(left, right)
    return frame_panel(grid, "Session", theme)


def lanes_panel(lanes: tuple[LaneSnapshot, ...], theme: UxTheme = DEFAULT_THEME) -> Panel:
    table = Table.grid(expand=True)
    table.add_column(ratio=1)
    table.add_column(ratio=1)
    table.add_column(ratio=2)
    table.add_column(ratio=1)
    for lane in lanes:
        lane_style = style(theme, LANE_STYLE.get(lane.status, "muted"))
        table.add_row(
            Text(lane.name.upper(), style=f"bold {lane_style}"),
            Text(lane.status, style=lane_style),
            Text(trim(lane.detail, 72), style=style(theme, "text")),
            Text(lane.model, style=style(theme, "muted")),
        )
    if not lanes:
        table.add_row(Text("NO LANES", style=style(theme, "muted")), Text("idle"), Text(""), Text(""))
    return frame_panel(table, "Agent Lanes", theme)


def task_board_panel(rows: tuple[BoardRow, ...], theme: UxTheme = DEFAULT_THEME) -> Panel:
    table = Table.grid(expand=True)
    table.add_column(width=5)
    table.add_column(ratio=3)
    table.add_column(ratio=1)
    for row in rows:
        row_style = style(theme, STATUS_STYLE.get(row.status, "muted"))
        detail = row.blocker if row.status == "blocked" and row.blocker else row.kind
        table.add_row(
            Text(STATUS_MARKERS.get(row.status, "[ ]"), style=f"bold {row_style}"),
            Text(trim(row.title, 92), style=row_style if row.status == "active" else style(theme, "text")),
            Text(trim(detail, 36), style=style(theme, "muted")),
        )
    if not rows:
        table.add_row(Text("[ ]", style=style(theme, "muted")), Text("Idle - task board appears for work turns"), Text(""))
    return frame_panel(table, "Task Board", theme)


def transcript_panel(items: tuple[TranscriptItem, ...], theme: UxTheme = DEFAULT_THEME, *, limit: int = 8) -> Panel:
    text = Text()
    selected = items[-limit:]
    for index, item in enumerate(selected):
        speaker = item.speaker.strip().lower() or "system"
        speaker_style = style(theme, "brand" if speaker in {"mo", "assistant"} else "blue")
        text.append(speaker.upper(), style=f"bold {speaker_style}")
        text.append("  ")
        text.append(trim(item.text, 160), style=style(theme, "text"))
        if index < len(selected) - 1:
            text.append("\n")
    if not selected:
        text.append("No transcript yet", style=style(theme, "muted"))
    return frame_panel(text, "Transcript", theme)


def composer_panel(snapshot: SessionSnapshot, theme: UxTheme = DEFAULT_THEME) -> Panel:
    text = Text()
    if snapshot.notice:
        text.append(trim(snapshot.notice, 120), style=style(theme, "amber"))
        text.append("\n")
    text.append("> ", style=f"bold {style(theme, 'brand')}")
    text.append(snapshot.composer_placeholder, style=style(theme, "muted"))
    if snapshot.composer_hint:
        text.append("    ")
        text.append(snapshot.composer_hint, style=style(theme, "muted"))
    return frame_panel(text, "Composer", theme)


def activity_panel(snapshot: SessionSnapshot, theme: UxTheme = DEFAULT_THEME) -> Panel:
    text = Text()
    state = "busy" if snapshot.busy else "ready"
    state_style = style(theme, "amber" if snapshot.busy else "green")
    text.append(state.upper(), style=f"bold {state_style}")
    if snapshot.notice:
        text.append("\n")
        text.append(trim(snapshot.notice, 96), style=style(theme, "amber"))
    text.append("\n")
    text.append("runtime truth: Gateway/taskboard", style=style(theme, "muted"))
    text.append("\n")
    text.append("surface: isolated UX", style=style(theme, "muted"))
    return frame_panel(text, "Activity", theme)
