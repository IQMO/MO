"""Validated shared-Board state and private working/draft persistence.

The portable Board is declarative ``board/v1`` data.  Fast user ink and MO
proposals live in one bounded private sidecar until Studio checkpoints or the
user accepts them; neither generated scripts nor the preview iframe can write
this state.
"""
from __future__ import annotations

import json
import math
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from core.utils.atomic_write import atomic_write_json


BOARD_SCHEMA_ID = "board/v1"
BOARD_WORKING_SCHEMA_ID = "board-working/v1"
BOARD_WORKING_SUFFIX = ".board-working.json"
BOARD_SIZE = 16_384.0
_GEOMETRY_EPSILON = 0.002
MAX_BOARD_ELEMENTS = 4_096
MAX_BOARD_POINTS = 50_000
MAX_POINTS_PER_STROKE = 4_096
MAX_BOARD_OPERATIONS = 64
MAX_BOARD_TEXT_CHARS = 64_000
MAX_WORKING_BYTES = 4_200_000

BOARD_KINDS = frozenset({"stroke", "line", "arrow", "rect", "ellipse", "text", "image"})
BOARD_ACTORS = frozenset({"user", "mo"})
BOARD_COLORS = frozenset({"text", "muted", "brand", "ok", "warn", "error"})
BOARD_FILLS = frozenset({"none", "surface", "brand", "ok", "warn", "error"})
BOARD_DASHES = frozenset({"solid", "dash", "dot"})
BOARD_ARROWHEADS = frozenset({"triangle", "open", "none"})

_BOARD_KEYS = frozenset({"version", "revision", "elements", "decisions", "questions"})
_ELEMENT_KEYS = frozenset({"id", "kind", "actor", "revision", "z", "bounds", "style", "points", "text", "attachment_id"})
_BOUNDS_KEYS = frozenset({"x", "y", "width", "height"})
_STYLE_KEYS = frozenset({"stroke", "fill", "width", "opacity", "dash", "arrowhead", "arrow_size"})
_OP_KEYS = frozenset({"op", "element", "id", "expected_revision"})
_ELEMENT_ID_RE = re.compile(r"[A-Za-z0-9_-]{3,80}")


class BoardValidationError(ValueError):
    """Safe validation/conflict error for Board boundaries."""


@dataclass(frozen=True)
class BoardState:
    revision: int = 0
    elements: tuple[dict[str, Any], ...] = ()
    decisions: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": BOARD_SCHEMA_ID,
            "revision": self.revision,
            "elements": [_clone_json(element) for element in self.elements],
            "decisions": list(self.decisions),
            "questions": list(self.questions),
        }


def empty_board() -> BoardState:
    return BoardState()


def parse_board(value: Any) -> BoardState:
    """Validate one complete portable Board and return a detached value."""
    if value in (None, ""):
        return empty_board()
    raw = _mapping(value, "board")
    _only_keys(raw, _BOARD_KEYS, "board")
    if raw.get("version") != BOARD_SCHEMA_ID:
        raise BoardValidationError(f"board.version must be {BOARD_SCHEMA_ID}")
    revision = _integer(raw.get("revision", 0), "board.revision", 0, 1_000_000)
    rows = raw.get("elements", [])
    if not isinstance(rows, list) or len(rows) > MAX_BOARD_ELEMENTS:
        raise BoardValidationError(f"board.elements must contain at most {MAX_BOARD_ELEMENTS} items")
    seen: set[str] = set()
    elements: list[dict[str, Any]] = []
    total_points = 0
    total_text = 0
    for index, row in enumerate(rows):
        element = _element(row, f"board.elements[{index}]")
        element_id = element["id"]
        if element_id in seen:
            raise BoardValidationError(f"board element id is duplicated: {element_id}")
        seen.add(element_id)
        total_points += len(element["points"])
        total_text += len(element["text"])
        if total_points > MAX_BOARD_POINTS:
            raise BoardValidationError(f"board contains more than {MAX_BOARD_POINTS} points")
        if total_text > MAX_BOARD_TEXT_CHARS:
            raise BoardValidationError("board text is too large")
        elements.append(element)
    decisions = _strings(raw.get("decisions"), "board.decisions", maximum=64, item_limit=1_000)
    questions = _strings(raw.get("questions"), "board.questions", maximum=64, item_limit=1_000)
    if total_text + sum(map(len, decisions)) + sum(map(len, questions)) > MAX_BOARD_TEXT_CHARS:
        raise BoardValidationError("board text is too large")
    return BoardState(
        revision=revision,
        elements=tuple(elements),
        decisions=decisions,
        questions=questions,
    )


