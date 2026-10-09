"""Make my voice: the user's good recordings become their voice, through the trainer installed on this computer.

MO ships no trainer. ``voice.trainer_path`` names an installed Applio; MO runs Applio's own command line (prepare,
extract, train, which also builds the index) in one background process that survives a Desktop restart, at below-
normal priority so the computer stays usable. Progress lives in ``profiles/my-voice/making.json``. When training
ends, the voice is copied into ``profiles/my-voice`` and saved as the speaking voice; Desktop watches the progress
file, loads the voice and speaks with it. Applio keeps resuming from its last checkpoint if training is interrupted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Mapping

MODEL_NAME = "mo-my-voice"       # MO's own experiment name inside the trainer's logs; removed after install
SAMPLE_RATE = 40000              # RVC v2 at 40 kHz with HiFi-GAN: the format MO's clone engine is proven with
TOTAL_EPOCHS = 300               # a fine-tune from Applio's pretrained voice; stays close to the user's own sound
SAVE_EVERY = 10                  # a resumable checkpoint every few minutes
BATCH_SIZE = 4                   # fits a 4 GB GPU
_EPOCH = re.compile(rf"^{re.escape(MODEL_NAME)} \| epoch=(\d+) \|")
_FINISHED = ("done", "failed", "stopped")


def _layout(config: Mapping[str, Any]) -> dict[str, Path]:
    from mo_desktop.voice.storage import resolve_voice_install_root, voice_layout

    return voice_layout(resolve_voice_install_root(dict(config or {})))


def trainer_python(trainer: str | Path) -> Path | None:
    """The trainer's own interpreter (Applio installs one next to ``core.py``), or ``None``."""
    root = Path(str(trainer or "")).expanduser()
    if not str(trainer or "").strip() or not (root / "core.py").is_file():
        return None
    for candidate in ("env/python.exe", "env/bin/python", ".venv/Scripts/python.exe", ".venv/bin/python"):
        if (root / candidate).is_file():
            return root / candidate
    return None


def trainer_text(config: Mapping[str, Any]) -> str:
    trainer = str(dict(config or {}).get("trainer_path") or "").strip()
    if trainer_python(trainer) is None:
        return "Not found · set voice.trainer_path to an installed Applio" if trainer else "Not installed"
    try:
        version = json.loads((Path(trainer) / "assets" / "config.json").read_text(encoding="utf-8")).get("version", "")
    except (OSError, ValueError):
        version = ""
    return f"Applio {version} · {trainer}".replace("Applio  ·", "Applio ·")


def _state_file(config: Mapping[str, Any]) -> Path:
    return _layout(config)["my_voice"] / "making.json"


def _alive(pid: int) -> bool:
    if not pid:
        return False
    if sys.platform == "win32":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))      # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259                                                # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


def progress(config: Mapping[str, Any]) -> dict[str, Any]:
    """Where making the voice stands, with whether it can start (or continue) now."""
    from mo_desktop.voice.own_voice import VoiceRecording

    try:
        state = json.loads(_state_file(config).read_text(encoding="utf-8"))
        state = state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        state = {}
    stage = str(state.get("stage") or "")
    running = bool(stage and stage not in _FINISHED and _alive(int(state.get("pid") or 0)))
    if stage and stage not in _FINISHED and not running:
        stage, state["message"] = "interrupted", "Stopped before it finished · Make my voice continues it"
    ready = VoiceRecording(config).status()["ready"]
    trainer = trainer_python(dict(config or {}).get("trainer_path")) is not None
    message = state.get("message") or ("Record about 5 minutes first" if not ready
                                       else "Ready to make your voice" if trainer else "Needs a trainer (see Trainer)")
    return {**state, "stage": stage, "running": running, "message": message,
            "can_start": ready and trainer and not running}


