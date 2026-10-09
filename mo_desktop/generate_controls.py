"""Presentation-only Generate controls for the existing composer.

The core catalog validates provider behavior. This module owns the pills the composer draws
(two fixed rows: kind first, then only that kind's choices, in order), the drop-down choices
behind each pill and how a pick changes the request-local selection. Never generation,
credentials or persistence.
"""
from __future__ import annotations

import re
from pathlib import Path

from core.media.catalog import OPERATIONS, default_model, settings

# In-sentence references (his request 2026-10-08): a dropped file sits in the prompt as [Video1], [Image1] or
# [Audio1], numbered per kind in attachment order. That is also the order the core sends each kind to the
# provider, which reads these names (Seedance 2.0; checked with a real job 2026-10-08).
REFERENCE_TOKEN = re.compile(r"\[(Image|Video|Audio|File)([1-9][0-9]?)\]")
_KIND_WORD = {"image": "Image", "video": "Video", "audio": "Audio", "file": "File"}


def reference_tokens(paths: list[str]) -> dict[str, str]:
    """``{name: path}`` for the attached files, numbered per kind in attachment order. Any other file (a PDF
    in the normal composer) is a File; Generate only ever holds pictures, clips and sound."""
    from core.media.preparation import kind_for

    counts: dict[str, int] = {}
    names: dict[str, str] = {}
    for path in paths:
        try:
            kind = kind_for(Path(str(path)))
        except ValueError:
            kind = "file"
        counts[kind] = counts.get(kind, 0) + 1
        names[f"[{_KIND_WORD[kind]}{counts[kind]}]"] = str(path)
    return names


def default_role(path: str, operation: str | None) -> str:
    """A dropped clip drives a video's motion and a picture is its subject; otherwise a plain reference."""
    from core.media.preparation import kind_for

    if kind_of(operation) != "video":
        return "reference"
    try:
        return {"video": "motion", "image": "subject"}.get(kind_for(Path(str(path))), "reference")
    except ValueError:
        return "reference"


def default_roles(paths: list[str], operation: str | None, roles: dict[str, str] | None = None) -> dict[str, str]:
    """Every file's role, keeping those already set: a new file gets its default for the chosen kind and
    only one clip drives the motion (the next one is a reference)."""
    roles = dict(roles or {})
    for path in paths:
        if path not in roles:
            role = default_role(path, operation)
            roles[path] = "reference" if role == "motion" and "motion" in roles.values() else role
    return roles


def role_cycle(path: str, operation: str | None) -> tuple[str, ...]:
    """The purposes one file steps through in the References menu, then remove. A sound in a video is the
    voice its subject lip-syncs or the music its motion follows; elsewhere it is a song."""
    from core.media.preparation import kind_for

    try:
        kind = kind_for(Path(str(path)))
    except ValueError:
        return ("reference", "remove")                  # a file Generate cannot use can only be removed
    if kind == "audio":
        return ("reference", "voice", "music", "remove") if kind_of(operation) == "video" else ("reference", "song", "remove")
    return {"image": ("reference", "subject", "first_frame", "last_frame", "remove"),
            "video": ("reference", "motion", "remove")}[kind]


def renumber_references(text: str, before: dict[str, str], after: dict[str, str]) -> str:
    """Keep each name on its file when the attachments change; a removed file's name leaves the sentence."""
    new_name = {path: name for name, path in after.items()}

    def swap(match: re.Match) -> str:
        path = before.get(match.group(0))
        return new_name.get(path, "") if path else match.group(0)

    changed = REFERENCE_TOKEN.sub(swap, str(text or ""))
    return re.sub(r" {2,}", " ", changed) if changed != text else changed

KINDS = (("", "Auto"), ("song", "Song"), ("image", "Image"), ("video", "Video"))
TYPES = {"song": ("music", "cover", "extend_music"), "image": ("image", "edit_image"),
         "video": ("video", "extend_video")}
# Each kind's options, in the order its pills appear.
OPTION_ORDER = {"song": ("instrumental",), "image": ("quality", "aspect_ratio"),
                "video": ("duration", "resolution", "aspect_ratio", "generate_audio")}
DEFAULTS = {"song": {"instrumental": False}, "image": {"quality": "basic", "aspect_ratio": "1:1"},
            "video": {"duration": 5, "resolution": "720p", "aspect_ratio": "16:9", "generate_audio": True}}
PROVIDERS = (("kie", "Kie"),)            # core/media generates through Kie; its key lives with the model providers
# What the composer's privacy icon says, plainly (core.media.catalog.PRIVACY_NOTICE is the full contract).
PRIVACY_LINES = (
    "Files stay on this PC until you send.",
    "Sending gives Kie a copy by a short link.",
    "MO then closes it; Kie keeps its copy.",
    "Results stay 14 days at Kie.",
)


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
    if key == "generate_audio":                        # the video's own synchronized sound
        return "Sound on" if value else "Sound off"
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
        "generate_audio": (True, False),
    }[key]


def top_pills(selection: dict, credit: str = "") -> list[tuple[str, str]]:
    """The composer's top row beside the role: the kind (Auto first), the provider and the credits
    balance, shown rather than kept in a menu (his order 2026-10-08)."""
    return [("kind", dict(KINDS)[kind_of(selection.get("operation"))]), ("provider", dict(PROVIDERS)["kie"]),
            ("credits", credit or "Credits")]


def option_pills(selection: dict, reference_count: int = 0) -> list[tuple[str, str]]:
    """The picked kind's choices in order (its model, type, then options), and the References
    pill once files are attached; nothing under Auto, where the request's words decide. The
    composer flows these into as many rows as they need."""
    operation = selection.get("operation")
    kind = kind_of(operation)
    pills: list[tuple[str, str]] = []
    if kind:
        pills.append(("model", _short_model(selection.get("model") or "")))
        pills.append(("type", OPERATIONS[operation][0]))
        options = {**DEFAULTS[kind], **dict(selection.get("options", {}))}
        pills.extend((key, _option_label(key, options[key])) for key in OPTION_ORDER[kind])
    if reference_count:
        pills.append(("refs", f"{reference_count} reference{'s' if reference_count != 1 else ''}"))
    return pills


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
        target += ("; references: " + ", ".join(f"{role} ({name})" for role, name in references)
                   + "; keep names like [Video1] and [Image1] exactly where the draft puts them")
    return f"{rules}\n\nThis request: {target}."


def menu(selection: dict, key: str, *, references: list[tuple[str, str]] = ()) -> tuple[list[tuple[str, str]], str]:
    """The drop-down behind one pill: ``(choices as (value, label), selected value)``."""
    operation = selection.get("operation")
    kind = kind_of(operation)
    if key == "kind":
        return list(KINDS), kind
    if key == "type":
        return [(op, OPERATIONS[op][0]) for op in TYPES[kind]], str(operation)
    if key == "model":
        return [(m, _short_model(m)) for m in OPERATIONS[operation][1]], str(selection.get("model") or "")
    if key == "provider":
        return [*PROVIDERS, ("settings", "Provider settings")], "kie"
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
    default = DEFAULTS[kind][key]                      # a pick keeps its default's type (bool before int)
    current["options"][key] = (value == "True" if isinstance(default, bool) else int(value) if isinstance(default, int)
                               else value)
    return current
