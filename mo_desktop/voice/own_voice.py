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
