"""Deterministic pre-render quality checks for explainer projects."""
from __future__ import annotations

from typing import Any

from .model import ExplainerProject, scene_source_ids
from .render import _require_pillow, caption_chunks, caption_layout, project_timeline, text_layout


def quality_report(project: ExplainerProject) -> dict[str, Any]:
    timeline = project_timeline(project)
    Image, ImageDraw, _ImageFont = _require_pillow()
    draw = ImageDraw.Draw(Image.new("L", (1, 1)))
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    used_sources: set[str] = set()
    style = project.data.get("style") if isinstance(project.data.get("style"), dict) else {}
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
        x = float(element.get("x", 0))
        y = float(element.get("y", 0))
    except (TypeError, ValueError):
        return
    kind = str(element.get("type") or "")
    x2 = x
    y2 = y
    if kind in {"box", "bar", "image", "video", "callout"}:
        x2 += float(element.get("width", 0))
        y2 += float(element.get("height", 0))
        if kind in {"image", "video"} and "move" in element:
            move = element["move"]
            target_x = float(move.get("x", x))
            target_y = float(move.get("y", y))
            x2 = max(x2, target_x + float(element.get("width", 0)))
            y2 = max(y2, target_y + float(element.get("height", 0)))
            x, y = min(x, target_x), min(y, target_y)
    elif kind == "text":
        x, y, x2, y2 = draw.multiline_textbbox(**text_layout(element, x, y))
    elif kind == "circle":
        radius = float(element.get("radius", 0))
        x -= radius
        y -= radius
        x2 += radius
        y2 += radius
    elif kind in {"line", "arrow"}:
        x2 = float(element.get("x2", x))
        y2 = float(element.get("y2", y))
    if min(x, x2) < 0 or max(x, x2) > width or min(y, y2) < 0 or max(y, y2) > safe_bottom:
        errors.append({"scene": scene_id, "code": "unsafe_bounds", "detail": f"{kind} enters frame edge or subtitle lane"})
