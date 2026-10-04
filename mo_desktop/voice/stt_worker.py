"""Spawn-safe heavy Whisper worker entrypoint."""
from __future__ import annotations

import sys
import time
from typing import Any


def stt_worker_main(connection: Any, config: dict[str, Any], cancel_event: Any) -> None:
    """Load Whisper only in this process and serve bounded memory-only audio."""
    try:
        # ctranslate2 imports torch whenever it is installed, only for model
        # conversion. Recognition never uses it, so this dedicated process
        # blocks it: seconds less startup and about a gigabyte less memory.
        sys.modules.setdefault("torch", None)
        from .input import ConfidenceLimits, VoiceRecognizer

        confidence = dict(config.get("confidence") or {})
        recognizer = VoiceRecognizer(
            model_size=str(config.get("model_size") or "tiny"),
            device=str(config.get("device") or "cpu"),
            compute_type=str(config.get("compute_type") or "int8"),
            beam_size=int(config.get("beam_size") or 1),
            confidence=ConfidenceLimits(**confidence),
        )
        ready = recognizer.warm()
        connection.send({"event": "ready", "ok": bool(ready)})
        if not ready:
            connection.send({"event": "stopped", "reason": "load_failed"})
            return
        idle_seconds = max(15.0, min(900.0, float(config.get("idle_seconds") or 180.0)))
        last_use = time.monotonic()
        used = False
        while True:
            remaining = idle_seconds - (time.monotonic() - last_use)
            if remaining <= 0 or not connection.poll(remaining):
                connection.send({"event": "idle_close"})
                break
            message = connection.recv()
            if not isinstance(message, dict):
                continue
            operation = str(message.get("op") or "")
            if operation == "stop":
                connection.send({"event": "stopped", "reason": "requested"})
                break
            if operation != "transcribe":
                continue
            request_id = str(message.get("request_id") or "")[:64]
            if not request_id:
                continue
            if not used:
                used = True
                connection.send({"event": "first_use"})
            last_use = time.monotonic()
            kwargs = {
                "sample_rate": int(message.get("sample_rate") or 16000),
                "cancel_event": cancel_event,
            }
            initial_prompt = " ".join(
                str(message.get("initial_prompt") or "").split()
            )[-320:]
            if initial_prompt:
                kwargs["initial_prompt"] = initial_prompt
            text = recognizer.transcribe(message.get("audio"), **kwargs)
            evidence = getattr(recognizer, "last_evidence", None)
            if cancel_event.is_set():
                connection.send({"event": "result", "request_id": request_id, "cancelled": True})
            else:
                connection.send({
                    "event": "result",
                    "request_id": request_id,
                    "text": str(text or "")[:8000],
                    "uncertain": bool(getattr(evidence, "uncertain", False)),
                    "reason": str(getattr(evidence, "reason", "") or "")[:80],
                })
            last_use = time.monotonic()
    except (EOFError, BrokenPipeError):
        pass
    except Exception:
        try:
            connection.send({"event": "crash"})
        except Exception:
            pass
    finally:
        try:
            closer = locals().get("recognizer")
            if closer is not None:
                closer.close()
        except Exception:
            pass
        try:
            connection.close()
        except Exception:
            pass


__all__ = ["stt_worker_main"]