def apply_board_operations(
    board: BoardState | dict[str, Any],
    operations: Iterable[dict[str, Any]],
    *,
    actor: str,
) -> BoardState:
    """Apply one bounded atomic operation batch with element-level conflicts."""
    current = board if isinstance(board, BoardState) else parse_board(board)
    clean_actor = str(actor or "").strip().lower()
    if clean_actor not in BOARD_ACTORS:
        raise BoardValidationError("board actor must be user or mo")
    rows = list(operations)
    if not rows or len(rows) > MAX_BOARD_OPERATIONS:
        raise BoardValidationError(f"board operations must contain 1-{MAX_BOARD_OPERATIONS} items")
    elements = {str(row["id"]): _clone_json(row) for row in current.elements}
    order = [str(row["id"]) for row in current.elements]
    for index, raw in enumerate(rows):
        op = _mapping(raw, f"operations[{index}]")
        _only_keys(op, _OP_KEYS, f"operations[{index}]")
        kind = str(op.get("op") or "").strip().lower()
        if kind == "add":
            supplied = _mapping(op.get("element"), f"operations[{index}].element")
            supplied["actor"] = clean_actor
            supplied["revision"] = 1
            element = _element(supplied, f"operations[{index}].element")
            element_id = element["id"]
            if element_id in elements:
                raise BoardValidationError(f"board element already exists: {element_id}")
            elements[element_id] = element
            order.append(element_id)
            continue
        if kind not in {"update", "delete"}:
            raise BoardValidationError("board operation must be add, update, or delete")
        element_id = _element_id(op.get("id"), f"operations[{index}].id")
        existing = elements.get(element_id)
        if existing is None:
            raise BoardValidationError(f"board element no longer exists: {element_id}")
        expected = _integer(
            op.get("expected_revision"), f"operations[{index}].expected_revision", 1, 1_000_000,
        )
        if expected != int(existing["revision"]):
            raise BoardValidationError(f"board element changed before this operation: {element_id}")
        if kind == "delete":
            del elements[element_id]
            order.remove(element_id)
            continue
        supplied = _mapping(op.get("element"), f"operations[{index}].element")
        supplied["id"] = element_id
        supplied["actor"] = clean_actor
        supplied["revision"] = expected + 1
        elements[element_id] = _element(supplied, f"operations[{index}].element")
    return parse_board({
        "version": BOARD_SCHEMA_ID,
        "revision": current.revision + 1,
        "elements": [elements[element_id] for element_id in order],
        "decisions": list(current.decisions),
        "questions": list(current.questions),
    })


def board_summary(board: BoardState | dict[str, Any]) -> str:
    """Return a deterministic semantic summary without interpreting geometry."""
    current = board if isinstance(board, BoardState) else parse_board(board)
    counts: dict[str, int] = {}
    actors: dict[str, int] = {}
    for element in current.elements:
        counts[element["kind"]] = counts.get(element["kind"], 0) + 1
        actors[element["actor"]] = actors.get(element["actor"], 0) + 1
    kinds = ", ".join(f"{count} {kind}" for kind, count in sorted(counts.items())) or "empty"
    ownership = ", ".join(f"{count} {actor}" for actor, count in sorted(actors.items()))
    suffix = f" ({ownership})" if ownership else ""
    return f"Board r{current.revision}: {kinds}{suffix}."


