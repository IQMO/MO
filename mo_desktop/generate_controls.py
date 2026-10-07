"""Presentation-only Generate controls for the existing composer.

The core catalog validates provider behavior. This module owns the pills the composer draws
(two fixed rows: kind first, then only that kind's choices, in order), the drop-down choices
behind each pill and how a pick changes the request-local selection. Never generation,
credentials or persistence.
"""
from __future__ import annotations

from core.media.catalog import OPERATIONS, default_model, settings

KINDS = (("", "Auto"), ("song", "Song"), ("image", "Image"), ("video", "Video"))
TYPES = {"song": ("music", "cover", "extend_music"), "image": ("image", "edit_image"),
         "video": ("video", "extend_video")}
# Each kind's options, in the order its pills appear.
OPTION_ORDER = {"song": ("instrumental",), "image": ("quality", "aspect_ratio"),
                "video": ("duration", "resolution", "aspect_ratio")}
DEFAULTS = {"song": {"instrumental": False}, "image": {"quality": "basic", "aspect_ratio": "1:1"},
            "video": {"duration": 5, "resolution": "720p", "aspect_ratio": "16:9"}}
MORE = (("credits", ""), ("setup", "Kie setup"), ("privacy", "Privacy"), ("jobs", "Saved results"))


def kind_of(operation: str | None) -> str:
    return next((kind for kind, ops in TYPES.items() if operation in ops), "")


def initial_selection(config: dict) -> dict:
    """Auto until a kind is picked: MO chooses the operation from the request's words."""
    return {"operation": None, "model": None, "options": {}}


def _model_for(operation: str, config: dict) -> str:
    # Invalid saved defaults stay visible and are rejected by the core; they must not prevent
    # opening the ordinary composer or Settings to fix them.
    try:
        return default_model(operation, config)
    except ValueError:
        key = "video_model" if "video" in operation else "music_model"
        return str(settings(config).get(key) or "Unavailable")


def request_selection(selection: dict) -> dict:
    """Snapshot every displayed choice, including defaults, at Submit. Auto sends no
    operation or model, so the request's words decide them."""
    operation = selection.get("operation")
    if not operation:
        return {"options": {}}
    value = {**selection, "options": dict(selection.get("options", {}))}
    value["options"] = {**DEFAULTS[kind_of(operation)], **value["options"]}
    return value


_MODEL_NAMES = {"bytedance/seedance-2": "Seedance 2.0", "bytedance/seedance-2-5": "Seedance 2.5",
                "seedream/4.5-text-to-image": "Seedream 4.5", "seedream/4.5-edit": "Seedream 4.5 Edit",
                "V6": "Suno V6", "V6_MINI": "Suno V6 Mini", "V6_WILD": "Suno V6 Wild"}


def _short_model(model: str) -> str:
    return _MODEL_NAMES.get(str(model or ""), str(model or ""))


def _option_label(key: str, value) -> str:
    if key == "duration":
        return "Auto length" if value == -1 else f"{value}s"
    if key == "instrumental":
        return "Instrumental" if value else "Vocals"
    if key == "quality":
        return str(value).capitalize()
    return str(value)


def _option_values(key: str, model: str) -> tuple:
    two_five = str(model).endswith("2-5")
    return {
        "duration": (4, 5, 10, 15) + ((30,) if two_five else ()) + (-1,),
        "resolution": ("480p", "720p", "1080p") + (() if two_five else ("4k",)),
        "aspect_ratio": ("1:1", "16:9", "9:16", "4:3", "3:4", "21:9"),
        "quality": ("basic", "high"),
        "instrumental": (False, True),
    }[key]


def pill_rows(selection: dict, reference_count: int = 0) -> list[list[tuple[str, str]]]:
    """Two rows, always: [kind, model, references?, more] and the picked kind's type and
    options in order (empty under Auto, where the composer shows a one-line hint). Every picked
    kind shows its model (Suno, Seedream, Seedance), even when it has only one."""
    operation = selection.get("operation")
    kind = kind_of(operation)
    first = [("kind", dict(KINDS)[kind])]
    if kind:
        first.append(("model", _short_model(selection.get("model") or "")))
    if reference_count:
        first.append(("refs", f"{reference_count} reference{'s' if reference_count != 1 else ''}"))
    first.append(("more", "More"))
    second: list[tuple[str, str]] = []
    if kind:
        second.append(("type", OPERATIONS[operation][0]))
        options = {**DEFAULTS[kind], **dict(selection.get("options", {}))}
        second.extend((key, _option_label(key, options[key])) for key in OPTION_ORDER[kind])
    return [first, second]


