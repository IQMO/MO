"""Tests for companion voice (Phase 3) — STT + TTS integration."""


class TestVoiceRecognizer:
    def test_import_voice_module(self):
        from interface.companion.voice import VoiceRecognizer, VoiceSpeaker, CompanionVoice
        assert VoiceRecognizer is not None
        assert VoiceSpeaker is not None
        assert CompanionVoice is not None

    def test_recognizer_available_without_deps(self):
        """VoiceRecognizer reports unavailable when faster-whisper not installed."""
        from interface.companion.voice import VoiceRecognizer
        rec = VoiceRecognizer()
        # In test env, faster-whisper may or may not be installed
        result = rec.available
        assert isinstance(result, bool)

    def test_recognizer_transcribe_without_model(self):
        """Transcribe returns error text when model not loaded."""
        from interface.companion.voice import VoiceRecognizer
        rec = VoiceRecognizer()
        # Without faster-whisper installed, transcribe returns error string
        import numpy as np
        audio = np.zeros(16000, dtype="float32")
        text = rec.transcribe(audio)
        assert isinstance(text, str)
        if not rec.available:
            assert "unavailable" in text.lower() or "not installed" in text.lower()

    def test_speaker_available_without_deps(self):
        from interface.companion.voice import VoiceSpeaker
        spk = VoiceSpeaker()
        result = spk.available
        assert isinstance(result, bool)

    def test_speaker_no_model_path(self):
        """Speaker returns False when no voice model path configured."""
        from interface.companion.voice import VoiceSpeaker
        spk = VoiceSpeaker(voice_model_path="")
        assert spk._ensure_voice() is False
        assert "no voice model" in str(spk._load_error).lower()

    def test_companion_voice_constructs(self):
        from interface.companion.voice import CompanionVoice
        cv = CompanionVoice()
        assert cv.recognizer is not None
        assert cv.speaker is not None
        assert cv.recorder is not None

    def test_companion_voice_stt_tts_properties(self):
        from interface.companion.voice import CompanionVoice
        cv = CompanionVoice()
        assert isinstance(cv.stt_available, bool)
        assert isinstance(cv.tts_available, bool)


class TestVoiceIntegration:
    def test_companion_surface_accepts_voice_config(self):
        from interface.companion.companion import CompanionSurface
        # CompanionSurface accepts voice_config (no gateway needed for construction)
        # Just verify the constructor signature
        import inspect
        sig = inspect.signature(CompanionSurface.__init__)
        params = list(sig.parameters.keys())
        assert "voice_config" in params

    def test_companion_voice_in_init(self):
        """verify CompanionVoice is exported from companion package."""
        from interface.companion import CompanionVoice
        assert CompanionVoice is not None


def test_recorder_caps_buffer_and_autostops_at_max_seconds():
    """V1: the audio buffer is bounded and recording auto-stops at max_seconds,
    so a forgotten recording can't grow memory or capture indefinitely."""
    from interface.companion.voice import PushToTalkRecorder
    rec = PushToTalkRecorder(sample_rate=100, max_seconds=1.0)  # cap = 100 samples
    rec._recording = True
    for _ in range(5):  # 5 x 50 = 250 samples fed, well past the 100 cap
        rec._audio_callback([0.0] * 50, 50, None, None)
    total = sum(len(c) for c in rec._buffer)
    assert total <= 100, total          # buffer bounded, not 250
    assert rec._recording is False      # auto-stopped at the cap
