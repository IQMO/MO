"""Bounded Kie operation contracts shared by tools, Settings and Create.

These are verified API contracts, not a live pricing catalog or a promise that
an account can use a model. See README.md for sources and verification date.
"""
from __future__ import annotations

import math
from typing import Any

VIDEO_MODELS = ("bytedance/seedance-2", "bytedance/seedance-2-5")
MUSIC_MODELS = ("V6", "V6_MINI", "V6_WILD")
OPERATIONS = {
    "music": ("New song", MUSIC_MODELS, "ai-music-api/generate"),
    "cover": ("Cover song", MUSIC_MODELS, "ai-music-api/upload-and-cover-audio"),
    "extend_music": ("Extend track", MUSIC_MODELS, "ai-music-api/extend"),
    "image": ("Image", ("seedream/4.5-text-to-image",), ""),
    "edit_image": ("Reference image", ("seedream/4.5-edit",), ""),
    "video": ("Video / motion", VIDEO_MODELS, ""),
    "extend_video": ("Continue video", VIDEO_MODELS, ""),
}
PRIVACY_NOTICE = (
    "Selected references are prepared locally and temporarily shared through Cloudflare "
    "with Kie and its generation provider. Anyone holding a live reference link can fetch "
    "that copy. MO revokes local access automatically; it cannot erase provider copies. "
    "Originals and saved results are kept. Kie documents 14 days for generated media and "
    "2 months for text/metadata logs; fetched-input retention is not verified."
)


def settings(config: dict | None) -> dict:
    value = (config or {}).get("media", {})
    return value if isinstance(value, dict) else {}


def apply_selection(arguments: dict, selection: dict | None) -> dict:
    """Bind native request scope before guards/history as well as execution."""
    if arguments.get("action") != "create" or not selection:
        return arguments
    effective = dict(arguments)
    for key in ("operation", "model", "references", "parent_id", "output_index"):
        if key in selection:
            effective[key] = selection[key]
    options = effective.get("options") or {}
    # Preserve invalid input for the normal validator; preparation must not
    # throw before the tool can report an ordinary argument error.
    if isinstance(options, dict):
        effective["options"] = {**options, **selection.get("options", {})}
    return effective


def default_model(operation: str, config: dict | None = None) -> str:
    if operation not in OPERATIONS:
        raise ValueError("Choose a supported media operation.")
    key = "music_model" if operation in {"music", "cover", "extend_music"} else "video_model"
    models = OPERATIONS[operation][1]
    if operation in {"image", "edit_image"}:
        return models[0]
    model = str(settings(config).get(key) or models[0])
    if model not in models:
        raise ValueError("The saved media model is unavailable; choose a supported model.")
    return model


def _number(value: Any, low: float, high: float, label: str, *, integer=False) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number.")
    if not math.isfinite(value) or not low <= value <= high or (integer and int(value) != value):
        raise ValueError(f"{label} must be between {low:g} and {high:g}.")
    return int(value) if integer else value


