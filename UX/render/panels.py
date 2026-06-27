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


def _board_row_parts(row: BoardRow, theme: UxTheme) -> tuple[str, str, str, str]:
    row_style = style(theme, STATUS_STYLE.get(row.status, "muted"))
    title_style = row_style if row.status == "active" else style(theme, "text")
    detail = row.blocker if row.status == "blocked" and row.blocker else row.kind
    return STATUS_MARKERS.get(row.status, "[ ]"), row_style, title_style, detail


def _append_board_row_text(text: Text, row: BoardRow, theme: UxTheme, *, detail_limit: int) -> None:
    marker, row_style, title_style, detail = _board_row_parts(row, theme)
    text.append(marker, style=f"bold {row_style}")
    text.append("  ")
    text.append(row.title, style=title_style)
    if detail:
        text.append("  ")
        text.append(trim(detail, detail_limit), style=style(theme, "muted"))


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
    text = Text()
    for index, lane in enumerate(lanes):
        lane_style = style(theme, LANE_STYLE.get(lane.status, "muted"))
        text.append(lane.name.upper(), style=f"bold {lane_style}")
        text.append("  ")
        text.append(lane.status, style=lane_style)
        if lane.model:
            text.append("  ")
            text.append(trim(lane.model, 22), style=style(theme, "muted"))
        if lane.detail:
            text.append("\n  ")
            text.append(trim(lane.detail, 72), style=style(theme, "text"))
        if index < len(lanes) - 1:
            text.append("\n")
    if not lanes:
        text.append("NO LANES  idle", style=style(theme, "muted"))
    return frame_panel(text, "Agent Lanes", theme)


def task_board_panel(rows: tuple[BoardRow, ...], theme: UxTheme = DEFAULT_THEME) -> Panel:
    table = Table.grid(expand=True)
    table.add_column(width=5)
    table.add_column(ratio=3)
    table.add_column(ratio=1)
    for row in rows:
        marker, row_style, title_style, detail = _board_row_parts(row, theme)
        table.add_row(
            Text(marker, style=f"bold {row_style}"),
            Text(trim(row.title, 92), style=title_style),
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


def ops_rail_panel(snapshot: SessionSnapshot, theme: UxTheme = DEFAULT_THEME) -> Panel:
    text = Text()
    text.append("Task Board", style=f"bold {style(theme, 'blue')}")
    text.append("\n")
    if snapshot.board:
        for index, row in enumerate(snapshot.board):
            _append_board_row_text(text, row, theme, detail_limit=12)
            if index < len(snapshot.board) - 1:
                text.append("\n")
    else:
        text.append("[ ]  Idle - task board appears for work turns", style=style(theme, "muted"))

    state = "busy" if snapshot.busy else "ready"
    state_style = style(theme, "amber" if snapshot.busy else "green")
    text.append("\n\n")
    text.append("Activity", style=f"bold {style(theme, 'blue')}")
    text.append("\n")
    text.append(state.upper(), style=f"bold {state_style}")
    if snapshot.notice:
        text.append("\n")
        text.append(trim(snapshot.notice, 78), style=style(theme, "amber"))
    text.append("\n")
    text.append("runtime truth: Gateway/taskboard", style=style(theme, "muted"))
    text.append("\n")
    text.append("surface: isolated UX", style=style(theme, "muted"))
    return frame_panel(text, "Ops Rail", theme)