def _write(config: Mapping[str, Any], **fields: Any) -> None:
    path = _state_file(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    state.update(fields, updated_at=time.time())
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(temporary, path)


def start(config: Mapping[str, Any], *, launcher: Any = None) -> dict[str, Any]:
    """Begin (or continue) making the voice in its own background process."""
    current = progress(config)
    if current["running"]:
        raise ValueError("Your voice is already being made")
    if not current["can_start"]:
        raise ValueError(current["message"])
    from core.runtime.subprocess_flags import gui_python_executable

    config_path = str(dict(config or {}).get("_config_path") or "")
    command = [gui_python_executable(), "-m", "mo_desktop.voice.make_voice", "--run"] + (
        ["--config", config_path] if config_path else [])
    flags = (0x00000008 | 0x00000200 | 0x00004000) if sys.platform == "win32" else 0   # detached, own group, below normal
    trainer = Path(str(dict(config or {}).get("trainer_path") or ""))
    resume = (current["stage"] in {"interrupted", "stopped", "failed"} and current.get("resume_from") == "train"
              and (trainer / "logs" / MODEL_NAME).is_dir())       # the trainer continues from its last checkpoint
    _write(config, stage="starting", message="Starting…", epoch=0 if not resume else current.get("epoch", 0),
           total=TOTAL_EPOCHS, error="", pid=0, started_at=time.time(), resume=resume, announced=False)
    process = (launcher or subprocess.Popen)(command, cwd=str(Path(__file__).resolve().parents[2]),
                                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL, creationflags=flags)
    _write(config, pid=int(process.pid))
    return progress(config)


def mark_announced(config: Mapping[str, Any]) -> None:
    """Desktop told the user (spoke in the new voice, or said why it failed); never again for this run."""
    _write(config, announced=True)


def stop(config: Mapping[str, Any]) -> dict[str, Any]:
    """Stop making the voice; the trainer's last checkpoint lets Make my voice continue later."""
    state = progress(config)
    for pid in (state.get("trainer_pid"), state.get("pid")):
        if pid and _alive(int(pid)):
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"], capture_output=True)
            else:
                os.kill(int(pid), 15)
    _write(config, stage="stopped", message="Stopped · Make my voice continues it")
    return progress(config)


# --- the background process --------------------------------------------------------------------------------------

def _dataset(config: Mapping[str, Any]) -> Path:
    """Only the good clips, linked (not copied) into a clean folder the trainer reads."""
    from mo_desktop.voice.own_voice import VoiceRecording

    session = VoiceRecording(config)
    folder = _layout(config)["my_voice"] / "dataset"
    shutil.rmtree(folder, ignore_errors=True)
    folder.mkdir(parents=True)
    for name, result in session.status()["results"].items():
        clip = session.folder / f"{name}.wav"
        if result.get("status") == "ok" and clip.is_file():
            try:
                os.link(clip, folder / clip.name)
            except OSError:
                shutil.copy2(clip, folder / clip.name)
    return folder


def _step(config: Mapping[str, Any], python: Path, trainer: Path, args: list[str], log: Any, *,
          stage: str, message: str) -> None:
    _write(config, stage=stage, message=message)
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}}
    flags = 0x08000000 | 0x00004000 if sys.platform == "win32" else 0                # no window, below normal
    process = subprocess.Popen([str(python), "core.py", *args], cwd=str(trainer), env=environment,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace", creationflags=flags)
    _write(config, trainer_pid=process.pid)
    tail: list[str] = []
    for line in process.stdout:
        log.write(line)
        log.flush()
        tail = (tail + [line.rstrip()])[-12:]
        match = _EPOCH.match(line.strip())
        if match:
            epoch = int(match.group(1))
            _write(config, epoch=epoch, message=f"Training your voice · epoch {epoch} of {TOTAL_EPOCHS}")
    if process.wait() != 0:
        raise RuntimeError(f"{args[0]} failed: " + (next((row for row in reversed(tail) if row.strip()), "") or "no output"))


def _install(config: Mapping[str, Any], trainer: Path) -> tuple[Path, Path | None]:
    """Copy the finished voice and its index into ``profiles/my-voice``; then remove MO's experiment folder."""
    experiment = trainer / "logs" / MODEL_NAME
    finals = sorted(experiment.glob(f"{MODEL_NAME}_*e_*s.pth"),
                    key=lambda path: int(re.search(r"_(\d+)e_", path.name).group(1)))
    if not finals:
        raise RuntimeError("training finished without a voice file")
    folder = _layout(config)["my_voice"]
    for old in [*folder.glob("my-voice*.pth"), *folder.glob("my-voice*.index")]:
        old.unlink()
    epochs = re.search(r"_(\d+)e_", finals[-1].name).group(1)
    voice = folder / f"my-voice_{epochs}e.pth"
    shutil.copy2(finals[-1], voice)
    index = None
    if (experiment / f"{MODEL_NAME}.index").is_file():
        index = folder / "my-voice.index"
        shutil.copy2(experiment / f"{MODEL_NAME}.index", index)
    if experiment.parent.name == "logs" and experiment.name == MODEL_NAME:
        shutil.rmtree(experiment, ignore_errors=True)
    shutil.rmtree(folder / "dataset", ignore_errors=True)
    return voice, index


def run(config: dict[str, Any]) -> int:
    from mo_desktop.settings import persist_mo_desktop_settings

    voice_cfg = dict(((config.get("mo_desktop") or {}).get("voice")) or {})
    trainer = Path(str(voice_cfg.get("trainer_path") or "")).expanduser()
    python = trainer_python(trainer)
    folder = _layout(voice_cfg)["my_voice"]
    folder.mkdir(parents=True, exist_ok=True)
    _write(voice_cfg, pid=os.getpid())
    try:
        if python is None:
            raise RuntimeError("no trainer installed")
        cores = str(max(1, min(64, (os.cpu_count() or 2) - 1)))
        resume = bool(json.loads(_state_file(voice_cfg).read_text(encoding="utf-8")).get("resume"))
        with open(folder / "making.log", "a", encoding="utf-8") as log:
            if not resume:
                shutil.rmtree(trainer / "logs" / MODEL_NAME, ignore_errors=True)     # a fresh start, MO's folder only
                dataset = _dataset(voice_cfg)
                _step(voice_cfg, python, trainer, ["preprocess", "--model_name", MODEL_NAME, "--dataset_path", str(dataset),
                                                   "--sample_rate", str(SAMPLE_RATE), "--cpu_cores", cores,
                                                   "--cut_preprocess", "Automatic", "--process_effects", "False",
                                                   "--noise_reduction", "False", "--noise_reduction_strength", "0.7",
                                                   "--chunk_len", "3.0", "--overlap_len", "0.3",
                                                   "--normalization_mode", "post"],
                      log, stage="preparing", message="Preparing your recordings")
                _step(voice_cfg, python, trainer, ["extract", "--model_name", MODEL_NAME, "--f0_method", "rmvpe",
                                                   "--cpu_cores", cores, "--gpu", "0", "--sample_rate", str(SAMPLE_RATE),
                                                   "--embedder_model", "contentvec", "--include_mutes", "2"],
                      log, stage="extracting", message="Learning how your voice sounds")
            _write(voice_cfg, resume_from="train")
            _step(voice_cfg, python, trainer, ["train", "--model_name", MODEL_NAME, "--save_every_epoch", str(SAVE_EVERY),
                                               "--save_only_latest", "True", "--save_every_weights", "False",
                                               "--total_epoch", str(TOTAL_EPOCHS), "--sample_rate", str(SAMPLE_RATE),
                                               "--batch_size", str(BATCH_SIZE), "--gpu", "0", "--pretrained", "True",
                                               "--custom_pretrained", "False", "--overtraining_detector", "False",
                                               "--overtraining_threshold", "50", "--cleanup", "False",
                                               "--cache_data_in_gpu", "False", "--vocoder", "HiFi-GAN",
                                               "--checkpointing", "False", "--index_algorithm", "Auto"],
                  log, stage="training", message=f"Training your voice · epoch 0 of {TOTAL_EPOCHS}")
        _write(voice_cfg, stage="installing", message="Installing your voice")
        voice, index = _install(voice_cfg, trainer)
        if not persist_mo_desktop_settings(config, {"voice": {"clone_model": str(voice),
                                                              "clone_index": str(index) if index else ""}}):
            raise RuntimeError("the new voice could not be saved as your speaking voice")
        _write(voice_cfg, stage="done", message="Your voice is ready", voice=str(voice), resume_from="", trainer_pid=0)
        return 0
    except Exception as exc:                                    # the reason reaches Settings and the cubes
        _write(voice_cfg, stage="failed", message="Making your voice failed: " + str(exc)[:200], error=str(exc)[:400],
               trainer_pid=0)
        return 1


def main(argv: list[str] | None = None) -> int:
    import argparse

    from core.provider.provider import load_config
    from core.state.paths import default_config_path

    parser = argparse.ArgumentParser(description="Make the user's own voice from their recordings.")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--config", default="")
    args = parser.parse_args(argv)
    if not args.run:
        return 2
    return run(load_config(args.config or default_config_path()))


if __name__ == "__main__":
    raise SystemExit(main())
