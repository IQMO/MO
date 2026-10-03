"""Schema loading and evidence checks for MO explainer projects."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from core.utils.file_hash import file_sha256


PROJECT_VERSION = 1
SUPPORTED_KINDS = frozenset({"title", "concept", "process", "comparison", "summary"})
SUPPORTED_ELEMENTS = frozenset({"text", "box", "circle", "line", "arrow", "bar", "image", "video", "callout"})
SUPPORTED_ANIMATIONS = frozenset({"none", "fade", "rise", "slide_left", "slide_right", "scale", "draw"})
SUPPORTED_LAYOUTS = frozenset({"explanation", "process", "comparison", "product-demo", "custom"})
SUPPORTED_TRANSITIONS = frozenset({"cut", "crossfade"})
ASSET_ORIGINS = frozenset({"user", "captured", "mo-generated", "licensed-local"})
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")


class ProjectValidationError(ValueError):
    """Raised when a project cannot be rendered safely or reproducibly."""


@dataclass(frozen=True)
class ExplainerProject:
    path: Path
    data: dict[str, Any]

    @property
    def directory(self) -> Path:
        return self.path.parent

    @property
    def title(self) -> str:
        return str(self.data["title"])

    @property
    def width(self) -> int:
        return int(self.data.get("width", 1280))

    @property
    def height(self) -> int:
        return int(self.data.get("height", 720))

    @property
    def fps(self) -> int:
        return int(self.data.get("fps", 30))

    @property
    def scenes(self) -> list[dict[str, Any]]:
        return list(self.data["scenes"])

    @property
    def assets(self) -> dict[str, dict[str, Any]]:
        return {str(asset["id"]): dict(asset) for asset in self.data.get("assets", [])}


def scene_source_ids(scene: dict[str, Any]) -> list[str]:
    """Return stable source IDs from the scene's one claim ledger."""
    identifiers: set[str] = set()
    for claim in scene.get("claims", []):
        if isinstance(claim, dict):
            identifiers.update(str(item) for item in claim.get("source_ids", []))
    return sorted(identifiers)


