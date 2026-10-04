"""Deterministic Pillow-to-FFmpeg renderer for MO explainer projects."""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Callable, Iterable
import uuid
import wave

from core.runtime.subprocess_flags import apply_windows_hidden_process_flags
from core.tooling.shell_processes import kill_process_tree
from core.utils.atomic_write import atomic_write_text
from core.utils.file_hash import file_sha256, file_sha256_or_empty
from interface.desktop_brand import draw_four_cube_mark

from .model import ExplainerProject, narration_digest, scene_source_ids


def project_timeline(project: ExplainerProject) -> list[dict[str, Any]]:
    payload = _timing_payload(project)
    timed: dict[str, dict[str, Any]] = {}
    if payload is not None:
        rows = payload["scenes"]
        timed = {str(row["id"]): row for row in rows}
        expected = {str(scene["id"]) for scene in project.scenes}
        if len(timed) != len(rows) or set(timed) != expected:
            raise RuntimeError("timings.json must contain exactly one row for every scene")
    timeline: list[dict[str, Any]] = []
    cursor = 0.0
    for scene in project.scenes:
        timing = timed.get(str(scene["id"]), {})
        try:
            duration = float(timing.get("duration") or scene.get("duration", 6.0))
            start = float(timing.get("start", cursor))
            speech_duration = float(timing.get("speech_duration", duration))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"invalid timing for scene {scene['id']}") from exc
        if not math.isfinite(duration) or not 0.1 <= duration <= 120.0:
            raise RuntimeError(f"timing duration for scene {scene['id']} must be between 0.1 and 120 seconds")
        if not math.isfinite(start) or start < cursor - 0.001:
            raise RuntimeError(f"timing start for scene {scene['id']} must be finite and not overlap the previous scene")
        if not math.isfinite(speech_duration) or not 0.0 <= speech_duration <= duration + 0.001:
            raise RuntimeError(f"speech duration for scene {scene['id']} must fit within its scene")
        timeline.append({
            "scene": scene,
            "start": start,
            "duration": duration,
            "speech_duration": speech_duration,
        })
        cursor = start + duration
    return timeline


