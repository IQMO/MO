"""MO Companion surface — desktop text-input overlay + global hotkey + voice + tray.

The companion is MO's on-screen surface: summon with Win+Alt+M, type, and get
results back via Ghost → Gateway → overlay bubble. Phase 3 adds push-to-talk
voice: sounddevice captures the mic, faster-whisper transcribes it, and
piper-tts can speak replies when a local voice model is configured. Phase 4 adds
system-tray icon with Guide/Do modes, action log, run-at-startup, and panic-stop.
"""
from __future__ import annotations

from interface.companion.companion import CompanionSurface, start_companion_if_enabled
from interface.companion.tray import CompanionTray, start_tray_if_enabled
from interface.companion.voice import CompanionVoice, VoiceRecognizer, VoiceSpeaker

__all__ = [
    "CompanionSurface",
    "CompanionTray",
    "CompanionVoice",
    "VoiceRecognizer",
    "VoiceSpeaker",
    "start_companion_if_enabled",
    "start_tray_if_enabled",
]
