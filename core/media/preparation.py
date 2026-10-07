"""File-only, bounded media preparation; never asks a model to inspect a reference."""
from __future__ import annotations

import json
import math
from pathlib import Path
import shutil
import subprocess

from core.runtime.subprocess_flags import apply_windows_hidden_process_flags

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv"}
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".aac"}
_DEMUXERS = "mov,matroska,webm,wav,mp3,flac,ogg,aac,image2,png_pipe,jpeg_pipe,webp_pipe"


def kind_for(path: Path) -> str:
    for kind, suffixes in (("image", IMAGE_SUFFIXES), ("video", VIDEO_SUFFIXES), ("audio", AUDIO_SUFFIXES)):
        if path.suffix.lower() in suffixes:
            return kind
    raise ValueError("Choose a supported image, audio or video file.")


def require_tools(operation: str, references: list[dict]) -> None:
    """Check local delivery/preparation prerequisites before a paid submission."""
    required = set()
    if operation in {"music", "cover", "extend_music", "video", "extend_video"}:
        required.add("ffprobe")
    if operation == "extend_video" or any(r["kind"] in {"audio", "video"} for r in references):
        required.update(("ffmpeg", "ffprobe"))
    missing = sorted(binary for binary in required if not shutil.which(binary))
    if missing:
        raise ValueError("Required local media tools are missing: " + ", ".join(missing) + ". Check Create setup; no job was submitted.")


def _run(binary: str, args: list[str], *, timeout: int = 90) -> str:
    exe = shutil.which(binary)
    if not exe:
        raise ValueError(f"{binary} is required for this media operation; use Create setup.")
    kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    apply_windows_hidden_process_flags(kwargs)
    try:
        result = subprocess.run([exe, *args], **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Local media processing timed out.") from exc
    if result.returncode:
        # Decoder stderr may contain local names, embedded URLs and metadata.
        raise ValueError("Media could not be decoded safely.")
    return result.stdout.decode("utf-8", errors="replace")


def probe(path: Path) -> dict:
    kind = kind_for(path)
    if kind == "image":
        from PIL import Image

        with Image.open(path) as img:
            width, height = img.size
            if width * height > 40_000_000 or min(width, height) < 1:
                raise ValueError("Image exceeds the safe decoded-pixel limit.")
            img.verify()
        return {"kind": kind, "width": width, "height": height, "bytes": path.stat().st_size}
    raw = _run("ffprobe", ["-v", "error", "-protocol_whitelist", "file,pipe",
                          "-format_whitelist", _DEMUXERS, "-show_entries",
                          "format=duration:stream=codec_type,width,height,avg_frame_rate",
                          "-of", "json", str(path)], timeout=30)
    try:
        value = json.loads(raw)
        duration = float(value["format"]["duration"])
        streams = [s for s in value["streams"] if s.get("codec_type") == kind]
        if not streams or not math.isfinite(duration) or not 0 < duration <= 3600:
            raise ValueError
        info = {"kind": kind, "duration": duration, "bytes": path.stat().st_size}
        if kind == "video":
            if len(streams) != 1:
                raise ValueError
            stream = streams[0]
            num, den = str(stream["avg_frame_rate"]).split("/", 1)
            fps = float(num) / float(den)
            width, height = int(stream["width"]), int(stream["height"])
            if not math.isfinite(fps) or not 0 < fps <= 240 or not 0 < width * height <= 40_000_000:
                raise ValueError
            info.update(width=width, height=height, fps=fps)
        return info
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError("Media has invalid or unsupported metadata.") from exc


def validate_reference(info: dict, operation: str, model: str) -> None:
    kind = info["kind"]
    size_limit = 30 * 1024 * 1024
    if operation in {"video", "extend_video"}:
        maximum = 30 if model.endswith("2-5") else 15
        if kind == "video":
            size_limit = (200 if maximum == 30 else 50) * 1024 * 1024
        elif kind == "audio":
            size_limit = 15 * 1024 * 1024
        if kind != "audio":
            w, h = info["width"], info["height"]
            if not (300 <= w <= 6000 and 300 <= h <= 6000 and .4 <= w / h <= 2.5):
                raise ValueError("Seedance references need 300–6000 px sides and a 0.4–2.5 aspect ratio.")
            if kind == "video" and not (409600 <= w * h <= 927408 and 24 <= info["fps"] <= 60):
                raise ValueError("Motion references need 409600–927408 pixels and 24–60 fps; no silent resizing.")
        if kind in {"video", "audio"} and not 2 <= info["duration"] <= maximum:
            raise ValueError(f"This Seedance model needs reference {kind} between 2 and {maximum} seconds.")
    elif operation in {"music", "cover"}:
        size_limit = {"image": 10, "video": 100, "audio": 500}[kind] * 1024 * 1024
        if kind == "video" and info["duration"] > 241:
            raise ValueError("A music video reference must be at most 241 seconds.")
        if kind == "audio" and not (6 <= info["duration"] <= (480 if operation == "cover" else 1800)):
            raise ValueError("A reference song must be 6 seconds–8 minutes for a cover, or up to 30 minutes for generation.")
    if info["bytes"] > size_limit:
        raise ValueError("Reference exceeds the selected operation's file-size limit.")


def prepare(source: Path, directory: Path, index: int, operation: str, model: str) -> tuple[Path, dict]:
    """Strip metadata into an owned temporary copy; never rewrite the source."""
    if not source.is_file() or not 0 < source.stat().st_size <= 500 * 1024 * 1024:
        raise ValueError("Reference is missing, empty or larger than 500 MB.")
    info = probe(source)
    validate_reference(info, operation, model)
    kind = info["kind"]
    target = directory / f"reference-{index}.{'png' if kind == 'image' else 'mp4' if kind == 'video' else 'wav'}"
    if kind == "image":
        from PIL import Image, ImageOps

        with Image.open(source) as image:
            oriented = ImageOps.exif_transpose(image).convert("RGBA" if "A" in image.getbands() else "RGB")
            clean = Image.frombytes(oriented.mode, oriented.size, oriented.tobytes())
            clean.save(target, format="PNG")
    else:
        codec = (["-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-movflags", "+faststart"]
                 if kind == "video" else ["-map", "0:a:0", "-c:a", "pcm_s16le", "-vn"])
        _run("ffmpeg", ["-v", "error", "-nostdin", "-n", "-threads", "1", "-protocol_whitelist", "file,pipe",
                         "-format_whitelist", _DEMUXERS, "-i", str(source), *codec,
                         "-map_metadata", "-1", "-map_metadata:s", "-1", "-map_chapters", "-1", "-threads", "1", str(target)])
    clean_info = probe(target)
    validate_reference(clean_info, operation, model)
    return target, clean_info


def final_frame(source: Path, target: Path) -> None:
    """Decode the actual final frame, without a guessed timestamp or provider thumbnail."""
    if probe(source)["kind"] != "video":
        raise ValueError("Video continuation needs a video parent.")
    _run("ffmpeg", ["-v", "error", "-nostdin", "-threads", "1", "-protocol_whitelist", "file,pipe",
                     "-format_whitelist", _DEMUXERS, "-i", str(source), "-map", "0:v:0",
                     "-an", "-sn", "-dn", "-map_metadata", "-1", "-threads", "1",
                     "-update", "1", "-y", str(target)], timeout=120)
    probe(target)
