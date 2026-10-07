"""Explicit optional-helper installation. No install, network or process on import."""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import tempfile
import threading

from core.runtime.lock import file_byte_lock
from core.state.paths import resolve_state_path
from core.utils.file_hash import file_sha256
from .kie import download
from .references import helper_path

_INSTALL_LOCK = threading.Lock()


def install_reference_helper(config: dict) -> dict:
    """Called only by an explicit native Install action, never by the model."""
    existing = helper_path(config)
    if existing:
        return {"ready": True, "installed": False}
    system = platform.system()
    architecture = {"amd64": "amd64", "x86_64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower())
    if system not in {"Windows", "Linux"} or not architecture:
        raise ValueError("Automatic verified helper installation is unavailable on this platform; reference sharing stays off.")
    asset_name = f"cloudflared-{'windows' if system == 'Windows' else 'linux'}-{architecture}" + (".exe" if system == "Windows" else "")
    root = Path(resolve_state_path("bin/media", config))
    root.mkdir(parents=True, exist_ok=True)
    with file_byte_lock(root / ".install.lock", _INSTALL_LOCK):
        if helper_path(config):
            return {"ready": True, "installed": False}
        with tempfile.TemporaryDirectory(prefix="install-", dir=root) as temporary:
            stage = Path(temporary)
            metadata = stage / "release.json"
            download("https://api.github.com/repos/cloudflare/cloudflared/releases/latest", metadata, config=config, max_bytes=2 * 1024 * 1024)
            release = json.loads(metadata.read_text(encoding="utf-8"))
            asset = next((a for a in release.get("assets", []) if a.get("name") == asset_name), None)
            digest = str((asset or {}).get("digest") or "")
            url = str((asset or {}).get("browser_download_url") or "")
            if not digest.startswith("sha256:") or len(digest) != 71 or not url.startswith("https://github.com/cloudflare/cloudflared/releases/download/"):
                raise ValueError("The official helper release has no verified checksum; nothing was installed.")
            binary = stage / "helper"
            download(url, binary, config=config, max_bytes=100 * 1024 * 1024)
            if file_sha256(binary) != digest[7:]:
                raise ValueError("Helper checksum did not match; nothing was installed.")
            target = root / ("cloudflared.exe" if system == "Windows" else "cloudflared")
            if system != "Windows":
                binary.chmod(0o700)
            os.replace(binary, target)
    return {"ready": True, "installed": True, "release": str(release.get("tag_name") or "")}
