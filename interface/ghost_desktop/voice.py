"""Ghost desktop voice — local STT + TTS for push-to-talk.

Phase 3 of the desktop companion: push-to-talk mic capture via sounddevice,
speech-to-text via faster-whisper, and text-to-speech via piper-tts. All local,
push-to-talk only (no always-on mic).

Dependencies (all optional, graceful degraded):
    faster-whisper   — local transcription (CTranslate2-backed Whisper)
    piper-tts        — local TTS (neural, espeak-ng phonemization)
    sounddevice      — audio capture / playback (cross-platform)
    numpy            — audio buffer processing
"""
from __future__ import annotations

import threading
import traceback
from typing import Any, Callable

_MODEL_CACHE: dict[str, Any] = {}


# ------------------------------------------------------------------
# Voice recognition (STT)
# ------------------------------------------------------------------

class VoiceRecognizer:
    """Local speech-to-text using faster-whisper.

    Lazy-loads the WhisperModel on first use. Model is downloaded from HuggingFace
    on first run and cached locally.
    """

    def __init__(self, model_size: str = "base", device: str = "cpu",
                 compute_type: str = "int8") -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model: Any = None
        self._load_error: str | None = None

    @property
    def available(self) -> bool:
        """True if faster-whisper imported successfully."""
        if self._load_error:
            return False
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            self._load_error = "faster-whisper not installed"
            return False

    def _ensure_model(self) -> bool:
        if self._model is not None:
            return True
        if self._load_error:
            return False
        try:
            import os
            # Quiet the benign HF symlink/cache notice on Windows (no Dev Mode).
            os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
            from faster_whisper import WhisperModel
            self._model = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
            )
            return True
        except ImportError:
            self._load_error = "faster-whisper not installed"
        except Exception as exc:
            self._load_error = f"WhisperModel load failed: {exc}"
            traceback.print_exc()
        return False

    def transcribe(self, audio: Any, sample_rate: int = 16000) -> str:
        """Transcribe raw audio (numpy array, float32, mono) to text."""
        if not self._ensure_model():
            return f"[STT unavailable: {self._load_error}]"
        try:
            segments, _info = self._model.transcribe(audio, beam_size=5)
            text = " ".join(seg.text.strip() for seg in segments)
            return text.strip()
        except Exception as exc:
            traceback.print_exc()
            return f"[STT error: {exc}]"


# ------------------------------------------------------------------
# Voice synthesis (TTS)
# ------------------------------------------------------------------

class VoiceSpeaker:
    """Local text-to-speech using piper-tts.

    Lazy-loads a Piper voice model on first use. Voice models must be downloaded
    separately (e.g. from https://huggingface.co/rhasspy/piper-voices).
    """

    def __init__(self, voice_model_path: str = "", voice_name: str = "en_US-lessac-medium") -> None:
        self._voice_model_path = voice_model_path
        self._voice_name = voice_name
        self._voice: Any = None
        self._load_error: str | None = None

    @property
    def available(self) -> bool:
        """True if piper-tts imported successfully."""
        if self._load_error:
            return False
        # The piper-tts package imports as `piper` (not `piper_tts`).
        try:
            import piper  # noqa: F401
            return True
        except ImportError:
            self._load_error = "piper-tts not installed"
            return False

    def _ensure_voice(self) -> bool:
        if self._voice is not None:
            return True
        if self._load_error:
            return False
        if not self._voice_model_path:
            self._load_error = "no voice model path configured (set ghost.voice.tts_model)"
            return False
        try:
            try:
                from piper.voice import PiperVoice
            except ImportError:
                from piper import PiperVoice  # older/newer layout
            import os
            model_path = os.path.expanduser(self._voice_model_path)
            if not os.path.exists(model_path):
                self._load_error = f"voice model not found: {model_path}"
                return False
            self._voice = PiperVoice.load(model_path)
            return True
        except ImportError:
            self._load_error = "piper-tts not installed"
        except Exception as exc:
            self._load_error = f"PiperVoice load failed: {exc}"
            traceback.print_exc()
        return False

    def speak(self, text: str) -> bool:
        """Speak text aloud. Returns True on success."""
        if not text or not text.strip():
            return False
        if not self._ensure_voice():
            return False
        try:
            import io
            import sounddevice as sd
            import numpy as np
            import wave

            # Synthesize to WAV in memory
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(22050)
                self._voice.synthesize(text.strip(), wf)

            # Play the audio
            buf.seek(0)
            with wave.open(buf, "rb") as wf:
                data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
                sd.play(data.astype(np.float32) / 32768.0, samplerate=wf.getframerate())
                sd.wait()
            return True
        except ImportError as exc:
            self._load_error = f"{exc}"
            return False
        except Exception:
            traceback.print_exc()
            return False

    def speak_async(self, text: str, on_error: Callable[[str], None] | None = None) -> None:
        """Speak in a background thread (non-blocking). If speaking fails (no model,
        no audio device), invoke on_error with the reason instead of dying silently."""
        if not text or not text.strip():
            return

        def _run() -> None:
            if not self.speak(text) and on_error is not None:
                on_error(self._load_error or "voice output failed")

        threading.Thread(target=_run, name="mo-companion-tts", daemon=True).start()


# ------------------------------------------------------------------
# Push-to-talk recorder
# ------------------------------------------------------------------

