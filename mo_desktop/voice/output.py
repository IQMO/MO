"""Light controller for MO Desktop's isolated streaming speech worker."""
from __future__ import annotations

import base64
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import uuid
from typing import Any, Callable, Mapping

from core.runtime.subprocess_flags import apply_windows_hidden_process_flags
from .storage import (
    activate_voice_dependencies,
    resolve_voice_install_root,
    voice_model_path,
    voice_process_environment,
    voice_runtime_ready,
    worker_python,
)


VoiceEvent = Callable[[str], None]


def normalize_speech_rate(value: Any = 1.0) -> float:
    """Return a finite Piper speed multiplier in the worker's supported range."""
    try:
        if isinstance(value, bool):
            return 1.0
        speed = float(value)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(speed):
        return 1.0
    return max(0.5, min(2.0, speed))


# Piper emits one chunk per sentence, so a chunk can be seconds long. Playback
# writes it in small slices instead, which is what bounds stop-to-silence: a
# cancel is honoured at the next slice boundary rather than at the next sentence.
_PLAYBACK_SLICE_FRAMES = 1024      # ~46 ms at 22.05 kHz
_PCM_FRAME_BYTES = 2               # int16 mono
_AUDIO_QUEUE_CHUNKS = 8


def prepare_spoken_text(text: str, *, max_chars: int = 320) -> str:
    """Return a short, speech-friendly projection without changing the visible reply."""
    clean = speech_safe_text(text)
    if not clean:
        return "I put the details in the bubble."
    if len(clean) <= max_chars:
        return clean

    suffix = " The full answer is in the bubble."
    limit = max(80, max_chars - len(suffix))
    sentences = re.split(r"(?<=[.!?])\s+", clean)
    spoken = ""
    for sentence in sentences:
        candidate = f"{spoken} {sentence}".strip()
        if len(candidate) > limit:
            break
        spoken = candidate
    if not spoken:
        spoken = clean[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return spoken + suffix


def speech_safe_text(text: str) -> str:
    """Drop code, commands, URLs, paths and Markdown so only speakable words remain."""
    clean = str(text or "").strip()
    clean = re.sub(r"```.*?```", " ", clean, flags=re.DOTALL)
    clean = re.sub(r"\[([^]]+)]\([^)]+\)", r"\1", clean)
    clean = re.sub(r"https?://\S+", " ", clean)
    clean = re.sub(r"(?m)^\s*`[^`\n]+`\s*$", " ", clean)
    clean = re.sub(
        r"(?im)^\s*(?:#{1,6}\s+|(?:[-*+]|\d+[.)])\s+)?"
        r"(?:run|execute|type|enter)\s+`?(?:python(?:\.exe)?|py|git|pip|pytest|"
        r"powershell|pwsh|cmd|curl)\b.*$",
        " ",
        clean,
    )
    clean = re.sub(r"`[^`\n]+`", " the detail shown in the bubble ", clean)
    clean = re.sub(
        r"(?im)^\s*(?:#{1,6}\s+|(?:[-*+]|\d+[.)])\s+)?"
        r"(?:[$>]\s*|PS\s+[^>\n]*>\s*|/[-a-z][\w-]*(?:\s|$)|"
        r"(?:python(?:\.exe)?|py|git|pip|pytest|powershell|pwsh|cmd|curl)\b).*$",
        " ",
        clean,
    )
    clean = re.sub(
        r"(?i)(?<!\w)(?:[a-z]:[\\/]|\\\\|\.\.?[\\/])[^\s,;:!?]*[a-z0-9_)}\]]",
        " the path shown in the bubble ",
        clean,
    )
    clean = re.sub(
        r"(?<!\w)/(?:[\w.-]+/)+[\w.-]*[\w)}\]]",
        " the path shown in the bubble ",
        clean,
    )
    clean = re.sub(r"(?m)^\s*\|.*\|\s*$", " ", clean)
    clean = re.sub(r"(?m)^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+)", "", clean)
    clean = re.sub(r"[*_~]+", "", clean)
    clean = re.sub(r"(?:\s*the detail shown in the bubble\s*){2,}", " the details shown in the bubble ", clean)
    clean = re.sub(r"(?:\s*the path shown in the bubble\s*){2,}", " the paths shown in the bubble ", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return re.sub(r"\s+([,.;:!?])", r"\1", clean)


def spoken_max_chars(config: Mapping[str, Any] | None = None, *, default: int = 320) -> int:
    """Clamp the configured spoken projection length (`voice.spoken_max_chars`).

    Manual voice input wants a short confirmation; Voice Chat may want a whole answer.
    The visible reply is never affected either way.
    """
    try:
        value = int((config or {}).get("spoken_max_chars", default))
    except (TypeError, ValueError):
        value = default
    return max(80, min(4000, value))


class SpeechOutput:
    """Start, feed, interrupt, and close one persistent Piper TTS worker."""

    def __init__(
        self,
        config: Mapping[str, Any] | None = None,
        *,
        device: str | int | None = None,
        on_event: VoiceEvent | None = None,
    ) -> None:
        self._config = dict(config or {})
        self._root = resolve_voice_install_root(self._config)
        activate_voice_dependencies(self._config)
        requested_device = self._config.get("output_device") if device is None else device
        self._device = requested_device if requested_device not in {"", "default", None} else None
        self._on_event = on_event
        self._process: subprocess.Popen[str] | None = None
        self._stream: Any = None
        self._request_id = ""
        self._lock = threading.RLock()
        self._reader: threading.Thread | None = None
        self._closed = False
        self._restart_count = 0
        # One thread owns the audio device. The event reader only decodes and
        # hands off, so nothing ever aborts a stream another thread is writing.
        # ``(generation, bytes)`` is PCM, ``(generation, None)`` completes one
        # request after its PCM drains, and bare ``None`` retires the player.
        self._audio: queue.Queue[tuple[int, bytes | None] | None] = queue.Queue(
            maxsize=_AUDIO_QUEUE_CHUNKS
        )
        self._player: threading.Thread | None = None
        self._generation = 0
        self._speaking_generation = -1
        self._sample_rate = 0
        # The open utterance: speed, clauses sent, clauses still synthesizing,
        # and whether more may follow (see ``begin``/``say``/``finish``).
        self._utterance: dict[str, Any] | None = None

    @property
    def installed(self) -> bool:
        return voice_runtime_ready(self._root)

    @property
    def running(self) -> bool:
        process = self._process
        return bool(process is not None and process.poll() is None)

    def start(self) -> bool:
        """Warm the worker asynchronously; heavy imports remain in its process."""
        with self._lock:
            self._closed = False
            if self.running:
                return True
            if not self.installed:
                self._notify("unavailable")
                return False
            python = self._python_path()
            env = voice_process_environment(self._root)
            env["MO_VOICE_MODEL"] = str(self._model_path())
            product_root = str(Path(__file__).resolve().parents[2])
            env["PYTHONPATH"] = product_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
            try:
                popen_kwargs = {
                    "stdin": subprocess.PIPE, "stdout": subprocess.PIPE,
                    "stderr": subprocess.DEVNULL, "text": True,
                    "encoding": "utf-8", "bufsize": 1,
                    "cwd": product_root, "env": env,
                }
                apply_windows_hidden_process_flags(popen_kwargs)
                self._process = subprocess.Popen(
                    [str(python), "-m", "mo_desktop.voice.worker"], **popen_kwargs,
                )
            except OSError:
                self._process = None
                self._notify("unavailable")
                return False
            self._reader = threading.Thread(target=self._read_events, name="mo-voice-output", daemon=True)
            self._reader.start()
            self._notify("loading")
            return True

    def speak(self, text: str, *, speed: float | None = None) -> bool:
        """Speak one complete reply, superseding anything still playing."""
        if not str(text or "").strip():
            return False
        return self.begin(speed=speed) and self.say(text) and self.finish()

    def begin(self, *, speed: float | None = None) -> bool:
        """Open one utterance whose clauses arrive later through ``say``.

        Opening supersedes anything still playing. Clauses play back to back;
        audible ``idle`` is published only after ``finish`` and the last
        clause's audio has drained.
        """
        if not self.running and not self.start():
            return False
        rate = normalize_speech_rate(
            self._config.get("speech_rate", 1.0) if speed is None else speed
        )
        with self._lock:
            self._request_id = uuid.uuid4().hex
            self._generation += 1  # anything still queued belongs to the old request
            self._utterance = {"rate": rate, "sent": 0, "pending": 0, "open": True}
        self._drain_audio()
        return True

    def say(self, text: str) -> bool:
        """Queue one clause behind the open utterance's earlier clauses."""
        clean = str(text or "").strip()
        with self._lock:
            utterance = getattr(self, "_utterance", None)
            if not clean or not utterance or not utterance["open"] or not self._request_id:
                return False
            # The first clause starts a new worker epoch (cancelling older
            # synthesis); later ones continue it.
            command = "speak" if utterance["sent"] == 0 else "append"
            utterance["sent"] += 1
            utterance["pending"] += 1
            payload = {"command": command, "id": self._request_id, "text": clean, "speed": utterance["rate"]}
        if self._send(payload):
            return True
        with self._lock:
            utterance["pending"] -= 1
        return False

    def finish(self) -> bool:
        """Close the open utterance; returns whether any clause was queued."""
        with self._lock:
            utterance = getattr(self, "_utterance", None)
            if not utterance or not utterance["open"]:
                return False
            utterance["open"] = False
            spoken = utterance["sent"] > 0
            drained = utterance["pending"] == 0
            generation = self._generation
        if spoken and drained:
            self._enqueue_done(generation)
        return spoken

    def cancel(self) -> None:
        """Stop speaking now. The device is only ever touched by the player thread."""
        with self._lock:
            self._request_id = ""
            self._generation += 1
            self._utterance = None
        self._drain_audio()
        self._send({"command": "cancel"})
        self._notify("idle")

    def close(self) -> None:
        self._closed = True
        self._request_id = ""
        self._send({"command": "shutdown"})
        self._stop_playback()
        process, self._process = self._process, None
        if process is not None:
            try:
                process.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()

    def set_device(self, device: str | int | None) -> bool:
        """Move playback to another device without reloading the speech model."""
        target = device if device not in {"", "default", None} else None
        with self._lock:
            if target == self._device:
                return True
            self._device = target
            sample_rate = self._sample_rate
            active = self._stream is not None
        if not active:
            return True
        self._stop_playback()
        if sample_rate:
            self._open_stream(sample_rate)
        return self._stream is not None

    def _python_path(self) -> Path:
        return worker_python(self._root)

    def _model_path(self) -> Path:
        return voice_model_path(self._root)

    def _send(self, payload: dict[str, Any]) -> bool:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None or process.stdin is None:
                return False
            try:
                process.stdin.write(json.dumps(payload, separators=(",", ":")) + "\n")
                process.stdin.flush()
                return True
            except (BrokenPipeError, OSError, ValueError):
                return False

    def _read_events(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for raw in process.stdout:
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            event = str(message.get("event") or "")
            request_id = str(message.get("id") or "")
            if event == "ready":
                # A worker that reached `ready` is healthy again, so the
                # one-restart budget belongs to the next unexpected exit, not to
                # the whole session.
                self._restart_count = 0
                self._open_stream(int(message.get("sample_rate") or 22050))
                self._notify("ready")
            elif event == "started" and request_id == self._request_id:
                # The worker has started synthesis; audible playback begins only
                # after the player successfully hands the first PCM slice to the
                # output device. Later clauses of an audible utterance must not
                # flip the state back to synthesizing between sentences.
                with self._lock:
                    audible = self._speaking_generation == self._generation
                if not audible:
                    self._notify("synthesizing")
            elif event == "audio" and self._stream is not None:
                with self._lock:
                    generation = self._generation if request_id == self._request_id else None
                if generation is None:
                    continue
                try:
                    self._enqueue(
                        base64.b64decode(str(message.get("data") or ""), validate=True),
                        generation=generation,
                    )
                except Exception:
                    self._notify("error")
            elif event == "done":
                with self._lock:
                    generation = self._generation if request_id == self._request_id else None
                    utterance = getattr(self, "_utterance", None)
                    if generation is not None and utterance:
                        utterance["pending"] = max(0, utterance["pending"] - 1)
                        # Only the last clause of a closed utterance completes it.
                        if utterance["open"] or utterance["pending"]:
                            generation = None
                if generation is not None:
                    # The worker has finished synthesizing, but the player still
                    # owns queued PCM. Only that thread can declare audible idle.
                    self._enqueue_done(generation)
            elif event in {"error", "fatal"}:
                self._notify("error")
        self._stop_playback()
        self._process = None
        self._request_id = ""
        if self._closed:
            return
        if not self._closed and self._restart_count < 1:
            self._restart_count += 1
            self._notify("restarting")
            if self.start():
                return
        self._notify("unavailable")

    def _open_stream(self, sample_rate: int) -> None:
        if self._stream is not None:
            return
        try:
            import sounddevice as sd

            try:
                self._stream = sd.RawOutputStream(
                    samplerate=sample_rate, channels=1, dtype="int16", device=self._device,
                )
            except Exception:
                if self._device is None:
                    raise
                self._stream = sd.RawOutputStream(samplerate=sample_rate, channels=1, dtype="int16")
                self._notify("device_fallback")
            self._stream.start()
            self._sample_rate = int(sample_rate)
        except Exception:
            self._stream = None
            self._notify("error")
            return
        self._start_playback()

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Playback (single device owner)
    # ------------------------------------------------------------------

    def _start_playback(self) -> None:
        if self._player is not None and self._player.is_alive():
            return
        self._player = threading.Thread(target=self._player_loop, name="mo-voice-playback", daemon=True)
        self._player.start()

    def _stop_playback(self) -> None:
        """Retire the player thread, then close the device it owned."""
        with self._lock:
            self._generation += 1
        self._drain_audio()
        player, self._player = self._player, None
        if player is not None and player.is_alive():
            try:
                self._audio.put_nowait(None)
            except queue.Full:
                self._drain_audio()
                try:
                    self._audio.put_nowait(None)
                except queue.Full:
                    pass
            player.join(timeout=1.5)
        self._close_stream()

    def _enqueue(self, data: bytes, *, generation: int | None = None) -> None:
        """Hand decoded PCM to the player. Blocks briefly for back-pressure —
        synthesis outruns playback, and dropping audio would clip the reply."""
        if not data:
            return
        if generation is None:
            with self._lock:
                generation = self._generation
        item = (generation, data)
        try:
            self._audio.put(item, timeout=5.0)
        except queue.Full:
            self._notify("error")

    def _enqueue_done(self, generation: int) -> None:
        """Queue synthesis completion behind the PCM for one request."""
        player = self._player
        if self._stream is None or player is None or not player.is_alive():
            with self._lock:
                if generation == self._generation:
                    self._request_id = ""
            self._notify("error")
            return
        try:
            self._audio.put((generation, None), timeout=5.0)
        except queue.Full:
            with self._lock:
                if generation == self._generation:
                    self._request_id = ""
            self._notify("error")

    def _drain_audio(self) -> None:
        while True:
            try:
                self._audio.get_nowait()
            except queue.Empty:
                return

    def _player_loop(self) -> None:
        step = _PLAYBACK_SLICE_FRAMES * _PCM_FRAME_BYTES
        while True:
            item = self._audio.get()
            if item is None:
                return
            generation, data = item
            if generation != self._generation:
                continue  # cancelled or superseded before playback reached it
            if data is None:
                self._finish_playback(generation)
                continue
            stream = self._stream
            if stream is None:
                continue
            for start in range(0, len(data), step):
                if generation != self._generation:
                    # Cancelled mid-sentence: drop what the device already holds.
                    # abort() is reached from this thread only, never across one.
                    try:
                        stream.abort()
                        stream.start()
                    except Exception:
                        self._close_stream()
                    break
                try:
                    stream.write(data[start:start + step])
                except Exception:
                    self._notify("error")
                    break
                announce_speaking = False
                with self._lock:
                    if (
                        generation == self._generation
                        and self._speaking_generation != generation
                    ):
                        self._speaking_generation = generation
                        announce_speaking = True
                if announce_speaking:
                    self._notify("speaking")

    def _finish_playback(self, generation: int) -> None:
        """Drain the device from its owner thread, then publish audible idle."""
        if generation != self._generation:
            return
        stream = self._stream
        if stream is None:
            self._notify("error")
            return
        try:
            # PortAudio stop waits for pending output. Restart keeps the warmed
            # stream ready for the next utterance without reloading the model.
            stream.stop()
            stream.start()
        except Exception:
            self._close_stream()
            with self._lock:
                if generation == self._generation:
                    self._request_id = ""
            self._notify("error")
            return
        with self._lock:
            if generation != self._generation:
                return
            self._request_id = ""
        self._notify("idle")

    def _notify(self, event: str) -> None:
        if self._on_event is not None:
            try:
                self._on_event(event)
            except Exception:
                pass
