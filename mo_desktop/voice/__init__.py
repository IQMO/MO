"""Light, optional MO Desktop voice boundary.

The existing voice-capture API remains available from ``mo_desktop.voice`` while
its implementation lives in ``input``. Speech output and conversation control
can grow beside it without putting optional audio/model imports on MO startup.
"""
from __future__ import annotations

from .input import (
    CompanionVoice,
    ConfidenceLimits,
    PushToTalkRecorder,
    TranscriptionEvidence,
    VoiceRecognizer,
    WhisperWorkerRecognizer,
    WindowsSpeechRecognizer,
    make_voice_recognizer,
    make_voice_recorder,
)
from .output import SpeechOutput, prepare_spoken_text, spoken_max_chars

__all__ = [
    "CompanionVoice",
    "ConfidenceLimits",
    "PushToTalkRecorder",
    "SpeechOutput",
    "TranscriptionEvidence",
    "VoiceRecognizer",
    "WhisperWorkerRecognizer",
    "WindowsSpeechRecognizer",
    "make_voice_recognizer",
    "make_voice_recorder",
    "prepare_spoken_text",
    "spoken_max_chars",
]
