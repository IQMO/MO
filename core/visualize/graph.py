"""JSON → graph parser — for MO's structure visualizer (core/visualize).

Core algorithm: recursive traversal of native Python JSON objects
into a node/edge graph suitable for diagram rendering.

Zero dependencies beyond the stdlib.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class NodeRow:
    """A single row of text inside a graph node."""

    key: str | None
    value: str | int | float | None | bool
    type: str  # "string" | "number" | "boolean" | "null" | "object" | "array"
    children_count: int | None = None
    to: list[str] | None = None


@dataclass
class NodeData:
    """One graph node."""

    id: str
    text: list[NodeRow] = field(default_factory=list)
    width: int = 0
    height: int = 0
    path: list[str | int] | None = None
    parent_key: str | None = None
    parent_type: str | None = None


@dataclass
class EdgeData:
    """One directed edge between nodes."""

    id: str
    from_id: str
    to_id: str
    text: str | None = None


@dataclass
class GraphData:
    """Complete parse result."""

    nodes: list[NodeData] = field(default_factory=list)
    edges: list[EdgeData] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Node size estimator (simplified from calculateNodeSize.ts)
# ---------------------------------------------------------------------------

_AVG_CHAR_WIDTH = 8
_LINE_HEIGHT = 20
_PADDING_X = 16
_PADDING_Y = 8


def _estimate_node_size(text_rows: list[NodeRow]) -> tuple[int, int]:
    """Estimate pixel dimensions for a node's text content."""
    if not text_rows:
        return 100, 30

    max_chars = 0
    for row in text_rows:
        key_str = row.key or ""
        val = row.value
        if row.type == "object":
            val_str = f"{{{row.children_count or 0} keys}}"
        elif row.type == "array":
            val_str = f"[{row.children_count or 0} items]"
        elif val is None:
            val_str = "null"
        else:
            val_str = str(val)
        # "key: value" layout
        line = f"{key_str}: {val_str}" if key_str else val_str
        max_chars = max(max_chars, len(line))

    width = max(100, max_chars * _AVG_CHAR_WIDTH + _PADDING_X * 2)
    height = len(text_rows) * _LINE_HEIGHT + _PADDING_Y * 2
    return width, height


# ---------------------------------------------------------------------------
# JSON type detection
# ---------------------------------------------------------------------------


