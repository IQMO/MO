"""Project-owned image/video ingestion and measured metadata."""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any
import uuid

from core.runtime.subprocess_flags import apply_windows_hidden_process_flags
from core.utils.atomic_write import atomic_write_text
from core.utils.file_hash import file_sha256

from .model import ASSET_ORIGINS


_SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".mkv", ".webm"})
_AUDIO_SUFFIXES = frozenset({".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"})


def ingest_project_asset(
    project: str | Path,
    source: str | Path,
    *,
    asset_id: str,
    origin: str,
) -> dict[str, Any]:
    """Copy one explicit local asset into a project and bind measured metadata."""
    project_path = Path(project).expanduser().resolve(strict=False)
    if project_path.is_dir():
        project_path = project_path / "project.json"
    source_path = Path(source).expanduser().resolve(strict=False)
    if not _SAFE_ID.fullmatch(str(asset_id)):
        raise ValueError("asset id must use letters, numbers, underscore, or hyphen")
    if origin not in ASSET_ORIGINS:
        raise ValueError(f"asset origin must be one of: {', '.join(sorted(ASSET_ORIGINS))}")
    if not source_path.is_file():
        raise FileNotFoundError(f"asset source not found: {source_path}")
    try:
        payload = json.loads(project_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("project.json is unreadable or invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("project.json must contain an object")
    assets = payload.setdefault("assets", [])
    if not isinstance(assets, list):
        raise ValueError("project assets must be a list")
    if any(isinstance(item, dict) and str(item.get("id")) == asset_id for item in assets):
        raise ValueError(f"asset id already exists: {asset_id}")

    suffix = source_path.suffix.lower()
    if suffix not in _IMAGE_SUFFIXES | _VIDEO_SUFFIXES | _AUDIO_SUFFIXES:
        raise ValueError(
            "asset must be a PNG, JPEG, WebP, MP4, MOV, MKV, WebM, WAV, MP3, M4A, AAC, OGG, or FLAC file"
        )
    media_dir = (project_path.parent / "media").resolve(strict=False)
    try:
        media_dir.relative_to(project_path.parent)
    except ValueError as exc:
        raise ValueError("project media directory escapes the project") from exc
    media_dir.mkdir(parents=True, exist_ok=True)
    destination = media_dir / f"{asset_id}{suffix}"
    if destination.exists():
        raise FileExistsError(f"project media already exists: {destination.name}")
    stage = media_dir / f".{asset_id}.{uuid.uuid4().hex}.stage{suffix}"
    try:
        shutil.copyfile(source_path, stage)
        metadata = inspect_media(stage)
        expected_type = (
            "image" if suffix in _IMAGE_SUFFIXES else "audio" if suffix in _AUDIO_SUFFIXES else "video"
        )
        if metadata["type"] != expected_type:
            raise ValueError(f"asset bytes are not a readable {expected_type}")
        row = {
            "id": asset_id,
            "type": metadata.pop("type"),
            "path": f"media/{destination.name}",
            "sha256": file_sha256(stage),
            "origin": origin,
            **metadata,
        }
        os.replace(stage, destination)
        assets.append(row)
        try:
            atomic_write_text(project_path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return row
    finally:
        stage.unlink(missing_ok=True)


def inspect_media(path: str | Path) -> dict[str, Any]:
    """Return bounded, measured visual-media metadata without trusting its name."""
    candidate = Path(path).expanduser().resolve(strict=False)
    suffix = candidate.suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        try:
            from PIL import Image
            with Image.open(candidate) as image:
                width, height = image.size
                image.verify()
        except (OSError, ValueError) as exc:
            raise ValueError("image asset is unreadable") from exc
        if not 1 <= width <= 7680 or not 1 <= height <= 4320:
            raise ValueError("image asset dimensions are outside the supported bounds")
        return {"type": "image", "width": width, "height": height}
    if suffix not in _VIDEO_SUFFIXES | _AUDIO_SUFFIXES:
        raise ValueError("unsupported media extension")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("FFprobe is required to inspect video assets")
    kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "timeout": 120,
    }
    apply_windows_hidden_process_flags(kwargs)
    try:
        result = subprocess.run([
            ffprobe, "-v", "error", "-show_entries",
            "format=duration:stream=codec_type,width,height,avg_frame_rate",
            "-of", "json", str(candidate),
        ], **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("FFprobe timed out while inspecting the video asset") from exc
    if result.returncode != 0:
        raise ValueError(f"video asset is unreadable: {str(result.stderr or '').strip()[-300:]}")
    if suffix in _AUDIO_SUFFIXES:
        return _audio_metadata(result.stdout)
    try:
        payload = json.loads(result.stdout)
        videos = [row for row in payload["streams"] if row.get("codec_type") == "video"]
        if len(videos) != 1:
            raise ValueError
        stream = videos[0]
        width = int(stream["width"])
        height = int(stream["height"])
        duration = float(payload["format"]["duration"])
        numerator, denominator = str(stream["avg_frame_rate"]).split("/", 1)
        fps = float(numerator) / float(denominator)
    except (KeyError, TypeError, ValueError, ZeroDivisionError, json.JSONDecodeError) as exc:
        raise ValueError("video asset metadata is incomplete") from exc
    if not (1 <= width <= 7680 and 1 <= height <= 4320):
        raise ValueError("video asset dimensions are outside the supported bounds")
    if not math.isfinite(duration) or not 0.1 <= duration <= 30.0:
        raise ValueError("video assets must be between 0.1 and 30 seconds")
    if not math.isfinite(fps) or not 1.0 <= fps <= 120.0:
        raise ValueError("video asset frame rate is outside the supported bounds")
    return {
        "type": "video",
        "width": width,
        "height": height,
        "duration_seconds": round(duration, 6),
        "fps": round(fps, 6),
    }


def _audio_metadata(probe_output: str) -> dict[str, Any]:
    """Bound a music bed measured by FFprobe; it loops or trims to the video."""
    try:
        payload = json.loads(probe_output)
        if not [row for row in payload["streams"] if row.get("codec_type") == "audio"]:
            raise ValueError
        duration = float(payload["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("audio asset metadata is incomplete") from exc
    if not math.isfinite(duration) or not 0.5 <= duration <= 3600.0:
        raise ValueError("audio assets must be between 0.5 seconds and one hour")
    return {"type": "audio", "duration_seconds": round(duration, 6)}
