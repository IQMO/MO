"""Private storage contract for optional MO Desktop voice runtimes."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import site
import sys
from typing import Any, Mapping

from core.state.paths import VOICE_RUNTIME_DIR, repo_root, resolve_state_path
from core.utils.file_hash import file_sha256_or_empty
from mo_desktop.desktop_launch import mo_desktop_config_block


ENV_MO_VOICE_HOME = "MO_VOICE_HOME"
DEFAULT_VOICE_ROOT = VOICE_RUNTIME_DIR
PIPER_MODEL = "en_US-joe-medium"


def resolve_voice_install_root(
    config: Mapping[str, Any] | None = None,
    *,
    override: str | Path | None = None,
) -> Path:
    """Resolve the one private root for voice venvs, models, caches, and profiles."""
    cfg = dict(config or {})
    desktop = mo_desktop_config_block(cfg)
    voice = desktop.get("voice") if isinstance(desktop.get("voice"), dict) else {}
    # Voice components are also constructed with the already-extracted voice
    # block.  Accept that shape without creating a second config contract.
    if not voice and "install_root" in cfg:
        voice = cfg
    raw = str(override or os.getenv(ENV_MO_VOICE_HOME, "") or voice.get("install_root") or "").strip()
    if raw:
        root = Path(raw).expanduser()
        if not root.is_absolute():
            raise ValueError("voice install root must be an absolute path")
        resolved = root.resolve(strict=False)
    else:
        resolved = Path(resolve_state_path(DEFAULT_VOICE_ROOT, cfg)).resolve(strict=False)
    _reject_checkout_root(resolved)
    return resolved


def voice_layout(root: str | Path) -> dict[str, Path]:
    """Return every managed directory below ``root`` without creating anything."""
    base = Path(root).expanduser().resolve(strict=False)
    _reject_checkout_root(base)
    return {
        "root": base,
        "venvs": base / "venvs",
        "models": base / "models",
        "engines": base / "engines",
        "huggingface_cache": base / "cache" / "huggingface",
        "torch_cache": base / "cache" / "torch",
        "pip_cache": base / "cache" / "pip",
        "numba_cache": base / "cache" / "numba",
        "pkuseg_cache": base / "cache" / "pkuseg",
        "clone_cache": base / "cache" / "clone",
        "tmp": base / "tmp",
        "profiles": base / "profiles",
        "my_voice": base / "profiles" / "my-voice",   # the user's recordings and the voice made from them
    }


def ensure_voice_layout(root: str | Path) -> dict[str, Path]:
    """Create the managed layout after explicit installer/user authorization."""
    paths = voice_layout(root)
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)
    return paths


def worker_python(root: str | Path) -> Path:
    """The one interpreter that owns the isolated voice runtime."""
    venv = voice_layout(root)["venvs"] / "piper"
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def voice_model_path(root: str | Path) -> Path:
    """The one model location for the installed Joe voice."""
    return voice_layout(root)["models"] / "piper" / f"{PIPER_MODEL}.onnx"


def clone_server_path(root: str | Path, backend: str = "vulkan") -> Path | None:
    """Return the newest installed audio.cpp server build for ``backend``, if any."""
    name = "audiocpp_server.exe" if sys.platform == "win32" else "audiocpp_server"
    builds = sorted(
        voice_layout(root)["engines"].glob(f"audio.cpp-*/{backend}/{name}"),
        key=lambda path: tuple(int(part) for part in re.findall(r"\d+", path.parts[-3])),
    )
    return builds[-1] if builds else None


def clone_base_model_path(root: str | Path) -> Path:
    """The audio.cpp RVC base package (content and pitch models) a voice clone runs on."""
    return voice_layout(root)["models"] / "audiocpp" / "RVC-GGUF" / "rvc-f16.gguf"


def voice_model_config_path(root: str | Path) -> Path:
    """Return Piper's required JSON sidecar for the managed voice model."""
    model = voice_model_path(root)
    return model.with_suffix(model.suffix + ".json")


def voice_install_marker(root: str | Path) -> Path:
    """Return the one successful-install marker owned by the voice layout."""
    return voice_layout(root)["profiles"] / "installed.json"


def voice_runtime_ready(root: str | Path) -> bool:
    """Return whether the managed worker/model can be used for playback.

    A partial download used to look installed as soon as the interpreter and
    ONNX file existed.  The installer writes its marker only after the model
    and sidecar have loaded successfully, and the recorded digest prevents a
    later replacement or truncated file from silently entering playback. A
    marker without a digest is incomplete, and a present-but-wrong digest always
    fails closed.
    """
    return voice_runtime_integrity(root) == "verified"


def voice_runtime_integrity(root: str | Path) -> str:
    """Return ``verified``, ``changed``, or ``incomplete``."""
    worker = worker_python(root)
    model = voice_model_path(root)
    sidecar = voice_model_config_path(root)
    marker = voice_install_marker(root)
    if not all(path.is_file() for path in (worker, model, sidecar, marker)):
        return "incomplete"
    try:
        metadata = json.loads(marker.read_text(encoding="utf-8"))
        recorded = str(metadata.get("model_sha256") or "").strip().lower()
    except (OSError, TypeError, ValueError):
        return "incomplete"
    if not recorded:
        return "incomplete"
    return "verified" if file_sha256_or_empty(model) == recorded else "changed"


def voice_process_environment(
    root: str | Path,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return child-process cache/temp variables scoped to the selected voice root."""
    paths = voice_layout(root)
    env = dict(os.environ if base is None else base)
    env.update({
        "HF_HOME": str(paths["huggingface_cache"]),
        "HUGGINGFACE_HUB_CACHE": str(paths["huggingface_cache"] / "hub"),
        "TORCH_HOME": str(paths["torch_cache"]),
        "PIP_CACHE_DIR": str(paths["pip_cache"]),
        "NUMBA_CACHE_DIR": str(paths["numba_cache"]),
        "PKUSEG_HOME": str(paths["pkuseg_cache"]),
        "TEMP": str(paths["tmp"]),
        "TMP": str(paths["tmp"]),
    })
    return env


def activate_voice_dependencies(config: Mapping[str, Any] | None = None) -> bool:
    """Expose the optional voice venv only after a voice component is requested.

    The directory is *appended*, never prepended. Voice-only packages
    (faster-whisper, sounddevice, ctranslate2, ...) still import, while MO's own
    environment keeps priority for every package both trees contain — the voice
    runtime pulls in PyYAML, typing_extensions, tqdm and certifi transitively,
    and MO must not silently start resolving those from an optional add-on.
    Voice is the optional surface, so MO's runtime wins the tie.
    """
    root = resolve_voice_install_root(config)
    venv = root / "venvs" / "piper"
    if sys.platform == "win32":
        packages = venv / "Lib" / "site-packages"
    else:
        packages = venv / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
    if not packages.is_dir():
        return False
    package_dir = str(packages)
    if package_dir in sys.path:
        return True  # already active; addsitedir would append a duplicate
    site.addsitedir(package_dir)
    return True


def _reject_checkout_root(path: Path) -> None:
    checkout = Path(repo_root()).resolve(strict=False)
    try:
        path.relative_to(checkout)
    except ValueError:
        return
    raise ValueError("voice runtime data cannot be stored inside the MO product checkout")
