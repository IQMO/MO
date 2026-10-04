"""Deterministic pre-render quality checks for explainer projects."""
from __future__ import annotations

import math
from typing import Any

from .model import ExplainerProject, scene_source_ids
from .render import (
    _connector_end,
    _keyframe_state,
    _prism_metrics,
    _require_pillow,
    caption_chunks,
    caption_layout,
    project_timeline,
    text_layout,
)


def quality_report(project: ExplainerProject) -> dict[str, Any]:
    timeline = project_timeline(project)
    Image, ImageDraw, _ImageFont = _require_pillow()
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    used_sources: set[str] = set()
    style = project.data.get("style") if isinstance(project.data.get("style"), dict) else {}
    assets = project.assets
    spacing = style.get("spacing") if isinstance(style.get("spacing"), dict) else {}
    subtitle_lane = int(spacing.get("subtitle_lane", 80))
    for row in timeline:
        scene = row["scene"]
        scene_id = str(scene["id"])
        duration = float(row.get("speech_duration") or row["duration"])
        words = len(str(scene.get("narration") or "").split())
        rate = words / max(duration, 0.01)
        if rate > 3.1:
            warnings.append({"scene": scene_id, "code": "fast_narration", "detail": f"{rate:.2f} words/second"})
        if rate < 0.45 and words > 2:
            warnings.append({"scene": scene_id, "code": "sparse_narration", "detail": f"{rate:.2f} words/second"})
        used_sources.update(scene_source_ids(scene))
        safe_bottom = project.height - subtitle_lane
        for caption in caption_chunks(str(scene.get("narration") or "")):
            _text, _font, (left, top, right, bottom), _position = caption_layout(draw, caption, project, style)
            safe_bottom = min(safe_bottom, top)
            if left >= right or top >= bottom or left < 0 or right > project.width or top < 0 or bottom > project.height:
                errors.append({"scene": scene_id, "code": "caption_bounds", "detail": "caption background does not fit within the frame"})
                break
        for element in scene.get("elements", []):
            _check_bounds(draw, element, scene_id, project.width, safe_bottom, errors)
            media_warning = _media_resolution_warning(element, assets)
            if media_warning:
                warnings.append({"scene": scene_id, "code": "upscaled_media", "detail": media_warning})
            if element.get("type") == "text" and len(str(element.get("text") or "")) > 180:
                warnings.append({"scene": scene_id, "code": "dense_text", "detail": "visual text exceeds 180 characters"})
    declared_sources = {str(source["id"]) for source in project.data.get("sources", [])}
    for source_id in sorted(declared_sources - used_sources):
        warnings.append({"scene": "-", "code": "unused_source", "detail": source_id})
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "scene_count": len(project.scenes),
        "source_count": len(declared_sources),
        "duration_seconds": round(max(float(row["start"]) + float(row["duration"]) for row in timeline), 3),
        "claim_coverage": round(
            sum(bool(scene.get("claims")) for scene in project.scenes[1:-1]) / max(1, len(project.scenes[1:-1])), 3
        ),
    }


def _check_bounds(
    draw,
    element: dict[str, Any],
    scene_id: str,
    width: int,
    safe_bottom: float,
    errors: list[dict[str, str]],
) -> None:
    try:
        start = float(element.get("start", 0.0))
        end = float(element.get("end", start))
    except (TypeError, ValueError):
        return
    sample_times = {start, end}
    keyframes = element.get("keyframes")
    if isinstance(keyframes, list):
        times = [start] + [float(row["at"]) for row in keyframes if isinstance(row, dict) and isinstance(row.get("at"), (int, float))]
        for left, right in zip(times, times[1:]):
            span = right - left
            sample_times.update(left + span * fraction for fraction in (0.0, 0.25, 0.5, 0.6, 0.75, 1.0))
    bounds: list[tuple[float, float, float, float]] = []
    for seconds in sorted(sample_times):
        state, _controlled = _keyframe_state(element, seconds, start)
        if state.get("opacity", 1.0) > 0.001:
            bounds.append(_effect_bounds(element, _element_bounds(draw, element, state)))
    if "move" in element and isinstance(element["move"], dict):
        moved = {
            "x": float(element["move"].get("x", element.get("x", 0))),
            "y": float(element["move"].get("y", element.get("y", 0))),
            "scale": float(element.get("scale", 1.0)),
            "rotation": float(element.get("rotation", 0.0)),
        }
        bounds.append(_effect_bounds(element, _element_bounds(draw, element, moved)))
    if not bounds:
        return
    left = min(item[0] for item in bounds)
    top = min(item[1] for item in bounds)
    right = max(item[2] for item in bounds)
    bottom = max(item[3] for item in bounds)
    if left < 0 or right > width or top < 0 or bottom > safe_bottom:
        kind = str(element.get("type") or "")
        errors.append({"scene": scene_id, "code": "unsafe_bounds", "detail": f"{kind} enters frame edge or subtitle lane"})


