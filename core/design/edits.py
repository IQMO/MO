"""Bounded manual Preview edits carried by the existing Design artifact."""
from __future__ import annotations

import re
from typing import Any


MAX_PREVIEW_EDITS = 512
PREVIEW_STYLE_PROPERTIES = frozenset({
    "translate", "width", "height", "min-width", "max-width", "min-height", "max-height",
    "padding", "margin", "gap", "border-radius", "color", "background-color",
    "font-size", "font-weight", "text-align", "opacity", "display", "order",
})
_VALUE = re.compile(r"[a-zA-Z0-9#.,%() +*/_\-]+")


def parse_preview_edits(value: Any) -> tuple[dict[str, Any], ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)) or len(value) > MAX_PREVIEW_EDITS:
        raise ValueError(f"design.edits must contain at most {MAX_PREVIEW_EDITS} elements")
    result = []
    seen = set()
    for raw in value:
        if not isinstance(raw, dict) or set(raw) - {"selector", "tag", "identity", "style", "text"}:
            raise ValueError("Invalid manual Preview edit")
        selector = _text(raw.get("selector"), 1000, "selector")
        tag = _text(raw.get("tag"), 32, "tag")
        identity = _text(raw.get("identity"), 512, "identity")
        if not selector or not re.fullmatch(r"[a-z][a-z0-9-]*", tag) or not identity:
            raise ValueError("A manual Preview edit requires an exact element")
        if selector in seen:
            raise ValueError("Duplicate manual Preview element")
        seen.add(selector)
        style = raw.get("style", {})
        if not isinstance(style, dict) or set(style) - PREVIEW_STYLE_PROPERTIES:
            raise ValueError("Unsupported manual Preview style")
        cleaned = {}
        for key, value in style.items():
            value = _text(value, 160, "style")
            if value and (not _VALUE.fullmatch(value) or re.search(r"url|expression|attr\s*\(", value, re.I)):
                raise ValueError("Invalid manual Preview style value")
            cleaned[key] = value
        row = {"selector": selector, "tag": tag, "identity": identity, "style": cleaned}
        if "text" in raw:
            row["text"] = _text(raw["text"], 8000, "text")
        result.append(row)
    return tuple(result)


def _text(value: Any, limit: int, label: str) -> str:
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError(f"Invalid manual Preview {label}")
    return value
