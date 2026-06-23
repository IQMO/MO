"""Back-compat shim — the desktop Ghost moved to ``interface.ghost_desktop``.

Kept only so existing run-at-startup shortcuts (``python -m interface.companion``)
and any old top-level imports keep working after the module-merge pass. New code
should import from ``interface.ghost_desktop``.
"""
from __future__ import annotations

from interface.ghost_desktop import (
    CompanionSurface,
    CompanionTray,
    CompanionVoice,
    VoiceRecognizer,
    VoiceSpeaker,
    start_companion_if_enabled,
    start_tray_if_enabled,
)

__all__ = [
    "CompanionSurface",
    "CompanionTray",
    "CompanionVoice",
    "VoiceRecognizer",
    "VoiceSpeaker",
    "start_companion_if_enabled",
    "start_tray_if_enabled",
]