def board_spatial_context(board: BoardState | dict[str, Any]) -> dict[str, Any]:
    """Return bounded deterministic placement evidence for an MO draft."""
    current = board if isinstance(board, BoardState) else parse_board(board)
    material = [element for element in current.elements if _has_material_geometry(element)]
    if not material:
        return {
            "content_bounds": None,
            "occupied": [],
            "occupied_truncated": False,
            "clear_placements": [
                {"name": "empty_board", "x": 96.0, "y": 96.0, "width": 640.0, "height": 480.0},
            ],
        }
    left = min(float(element["bounds"]["x"]) for element in material)
    top = min(float(element["bounds"]["y"]) for element in material)
    right = max(float(element["bounds"]["x"] + element["bounds"]["width"]) for element in material)
    bottom = max(float(element["bounds"]["y"] + element["bounds"]["height"]) for element in material)
    content = {"x": left, "y": top, "width": right - left, "height": bottom - top}
    occupied = [
        {
            "id": str(element["id"]),
            "kind": str(element["kind"]),
            "actor": str(element["actor"]),
            "bounds": _clone_json(element["bounds"]),
        }
        for element in material[:256]
    ]
    return {
        "content_bounds": content,
        "occupied": occupied,
        "occupied_truncated": len(material) > len(occupied),
        "clear_placements": _clear_placements(material, content),
    }


def board_operation_contract() -> dict[str, Any]:
    """Return the bounded provider-facing operation contract from this owner."""
    return {
        "batch": f"1-{MAX_BOARD_OPERATIONS} atomic operations",
        "operation_keys": sorted(_OP_KEYS),
        "add": {
            "required": ["op", "element"],
            "note": "element.id is required; actor and revision are assigned by the Board",
        },
        "update": {
            "required": ["op", "id", "expected_revision", "element"],
            "note": "element is complete; its id, actor, and next revision are assigned by the Board",
        },
        "delete": {"required": ["op", "id", "expected_revision"]},
        "element": {
            "allowed_keys": sorted(_ELEMENT_KEYS - {"actor", "revision"}),
            "id": "3-80 ASCII letters, digits, underscore, or hyphen",
            "kind": sorted(BOARD_KINDS),
            "bounds": f"x, y, width, height in 0-{int(BOARD_SIZE)}; must contain every point",
            "points": (
                f"absolute [x,y] or [x,y,pressure] coordinates; at most {MAX_POINTS_PER_STROKE}; "
                "stroke requires points; line requires two; arrow accepts two endpoints "
                "or start, quadratic control, end"
            ),
            "style": {
                "stroke": sorted(BOARD_COLORS),
                "fill": sorted(BOARD_FILLS),
                "width": "0.5-32",
                "opacity": "0.05-1",
                "dash": sorted(BOARD_DASHES),
                "arrowhead": sorted(BOARD_ARROWHEADS),
                "arrow_size": "0.5-2.5",
            },
            "text": "required only for text; maximum 8000 characters",
        },
    }


def _clear_placements(
    occupied: list[dict[str, Any]],
    content: dict[str, float],
) -> list[dict[str, float | str]]:
    width, height, gap = 640.0, 480.0, 96.0
    candidates = [
        ("right_of_content", content["x"] + content["width"] + gap, content["y"]),
        ("below_content", content["x"], content["y"] + content["height"] + gap),
        ("left_of_content", content["x"] - width - gap, content["y"]),
        ("above_content", content["x"], content["y"] - height - gap),
    ]
    for y in range(96, int(BOARD_SIZE - height), 576):
        for x in range(96, int(BOARD_SIZE - width), 736):
            candidates.append(("open_canvas", float(x), float(y)))
    placements: list[dict[str, float | str]] = []
    for name, x, y in candidates:
        candidate = (float(x), float(y), width, height)
        if x < 0 or y < 0 or x + width > BOARD_SIZE or y + height > BOARD_SIZE:
            continue
        if any(_rect_intersection(candidate, _collision_rect(element)) > 0 for element in occupied):
            continue
        placements.append({"name": name, "x": x, "y": y, "width": width, "height": height})
        if len(placements) == 4:
            break
    return placements


def board_working_path(artifact_path: str | Path) -> Path:
    artifact = Path(artifact_path).expanduser().resolve(strict=False)
    if artifact.suffix.casefold() != ".modesign":
        raise ValueError("MO Board state requires a .modesign artifact")
    return artifact.with_name(artifact.stem + BOARD_WORKING_SUFFIX)


