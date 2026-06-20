"""MO Companion surface — desktop text-input overlay + global hotkey.

The companion is MO's on-screen surface: summon with Win+Alt+M, type, and get
results back via Ghost → Gateway → overlay bubble. Runs alongside the TUI as a
daemon thread.
"""
from __future__ import annotations

from interface.companion.companion import CompanionSurface, start_companion_if_enabled

__all__ = ["CompanionSurface", "start_companion_if_enabled"]
