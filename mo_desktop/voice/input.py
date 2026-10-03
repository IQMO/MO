"""MO Desktop voice input — local speech-to-text capture.

Voice input uses sounddevice capture plus either faster-whisper or Windows'
default speech recognizer. Input remains usable as manual capture while optional
Voice Chat can continue into local spoken replies.

Dependencies (all optional, graceful degraded):
    faster-whisper   — local transcription (CTranslate2-backed Whisper)
    pywin32/SAPI     — Windows default speech recognizer backend
    sounddevice      — microphone capture
    numpy            — audio buffer processing
"""
from __future__ import annotations

from core.state.configuration_defaults import DEFAULT_PREFERENCES

import sys
import tempfile
import threading
import time
import traceback
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .storage import activate_voice_dependencies, resolve_voice_install_root, voice_layout

_WINDOWS_STT_ENGINES = frozenset({"windows", "windows-default", "sapi", "system"})


@dataclass(frozen=True)
class TranscriptionEvidence:
    """Small, backend-neutral signal used to avoid acting on dubious speech."""

    text: str = ""
    uncertain: bool = False
    reason: str = ""


@dataclass(frozen=True)
class ConfidenceLimits:
    """Thresholds that mark a transcript as dubious.

    These are model-dependent: a `tiny` int8 model reports lower average log
    probabilities than `small` for the same clean speech, so a single hardcoded
    cut-off either nags on one model or lets errors through on the other. The
    defaults preserve the shipped behaviour; `voice.stt_*` keys let the operator
    tune them against measurements instead of guesses.
    """

    min_avg_logprob: float = -1.0
    max_no_speech: float = 0.6
    max_compression: float = 2.4
    min_language_probability: float = 0.45


# ------------------------------------------------------------------
# Voice recognition (STT)
# ------------------------------------------------------------------

class VoiceRecognizer:
    """Local speech-to-text using faster-whisper.

    Lazy-loads the WhisperModel on first use. Model is downloaded from HuggingFace
    on first run and cached locally.
    """

    def __init__(self, model_size: str = "tiny", device: str = "cpu",
                 compute_type: str = "int8", beam_size: int = 1,
                 *, confidence: "ConfidenceLimits | None" = None) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._beam_size = max(1, int(beam_size or 1))
        self._model: Any = None
        self._load_error: str | None = None
        self._model_lock = threading.RLock()
        self._confidence = confidence or ConfidenceLimits()
        self.last_evidence = TranscriptionEvidence()

    def warm(self) -> bool:
        """Load the model before it is needed. Safe to call from a worker thread.

        Without this the whole cold start (import + model load) lands *after* the
        operator stops speaking, on the visible path to their first transcript.
        """
        return self._ensure_model()

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
        # Voice Chat can open as soon as Piper is ready while the background
        # Whisper warm-up is still running. Serialize first use so an immediate
        # transcript joins that warm-up instead of constructing a second model.
        with self._model_lock:
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

    def close(self) -> None:
        """Release this recognizer's model."""
        with self._model_lock:
            self._model = None

    def transcribe(
        self,
        audio: Any,
        sample_rate: int = 16000,
        *,
        cancel_event: Any = None,
        initial_prompt: str = "",
    ) -> str:
        """Transcribe raw audio (numpy array, float32, mono) to text."""
        if not self._ensure_model():
            text = f"[STT unavailable: {self._load_error}]"
            self.last_evidence = TranscriptionEvidence(text, True, "stt-unavailable")
            return text
        try:
            # faster-whisper needs a 1-D mono float32 waveform. A (samples, 1) array
            # makes its feature extractor explode the mel dims, so flatten defensively.
            import numpy as np
            audio = np.ascontiguousarray(np.asarray(audio, dtype="float32")).reshape(-1)
            prompt = " ".join(str(initial_prompt or "").split())[-320:]
            segments, info = self._model.transcribe(
                audio,
                beam_size=self._beam_size,
                initial_prompt=prompt or None,
            )
            segment_rows = []
            for segment in segments:
                if cancel_event is not None and cancel_event.is_set():
                    self.last_evidence = TranscriptionEvidence("", True, "stt-cancelled")
                    return ""
                segment_rows.append(segment)
            text = " ".join(
                str(getattr(seg, "text", "") or "").strip()
                for seg in segment_rows
            ).strip()
            reasons: list[str] = []
            log_probs = [
                float(value)
                for value in (getattr(seg, "avg_logprob", None) for seg in segment_rows)
                if isinstance(value, (int, float))
            ]
            no_speech = [
                float(value)
                for value in (getattr(seg, "no_speech_prob", None) for seg in segment_rows)
                if isinstance(value, (int, float))
            ]
            compression = [
                float(value)
                for value in (getattr(seg, "compression_ratio", None) for seg in segment_rows)
                if isinstance(value, (int, float))
            ]
            language_probability = getattr(info, "language_probability", None)
            limits = self._confidence
            if log_probs and sum(log_probs) / len(log_probs) < limits.min_avg_logprob:
                reasons.append("low-log-probability")
            if no_speech and max(no_speech) >= limits.max_no_speech:
                reasons.append("probable-no-speech")
            if compression and max(compression) > limits.max_compression:
                reasons.append("repetitive-transcript")
            if (
                isinstance(language_probability, (int, float))
                and float(language_probability) < limits.min_language_probability
            ):
                reasons.append("uncertain-language")
            self.last_evidence = TranscriptionEvidence(
                text,
                bool(reasons),
                reasons[0] if reasons else "",
            )
            return text
        except Exception as exc:
            traceback.print_exc()
            text = f"[STT error: {exc}]"
            self.last_evidence = TranscriptionEvidence(text, True, "stt-error")
            return text