_REFINE_HEADING = "## Refining a request"


def refine_guidance(skill_body: str, selection: dict, references: list[tuple[str, str]] = ()) -> str:
    """What Refine hands MO's prompt enhancer: the Generate skill's own refining rules and the
    generator this request is set to, so the draft keeps its goal and reads the way that
    generator follows it."""
    body = str(skill_body or "")
    start = body.find(_REFINE_HEADING)
    rules = ""
    if start >= 0:
        rest = body[start + len(_REFINE_HEADING):]
        end = rest.find("\n## ")
        rules = (rest if end < 0 else rest[:end]).strip()
    if not rules:
        rules = "Keep the user's goal, subject and scope; make the prompt precise for the chosen generator."
    operation = selection.get("operation")
    kind = kind_of(operation)
    if kind:
        options = {**DEFAULTS[kind], **dict(selection.get("options", {}))}
        target = (f"{dict(KINDS)[kind]} with {_short_model(selection.get('model') or '')} "
                  f"({OPERATIONS[operation][0]}): "
                  + ", ".join(_option_label(key, options[key]) for key in OPTION_ORDER[kind]))
    else:
        target = "Auto, no kind chosen yet: infer it from the draft without changing the draft's goal"
    if references:
        target += "; references: " + ", ".join(f"{role} ({name})" for role, name in references)
    return f"{rules}\n\nThis request: {target}."


def menu(selection: dict, key: str, *, references: list[tuple[str, str]] = (),
         credit: str = "Credits · refresh") -> tuple[list[tuple[str, str]], str]:
    """The drop-down behind one pill: ``(choices as (value, label), selected value)``."""
    operation = selection.get("operation")
    kind = kind_of(operation)
    if key == "kind":
        return list(KINDS), kind
    if key == "type":
        return [(op, OPERATIONS[op][0]) for op in TYPES[kind]], str(operation)
    if key == "model":
        return [(m, _short_model(m)) for m in OPERATIONS[operation][1]], str(selection.get("model") or "")
    if key == "more":
        return [(value, credit if value == "credits" else label) for value, label in MORE], ""
    if key == "refs":
        return [(f"reference:{index}", f"{role} · {name}") for index, (role, name) in enumerate(references)], ""
    current = {**DEFAULTS[kind], **dict(selection.get("options", {}))}.get(key)
    values = _option_values(key, str(selection.get("model") or ""))
    return [(str(value), _option_label(key, value)) for value in values], str(current)


def choose(selection: dict, key: str, value: str, config: dict) -> dict:
    """Apply one drop-down pick. A new kind starts at its first type with its defaults; a type
    in the same kind keeps the options; an incompatible choice stays visible for the core's
    validator (never silently lowering 4K or shortening a requested 30-second video)."""
    current = {**selection, "options": dict(selection.get("options", {}))}
    if key == "kind":
        if not value:
            return {"operation": None, "model": None, "options": {}}
        operation = TYPES[value][0]
        return {"operation": operation, "model": _model_for(operation, config), "options": {}}
    if key == "type":
        if kind_of(value) != kind_of(current.get("operation")):
            return choose(current, "kind", kind_of(value), config) | {"operation": value}
        current["operation"] = value
        if current.get("model") not in OPERATIONS[value][1]:
            current["model"] = _model_for(value, config)
        current.pop("parent_id", None)
        current.pop("output_index", None)
        return current
    if key == "model":
        current["model"] = value
        return current
    kind = kind_of(current.get("operation"))
    if key not in OPTION_ORDER.get(kind, ()):
        return current
    current["options"][key] = (int(value) if key == "duration" else value == "True" if key == "instrumental"
                               else value)
    return current
