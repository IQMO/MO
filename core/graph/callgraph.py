"""Call-graph traversal over MO's structural graph — internal tool for MO.

When MO needs to answer "who calls X?" or "what does X call?", this walks the
structural graph's dependency edges. Zero new dependencies — reuses the existing
graph data that structural_graph.py already builds.

Exposed to MO as the first-class ``find_callers`` / ``find_callees`` tools
(tools/__init__.py); the shell one-liners below are only a manual/debug fallback:
    python -c "from core.graph.callgraph import get_callers; import json; \\
        print(json.dumps(get_callers('run_turn'), indent=2))"
    python -c "from core.graph.callgraph import get_callees; import json; \\
        print(json.dumps(get_callees('run_turn'), indent=2))"
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .query import _match_node_ids
from .structural_graph import (
    _edge_confidence,
    _graph_topology,
    _node_map,
    load_or_build_graph_data,
    project_root,
)

# Relations that indicate a forward dependency / call relationship
_CALL_RELATIONS = {
    "calls", "references", "imports", "imports_from", "uses",
    "extends", "inherits", "implements", "mixes_in", "passes_callback",
}


def _trace(
    symbol: str,
    *,
    cwd: str | Path | None,
    max_depth: int,
    reverse: bool,
    return_meta: bool,
) -> list[dict[str, Any]] | dict[str, Any]:
    root = project_root(cwd)
    data = load_or_build_graph_data(root, build_if_missing=False, refresh_if_stale=False)
    if not data:
        return {
            "available": False,
            "reason": "graph_unavailable",
            "results": [],
            "matched_ids": [],
            "ambiguous": False,
            "match_type": "none",
        } if return_meta else []

    nodes = _node_map(data)
    match = _match_node_ids(data, symbol, limit=5, details=True)
    start_ids = set(match["ids"])
    if match["ambiguous"]:
        return {
            "available": True,
            "results": [],
            "matched_ids": match["ids"],
            "ambiguous": True,
            "match_type": match["match_type"],
        } if return_meta else []
    if not start_ids:
        return {
            "available": True,
            "results": [],
            "matched_ids": [],
            "ambiguous": False,
            "match_type": match["match_type"],
            # Carry near misses through so a mistyped or wrongly-qualified symbol
            # reads as "did you mean" rather than "this symbol has no callers".
            "suggestions": list(match.get("suggestions") or []),
        } if return_meta else []

    _edges, adjacency = _graph_topology(data)

    visited: set[str] = set()
    results: list[dict[str, Any]] = []

    def walk(current_ids: set[str], depth: int) -> None:
        if depth > max_depth or not current_ids:
            return
        next_ids: set[str] = set()
        for cid in current_ids:
            if cid in visited:
                continue
            visited.add(cid)
            for other_id, edge, edge_reverse in adjacency.get(cid, []):
                if edge_reverse != reverse:
                    continue
                relation = str(edge.get("relation") or edge.get("type") or "")
                if relation not in _CALL_RELATIONS:
                    continue
                if other_id in visited:
                    continue
                caller_id, callee_id = (other_id, cid) if reverse else (cid, other_id)
                caller_node = nodes.get(caller_id, {})
                callee_node = nodes.get(callee_id, {})
                results.append({
                    "caller_id": caller_id,
                    "caller_label": caller_node.get("label") or caller_node.get("name") or caller_id,
                    "caller_file": caller_node.get("source_file") or "",
                    "caller_location": caller_node.get("source_location") or "",
                    "callee_id": callee_id,
                    "callee_label": callee_node.get("label") or callee_node.get("name") or callee_id,
                    "callee_file": callee_node.get("source_file") or "",
                    "callee_location": callee_node.get("source_location") or "",
                    "relation": edge.get("relation") or edge.get("type") or "",
                    "confidence": _edge_confidence(edge),
                    "resolution": edge.get("resolution") or "",
                    "depth": depth,
                })
                next_ids.add(other_id)
        walk(next_ids, depth + 1)

    walk(start_ids, 1)
    if return_meta:
        return {
            "available": True,
            "results": results,
            "matched_ids": sorted(start_ids),
            "ambiguous": False,
            "match_type": match["match_type"],
        }
    return results


def get_callers(
    symbol: str,
    *,
    cwd: str | Path | None = None,
    max_depth: int = 2,
    return_meta: bool = False,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Return who calls/depends on the given symbol, walking edges backward."""
    return _trace(symbol, cwd=cwd, max_depth=max_depth, reverse=True, return_meta=return_meta)


def get_callees(
    symbol: str,
    *,
    cwd: str | Path | None = None,
    max_depth: int = 2,
    return_meta: bool = False,
) -> list[dict[str, Any]] | dict[str, Any]:
    """Return what the given symbol calls/depends on, walking edges forward."""
    return _trace(symbol, cwd=cwd, max_depth=max_depth, reverse=False, return_meta=return_meta)