def build_payload(operation: str, model: str, options: dict, references: list[dict]) -> dict:
    """Validate before publishing media; URLs come only from the reference owner.

    Call once with inert URLs before preparation and again with leased URLs.
    Never pass arbitrary model-supplied options through to the provider.
    """
    if operation not in OPERATIONS or model not in OPERATIONS[operation][1]:
        raise ValueError("Unsupported operation/model combination; no model was substituted.")
    if not isinstance(options, dict) or not isinstance(references, list):
        raise ValueError("Media options and references must be structured values.")
    opts = dict(options)
    for ref in references:
        if ref.get("kind") not in {"image", "video", "audio"}:
            raise ValueError("Each reference needs an image, video or audio kind.")
        if ref.get("role", "reference") not in {"reference", "subject", "motion", "song", "first_frame", "last_frame"}:
            raise ValueError("Unknown reference purpose.")
        if ref.get("role") in {"first_frame", "last_frame"} and ref["kind"] != "image":
            raise ValueError("First and last frames must be images.")
        role = ref.get("role", "reference")
        if role == "subject" and ref["kind"] != "image" or role == "motion" and ref["kind"] != "video" or role == "song" and ref["kind"] != "audio":
            raise ValueError("Reference purpose does not match its media type.")
        if role in {"first_frame", "last_frame", "motion"} and operation not in {"video", "extend_video"}:
            raise ValueError("Frame and motion controls belong to video generation.")
    by_kind = {kind: [r["url"] for r in references if r["kind"] == kind]
               for kind in ("image", "video", "audio")}
    music = operation in {"music", "cover", "extend_music"}
    video = operation in {"video", "extend_video"}
    allowed = {"prompt"}
    if music:
        allowed |= {"style", "title", "lyrics", "instrumental", "negative_tags", "vocal_gender",
                    "style_weight", "weirdness_constraint", "audio_weight", "variety"}
        allowed |= {"custom_mode", "duration"} if operation == "music" else (
            {"continue_at", "audio_id"} if operation == "extend_music" else {"duration"})
    elif video:
        allowed |= {"duration", "resolution", "aspect_ratio", "generate_audio"}
    else:
        allowed |= {"aspect_ratio", "quality"}
    if set(opts) - allowed:
        raise ValueError("Unsupported media options: " + ", ".join(sorted(set(opts) - allowed)))
    for key, limit in {"prompt": 30000 if model.endswith("2-5") else 20000 if video else 3000 if not music else 5000,
                       "lyrics": 5000, "style": 1000, "title": 100 if operation == "extend_music" else 80,
                       "negative_tags": 1000, "audio_id": 200}.items():
        if key in opts and (not isinstance(opts[key], str) or len(opts[key]) > limit):
            raise ValueError(f"{key} must be text of at most {limit} characters.")
    for key in ("custom_mode", "instrumental", "generate_audio"):
        if key in opts and not isinstance(opts[key], bool):
            raise ValueError(f"{key} must be true or false.")
    if music:
        opts["model"] = model
        opts.setdefault("instrumental", False)
        for key in ("style_weight", "weirdness_constraint", "audio_weight"):
            if key in opts:
                opts[key] = round(_number(opts[key], 0, 1, key), 2)
        if "variety" in opts:
            _number(opts["variety"], 0, 4, "variety", integer=True)
        if "duration" in opts:
            _number(opts["duration"], 10, 360, "duration", integer=True)
        if opts.get("vocal_gender") not in (None, "m", "f"):
            raise ValueError("vocal_gender must be m or f.")
        if operation == "music":
            opts.setdefault("custom_mode", False)
            if opts["custom_mode"]:
                if references:
                    raise ValueError("Song references require non-custom mode, not literal-lyrics custom mode.")
                if not opts.get("title") or not any(opts.get(k) for k in ("style", "lyrics", "negative_tags")):
                    raise ValueError("Custom music needs a title and style, lyrics or negative tags.")
            else:
                if len(opts.get("prompt", "")) > 3000:
                    raise ValueError("A song idea must be at most 3000 characters.")
                if any(k in opts for k in ("duration", "vocal_gender", "style_weight", "weirdness_constraint", "audio_weight", "variety")):
                    raise ValueError("These advanced song controls require custom mode.")
                if not references and not (opts.get("style") or opts.get("lyrics")):
                    raise ValueError("A song idea also needs a style, lyrics or a reference.")
                if len(by_kind["image"]) > 5 or len(by_kind["video"]) > 1 or len(references) + bool(opts.get("style")) + bool(opts.get("lyrics")) > 10:
                    raise ValueError("Song references exceed the selected operation's attachment limit.")
                opts.update({kind + "_urls": urls for kind, urls in by_kind.items() if urls})
        elif operation == "cover":
            if len(references) != 1 or len(by_kind["audio"]) != 1:
                raise ValueError("A cover needs exactly one source song.")
            opts["upload_url"] = by_kind["audio"][0]
        else:
            if references or not opts.get("audio_id"):
                raise ValueError("Music continuation needs an exact returned track ID, not an upload.")
            if "continue_at" in opts:
                _number(opts["continue_at"], 0, 3600, "continue_at")
                if opts["continue_at"] == 0:
                    raise ValueError("continue_at must be greater than zero and before the track ends.")
        if operation != "music" and opts["instrumental"] and any(opts.get(k) for k in ("lyrics", "prompt", "vocal_gender")):
            raise ValueError("Instrumental cover/extension cannot include lyrics, prompt or vocal gender.")
    elif video:
        max_duration = 30 if model.endswith("2-5") else 15
        opts.setdefault("duration", 5)
        if opts["duration"] != -1:
            _number(opts["duration"], 4, max_duration, "duration", integer=True)
        opts.setdefault("resolution", "720p")
        resolutions = ("480p", "720p", "1080p") + (() if model.endswith("2-5") else ("4k",))
        if opts["resolution"] not in resolutions:
            raise ValueError("Resolution is not supported by the selected Seedance model.")
        opts.setdefault("aspect_ratio", "16:9")
        opts.setdefault("generate_audio", True)
        frames = [r for r in references if r.get("role") in {"first_frame", "last_frame"}]
        if frames:
            roles = [r["role"] for r in frames]
            if len(frames) != len(references) or len(set(roles)) != len(roles) or "first_frame" not in roles:
                raise ValueError("Strict first/last frames cannot be combined with other references; choose a supported mode.")
            opts.update({r["role"] + "_url": r["url"] for r in frames})
        else:
            limits = (30, 10, 10) if model.endswith("2-5") else (9, 3, 3)
            for (kind, urls), limit in zip(by_kind.items(), limits):
                if len(urls) > limit:
                    raise ValueError(f"Too many {kind} references for the selected model.")
                if urls:
                    opts["reference_" + kind + "_urls"] = urls
        if not opts.get("prompt"):
            raise ValueError("Describe the intended video.")
    else:
        if not opts.get("prompt"):
            raise ValueError("Describe the intended image.")
        if operation == "image" and references:
            raise ValueError("Choose reference image editing to use image references.")
        if operation == "edit_image":
            if not 1 <= len(references) <= 14 or len(by_kind["image"]) != len(references):
                raise ValueError("Reference editing needs 1–14 images and no audio/video.")
            opts["image_urls"] = by_kind["image"]
        opts.setdefault("aspect_ratio", "1:1")
        opts.setdefault("quality", "basic")
        if opts["quality"] not in {"basic", "high"}:
            raise ValueError("Image quality must be basic or high.")
    if not music:
        ratios = {"1:1", "4:3", "3:4", "16:9", "9:16", "21:9"} | ({"adaptive"} if video else {"2:3", "3:2"})
        if opts["aspect_ratio"] not in ratios:
            raise ValueError("Unsupported aspect ratio.")
    return {"model": OPERATIONS[operation][2] or model, "input": opts}
