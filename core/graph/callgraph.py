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

from .structural_graph import _edge_list, _node_map, load_or_build_graph_data, project_root

# Relations that indicate a forward dependency / call relationship
_CALL_RELATIONS = {
    "calls", "references", "imports", "imports_from", "uses",
    "extends", "inherits", "implements", "mixes_in",
}


def _find_node_ids(data: dict[str, Any], symbol: str) -> list[str]:
    """Find graph node IDs matching a symbol name, ranked by match quality.

    Priorities: 0=exact match, 1=word match, 2=substring match.
    """
    nodes = _node_map(data)
    symbol_lower = symbol.lower()
    matches: list[tuple[int, str]] = []
    for nid, node in nodes.items():
        label = str(node.get("label") or node.get("name") or "").lower()
        nid_lower = nid.lower()
        if label == symbol_lower or nid_lower == symbol_lower:
            matches.append((0, nid))
        elif symbol_lower in label.split() or symbol_lower in nid_lower.split("_"):
            matches.append((1, nid))
        elif symbol_lower in label or symbol_lower in nid_lower:
            matches.append((2, nid))
    matches.sort()
    return [nid for _, nid in matches[:5]]


def get_callers(
    symbol: str,
    *,
    cwd: str | Path | None = None,
    max_depth: int = 2,
) -> list[dict[str, Any]]:
    """Return who calls/depends on the given symbol.

    Walks edges backward up to *max_depth*: finds nodes where the target matches
    *symbol* and the source is the caller.

    Returns list of dicts with: caller_id, caller_label, caller_file,
    callee_id, callee_label, relation, depth.
    """
    root = project_root(cwd)
    data = load_or_build_graph_data(root)
    if not data:
        return []

    nodes = _node_map(data)
    target_ids = set(_find_node_ids(data, symbol))
    if not target_ids:
        return []

    # Build reverse adjacency: target -> list of (source, edge)
    reverse: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for edge in _edge_list(data):
        if not isinstance(edge, dict):
            continue
        relation = str(edge.get("relation") or edge.get("type") or "")
        if relation not in _CALL_RELATIONS:
            continue
        src = str(edge.get("source") or "")
        tgt = str(edge.get("target") or "")
        if not src or not tgt:
            continue
        reverse.setdefault(tgt, []).append((src, edge))

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
            for caller_id, edge in reverse.get(cid, []):
                if caller_id in visited:
                    continue
                caller_node = nodes.get(caller_id, {})
                callee_node = nodes.get(cid, {})
                results.append({
                    "caller_id": caller_id,
                    "caller_label": caller_node.get("label") or caller_node.get("name") or caller_id,
                    "caller_file": caller_node.get("source_file") or "",
                    "callee_id": cid,
                    "callee_label": callee_node.get("label") or callee_node.get("name") or cid,
                    "relation": edge.get("relation") or edge.get("type") or "",
                    "depth": depth,
                })
                next_ids.add(caller_id)
        walk(next_ids, depth + 1)

    walk(target_ids, 1)
    return results


def get_callees(
    symbol: str,
    *,
    cwd: str | Path | None = None,
    max_depth: int = 2,
) -> list[dict[str, Any]]:
    """Return what the given symbol calls/depends on.

    Walks edges forward up to *max_depth*: finds nodes where the source matches
    *symbol* and the target is the callee.

    Returns list of dicts with: caller_id, caller_label, callee_id,
    callee_label, callee_file, relation, depth.
    """
    root = project_root(cwd)
    data = load_or_build_graph_data(root)
    if not data:
        return []

    nodes = _node_map(data)
    source_ids = set(_find_node_ids(data, symbol))
    if not source_ids:
        return []

    # Build forward adjacency: source -> list of (target, edge)
    forward: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for edge in _edge_list(data):
        if not isinstance(edge, dict):
            continue
        relation = str(edge.get("relation") or edge.get("type") or "")
        if relation not in _CALL_RELATIONS:
            continue
        src = str(edge.get("source") or "")
        tgt = str(edge.get("target") or "")
        if not src or not tgt:
            continue
        forward.setdefault(src, []).append((tgt, edge))

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
            for callee_id, edge in forward.get(cid, []):
                if callee_id in visited:
                    continue
                caller_node = nodes.get(cid, {})
                callee_node = nodes.get(callee_id, {})
                results.append({
                    "caller_id": cid,
                    "caller_label": caller_node.get("label") or caller_node.get("name") or cid,
                    "callee_id": callee_id,
                    "callee_label": callee_node.get("label") or callee_node.get("name") or callee_id,
                    "callee_file": callee_node.get("source_file") or "",
                    "relation": edge.get("relation") or edge.get("type") or "",
                    "depth": depth,
                })
                next_ids.add(callee_id)
        walk(next_ids, depth + 1)

    walk(source_ids, 1)
    return results
