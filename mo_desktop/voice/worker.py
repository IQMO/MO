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
            command["_epoch"] = epoch[0]
        commands.put(command)


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
    _emit("ready", sample_rate=int(model.config.sample_rate))
    if "--prepare" in sys.argv[1:]:
        return 0

    while True:
        command = commands.get()
        name = str(command.get("command") or "").strip().lower()
        if name == "shutdown":
            return 0
        if name != "speak":
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
            chunks = model.synthesize(text) if speed == 1.0 else model.synthesize(
                text, syn_config=SynthesisConfig(length_scale=model.config.length_scale / speed),
            )
            for chunk in chunks:
                if epoch[0] != request_epoch:
                    cancelled = True
                    break
                _emit("audio", id=request_id, data=base64.b64encode(chunk.audio_int16_bytes).decode("ascii"))
            _emit("done", id=request_id, cancelled=cancelled or epoch[0] != request_epoch)
        except Exception as exc:
            _emit("error", id=request_id, error=type(exc).__name__)


if __name__ == "__main__":
    raise SystemExit(main())
