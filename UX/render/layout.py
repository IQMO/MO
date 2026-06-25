"""Compatibility exports for UX render layout.

New code should import from ``UX.render.screen`` and ``UX.render.panels``.
"""
from __future__ import annotations

from .panels import (
    STATUS_MARKERS,
    activity_panel,
    composer_panel,
    header,
    lanes_panel,
    task_board_panel,
    transcript_panel,
)
from .screen import COMMAND_CENTER_MIN_WIDTH, build_screen, render_text

__all__ = [
    "COMMAND_CENTER_MIN_WIDTH",
    "STATUS_MARKERS",
    "activity_panel",
    "build_screen",
    "composer_panel",
    "header",
    "lanes_panel",
    "render_text",
    "task_board_panel",
    "transcript_panel",
]
