"""Prompt-toolkit theme for the MO full-screen TUI.

This module is visual-freeze protected: keys and values mirror the previous
inline Style.from_dict mapping in interface.main_terminal.
"""
from __future__ import annotations

TUI_STYLE_DICT: dict[str, str] = {
    "separator": "#555555",
    "footer": "#666666",
    "spinner": "#dddddd",
    "activity": "#00cccc bold",
    "goal-detail": "#66d9ef bold",
    "task-done": "#666666",
    "task-active": "#ddaa00",
    "task-blocked": "#cc4444",
    "task-pending": "#666666",
    "task-info": "#888888",
    "logo": "#00cccc bold",
    "user-msg": "bg:#1a3a4a #dddddd",
    "mo-marker": "#00cccc bold",
    "mo-response": "#bbccdd",
    "response-heading": "#00cccc bold",
    "response-bullet-marker": "#00cccc",
    "response-bullet-head": "#ffffff bold",
    "response-bullet-rest": "#8a949e",
    "response-code": "#a0c4ff italic",
    "palette-title": "#00cccc bold",
    "palette-category": "#00cccc",
    "palette-selected": "bg:#005f5f #ffffff bold",
    "palette-command": "#00cccc bold",
    "palette-desc": "#bbbbbb",
    "palette-hint": "#777777",
    "ghost-frame": "#00cccc",
    "ghost-hint": "#777777",
    "ghost-user": "bg:#005f5f #ffffff",
    "ghost-thinking": "#dddddd italic",
    "ghost-gap": "#000000",
    "ghost-response": "#8fa7b8",
    "ghost-route": "#bb86fc bold",
    "ghost-route-blocked": "#cc4444 bold",
    "dim": "#666666",
    "info": "#00cccc",
    "input-placeholder": "#555555 italic",
    "notification-idle": "#00cccc italic",
    "notification-prt": "#bb86fc bold",
    "notification-goal": "#ffae42 bold",
    "notification-worker": "#888888",
    "notification-critical": "#ff4444 bold",
    "low-balance": "#ffae42 bold",
    "model-fallback": "#ffae42 bold",
    "prt-header": "#bb86fc bold",
    "prt-critical": "#ff4444 bold",
    "prt-major": "#ffd166 bold",
    "prt-minor": "#ffae42",
    "prt-info": "#66d9ef",
    "prt-clean": "#00cc88 bold",
    "prt-summary": "#bbbbbb",
}


def build_tui_style():
    from prompt_toolkit.styles import Style

    return Style.from_dict(TUI_STYLE_DICT)
