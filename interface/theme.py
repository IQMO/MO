"""Prompt-toolkit theme for the MO full-screen TUI.

Delegates colour values to ``interface.theming`` (single source of truth).
"""
from __future__ import annotations

from interface.theming import skin_to_tui_style_dict


def build_tui_style():
    from prompt_toolkit.styles import Style

    return Style.from_dict(skin_to_tui_style_dict())


def build_tui_color_depth():
    from prompt_toolkit.output.color_depth import ColorDepth

    return ColorDepth.from_env() or ColorDepth.TRUE_COLOR
