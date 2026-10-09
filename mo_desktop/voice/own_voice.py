"""The user's own voice in Settings → Voice: which voice MO speaks with, the Arabic voice, and their state.

MO ships no voice and no engine. A voice made from the user's recordings lives in the private voice root
(``profiles/my-voice``); any other trained RVC voice the user configured stays where it is. Choices list
only what exists on this computer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

MO_VOICE = ""          # the empty choice: MO's own (Piper) voice, or Arabic left silent


def _root(config: Mapping[str, Any]) -> Path:
    from mo_desktop.voice.storage import resolve_voice_install_root

    return resolve_voice_install_root(dict(config or {}))


def my_voice_model(config: Mapping[str, Any]) -> Path | None:
    """The newest voice made from the user's own recordings, if one exists."""
    from mo_desktop.voice.storage import voice_layout

    folder = voice_layout(_root(config))["my_voice"]
    models = sorted(folder.glob("*.pth"), key=lambda path: path.stat().st_mtime) if folder.is_dir() else []
    return models[-1] if models else None


def matching_index(model: str | Path) -> str:
    """The retrieval index trained with ``model``: the one ``.index`` beside it, else none."""
    if not str(model or "").strip():
        return ""
    indexes = sorted(Path(model).parent.glob("*.index"))
    return str(indexes[0]) if len(indexes) == 1 else ""


def speaking_voice_choices(config: Mapping[str, Any]) -> list[tuple[str, str]]:
    """MO's voice, the user's own voice once made, and a voice file configured by hand."""
    choices = [(MO_VOICE, "MO's voice")]
    mine = my_voice_model(config)
    if mine is not None:
        choices.append((str(mine), "Your voice"))
    configured = str(dict(config or {}).get("clone_model") or "").strip()
    if configured and configured not in {value for value, _label in choices} and Path(configured).is_file():
        choices.append((configured, Path(configured).stem))
    return choices