def narration_digest(project: ExplainerProject) -> str:
    """Bind derived speech/timing artifacts to their effective narration inputs."""
    inputs = [
        {
            "id": str(scene["id"]),
            "narration": str(scene.get("narration") or ""),
            "duration": float(scene.get("duration", 6.0)),
        }
        for scene in project.scenes
    ]
    speed = float((project.data.get("voice") or {}).get("speed", 1.0))
    # Preserve bindings for existing projects using the model's default pace.
    effective_inputs = inputs if speed == 1.0 else {"scenes": inputs, "voice_speed": speed}
    encoded = json.dumps(effective_inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def load_project(path: str | Path) -> ExplainerProject:
    candidate = Path(path).expanduser().resolve(strict=False)
    if candidate.is_dir():
        candidate = candidate / "project.json"
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProjectValidationError(f"project file not found: {candidate}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectValidationError(f"cannot read project JSON: {type(exc).__name__}") from exc
    if not isinstance(payload, dict):
        raise ProjectValidationError("project JSON must be an object")
    issues = validate_project(payload)
    if not issues:
        issues.extend(validate_project_assets(candidate.parent, payload))
    if issues:
        raise ProjectValidationError("; ".join(issues))
    return ExplainerProject(candidate, payload)


def validate_project(data: dict[str, Any]) -> list[str]:
    """Return all deterministic schema/evidence issues in a project document."""
    issues: list[str] = []
    if data.get("version") != PROJECT_VERSION:
        issues.append(f"version must be {PROJECT_VERSION}")
    title = str(data.get("title") or "").strip()
    if not title:
        issues.append("title is required")
    _bounded_int(data, "width", 640, 3840, issues, default=1280, even=True)
    _bounded_int(data, "height", 360, 2160, issues, default=720, even=True)
    _bounded_int(data, "fps", 12, 60, issues, default=30)
    theme = data.get("theme", {})
    if not isinstance(theme, dict):
        issues.append("theme must be an object")
    else:
        for key in ("background", "foreground", "accent", "secondary", "muted"):
            value = theme.get(key)
            if value is None:
                issues.append(f"theme.{key} is required")
            elif not _HEX_COLOR.fullmatch(str(value)):
                issues.append(f"theme.{key} must be a six-digit hex color")

    style = data.get("style", {})
    if not isinstance(style, dict):
        issues.append("style must be an object")
    elif style:
        layout = str(style.get("layout") or "custom")
        if layout not in SUPPORTED_LAYOUTS:
            issues.append("style.layout is unsupported")
        source = str(style.get("source") or "custom")
        if source not in {"mo-system", "custom"}:
            issues.append("style.source must be mo-system or custom")
        skin = str(style.get("skin") or "").strip()
        if source == "mo-system" and not skin:
            issues.append("style.skin is required for MO system styling")
        brand = style.get("brand", {})
        if not isinstance(brand, dict):
            issues.append("style.brand must be an object")
        else:
            if "enabled" in brand and not isinstance(brand["enabled"], bool):
                issues.append("style.brand.enabled must be a boolean")
            if str(brand.get("mark") or "four-cube") not in {"four-cube", "none"}:
                issues.append("style.brand.mark must be four-cube or none")
            if len(str(brand.get("name") or "MO")) > 40:
                issues.append("style.brand.name must be at most 40 characters")
        decorations = style.get("decorations", {})
        if not isinstance(decorations, dict):
            issues.append("style.decorations must be an object")
        else:
            for key in ("grid", "title", "scene_badge", "timeline"):
                if key in decorations and not isinstance(decorations[key], bool):
                    issues.append(f"style.decorations.{key} must be a boolean")
        for section, fields in {
            "typography": {"caption_size": (12.0, 96.0)},
            "spacing": {"margin": (0.0, 240.0), "subtitle_lane": (40.0, 240.0)},
            "motion": {"entrance_seconds": (0.05, 3.0), "exit_seconds": (0.05, 3.0)},
        }.items():
            values = style.get(section, {})
            if not isinstance(values, dict):
                issues.append(f"style.{section} must be an object")
                continue
            if section == "typography" and values.get("family", "system-sans") != "system-sans":
                issues.append("style.typography.family must be system-sans")
            for key, (minimum, maximum) in fields.items():
                if key not in values:
                    continue
                value = _finite_number(values[key], f"style.{section}.{key}", issues)
                if value is not None and not minimum <= value <= maximum:
                    issues.append(f"style.{section}.{key} must be between {minimum:g} and {maximum:g}")

    voice = data.get("voice", {})
    if not isinstance(voice, dict):
        issues.append("voice must be an object")
    elif "speed" in voice:
        _bounded_asset_number(voice, "speed", 0.5, 2.0, "voice", issues)
    _validate_brief(data.get("brief"), issues)

    assets_by_id: dict[str, dict[str, Any]] = {}
    assets = data.get("assets", [])
    if not isinstance(assets, list):
        issues.append("assets must be a list")
        assets = []
    for index, asset in enumerate(assets):
        prefix = f"assets[{index}]"
        if not isinstance(asset, dict):
            issues.append(f"{prefix} must be an object")
            continue
        asset_id = str(asset.get("id") or "")
        media_type = str(asset.get("type") or "")
        if not _SAFE_ID.fullmatch(asset_id):
            issues.append(f"{prefix}.id is invalid")
        elif asset_id in assets_by_id:
            issues.append(f"duplicate asset id: {asset_id}")
        else:
            assets_by_id[asset_id] = asset
        if media_type not in {"image", "video"}:
            issues.append(f"{prefix}.type must be image or video")
        raw_path = str(asset.get("path") or "")
        asset_path = Path(raw_path)
        if not raw_path or asset_path.is_absolute() or ".." in asset_path.parts or asset_path.parts[:1] != ("media",):
            issues.append(f"{prefix}.path must be a project-owned relative media path")
        if not _SHA256.fullmatch(str(asset.get("sha256") or "")):
            issues.append(f"{prefix}.sha256 must be a lowercase SHA-256 digest")
        if str(asset.get("origin") or "") not in ASSET_ORIGINS:
            issues.append(f"{prefix}.origin is unsupported")
        _bounded_asset_number(asset, "width", 1, 7680, prefix, issues, integer=True)
        _bounded_asset_number(asset, "height", 1, 4320, prefix, issues, integer=True)
        if media_type == "video":
            _bounded_asset_number(asset, "duration_seconds", 0.1, 30.0, prefix, issues)
            _bounded_asset_number(asset, "fps", 1.0, 120.0, prefix, issues)

    source_ids: set[str] = set()
    sources = data.get("sources")
    if not isinstance(sources, list):
        issues.append("sources must be a list")
        sources = []
    for index, source in enumerate(sources):
        prefix = f"sources[{index}]"
        if not isinstance(source, dict):
            issues.append(f"{prefix} must be an object")
            continue
        source_id = str(source.get("id") or "")
        if not _SAFE_ID.fullmatch(source_id):
            issues.append(f"{prefix}.id is invalid")
        elif source_id in source_ids:
            issues.append(f"duplicate source id: {source_id}")
        else:
            source_ids.add(source_id)
        if not str(source.get("title") or "").strip():
            issues.append(f"{prefix}.title is required")
        parsed = urlparse(str(source.get("url") or ""))
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            issues.append(f"{prefix}.url must be an http(s) URL")

    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        issues.append("scenes must be a non-empty list")
        return issues
    scene_ids: set[str] = set()
    for index, scene in enumerate(scenes):
        prefix = f"scenes[{index}]"
        if not isinstance(scene, dict):
            issues.append(f"{prefix} must be an object")
            continue
        scene_id = str(scene.get("id") or "")
        if not _SAFE_ID.fullmatch(scene_id):
            issues.append(f"{prefix}.id is invalid")
        elif scene_id in scene_ids:
            issues.append(f"duplicate scene id: {scene_id}")
        else:
            scene_ids.add(scene_id)
        kind = str(scene.get("kind") or "concept")
        if kind not in SUPPORTED_KINDS:
            issues.append(f"{prefix}.kind is unsupported")
        if "factual" in scene and not isinstance(scene["factual"], bool):
            issues.append(f"{prefix}.factual must be a boolean")
        factual = scene.get("factual") is not False
        narration = str(scene.get("narration") or "").strip()
        if not narration and factual:
            issues.append(f"{prefix}.narration is required unless factual is false")
        duration = 6.0
        try:
            duration = float(scene.get("duration", 6.0))
            if not math.isfinite(duration) or not 1.0 <= duration <= 60.0:
                raise ValueError
        except (TypeError, ValueError):
            issues.append(f"{prefix}.duration must be between 1 and 60 seconds")
        transition = scene.get("transition", {"type": "cut"})
        if not isinstance(transition, dict):
            issues.append(f"{prefix}.transition must be an object")
        else:
            transition_type = str(transition.get("type") or "cut")
            if transition_type not in SUPPORTED_TRANSITIONS:
                issues.append(f"{prefix}.transition.type is unsupported")
            transition_duration = _finite_number(
                transition.get("duration", 0.0 if transition_type == "cut" else 0.5),
                f"{prefix}.transition.duration",
                issues,
            )
            if transition_duration is not None and not 0.0 <= transition_duration <= min(2.0, duration / 2):
                issues.append(f"{prefix}.transition.duration must fit within the scene and be at most 2 seconds")
        claims = scene.get("claims", [])
        if not isinstance(claims, list):
            issues.append(f"{prefix}.claims must be a list")
            claims = []
        if index not in {0, len(scenes) - 1} and factual and not claims:
            issues.append(f"{prefix}.claims are required for factual content")
        for claim_index, claim in enumerate(claims):
            claim_prefix = f"{prefix}.claims[{claim_index}]"
            if not isinstance(claim, dict):
                issues.append(f"{claim_prefix} must be an object")
                continue
            if not str(claim.get("text") or "").strip():
                issues.append(f"{claim_prefix}.text is required")
            claim_sources = claim.get("source_ids")
            if not isinstance(claim_sources, list) or not claim_sources:
                issues.append(f"{claim_prefix}.source_ids must be a non-empty list")
                continue
            unknown = sorted({str(item) for item in claim_sources} - source_ids)
            if unknown:
                issues.append(f"{claim_prefix}.source_ids reference unknown sources: {', '.join(unknown)}")
        elements = scene.get("elements", [])
        if not isinstance(elements, list) or not elements:
            issues.append(f"{prefix}.elements must be a non-empty list")
            continue
        for element_index, element in enumerate(elements):
            _validate_element(
                element,
                f"{prefix}.elements[{element_index}]",
                issues,
                scene_duration=duration,
                assets_by_id=assets_by_id,
            )
    return issues


def _validate_brief(brief: Any, issues: list[str]) -> None:
    if brief is None:
        return
    if not isinstance(brief, dict):
        issues.append("brief must be an object")
        return
    purpose = str(brief.get("purpose") or "explain")
    if purpose not in {"explain", "introduce", "promote", "story"}:
        issues.append("brief.purpose is unsupported")
    for key, limit in {"audience": 240, "language": 40, "tone": 160, "call_to_action": 300}.items():
        if key in brief and (not isinstance(brief[key], str) or len(brief[key]) > limit):
            issues.append(f"brief.{key} must be text no longer than {limit} characters")
    if "target_duration_seconds" in brief:
        _bounded_asset_number(
            brief, "target_duration_seconds", 5.0, 600.0, "brief", issues,
        )


def _bounded_asset_number(
    data: dict[str, Any],
    key: str,
    minimum: float,
    maximum: float,
    prefix: str,
    issues: list[str],
    *,
    integer: bool = False,
) -> None:
    value = _finite_number(data.get(key), f"{prefix}.{key}", issues)
    if value is None:
        return
    if not minimum <= value <= maximum or (integer and value != int(value)):
        kind = "integer " if integer else ""
        issues.append(f"{prefix}.{key} must be an {kind}value between {minimum:g} and {maximum:g}")


def validate_project_assets(root: Path, data: dict[str, Any]) -> list[str]:
    """Verify project-local asset containment and content digests before use."""
    issues: list[str] = []
    project_root = root.resolve(strict=False)
    for index, asset in enumerate(data.get("assets", [])):
        if not isinstance(asset, dict):
            continue
        try:
            candidate = (project_root / str(asset["path"])).resolve(strict=False)
            candidate.relative_to(project_root)
        except (KeyError, OSError, ValueError):
            issues.append(f"assets[{index}].path escapes the project")
            continue
        if not candidate.is_file():
            issues.append(f"assets[{index}].path is missing")
            continue
        try:
            digest = file_sha256(candidate)
        except OSError:
            issues.append(f"assets[{index}].path is unreadable")
            continue
        if digest != asset.get("sha256"):
            issues.append(f"assets[{index}].sha256 does not match the current file")
    return issues


def project_asset_path(project: ExplainerProject, asset: dict[str, Any]) -> Path:
    candidate = (project.directory / str(asset["path"])).resolve(strict=False)
    try:
        candidate.relative_to(project.directory.resolve(strict=False))
    except ValueError as exc:
        raise ProjectValidationError("project asset escapes its project") from exc
    return candidate


def _validate_element(
    element: Any,
    prefix: str,
    issues: list[str],
    *,
    scene_duration: float,
    assets_by_id: dict[str, dict[str, Any]],
) -> None:
    if not isinstance(element, dict):
        issues.append(f"{prefix} must be an object")
        return
    kind = str(element.get("type") or "")
    if kind not in SUPPORTED_ELEMENTS:
        issues.append(f"{prefix}.type is unsupported")
        return

    for key in ("x", "y"):
        _finite_number(element.get(key, 0), f"{prefix}.{key}", issues)

    start = _finite_number(element.get("start", 0.0), f"{prefix}.start", issues)
    end = _finite_number(element.get("end", scene_duration), f"{prefix}.end", issues)
    if start is not None and not 0.0 <= start <= scene_duration:
        issues.append(f"{prefix}.start must be within the scene duration")
    if end is not None and not 0.0 <= end <= scene_duration:
        issues.append(f"{prefix}.end must be within the scene duration")
    if start is not None and end is not None and end < start:
        issues.append(f"{prefix}.end must not precede start")

    if "move" in element:
        move = element["move"]
        if kind not in {"image", "video"}:
            issues.append(f"{prefix}.move is supported only for image and video")
        elif not isinstance(move, dict):
            issues.append(f"{prefix}.move must be an object")
        else:
            if not {"x", "y"}.intersection(move):
                issues.append(f"{prefix}.move requires a destination x or y")
            for key in ("x", "y"):
                if key in move:
                    _finite_number(move[key], f"{prefix}.move.{key}", issues)
            move_start = _finite_number(move.get("start", start), f"{prefix}.move.start", issues)
            move_end = _finite_number(move.get("end", end), f"{prefix}.move.end", issues)
            if all(value is not None for value in (start, end, move_start, move_end)):
                if not start <= move_start < move_end <= end:
                    issues.append(f"{prefix}.move must have start < end within the element's visibility window")

    if "stroke" in element:
        stroke = _finite_number(element["stroke"], f"{prefix}.stroke", issues)
        if stroke is not None and not 1.0 <= stroke <= 64.0:
            issues.append(f"{prefix}.stroke must be between 1 and 64")
    if "fill" in element and not isinstance(element["fill"], bool):
        issues.append(f"{prefix}.fill must be a boolean")
    if "fill_opacity" in element:
        if kind not in {"box", "circle"}:
            issues.append(f"{prefix}.fill_opacity is supported only for box and circle")
        else:
            opacity = _finite_number(element["fill_opacity"], f"{prefix}.fill_opacity", issues)
            if opacity is not None and not 0.0 <= opacity <= 1.0:
                issues.append(f"{prefix}.fill_opacity must be between 0 and 1")

    for key in ("color", "fill_color"):
        if key not in element:
            continue
        color = str(element[key])
        if color not in {"background", "foreground", "accent", "secondary", "muted"} and not _HEX_COLOR.fullmatch(color):
            issues.append(f"{prefix}.{key} must be a theme color name or six-digit hex color")
    animation = str(element.get("animation") or "fade")
    if animation not in SUPPORTED_ANIMATIONS:
        issues.append(f"{prefix}.animation is unsupported")

    if kind == "text":
        if not str(element.get("text") or "").strip():
            issues.append(f"{prefix}.text is required")
        width = _finite_number(element.get("width", 800), f"{prefix}.width", issues)
        if width is not None and width <= 0:
            issues.append(f"{prefix}.width must be positive")
        size = _finite_number(element.get("size", 44), f"{prefix}.size", issues)
        if size is not None and not 8.0 <= size <= 300.0:
            issues.append(f"{prefix}.size must be between 8 and 300")
        if str(element.get("align") or "left") not in {"left", "center", "right"}:
            issues.append(f"{prefix}.align must be left, center, or right")
        if str(element.get("weight") or "bold") not in {"regular", "bold"}:
            issues.append(f"{prefix}.weight must be regular or bold")
    elif kind in {"box", "bar", "image", "video", "callout"}:
        dimensions: dict[str, float] = {}
        for key in ("width", "height"):
            value = _finite_number(element.get(key), f"{prefix}.{key}", issues)
            if value is not None:
                dimensions[key] = value
                if value <= 0:
                    issues.append(f"{prefix}.{key} must be positive")
        if kind == "box" and "radius" in element:
            radius = _finite_number(element["radius"], f"{prefix}.radius", issues)
            maximum = min(dimensions.values()) / 2 if len(dimensions) == 2 else None
            if radius is not None and (radius < 0 or (maximum is not None and radius > maximum)):
                issues.append(f"{prefix}.radius must fit within the box")
        if kind == "bar":
            value = _finite_number(element.get("value", 0.5), f"{prefix}.value", issues)
            if value is not None and not 0.0 <= value <= 1.0:
                issues.append(f"{prefix}.value must be between 0 and 1")
        if kind in {"image", "video"}:
            asset_id = str(element.get("asset_id") or "")
            asset = assets_by_id.get(asset_id)
            if asset is None:
                issues.append(f"{prefix}.asset_id references an unknown asset")
            elif asset.get("type") != kind:
                issues.append(f"{prefix}.asset_id must reference a {kind} asset")
            if str(element.get("fit") or "cover") not in {"contain", "cover"}:
                issues.append(f"{prefix}.fit must be contain or cover")
            for key, default, minimum, maximum in (
                ("opacity", 1.0, 0.0, 1.0),
                ("zoom", 1.0, 1.0, 4.0),
                ("zoom_to", element.get("zoom", 1.0), 1.0, 4.0),
                ("pan_x", 0.0, -1.0, 1.0),
                ("pan_y", 0.0, -1.0, 1.0),
                ("pan_to_x", element.get("pan_x", 0.0), -1.0, 1.0),
                ("pan_to_y", element.get("pan_y", 0.0), -1.0, 1.0),
            ):
                value = _finite_number(element.get(key, default), f"{prefix}.{key}", issues)
                if value is not None and not minimum <= value <= maximum:
                    issues.append(f"{prefix}.{key} must be between {minimum:g} and {maximum:g}")
            if kind == "video":
                # Asset validation already reports malformed metadata; use its
                # finite duration here only to constrain the referenced window.
                asset_duration = _finite_number((asset or {}).get("duration_seconds"), "", [])
                trim_start = _finite_number(element.get("trim_start", 0.0), f"{prefix}.trim_start", issues)
                trim_end = _finite_number(
                    element.get("trim_end", asset_duration if asset_duration is not None else 30.0),
                    f"{prefix}.trim_end", issues,
                )
                if trim_start is not None and not 0.0 <= trim_start < 30.0:
                    issues.append(f"{prefix}.trim_start must be between 0 and 30 seconds")
                if trim_end is not None and not 0.1 <= trim_end <= 30.0:
                    issues.append(f"{prefix}.trim_end must be between 0.1 and 30 seconds")
                if trim_start is not None and trim_end is not None and trim_end <= trim_start:
                    issues.append(f"{prefix}.trim_end must follow trim_start")
                elif trim_start is not None and trim_end is not None and asset_duration is not None:
                    if trim_start >= min(trim_end, asset_duration):
                        issues.append(f"{prefix}.trim_start must precede the referenced video's end")
                if "loop" in element and not isinstance(element["loop"], bool):
                    issues.append(f"{prefix}.loop must be a boolean")
                if element.get("muted", True) is not True:
                    issues.append(f"{prefix}.muted must be true; narration owns the current audio track")
        if kind == "callout":
            if not str(element.get("text") or "").strip():
                issues.append(f"{prefix}.text is required")
            for key in ("target_x", "target_y"):
                _finite_number(element.get(key), f"{prefix}.{key}", issues)
            size = _finite_number(element.get("size", 28), f"{prefix}.size", issues)
            if size is not None and not 12.0 <= size <= 72.0:
                issues.append(f"{prefix}.size must be between 12 and 72")
    elif kind == "circle":
        radius = _finite_number(element.get("radius"), f"{prefix}.radius", issues)
        if radius is not None and radius <= 0:
            issues.append(f"{prefix}.radius must be positive")
    elif kind in {"line", "arrow"}:
        for key in ("x2", "y2"):
            _finite_number(element.get(key), f"{prefix}.{key}", issues)


def _finite_number(value: Any, label: str, issues: list[str]) -> float | None:
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError
        number = float(value)
        if not math.isfinite(number):
            raise ValueError
        return number
    except (TypeError, ValueError, OverflowError):
        issues.append(f"{label} must be numeric")
        return None


def _bounded_int(
    data: dict[str, Any], key: str, minimum: int, maximum: int,
    issues: list[str], *, default: int, even: bool = False,
) -> None:
    try:
        raw = data.get(key, default)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError
        value = int(raw)
        if value != raw or not minimum <= value <= maximum or (even and value % 2):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        constraint = "an even integer" if even else "an integer"
        issues.append(f"{key} must be {constraint} between {minimum} and {maximum}")
