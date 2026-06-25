"""Shared Rich helpers for the isolated UX render layer."""
from __future__ import annotations

from rich import box
from rich.panel import Panel

from .theme import UxTheme


def style(theme: UxTheme, token: str) -> str:
    return getattr(theme, token, theme.text)


def trim(value: str, limit: int) -> str:
    text = str(value or "").replace("\r", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def frame_panel(renderable: object, title: str, theme: UxTheme) -> Panel:
    return Panel(
        renderable,
        title=title,
        title_align="left",
        border_style=style(theme, "border"),
        padding=(0, 1),
        box=box.ASCII,
    )
