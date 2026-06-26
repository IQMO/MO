"""Screen-level composition for the isolated UX surface."""
from __future__ import annotations

from rich.console import Console, Group
from rich.layout import Layout

from UX.state.models import SessionSnapshot
from .panels import activity_panel, composer_panel, header, lanes_panel, ops_rail_panel, task_board_panel, transcript_panel
from .theme import DEFAULT_THEME, UxTheme

COMMAND_CENTER_MIN_WIDTH = 112


def build_screen(snapshot: SessionSnapshot, theme: UxTheme = DEFAULT_THEME, *, width: int = 110) -> object:
    if width < COMMAND_CENTER_MIN_WIDTH:
        return Group(
            header(snapshot, theme),
            lanes_panel(snapshot.lanes, theme),
            task_board_panel(snapshot.board, theme),
            transcript_panel(snapshot.transcript, theme, limit=12),
            activity_panel(snapshot, theme),
            composer_panel(snapshot, theme),
        )

    root = Layout(name="root")
    root.split_column(
        Layout(header(snapshot, theme), name="header", size=4),
        Layout(name="body", ratio=1),
        Layout(composer_panel(snapshot, theme), name="composer", size=4),
    )
    root["body"].split_row(
        Layout(lanes_panel(snapshot.lanes, theme), name="lanes", size=34),
        Layout(transcript_panel(snapshot.transcript, theme, limit=12), name="transcript", ratio=1),
        Layout(ops_rail_panel(snapshot, theme), name="side", size=40),
    )
    return root


def render_text(snapshot: SessionSnapshot, *, width: int = 110, theme: UxTheme = DEFAULT_THEME) -> str:
    display_width = max(60, int(width or 110))
    console = Console(record=True, width=display_width, color_system=None)
    console.print(build_screen(snapshot, theme, width=display_width))
    return console.export_text(clear=False)