def arabic_voice_choices(config: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Off, or an Arabic Piper voice placed in the voice root (``ar_*.onnx`` with its ``.onnx.json``)."""
    from mo_desktop.voice.storage import voice_layout

    folder = voice_layout(_root(config))["models"] / "piper"
    found = [path for path in sorted(folder.glob("ar_*.onnx")) if path.with_suffix(".onnx.json").is_file()]
    choices = [(MO_VOICE, "Off · Arabic replies stay silent")] + [(str(path), path.stem) for path in found]
    configured = str(dict(config or {}).get("arabic_model") or "").strip()
    if configured and configured not in {value for value, _label in choices}:
        choices.append((configured, Path(configured).stem))
    return choices


def status_text(config: Mapping[str, Any], speech: Any) -> str:
    """One line: which voice MO speaks with and whether the user's voice loaded."""
    if not str(dict(config or {}).get("clone_model") or "").strip():
        return "MO's voice"
    state = str(getattr(speech, "clone_state", "off") or "off") if speech is not None else "off"
    error = str(getattr(speech, "clone_error", "") or "") if speech is not None else ""
    return {
        "loading": "Loading your voice… (a minute or two)",
        "ready": "Ready · MO speaks in your voice",
        "failed": "Didn't load" + (f": {error[:120]}" if error else "") + " · MO speaks with its own voice",
    }.get(state, "Loads the next time MO speaks")


def microphone_text() -> str:
    """The microphone MO listens with: the system default input device."""
    try:
        import sounddevice as sd

        return "System default · " + str(sd.query_devices(kind="input")["name"])
    except Exception:
        return "System default"


# --- Record my voice: lines read aloud, then free talk; each clip checked and kept privately ---------------------

# What MO says in its real work, so the voice is trained on how it will speak; no app names.
LINES = (
    "Sure, I'm on it.",
    "Give me a second, I'll check that for you.",
    "Done. Everything you asked for is ready.",
    "I found three files that match. Which one do you want?",
    "That didn't work the first time, so I tried another way.",
    "The tests passed, and the change is ready for you to review.",
    "Honestly, I'm not sure yet. Let me look properly.",
    "Good morning! How did you sleep?",
    "Do you want me to read the rest, or is that enough?",
    "Okay, I've stopped. Tell me when you want to continue.",
    "Ha, that's a good one.",
    "Careful, that will delete the file for good. Are you sure?",
    "It's quarter past nine, and you have two new emails.",
    "I moved the window to your second screen.",
    "Playing your music now. Say stop whenever you like.",
    "Your meeting starts in ten minutes. Should I remind you again?",
    "I saved a copy in your documents folder.",
    "The download finished. It took about four minutes.",
    "Let me think about that for a moment.",
    "Yes, exactly. That's what I meant.",
    "No problem at all. Anything else?",
    "I couldn't reach the server, so I'll try again shortly.",
    "Here's what I found, starting with the most important part.",
    "Would you like the short version or the full story?",
    "Thank you, that really helps.",
)
# Free talk gives the voice its real rhythm, in whatever language the user speaks.
FREE_TALK = (
    "Talk freely for about two minutes, in your own language: tell MO about your day.",
    "Talk for about two minutes about something you enjoy, the way you would tell a friend.",
    "For about two minutes, explain a task you often do, and ask MO a few questions.",
)
READY_SECONDS = 300            # enough good speech to make a voice; about ten minutes makes a better one
_LINE_SECONDS, _TALK_SECONDS = 30, 180


def recording_steps() -> list[dict[str, Any]]:
    return ([{"kind": "line", "text": text, "seconds": _LINE_SECONDS} for text in LINES]
            + [{"kind": "talk", "text": text, "seconds": _TALK_SECONDS} for text in FREE_TALK])


def check_clip(audio: Any, sample_rate: int, kind: str = "line") -> tuple[str, str]:
    """``(status, what to do)`` for one clip: ok, too_short, too_quiet, too_loud or noisy."""
    import numpy as np

    samples = np.asarray(audio if audio is not None else [], dtype="float32").reshape(-1)
    seconds = len(samples) / float(sample_rate or 1)
    if seconds < (1.0 if kind == "line" else 20.0):
        return "too_short", ("Read the whole line, then press Stop." if kind == "line"
                             else "Keep talking a little longer, then press Stop.")
    if float(np.mean(np.abs(samples) >= 0.99)) > 0.001:
        return "too_loud", "Too loud. Move a little away from the mic and record it again."
    if float(np.sqrt(np.mean(np.square(samples)))) < 0.02:
        return "too_quiet", "Too quiet. Move closer to the mic and record it again."
    frame = max(1, int(sample_rate * 0.05))
    frames = samples[: len(samples) // frame * frame].reshape(-1, frame)
    # The quietest 5% of the clip is the room between words; a clean take keeps it near silence.
    floor = float(np.percentile(np.sqrt(np.mean(np.square(frames), axis=1)), 5)) if len(frames) else 0.0
    if floor > 0.015:
        return "noisy", "There's background noise. Find a quieter spot and record it again."
    return "ok", "Good."


def _input_rate() -> int:
    """The microphone's own rate: training wants every detail the mic gives (not speech recognition's 16 kHz)."""
    try:
        import sounddevice as sd

        rate = int(float(sd.query_devices(kind="input")["default_samplerate"]))
        return rate if rate >= 32000 else 48000
    except Exception:
        return 48000


def _default_recorder(seconds: float) -> Any:
    from mo_desktop.voice.input import PushToTalkRecorder

    return PushToTalkRecorder(sample_rate=_input_rate(), max_seconds=seconds)


class VoiceRecording:
    """One Settings recording session: one clip per step in ``profiles/my-voice/recordings``, each checked.

    Start and Stop are two short Settings requests; the microphone records in between. The results file keeps
    each clip's check, so Settings shows the same progress after it or MO Desktop reopens."""

    def __init__(self, config: Mapping[str, Any], *, recorder_factory: Any = None) -> None:
        from mo_desktop.voice.storage import voice_layout

        self.folder = voice_layout(_root(config))["my_voice"] / "recordings"
        self._factory = recorder_factory or _default_recorder
        self._recorder: Any = None
        self._step: int | None = None

    def _results(self) -> dict[str, dict[str, Any]]:
        import json

        try:
            value = json.loads((self.folder / "results.json").read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def status(self) -> dict[str, Any]:
        results = self._results()
        steps = recording_steps()
        good = sum(float(item.get("seconds") or 0) for item in results.values() if item.get("status") == "ok")
        nxt = next((index for index in range(len(steps)) if results.get(f"{index + 1:02d}", {}).get("status") != "ok"),
                   len(steps))
        return {"steps": steps, "results": results, "next": nxt, "recording": self._step,
                "good_seconds": round(good, 1), "ready": good >= READY_SECONDS}

    def start(self, step: int) -> dict[str, Any]:
        steps = recording_steps()
        if self._step is not None:
            raise ValueError("Already recording · press Stop first")
        if not isinstance(step, int) or isinstance(step, bool) or not 0 <= step < len(steps):
            raise ValueError("Choose a line to record")
        recorder = self._factory(steps[step]["seconds"])
        if not recorder.start():
            raise ValueError("The microphone did not start" + (f": {recorder.last_error}" if recorder.last_error else ""))
        self._recorder, self._step = recorder, step
        return self.status()

    def stop(self) -> dict[str, Any]:
        import json
        import wave

        import numpy as np

        recorder, step, self._recorder, self._step = self._recorder, self._step, None, None
        if recorder is None or step is None:
            raise ValueError("Nothing is recording")
        audio = recorder.stop()
        rate = int(getattr(recorder, "_sample_rate", 48000))
        kind = recording_steps()[step]["kind"]
        status, advice = check_clip(audio, rate, kind) if audio is not None else (
            "too_quiet", "Nothing was heard. Check the microphone and record it again.")
        seconds = round(len(np.asarray(audio).reshape(-1)) / rate, 1) if audio is not None else 0.0
        self.folder.mkdir(parents=True, exist_ok=True)
        name = f"{step + 1:02d}"
        clip = self.folder / f"{name}.wav"
        if audio is not None and status == "ok":          # only good clips are kept for the voice
            pcm = (np.clip(np.asarray(audio, dtype="float32").reshape(-1), -1.0, 1.0) * 32767).astype("<i2")
            with wave.open(str(clip), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(rate)
                handle.writeframes(pcm.tobytes())
        else:
            clip.unlink(missing_ok=True)                  # a failed retake never leaves the old take behind
        results = self._results()
        results[name] = {"status": status, "advice": advice, "seconds": seconds}
        (self.folder / "results.json").write_text(json.dumps(results, indent=1), encoding="utf-8")
        return {**self.status(), "last": {"step": step, "status": status, "advice": advice, "seconds": seconds}}

    def cancel(self) -> None:
        recorder, self._recorder, self._step = self._recorder, None, None
        if recorder is not None:
            recorder.stop()
