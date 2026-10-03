"""MO Desktop — parse an options block from MO's reply into selectable choices.

When MO offers the operator a set of actionable choices, it emits a structured
options block so the companion panel renders pick-and-submit options instead of
prose the operator must retype. Free-form prose with no block stays prose — the
feature never forces itself on a normal answer.

Marker (one line MO appends to its reply):

    __MO_OPTIONS__:{"mode":"multi","options":[{"label":"Scan","detail":"read-only"}]}

``mode`` is ``single`` (radio) or ``multi`` (checkboxes). Parsing is defensive:
a malformed or empty block is ignored and the whole reply stays prose. Stdlib
only, so importing this never weighs down the light desktop launch path.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

OPTIONS_MARKER = "__MO_OPTIONS__"
MAX_OPTIONS = 6
MAX_LABEL_CHARS = 80
MAX_DETAIL_CHARS = 140


@dataclass
class Option:
    label: str
    detail: str = ""


@dataclass
class OptionSet:
    mode: str  # "single" | "multi"
    options: list[Option] = field(default_factory=list)


def parse_options(reply: str) -> tuple[str, "OptionSet | None"]:
    """Split a reply into ``(prose, OptionSet | None)``.

    Returns the reply with the marker block removed as prose, plus the parsed
    ``OptionSet`` — or ``(reply, None)`` when there is no valid block, so a
    normal answer is untouched.
    """
    if not isinstance(reply, str) or OPTIONS_MARKER not in reply:
        return reply, None
    head, _, tail = reply.partition(OPTIONS_MARKER)
    try:
        data, _end = json.JSONDecoder().raw_decode(tail.lstrip(": \n\r\t"))
    except (ValueError, TypeError):
        return reply, None
    raw = data.get("options") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        return reply, None
    prose = head.strip()
    options: list[Option] = []
    for item in raw:
        if len(options) >= MAX_OPTIONS:
            break
        if isinstance(item, dict) and str(item.get("label", "")).strip():
            label = str(item["label"]).strip()[:MAX_LABEL_CHARS]
            options.append(Option(
                label,
                str(item.get("detail", "") or "").strip()[:MAX_DETAIL_CHARS],
            ))
        elif isinstance(item, str) and item.strip():
            label = item.strip()[:MAX_LABEL_CHARS]
            options.append(Option(label))
    if not options:
        return prose, None
    mode = "single" if str(data.get("mode", "multi")).strip().lower() == "single" else "multi"
    return prose, OptionSet(mode, options)


def format_submission(selected: list[str | Option], note: str = "") -> str:
    """Submit the selected meaning, including details hidden by narrow labels."""
    parts: list[str] = []
    picks = [
        (f"{item.label}: {item.detail}" if item.detail else item.label).strip()
        if isinstance(item, Option) else str(item or "").strip()
        for item in selected
    ]
    picks = [pick for pick in picks if pick]
    if len(picks) == 1:
        parts.append("Proceed with: " + picks[0] + ".")
    elif picks:
        parts.append("Do the following, in order: " + "; ".join(picks) + ".")
    if note and note.strip():
        parts.append(note.strip())
    return " ".join(parts).strip()