def load_board_working(
    artifact_path: str | Path,
    *,
    design_id: str,
    design_revision: int,
    committed: BoardState,
) -> dict[str, Any]:
    """Load valid private working state, preserving only a safe exact origin."""
    path = board_working_path(artifact_path)
    raw = _read_working(path)
    origin = _origin(raw.get("origin")) if isinstance(raw, dict) else None
    fallback = {
        "board": committed,
        "base_design_revision": int(design_revision),
        "base_board_revision": committed.revision,
        "draft": None,
        "origin": origin,
    }
    if not isinstance(raw, dict) or raw.get("version") != BOARD_WORKING_SCHEMA_ID:
        return fallback
    if str(raw.get("design_id") or "") != str(design_id or ""):
        return fallback
    try:
        base_design_revision = _integer(
            raw.get("base_design_revision"), "board working base design revision", 1, 1_000_000,
        )
        base_board_revision = _integer(
            raw.get("base_board_revision"), "board working base board revision", 0, 1_000_000,
        )
        working = parse_board(raw.get("board"))
        if (
            base_design_revision != int(design_revision)
            or base_board_revision != committed.revision
        ):
            return fallback
        draft = _draft(raw.get("draft"), working)
        return {
            "board": working,
            "base_design_revision": base_design_revision,
            "base_board_revision": base_board_revision,
            "draft": draft,
            "origin": origin,
        }
    except (TypeError, ValueError):
        return fallback


def save_board_working(
    artifact_path: str | Path,
    *,
    design_id: str,
    base_design_revision: int,
    base_board_revision: int,
    board: BoardState | dict[str, Any],
) -> dict[str, Any]:
    """Atomically save immediate user work without creating artifact history."""
    path = board_working_path(artifact_path)
    current = _read_working(path)
    validated = board if isinstance(board, BoardState) else parse_board(board)
    row = {
        "version": BOARD_WORKING_SCHEMA_ID,
        "design_id": str(design_id or ""),
        "base_design_revision": int(base_design_revision),
        "base_board_revision": int(base_board_revision),
        "board": validated.to_dict(),
        "draft": None,
        "origin": _origin(current.get("origin")) if isinstance(current, dict) else None,
    }
    _write_working(path, row)
    return row


def save_board_draft(
    artifact_path: str | Path,
    *,
    design_id: str,
    design_revision: int,
    committed: BoardState,
    operations: Iterable[dict[str, Any]],
    allow_overlap: bool = False,
) -> dict[str, Any]:
    """Persist one MO proposal as an explicit review layer."""
    working = load_board_working(
        artifact_path,
        design_id=design_id,
        design_revision=design_revision,
        committed=committed,
    )
    if working.get("draft"):
        raise BoardValidationError("Review or reject the current MO Board draft first")
    rows = [_clone_json(row) for row in operations]
    proposed = apply_board_operations(working["board"], rows, actor="mo")
    if not allow_overlap:
        added_ids = {
            str(row.get("element", {}).get("id") or "")
            for row in rows
            if isinstance(row, dict) and str(row.get("op") or "").strip().lower() == "add"
            and isinstance(row.get("element"), dict)
        }
        added = [element for element in proposed.elements if str(element["id"]) in added_ids]
        existing = [element for element in proposed.elements if str(element["id"]) not in added_ids]
        overlap = _first_material_overlap(added, existing)
        if overlap is not None:
            added_id, existing_id = overlap
            raise BoardValidationError(
                "MO Board draft overlaps existing content "
                f"({added_id} with {existing_id}). Read board.spatial and use a clear placement, "
                "or set allow_overlap=true only for an intentional annotation."
            )
    draft = {
        "id": secrets.token_hex(8),
        "base_board_revision": working["board"].revision,
        "operations": rows,
        "board": proposed.to_dict(),
    }
    path = board_working_path(artifact_path)
    current = _read_working(path)
    row = {
        "version": BOARD_WORKING_SCHEMA_ID,
        "design_id": design_id,
        "base_design_revision": working["base_design_revision"],
        "base_board_revision": working["base_board_revision"],
        "board": working["board"].to_dict(),
        "draft": draft,
        "origin": _origin(current.get("origin")) if isinstance(current, dict) else None,
    }
    _write_working(path, row)
    return draft


