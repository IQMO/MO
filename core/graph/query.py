"""Bounded query helpers over MO's native structural graph.

These APIs are graph-orientation helpers, not proof. Callers still need file
reads, tests, diagnostics, or runtime evidence before making completion claims.
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
import re
from typing import Any

from .structural_graph import (
    _SKIP_RELATIONS_FOR_CONTEXT,
    _analysis_list,
    _as_int,
    _clean,
    _community_name,
    _edge_confidence,
    _graph_topology,
    _node_map,
    graph_status,
    load_analysis,
    load_labels,
    load_or_build_graph_data,
    project_root,
)

MAX_QUERY_DEPTH = 4
MAX_PATH_HOPS = 8
MAX_QUERY_LIMIT = 50


def explain(query: str, *, cwd: str | Path | None = None, limit: int = 12) -> dict[str, Any]:
    """Explain one matched node with bounded direct graph context."""
    root = project_root(cwd)
    data = load_or_build_graph_data(root, build_if_missing=False, refresh_if_stale=False)
    if not data:
        return _empty("graph_unavailable", query=query)
    nodes = _node_map(data)
    labels = load_labels(root)
    resolution = _match_node_ids(data, query, limit=5, details=True)
    matches = resolution["ids"]
    if not matches:
        return {"available": True, "matched": False, "query": str(query or ""), "orientation_only": True, **resolution}
    if resolution["ambiguous"]:
        return {
            "available": True, "matched": False, "ambiguous": True, "query": str(query or ""),
            "orientation_only": True, "match_type": resolution["match_type"],
            "candidates": [_node_summary(nodes.get(node_id, {}), node_id, labels) for node_id in matches],
        }
    target = matches[0]
    adjacent = _adjacency(data, directed=False).get(target, [])
    confidence: dict[str, int] = {}
    neighbours: list[dict[str, Any]] = []
    for other_id, edge, reverse in adjacent:
        relation = str(edge.get("relation") or edge.get("type") or "")
        if relation in _SKIP_RELATIONS_FOR_CONTEXT:
            continue
        conf = _edge_confidence(edge)
        confidence[conf] = confidence.get(conf, 0) + 1
        neighbours.append(_edge_summary(edge, nodes, reverse=reverse))
    neighbours.sort(key=lambda item: (item.get("relation", ""), item.get("target_label", ""), item.get("source_label", "")))
    return {
        "available": True,
        "matched": True,
        "query": str(query or ""),
        "orientation_only": True,
        "node": _node_summary(nodes.get(target, {}), target, labels),
        "degree": len(neighbours),
        "confidence_breakdown": dict(sorted(confidence.items())),
        "neighbours": neighbours[:_bounded(limit, 12, minimum=1, maximum=MAX_QUERY_LIMIT)],
        "matched_ids": matches,
        "match_type": resolution["match_type"],
    }


def neighbors(
    query: str,
    *,
    cwd: str | Path | None = None,
    depth: int = 1,
    limit: int = 20,
    relation_filter: str | None = None,
) -> dict[str, Any]:
    """Return a bounded neighborhood around the best graph match."""
    root = project_root(cwd)
    data = load_or_build_graph_data(root, build_if_missing=False, refresh_if_stale=False)
    if not data:
        return _empty("graph_unavailable", query=query)
    nodes = _node_map(data)
    labels = load_labels(root)
    resolution = _match_node_ids(data, query, limit=3, details=True)
    matches = resolution["ids"]
    if not matches:
        return {"available": True, "matched": False, "query": str(query or ""), "orientation_only": True, **resolution}
    if resolution["ambiguous"]:
        return {
            "available": True, "matched": False, "ambiguous": True, "query": str(query or ""),
            "orientation_only": True, "match_type": resolution["match_type"],
            "candidates": [_node_summary(nodes.get(node_id, {}), node_id, labels) for node_id in matches],
        }
    wanted_relation = str(relation_filter or "").strip().lower()
    max_depth = _bounded(depth, 1, minimum=1, maximum=MAX_QUERY_DEPTH)
    max_items = _bounded(limit, 20, minimum=1, maximum=MAX_QUERY_LIMIT)
    adjacency = _adjacency(data, directed=False)
    seen_nodes: set[str] = set(matches[:1])
    seen_edges: set[tuple[str, str, str]] = set()
    out_edges: list[dict[str, Any]] = []
    queue: deque[tuple[str, int]] = deque((node_id, 0) for node_id in matches[:1])
    while queue and len(out_edges) < max_items:
        current, dist = queue.popleft()
        if dist >= max_depth:
            continue
        for other_id, edge, reverse in adjacency.get(current, []):
            relation = str(edge.get("relation") or edge.get("type") or "")
            if relation in _SKIP_RELATIONS_FOR_CONTEXT:
                continue
            if wanted_relation and relation.lower() != wanted_relation:
                continue
            key = (str(edge.get("source") or ""), str(edge.get("target") or ""), relation)
            if key not in seen_edges:
                seen_edges.add(key)
                out_edges.append(_edge_summary(edge, nodes, reverse=reverse))
            if other_id not in seen_nodes:
                seen_nodes.add(other_id)
                queue.append((other_id, dist + 1))
            if len(out_edges) >= max_items:
                break
    return {
        "available": True,
        "matched": True,
        "query": str(query or ""),
        "orientation_only": True,
        "root_ids": matches[:1],
        "depth": max_depth,
        "nodes": [_node_summary(nodes.get(node_id, {}), node_id, labels) for node_id in sorted(seen_nodes)],
        "edges": out_edges,
        "truncated": len(out_edges) >= max_items,
        "match_type": resolution["match_type"],
    }


def shortest_path(
    source: str,
    target: str,
    *,
    cwd: str | Path | None = None,
    max_hops: int = MAX_PATH_HOPS,
) -> dict[str, Any]:
    """Find a bounded undirected shortest path between two graph concepts."""
    root = project_root(cwd)
    data = load_or_build_graph_data(root, build_if_missing=False, refresh_if_stale=False)
    if not data:
        return _empty("graph_unavailable", source=source, target=target)
    nodes = _node_map(data)
    labels = load_labels(root)
    source_match = _match_node_ids(data, source, limit=5, details=True)
    target_match = _match_node_ids(data, target, limit=5, details=True)
    source_ids = source_match["ids"]
    target_ids = set(target_match["ids"])
    if source_match["ambiguous"] or target_match["ambiguous"]:
        return {
            "available": True,
            "found": False,
            "source": str(source or ""),
            "target": str(target or ""),
            "orientation_only": True,
            "reason": "endpoint_ambiguous",
            "source_ambiguous": source_match["ambiguous"],
            "target_ambiguous": target_match["ambiguous"],
        }
    if not source_ids or not target_ids:
        return {
            "available": True,
            "found": False,
            "source": str(source or ""),
            "target": str(target or ""),
            "orientation_only": True,
            "reason": "endpoint_not_found",
        }
    max_depth = _bounded(max_hops, MAX_PATH_HOPS, minimum=1, maximum=MAX_PATH_HOPS)
    adjacency = _adjacency(data, directed=False)
    allow_containment = any(
        str(nodes.get(node_id, {}).get("file_type") or nodes.get(node_id, {}).get("type") or "") == "file"
        for node_id in [*source_ids, *target_ids]
    )
    queue: deque[tuple[str, int]] = deque((node_id, 0) for node_id in source_ids)
    prev: dict[str, tuple[str, dict[str, Any], bool]] = {}
    seen = set(source_ids)
    found = ""
    while queue:
        current, dist = queue.popleft()
        if current in target_ids:
            found = current
            break
        if dist >= max_depth:
            continue
        for other_id, edge, reverse in adjacency.get(current, []):
            relation = str(edge.get("relation") or edge.get("type") or "")
            if (
                (relation in _SKIP_RELATIONS_FOR_CONTEXT and not (
                    allow_containment and relation in {"contains", "method"}
                ))
                or other_id in seen
            ):
                continue
            seen.add(other_id)
            prev[other_id] = (current, edge, reverse)
            queue.append((other_id, dist + 1))
    if not found:
        return {
            "available": True,
            "found": False,
            "source": str(source or ""),
            "target": str(target or ""),
            "orientation_only": True,
            "max_hops": max_depth,
            "reason": "path_not_found",
        }
    path_ids = [found]
    path_edges: list[dict[str, Any]] = []
    current = found
    while current in prev:
        parent, edge, reverse = prev[current]
        path_edges.append(_edge_summary(edge, nodes, reverse=reverse))
        current = parent
        path_ids.append(current)
    path_ids.reverse()
    path_edges.reverse()
    return {
        "available": True,
        "found": True,
        "source": str(source or ""),
        "target": str(target or ""),
        "orientation_only": True,
        "hops": max(0, len(path_ids) - 1),
        "nodes": [_node_summary(nodes.get(node_id, {}), node_id, labels) for node_id in path_ids],
        "edges": path_edges,
        "source_match_type": source_match["match_type"],
        "target_match_type": target_match["match_type"],
    }


def stats(*, cwd: str | Path | None = None, limit: int = 10) -> dict[str, Any]:
    """Return a compact graph audit/stats summary safe for user-facing formatters."""
    root = project_root(cwd)
    status = graph_status(root)
    if not status.get("available"):
        return {"available": False, "orientation_only": True, "status": _public_status(status)}
    analysis = load_analysis(root)
    max_items = _bounded(limit, 10, minimum=1, maximum=MAX_QUERY_LIMIT)
    return {
        "available": True,
        "orientation_only": True,
        "status": _public_status(status),
        "god_nodes": _analysis_list(analysis, "gods")[:max_items],
        "surprises": _analysis_list(analysis, "surprises")[:max_items],
        "communities": _analysis_list(analysis, "communities")[:max_items],
    }


def _empty(reason: str, **extra: Any) -> dict[str, Any]:
    out = {"available": False, "orientation_only": True, "reason": reason}
    out.update(extra)
    return out


def _bounded(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return max(minimum, min(maximum, result))


def _match_node_ids(
    data: dict[str, Any], query: str, *, limit: int, details: bool = False
) -> list[str] | dict[str, Any]:
    nodes = _node_map(data)
    raw = str(query or "").strip().lower()
    if not raw:
        result = {"ids": [], "match_type": "none", "ambiguous": False}
        return result if details else []
    normalized = raw.replace("\\", "/")
    from .search import exact_file_node_id

    file_node_id = exact_file_node_id(nodes, normalized)
    if file_node_id:
        result = {"ids": [file_node_id], "match_type": "exact", "ambiguous": False}
        return result if details else result["ids"]
    symbol_shaped = _symbol_shaped_query(str(query or ""))
    exact: list[str] = []
    substring: list[str] = []
    for node_id, node in nodes.items():
        source = str(node.get("source_file") or "").lower().replace("\\", "/")
        label = str(node.get("label") or node.get("name") or "").lower()
        short_name = str(node.get("short_name") or "").lower()
        qualified_name = str(node.get("qualified_name") or "").lower()
        # The dotted module-qualified form is what a caller naturally writes for
        # a Python symbol, and the tool description steers them to it — but only
        # path-shaped keys existed, so `core.agent.agent.Agent.method` failed
        # closed while `Agent.method` matched exactly. Accept both.
        module_dotted = _module_dotted_path(source)
        values = {
            node_id.lower(),
            label,
            short_name,
            qualified_name,
            source,
            f"{source}:{label}" if source and label else "",
            f"{source}:{short_name}" if source and short_name else "",
            f"{source}:{qualified_name}" if source and qualified_name else "",
            f"{module_dotted}.{label}" if module_dotted and label else "",
            f"{module_dotted}.{short_name}" if module_dotted and short_name else "",
            f"{module_dotted}.{qualified_name}" if module_dotted and qualified_name else "",
            module_dotted,
        }
        if normalized in values:
            exact.append(node_id)
        elif not symbol_shaped and any(normalized in value for value in values if value):
            substring.append(node_id)
    max_items = _bounded(limit, 5, minimum=1, maximum=MAX_QUERY_LIMIT)
    if exact:
        exact.sort()
        ambiguous = len(exact) > 1 and not any(marker in raw for marker in (".", ":", "/", "\\"))
        result = {"ids": exact[:max_items], "match_type": "exact", "ambiguous": ambiguous}
        return result if details else result["ids"]
    if substring:
        substring.sort()
        result = {"ids": substring[:max_items], "match_type": "substring", "ambiguous": False}
        return result if details else result["ids"]
    # Symbol/path-shaped queries must fail closed. A missing qualified target is
    # not a prose search request and must never degrade to an unrelated file or
    # method merely because pieces of its path happen to match.
    if symbol_shaped:
        # Still fail closed — a missing qualified target must never degrade to an
        # unrelated match. But name the near misses so the caller can correct the
        # spelling instead of concluding the symbol has no callers.
        result = {
            "ids": [],
            "match_type": "none",
            "ambiguous": False,
            "suggestions": _symbol_suggestions(nodes, normalized, limit=max_items),
        }
        return result if details else []
    try:
        from .search import rank_graph_nodes

        ranked = rank_graph_nodes(data, raw, top_n=max_items)
    except Exception:
        ranked = []
    ids = [str(item.get("id") or "") for item in ranked if item.get("id")]
    result = {"ids": ids, "match_type": "terms" if ids else "none", "ambiguous": False}
    return result if details else ids


def _symbol_suggestions(nodes: dict[str, Any], normalized: str, *, limit: int = 5) -> list[str]:
    """Return node ids whose trailing name matches the query's trailing name."""
    tail = normalized.replace(":", ".").replace("/", ".").rsplit(".", 1)[-1].strip()
    if len(tail) < 3:
        return []
    hits: list[str] = []
    names: dict[str, list[str]] = {}
    for node_id, node in nodes.items():
        short_name = str(node.get("short_name") or "").lower()
        label = str(node.get("label") or node.get("name") or "").lower()
        if tail == short_name or label.endswith("." + tail) or label == tail:
            hits.append(node_id)
        if short_name:
            names.setdefault(short_name, []).append(node_id)
    if not hits:
        # No exact trailing name: the caller most likely mistyped it.
        import difflib

        for close in difflib.get_close_matches(tail, list(names), n=limit, cutoff=0.8):
            hits.extend(names[close])
    hits.sort()
    return hits[:limit]