class PushToTalkRecorder:
    """Record audio while a key is held, transcribe on release.

    Uses sounddevice for capture. The recording is triggered externally
    (by the companion hotkey system) — start() on key-down, stop() on key-up.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        max_seconds: float = 30.0,
        *,
        silence_threshold: float = 0.012,
        silence_hangover: float = 1.2,
    ) -> None:
        self._sample_rate = sample_rate
        self._max_seconds = max_seconds
        self._max_samples = int(sample_rate * max_seconds)
        self._collected = 0
        self._recording = False
        self._buffer: list[Any] = []
        self._stream: Any = None
        self.last_error = ""  # human-readable reason start() failed (mic perms, etc.)
        # Voice-activity auto-stop: once the operator has spoken, end the capture
        # after a short trailing silence so they never have to tap again to finish.
        self._silence_threshold = float(silence_threshold)
        self._silence_hangover = float(silence_hangover)
        self._speech_started = False
        self._silence_run = 0.0  # seconds of quiet accumulated since the last speech
        self.level = 0.0  # smoothed live RMS (0..~0.3); the orb reads this to react

    @property
    def available(self) -> bool:
        try:
            import sounddevice  # noqa: F401
            return True
        except ImportError:
            return False

    def start(self) -> bool:
        """Begin recording. Returns True on success."""
        if self._recording:
            self.last_error = "already recording"
            return False
        if not self.available:
            self.last_error = "audio backend (sounddevice) not installed"
            return False
        try:
            import sounddevice as sd
            self._buffer = []
            self._collected = 0
            self._speech_started = False
            self._silence_run = 0.0
            self.level = 0.0
            self.last_error = ""
            self._stream = sd.InputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="float32",
                callback=self._audio_callback,
            )
            self._stream.start()
            self._recording = True
            return True
        except Exception as exc:
            self.last_error = str(exc) or exc.__class__.__name__
            traceback.print_exc()
            return False

    def stop(self) -> Any | None:
        """Stop recording and return the raw audio as a numpy array (float32)."""
        # Tear the stream down even if the cap-callback already cleared _recording —
        # otherwise the InputStream (and the OS mic) stays open until process exit.
        if not self._recording and self._stream is None:
            return None
        self._recording = False
        try:
            import numpy as np
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None
            if self._buffer:
                audio = np.concatenate(self._buffer, axis=0)
                self._buffer = []
                # Trim to max_seconds
                max_samples = int(self._sample_rate * self._max_seconds)
                if len(audio) > max_samples:
                    audio = audio[:max_samples]
                return audio
        except Exception:
            traceback.print_exc()
        self._buffer = []
        return None

    def _audio_callback(self, indata: Any, _frames: int, _time: Any, _status: Any) -> None:
        if not self._recording:
            return
        # Bound the buffer + auto-stop collecting at max_seconds so a forgotten
        # recording can't grow memory unbounded or capture indefinitely.
        if self._collected >= self._max_samples:
            self._recording = False
            # Release the mic device too, not just the flag — a forgotten recording
            # must not leave the InputStream (and the OS mic) hot. CallbackStop halts
            # the PortAudio stream cleanly; stop() then closes it.
            if self._stream is not None:
                import sounddevice as sd
                raise sd.CallbackStop
            return
        chunk = indata.copy()
        self._buffer.append(chunk)
        self._collected += len(chunk)
        # End-of-speech detection: stop shortly after the operator goes quiet, so
        # they don't have to tap again to signal "done". Only armed after speech is
        # actually heard, so the initial pre-speech silence never cuts the capture.
        if self._is_trailing_silence(chunk):
            self._recording = False
            if self._stream is not None:
                import sounddevice as sd
                raise sd.CallbackStop

    def _is_trailing_silence(self, chunk: Any) -> bool:
        """True once speech has been heard and enough trailing quiet has elapsed."""
        try:
            import numpy as np
            rms = float(np.sqrt(np.mean(np.square(chunk)))) if len(chunk) else 0.0
        except Exception:
            return False
        # Smoothed live level so the orb can pulse with the operator's voice.
        self.level = 0.8 * self.level + 0.2 * rms
        duration = len(chunk) / float(self._sample_rate or 16000)
        if rms >= self._silence_threshold:
            self._speech_started = True
            self._silence_run = 0.0
            return False
        if not self._speech_started:
            return False  # still waiting for the first word — don't arm yet
        self._silence_run += duration
        return self._silence_run >= self._silence_hangover


# ------------------------------------------------------------------
# Voice integration helper
# ------------------------------------------------------------------

class CompanionVoice:
    """Ties STT + TTS + PTT into one voice-capable companion component.

    Integrate into CompanionSurface for push-to-talk voice input/output.
    """

    def __init__(self, recognizer: VoiceRecognizer | None = None,
                 speaker: VoiceSpeaker | None = None,
                 recorder: PushToTalkRecorder | None = None) -> None:
        self.recognizer = recognizer or VoiceRecognizer()
        self.speaker = speaker or VoiceSpeaker()
        self.recorder = recorder or PushToTalkRecorder()

    @property
    def stt_available(self) -> bool:
        return self.recognizer.available and self.recorder.available

    @property
    def tts_available(self) -> bool:
        return self.speaker.available

    def start_recording(self) -> bool:
        """Begin push-to-talk capture. GUI-driven — no blocking input()."""
        if not self.stt_available:
            return False
        return self.recorder.start()

    def stop_and_transcribe(self) -> str:
        """Stop capture (closes the mic — never left open) and transcribe."""
        if not self.stt_available:
            return "[Voice input not available]"
        audio = self.recorder.stop()
        if audio is None:
            return ""
        return self.recognizer.transcribe(audio)

    def speak_result(self, text: str, on_error: Callable[[str], None] | None = None) -> None:
        """Speak the result text via TTS (async, non-blocking)."""
        if not self.tts_available:
            return
        # Speak only the first ~300 chars for brevity
        summary = text.strip()[:300]
        self.speaker.speak_async(summary, on_error=on_error)