def _json_type(value: Any) -> str:
    """Return JSON-style type string for a Python value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


# ---------------------------------------------------------------------------
# Lenient JSON loader (fault-tolerant — comments and trailing commas)
# ---------------------------------------------------------------------------

_JSON_COMMENT_RE = re.compile(r"//.*?$|/\*.*?\*/", re.MULTILINE | re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")


def _lenient_json_loads(text: str) -> Any:
    """Attempt to parse lenient JSON (comments, trailing commas).

    Returns the parsed object or raises ValueError.
    """
    cleaned = _JSON_COMMENT_RE.sub("", text)
    cleaned = _TRAILING_COMMA_RE.sub(r"\1", cleaned)
    return json.loads(cleaned)


# ---------------------------------------------------------------------------
# Core algorithm — structure traversal
# ---------------------------------------------------------------------------


def parse_json_graph(
    json_input: str | dict | list,
    *,
    lenient: bool = False,
    max_nodes: int = 1000,
) -> GraphData:
    """Convert JSON text / object into a node-edge graph.

    Args:
        json_input: JSON string, or a native dict/list.
        lenient: If True, try lenient parsing before falling back to strict.
        max_nodes: Abort if graph exceeds this many nodes.

    Returns:
        GraphData with nodes, edges, and any parse errors.
    """
    # --- normalize to native Python object ---
    if isinstance(json_input, (dict, list)):
        root = json_input
    elif isinstance(json_input, str):
        root = None
        errors: list[str] = []
        # strict first
        try:
            root = json.loads(json_input)
        except (json.JSONDecodeError, ValueError) as strict_err:
            if lenient:
                try:
                    root = _lenient_json_loads(json_input)
                except (json.JSONDecodeError, ValueError) as lenient_err:
                    errors.append(f"JSON parse error: {lenient_err}")
            else:
                errors.append(f"JSON parse error: {strict_err}")

        if root is None:
            return GraphData(errors=errors)
    else:
        return GraphData(errors=[f"Unsupported input type: {type(json_input).__name__}"])

    # --- traverse ---
    graph = GraphData()
    _node_counter = [1]  # mutable counter for node IDs
    _edge_counter = [1]

    def _traverse(
        value: Any,
        parent_id: str | None = None,
        parent_key: str | None = None,
        parent_type: str | None = None,
        path: list[str | int] | None = None,
    ) -> str | None:
        if _node_counter[0] > max_nodes:
            return None

        node_id = str(_node_counter[0])
        _node_counter[0] += 1
        current_path = list(path or [])

        rows: list[NodeRow] = []
        value_type = _json_type(value)

        # --- array ---
        if isinstance(value, list):
            if not value:
                rows.append(NodeRow(key=parent_key, value="[]", type="array", children_count=0))
            else:
                # array box shows "[N items]"
                rows.append(
                    NodeRow(
                        key=parent_key,
                        value=f"[{len(value)} items]",
                        type="array",
                        children_count=len(value),
                    )
                )
                # wire edges to each child in a flat list
                child_ids: list[str] = []
                for i, item in enumerate(value):
                    child_path = current_path + [i]
                    child_id = _traverse(item, parent_id=None, parent_key=None, parent_type="array", path=child_path)
                    if child_id:
                        child_ids.append(child_id)
                        graph.edges.append(
                            EdgeData(
                                id=str(_edge_counter[0]),
                                from_id=node_id,
                                to_id=child_id,
                            )
                        )
                        _edge_counter[0] += 1
                if child_ids:
                    rows[0].to = child_ids

        # --- object ---
        elif isinstance(value, dict):
            if not value:
                rows.append(NodeRow(key=parent_key, value="{}", type="object", children_count=0))
            else:
                rows.append(
                    NodeRow(
                        key=parent_key,
                        value=f"{{{len(value)} keys}}",
                        type="object",
                        children_count=len(value),
                    )
                )
                child_ids: list[str] = []
                for k, v in value.items():
                    child_path = current_path + [k]
                    child_id = _traverse(v, parent_id=node_id, parent_key=k, parent_type="object", path=child_path)
                    if child_id:
                        child_ids.append(child_id)
                        graph.edges.append(
                            EdgeData(
                                id=str(_edge_counter[0]),
                                from_id=node_id,
                                to_id=child_id,
                                text=k,
                            )
                        )
                        _edge_counter[0] += 1
                if child_ids:
                    rows[0].to = child_ids

        # --- scalar ---
        else:
            rows.append(
                NodeRow(
                    key=parent_key,
                    value=value,
                    type=value_type,
                )
            )

        # --- build NodeData ---
        width, height = _estimate_node_size(rows)
        node = NodeData(
            id=node_id,
            text=rows,
            width=width,
            height=height,
            path=current_path if current_path else None,
            parent_key=parent_key,
            parent_type=parent_type,
        )
        graph.nodes.append(node)
        return node_id

    _traverse(root, parent_id=None, parent_key=None, parent_type=_json_type(root), path=[])
    return graph


# ---------------------------------------------------------------------------
# Quick access
# ---------------------------------------------------------------------------


def graph_to_dict(graph: GraphData) -> dict[str, Any]:
    """Serialize GraphData to a plain dict (useful for JSON export / debugging)."""
    return {
        "nodes": [
            {
                "id": n.id,
                "text": [
                    {
                        "key": r.key,
                        "value": str(r.value) if r.value is not None else None,
                        "type": r.type,
                        "children_count": r.children_count,
                        "to": r.to,
                    }
                    for r in n.text
                ],
                "width": n.width,
                "height": n.height,
                "path": n.path,
                "parent_key": n.parent_key,
                "parent_type": n.parent_type,
            }
            for n in graph.nodes
        ],
        "edges": [
            {"id": e.id, "from": e.from_id, "to": e.to_id, "text": e.text}
            for e in graph.edges
        ],
        "errors": graph.errors,
    }
