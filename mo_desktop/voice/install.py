"""Small standalone installer/status entry point for optional MO Desktop voice."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Sequence

from core.provider.provider import load_config
from core.state.paths import default_config_path, repo_root
from core.utils.atomic_write import atomic_write_text
from core.utils.file_hash import file_sha256_or_empty
from mo_desktop.voice.storage import (
    PIPER_MODEL,
    ensure_voice_layout,
    resolve_voice_install_root,
    voice_install_marker,
    voice_layout,
    voice_model_config_path,
    voice_model_path,
    voice_process_environment,
    voice_runtime_integrity,
    worker_python,
)


ENGINE = "piper-tts"
ENGINE_VERSION = "1.4.2"
ENGINE_LICENSE = "GPL-3.0-or-later"
VOICE = "joe"
VOICE_DATASET_LICENSE = "CC0"
MIN_FREE_BYTES = 2 * 1024 ** 3


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare or inspect MO Desktop's optional voice runtime.")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("status", "prepare", "install", "update", "uninstall"),
        default="status",
    )
    parser.add_argument("--config", default=None, help="MO config path; default is the private MO config")
    parser.add_argument("--root", default=None, help="absolute voice runtime root; overrides config for this command")
    parser.add_argument("--yes", action="store_true", help="confirm removal of the managed voice runtime")
    args = parser.parse_args(list(argv) if argv is not None else None)

    config_path = args.config or default_config_path()
    try:
        config = load_config(config_path) if Path(config_path).is_file() else {}
        root = resolve_voice_install_root(config, override=args.root)
    except Exception as exc:
        print(f"MO voice setup error: {exc}")
        return 2

    paths = (
        ensure_voice_layout(root)
        if args.command in {"prepare", "install", "update"}
        else voice_layout(root)
    )
    free = _free_bytes(root)
    print(f"MO voice root: {root}")
    model = voice_model_path(root)
    marker = voice_install_marker(root)
    integrity = voice_runtime_integrity(root)
    ready = integrity == "verified"
    print(f"status: {'installed' if ready else ('prepared' if root.is_dir() else 'not prepared')}")
    _print_disclosure()
    if marker.is_file():
        try:
            installed = json.loads(marker.read_text(encoding="utf-8"))
            print(
                "engine: "
                f"{installed.get('engine', 'unknown')} {installed.get('version', '')}; "
                f"voice: {installed.get('voice', 'unknown')}; "
                f"engine license: {installed.get('engine_license', 'unknown')}; "
                f"voice dataset license: {installed.get('voice_dataset_license', 'unknown')}"
            )
            recorded = str(installed.get("model_sha256") or "")
            if model.is_file():
                if not recorded:
                    print("model integrity: no digest recorded; run update to reinstall")
                else:
                    print("model integrity: " + ("verified" if integrity == "verified" else "CHANGED since install"))
        except (OSError, ValueError):
            print("engine metadata: unreadable")
    if free is not None:
        print(f"free space: {free / (1024 ** 3):.1f} GiB")
    if args.command == "prepare":
        print(f"managed directories: {len(paths) - 1}")
    if args.command == "uninstall":
        return _uninstall(paths, confirmed=args.yes)
    if args.command in {"install", "update"}:
        if free is not None and free < MIN_FREE_BYTES:
            print("MO voice setup error: at least 2 GiB free space is required")
            return 2
        return _install(paths, upgrade=args.command == "update")
    return 0


def _install(paths: dict[str, Path], *, upgrade: bool = False) -> int:
    product = Path(repo_root())
    root = paths["root"]
    env = voice_process_environment(root)
    worker_dir = paths["venvs"] / "piper"
    worker = worker_python(root)
    if not worker.is_file():
        if subprocess.run(
            [sys.executable, "-m", "venv", str(worker_dir)], cwd=product, env=env, check=False,
        ).returncode:
            print("MO voice setup error: dependency installation failed")
            return 2
    worker = worker_python(root)
    install_command = [str(worker), "-m", "pip", "install"]
    if upgrade:
        install_command.append("--upgrade")
    install_command.extend(["-r", str(product / "requirements-voice.txt")])
    if subprocess.run(install_command, cwd=product, env=env, check=False).returncode:
        print("MO voice setup error: speech worker installation failed")
        return 2
    stage = paths["tmp"] / f"install-{uuid.uuid4().hex}"
    stage.mkdir(parents=True, exist_ok=False)
    try:
        if subprocess.run(
            [str(worker), "-m", "piper.download_voices", "--data-dir", str(stage), PIPER_MODEL],
            cwd=product, env=env, check=False,
        ).returncode:
            print("MO voice setup error: Joe voice download failed")
            return 2
        staged_model = stage / f"{PIPER_MODEL}.onnx"
        staged_sidecar = stage / f"{PIPER_MODEL}.onnx.json"
        if not staged_model.is_file() or staged_model.stat().st_size <= 0 or not staged_sidecar.is_file():
            print("MO voice setup error: downloaded voice artifacts are incomplete")
            return 2
        digest = file_sha256_or_empty(staged_model)
        if not digest:
            print("MO voice setup error: downloaded voice digest could not be computed")
            return 2
        warm_env = dict(env)
        warm_env["PYTHONPATH"] = str(product)
        warm_env["MO_VOICE_MODEL"] = str(staged_model)
        if subprocess.run(
            [str(worker), "-m", "mo_desktop.voice.worker", "--prepare"],
            cwd=product, env=warm_env, check=False,
        ).returncode:
            print("MO voice setup error: model preparation failed")
            return 2

        model = voice_model_path(root)
        sidecar = voice_model_config_path(root)
        model.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_sidecar, sidecar)
        os.replace(staged_model, model)
        atomic_write_text(
            voice_install_marker(root),
            json.dumps({
                "engine": ENGINE,
                "version": ENGINE_VERSION,
                "model": PIPER_MODEL,
                "voice": VOICE,
                "engine_license": ENGINE_LICENSE,
                "voice_dataset_license": VOICE_DATASET_LICENSE,
                "model_sha256": digest,
                "installed_at": int(time.time()),
            }, indent=2) + "\n",
        )
    except OSError as exc:
        print(f"MO voice setup error: could not finalize installation ({type(exc).__name__})")
        return 2
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    action = "updated" if upgrade else "installed"
    print(f"MO voice {action}: English preset speech and local voice capture are ready")
    return 0


def _uninstall(paths: dict[str, Path], *, confirmed: bool) -> int:
    """Remove only directories owned by the voice layout, preserving other data."""
    root = paths["root"]
    if not confirmed:
        print("MO voice uninstall requires --yes; managed voice models, runtimes, caches, and profiles will be removed")
        return 2
    for path in (
        root / "venvs",
        root / "models",
        root / "cache",
        root / "tmp",
        root / "profiles",
    ):
        try:
            if path.is_symlink() or path.is_file():
                path.unlink(missing_ok=True)
            elif path.is_dir():
                shutil.rmtree(path)
        except OSError as exc:
            print(f"MO voice setup error: uninstall failed ({type(exc).__name__})")
            return 2
    try:
        root.rmdir()
        print("MO voice uninstalled: managed runtime and private voice data removed")
    except FileNotFoundError:
        print("MO voice uninstalled: no managed runtime was present")
    except OSError:
        print("MO voice uninstalled: managed data removed; unrelated root entries were preserved")
    return 0


def _print_disclosure() -> None:
    print(
        f"package: {ENGINE} {ENGINE_VERSION}; model: {PIPER_MODEL}; "
        f"engine license: {ENGINE_LICENSE}; voice dataset license: {VOICE_DATASET_LICENSE}"
    )
    print("source: pinned Piper package and public Piper voice catalog; local/offline after installation")


def _free_bytes(path: Path) -> int | None:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return int(shutil.disk_usage(probe).free)
    except OSError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
