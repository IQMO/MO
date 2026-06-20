"""MO Companion surface — desktop text-input overlay + global hotkey + voice.

The companion is MO's on-screen surface: summon with Win+Alt+M, type, and get
results back via Ghost → Gateway → overlay bubble. Phase 3 adds push-to-talk
voice (STT via faster-whisper, TTS via piper-tts).
"""
from __future__ import annotations

from interface.companion.companion import CompanionSurface, start_companion_if_enabled
from interface.companion.voice import CompanionVoice, VoiceRecognizer, VoiceSpeaker

__all__ = [
    "CompanionSurface",
    "CompanionVoice",
    "VoiceRecognizer",
    "VoiceSpeaker",
    "start_companion_if_enabled",
]
