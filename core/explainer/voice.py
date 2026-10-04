"""Narration synthesis through MO's existing optional Piper runtime."""
from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import uuid
import wave
from typing import Any, Callable, Mapping, TextIO

from core.runtime.subprocess_flags import apply_windows_hidden_process_flags
from core.tooling.shell_processes import kill_process_tree
from core.utils.atomic_write import atomic_write_text
from core.utils.file_hash import file_sha256_or_empty
from mo_desktop.voice.storage import (
    resolve_voice_install_root,
    voice_model_path,
    voice_process_environment,
    voice_runtime_ready,
    worker_python,
)

from .model import ExplainerProject, narration_digest
from .storage import sweep_stale_stages


def synthesize_narration(
    project: ExplainerProject,
    *,
    config: Mapping[str, Any] | None = None,
    cancel_event: object = None,
    progress: Callable[[int, int], None] | None = None,
    event_timeout_seconds: float = 120.0,
) -> tuple[Path, Path]:
    """Write one narration-bound WAV/timing pair using MO's installed voice."""
    speed = float((project.data.get("voice") or {}).get("speed", 1.0))
    root = resolve_voice_install_root(config)
    if not voice_runtime_ready(root):
        raise RuntimeError("MO voice is not installed; install optional MO Desktop voice or provide audio.wav")
    python = worker_python(root)
    model = voice_model_path(root)
    env = voice_process_environment(root)
    env["MO_VOICE_MODEL"] = str(model)
    product_root = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = product_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")

    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stderr_file:
        kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": stderr_file,
            "text": True,
            "encoding": "utf-8",
            "bufsize": 1,
            "cwd": product_root,
            "env": env,
        }
        apply_windows_hidden_process_flags(kwargs)
        process = subprocess.Popen([str(python), "-m", "mo_desktop.voice.worker"], **kwargs)
        if process.stdin is None or process.stdout is None:
            _terminate_process(process)
            raise RuntimeError("MO voice worker did not expose its streams")
        events = _event_queue(process.stdout)
        try:
            ready = _next_event(
                process,
                events,
                wanted={"ready", "fatal"},
                stderr_file=stderr_file,
                cancel_event=cancel_event,
                timeout_seconds=event_timeout_seconds,
            )
            if ready.get("event") != "ready":
                raise RuntimeError(f"MO voice worker could not load its model: {_stderr_tail(stderr_file)}")
            sample_rate = int(ready.get("sample_rate") or 22050)
            if sample_rate <= 0:
                raise RuntimeError("MO voice worker returned an invalid sample rate")
            pcm_parts: list[bytes] = []
            timings: list[dict[str, Any]] = []
            cursor_samples = 0
            lead_samples = int(sample_rate * 0.20)
            pcm_parts.append(b"\0\0" * lead_samples)
            cursor_samples += lead_samples
            total_scenes = len(project.scenes)
            if progress is not None:
                progress(0, total_scenes)
            for index, scene in enumerate(project.scenes):
                if _cancel_requested(cancel_event):
                    raise RuntimeError("explainer narration cancelled")
                narration = str(scene.get("narration") or "").strip()
                request_id = uuid.uuid4().hex
                speech = bytearray()
                if narration:
                    command = {"command": "speak", "id": request_id, "text": narration}
                    if speed != 1.0:
                        command["speed"] = speed
                    process.stdin.write(json.dumps(command, separators=(",", ":")) + "\n")
                    process.stdin.flush()
                while narration:
                    event = _next_event(
                        process,
                        events,
                        wanted={"audio", "done", "error", "fatal"},
                        stderr_file=stderr_file,
                        cancel_event=cancel_event,
                        timeout_seconds=event_timeout_seconds,
                    )
                    if str(event.get("id") or "") not in {"", request_id}:
                        continue
                    name = event.get("event")
                    if name == "audio":
                        try:
                            speech.extend(base64.b64decode(str(event.get("data") or ""), validate=True))
                        except (binascii.Error, ValueError) as exc:
                            raise RuntimeError("MO voice worker returned invalid audio") from exc
                    elif name == "done" and event.get("cancelled") is not True:
                        break
                    elif name == "done":
                        raise RuntimeError("MO voice synthesis was cancelled")
                    else:
                        raise RuntimeError(f"MO voice synthesis failed: {name}")
                if narration and (not speech or len(speech) % 2):
                    raise RuntimeError(f"MO voice returned invalid PCM for scene {scene['id']}")
                speech_samples = len(speech) // 2
                configured_samples = int(float(scene.get("duration", 6.0)) * sample_rate)
                scene_samples = max(configured_samples, speech_samples + int(sample_rate * 0.65))
                start = cursor_samples / sample_rate
                pcm_parts.append(bytes(speech))
                padding = max(0, scene_samples - speech_samples)
                pcm_parts.append(b"\0\0" * padding)
                cursor_samples += scene_samples
                timings.append({
                    "id": scene["id"],
                    "start": round(start, 6),
                    "duration": round(scene_samples / sample_rate, 6),
                    "speech_duration": round(speech_samples / sample_rate, 6),
                })
                if progress is not None:
                    progress(index + 1, total_scenes)
        finally:
            _shutdown_process(process)

    audio_path = project.directory / "audio.wav"
    timing_path = project.directory / "timings.json"
    sweep_stale_stages(project.directory, (".audio.*.stage.wav", ".timings.*.stage.json"))
    audio_stage = audio_path.with_name(f".audio.{uuid.uuid4().hex}.stage.wav")
    timing_stage = timing_path.with_name(f".timings.{uuid.uuid4().hex}.stage.json")
    try:
        with wave.open(str(audio_stage), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(sample_rate)
            target.writeframes(b"".join(pcm_parts))
        audio_sha256 = file_sha256_or_empty(audio_stage)
        timing_payload = {
            "version": 2,
            "sample_rate": sample_rate,
            "audio_source": "piper",
            "voice_speed": speed,
            "audio_sha256": audio_sha256,
            "narration_sha256": narration_digest(project),
            "duration_seconds": round(cursor_samples / sample_rate, 6),
            "scenes": timings,
        }
        atomic_write_text(timing_stage, json.dumps(timing_payload, indent=2, ensure_ascii=False) + "\n")
        os.replace(audio_stage, audio_path)
        os.replace(timing_stage, timing_path)
    finally:
        audio_stage.unlink(missing_ok=True)
        timing_stage.unlink(missing_ok=True)
    return audio_path, timing_path


def _event_queue(stream: TextIO) -> queue.Queue[dict[str, Any] | None]:
    events: queue.Queue[dict[str, Any] | None] = queue.Queue()

    def _drain() -> None:
        try:
            while True:
                line = stream.readline()
                if not line:
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    events.put(event)
        except (OSError, ValueError):
            pass
        finally:
            events.put(None)

    threading.Thread(target=_drain, name="mo-explainer-voice-events", daemon=True).start()
    return events


def _next_event(
    process: subprocess.Popen[str],
    events: queue.Queue[dict[str, Any] | None],
    *,
    wanted: set[str],
    stderr_file: TextIO,
    cancel_event: object,
    timeout_seconds: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + max(1.0, float(timeout_seconds))
    while True:
        if _cancel_requested(cancel_event):
            raise RuntimeError("explainer narration cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"MO voice worker timed out{_detail_suffix(stderr_file)}")
        try:
            event = events.get(timeout=min(0.1, remaining))
        except queue.Empty:
            continue
        if event is None:
            raise RuntimeError(f"MO voice worker exited unexpectedly{_detail_suffix(stderr_file)}")
        if str(event.get("event") or "") in wanted:
            return event


def _cancel_requested(cancel_event: object) -> bool:
    return bool(getattr(cancel_event, "is_set", lambda: False)())


def _stderr_tail(stderr_file: TextIO, limit: int = 300) -> str:
    try:
        stderr_file.flush()
        stderr_file.seek(0)
        return stderr_file.read().strip()[-limit:]
    except (OSError, ValueError):
        return ""


def _detail_suffix(stderr_file: TextIO) -> str:
    detail = _stderr_tail(stderr_file)
    return f": {detail}" if detail else ""


def _terminate_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        kill_process_tree(process.pid)
    try:
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _shutdown_process(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if process.stdin is not None:
            process.stdin.write('{"command":"shutdown"}\n')
            process.stdin.flush()
        process.wait(timeout=3)
    except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
        _terminate_process(process)
