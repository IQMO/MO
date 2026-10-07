"""Presentation-only Create controls for the existing composer.

The core catalog validates provider behavior. This module owns short labels and
request-local choices, never generation, credentials or persistence.
"""
from __future__ import annotations

from core.media.catalog import OPERATIONS, default_model, settings


def initial_selection(config: dict) -> dict:
    # Invalid saved defaults stay visible and are rejected by the core; they
    # must not prevent opening the ordinary composer or Settings to fix them.
    model = str(settings(config).get("music_model") or OPERATIONS["music"][1][0])
    return {"operation": "music", "model": model, "options": {}}


def request_selection(selection: dict) -> dict:
    """Snapshot every displayed choice, including defaults, at Submit."""
    value = {**selection, "options": dict(selection.get("options", {}))}
    op = value["operation"]
    defaults = ({"duration": 5, "resolution": "720p", "aspect_ratio": "16:9"} if "video" in op else
                {"quality": "basic", "aspect_ratio": "1:1"} if "image" in op else {"instrumental": False})
    value["options"] = {**defaults, **value["options"]}
    return value


def control_rows(selection: dict, credit: str = "Credits · refresh") -> list[list[tuple[str, str]]]:
    op = selection["operation"]
    model = selection["model"]
    opts = selection.get("options", {})
    short_model = model.replace("bytedance/", "").replace("seedream/", "")
    rows = [[("operation", OPERATIONS[op][0]), ("model", short_model)]]
    if op in {"video", "extend_video"}:
        duration = opts.get("duration", 5)
        rows.append([("duration", "Auto" if duration == -1 else f"{duration}s"), ("resolution", opts.get("resolution", "720p")),
                     ("aspect_ratio", opts.get("aspect_ratio", "16:9"))])
    elif op in {"image", "edit_image"}:
        rows.append([("quality", opts.get("quality", "basic")), ("aspect_ratio", opts.get("aspect_ratio", "1:1"))])
    else:
        rows.append([("instrumental", "Instrumental" if opts.get("instrumental") else "Vocals")])
    rows.append([("credits", credit), ("setup", "Kie · setup"), ("privacy", "Privacy")])
    rows.append([("jobs", "Jobs · saved results · continue")])
    return rows


def cycle(selection: dict, key: str, config: dict) -> dict:
    value = {**selection, "options": dict(selection.get("options", {}))}
    op = value["operation"]
    if key == "operation":
        order = tuple(OPERATIONS)
        value["operation"] = order[(order.index(op) + 1) % len(order)]
        try:
            value["model"] = default_model(value["operation"], config)
        except ValueError:
            key = "video_model" if "video" in value["operation"] else "music_model"
            value["model"] = str(settings(config).get(key) or "Unavailable")
        value["options"] = {}
        value.pop("parent_id", None)
        value.pop("output_index", None)
        return value
    if key == "model":
        models = OPERATIONS[op][1]
        value["model"] = models[(models.index(value["model"]) + 1) % len(models)] if value["model"] in models else models[0]
        # Leave an incompatible choice visible as a validation issue; never
        # silently lower 4K or shorten a requested 30-second video.
        return value
    choices = {
        "duration": (5, 10, 15, 30, -1, 4) if value["model"].endswith("2-5") else (5, 10, 15, -1, 4),
        "resolution": ("720p", "1080p", "480p") + (() if value["model"].endswith("2-5") else ("4k",)),
        "aspect_ratio": ("1:1", "16:9", "9:16", "4:3", "3:4", "21:9"),
        "quality": ("basic", "high"), "instrumental": (False, True),
    }
    order = choices[key]
    default = "16:9" if key == "aspect_ratio" and "video" in op else order[0]
    current = value["options"].get(key, default)
    value["options"][key] = order[(order.index(current) + 1) % len(order)] if current in order else order[0]
    return value