def _module_dotted_path(source: str) -> str:
    """Return the Python module path for a source file, or "" when not a module.

    ``core/agent/agent.py`` -> ``core.agent.agent`` and
    ``core/graph/__init__.py`` -> ``core.graph``.
    """
    value = str(source or "").strip().lower().replace("\\", "/")
    if not value.endswith(".py"):
        return ""
    value = value[: -len(".py")]
    if value.endswith("/__init__"):
        value = value[: -len("/__init__")]
    if not value:
        return ""
    return value.replace("/", ".")


def _symbol_shaped_query(query: str) -> bool:
    value = str(query or "").strip()
    if not value:
        return False
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", value):
        return True
    return not any(char.isspace() for char in value) and any(
        marker in value for marker in ("/", "\\", ":")
    )


def _adjacency(data: dict[str, Any], *, directed: bool) -> dict[str, list[tuple[str, dict[str, Any], bool]]]:
    _edges, adjacency = _graph_topology(data)
    if not directed:
        return adjacency
    return {
        node_id: [row for row in rows if not row[2]]
        for node_id, rows in adjacency.items()
    }


def _node_summary(node: dict[str, Any], node_id: str, labels: dict[int, str]) -> dict[str, Any]:
    cid = _as_int(node.get("community"))
    return {
        "id": node_id,
        "label": _clean(node.get("label") or node.get("name") or node_id, 120),
        "type": _clean(node.get("file_type") or node.get("type") or "node", 40),
        "source_file": _clean(str(node.get("source_file") or "").replace("\\", "/"), 220),
        "source_location": _clean(node.get("source_location") or "", 40),
        "community": cid,
        "community_label": _community_name(cid, labels) if cid is not None else "",
        "short_name": _clean(node.get("short_name") or "", 120),
        "qualified_name": _clean(node.get("qualified_name") or "", 180),
    }


def _edge_summary(edge: dict[str, Any], nodes: dict[str, dict[str, Any]], *, reverse: bool) -> dict[str, Any]:
    src = str(edge.get("source") or "")
    tgt = str(edge.get("target") or "")
    src_node = nodes.get(src, {})
    tgt_node = nodes.get(tgt, {})
    return {
        "source": tgt if reverse else src,
        "target": src if reverse else tgt,
        "source_label": _clean((tgt_node if reverse else src_node).get("label") or (tgt if reverse else src), 120),
        "target_label": _clean((src_node if reverse else tgt_node).get("label") or (src if reverse else tgt), 120),
        "relation": _clean(edge.get("relation") or edge.get("type") or "related", 50),
        "confidence": _edge_confidence(edge),
        "reverse": bool(reverse),
        "source_file": _clean(edge.get("source_file") or src_node.get("source_file") or "", 220),
        "resolution": _clean(edge.get("resolution") or "", 40),
    }


def _public_status(status: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "available", "source_kind", "nodes", "edges", "communities", "groups", "group_strategy", "stale", "trust", "quality",
        "confidence_breakdown", "provenance_breakdown", "legacy_confidence_edges",
        "unknown_confidence_edges",
    )
    return {key: status.get(key) for key in keys if key in status}
