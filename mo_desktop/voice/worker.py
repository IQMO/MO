"""Piper TTS subprocess for MO Desktop.

This module is launched with the optional voice environment's Python.  It owns
the heavy model imports and emits small base64 PCM chunks over stdout; MO owns
playback, cancellation, settings, and every user-facing decision.
"""
from __future__ import annotations

import base64
import json
import math
import os
from pathlib import Path
import queue
import sys
import threading
from typing import Any


def _emit(event: str, **fields: Any) -> None:
    print(json.dumps({"event": event, **fields}, separators=(",", ":")), flush=True)


def _is_arabic_text(text: str) -> bool:
    """True when Arabic letters outnumber Latin ones (mixed lines keep their main script)."""
    arabic = sum(1 for char in text if "\u0600" <= char <= "\u06ff")
    latin = sum(1 for char in text if char.isascii() and char.isalpha())
    return arabic > latin


def _read_commands(commands: queue.Queue[dict[str, Any]], epoch: list[int]) -> None:
    for raw in sys.stdin:
        try:
            command = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(command, dict):
            continue
        name = str(command.get("command") or "").strip().lower()
        if name in {"cancel", "shutdown", "speak"}:
            epoch[0] += 1
        # An ``append`` continues the current utterance: it shares the epoch, so
        # only a cancel or a new ``speak`` interrupts it.
        command["_epoch"] = epoch[0]
        commands.put(command)
    # MO closed the pipe (it exited or crashed): stop instead of idling as an orphan.
    epoch[0] += 1
    commands.put({"command": "shutdown", "_epoch": epoch[0]})


def main() -> int:
    from piper import PiperVoice, SynthesisConfig

    commands: queue.Queue[dict[str, Any]] = queue.Queue()
    epoch = [0]
    threading.Thread(
        target=_read_commands,
        args=(commands, epoch),
        name="mo-voice-worker-input",
        daemon=True,
    ).start()

    try:
        model_path = Path(os.environ["MO_VOICE_MODEL"])
        if not model_path.is_absolute() or not model_path.is_file():
            raise FileNotFoundError("MO_VOICE_MODEL")
        model = PiperVoice.load(model_path)
    except Exception as exc:
        _emit("fatal", error=type(exc).__name__)
        return 2
    arabic_model = None
    if os.environ.get("MO_VOICE_MODEL_AR"):
        try:
            arabic_model = PiperVoice.load(Path(os.environ["MO_VOICE_MODEL_AR"]))
            if int(arabic_model.config.sample_rate) != int(model.config.sample_rate):
                arabic_model = None  # one playback stream; a different rate cannot share it
        except Exception:
            arabic_model = None
        _emit("arabic", available=arabic_model is not None)
    _emit("ready", sample_rate=int(model.config.sample_rate))
    if "--prepare" in sys.argv[1:]:
        return 0
    clone = None
    if os.environ.get("MO_VOICE_CLONE"):
        from mo_desktop.voice.clone import CloneServer

        clone = CloneServer(json.loads(os.environ["MO_VOICE_CLONE"]), sample_rate=int(model.config.sample_rate))
        clone.start(on_state=lambda state, error: _emit("clone", state=state, error=error))
        _emit("clone", state="loading", error="")

    while True:
        command = commands.get()
        name = str(command.get("command") or "").strip().lower()
        if name == "shutdown":
            if clone is not None:
                clone.close()
            return 0
        if name not in {"speak", "append"}:
            continue
        request_id = str(command.get("id") or "")
        text = str(command.get("text") or "").strip()
        if not request_id or not text:
            continue
        request_epoch = int(command.get("_epoch") or 0)
        try:
            speed = command.get("speed", 1.0)
            if (
                isinstance(speed, bool)
                or not isinstance(speed, (int, float))
                or not math.isfinite(speed)
                or not 0.5 <= speed <= 2.0
            ):
                raise ValueError("voice speed must be a finite number from 0.5 to 2.0")
            _emit("started", id=request_id)
            cancelled = False
            voice = arabic_model if arabic_model is not None and _is_arabic_text(text) else model
            chunks = voice.synthesize(text) if speed == 1.0 else voice.synthesize(
                text, syn_config=SynthesisConfig(length_scale=voice.config.length_scale / speed),
            )
            for chunk in chunks:
                if epoch[0] != request_epoch:
                    cancelled = True
                    break
                audio = chunk.audio_int16_bytes
                if clone is not None and clone.ready:
                    # One sentence at a time in the user's own voice; the plain
                    # voice speaks a sentence the clone could not convert.
                    audio = clone.convert(audio) or audio
                    if epoch[0] != request_epoch:
                        cancelled = True
                        break
                _emit("audio", id=request_id, data=base64.b64encode(audio).decode("ascii"))
            _emit("done", id=request_id, cancelled=cancelled or epoch[0] != request_epoch)
        except Exception as exc:
            _emit("error", id=request_id, error=type(exc).__name__)


if __name__ == "__main__":
    raise SystemExit(main())