class WhisperWorkerRecognizer(VoiceRecognizer):
    """Light parent controller for one demand-started Whisper model process."""

    def __init__(
        self,
        *args: Any,
        idle_seconds: float = 180.0,
        request_timeout_seconds: float = 180.0,
        max_audio_seconds: float = 300.0,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._idle_seconds = max(15.0, min(900.0, float(idle_seconds)))
        self._request_timeout_seconds = max(
            15.0,
            min(600.0, float(request_timeout_seconds)),
        )
        self._max_audio_seconds = max(1.0, min(300.0, float(max_audio_seconds)))
        self._process: Any = None
        self._connection: Any = None
        self._reader: threading.Thread | None = None
        self._cancel_event: Any = None
        self._ready_event = threading.Event()
        self._condition = threading.Condition()
        self._results: dict[str, dict[str, Any]] = {}
        self._controller_lock = threading.RLock()
        self._expected_stop = False
        self._worker_ok = False
        self._started_at = 0.0

    @property
    def running(self) -> bool:
        process = self._process
        return bool(process is not None and process.is_alive())

    @property
    def available(self) -> bool:
        return not bool(self._load_error)

    def _worker_config(self) -> dict[str, Any]:
        return {
            "model_size": self._model_size,
            "device": self._device,
            "compute_type": self._compute_type,
            "beam_size": self._beam_size,
            "idle_seconds": self._idle_seconds,
            "confidence": {
                "min_avg_logprob": self._confidence.min_avg_logprob,
                "max_no_speech": self._confidence.max_no_speech,
                "max_compression": self._confidence.max_compression,
                "min_language_probability": self._confidence.min_language_probability,
            },
        }

    def start(self) -> bool:
        """Start loading without waiting; safe to overlap with microphone capture."""
        import multiprocessing

        with self._controller_lock:
            if self.running:
                return True
            self._close_handles()
            from .stt_worker import stt_worker_main

            context = multiprocessing.get_context("spawn")
            parent, child = context.Pipe(duplex=True)
            cancel_event = context.Event()
            process = context.Process(
                target=stt_worker_main,
                args=(child, self._worker_config(), cancel_event),
                name="mo-desktop-whisper",
                daemon=True,
            )
            self._ready_event.clear()
            self._worker_ok = False
            self._expected_stop = False
            self._started_at = time.monotonic()
            process.start()
            child.close()
            self._process = process
            self._connection = parent
            self._cancel_event = cancel_event
            self._reader = threading.Thread(
                target=self._read_worker,
                name="mo-desktop-whisper-events",
                daemon=True,
            )
            self._reader.start()
            self._emit_resource("start")
            return True

    def warm(self) -> bool:
        if not self.start():
            return False
        self._ready_event.wait(self._request_timeout_seconds)
        return bool(self.running and self._worker_ok)

    def _read_worker(self) -> None:
        connection = self._connection
        try:
            while connection is not None:
                message = connection.recv()
                if not isinstance(message, dict):
                    continue
                event = str(message.get("event") or "")
                if event == "ready":
                    self._worker_ok = bool(message.get("ok"))
                    if not self._worker_ok:
                        self._load_error = "Whisper worker could not load the model"
                    self._ready_event.set()
                    self._emit_resource("ready", reason="ok" if self._worker_ok else "load_failed")
                elif event == "first_use":
                    self._emit_resource("first_use")
                elif event == "result":
                    request_id = str(message.get("request_id") or "")
                    with self._condition:
                        self._results[request_id] = message
                        self._condition.notify_all()
                elif event == "idle_close":
                    self._expected_stop = True
                    self._emit_resource("idle_close", reason="lease_expired")
                elif event == "stopped":
                    self._expected_stop = True
                elif event == "crash":
                    self._expected_stop = True
                    self._emit_resource("crash", reason="worker_exception")
        except (EOFError, OSError):
            if not self._expected_stop:
                self._emit_resource("crash", reason="worker_pipe_closed")
        finally:
            self._ready_event.set()
            with self._condition:
                self._condition.notify_all()

    def _emit_resource(self, transition: str, *, reason: str = "") -> None:
        process = self._process
        pid = int(getattr(process, "pid", 0) or 0)
        try:
            alive = bool(process is not None and process.is_alive())
        except Exception:
            alive = False
        try:
            from core.runtime.resource_events import emit_component_resource_event

            emit_component_resource_event(
                "desktop_whisper",
                transition,
                pids=(pid,) if pid and alive else (),
                elapsed_seconds=(time.monotonic() - self._started_at if self._started_at else 0.0),
                reason=reason,
            )
        except Exception:
            pass

    def transcribe(
        self,
        audio: Any,
        sample_rate: int = 16000,
        *,
        initial_prompt: str = "",
    ) -> str:
        max_samples = int(max(1, sample_rate) * self._max_audio_seconds)
        try:
            if int(getattr(audio, "size", len(audio))) > max_samples:
                audio = audio[:max_samples]
        except Exception:
            pass
        for attempt in range(2):
            result = self._transcribe_once(audio, sample_rate, initial_prompt=initial_prompt)
            if result is not None:
                return result
            self._stop_worker(reason="crash_recovery")
            if attempt == 0:
                continue
        text = "[STT unavailable: Whisper worker did not respond]"
        self.last_evidence = TranscriptionEvidence(text, True, "stt-worker-unavailable")
        return text

    def _transcribe_once(
        self,
        audio: Any,
        sample_rate: int,
        *,
        initial_prompt: str = "",
    ) -> str | None:
        if not self.start():
            return None
        if not self._ready_event.wait(self._request_timeout_seconds):
            return None
        if not self._worker_ok:
            text = "[STT unavailable: Whisper worker could not load the model]"
            self.last_evidence = TranscriptionEvidence(text, True, "stt-unavailable")
            return text
        request_id = uuid.uuid4().hex
        connection = self._connection
        try:
            if self._cancel_event is not None:
                self._cancel_event.clear()
            connection.send({
                "op": "transcribe",
                "request_id": request_id,
                "audio": audio,
                "sample_rate": int(sample_rate),
                "initial_prompt": " ".join(str(initial_prompt or "").split())[-320:],
            })
        except (BrokenPipeError, EOFError, OSError):
            return None
        deadline = time.monotonic() + self._request_timeout_seconds
        with self._condition:
            while request_id not in self._results and self.running:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                self._condition.wait(remaining)
            message = self._results.pop(request_id, None)
        if not isinstance(message, dict):
            return None
        if message.get("cancelled"):
            self.last_evidence = TranscriptionEvidence("", True, "stt-cancelled")
            return ""
        text = str(message.get("text") or "")
        self.last_evidence = TranscriptionEvidence(
            text,
            bool(message.get("uncertain")),
            str(message.get("reason") or ""),
        )
        return text

    def cancel(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()

    def close(self) -> None:
        self._stop_worker(reason="requested")

    def _stop_worker(self, *, reason: str) -> None:
        with self._controller_lock:
            process = self._process
            connection = self._connection
            self._expected_stop = True
            if process is not None and process.is_alive() and connection is not None:
                try:
                    connection.send({"op": "stop"})
                except (BrokenPipeError, EOFError, OSError):
                    pass
                process.join(timeout=4.0)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=1.0)
            if process is not None:
                self._emit_resource("stop", reason=reason)
            self._close_handles()

    def _close_handles(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
        reader, self._reader = self._reader, None
        if (
            reader is not None
            and reader is not threading.current_thread()
            and reader.is_alive()
        ):
            reader.join(timeout=0.25)
        process, self._process = self._process, None
        if process is not None:
            try:
                process.join(timeout=0)
                process.close()
            except Exception:
                pass
        self._cancel_event = None
        self._worker_ok = False
        self._ready_event.clear()


class _SapiRecognitionEvents:
    """win32com event sink for SAPI file recognition."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.done = False
        self.error = ""

    def OnRecognition(self, *_args: Any) -> None:
        try:
            result = _args[-1] if _args else None
            text = result.PhraseInfo.GetText() if result is not None else ""
            if text:
                self.parts.append(str(text).strip())
        except Exception as exc:
            self.error = str(exc) or exc.__class__.__name__

    def OnEndStream(self, *_args: Any) -> None:
        self.done = True

    def OnRecognitionForOtherContext(self, *_args: Any) -> None:
        self.done = True

    def text(self) -> str:
        return " ".join(part for part in self.parts if part).strip()


class WindowsSpeechRecognizer:
    """Speech-to-text using the Windows default SAPI recognizer.

    MO records manual voice audio through the existing recorder, then feeds
    the captured WAV into SAPI. That keeps the same visual-only MO Desktop
    flow and avoids adding a new dependency: pywin32 is already part of the
    optional Windows desktop extras.
    """

    engine = "windows"

    def __init__(self, timeout_seconds: float = 15.0, *, temp_dir: str | Path | None = None) -> None:
        self._timeout_seconds = max(1.0, float(timeout_seconds or 15.0))
        self._load_error: str | None = None
        # Captured speech is operator data: keep the scratch WAV inside the
        # private voice root when it exists, like every other voice temp file.
        self._temp_dir = Path(temp_dir) if temp_dir else None
        self.last_evidence = TranscriptionEvidence()

    def warm(self) -> bool:
        """SAPI loads with the OS; nothing to prewarm."""
        return self.available

    @property
    def available(self) -> bool:
        if self._load_error:
            return False
        if sys.platform != "win32":
            self._load_error = "Windows Speech recognition requires Windows"
            return False
        try:
            import pythoncom  # noqa: F401
            import win32com.client  # noqa: F401
            return True
        except ImportError:
            self._load_error = "pywin32 not installed"
            return False

    def transcribe(
        self,
        audio: Any,
        sample_rate: int = 16000,
        *,
        initial_prompt: str = "",
    ) -> str:
        if isinstance(audio, str):
            text = audio.strip()
            self.last_evidence = TranscriptionEvidence(text)
            return text
        if not self.available:
            text = f"[STT unavailable: {self._load_error}]"
            self.last_evidence = TranscriptionEvidence(text, True, "stt-unavailable")
            return text
        path: Path | None = None
        try:
            path = self._write_wave_file(audio, sample_rate=sample_rate)
            if path is None:
                self.last_evidence = TranscriptionEvidence()
                return ""
            text = self._recognize_wave_file(path)
            self.last_evidence = TranscriptionEvidence(text)
            return text
        except Exception as exc:
            traceback.print_exc()
            text = f"[STT error: {exc}]"
            self.last_evidence = TranscriptionEvidence(text, True, "stt-error")
            return text
        finally:
            if path is not None:
                try:
                    path.unlink(missing_ok=True)
                except Exception:
                    pass

    def _write_wave_file(self, audio: Any, *, sample_rate: int) -> Path | None:
        import numpy as np
        import wave

        waveform = np.asarray(audio, dtype="float32").reshape(-1)
        if waveform.size == 0:
            return None
        clipped = np.clip(waveform, -1.0, 1.0)
        pcm = (clipped * 32767.0).astype("<i2").tobytes()
        scratch = self._temp_dir if self._temp_dir and self._temp_dir.is_dir() else None
        handle = tempfile.NamedTemporaryFile(
            prefix="mo-desktop-stt-", suffix=".wav", delete=False,
            dir=str(scratch) if scratch else None,
        )
        path = Path(handle.name)
        handle.close()
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(int(sample_rate or 16000))
            wav.writeframes(pcm)
        return path

    def _recognize_wave_file(self, path: Path) -> str:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        stream = None
        grammar = None
        try:
            recognizer = win32com.client.Dispatch("SAPI.SpInprocRecognizer")
            stream = win32com.client.Dispatch("SAPI.SpFileStream")
            stream.Open(str(path), 0, False)  # SSFMOpenForRead
            recognizer.AudioInputStream = stream
            context = recognizer.CreateRecoContext()
            events = win32com.client.WithEvents(context, _SapiRecognitionEvents)
            grammar = context.CreateGrammar()
            grammar.DictationLoad()
            grammar.DictationSetState(1)  # SGDSActive
            deadline = time.time() + self._timeout_seconds
            while time.time() < deadline and not getattr(events, "done", False):
                pythoncom.PumpWaitingMessages()
                time.sleep(0.03)
            text = events.text()
            if text:
                return text
            if getattr(events, "error", ""):
                return f"[STT error: {events.error}]"
            return ""
        finally:
            try:
                if grammar is not None:
                    grammar.DictationSetState(0)
            except Exception:
                pass
            try:
                if stream is not None:
                    stream.Close()
            except Exception:
                pass
            pythoncom.CoUninitialize()


def configured_stt_engine(config: dict[str, Any] | None = None) -> str:
    """Return the canonical configured input engine name."""
    cfg = config or {}
    engine = str(cfg.get("stt_engine") or cfg.get("engine") or DEFAULT_PREFERENCES["mo_desktop.voice.stt_engine"]).strip().lower()
    return "windows" if engine in _WINDOWS_STT_ENGINES else "whisper"


def make_voice_recognizer(
    config: dict[str, Any] | None = None,
) -> WhisperWorkerRecognizer | WindowsSpeechRecognizer:
    """Create the configured STT recognizer.

    `whisper` remains the default because it is offline and cross-platform.
    `windows` uses the OS speech recognizer through pywin32/SAPI.
    """
    cfg = config or {}
    activate_voice_dependencies(cfg)

    def _float_cfg(key: str, default: float) -> float:
        try:
            return float(cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    def _int_cfg(key: str, default: int) -> int:
        try:
            return int(cfg.get(key, default))
        except (TypeError, ValueError):
            return default

    if configured_stt_engine(cfg) == "windows":
        return WindowsSpeechRecognizer(
            timeout_seconds=_float_cfg("stt_timeout_seconds", 15.0),
            temp_dir=voice_scratch_dir(cfg),
        )

    defaults = ConfidenceLimits()
    max_audio_seconds = max(1.0, min(300.0, _float_cfg("max_seconds", 30.0)))
    configured_idle = max(15.0, min(900.0, _float_cfg("stt_idle_seconds", DEFAULT_PREFERENCES["mo_desktop.voice.stt_idle_seconds"])))
    return WhisperWorkerRecognizer(
        model_size=cfg.get("stt_model", DEFAULT_PREFERENCES["mo_desktop.voice.stt_model"]),
        device=cfg.get("stt_device", DEFAULT_PREFERENCES["mo_desktop.voice.stt_device"]),
        compute_type=cfg.get("stt_compute_type", "int8"),
        beam_size=_int_cfg("stt_beam_size", DEFAULT_PREFERENCES["mo_desktop.voice.stt_beam_size"]),
        confidence=ConfidenceLimits(
            min_avg_logprob=_float_cfg("stt_min_avg_logprob", defaults.min_avg_logprob),
            max_no_speech=_float_cfg("stt_max_no_speech", defaults.max_no_speech),
            max_compression=_float_cfg("stt_max_compression", defaults.max_compression),
            min_language_probability=_float_cfg(
                "stt_min_language_probability", defaults.min_language_probability
            ),
        ),
        idle_seconds=max(configured_idle, max_audio_seconds + 30.0),
        request_timeout_seconds=max(
            15.0,
            min(600.0, _float_cfg("stt_worker_timeout_seconds", 180.0)),
        ),
        max_audio_seconds=max_audio_seconds,
    )


def voice_scratch_dir(config: dict[str, Any] | None = None) -> Path | None:
    """The private voice temp directory when it already exists, else None.

    Resolution must never break voice construction, and it never creates the
    layout — installation is the only thing allowed to do that.
    """
    try:
        scratch = voice_layout(resolve_voice_install_root(config or {}))["tmp"]
    except Exception:
        return None
    return scratch if scratch.is_dir() else None


def make_voice_recorder(config: dict[str, Any] | None = None) -> "PushToTalkRecorder":
    """Create the configured voice recorder.

    The recorder is separate from recognizer selection: STT may be Whisper or
    Windows SAPI, but mic capture uses the same local recorder. Keep defaults
    conservative so brief background noise is less likely to become a request.
    """
    cfg = config or {}
    activate_voice_dependencies(cfg)

    def _float_cfg(key: str, default: float, low: float, high: float) -> float:
        try:
            value = float(cfg.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))

    def _int_cfg(key: str, default: int, low: int, high: int) -> int:
        try:
            value = int(cfg.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(low, min(high, value))

    return PushToTalkRecorder(
        sample_rate=_int_cfg("sample_rate", 16000, 8000, 48000),
        max_seconds=_float_cfg("max_seconds", 30.0, 1.0, 300.0),
        silence_threshold=_float_cfg("silence_threshold", 0.014, 0.001, 0.2),
        silence_hangover=_float_cfg("silence_hangover", 1.2, 0.4, 5.0),
        adaptive_endpointing=bool(cfg.get("adaptive_endpointing", False)),
    )


# ------------------------------------------------------------------
# Voice recorder
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
        silence_threshold: float = 0.014,
        silence_hangover: float = 1.2,   # short pause after you finish speaking
        adaptive_endpointing: bool = False,
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
        self.adaptive_endpointing = bool(adaptive_endpointing)
        # The configured baseline has to survive per-capture overrides. Voice Chat
        # switches endpointing on for its own turns; an operator who enabled it in
        # config keeps it for manual voice capture too, instead of having the value
        # silently overwritten before it is ever read.
        self.configured_adaptive_endpointing = bool(adaptive_endpointing)
        # Voice Chat can set this per capture. Manual capture leaves it off.
        self.initial_silence_timeout: float | None = None
        self._speech_started = False
        self._speech_seconds = 0.0
        self._silence_run = 0.0  # seconds of quiet accumulated since the last speech
        self.level = 0.0  # smoothed live RMS (0..~0.3); the cube reads this to react

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
            self._speech_seconds = 0.0
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
                # Flatten (frames, 1) mono chunks to a 1-D waveform — faster-whisper
                # requires 1-D; a (samples, 1) array blows up its mel allocation.
                audio = np.concatenate(self._buffer, axis=0).reshape(-1)
                self._buffer = []
                # Trim to max_seconds
                max_samples = int(self._sample_rate * self._max_seconds)
                if len(audio) > max_samples:
                    audio = audio[:max_samples]
                if not self._speech_started:
                    try:
                        rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
                    except Exception:
                        rms = 0.0
                    if rms < self._silence_threshold:
                        return None
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
        trailing_silence = self._is_trailing_silence(chunk)
        initial_timeout = self.initial_silence_timeout
        initial_silence_expired = bool(
            initial_timeout
            and not self._speech_started
            and self._collected >= int(self._sample_rate * initial_timeout)
        )
        if trailing_silence or initial_silence_expired:
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
        # Smoothed live level so the cube can pulse with the operator's voice.
        self.level = 0.8 * self.level + 0.2 * rms
        duration = len(chunk) / float(self._sample_rate or 16000)
        if rms >= self._silence_threshold:
            self._speech_started = True
            self._speech_seconds += duration
            self._silence_run = 0.0
            return False
        if not self._speech_started:
            return False  # still waiting for the first word — don't arm yet
        self._silence_run += duration
        hangover = self._silence_hangover
        if self.adaptive_endpointing:
            # A long thought gets more room for an internal pause. This is
            # deliberately duration-aware, not a claim to understand semantics.
            hangover += min(0.8, self._speech_seconds * 0.08)
        return self._silence_run >= hangover


# ------------------------------------------------------------------
# Voice integration helper
# ------------------------------------------------------------------

class CompanionVoice:
    """Ties speech recognition and recording into one voice-input component.

    Integrate into CompanionSurface for manual and Voice Chat input.
    """

    def __init__(
        self,
        recognizer: (
            VoiceRecognizer | WhisperWorkerRecognizer | WindowsSpeechRecognizer | None
        ) = None,
        recorder: PushToTalkRecorder | None = None,
    ) -> None:
        self.recognizer = recognizer
        self.recorder = recorder or (PushToTalkRecorder() if recognizer is not None else None)
        self.last_transcription_evidence: TranscriptionEvidence | None = None

    def warm(self) -> bool:
        """Prepare the recognizer before the first capture. Call off the GUI thread."""
        warmer = getattr(self.recognizer, "warm", None)
        if not callable(warmer):
            return False
        try:
            return bool(warmer())
        except Exception:
            return False

    def prepare_recognizer(self) -> bool:
        """Demand-start a worker without waiting so capture overlaps model load."""
        starter = getattr(self.recognizer, "start", None)
        if not callable(starter):
            return True
        try:
            return bool(starter())
        except Exception:
            return False

    @property
    def recording_configured(self) -> bool:
        return self.recorder is not None

    @property
    def recording_available(self) -> bool:
        return bool(self.recorder and self.recorder.available)

    def start_recording(self) -> bool:
        """Begin voice capture. GUI-driven — no blocking input()."""
        if not self.recording_available:
            return False
        return bool(self.recorder and self.recorder.start())

    def stop_and_transcribe(self, *, initial_prompt: str = "") -> str:
        """Stop capture (closes the mic — never left open) and transcribe."""
        if self.recorder is None or self.recognizer is None:
            self.last_transcription_evidence = TranscriptionEvidence(
                "[Voice input not available]", True, "voice-unavailable"
            )
            return "[Voice input not available]"
        audio = self.recorder.stop()
        if audio is None:
            self.last_transcription_evidence = TranscriptionEvidence()
            return ""
        if not self.recognizer.available:
            self.last_transcription_evidence = TranscriptionEvidence(
                "[Voice input not available]", True, "stt-unavailable"
            )
            return "[Voice input not available]"
        prompt = " ".join(str(initial_prompt or "").split())[-320:]
        text = (
            self.recognizer.transcribe(audio, initial_prompt=prompt)
            if prompt
            else self.recognizer.transcribe(audio)
        )
        evidence = getattr(self.recognizer, "last_evidence", None)
        self.last_transcription_evidence = evidence if isinstance(evidence, TranscriptionEvidence) else None
        return text

    def close(self) -> None:
        if self.recorder is not None:
            self.recorder.stop()
        closer = getattr(self.recognizer, "close", None)
        if callable(closer):
            closer()