def _timing_payload(project: ExplainerProject) -> dict[str, Any] | None:
    timing_path = project.directory / "timings.json"
    if not timing_path.is_file():
        return None
    try:
        payload = json.loads(timing_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("timing document must be an object")
        rows = payload.get("scenes")
        if not isinstance(rows, list) or any(not isinstance(row, dict) or "id" not in row for row in rows):
            raise ValueError("scenes must be rows with IDs")
        payload["scenes"] = rows
        return payload
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise RuntimeError("timings.json is unreadable or invalid") from exc


def render_video(
    project: ExplainerProject,
    *,
    output: str | Path | None = None,
    preview_seconds: float | None = None,
    preview_width: int | None = None,
    cancel_event: object = None,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Validate staged media, then publish video and matching receipt in sequence."""
    _require_pillow()
    output_width, output_height = _preview_dimensions(
        project, preview_seconds=preview_seconds, preview_width=preview_width,
    )
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required to encode explainer video")
    if not ffprobe:
        raise RuntimeError("FFprobe is required to validate explainer video")
    target = Path(output).expanduser().resolve(strict=False) if output else project.directory / (
        "preview.mp4" if preview_seconds else "explainer.mp4"
    )
    if target.suffix.lower() != ".mp4":
        raise ValueError("explainer video output must use the .mp4 extension")
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.stage.mp4")
    staged_report = stage.with_suffix(".render.json")
    timeline = project_timeline(project)
    timeline_end = max(float(row["start"]) + float(row["duration"]) for row in timeline)
    requested_duration = timeline_end
    if preview_seconds is not None:
        requested_duration = min(timeline_end, max(1.0, float(preview_seconds)))
    frames = max(1, math.ceil(requested_duration * project.fps))
    expected_duration = frames / project.fps
    render_factor = _supersampling(project)
    native_scale = output_width / project.width * render_factor
    native_size = (output_width * render_factor, output_height * render_factor)
    needs_scaled_project = (
        not math.isclose(native_scale, 1.0)
        or native_size != (project.width, project.height)
    )
    render_project = (
        _scaled_project(project, native_scale, width=native_size[0], height=native_size[1])
        if needs_scaled_project else project
    )
    render_timeline = _timeline_for_project(timeline, render_project) if needs_scaled_project else timeline
    audio = project.directory / "audio.wav"
    has_audio = audio.is_file()
    audio_details: dict[str, Any] | None = None
    if has_audio:
        audio_details = _validate_audio_binding(
            project,
            audio,
            timeline,
            required_duration=expected_duration,
            full_render=preview_seconds is None,
        )

    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{output_width}x{output_height}", "-r", str(project.fps), "-i", "-",
    ]
    if has_audio:
        command.extend(["-i", str(audio)])
    command.extend(["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"])
    if has_audio:
        command.extend(["-c:a", "aac", "-b:a", "160k"])
    else:
        command.append("-an")
    command.extend(["-t", f"{expected_duration:.6f}", "-movflags", "+faststart", str(stage)])

    if _cancel_requested(cancel_event):
        raise RuntimeError("explainer render cancelled")
    with _PreparedMedia(render_project, ffmpeg=ffmpeg) as prepared_media, tempfile.TemporaryFile(mode="w+b") as stderr_file:
        kwargs: dict[str, Any] = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.DEVNULL,
            "stderr": stderr_file,
        }
        apply_windows_hidden_process_flags(kwargs)
        process = subprocess.Popen(command, **kwargs)
        if process.stdin is None:
            _terminate_process(process)
            raise RuntimeError("FFmpeg did not expose its input stream")
        try:
            cadence = max(1, frames // 20)
            if progress is not None:
                progress(0, frames)
            for frame_index in range(frames):
                if _cancel_requested(cancel_event):
                    raise RuntimeError("explainer render cancelled")
                image = _render_video_frame(
                    render_project,
                    frame_index / project.fps,
                    timeline=render_timeline,
                    media=prepared_media,
                    maximum_seconds=max(0.0, expected_duration - 1.0 / project.fps),
                )
                if image.size != (output_width, output_height):
                    image = _resize_rgba(image, (output_width, output_height))
                process.stdin.write(image.convert("RGB").tobytes())
                completed = frame_index + 1
                if progress is not None and (completed == frames or completed % cadence == 0):
                    progress(completed, frames)
            process.stdin.close()
            if _cancel_requested(cancel_event):
                raise RuntimeError("explainer render cancelled")
            try:
                result = process.wait(timeout=max(30.0, min(600.0, expected_duration * 2.0)))
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("FFmpeg timed out while finalizing explainer video") from exc
            if result != 0:
                raise RuntimeError(f"FFmpeg failed: {_stream_tail(stderr_file, 500)}")
            inspection = probe_video(
                stage,
                ffprobe=ffprobe,
                expected_duration=expected_duration,
                expected_frames=frames,
                expected_width=output_width,
                expected_height=output_height,
                expected_fps=project.fps,
                expect_audio=has_audio,
            )
            inspection["narration"] = audio_details or {"source": "silent"}
            inspection["captions"] = {
                "mode": "burned-in",
                "timing": (
                    str((_timing_payload(project) or {}).get("caption_timing") or "scene-duration-linear-approximate")
                    if has_audio else "scene-duration-linear-approximate"
                ),
            }
            motion = project.data.get("style", {}).get("motion", {})
            inspection["motion_blur"] = {
                "samples": int(motion.get("blur_samples", 1)),
                "shutter_angle": float(motion.get("shutter_angle", 180.0)),
            }
            inspection["path"] = str(target)
            inspection["sha256"] = file_sha256(stage)
            atomic_write_text(
                staged_report,
                json.dumps(inspection, indent=2, ensure_ascii=False) + "\n",
            )
            _publish_video_pair(stage, staged_report, target, cancel_event=cancel_event)
        except BaseException:
            _terminate_process(process)
            stage.unlink(missing_ok=True)
            staged_report.unlink(missing_ok=True)
            raise
    return target


def _preview_dimensions(
    project: ExplainerProject,
    *,
    preview_seconds: float | None,
    preview_width: int | None,
) -> tuple[int, int]:
    if preview_width is None:
        return project.width, project.height
    if preview_seconds is None:
        raise ValueError("preview_width requires preview_seconds so it cannot replace the final render")
    if isinstance(preview_width, bool) or preview_width != int(preview_width):
        raise ValueError("preview_width must be an even integer")
    width = int(preview_width)
    if width % 2 or not 160 <= width <= project.width:
        raise ValueError(f"preview_width must be an even integer between 160 and {project.width}")
    height = round(project.height * width / project.width / 2.0) * 2
    if height < 90:
        raise ValueError("preview_width produces a preview shorter than 90 pixels")
    return width, height


def _render_video_frame(
    project: ExplainerProject,
    seconds: float,
    *,
    timeline: list[dict[str, Any]],
    media: "_PreparedMedia",
    maximum_seconds: float,
):
    """Render one native-sized frame, then apply one shared optical finish."""
    Image, _ImageDraw, _ImageFont = _require_pillow()
    motion = project.data.get("style", {}).get("motion", {})
    samples = int(motion.get("blur_samples", 1)) if isinstance(motion, dict) else 1
    if samples <= 1:
        return _finish_frame(
            _render_frame_native(project, seconds, timeline=timeline, _media=media),
            project,
        )
    shutter = float(motion.get("shutter_angle", 180.0))
    exposure = shutter / 360.0 / project.fps
    start = seconds - exposure / 2.0
    step = exposure / max(1, samples - 1)
    frames = [
        _render_frame_native(
            project,
            min(maximum_seconds, max(0.0, start + index * step)),
            timeline=timeline,
            _media=media,
        ).convert("RGBA")
        for index in range(samples)
    ]
    averaged = frames[0]
    for index, frame in enumerate(frames[1:], start=2):
        averaged = Image.blend(averaged, frame, 1.0 / index)
    return _finish_frame(averaged, project)


def _publish_video_pair(stage: Path, staged_report: Path, target: Path, *, cancel_event: object = None) -> None:
    """Publish a checked pair while recovering handled final-promotion failures."""
    if _cancel_requested(cancel_event):
        raise RuntimeError("explainer render cancelled")
    report_path = target.with_suffix(".render.json")
    prior_report = target.with_name(f".{target.stem}.{uuid.uuid4().hex}.previous.render.json")
    retain_prior_report = False
    try:
        if report_path.is_file():
            shutil.copyfile(report_path, prior_report)
        os.replace(staged_report, report_path)
        try:
            # The video is the commit point: until this succeeds, the prior video is intact.
            if _cancel_requested(cancel_event):
                raise RuntimeError("explainer render cancelled")
            os.replace(stage, target)
        except BaseException as promotion_error:
            try:
                if prior_report.is_file():
                    os.replace(prior_report, report_path)
                else:
                    report_path.unlink(missing_ok=True)
            except BaseException as recovery_error:
                retain_prior_report = prior_report.is_file()
                evidence = f"; prior receipt retained at {prior_report}" if retain_prior_report else ""
                raise RuntimeError(
                    "video publication failed and its prior receipt could not be restored"
                    f" ({type(recovery_error).__name__}: {recovery_error}){evidence}"
                ) from promotion_error
            raise
    finally:
        if not retain_prior_report:
            prior_report.unlink(missing_ok=True)


def probe_video(
    path: str | Path,
    *,
    ffprobe: str | None = None,
    expected_duration: float | None = None,
    expected_frames: int | None = None,
    expected_width: int | None = None,
    expected_height: int | None = None,
    expected_fps: int | None = None,
    expect_audio: bool | None = None,
) -> dict[str, Any]:
    """Return measured media metadata and fail on structural/duration drift."""
    executable = ffprobe or shutil.which("ffprobe")
    if not executable:
        raise RuntimeError("FFprobe is required to validate explainer video")
    candidate = Path(path).expanduser().resolve(strict=False)
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
            executable, "-v", "error", "-count_frames",
            "-show_entries", "format=duration:stream=index,codec_type,codec_name,width,height,avg_frame_rate,nb_read_frames,duration",
            "-of", "json", str(candidate),
        ], **kwargs)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("FFprobe timed out while validating explainer video") from exc
    if result.returncode != 0:
        raise RuntimeError(f"FFprobe failed: {str(result.stderr or '').strip()[-500:]}")
    try:
        payload = json.loads(result.stdout)
        streams = payload["streams"]
        duration = float(payload["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("FFprobe returned incomplete explainer metadata") from exc
    videos = [stream for stream in streams if stream.get("codec_type") == "video"]
    audios = [stream for stream in streams if stream.get("codec_type") == "audio"]
    if len(videos) != 1:
        raise RuntimeError("rendered explainer must contain exactly one video stream")
    video = videos[0]
    if video.get("codec_name") != "h264":
        raise RuntimeError("rendered explainer video codec must be H.264")
    if expected_width is not None and int(video.get("width") or 0) != expected_width:
        raise RuntimeError("rendered explainer width does not match the project")
    if expected_height is not None and int(video.get("height") or 0) != expected_height:
        raise RuntimeError("rendered explainer height does not match the project")
    observed_frames = int(video.get("nb_read_frames") or 0)
    if expected_frames is not None and abs(observed_frames - expected_frames) > 1:
        raise RuntimeError("rendered explainer frame coverage is incomplete")
    observed_fps = _rate_value(video.get("avg_frame_rate"))
    if expected_fps is not None and abs(observed_fps - expected_fps) > 0.01:
        raise RuntimeError("rendered explainer frame rate does not match the project")
    if expected_duration is not None:
        tolerance = max(0.15, 3.0 / max(1, int(expected_fps or 30)))
        if abs(duration - expected_duration) > tolerance:
            raise RuntimeError("rendered explainer duration does not match its timeline")
    if expect_audio is True and len(audios) != 1:
        raise RuntimeError("narrated explainer must contain exactly one audio stream")
    if expect_audio is False and audios:
        raise RuntimeError("silent explainer unexpectedly contains audio")
    if audios and audios[0].get("codec_name") != "aac":
        raise RuntimeError("rendered explainer audio codec must be AAC")
    return {
        "verified": True,
        "verification": "ffprobe",
        "duration_seconds": round(duration, 6),
        "frame_count": observed_frames,
        "fps": observed_fps,
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "video_codec": str(video.get("codec_name") or ""),
        "audio_codec": str(audios[0].get("codec_name") or "") if audios else "",
        "narrated": bool(audios),
    }


def _validate_audio_binding(
    project: ExplainerProject,
    audio: Path,
    timeline: list[dict[str, Any]],
    *,
    required_duration: float,
    full_render: bool,
) -> dict[str, Any]:
    payload = _timing_payload(project)
    if payload is None:
        raise RuntimeError("audio.wav requires timings.json so scenes cannot drift from narration")
    expected_narration = narration_digest(project)
    if str(payload.get("narration_sha256") or "") != expected_narration:
        raise RuntimeError("narration audio/timings are stale; regenerate or bind them to the current script")
    audio_sha256 = file_sha256_or_empty(audio)
    if not audio_sha256 or str(payload.get("audio_sha256") or "") != audio_sha256:
        raise RuntimeError("audio.wav does not match the timing artifact")
    details = _wav_details(audio)
    audio_duration = float(details["duration_seconds"])
    timeline_end = max(float(row["start"]) + float(row["duration"]) for row in timeline)
    tolerance = max(0.08, 2.0 / project.fps)
    if audio_duration + tolerance < required_duration:
        raise RuntimeError("audio.wav is shorter than the rendered timeline")
    if full_render and abs(audio_duration - timeline_end) > tolerance:
        raise RuntimeError("audio.wav duration does not match the full scene timeline")
    return {
        "source": str(payload.get("audio_source") or "supplied"),
        "audio_sha256": audio_sha256,
        "narration_sha256": expected_narration,
        **details,
    }


def _wav_details(path: Path) -> dict[str, Any]:
    try:
        with wave.open(str(path), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            frames = source.getnframes()
    except (OSError, wave.Error) as exc:
        raise RuntimeError("audio.wav must be a readable PCM WAV file") from exc
    if channels not in {1, 2} or sample_width != 2 or sample_rate <= 0 or frames <= 0:
        raise RuntimeError("audio.wav must be non-empty 16-bit mono or stereo PCM")
    return {
        "duration_seconds": round(frames / sample_rate, 6),
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_width": sample_width,
    }


def _rate_value(value: Any) -> float:
    try:
        numerator, denominator = str(value).split("/", 1)
        return float(numerator) / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise RuntimeError("FFprobe returned an invalid frame rate") from exc


def _cancel_requested(cancel_event: object) -> bool:
    return bool(getattr(cancel_event, "is_set", lambda: False)())


def _terminate_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        kill_process_tree(process.pid)
    try:
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _stream_tail(stream: Any, limit: int) -> str:
    try:
        stream.flush()
        stream.seek(0)
        return stream.read().decode("utf-8", errors="replace").strip()[-limit:]
    except (OSError, ValueError):
        return ""


class _PreparedMedia:
    """Decode each bounded clip once into task-owned temporary frames."""

    def __init__(self, project: ExplainerProject, *, ffmpeg: str | None = None) -> None:
        self.project = project
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg")
        self.assets = project.assets
        self.images: dict[str, Any] = {}
        self.video_frames: dict[str, list[Path]] = {}
        self._temporary: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self):
        Image, _ImageDraw, _ImageFont = _require_pillow()
        from .model import project_asset_path
        video_assets = [asset for asset in self.assets.values() if asset.get("type") == "video"]
        if video_assets and not self.ffmpeg:
            raise RuntimeError("FFmpeg is required to decode project video assets")
        self._temporary = tempfile.TemporaryDirectory(prefix="mo-explainer-media-")
        try:
            root = Path(self._temporary.name)
            for asset_id, asset in self.assets.items():
                path = project_asset_path(self.project, asset)
                if asset.get("type") == "image":
                    try:
                        with Image.open(path) as source:
                            self.images[asset_id] = source.convert("RGBA").copy()
                    except OSError as exc:
                        raise RuntimeError(f"image asset {asset_id} is unreadable") from exc
                    continue
                width = int(asset["width"])
                height = int(asset["height"])
                ratio = min(1.0, self.project.width / width, self.project.height / height)
                decoded_width = max(2, round(width * ratio))
                decoded_height = max(2, round(height * ratio))
                frames = root / asset_id
                frames.mkdir()
                command = [
                    str(self.ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(path), "-an", "-t", str(asset["duration_seconds"]),
                    "-vf", f"fps={self.project.fps},scale={decoded_width}:{decoded_height}:flags=lanczos",
                    "-q:v", "3", str(frames / "%06d.jpg"),
                ]
                kwargs: dict[str, Any] = {
                    "stdout": subprocess.DEVNULL, "stderr": subprocess.PIPE,
                    "text": True, "encoding": "utf-8", "errors": "replace", "timeout": 180,
                }
                apply_windows_hidden_process_flags(kwargs)
                try:
                    result = subprocess.run(command, **kwargs)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError(f"video asset {asset_id} decode timed out") from exc
                decoded = sorted(frames.glob("*.jpg"))
                if result.returncode != 0 or not decoded:
                    detail = str(result.stderr or "").strip()[-300:]
                    raise RuntimeError(f"video asset {asset_id} could not be decoded: {detail}")
                self.video_frames[asset_id] = decoded
        except BaseException:
            # A failed __enter__ never calls __exit__; release decoded scratch here.
            self._temporary.cleanup()
            raise
        return self

    def __exit__(self, *_args) -> None:
        if self._temporary is not None:
            self._temporary.cleanup()

    def frame(self, asset_id: str, seconds: float, *, loop: bool = False):
        if asset_id in self.images:
            return self.images[asset_id]
        frames = self.video_frames.get(asset_id, [])
        if not frames:
            return None
        index = int(max(0.0, seconds) * self.project.fps)
        if loop:
            index %= len(frames)
        elif index >= len(frames):
            return None
        Image, _ImageDraw, _ImageFont = _require_pillow()
        try:
            with Image.open(frames[index]) as source:
                return source.convert("RGBA").copy()
        except OSError as exc:
            raise RuntimeError(f"decoded video frame for {asset_id} is unreadable") from exc


def render_contact_sheet(
    project: ExplainerProject,
    *,
    output: str | Path | None = None,
    columns: int = 3,
) -> Path:
    """Render one representative still per scene for provider/operator QC."""
    Image, ImageDraw, _ImageFont = _require_pillow()
    factor = _supersampling(project)
    render_project = _scaled_project(project, factor) if factor > 1 else project
    timeline = project_timeline(render_project)
    with _PreparedMedia(render_project) as prepared_media:
        stills = []
        for row in timeline:
            still = render_frame(
                render_project,
                float(row["start"]) + float(row["duration"]) * 0.58,
                timeline=timeline,
                _media=prepared_media,
            )
            if factor > 1:
                still = _resize_rgba(still, (project.width, project.height))
            stills.append(still)
    thumb_width = 480
    thumb_height = round(thumb_width * project.height / project.width)
    columns = max(1, min(5, int(columns)))
    rows = math.ceil(len(stills) / columns)
    sheet = Image.new("RGB", (columns * thumb_width, rows * (thumb_height + 34)), "#080D12")
    draw = ImageDraw.Draw(sheet)
    font = _font(20, bold=True)
    for index, still in enumerate(stills):
        x = (index % columns) * thumb_width
        y = (index // columns) * (thumb_height + 34)
        sheet.paste(still.convert("RGB").resize((thumb_width, thumb_height), Image.Resampling.LANCZOS), (x, y))
        draw.text((x + 10, y + thumb_height + 6), str(project.scenes[index]["id"]), font=font, fill="#DCE6EC")
    target = Path(output).expanduser().resolve(strict=False) if output else project.directory / "contact-sheet.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(target)
    return target


def render_frame(
    project: ExplainerProject,
    seconds: float,
    *,
    timeline: list[dict[str, Any]] | None = None,
    _media: _PreparedMedia | None = None,
):
    """Render one output-sized frame with optional high-resolution sampling."""
    factor = _supersampling(project)
    if factor <= 1:
        return _finish_frame(
            _render_frame_native(project, seconds, timeline=timeline, _media=_media), project,
        )
    scaled = _scaled_project(project, factor)
    scaled_timeline = _timeline_for_project(timeline or project_timeline(project), scaled)
    high_resolution = _render_frame_native(
        scaled, seconds, timeline=scaled_timeline, _media=_media,
    )
    finished = _finish_frame(high_resolution, scaled)
    Image, _ImageDraw, _ImageFont = _require_pillow()
    return _resize_rgba(finished, (project.width, project.height))


def _render_frame_native(
    project: ExplainerProject,
    seconds: float,
    *,
    timeline: list[dict[str, Any]] | None = None,
    _media: _PreparedMedia | None = None,
):
    Image, ImageDraw, _ImageFont = _require_pillow()
    if _media is None and project.assets:
        with _PreparedMedia(project) as prepared_media:
            return _render_frame_native(project, seconds, timeline=timeline, _media=prepared_media)
    rows = timeline or project_timeline(project)
    total = max(float(item["start"]) + float(item["duration"]) for item in rows)
    row_index = len(rows) - 1
    for index, candidate in enumerate(rows):
        if seconds < float(candidate["start"]) + float(candidate["duration"]):
            row_index = index
            break
    row = rows[row_index]
    caption_local = seconds - float(row["start"])
    local = max(0.0, caption_local)
    transition = row["scene"].get("transition") or {}
    transition_seconds = (
        float(transition.get("duration", 0.5))
        if row_index > 0 and transition.get("type") == "crossfade" else 0.0
    )
    next_transition = (
        rows[row_index + 1]["scene"].get("transition") or {} if row_index + 1 < len(rows) else {}
    )
    crossfade_out = next_transition.get("type") == "crossfade" and float(next_transition.get("duration", 0.5)) > 0
    image = _render_scene_frame(
        project, row, caption_local, _media, fade_in=transition_seconds <= 0, fade_out=not crossfade_out,
    )
    if transition_seconds > 0 and local < transition_seconds:
        previous = rows[row_index - 1]
        previous_image = _render_scene_frame(
            project, previous, max(0.0, float(previous["duration"]) - 1.0 / project.fps), _media,
            fade_out=False,
        )
        image = Image.blend(previous_image, image, _ease(local / transition_seconds))
    decorations = (project.data.get("style") or {}).get("decorations", {})
    if decorations.get("timeline", True):
        image = image.convert("RGB")
        draw = ImageDraw.Draw(image, "RGBA")
        theme = dict(project.data["theme"])
        coordinate_scale = _coordinate_scale(project.data.get("style") or {})
        timeline_height = max(1, round(8 * coordinate_scale))
        progress = 0.0 if total <= 0 else min(1.0, max(0.0, seconds / total))
        draw.rectangle((0, project.height - timeline_height, project.width, project.height), fill=_rgba(theme["muted"], 90))
        draw.rectangle((0, project.height - timeline_height, round(project.width * progress), project.height), fill=_rgba(theme["accent"], 255))
        return image.convert("RGBA")
    return image if image.mode == "RGBA" else image.convert("RGBA")


def _supersampling(project: ExplainerProject) -> int:
    style = project.data.get("style") if isinstance(project.data.get("style"), dict) else {}
    render_style = style.get("render") if isinstance(style.get("render"), dict) else {}
    return max(1, int(render_style.get("supersampling", 1)))


def _coordinate_scale(style: dict[str, Any]) -> float:
    render_style = style.get("render") if isinstance(style.get("render"), dict) else {}
    return max(0.01, float(render_style.get("coordinate_scale", 1.0)))


def _scaled_project(
    project: ExplainerProject,
    factor: float,
    *,
    width: int | None = None,
    height: int | None = None,
) -> ExplainerProject:
    """Create one in-memory high-resolution view; source asset metadata stays factual."""
    data = deepcopy(project.data)
    data["width"] = int(width if width is not None else round(project.width * factor))
    data["height"] = int(height if height is not None else round(project.height * factor))
    style = data.setdefault("style", {})
    render_style = style.setdefault("render", {})
    render_style["supersampling"] = 1
    render_style["coordinate_scale"] = factor
    if "bloom_radius" in render_style:
        render_style["bloom_radius"] = float(render_style["bloom_radius"]) * factor
    for section, keys in (
        ("typography", ("caption_size",)),
        ("spacing", ("margin",)),
    ):
        values = style.get(section)
        if not isinstance(values, dict):
            continue
        for key in keys:
            if key in values:
                values[key] = float(values[key]) * factor
    spatial = {
        "x", "y", "x2", "y2", "width", "height", "radius", "stroke", "size",
        "target_x", "target_y", "curve", "head_size", "border_width", "blur", "depth",
    }
    for scene in data.get("scenes", []):
        for element in scene.get("elements", []):
            for key in spatial:
                if key in element:
                    element[key] = float(element[key]) * factor
            move = element.get("move")
            if isinstance(move, dict):
                for key in ("x", "y"):
                    if key in move:
                        move[key] = float(move[key]) * factor
            for row in element.get("keyframes", []):
                if not isinstance(row, dict):
                    continue
                for key in ("x", "y"):
                    if key in row:
                        row[key] = float(row[key]) * factor
            for effect_name in ("shadow", "glow"):
                effect = element.get(effect_name)
                if not isinstance(effect, dict):
                    continue
                for key in ("blur", "x", "y"):
                    if key in effect:
                        effect[key] = float(effect[key]) * factor
    return ExplainerProject(project.path, data)


def _timeline_for_project(
    timeline: list[dict[str, Any]], project: ExplainerProject,
) -> list[dict[str, Any]]:
    scenes = {str(scene["id"]): scene for scene in project.scenes}
    return [{**row, "scene": scenes[str(row["scene"]["id"])]} for row in timeline]


def _finish_frame(image, project: ExplainerProject):
    """Apply one restrained scene-wide optical pass so elements share a world."""
    Image, _ImageDraw, _ImageFont = _require_pillow()
    from PIL import ImageFilter

    style = project.data.get("style") if isinstance(project.data.get("style"), dict) else {}
    render_style = style.get("render") if isinstance(style.get("render"), dict) else {}
    bloom = float(render_style.get("bloom", 0.0))
    vignette = float(render_style.get("vignette", 0.0))
    grain = float(render_style.get("grain", 0.0))
    result = image.convert("RGBA")
    if bloom > 0:
        luminance = result.convert("RGB").convert("L")
        highlights = luminance.point(lambda value: max(0, min(255, (value - 148) * 3)))
        radius = float(render_style.get("bloom_radius", 12.0))
        if radius > 0:
            highlights = highlights.filter(ImageFilter.GaussianBlur(radius))
        highlights = highlights.point(lambda value: round(value * bloom))
        glow = result.copy()
        glow.putalpha(highlights)
        result = Image.alpha_composite(result, glow)
    if vignette > 0:
        mask = _vignette_mask(result.width, result.height).point(
            lambda value: round(value * vignette),
        )
        shade = Image.new("RGBA", result.size, (0, 0, 0, 0))
        shade.putalpha(mask)
        result = Image.alpha_composite(result, shade)
    if grain > 0:
        result = Image.alpha_composite(result, _grain_overlay(result.width, result.height, round(grain, 4)))
    return result


@lru_cache(maxsize=16)
def _vignette_mask(width: int, height: int):
    Image, _ImageDraw, _ImageFont = _require_pillow()
    size = 256
    center = (size - 1) / 2.0
    maximum = math.hypot(center, center)
    values = bytearray(size * size)
    offset = 0
    for y in range(size):
        for x in range(size):
            radius = math.hypot(x - center, y - center) / maximum
            values[offset] = round(255 * max(0.0, min(1.0, (radius - 0.34) / 0.66)) ** 1.8)
            offset += 1
    return Image.frombytes("L", (size, size), bytes(values)).resize((width, height), Image.Resampling.BICUBIC)


@lru_cache(maxsize=16)
def _grain_overlay(width: int, height: int, strength: float):
    Image, _ImageDraw, _ImageFont = _require_pillow()
    tile_size = 128
    pixels = bytearray(tile_size * tile_size * 4)
    state = 0x5A17C9E3
    for offset in range(0, len(pixels), 4):
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        noise = ((state >> 24) & 0xFF) - 128
        value = 255 if noise >= 0 else 0
        alpha = round(abs(noise) / 128.0 * 255 * strength)
        pixels[offset:offset + 4] = bytes((value, value, value, alpha))
    tile = Image.frombytes("RGBA", (tile_size, tile_size), bytes(pixels))
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for y in range(0, height, tile_size):
        for x in range(0, width, tile_size):
            overlay.paste(tile, (x, y))
    return overlay


def _render_scene_frame(
    project: ExplainerProject,
    row: dict[str, Any],
    local: float,
    media: _PreparedMedia | None,
    *,
    fade_in: bool = True,
    fade_out: bool = True,
):
    _Image, ImageDraw, _ImageFont = _require_pillow()
    scene = row["scene"]
    duration = float(row["duration"])
    style = dict(project.data.get("style") or {})
    theme = dict(project.data["theme"])
    decorations = style.get("decorations", {})
    coordinate_scale = _coordinate_scale(style)
    image = _background(
        project.width,
        project.height,
        theme["background"],
        grid=decorations.get("grid", True),
        coordinate_scale=coordinate_scale,
    ).copy()
    for element in scene.get("elements", []):
        _draw_element(image, element, local, duration, theme, style, media=media, fade_in=fade_in, fade_out=fade_out)
    evidence = scene_source_ids(scene)
    brand = style.get("brand") if isinstance(style.get("brand"), dict) else {}
    needs_header = (
        brand.get("enabled", False) is True
        or decorations.get("title", True)
        or decorations.get("scene_badge", True)
    )
    narration = str(scene.get("narration") or "")
    if not needs_header and not narration.strip() and not evidence:
        return image
    # Pillow blends translucent drawing onto RGB; on RGBA it replaces alpha.
    image = image.convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    _draw_header(draw, project, scene, theme, style)
    _draw_caption(
        draw,
        narration,
        local,
        min(duration, float(row.get("speech_duration", duration))),
        project,
        theme,
        style,
    )
    if evidence:
        source_domains = {
            str(source["id"]): str(source.get("url") or "").split("//", 1)[-1].split("/", 1)[0]
            for source in project.data.get("sources", [])
        }
        label = "Sources: " + ", ".join(f"[{item}] {source_domains.get(item, '')}".rstrip() for item in evidence)
        draw.text(
            (project.width - 28 * coordinate_scale, 79 * coordinate_scale),
            label,
            font=_font(max(1, round(18 * coordinate_scale))),
            fill=_rgba(theme["muted"], 230),
            anchor="ra",
        )
    return image.convert("RGBA")


@lru_cache(maxsize=16)
def _background(
    width: int,
    height: int,
    color: str,
    *,
    grid: bool = True,
    coordinate_scale: float = 1.0,
):
    Image, ImageDraw, _ImageFont = _require_pillow()
    image = Image.new("RGB", (width, height), color)
    if grid:
        draw = ImageDraw.Draw(image, "RGBA")
        inset = max(1, round(32 * coordinate_scale))
        spacing = max(2, round(48 * coordinate_scale))
        stagger = max(1, round(24 * coordinate_scale))
        dot = max(1, round(coordinate_scale))
        for y in range(inset, height, spacing):
            for x in range(inset + (y // spacing % 2) * stagger, width, spacing):
                draw.ellipse((x - dot, y - dot, x + dot, y + dot), fill=(255, 255, 255, 22))
    return image.convert("RGBA")


def _draw_element(
    image,
    element: dict[str, Any],
    local: float,
    duration: float,
    theme: dict[str, str],
    style: dict[str, Any],
    *,
    media: _PreparedMedia | None = None,
    fade_in: bool = True,
    fade_out: bool = True,
) -> None:
    Image, ImageDraw, _ImageFont = _require_pillow()
    start = float(element.get("start", 0.0))
    end = float(element.get("end", duration))
    if local < start or local > end:
        return
    motion = style.get("motion") if isinstance(style.get("motion"), dict) else {}
    preset_alpha, dx, dy, preset_scale, preset_draw = _motion(
        str(element.get("animation") or "fade"),
        local - start,
        max(0.01, end - start),
        entrance_seconds=float(motion.get("entrance_seconds", 0.65)),
        exit_seconds=float(motion.get("exit_seconds", 0.4)),
        # Scene crossfades own boundary opacity; explicit element windows keep
        # their own fades and visibility.
        fade_in=fade_in or start > 0,
        fade_out=fade_out or end < duration,
    )
    state, controlled = _keyframe_state(element, local, start)
    if "move" in element:
        move = element["move"]
        move_start = float(move.get("start", start))
        move_end = float(move.get("end", end))
        progress = min(1.0, max(0.0, (local - move_start) / (move_end - move_start)))
        state["x"] = _lerp(float(element.get("x", 0)), float(move.get("x", element.get("x", 0))), _ease(progress))
        state["y"] = _lerp(float(element.get("y", 0)), float(move.get("y", element.get("y", 0))), _ease(progress))
        controlled.update({"x", "y"})
    x = state["x"] + (0.0 if "x" in controlled else dx)
    y = state["y"] + (0.0 if "y" in controlled else dy)
    scale = state["scale"] if "scale" in controlled else state["scale"] * preset_scale
    draw_progress = state["draw"] if "draw" in controlled else preset_draw
    overall_alpha = preset_alpha * state["opacity"]
    if overall_alpha <= 0.001:
        return
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    rotation = state["rotation"]
    color = _rgba(_color(element.get("color"), theme), 255)
    kind = str(element.get("type"))
    default_fill_opacity = 0.92 if kind == "prism" else 52 / 255
    fill_alpha = 255 * float(element.get("fill_opacity", default_fill_opacity))
    fill_color = _rgba(_color(element.get("fill_color") or element.get("color"), theme), round(fill_alpha))
    base_width = float(element.get("width", 0))
    base_height = float(element.get("height", 0))
    width = float(element.get("width", 0)) * scale
    height = float(element.get("height", 0)) * scale
    prism_depth = 0.0
    prism_rise = 0.0
    if str(element.get("anchor") or "top_left") == "center":
        if kind == "prism":
            x, y, width, height, prism_depth, prism_rise = _prism_metrics(element, x, y, scale)
        elif kind in {"box", "bar", "image", "video", "callout"}:
            x -= (width - base_width) / 2.0
            y -= (height - base_height) / 2.0
    elif kind == "prism":
        x, y, width, height, prism_depth, prism_rise = _prism_metrics(element, x, y, scale)
    stroke = max(1, round(float(element.get("stroke", 4)) * scale))
    if kind == "text":
        draw.multiline_text(**text_layout(element, x, y, scale), fill=color)
    elif kind == "box":
        box = (x, y, x + width, y + height)
        radius = float(element.get("radius", 24)) * scale
        if element.get("fill", True):
            _fill_shape(layer, box, radius, fill_color, element.get("gradient"), theme)
        draw.rounded_rectangle(box, radius=radius, outline=color, width=stroke)
    elif kind == "circle":
        radius = float(element.get("radius", 40)) * scale
        box = (x - radius, y - radius, x + radius, y + radius)
        if element.get("fill", True):
            _fill_shape(layer, box, radius, fill_color, element.get("gradient"), theme, ellipse=True)
        draw.ellipse(box, outline=color, width=stroke)
    elif kind == "prism":
        front = [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
        top = [
            (x, y),
            (x + prism_depth, y - prism_rise),
            (x + width + prism_depth, y - prism_rise),
            (x + width, y),
        ]
        side = [
            (x + width, y),
            (x + width + prism_depth, y - prism_rise),
            (x + width + prism_depth, y + height - prism_rise),
            (x + width, y + height),
        ]
        base = _color(element.get("fill_color") or element.get("color"), theme)
        face_alpha = round(fill_alpha)
        top_fill = _rgba(
            _color(element.get("top_color"), theme)
            if element.get("top_color") else _mix_color(base, theme["foreground"], 0.24),
            face_alpha,
        )
        side_fill = _rgba(
            _color(element.get("side_color"), theme)
            if element.get("side_color") else _mix_color(base, theme["background"], 0.34),
            face_alpha,
        )
        if element.get("fill", True):
            draw.polygon(top, fill=top_fill)
            draw.polygon(side, fill=side_fill)
            draw.polygon(front, fill=fill_color)
        for face in (top, side, front):
            draw.line(face + [face[0]], fill=color, width=stroke, joint="curve")
    elif kind in {"line", "arrow"}:
        x2, y2 = _connector_end(element, x, y, scale)
        _draw_connector(
            draw,
            (x, y),
            (x2, y2),
            color=color,
            width=stroke,
            curve=float(element.get("curve", 0.0)) * scale,
            progress=draw_progress,
            head=str(element.get("head") or ("triangle" if kind == "arrow" else "none")),
            head_size=float(
                element.get("head_size", max(12.0, float(element.get("stroke", 4.0)) * 4.0))
            ) * scale,
        )
    elif kind == "bar":
        value = min(1.0, max(0.0, float(element.get("value", 0.5))))
        track = (x, y, x + width, y + height)
        fill = (x, y, x + width * value * draw_progress, y + height)
        _fill_shape(layer, track, height / 2, _rgba(theme["muted"], 70), None, theme)
        if fill[2] > fill[0]:
            _fill_shape(layer, fill, min(height / 2, (fill[2] - fill[0]) / 2), color, element.get("gradient"), theme)
    elif kind in {"image", "video"} and media is not None:
        asset_id = str(element.get("asset_id") or "")
        source_seconds = 0.0
        if kind == "video":
            trim_start = float(element.get("trim_start", 0.0))
            duration = float(media.assets[asset_id]["duration_seconds"])
            trim_end = min(duration, float(element.get("trim_end", duration)))
            span = max(0.01, trim_end - trim_start)
            source_seconds = trim_start + max(0.0, local - start)
            if element.get("loop", False):
                source_seconds = trim_start + (source_seconds - trim_start) % span
            elif source_seconds >= trim_end:
                source_seconds = -1.0
        source = media.frame(asset_id, source_seconds, loop=False) if source_seconds >= 0 else None
        if source is not None:
            progress = min(1.0, max(0.0, (local - start) / max(0.01, end - start)))
            zoom = _lerp(float(element.get("zoom", 1.0)), float(element.get("zoom_to", element.get("zoom", 1.0))), _ease(progress))
            pan_x = _lerp(float(element.get("pan_x", 0.0)), float(element.get("pan_to_x", element.get("pan_x", 0.0))), _ease(progress))
            pan_y = _lerp(float(element.get("pan_y", 0.0)), float(element.get("pan_to_y", element.get("pan_y", 0.0))), _ease(progress))
            fitted = _fit_media(source, max(1, round(width)), max(1, round(height)), str(element.get("fit") or "cover"), zoom, pan_x, pan_y)
            radius = max(0, round(float(element.get("radius", 0.0)) * scale))
            if radius:
                fitted = _rounded_media(fitted, radius)
            layer.alpha_composite(fitted, (round(x), round(y)))
            border = round(float(element.get("border_width", 0.0)) * scale)
            if border > 0:
                border_color = _rgba(_color(element.get("border_color") or element.get("color"), theme), 255)
                draw.rounded_rectangle(
                    (x, y, x + width, y + height), radius=radius, outline=border_color, width=border,
                )
    elif kind == "callout":
        box = (x, y, x + width, y + height)
        radius = float(element.get("radius", 18)) * scale
        callout_fill = _rgba(
            _color(element.get("fill_color") or "background", theme),
            round(255 * float(element.get("fill_opacity", 225 / 255))),
        )
        _fill_shape(layer, box, radius, callout_fill, element.get("gradient"), theme)
        draw.rounded_rectangle(box, radius=radius, outline=color, width=stroke)
        target_x = float(element.get("target_x", x))
        target_y = float(element.get("target_y", y))
        anchor_x = x if target_x < x else x + width
        anchor_y = min(y + height, max(y, target_y))
        _draw_connector(
            draw,
            (anchor_x, anchor_y),
            (target_x, target_y),
            color=color,
            width=stroke,
            curve=float(element.get("curve", 0.0)) * scale,
        )
        marker = max(4, 6 * scale)
        draw.ellipse((target_x - marker, target_y - marker, target_x + marker, target_y + marker), fill=color)
        font = _font(max(12, round(float(element.get("size", 28)) * scale)), bold=True)
        text = _wrap_text(str(element.get("text") or ""), font, max(40, round(width - 32)))
        draw.multiline_text((x + 16 * scale, y + 14 * scale), text, font=font, fill=_rgba(theme["foreground"], 255), spacing=max(5, round(5 * scale)))
    if overall_alpha < 1.0:
        layer.putalpha(layer.getchannel("A").point(lambda value: round(value * max(0.0, overall_alpha))))
    prepared = _prepare_element_layer(
        layer,
        pivot=_element_pivot(element, x, y, width, height, scale),
        rotation=rotation,
        padding=_effect_padding(element),
    )
    if prepared is None:
        return
    layer, offset = prepared
    blur = float(element.get("blur", 0.0))
    if blur > 0:
        from PIL import ImageFilter

        layer = layer.filter(ImageFilter.GaussianBlur(blur))
    _composite_effects(image, layer, element, theme, offset=offset)


def _keyframe_state(
    element: dict[str, Any],
    local: float,
    start: float,
) -> tuple[dict[str, float], set[str]]:
    """Interpolate sparse absolute keyframes while preserving stable holds."""
    state = {
        "x": float(element.get("x", 0.0)),
        "y": float(element.get("y", 0.0)),
        "scale": float(element.get("scale", 1.0)),
        "opacity": float(element.get("opacity", 1.0)),
        "rotation": float(element.get("rotation", 0.0)),
        "draw": 1.0,
    }
    rows = element.get("keyframes")
    if not isinstance(rows, list) or not rows:
        return state, set()
    controlled = {key for row in rows if isinstance(row, dict) for key in state if key in row}
    anchors: list[tuple[float, dict[str, float], str]] = [(start, state.copy(), "linear")]
    current = state.copy()
    for row in rows:
        at = float(row["at"])
        for key in state:
            if key in row:
                current[key] = float(row[key])
        anchor = (at, current.copy(), str(row.get("ease") or "ease_in_out"))
        if abs(at - anchors[-1][0]) < 1e-9:
            anchors[-1] = anchor
        else:
            anchors.append(anchor)
    if local <= anchors[0][0]:
        return anchors[0][1], controlled
    for index in range(1, len(anchors)):
        right_at, right_state, easing = anchors[index]
        left_at, left_state, _left_easing = anchors[index - 1]
        if local <= right_at:
            progress = (local - left_at) / max(0.000001, right_at - left_at)
            eased = _keyframe_ease(progress, easing)
            return {
                key: _mix(left_state[key], right_state[key], eased)
                for key in state
            }, controlled
    return anchors[-1][1], controlled


def _keyframe_ease(value: float, name: str) -> float:
    progress = min(1.0, max(0.0, value))
    if name == "linear":
        return progress
    if name == "ease_in":
        return progress ** 3
    if name == "ease_out":
        return 1.0 - (1.0 - progress) ** 3
    if name == "ease_out_back":
        overshoot = 1.70158
        shifted = progress - 1.0
        return 1.0 + (overshoot + 1.0) * shifted ** 3 + overshoot * shifted ** 2
    return progress * progress * (3.0 - 2.0 * progress)


def _draw_connector(
    draw,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    color: tuple[int, int, int, int],
    width: int,
    curve: float = 0.0,
    progress: float = 1.0,
    head: str = "none",
    head_size: float = 16.0,
) -> None:
    progress = min(1.0, max(0.0, progress))
    if progress <= 0:
        return
    x1, y1 = start
    x2, y2 = end
    distance = math.hypot(x2 - x1, y2 - y1)
    if distance < 0.01:
        return
    normal_x = -(y2 - y1) / distance
    normal_y = (x2 - x1) / distance
    control = ((x1 + x2) / 2.0 + normal_x * curve, (y1 + y2) / 2.0 + normal_y * curve)
    steps = max(8, min(240, math.ceil((distance + abs(curve)) / 8.0)))
    points: list[tuple[float, float]] = []
    visible_steps = max(1, math.ceil(steps * progress))
    for index in range(visible_steps + 1):
        t = min(progress, index / steps)
        inverse = 1.0 - t
        points.append((
            inverse * inverse * x1 + 2.0 * inverse * t * control[0] + t * t * x2,
            inverse * inverse * y1 + 2.0 * inverse * t * control[1] + t * t * y2,
        ))
    if len(points) < 2:
        return
    draw.line(points, fill=color, width=width, joint="curve")
    radius = max(0.5, width / 2.0)
    draw.ellipse((x1 - radius, y1 - radius, x1 + radius, y1 + radius), fill=color)
    tip_x, tip_y = points[-1]
    prior_x, prior_y = points[-2]
    tangent_length = max(0.001, math.hypot(tip_x - prior_x, tip_y - prior_y))
    unit_x = (tip_x - prior_x) / tangent_length
    unit_y = (tip_y - prior_y) / tangent_length
    if head == "none":
        draw.ellipse((tip_x - radius, tip_y - radius, tip_x + radius, tip_y + radius), fill=color)
        return
    size = max(4.0, head_size)
    base_x = tip_x - unit_x * size
    base_y = tip_y - unit_y * size
    wing = size * 0.48
    left = (base_x - unit_y * wing, base_y + unit_x * wing)
    right = (base_x + unit_y * wing, base_y - unit_x * wing)
    if head == "chevron":
        draw.line((left, (tip_x, tip_y), right), fill=color, width=width, joint="curve")
    else:
        draw.polygon(((tip_x, tip_y), left, right), fill=color)


def _fill_shape(
    layer,
    bounds: tuple[float, float, float, float],
    radius: float,
    fill: tuple[int, int, int, int],
    gradient: Any,
    theme: dict[str, str],
    *,
    ellipse: bool = False,
) -> None:
    Image, ImageDraw, _ImageFont = _require_pillow()
    left, top, right, bottom = bounds
    width = max(1, round(right - left))
    height = max(1, round(bottom - top))
    mask = Image.new("L", (width, height), 0)
    mask_draw = ImageDraw.Draw(mask)
    local_bounds = (0, 0, width - 1, height - 1)
    opacity = fill[3]
    if ellipse:
        mask_draw.ellipse(local_bounds, fill=opacity)
    else:
        mask_draw.rounded_rectangle(
            local_bounds,
            radius=max(0, min(float(radius), width / 2.0, height / 2.0)),
            fill=opacity,
        )
    if isinstance(gradient, dict):
        fill_layer = _gradient_image(
            width,
            height,
            _color(gradient.get("from"), theme),
            _color(gradient.get("to"), theme),
            round(float(gradient.get("angle", 0.0)), 3),
        ).copy()
    else:
        fill_layer = Image.new("RGBA", (width, height), fill[:3] + (255,))
    fill_layer.putalpha(mask)
    layer.alpha_composite(fill_layer, (round(left), round(top)))


@lru_cache(maxsize=128)
def _gradient_image(width: int, height: int, start: str, end: str, angle: float):
    Image, _ImageDraw, _ImageFont = _require_pillow()
    from PIL import ImageOps

    start_rgb = _rgba(start, 255)[:3]
    end_rgb = _rgba(end, 255)[:3]
    radians = math.radians(angle)
    vector_x = math.cos(radians)
    vector_y = math.sin(radians)
    projections = (
        0.0,
        (width - 1) * vector_x,
        (height - 1) * vector_y,
        (width - 1) * vector_x + (height - 1) * vector_y,
    )
    minimum = min(projections)
    span = max(0.000001, max(projections) - minimum)
    ramp = Image.linear_gradient("L").rotate(90)
    mask = ramp.transform(
        (width, height),
        Image.Transform.AFFINE,
        (
            vector_x * 255.0 / span,
            vector_y * 255.0 / span,
            -minimum * 255.0 / span,
            0.0,
            0.0,
            128.0,
        ),
        resample=Image.Resampling.BILINEAR,
    )
    return ImageOps.colorize(mask, start_rgb, end_rgb).convert("RGBA")


def _rounded_media(image, radius: int):
    Image, ImageDraw, _ImageFont = _require_pillow()
    from PIL import ImageChops

    result = image.copy()
    mask = Image.new("L", result.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, result.width - 1, result.height - 1),
        radius=min(radius, result.width // 2, result.height // 2),
        fill=255,
    )
    result.putalpha(ImageChops.multiply(result.getchannel("A"), mask))
    return result


def _element_pivot(
    element: dict[str, Any],
    x: float,
    y: float,
    width: float,
    height: float,
    scale: float,
) -> tuple[float, float]:
    kind = str(element.get("type") or "")
    if kind == "circle":
        return x, y
    if kind in {"line", "arrow"}:
        x2, y2 = _connector_end(element, x, y, scale)
        return (x + x2) / 2.0, (y + y2) / 2.0
    if kind == "text":
        return (
            x + float(element.get("width", 800)) * scale / 2.0,
            y + float(element.get("size", 44)) * scale / 2.0,
        )
    if kind == "prism":
        depth = float(element.get("depth", 0.0)) * scale
        rise = depth * 0.65
        return x + (width + depth) / 2.0, y + (height - rise) / 2.0
    return x + width / 2.0, y + height / 2.0


def _connector_end(element: dict[str, Any], x: float, y: float, scale: float) -> tuple[float, float]:
    """Scaled end point of a line or arrow whose animated start is ``(x, y)``."""
    return (
        x + (float(element.get("x2", x)) - float(element.get("x", 0))) * scale,
        y + (float(element.get("y2", y)) - float(element.get("y", 0))) * scale,
    )


def _prism_metrics(
    element: dict[str, Any],
    x: float,
    y: float,
    scale: float,
) -> tuple[float, float, float, float, float, float]:
    """Return anchor-adjusted projected prism metrics for render and QC."""
    base_width = float(element.get("width", 0.0))
    base_height = float(element.get("height", 0.0))
    base_depth = float(element.get("depth", 0.0))
    width = base_width * scale
    height = base_height * scale
    depth = base_depth * scale
    base_rise = base_depth * 0.65
    rise = depth * 0.65
    if str(element.get("anchor") or "top_left") == "center":
        center_x = x + (base_width + base_depth) / 2.0
        center_y = y + (base_height - base_rise) / 2.0
        x = center_x - (width + depth) / 2.0
        y = center_y - (height - rise) / 2.0
    return x, y, width, height, depth, rise


def _effect_padding(element: dict[str, Any]) -> int:
    """Bound local optical work without clipping Gaussian effect tails."""
    padding = float(element.get("blur", 0.0)) * 3.0
    for name in ("shadow", "glow"):
        spec = element.get(name)
        if not isinstance(spec, dict) or float(spec.get("opacity", 0.35)) <= 0:
            continue
        extent = float(spec.get("blur", 18.0)) * 3.0
        if name == "shadow":
            extent += max(abs(float(spec.get("x", 0.0))), abs(float(spec.get("y", 10.0))))
        padding = max(padding, extent)
    return max(2, math.ceil(padding))


def _prepare_element_layer(
    layer,
    *,
    pivot: tuple[float, float],
    rotation: float,
    padding: int,
):
    """Crop one sparse frame layer before transforms and optical effects."""
    Image, _ImageDraw, _ImageFont = _require_pillow()
    bounds = layer.getbbox()
    if bounds is None:
        return None
    if rotation:
        pivot_x, pivot_y = round(pivot[0]), round(pivot[1])
        half_width = max(pivot_x - bounds[0], bounds[2] - pivot_x, 1) + 2
        half_height = max(pivot_y - bounds[1], bounds[3] - pivot_y, 1) + 2
        crop_box = (
            pivot_x - half_width,
            pivot_y - half_height,
            pivot_x + half_width,
            pivot_y + half_height,
        )
        local = layer.crop(crop_box).rotate(
            rotation,
            resample=Image.Resampling.BICUBIC,
            expand=True,
        )
        offset = (
            round(pivot_x - local.width / 2.0),
            round(pivot_y - local.height / 2.0),
        )
    else:
        local = layer.crop(bounds)
        offset = (bounds[0], bounds[1])
    if padding > 0:
        padded = Image.new("RGBA", (local.width + padding * 2, local.height + padding * 2), (0, 0, 0, 0))
        padded.alpha_composite(local, (padding, padding))
        local = padded
        offset = (offset[0] - padding, offset[1] - padding)
    return local, offset


def _composite_effects(
    image,
    layer,
    element: dict[str, Any],
    theme: dict[str, str],
    *,
    offset: tuple[int, int] = (0, 0),
) -> None:
    Image, _ImageDraw, _ImageFont = _require_pillow()
    from PIL import ImageFilter

    for name in ("shadow", "glow"):
        spec = element.get(name)
        if not isinstance(spec, dict) or float(spec.get("opacity", 0.35)) <= 0:
            continue
        mask = layer.getchannel("A")
        blur = float(spec.get("blur", 18.0))
        if blur > 0:
            mask = mask.filter(ImageFilter.GaussianBlur(blur))
        opacity = float(spec.get("opacity", 0.35))
        mask = mask.point(lambda value: round(value * opacity))
        default_color = "#000000" if name == "shadow" else element.get("color") or "accent"
        effect = Image.new("RGBA", layer.size, _rgba(_color(spec.get("color") or default_color, theme), 255))
        effect.putalpha(mask)
        effect_offset = (
            round(float(spec.get("x", 0.0))) if name == "shadow" else 0,
            round(float(spec.get("y", 10.0))) if name == "shadow" else 0,
        )
        image.alpha_composite(effect, (offset[0] + effect_offset[0], offset[1] + effect_offset[1]))
    image.alpha_composite(layer, offset)


def _fit_media(source, width: int, height: int, fit: str, zoom: float, pan_x: float, pan_y: float):
    Image, _ImageDraw, _ImageFont = _require_pillow()
    source = source.convert("RGBA")
    base = min(width / source.width, height / source.height) if fit == "contain" else max(width / source.width, height / source.height)
    scale = base * zoom
    resized = _resize_rgba(
        source,
        (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
    )
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    overflow_x = max(0, resized.width - width)
    overflow_y = max(0, resized.height - height)
    left = round((width - resized.width) / 2 - pan_x * overflow_x / 2)
    top = round((height - resized.height) / 2 - pan_y * overflow_y / 2)
    canvas.alpha_composite(resized, (left, top))
    return canvas


def _resize_rgba(image, size: tuple[int, int]):
    """LANCZOS-resize RGBA at one shared alpha-aware boundary."""
    Image, _ImageDraw, _ImageFont = _require_pillow()
    rgba = image if image.mode == "RGBA" else image.convert("RGBA")
    return rgba.resize(size, Image.Resampling.LANCZOS)


def _lerp(start: float, end: float, progress: float) -> float:
    return start + (end - start) * min(1.0, max(0.0, progress))


def _mix(start: float, end: float, progress: float) -> float:
    return start + (end - start) * progress


def text_layout(element: dict[str, Any], x: float, y: float, scale: float = 1.0) -> dict[str, Any]:
    """Share the renderer's exact wrapping, font and anchor with bounds checks."""
    size = max(12, round(float(element.get("size", 44)) * scale))
    font = _font(size, bold=str(element.get("weight") or "bold") != "regular")
    lines = _wrap_text(str(element.get("text") or ""), font, max(80, int(element.get("width", 800))))
    align = str(element.get("align") or "left")
    anchor = {"left": "la", "center": "ma", "right": "ra"}.get(align, "la")
    tx = x if align == "left" else x + float(element.get("width", 800)) / (2 if align == "center" else 1)
    return {
        "xy": (tx, y), "text": lines, "font": font,
        "spacing": max(5, size // 6), "align": align, "anchor": anchor,
    }


def _motion(
    name: str,
    elapsed: float,
    duration: float,
    *,
    entrance_seconds: float,
    exit_seconds: float,
    fade_in: bool = True,
    fade_out: bool = True,
) -> tuple[float, float, float, float, float]:
    if name == "none":
        return 1.0, 0.0, 0.0, 1.0, 1.0
    entrance_window = max(0.05, min(entrance_seconds, duration * 0.35))
    exit_window = max(0.05, min(exit_seconds, duration * 0.35))
    entrance = _ease(min(1.0, elapsed / entrance_window))
    exit_progress = _ease(min(1.0, max(0.0, (elapsed - duration + exit_window) / exit_window)))
    alpha = (entrance if fade_in else 1.0) * (1.0 - exit_progress if fade_out else 1.0)
    drift = math.sin(elapsed * 1.4) * 1.5
    if name == "rise":
        return alpha, 0.0, 34 * (1.0 - entrance) + drift, 1.0, 1.0
    if name == "slide_left":
        return alpha, 55 * (1.0 - entrance), drift, 1.0, 1.0
    if name == "slide_right":
        return alpha, -55 * (1.0 - entrance), drift, 1.0, 1.0
    if name == "scale":
        return alpha, 0.0, drift, 0.88 + entrance * 0.12, 1.0
    if name == "draw":
        return alpha, 0.0, 0.0, 1.0, entrance
    return alpha, 0.0, drift, 1.0, 1.0


def _draw_header(
    draw,
    project: ExplainerProject,
    scene: dict[str, Any],
    theme: dict[str, str],
    style: dict[str, Any],
) -> None:
    coordinate_scale = _coordinate_scale(style)
    spacing = style.get("spacing") if isinstance(style.get("spacing"), dict) else {}
    margin = int(spacing.get("margin", 32 * coordinate_scale))
    brand =style.get("brand") if isinstance(style.get("brand"), dict) else {}
    brand_enabled = brand.get("enabled", False) is True
    title_x = margin
    if brand_enabled and str(brand.get("mark") or "four-cube") == "four-cube":
        draw_four_cube_mark(
            draw,
            margin,
            20 * coordinate_scale,
            cube_size=max(1, round(9 * coordinate_scale)),
            gap=max(1, round(3 * coordinate_scale)),
            radius=max(1, round(2 * coordinate_scale)),
            fill=_rgba(theme["accent"], 255),
            shade=_rgba(theme["background"], 170),
        )
        title_x += 34 * coordinate_scale
    brand_name = str(brand.get("name") or "").strip() if brand_enabled else ""
    decorations = style.get("decorations", {})
    title = project.title if decorations.get("title", True) else ""
    label = " · ".join(value for value in (brand_name, title) if value)
    if label:
        draw.text(
            (title_x, 25 * coordinate_scale),
            label,
            font=_font(max(1, round(22 * coordinate_scale)), bold=True),
            fill=_rgba(theme["foreground"], 220),
        )
    if decorations.get("scene_badge", True):
        kind = str(scene.get("kind") or "concept").upper()
        badge_width = max(110, len(kind) * 14) * coordinate_scale
        draw.rounded_rectangle(
            (margin, 62 * coordinate_scale, margin + badge_width, 96 * coordinate_scale),
            radius=17 * coordinate_scale,
            fill=_rgba(theme["accent"], 45),
        )
        draw.text(
            (margin + 16 * coordinate_scale, 79 * coordinate_scale),
            kind,
            font=_font(max(1, round(16 * coordinate_scale)), bold=True),
            fill=_rgba(theme["accent"], 255),
            anchor="lm",
        )


def _draw_caption(
    draw,
    narration: str,
    local: float,
    speech_duration: float,
    project: ExplainerProject,
    theme: dict[str, str],
    style: dict[str, Any],
) -> None:
    chunks = caption_chunks(narration)
    if not chunks or speech_duration <= 0 or local < 0 or local >= speech_duration:
        return
    index = min(len(chunks) - 1, int((local / max(0.01, speech_duration)) * len(chunks)))
    caption, font, background, position = caption_layout(draw, chunks[index], project, style)
    coordinate_scale = _coordinate_scale(style)
    draw.rounded_rectangle(background, radius=20 * coordinate_scale, fill=(4, 9, 13, 205))
    draw.multiline_text(
        position,
        caption,
        font=font,
        fill=_rgba(theme["foreground"], 255),
        spacing=max(1, round(6 * coordinate_scale)),
        align="center",
        anchor="ma",
    )


def caption_chunks(narration: str) -> list[str]:
    words = narration.split()
    return [" ".join(words[i:i + 12]) for i in range(0, len(words), 12)]


def caption_layout(draw, caption: str, project: ExplainerProject, style: dict[str, Any]):
    """Measure the exact caption placement shared by drawing and quality checks."""
    typography = style.get("typography") if isinstance(style.get("typography"), dict) else {}
    spacing = style.get("spacing") if isinstance(style.get("spacing"), dict) else {}
    coordinate_scale = _coordinate_scale(style)
    margin = max(round(24 * coordinate_scale), int(spacing.get("margin", 32 * coordinate_scale)))
    font = _font(int(typography.get("caption_size", 28 * coordinate_scale)), bold=True)
    caption = _wrap_text(caption, font, project.width - margin * 5)
    line_spacing = max(1, round(6 * coordinate_scale))
    box = draw.multiline_textbbox((0, 0), caption, font=font, spacing=line_spacing, align="center")
    text_height = box[3] - box[1]
    bottom = project.height - max(round(18 * coordinate_scale), margin * 3 // 4)
    top = bottom - text_height - 18 * coordinate_scale
    background = (
        margin * 2,
        top - 12 * coordinate_scale,
        project.width - margin * 2,
        bottom + 6 * coordinate_scale,
    )
    return caption, font, background, (project.width / 2, top)


def _wrap_text(text: str, font, max_width: int) -> str:
    words = text.split()
    if not words:
        return ""
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if font.getlength(candidate) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return "\n".join(lines)


@lru_cache(maxsize=128)
def _font(size: int, bold: bool = False):
    _Image, _ImageDraw, ImageFont = _require_pillow()
    candidates: Iterable[str] = (
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _color(value: Any, theme: dict[str, str]) -> str:
    text = str(value or "foreground")
    return theme.get(text, text if text.startswith("#") else theme["foreground"])


def _mix_color(start: str, end: str, amount: float) -> str:
    start_rgb = _rgba(start, 255)[:3]
    end_rgb = _rgba(end, 255)[:3]
    ratio = min(1.0, max(0.0, amount))
    mixed = tuple(round(left + (right - left) * ratio) for left, right in zip(start_rgb, end_rgb))
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def _rgba(color: str, alpha: int) -> tuple[int, int, int, int]:
    text = str(color).lstrip("#")
    return tuple(int(text[index:index + 2], 16) for index in (0, 2, 4)) + (max(0, min(255, alpha)),)


def _ease(value: float) -> float:
    return 1.0 - (1.0 - value) ** 3


def _require_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise RuntimeError("Pillow is required; install MO's optional computer-use requirements") from exc
    return Image, ImageDraw, ImageFont