def _has_material_geometry(element: dict[str, Any]) -> bool:
    bounds = element["bounds"]
    width = float(bounds["width"])
    height = float(bounds["height"])
    return width >= 16.0 or height >= 16.0 or width * height >= 64.0


def _collision_rect(element: dict[str, Any]) -> tuple[float, float, float, float]:
    bounds = element["bounds"]
    width = float(bounds["width"])
    height = float(bounds["height"])
    collision_width = max(8.0, width)
    collision_height = max(8.0, height)
    return (
        float(bounds["x"]) - (collision_width - width) / 2.0,
        float(bounds["y"]) - (collision_height - height) / 2.0,
        collision_width,
        collision_height,
    )


def _rect_intersection(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[0] + first[2], second[0] + second[2])
    bottom = min(first[1] + first[3], second[1] + second[3])
    return max(0.0, right - left) * max(0.0, bottom - top)


def _first_material_overlap(
    added: list[dict[str, Any]],
    existing: list[dict[str, Any]],
) -> tuple[str, str] | None:
    material_existing = [element for element in existing if _has_material_geometry(element)]
    for candidate in added:
        if not _has_material_geometry(candidate):
            continue
        candidate_rect = _collision_rect(candidate)
        candidate_area = candidate_rect[2] * candidate_rect[3]
        for present in material_existing:
            present_rect = _collision_rect(present)
            intersection = _rect_intersection(candidate_rect, present_rect)
            smaller_area = min(candidate_area, present_rect[2] * present_rect[3])
            if intersection >= 64.0 and smaller_area > 0 and intersection / smaller_area >= 0.08:
                return str(candidate["id"]), str(present["id"])
    return None


def clear_board_working(
    artifact_path: str | Path,
    *,
    design_id: str,
    design_revision: int,
    committed: BoardState,
    keep_origin: bool = True,
) -> None:
    path = board_working_path(artifact_path)
    current = _read_working(path)
    origin = _origin(current.get("origin")) if keep_origin and isinstance(current, dict) else None
    if origin:
        _write_working(path, {
            "version": BOARD_WORKING_SCHEMA_ID,
            "design_id": design_id,
            "base_design_revision": int(design_revision),
            "base_board_revision": committed.revision,
            "board": committed.to_dict(),
            "draft": None,
            "origin": origin,
        })
    else:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def set_board_origin(artifact_path: str | Path, target: dict[str, Any]) -> None:
    """Record the exact originating terminal privately; never serialize it."""
    origin = _origin(target)
    if origin is None:
        raise BoardValidationError("The MO Board terminal origin is invalid")
    path = board_working_path(artifact_path)
    row = _read_working(path)
    if not isinstance(row, dict) or row.get("version") != BOARD_WORKING_SCHEMA_ID:
        row = {"version": BOARD_WORKING_SCHEMA_ID, "origin": origin}
    else:
        row = dict(row)
        row["origin"] = origin
    _write_working(path, row)


def reject_board_draft(artifact_path: str | Path) -> None:
    path = board_working_path(artifact_path)
    row = _read_working(path)
    if isinstance(row, dict) and row.get("version") == BOARD_WORKING_SCHEMA_ID:
        row = dict(row)
        row["draft"] = None
        _write_working(path, row)


