"""Prompt-toolkit theme for the MO full-screen TUI.

Delegates colour values to ``interface.theming`` (single source of truth).
"""
from __future__ import annotations

from interface.theming import skin_to_tui_style_dict


def build_tui_style():
    from prompt_toolkit.styles import Style

    return Style.from_dict(skin_to_tui_style_dict())


# Back-compat dict for tests that import TUI_STYLE_DICT directly.
# Values are resolved from the active skin at import time.
TUI_STYLE_DICT = skin_to_tui_style_dict()
