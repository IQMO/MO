"""Deterministic Pillow-to-FFmpeg renderer for MO explainer projects."""
from __future__ import annotations

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
    cancel_event: object = None,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Validate staged media, then publish video and matching receipt in sequence."""
    _require_pillow()
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
        "-s", f"{project.width}x{project.height}", "-r", str(project.fps), "-i", "-",
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
    with _PreparedMedia(project, ffmpeg=ffmpeg) as prepared_media, tempfile.TemporaryFile(mode="w+b") as stderr_file:
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
                image = render_frame(
                    project, frame_index / project.fps, timeline=timeline, _media=prepared_media,
                )
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
                expected_width=project.width,
                expected_height=project.height,
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
    timeline = project_timeline(project)
    with _PreparedMedia(project) as prepared_media:
        stills = [
            render_frame(
                project,
                float(row["start"]) + float(row["duration"]) * 0.58,
                timeline=timeline,
                _media=prepared_media,
            )
            for row in timeline
        ]
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
    Image, ImageDraw, _ImageFont = _require_pillow()
    if _media is None and project.assets:
        with _PreparedMedia(project) as prepared_media:
            return render_frame(project, seconds, timeline=timeline, _media=prepared_media)
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
    image = image.convert("RGB")
    decorations = (project.data.get("style") or {}).get("decorations", {})
    if decorations.get("timeline", True):
        draw = ImageDraw.Draw(image, "RGBA")
        theme = dict(project.data["theme"])
        progress = 0.0 if total <= 0 else min(1.0, max(0.0, seconds / total))
        draw.rectangle((0, project.height - 8, project.width, project.height), fill=_rgba(theme["muted"], 90))
        draw.rectangle((0, project.height - 8, round(project.width * progress), project.height), fill=_rgba(theme["accent"], 255))
    return image.convert("RGBA")


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
    image = _background(
        project.width, project.height, theme["background"], grid=decorations.get("grid", True),
    ).copy()
    for element in scene.get("elements", []):
        _draw_element(image, element, local, duration, theme, style, media=media, fade_in=fade_in, fade_out=fade_out)
    # Pillow blends translucent drawing onto RGB; on RGBA it replaces alpha.
    image = image.convert("RGB")
    draw = ImageDraw.Draw(image, "RGBA")
    _draw_header(draw, project, scene, theme, style)
    _draw_caption(
        draw,
        str(scene.get("narration") or ""),
        local,
        min(duration, float(row.get("speech_duration", duration))),
        project,
        theme,
        style,
    )
    evidence = scene_source_ids(scene)
    if evidence:
        source_domains = {
            str(source["id"]): str(source.get("url") or "").split("//", 1)[-1].split("/", 1)[0]
            for source in project.data.get("sources", [])
        }
        label = "Sources: " + ", ".join(f"[{item}] {source_domains.get(item, '')}".rstrip() for item in evidence)
        draw.text((project.width - 28, 79), label, font=_font(18), fill=_rgba(theme["muted"], 230), anchor="ra")
    return image.convert("RGBA")


@lru_cache(maxsize=8)
def _background(width: int, height: int, color: str, *, grid: bool = True):
    Image, ImageDraw, _ImageFont = _require_pillow()
    image = Image.new("RGB", (width, height), color)
    if grid:
        draw = ImageDraw.Draw(image, "RGBA")
        for y in range(32, height, 48):
            for x in range(32 + (y // 48 % 2) * 24, width, 48):
                draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=(255, 255, 255, 22))
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
    alpha, dx, dy, scale, draw_progress = _motion(
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
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer, "RGBA")
    x = float(element.get("x", 0)) + dx
    y = float(element.get("y", 0)) + dy
    color = _rgba(_color(element.get("color"), theme), round(255 * alpha))
    fill_alpha = 255 * float(element.get("fill_opacity", 52 / 255))
    fill_color = _rgba(_color(element.get("fill_color") or element.get("color"), theme), round(fill_alpha * alpha))
    width = float(element.get("width", 0)) * scale
    height = float(element.get("height", 0)) * scale
    kind = str(element.get("type"))
    stroke = max(1, int(element.get("stroke", 4)))
    if kind == "text":
        draw.multiline_text(**text_layout(element, x, y, scale), fill=color)
    elif kind == "box":
        box = (x, y, x + width, y + height)
        fill = fill_color if element.get("fill", True) else None
        draw.rounded_rectangle(box, radius=int(element.get("radius", 24)), fill=fill, outline=color, width=stroke)
    elif kind == "circle":
        radius = float(element.get("radius", 40)) * scale
        box = (x - radius, y - radius, x + radius, y + radius)
        fill = fill_color if element.get("fill", True) else None
        draw.ellipse(box, fill=fill, outline=color, width=stroke)
    elif kind in {"line", "arrow"}:
        x2 = x + (float(element.get("x2", x)) - float(element.get("x", 0))) * draw_progress
        y2 = y + (float(element.get("y2", y)) - float(element.get("y", 0))) * draw_progress
        draw.line((x, y, x2, y2), fill=color, width=stroke)
        if kind == "arrow" and draw_progress > 0.92:
            angle = math.atan2(y2 - y, x2 - x)
            head = max(12, stroke * 4)
            points = [(x2, y2)]
            for offset in (2.55, -2.55):
                points.append((x2 + math.cos(angle + offset) * head, y2 + math.sin(angle + offset) * head))
            draw.polygon(points, fill=color)
    elif kind == "bar":
        value = min(1.0, max(0.0, float(element.get("value", 0.5))))
        draw.rounded_rectangle((x, y, x + width, y + height), radius=height / 2, fill=_rgba(theme["muted"], 70))
        draw.rounded_rectangle((x, y, x + width * value * draw_progress, y + height), radius=height / 2, fill=color)
    elif kind in {"image", "video"} and media is not None:
        if "move" in element:
            move = element["move"]
            move_start = float(move.get("start", start))
            move_end = float(move.get("end", end))
            progress = min(1.0, max(0.0, (local - move_start) / (move_end - move_start)))
            # Explicit placement owns translation, including the destination
            # hold; preset drift/entrance offsets must not move it again.
            x = _lerp(float(element.get("x", 0)), float(move.get("x", element.get("x", 0))), _ease(progress))
            y = _lerp(float(element.get("y", 0)), float(move.get("y", element.get("y", 0))), _ease(progress))
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
            opacity = min(1.0, max(0.0, float(element.get("opacity", 1.0)))) * alpha
            if opacity < 1.0:
                fitted.putalpha(fitted.getchannel("A").point(lambda value: round(value * opacity)))
            layer.alpha_composite(fitted, (round(x), round(y)))
    elif kind == "callout":
        box = (x, y, x + width, y + height)
        draw.rounded_rectangle(box, radius=18, fill=_rgba(theme["background"], round(225 * alpha)), outline=color, width=stroke)
        target_x = float(element.get("target_x", x))
        target_y = float(element.get("target_y", y))
        anchor_x = x if target_x < x else x + width
        anchor_y = min(y + height, max(y, target_y))
        draw.line((anchor_x, anchor_y, target_x, target_y), fill=color, width=stroke)
        draw.ellipse((target_x - 6, target_y - 6, target_x + 6, target_y + 6), fill=color)
        font = _font(int(element.get("size", 28)), bold=True)
        text = _wrap_text(str(element.get("text") or ""), font, max(40, round(width - 32)))
        draw.multiline_text((x + 16, y + 14), text, font=font, fill=_rgba(theme["foreground"], round(255 * alpha)), spacing=5)
    image.alpha_composite(layer)


def _fit_media(source, width: int, height: int, fit: str, zoom: float, pan_x: float, pan_y: float):
    Image, _ImageDraw, _ImageFont = _require_pillow()
    source = source.convert("RGBA")
    base = min(width / source.width, height / source.height) if fit == "contain" else max(width / source.width, height / source.height)
    scale = base * zoom
    resized = source.resize(
        (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
        Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    overflow_x = max(0, resized.width - width)
    overflow_y = max(0, resized.height - height)
    left = round((width - resized.width) / 2 - pan_x * overflow_x / 2)
    top = round((height - resized.height) / 2 - pan_y * overflow_y / 2)
    canvas.alpha_composite(resized, (left, top))
    return canvas


def _lerp(start: float, end: float, progress: float) -> float:
    return start + (end - start) * min(1.0, max(0.0, progress))


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
    spacing = style.get("spacing") if isinstance(style.get("spacing"), dict) else {}
    margin = int(spacing.get("margin", 32))
    brand = style.get("brand") if isinstance(style.get("brand"), dict) else {}
    brand_enabled = brand.get("enabled", False) is True
    title_x = margin
    if brand_enabled and str(brand.get("mark") or "four-cube") == "four-cube":
        draw_four_cube_mark(
            draw,
            margin,
            20,
            cube_size=9,
            gap=3,
            radius=2,
            fill=_rgba(theme["accent"], 255),
            shade=_rgba(theme["background"], 170),
        )
        title_x += 34
    brand_name = str(brand.get("name") or "").strip() if brand_enabled else ""
    decorations = style.get("decorations", {})
    title = project.title if decorations.get("title", True) else ""
    label = " · ".join(value for value in (brand_name, title) if value)
    if label:
        draw.text((title_x, 25), label, font=_font(22, bold=True), fill=_rgba(theme["foreground"], 220))
    if decorations.get("scene_badge", True):
        kind = str(scene.get("kind") or "concept").upper()
        draw.rounded_rectangle((margin, 62, margin + max(110, len(kind) * 14), 96), radius=17, fill=_rgba(theme["accent"], 45))
        draw.text((margin + 16, 79), kind, font=_font(16, bold=True), fill=_rgba(theme["accent"], 255), anchor="lm")


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
    draw.rounded_rectangle(background, radius=20, fill=(4, 9, 13, 205))
    draw.multiline_text(position, caption, font=font, fill=_rgba(theme["foreground"], 255), spacing=6, align="center", anchor="ma")


def caption_chunks(narration: str) -> list[str]:
    words = narration.split()
    return [" ".join(words[i:i + 12]) for i in range(0, len(words), 12)]


def caption_layout(draw, caption: str, project: ExplainerProject, style: dict[str, Any]):
    """Measure the exact caption placement shared by drawing and quality checks."""
    typography = style.get("typography") if isinstance(style.get("typography"), dict) else {}
    spacing = style.get("spacing") if isinstance(style.get("spacing"), dict) else {}
    margin = max(24, int(spacing.get("margin", 32)))
    font = _font(int(typography.get("caption_size", 28)), bold=True)
    caption = _wrap_text(caption, font, project.width - margin * 5)
    box = draw.multiline_textbbox((0, 0), caption, font=font, spacing=6, align="center")
    text_height = box[3] - box[1]
    bottom = project.height - max(18, margin * 3 // 4)
    top = bottom - text_height - 18
    background = (margin * 2, top - 12, project.width - margin * 2, bottom + 6)
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