def _element(value: Any, label: str) -> dict[str, Any]:
    raw = _mapping(value, label)
    _only_keys(raw, _ELEMENT_KEYS, label)
    kind = str(raw.get("kind") or "").strip().lower()
    actor = str(raw.get("actor") or "").strip().lower()
    if kind not in BOARD_KINDS:
        raise BoardValidationError(f"{label}.kind is invalid")
    if actor not in BOARD_ACTORS:
        raise BoardValidationError(f"{label}.actor must be user or mo")
    bounds = _mapping(raw.get("bounds"), f"{label}.bounds")
    _only_keys(bounds, _BOUNDS_KEYS, f"{label}.bounds")
    style = _mapping(raw.get("style", {}), f"{label}.style")
    _only_keys(style, _STYLE_KEYS, f"{label}.style")
    points_raw = raw.get("points", [])
    if not isinstance(points_raw, list) or len(points_raw) > MAX_POINTS_PER_STROKE:
        raise BoardValidationError(f"{label}.points must contain at most {MAX_POINTS_PER_STROKE} items")
    points = [_point(point, f"{label}.points[{index}]") for index, point in enumerate(points_raw)]
    if kind == "stroke" and not points:
        raise BoardValidationError(f"{label}.points is required for a stroke")
    if kind == "line" and len(points) != 2:
        raise BoardValidationError(f"{label}.points must contain exactly two points")
    if kind == "arrow" and len(points) not in {2, 3}:
        raise BoardValidationError(f"{label}.points must contain two endpoints or start, control, end")
    if kind not in {"stroke", "line", "arrow"} and points:
        raise BoardValidationError(f"{label}.points is supported only for stroke, line, or arrow")
    text = _text(raw.get("text", ""), f"{label}.text", limit=8_000)
    attachment_id = _text(raw.get("attachment_id", ""), f"{label}.attachment_id", limit=128)
    if kind == "text" and not text.strip():
        raise BoardValidationError(f"{label}.text is required")
    if kind == "image" and not attachment_id:
        raise BoardValidationError(f"{label}.attachment_id is required")
    x = _number(bounds.get("x", 0), f"{label}.bounds.x", 0, BOARD_SIZE)
    y = _number(bounds.get("y", 0), f"{label}.bounds.y", 0, BOARD_SIZE)
    width = _number(bounds.get("width", 0), f"{label}.bounds.width", 0, BOARD_SIZE)
    height = _number(bounds.get("height", 0), f"{label}.bounds.height", 0, BOARD_SIZE)
    if x + width > BOARD_SIZE + _GEOMETRY_EPSILON or y + height > BOARD_SIZE + _GEOMETRY_EPSILON:
        raise BoardValidationError(f"{label}.bounds must stay inside the Board")
    if points and any(
        point[0] < x - _GEOMETRY_EPSILON
        or point[0] > x + width + _GEOMETRY_EPSILON
        or point[1] < y - _GEOMETRY_EPSILON
        or point[1] > y + height + _GEOMETRY_EPSILON
        for point in points
    ):
        raise BoardValidationError(f"{label}.bounds must contain its points")
    return {
        "id": _element_id(raw.get("id"), f"{label}.id"),
        "kind": kind,
        "actor": actor,
        "revision": _integer(raw.get("revision", 1), f"{label}.revision", 1, 1_000_000),
        "z": _integer(raw.get("z", 0), f"{label}.z", 0, 1_000_000),
        "bounds": {
            "x": x, "y": y, "width": width, "height": height,
        },
        "style": {
            "stroke": _choice(style.get("stroke", "text"), f"{label}.style.stroke", BOARD_COLORS),
            "fill": _choice(style.get("fill", "none"), f"{label}.style.fill", BOARD_FILLS),
            "width": _number(style.get("width", 2), f"{label}.style.width", 0.5, 32),
            "opacity": _number(style.get("opacity", 1), f"{label}.style.opacity", 0.05, 1),
            "dash": _choice(style.get("dash", "solid"), f"{label}.style.dash", BOARD_DASHES),
            "arrowhead": _choice(style.get("arrowhead", "triangle"), f"{label}.style.arrowhead", BOARD_ARROWHEADS),
            "arrow_size": _number(style.get("arrow_size", 1), f"{label}.style.arrow_size", 0.5, 2.5),
        },
        "points": points,
        "text": text,
        "attachment_id": attachment_id,
    }


def _point(value: Any, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) not in {2, 3}:
        raise BoardValidationError(f"{label} must be [x, y] or [x, y, pressure]")
    point = [
        _number(value[0], f"{label}[0]", 0, BOARD_SIZE),
        _number(value[1], f"{label}[1]", 0, BOARD_SIZE),
    ]
    if len(value) == 3:
        point.append(_number(value[2], f"{label}[2]", 0, 1))
    return point