def _element_bounds(
    draw,
    element: dict[str, Any],
    state: dict[str, float],
) -> tuple[float, float, float, float]:
    kind = str(element.get("type") or "")
    x = float(state.get("x", element.get("x", 0)))
    y = float(state.get("y", element.get("y", 0)))
    scale = float(state.get("scale", element.get("scale", 1.0)))
    rotation = float(state.get("rotation", element.get("rotation", 0.0)))
    if kind in {"box", "prism", "bar", "image", "video", "callout"}:
        base_width = float(element.get("width", 0))
        base_height = float(element.get("height", 0))
        element_width = base_width * scale
        element_height = base_height * scale
        if kind == "prism":
            x, y, element_width, element_height, depth, rise = _prism_metrics(element, x, y, scale)
            bounds = (x, y - rise, x + element_width + depth, y + element_height)
        else:
            if str(element.get("anchor") or "top_left") == "center":
                x -= (element_width - base_width) / 2.0
                y -= (element_height - base_height) / 2.0
            bounds = (x, y, x + element_width, y + element_height)
        if kind == "callout":
            target_x = float(element.get("target_x", x))
            target_y = float(element.get("target_y", y))
            bounds = (
                min(bounds[0], target_x), min(bounds[1], target_y),
                max(bounds[2], target_x), max(bounds[3], target_y),
            )
    elif kind == "text":
        bounds = tuple(float(value) for value in draw.multiline_textbbox(**text_layout(element, x, y, scale)))
    elif kind == "circle":
        radius = float(element.get("radius", 0)) * scale
        bounds = (x - radius, y - radius, x + radius, y + radius)
    elif kind in {"line", "arrow"}:
        x2, y2 = _connector_end(element, x, y, scale)
        curve = abs(float(element.get("curve", 0.0)) * scale)
        bounds = (min(x, x2) - curve, min(y, y2) - curve, max(x, x2) + curve, max(y, y2) + curve)
    else:
        bounds = (x, y, x, y)
    if not rotation:
        return bounds
    left, top, right, bottom = bounds
    radians = math.radians(rotation)
    rotated_width = abs((right - left) * math.cos(radians)) + abs((bottom - top) * math.sin(radians))
    rotated_height = abs((right - left) * math.sin(radians)) + abs((bottom - top) * math.cos(radians))
    center_x = (left + right) / 2.0
    center_y = (top + bottom) / 2.0
    return (
        center_x - rotated_width / 2.0,
        center_y - rotated_height / 2.0,
        center_x + rotated_width / 2.0,
        center_y + rotated_height / 2.0,
    )


def _effect_bounds(
    element: dict[str, Any],
    bounds: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    left, top, right, bottom = bounds
    element_blur = float(element.get("blur", 0.0))
    if element_blur > 0:
        spread = element_blur * 2.0
        left -= spread
        top -= spread
        right += spread
        bottom += spread
    for name in ("shadow", "glow"):
        effect = element.get(name)
        if not isinstance(effect, dict) or float(effect.get("opacity", 0.35)) <= 0:
            continue
        spread = float(effect.get("blur", 18.0)) * 2.0
        offset_x = float(effect.get("x", 0.0)) if name == "shadow" else 0.0
        offset_y = float(effect.get("y", 10.0)) if name == "shadow" else 0.0
        left = min(left, bounds[0] + offset_x - spread)
        top = min(top, bounds[1] + offset_y - spread)
        right = max(right, bounds[2] + offset_x + spread)
        bottom = max(bottom, bounds[3] + offset_y + spread)
    return left, top, right, bottom


def _media_resolution_warning(
    element: dict[str, Any],
    assets: dict[str, dict[str, Any]],
) -> str:
    if element.get("type") not in {"image", "video"}:
        return ""
    asset_id = str(element.get("asset_id") or "")
    asset = assets.get(asset_id)
    if not asset:
        return ""
    try:
        source_width = float(asset["width"])
        source_height = float(asset["height"])
        frame_width = float(element["width"])
        frame_height = float(element["height"])
        scales = [float(element.get("scale", 1.0))]
        for row in element.get("keyframes", []):
            if isinstance(row, dict) and "scale" in row:
                scales.append(float(row["scale"]))
        zoom = max(
            float(element.get("zoom", 1.0)),
            float(element.get("zoom_to", element.get("zoom", 1.0))),
        )
    except (KeyError, TypeError, ValueError):
        return ""
    width_ratio = frame_width * max(scales) / source_width
    height_ratio = frame_height * max(scales) / source_height
    fit_scale = min(width_ratio, height_ratio) if element.get("fit") == "contain" else max(width_ratio, height_ratio)
    factor = fit_scale * zoom
    if factor <= 1.05:
        return ""
    return f"{asset_id} renders at up to {factor:.2f}x its source resolution"