def _draft(value: Any, board: BoardState) -> dict[str, Any] | None:
    if value is None:
        return None
    raw = _mapping(value, "board draft")
    if set(raw) != {"id", "base_board_revision", "operations", "board"}:
        raise BoardValidationError("board draft fields are invalid")
    if _integer(raw.get("base_board_revision"), "board draft base revision", 0, 1_000_000) != board.revision:
        raise BoardValidationError("board draft is stale")
    operations = raw.get("operations")
    if not isinstance(operations, list):
        raise BoardValidationError("board draft operations are invalid")
    proposed = apply_board_operations(board, operations, actor="mo")
    stored = parse_board(raw.get("board"))
    if proposed.to_dict() != stored.to_dict():
        raise BoardValidationError("board draft preview does not match its operations")
    return {
        "id": _text(raw.get("id"), "board draft id", required=True, limit=80),
        "base_board_revision": board.revision,
        "operations": _clone_json(operations),
        "board": stored.to_dict(),
    }


def _origin(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    instance_id = str(value.get("instance_id") or "").strip()
    try:
        pid = int(value.get("pid") or 0)
    except (TypeError, ValueError, OverflowError):
        return None
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", instance_id) or pid <= 0:
        return None
    return {"instance_id": instance_id, "pid": pid}


def _read_working(path: Path) -> dict[str, Any]:
    try:
        data = path.read_bytes()
        if len(data) > MAX_WORKING_BYTES:
            return {}
        value = json.loads(data.decode("utf-8", errors="strict"))
        return dict(value) if isinstance(value, dict) else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def _write_working(path: Path, row: dict[str, Any]) -> None:
    encoded = json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_WORKING_BYTES:
        raise BoardValidationError("MO Board working state is too large")
    atomic_write_json(path, row, ensure_ascii=False, separators=(",", ":"))


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BoardValidationError(f"{label} must be an object")
    return dict(value)


def _only_keys(value: dict[str, Any], allowed: frozenset[str], label: str) -> None:
    extra = sorted(str(key) for key in value if key not in allowed)
    if extra:
        raise BoardValidationError(f"{label} contains unsupported field: {extra[0]}")


def _integer(value: Any, label: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise BoardValidationError(f"{label} must be an integer from {low} through {high}")
    return value


def _number(value: Any, label: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BoardValidationError(f"{label} must be a finite number")
    parsed = float(value)
    if not math.isfinite(parsed) or not low <= parsed <= high:
        raise BoardValidationError(f"{label} must be from {low:g} through {high:g}")
    return round(parsed, 3)


def _text(value: Any, label: str, *, required: bool = False, limit: int) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str) or "\x00" in value or len(value) > limit:
        raise BoardValidationError(f"{label} must be bounded text")
    if required and not value.strip():
        raise BoardValidationError(f"{label} is required")
    return value


def _strings(value: Any, label: str, *, maximum: int, item_limit: int) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, list) or len(value) > maximum:
        raise BoardValidationError(f"{label} must contain at most {maximum} items")
    return tuple(_text(item, f"{label} item", required=True, limit=item_limit) for item in value)


def _element_id(value: Any, label: str) -> str:
    text = _text(value, label, required=True, limit=80)
    if not _ELEMENT_ID_RE.fullmatch(text):
        raise BoardValidationError(f"{label} must use letters, numbers, underscore, or hyphen")
    return text


def _choice(value: Any, label: str, allowed: frozenset[str]) -> str:
    selected = str(value or "").strip().lower()
    if selected not in allowed:
        raise BoardValidationError(f"{label} is invalid")
    return selected


def _clone_json(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, separators=(",", ":")))


__all__ = [
    "BOARD_ACTORS", "BOARD_COLORS", "BOARD_FILLS", "BOARD_KINDS", "BOARD_SCHEMA_ID",
    "BoardState", "BoardValidationError", "apply_board_operations", "board_operation_contract", "board_summary",
    "board_working_path", "clear_board_working", "empty_board", "load_board_working",
    "parse_board", "reject_board_draft", "save_board_draft", "save_board_working",
    "set_board_origin",
]
