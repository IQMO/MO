"""Optional structural graph support for MO.

MO can build its native community code map under ``memory/structural_graph`` and
can also consume a compatible external ``graphify-out/graph.json`` input when it
exists. The graph is orientation only; ``core.graph.code_graph`` remains the public
fallback path when no useful structural graph is available.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shlex
import subprocess
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any
import traceback

from ..backend_monitor import get_monitor, redact_monitor_text
from ..env_utils import int_env
from ..number_utils import as_optional_int as _as_int
from ..path_defaults import ENV_MO_STATE_HOME, project_cache_dir
from ..text_utils import DEFAULT_CONTEXT_STOPWORDS

STRUCTURAL_GRAPH_DIR = Path("memory") / "structural_graph"
COMPAT_STRUCTURAL_GRAPH_DIR = Path("graphify-out")
STRUCTURAL_GRAPH_FILE = "graph.json"
GRAPH_VERSION = "mo-structural-graph-v2"
COMPATIBLE_GRAPH_VERSIONS = {"mo-structural-graph-v1", GRAPH_VERSION}
MAX_GRAPH_BYTES = 25 * 1024 * 1024
DEFAULT_CONTEXT_CHARS = 1400
DEFAULT_MAX_NODES = 8
SMALL_STALE_UPDATE_LIMIT = 24

_DEPENDENCY_RELATIONS = {
    "calls", "references", "imports", "imports_from", "re_exports", "inherits", "extends",
    "implements", "uses", "mixes_in", "embeds", "semantically_similar_to",
}
_IMPORT_RELATIONS = {"imports", "imports_from", "re_exports"}
_SKIP_RELATIONS_FOR_CONTEXT = {"contains", "method", "case_of"}
_STOPWORDS = DEFAULT_CONTEXT_STOPWORDS
_UPDATE_LOCK = threading.Lock()
_UPDATE_RUNNING: set[str] = set()


def structural_graph_enabled() -> bool:
    """Return True unless structural graph support is explicitly disabled."""
    raw = os.environ.get("MO_STRUCTURAL_GRAPH", "1")
    return str(raw).strip().lower() not in {"0", "false", "off", "no", "disabled"}


def auto_build_enabled() -> bool:
    """Return True unless MO-native structural graph building is disabled."""
    raw = os.environ.get("MO_STRUCTURAL_GRAPH_AUTOBUILD", "1")
    return str(raw).strip().lower() not in {"0", "false", "off", "no", "disabled"}


def project_root(cwd: str | Path | None = None) -> Path:
    """Resolve a project root using git when available."""
    path = Path(cwd or os.getcwd()).resolve()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(path), text=True, capture_output=True, timeout=3,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(proc.stdout.strip()).resolve()
    except Exception:
        traceback.print_exc()
    return path


def native_graph_path(root: str | Path | None = None) -> Path:
    root_path = project_root(root)
    if os.environ.get(ENV_MO_STATE_HOME):
        return project_cache_dir("structural_graph", root_path) / STRUCTURAL_GRAPH_FILE
    return root_path / STRUCTURAL_GRAPH_DIR / STRUCTURAL_GRAPH_FILE


def compatibility_graph_path(root: str | Path | None = None) -> Path:
    return project_root(root) / COMPAT_STRUCTURAL_GRAPH_DIR / STRUCTURAL_GRAPH_FILE


def graph_path(root: str | Path | None = None) -> Path:
    root_path = project_root(root)
    native = native_graph_path(root_path)
    if native.is_file():
        return native
    compat = compatibility_graph_path(root_path)
    if compat.is_file():
        return compat
    return native


def analysis_path(root: str | Path | None = None) -> Path:
    return graph_path(root).parent / ".structural_analysis.json"


def labels_path(root: str | Path | None = None) -> Path:
    return graph_path(root).parent / ".structural_labels.json"


def graph_exists(root: str | Path | None = None) -> bool:
    return structural_graph_enabled() and graph_path(root).is_file()


def load_graph_data(root: str | Path | None = None) -> dict[str, Any] | None:
    """Load structural graph node-link JSON if present and small enough."""
    if not structural_graph_enabled():
        return None
    path = graph_path(root)
    try:
        if not path.is_file() or path.stat().st_size > MAX_GRAPH_BYTES:
            return None
        data = _load_graph_json(path)
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        return None
    if not isinstance(_edge_list(data), list):
        return None
    return _migrate_graph(data)


def _load_graph_json(path: Path) -> dict[str, Any] | None:
    """Load graph JSON; optional ijson path is available for future node streaming."""
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_nodes(path: Path):
    """Yield graph nodes lazily when ijson is installed; otherwise fallback to JSON."""
    try:
        import ijson  # type: ignore
        with path.open("rb") as fh:
            yield from ijson.items(fh, "nodes.item")
        return
    except Exception:
        traceback.print_exc()
    data = _load_graph_json(path) or {}
    for node in data.get("nodes", []) if isinstance(data, dict) else []:
        yield node


def _migrate_graph(data: dict[str, Any]) -> dict[str, Any] | None:
    version = str(data.get("version") or "")
    if version and version not in COMPATIBLE_GRAPH_VERSIONS:
        return None
    if not version or version == "mo-structural-graph-v1":
        data = dict(data)
        for node in data.get("nodes", []):
            if isinstance(node, dict) and "file_type" not in node and "type" in node:
                node["file_type"] = node.get("type")
        data["version"] = GRAPH_VERSION
    return data


def load_analysis(root: str | Path | None = None) -> dict[str, Any]:
    try:
        path = analysis_path(root)
        if path.is_file() and path.stat().st_size <= MAX_GRAPH_BYTES:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        traceback.print_exc()
    return {}


def load_labels(root: str | Path | None = None) -> dict[int, str]:
    try:
        path = labels_path(root)
        if not path.is_file():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        return {int(k): str(v) for k, v in raw.items() if str(k).lstrip("-").isdigit()}
    except Exception:
        return {}


def graph_status(root: str | Path | None = None) -> dict[str, Any]:
    root_path = project_root(root)
    active_path = graph_path(root_path)
    data = load_graph_data(root_path)
    if not data:
        return {
            "available": False,
            "path": str(active_path),
            "native_path": str(native_graph_path(root_path)),
            "compatibility_path": str(compatibility_graph_path(root_path)),
            "source_kind": "none",
            "nodes": 0,
            "edges": 0,
        }
    built = str(data.get("built_at_commit") or "")
    head = _git_head(root_path)
    stale = bool(built and head and built != head)
    communities = {
        node.get("community") for node in data.get("nodes", [])
        if isinstance(node, dict) and node.get("community") is not None
    }
    return {
        "available": True,
        "path": str(active_path),
        "native_path": str(native_graph_path(root_path)),
        "compatibility_path": str(compatibility_graph_path(root_path)),
        "source_kind": "native" if active_path == native_graph_path(root_path) else "compatibility",
        "nodes": len(data.get("nodes", [])),
        "edges": len(_edge_list(data)),
        "communities": len(communities),
        "built_at_commit": built,
        "git_head": head,
        "stale": stale,
        "mtime": active_path.stat().st_mtime if active_path.exists() else 0.0,
    }


def load_or_build_graph_data(root: str | Path | None = None) -> dict[str, Any] | None:
    """Load the active graph, building MO's native map when allowed and missing."""
    root_path = project_root(root)
    data = load_graph_data(root_path)
    if data or not structural_graph_enabled() or not auto_build_enabled():
        return data
    result = build_structural_graph(root_path)
    if not result.get("built"):
        _emit("skipped", root=root_path, reason=str(result.get("reason") or "build_failed"))
        return None
    return load_graph_data(root_path)


def build_focused_map(
    root: str | Path | None = None,
    *,
    task_board: Any | None = None,
    query: str = "",
    output: str | Path | None = None,
) -> dict[str, Any]:
    """Write a compact task-aware HTML orientation map.

    This complements ``code_map.html`` without replacing it. The artifact is
    intentionally evidence/orientation only: source truth still comes from file
    reads, trace logs, and tests.
    """
    root_path = project_root(root)
    data = load_or_build_graph_data(root_path)
    status = graph_status(root_path)
    if not data:
        return {"built": False, "reason": "graph_unavailable", "path": str(output or root_path / STRUCTURAL_GRAPH_DIR / "focused_map.html")}

    board_summary = _focused_board_summary(task_board)
    files = _focused_files(data, board_summary, query=query)
    out_path = Path(output) if output else root_path / STRUCTURAL_GRAPH_DIR / "focused_map.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_render_focused_map(status, board_summary, files, query=query), encoding="utf-8")
    return {
        "built": True,
        "path": str(out_path),
        "nodes": int(status.get("nodes") or 0),
        "edges": int(status.get("edges") or 0),
        "tasks": len(board_summary.get("tasks") or []),
        "files": len(files),
        "bytes": out_path.stat().st_size,
    }


def build_structural_graph(root: str | Path | None = None, *, max_files: int | None = None) -> dict[str, Any]:
    """Build or incrementally refresh MO's native community code map."""
    if not structural_graph_enabled():
        return {"built": False, "reason": "disabled"}
    root_path = project_root(root)
    try:
        from . import code_graph as private_map

        files = [path for path in private_map._discover_files(root_path) if not _is_structural_graph_artifact(path)]
        if not files:
            return {"built": False, "reason": "no indexable files", "path": str(native_graph_path(root_path))}
        limit = int(max_files or getattr(private_map, "_max_files", lambda: getattr(private_map, "DEFAULT_MAX_FILES", 350))() or 350)
        if len(files) > limit:
            return {"built": False, "reason": f"project has {len(files)} indexable files; limit is {limit}", "path": str(native_graph_path(root_path)), "files": len(files)}
        fingerprints = private_map._fingerprints(root_path, files)
        graph_path_private = private_map._graph_path(root_path)
        private_graph = private_map._load_graph(graph_path_private)
        stale = private_map._stale_files(private_graph, fingerprints)
        delta_limit = int_env("MO_STRUCTURAL_GRAPH_DELTA_LIMIT", SMALL_STALE_UPDATE_LIMIT)
        status = "built"
        if private_graph and private_graph.get("version") == private_map.GRAPH_VERSION and stale and len(stale) <= delta_limit:
            private_graph = private_map._refresh_graph_delta(root_path, private_graph, files, fingerprints, stale)
            private_map._save_graph(graph_path_private, private_graph)
            status = "incremental"
        elif not private_graph or private_graph.get("version") != private_map.GRAPH_VERSION or stale:
            private_graph = private_map._build_graph(root_path, files, fingerprints)
            private_map._save_graph(graph_path_private, private_graph)
        graph = _structural_graph_from_private_map(private_graph, root_path, fingerprints)
        path = native_graph_path(root_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
        _write_native_sidecars(path, graph)
        _maybe_generate_code_map(path)
        _emit(status, root=root_path, file_count=len(files))
        return {"built": True, "status": status, "path": str(path), "nodes": len(graph.get("nodes", [])), "edges": len(_edge_list(graph)), "files": len(files), "stale_files": len(stale)}
    except Exception as exc:
        return {"built": False, "reason": f"{type(exc).__name__}: {exc}", "path": str(native_graph_path(root_path))}


def select_context(
    question: str,
    *,
    cwd: str | Path | None = None,
    max_chars: int = DEFAULT_CONTEXT_CHARS,
    max_nodes: int = DEFAULT_MAX_NODES,
    depth: int = 2,
    build_if_missing: bool = True,
    profile: Any | None = None,
) -> str:
    """Return a compact structural-graph orientation slice, or ``""``.

    The text intentionally uses the same public header as MO's legacy code map so
    provider and tests keep treating it as orientation-only support context.
    """
    root = project_root(cwd)
    data = load_or_build_graph_data(root) if build_if_missing else load_graph_data(root)
    if not data:
        _emit("skipped", root=root, reason="structural_graph_missing")
        return ""
    terms = _terms(question)
    if not terms:
        _emit("skipped", root=root, reason="no_terms")
        return ""

    nodes = _node_map(data)
    scored = _score_nodes(data, terms, profile=profile)
    if not scored:
        _emit("skipped", root=root, reason="no_relevant_nodes", file_count=len(nodes))
        return ""

    seeds = [node_id for _score, node_id in scored[:3]]
    selected_ids, selected_edges = _select_subgraph(data, seeds, scored, max_nodes=max_nodes, depth=depth)
    if not selected_ids:
        _emit("skipped", root=root, reason="empty_selection", file_count=len(nodes))
        return ""

    status = graph_status(root)
    labels = load_labels(root)
    text = _format_context(
        data, selected_ids, selected_edges, root=root, labels=labels,
        status=status, max_chars=max_chars,
    )
    selected_nodes = [nodes[nid] for nid in selected_ids if nid in nodes]
    _emit("structural_graph", root=root, file_count=len(nodes), selected=selected_nodes)
    return text


def build_structural_summary(
    query: str = "",
    *,
    cwd: str | Path | None = None,
    max_chars: int = 1200,
) -> str:
    """Return handoff/PRT-friendly structural facts from structural graph outputs."""
    root = project_root(cwd)
    data = load_or_build_graph_data(root)
    if not data:
        return ""
    analysis = load_analysis(root)
    labels = load_labels(root)
    terms = _terms(query)
    touched = [node_id for _score, node_id in _score_nodes(data, terms)[:8]] if terms else []
    node_map = _node_map(data)
    communities = _communities(data)

    community_counts: dict[int, int] = defaultdict(int)
    for nid in touched:
        cid = _as_int(node_map.get(nid, {}).get("community"))
        if cid is not None:
            community_counts[cid] += 1

    lines = [
        "### MO Structural Graph Context - orientation only",
        "Use as a navigation/risk hint only; verify with file reads/tests before claims.",
    ]
    if community_counts:
        best = sorted(community_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
        lines.append(f"- Current likely community: {_community_name(best, labels)} ({len(communities.get(best, []))} node(s)).")
    status = graph_status(root)
    if status.get("stale"):
        lines.append("- Graph may be stale vs current git HEAD; refresh the structural graph before relying on structure.")
    gods = _analysis_list(analysis, "gods")[:5]
    if gods:
        lines.append("- God nodes: " + "; ".join(
            f"{_clean(item.get('label') or item.get('id'), 60)} degree={int(item.get('degree') or 0)}"
            for item in gods[:3] if isinstance(item, dict)
        ))
    surprises = _analysis_list(analysis, "surprises")[:3]
    if surprises:
        compact = []
        for item in surprises:
            if not isinstance(item, dict):
                continue
            compact.append(f"{_clean(item.get('source'), 50)} -> {_clean(item.get('target'), 50)} [{_clean(item.get('confidence'), 20)}]")
        if compact:
            lines.append("- Surprising connections: " + "; ".join(compact[:3]))
    cycles = find_import_cycles(root=root, changed_files=None, top_n=3)
    if cycles:
        lines.append("- Import cycles detected: " + "; ".join(" -> ".join(c["cycle"][:5]) for c in cycles[:2]))
    text = "\n".join(line for line in lines if line.strip())
    return redact_monitor_text(text, max_chars)


def analyze_diff_impact(diff_text: str, root: str | Path | None = None) -> list[str]:
    """Return files that depend on files changed in a diff using structural graph data."""
    root_path = project_root(root)
    data = load_or_build_graph_data(root_path)
    if not data:
        return []
    changed = set(_changed_files_from_diff(diff_text))
    if not changed:
        return []
    nodes_by_file = _nodes_by_source_file(data)
    file_by_node = _source_file_by_node(data)
    changed_nodes = {nid for file in changed for nid in nodes_by_file.get(file, set())}
    impacted: set[str] = set()
    for edge in _edge_list(data):
        if not isinstance(edge, dict):
            continue
        relation = str(edge.get("relation") or edge.get("type") or "")
        if relation and relation not in _DEPENDENCY_RELATIONS:
            continue
        src = str(edge.get("source") or "")
        tgt = str(edge.get("target") or "")
        src_file = file_by_node.get(src) or _norm_path(edge.get("source_file"))
        tgt_file = file_by_node.get(tgt)
        if (tgt in changed_nodes or (tgt_file and tgt_file in changed)) and src_file and src_file not in changed:
            impacted.add(src_file)
    return sorted(impacted)


def affected_tests(diff_text: str, root: str | Path | None = None) -> list[str]:
    impacted = set(analyze_diff_impact(diff_text, root=root))
    impacted.update(_changed_files_from_diff(diff_text))
    return sorted(path for path in impacted if _is_test_path(path))


def prt_impact_summary(diff_text: str, root: str | Path | None = None) -> dict[str, Any]:
    """Return deterministic structural impact hints for PRT."""
    root_path = project_root(root)
    data = load_or_build_graph_data(root_path)
    changed = _changed_files_from_diff(diff_text)
    if not data:
        return {"available": False, "changed_files": changed, "impacted_files": []}

    impacted = analyze_diff_impact(diff_text, root_path)
    nodes_by_file = _nodes_by_source_file(data)
    node_map = _node_map(data)
    touched_nodes = {nid for file in set(changed + impacted) for nid in nodes_by_file.get(file, set())}
    communities = sorted({
        int(node_map[nid]["community"])
        for nid in touched_nodes
        if nid in node_map and _as_int(node_map[nid].get("community")) is not None
    })
    analysis = load_analysis(root_path)
    god_files = _god_files(data, analysis)
    god_nodes_touched = sorted({file for file in set(changed + impacted) if file in god_files})
    cross_edges = _cross_community_edges_for_files(data, set(changed + impacted))
    cycles = find_import_cycles(root=root_path, changed_files=set(changed), top_n=5)
    status = graph_status(root_path)
    return {
        "available": True,
        "changed_files": changed,
        "impacted_files": impacted,
        "affected_tests": affected_tests(diff_text, root_path),
        "communities_touched": communities,
        "community_count": len(communities),
        "cross_community_edges": cross_edges[:10],
        "cross_community_edge_count": len(cross_edges),
        "god_files_touched": god_nodes_touched,
        "import_cycles": cycles,
        "stale": bool(status.get("stale")),
        "graph_path": str(graph_path(root_path)),
    }


def format_prt_impact(summary: dict[str, Any], *, max_chars: int = 1600) -> str:
    if not summary or not summary.get("available"):
        return ""
    lines = ["### MO structural graph impact - orientation only"]
    if summary.get("stale"):
        lines.append("- Graph may be stale vs git HEAD; verify with file reads/tests.")
    lines.append(f"- Changed files: {len(summary.get('changed_files') or [])}; impacted dependents: {len(summary.get('impacted_files') or [])}.")
    if summary.get("communities_touched"):
        lines.append(f"- Communities touched: {', '.join(str(c) for c in summary['communities_touched'][:8])}.")
    if summary.get("cross_community_edge_count"):
        lines.append(f"- Cross-community coupling edges near diff: {summary['cross_community_edge_count']}.")
    if summary.get("god_files_touched"):
        lines.append("- God-node files touched: " + ", ".join(summary["god_files_touched"][:6]))
    if summary.get("import_cycles"):
        lines.append("- Import cycles: " + "; ".join(" -> ".join(item.get("cycle", [])[:5]) for item in summary["import_cycles"][:3]))
    text = "\n".join(lines)
    return redact_monitor_text(text, max_chars)


def structural_risk_score(changed_files: list[str], impacted_files: list[str], root: str | Path | None = None) -> int:
    """Return an additive risk boost based on structural graph topology."""
    data = load_or_build_graph_data(project_root(root))
    if not data:
        return 0
    files = {_norm_path(f) for f in list(changed_files or []) + list(impacted_files or []) if _norm_path(f)}
    if not files:
        return 0
    nodes_by_file = _nodes_by_source_file(data)
    node_map = _node_map(data)
    communities = {
        int(node_map[nid]["community"])
        for file in files for nid in nodes_by_file.get(file, set())
        if nid in node_map and _as_int(node_map[nid].get("community")) is not None
    }
    boost = 0
    if len(communities) >= 2:
        boost += 4
    if len(communities) >= 4:
        boost += 4
    if _cross_community_edges_for_files(data, files):
        boost += 3
    if files & _god_files(data, load_analysis(root)):
        boost += 5
    return boost


def structural_patterns(root: str | Path | None = None, *, max_items: int = 10) -> list[dict[str, Any]]:
    """Return inert structural learning candidates from an existing graph."""
    root_path = project_root(root)
    data = load_or_build_graph_data(root_path)
    if not data:
        return []
    analysis = load_analysis(root_path)
    patterns: list[dict[str, Any]] = []
    for item in _analysis_list(analysis, "gods")[:4]:
        if not isinstance(item, dict):
            continue
        label = _clean(item.get("label") or item.get("id"), 90)
        degree = int(item.get("degree") or 0)
        patterns.append({
            "kind": "god_node",
            "trigger": f"changes near high-connectivity module `{label}`",
            "behavior": f"read `{label}` and its direct dependents first; it is a graph god node with degree {degree}",
            "scope": "review/fix/refactor turns touching this project graph",
            "evidence": f"structural graph god_nodes degree={degree}",
        })
    for item in _analysis_list(analysis, "surprises")[:4]:
        if not isinstance(item, dict):
            continue
        source = _clean(item.get("source"), 70)
        target = _clean(item.get("target"), 70)
        if not source or not target:
            continue
        patterns.append({
            "kind": "surprising_connection",
            "trigger": f"work crossing `{source}` and `{target}`",
            "behavior": "verify the non-obvious graph relationship with file reads before assuming subsystem boundaries",
            "scope": "investigation/review turns involving the connected files or concepts",
            "evidence": f"structural graph surprising_connection confidence={_clean(item.get('confidence'), 30)}",
        })
    for item in find_import_cycles(root=root_path, changed_files=None, top_n=3):
        cycle = " -> ".join(item.get("cycle", []))
        patterns.append({
            "kind": "import_cycle",
            "trigger": f"changes in circular dependency `{cycle}`",
            "behavior": "treat edits as higher risk and verify imports/smoke tests before completion claims",
            "scope": "fix/refactor/review turns touching files in the cycle",
            "evidence": "structural graph import cycle detection",
        })
    return patterns[:max_items]


def find_import_cycles(
    *,
    root: str | Path | None = None,
    changed_files: set[str] | None = None,
    max_cycle_length: int = 5,
    top_n: int = 20,
) -> list[dict[str, Any]]:
    data = load_or_build_graph_data(project_root(root))
    if not data:
        return []
    file_by_node = _source_file_by_node(data)
    graph: dict[str, set[str]] = defaultdict(set)
    for edge in _edge_list(data):
        if not isinstance(edge, dict):
            continue
        relation = str(edge.get("relation") or edge.get("type") or "")
        if relation not in _IMPORT_RELATIONS:
            continue
        src = file_by_node.get(str(edge.get("source") or "")) or _norm_path(edge.get("source_file"))
        tgt = file_by_node.get(str(edge.get("target") or ""))
        if src and tgt and src != tgt:
            graph[src].add(tgt)
    if not graph:
        return []

    cycles: set[tuple[str, ...]] = set()

    def visit(start: str, current: str, path: list[str]) -> None:
        if len(path) > max_cycle_length:
            return
        for nxt in sorted(graph.get(current, set())):
            if nxt == start and len(path) > 1:
                cycles.add(_normalize_cycle(path))
            elif nxt not in path:
                visit(start, nxt, path + [nxt])

    for start in sorted(graph):
        visit(start, start, [start])
        if len(cycles) >= top_n * 4:
            break

    changed_norm = {_norm_path(f) for f in (changed_files or set()) if _norm_path(f)}
    result: list[dict[str, Any]] = []
    for cycle in sorted(cycles, key=lambda c: (len(c), c)):
        if changed_norm and not (set(cycle) & changed_norm):
            continue
        result.append({"cycle": list(cycle), "length": len(cycle), "why": "circular dependency"})
        if len(result) >= top_n:
            break
    return result


def maybe_update_graph_async(
    *,
    root: str | Path | None = None,
    profile: Any | None = None,
    reason: str = "",
    timeout: int = 180,
) -> bool:
    """Best-effort background structural graph refresh after commits/turns.

    Returns True when a worker was started. It is a no-op when structural graph
    support is disabled or ``MO_STRUCTURAL_GRAPH_AUTO_UPDATE=0``. Without a
    configured refresh command, MO rebuilds its native community code map.
    A refresh command can be supplied with ``MO_STRUCTURAL_GRAPH_UPDATE_CMD``;
    ``{root}`` placeholders are replaced with the project root.
    """
    if str(os.environ.get("MO_STRUCTURAL_GRAPH_AUTO_UPDATE", "1")).strip().lower() in {"0", "false", "off", "no"}:
        return False
    root_path = project_root(root)
    if not graph_exists(root_path) and not auto_build_enabled():
        return False
    command = _refresh_command(root_path)
    key = str(root_path)
    with _UPDATE_LOCK:
        if key in _UPDATE_RUNNING:
            return False
        _UPDATE_RUNNING.add(key)

    def worker() -> None:
        try:
            _emit("structural_graph_update_started", root=root_path, reason=reason)
            if command:
                proc = subprocess.run(
                    command,
                    cwd=str(root_path), text=True, capture_output=True, timeout=timeout,
                )
                ok = proc.returncode == 0
                detail = (proc.stderr or proc.stdout or reason)[:500]
            else:
                result = build_structural_graph(root_path)
                ok = bool(result.get("built"))
                detail = str(result.get("reason") or result.get("path") or reason)[:500]
            status = "structural_graph_update_ok" if ok else "structural_graph_update_failed"
            _emit(status, root=root_path, reason=detail)
            if ok:
                try:
                    from ..learning.workflow_learning import stage_structural_graph_candidates
                    stage_structural_graph_candidates(profile, root=root_path)
                except Exception:
                    traceback.print_exc()
        except Exception as exc:
            _emit("structural_graph_update_failed", root=root_path, reason=f"{type(exc).__name__}: {exc}")
        finally:
            with _UPDATE_LOCK:
                _UPDATE_RUNNING.discard(key)

    threading.Thread(target=worker, name="mo-structural-graph-update", daemon=True).start()
    return True


def _refresh_command(root_path: Path) -> list[str]:
    raw = str(os.environ.get("MO_STRUCTURAL_GRAPH_UPDATE_CMD", "") or "").strip()
    if not raw:
        return []
    try:
        parts = shlex.split(raw)
    except ValueError:
        return []
    return [part.replace("{root}", str(root_path)) for part in parts]


def _is_structural_graph_artifact(path: str) -> bool:
    normalized = _norm_path(path)
    return normalized.startswith("graphify-out/") or normalized.startswith("memory/structural_graph/")


def _structural_graph_from_private_map(private_graph: dict[str, Any], root: Path, fingerprints: dict[str, str]) -> dict[str, Any]:
    private_nodes = [node for node in private_graph.get("nodes", []) if isinstance(node, dict)]
    private_edges = [edge for edge in private_graph.get("edges", []) if isinstance(edge, dict)]
    source_by_node = {
        str(node.get("id") or ""): _norm_path(node.get("filePath"))
        for node in private_nodes
        if str(node.get("id") or "")
    }
    community_by_key: dict[str, int] = {}

    def community_for(source: str) -> int:
        key = _community_key(source)
        if key not in community_by_key:
            community_by_key[key] = len(community_by_key) + 1
        return community_by_key[key]

    nodes: list[dict[str, Any]] = []
    for node in private_nodes:
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        source = _norm_path(node.get("filePath"))
        line_range = node.get("lineRange") if isinstance(node.get("lineRange"), list) else []
        location = ""
        if len(line_range) >= 2:
            location = f"L{int(line_range[0])}-L{int(line_range[1])}"
        elif len(line_range) == 1:
            location = f"L{int(line_range[0])}"
        label = _clean(node.get("name") or source or node_id, 120)
        typ = _clean(node.get("type") or "node", 40)
        nodes.append({
            "id": node_id,
            "label": label,
            "type": typ,
            "file_type": typ,
            "source_file": source,
            "source_location": location,
            "summary": _clean(node.get("summary"), 260),
            "community": community_for(source),
        })

    links: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    node_ids = {str(node.get("id") or "") for node in nodes}
    for edge in private_edges:
        src = str(edge.get("source") or "")
        tgt = str(edge.get("target") or "")
        relation = str(edge.get("relation") or edge.get("type") or "related")
        key = (src, tgt, relation)
        if not src or not tgt or src not in node_ids or tgt not in node_ids or key in seen:
            continue
        seen.add(key)
        links.append({
            "source": src,
            "target": tgt,
            "relation": relation,
            "confidence": "MO_LOCAL",
            "source_file": source_by_node.get(src, ""),
        })

    return {
        "directed": True,
        "kind": "mo-structural-code-map",
        "version": GRAPH_VERSION,
        "built_by": "mo",
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "built_at_commit": _git_head(root),
        "project": {"root": str(root), "name": root.name},
        "fingerprints": fingerprints,
        "nodes": nodes,
        "links": links,
    }


def _community_key(source: str) -> str:
    path = _norm_path(source)
    if not path:
        return "root"
    strategy = os.environ.get("MO_STRUCTURAL_COMMUNITY_STRATEGY", "path").strip().lower() or "path"
    if strategy == "path_depth2":
        parts = path.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
    if strategy == "module":
        return path.replace("/", ".").rsplit(".", 1)[0] if "." in path else path
    first = path.split("/", 1)[0]
    return first or "root"


def _maybe_generate_code_map(path: Path) -> None:
    raw = os.environ.get("MO_CODE_MAP_AUTOGEN", "1").strip().lower()
    if raw in {"0", "false", "off", "no", "disabled"}:
        return
    try:
        from .generate_code_map import generate_code_map
        generate_code_map(path)
    except Exception:
        return


def _write_native_sidecars(path: Path, graph: dict[str, Any]) -> None:
    try:
        labels: dict[int, str] = {}
        for node in graph.get("nodes", []):
            if not isinstance(node, dict):
                continue
            cid = _as_int(node.get("community"))
            source = _norm_path(node.get("source_file"))
            if cid is not None and cid not in labels:
                labels[cid] = _community_key(source).replace("_", " ").title()
        (path.parent / ".structural_labels.json").write_text(
            json.dumps({str(k): v for k, v in sorted(labels.items())}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        analysis = _native_analysis(graph)
        (path.parent / ".structural_analysis.json").write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        traceback.print_exc()


def _native_analysis(graph: dict[str, Any]) -> dict[str, Any]:
    node_map = _node_map(graph)
    degree: dict[str, int] = defaultdict(int)
    surprises: list[dict[str, Any]] = []
    for edge in _edge_list(graph):
        if not isinstance(edge, dict):
            continue
        src = str(edge.get("source") or "")
        tgt = str(edge.get("target") or "")
        relation = str(edge.get("relation") or edge.get("type") or "")
        if relation not in _SKIP_RELATIONS_FOR_CONTEXT:
            degree[src] += 1
            degree[tgt] += 1
        src_c = _as_int(node_map.get(src, {}).get("community"))
        tgt_c = _as_int(node_map.get(tgt, {}).get("community"))
        if src_c is not None and tgt_c is not None and src_c != tgt_c and relation not in _SKIP_RELATIONS_FOR_CONTEXT:
            surprises.append({
                "source": node_map.get(src, {}).get("label") or src,
                "target": node_map.get(tgt, {}).get("label") or tgt,
                "confidence": edge.get("confidence") or "MO_LOCAL",
            })
    gods = [
        {"id": node_id, "label": node_map.get(node_id, {}).get("label") or node_id, "degree": count}
        for node_id, count in sorted(degree.items(), key=lambda item: (-item[1], item[0]))[:8]
        if count >= 2
    ]
    # Community summary
    communities = _communities(graph)
    community_list = sorted(
        [{"id": cid, "size": len(nodes)} for cid, nodes in communities.items()],
        key=lambda c: -c["size"],
    )
    return {"gods": gods, "surprises": surprises[:10], "communities": community_list}


def _edge_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    links = data.get("links") if isinstance(data.get("links"), list) else data.get("edges")
    return links if isinstance(links, list) else []


def _node_map(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(node.get("id")): node
        for node in data.get("nodes", [])
        if isinstance(node, dict) and str(node.get("id") or "")
    }


def _focused_board_summary(task_board: Any | None) -> dict[str, Any]:
    if task_board is None:
        return {"total": 0, "done": 0, "open": 0, "tasks": []}
    try:
        if hasattr(task_board, "summary"):
            summary = task_board.summary()
        elif isinstance(task_board, dict):
            summary = dict(task_board)
        else:
            summary = {}
    except Exception:
        summary = {}
    tasks = summary.get("tasks") if isinstance(summary.get("tasks"), list) else []
    return {
        "title": _clean(summary.get("title") or "Task board", 160),
        "total": int(summary.get("total") or len(tasks)),
        "done": int(summary.get("done") or 0),
        "open": int(summary.get("open") or 0),
        "active_task_id": _clean(summary.get("active_task_id") or "", 40),
        "tasks": [task for task in tasks if isinstance(task, dict)][:12],
    }


def _focused_files(data: dict[str, Any], board_summary: dict[str, Any], *, query: str = "") -> list[dict[str, Any]]:
    scores: dict[str, int] = defaultdict(int)
    for node in data.get("nodes", []):
        if not isinstance(node, dict):
            continue
        source = _clean(node.get("source_file") or "", 220)
        if source:
            scores[source] += 1
    for task in board_summary.get("tasks") or []:
        for evidence in task.get("evidence") or []:
            for source in _evidence_paths(str(evidence)):
                scores[source] += 20
    for term in _terms(query):
        for source in list(scores):
            if term in source.lower():
                scores[source] += 5
    return [
        {"path": path, "score": score}
        for path, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:24]
    ]


def _evidence_paths(text: str) -> list[str]:
    paths: list[str] = []
    for match in re.findall(r"([A-Za-z0-9_.:/\\-]+\.(?:py|md|json|jsonl|toml|yaml|yml|txt|html|css|js|ts))", text):
        normalized = match.replace("\\", "/").strip()
        if ":" in normalized and not re.match(r"^[A-Za-z]:/", normalized):
            normalized = normalized.rsplit(":", 1)[-1]
        if normalized and normalized not in paths:
            paths.append(normalized)
    return paths


def _render_focused_map(
    status: dict[str, Any],
    board_summary: dict[str, Any],
    files: list[dict[str, Any]],
    *,
    query: str = "",
) -> str:
    task_rows = []
    for task in board_summary.get("tasks") or []:
        evidence = ", ".join(_clean(item, 120) for item in (task.get("evidence") or [])[:3])
        task_rows.append(
            "<tr>"
            f"<td>{html.escape(_clean(task.get('id'), 30))}</td>"
            f"<td>{html.escape(_clean(task.get('status'), 30))}</td>"
            f"<td>{html.escape(_clean(task.get('title'), 180))}</td>"
            f"<td>{html.escape(evidence)}</td>"
            "</tr>"
        )
    file_rows = [
        f"<tr><td>{html.escape(_clean(item.get('path'), 220))}</td><td>{int(item.get('score') or 0)}</td></tr>"
        for item in files
    ]
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>MO Focused Map</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 24px; color: #1f2937; background: #f8fafc; }}
    h1, h2 {{ margin: 0 0 12px; }}
    section {{ margin: 0 0 24px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; }}
    th, td {{ border: 1px solid #d1d5db; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #e5e7eb; }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }}
    .pill {{ background: white; border: 1px solid #d1d5db; padding: 6px 10px; }}
    .note {{ color: #4b5563; }}
  </style>
</head>
<body>
  <h1>MO Focused Map</h1>
  <p class="note">Orientation only. Verify claims with source reads, traces, and tests.</p>
  <section class="meta">
    <div class="pill">Graph: {available}</div>
    <div class="pill">Nodes: {nodes}</div>
    <div class="pill">Edges: {edges}</div>
    <div class="pill">Stale: {stale}</div>
    <div class="pill">Tasks: {done}/{total} done</div>
    <div class="pill">Query: {query}</div>
  </section>
  <section>
    <h2>Task Board</h2>
    <table><thead><tr><th>ID</th><th>Status</th><th>Task</th><th>Evidence</th></tr></thead><tbody>{tasks}</tbody></table>
  </section>
  <section>
    <h2>Focused Files</h2>
    <table><thead><tr><th>Path</th><th>Score</th></tr></thead><tbody>{files}</tbody></table>
  </section>
</body>
</html>
""".format(
        available=html.escape(str(bool(status.get("available")))),
        nodes=int(status.get("nodes") or 0),
        edges=int(status.get("edges") or 0),
        stale=html.escape(str(bool(status.get("stale")))),
        done=int(board_summary.get("done") or 0),
        total=int(board_summary.get("total") or 0),
        query=html.escape(_clean(query, 120)),
        tasks="\n".join(task_rows) or "<tr><td colspan=\"4\">No active taskboard supplied</td></tr>",
        files="\n".join(file_rows) or "<tr><td colspan=\"2\">No focused files found</td></tr>",
    )


def _terms(text: str) -> list[str]:
    raw = re.findall(r"[a-zA-Z0-9_./-]{2,}", str(text or "").lower())
    terms: list[str] = []
    for item in raw:
        pieces = [item]
        pieces.extend(re.split(r"[/_.-]+", item))
        for piece in pieces:
            if len(piece) < 2 or piece in _STOPWORDS:
                continue
            for variant in _term_variants(piece):
                if variant not in _STOPWORDS and variant not in terms:
                    terms.append(variant)
    return terms[:48]


def _term_variants(term: str) -> list[str]:
    value = str(term or "").lower().strip()
    if not value:
        return []
    variants = [value]
    if value.endswith("ies") and len(value) > 4:
        variants.append(value[:-3] + "y")
    if value.endswith("s") and len(value) > 3:
        variants.append(value[:-1])
    elif len(value) > 2:
        variants.append(value + "s")
    if value.endswith("ing") and len(value) > 5:
        variants.append(value[:-3])
    if value.endswith("ment") and len(value) > 6:
        variants.append(value[:-4])
    return list(dict.fromkeys(v for v in variants if len(v) >= 2))


def _score_nodes(data: dict[str, Any], terms: list[str], profile: Any | None = None) -> list[tuple[float, str]]:
    scored: list[tuple[float, str]] = []
    for node_id, node in _node_map(data).items():
        label = str(node.get("label") or node.get("name") or node_id).lower()
        bare = label.rstrip("()")
        source = str(node.get("source_file") or "").lower().replace("\\", "/")
        hay = " ".join(str(node.get(key, "")) for key in ("id", "label", "file_type", "source_file", "source_location", "community")).lower()
        score = 0.0
        for term in terms:
            for variant in _term_variants(term):
                if variant == bare or variant == label or variant == node_id.lower():
                    score += 100.0
                    break
                if bare.startswith(variant):
                    score += 25.0
                    break
                if variant in label:
                    score += 8.0
                    break
                if variant in source:
                    score += 5.0
                    break
                if variant in hay:
                    score += 1.0
                    break
        score += _personalized_boost(source, profile)
        if source.startswith("tests/") and not ({"test", "tests", "pytest", "verify"} & set(terms)):
            score *= 0.55
        if score > 0:
            scored.append((score, node_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored


def _personalized_boost(source: str, profile: Any | None = None) -> float:
    paths = [str(path).lower().replace("\\", "/").strip("/") for path in getattr(profile, "important_paths", []) or []]
    for path in paths:
        if path and (source == path or source.startswith(path + "/")):
            return 3.0
    active = None
    try:
        active = profile.active_project() if profile else None
    except Exception:
        active = None
    active_path = str(getattr(active, "path", "") or "").lower().replace("\\", "/").strip("/")
    if active_path and source.startswith(active_path):
        return 1.5
    return 1.5 if source.startswith(("core/", "interface/", "tools/")) else 0.0


def _select_subgraph(
    data: dict[str, Any],
    seeds: list[str],
    scored: list[tuple[float, str]],
    *,
    max_nodes: int,
    depth: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    node_map = _node_map(data)
    adjacency: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for edge in _edge_list(data):
        if not isinstance(edge, dict):
            continue
        src = str(edge.get("source") or "")
        tgt = str(edge.get("target") or "")
        if not src or not tgt or src not in node_map or tgt not in node_map:
            continue
        adjacency[src].append((tgt, edge))
        adjacency[tgt].append((src, edge))

    selected: list[str] = []
    seen: set[str] = set()

    def add(nid: str) -> None:
        if nid in node_map and nid not in seen and len(selected) < max_nodes:
            seen.add(nid)
            selected.append(nid)

    for seed in seeds:
        add(seed)

    top_community = _as_int(node_map.get(seeds[0], {}).get("community")) if seeds else None
    queue: deque[tuple[str, int]] = deque((seed, 0) for seed in seeds if seed in node_map)
    selected_edges: list[dict[str, Any]] = []
    edge_seen: set[tuple[str, str, str]] = set()
    while queue and len(selected) < max_nodes:
        current, dist = queue.popleft()
        if dist >= depth:
            continue
        neighbours = sorted(
            adjacency.get(current, []),
            key=lambda item: (
                _as_int(node_map.get(item[0], {}).get("community")) != top_community,
                item[1].get("relation") in _SKIP_RELATIONS_FOR_CONTEXT,
                item[0],
            ),
        )
        for neighbour, edge in neighbours:
            relation = str(edge.get("relation") or edge.get("type") or "")
            key = (str(edge.get("source") or ""), str(edge.get("target") or ""), relation)
            if relation not in _SKIP_RELATIONS_FOR_CONTEXT and key not in edge_seen:
                selected_edges.append(edge)
                edge_seen.add(key)
            if neighbour not in seen:
                add(neighbour)
                queue.append((neighbour, dist + 1))
            if len(selected) >= max_nodes:
                break

    for _score, nid in scored:
        if len(selected) >= max_nodes:
            break
        if top_community is None or _as_int(node_map.get(nid, {}).get("community")) == top_community:
            add(nid)
    for _score, nid in scored:
        if len(selected) >= max_nodes:
            break
        add(nid)

    selected_set = set(selected)
    for edge in _edge_list(data):
        if len(selected_edges) >= 10:
            break
        if not isinstance(edge, dict):
            continue
        src = str(edge.get("source") or "")
        tgt = str(edge.get("target") or "")
        relation = str(edge.get("relation") or edge.get("type") or "")
        key = (src, tgt, relation)
        if src in selected_set and tgt in selected_set and relation not in _SKIP_RELATIONS_FOR_CONTEXT and key not in edge_seen:
            selected_edges.append(edge)
            edge_seen.add(key)
    return selected, selected_edges[:10]


def _format_context(
    data: dict[str, Any],
    selected_ids: list[str],
    selected_edges: list[dict[str, Any]],
    *,
    root: Path,
    labels: dict[int, str],
    status: dict[str, Any],
    max_chars: int,
) -> str:
    node_map = _node_map(data)
    lines = [
        "### MO Internal Code Map - orientation only",
        f"Source: structural graph; project: {root.name}; selected {len(selected_ids)} of {len(node_map)} nodes; communities {status.get('communities', 0)}.",
        f"[Code map: {len(selected_ids)}/{len(node_map)} nodes, {status.get('communities', 0)} comms]",
        "Use this to choose likely files only. It is not proof; read files and verify before editing or claiming completion.",
    ]
    if status.get("stale"):
        lines.append("Graph freshness: may be stale vs current git HEAD.")
    for node_id in selected_ids:
        node = node_map.get(node_id, {})
        label = _clean(node.get("label") or node.get("name") or node_id, 90)
        typ = _clean(node.get("file_type") or node.get("type") or "node", 30)
        source = _norm_path(node.get("source_file"))
        loc = _clean(node.get("source_location") or "", 30)
        cid = _as_int(node.get("community"))
        comm = f" community={_community_name(cid, labels)}" if cid is not None else ""
        src_text = f" `{source}{(':' + loc) if loc and not str(loc).startswith('L') else (':' + loc if loc else '')}`" if source else ""
        lines.append(f"- {typ}: {label}{src_text}{comm}")
    if selected_edges:
        lines.append("Relevant relationships:")
        for edge in selected_edges[:8]:
            src = str(edge.get("source") or "")
            tgt = str(edge.get("target") or "")
            relation = _clean(edge.get("relation") or edge.get("type") or "related", 50)
            conf = _clean(edge.get("confidence") or "", 20)
            src_label = _clean(node_map.get(src, {}).get("label") or src, 55)
            tgt_label = _clean(node_map.get(tgt, {}).get("label") or tgt, 55)
            suffix = f" [{conf}]" if conf else ""
            lines.append(f"- {src_label} --{relation}{suffix}--> {tgt_label}")
    text = redact_monitor_text("\n".join(lines), max_chars)
    digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:8]
    text += f"\nMap slice id: structural-{digest}"
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def _changed_files_from_diff(diff_text: str) -> list[str]:
    files: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"^diff --git a/(.+?) b/(.+)$", str(diff_text or ""), flags=re.MULTILINE):
        path = _norm_path(match.group(2))
        if path and path not in seen:
            seen.add(path)
            files.append(path)
    return files


def _nodes_by_source_file(data: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for node_id, node in _node_map(data).items():
        source = _norm_path(node.get("source_file"))
        if source:
            result[source].add(node_id)
    return result


def _source_file_by_node(data: dict[str, Any]) -> dict[str, str]:
    return {
        node_id: source for node_id, node in _node_map(data).items()
        if (source := _norm_path(node.get("source_file")))
    }


def _communities(data: dict[str, Any]) -> dict[int, list[str]]:
    result: dict[int, list[str]] = defaultdict(list)
    for node_id, node in _node_map(data).items():
        cid = _as_int(node.get("community"))
        if cid is not None:
            result[cid].append(node_id)
    return result


def _cross_community_edges_for_files(data: dict[str, Any], files: set[str]) -> list[dict[str, Any]]:
    node_map = _node_map(data)
    file_by_node = _source_file_by_node(data)
    result: list[dict[str, Any]] = []
    for edge in _edge_list(data):
        if not isinstance(edge, dict):
            continue
        relation = str(edge.get("relation") or edge.get("type") or "")
        if relation in _SKIP_RELATIONS_FOR_CONTEXT:
            continue
        src = str(edge.get("source") or "")
        tgt = str(edge.get("target") or "")
        src_file = file_by_node.get(src)
        tgt_file = file_by_node.get(tgt)
        if src_file not in files and tgt_file not in files:
            continue
        src_c = _as_int(node_map.get(src, {}).get("community"))
        tgt_c = _as_int(node_map.get(tgt, {}).get("community"))
        if src_c is not None and tgt_c is not None and src_c != tgt_c:
            result.append({
                "source_file": src_file,
                "target_file": tgt_file,
                "relation": relation,
                "source_community": src_c,
                "target_community": tgt_c,
                "confidence": edge.get("confidence", ""),
            })
    return result


def _god_files(data: dict[str, Any], analysis: dict[str, Any]) -> set[str]:
    node_map = _node_map(data)
    files: set[str] = set()
    for item in _analysis_list(analysis, "gods"):
        if not isinstance(item, dict):
            continue
        node = node_map.get(str(item.get("id") or ""))
        source = _norm_path(node.get("source_file")) if node else ""
        if source:
            files.add(source)
    return files


def _analysis_list(analysis: dict[str, Any], key: str) -> list[Any]:
    value = analysis.get(key)
    return value if isinstance(value, list) else []


def _normalize_cycle(cycle: list[str]) -> tuple[str, ...]:
    if not cycle:
        return tuple()
    best = min(range(len(cycle)), key=lambda idx: cycle[idx])
    return tuple(cycle[best:] + cycle[:best])


def _norm_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def _is_test_path(path: str) -> bool:
    low = _norm_path(path).lower()
    return bool(
        low.startswith(("test/", "tests/"))
        or re.search(r"(^|/)(test_[^/]*|[^/]*_test)\.py$", low)
        or low.endswith((".test.js", ".test.ts", ".test.tsx", ".spec.js", ".spec.ts", ".spec.tsx"))
    )


def _community_name(cid: int | None, labels: dict[int, str]) -> str:
    if cid is None:
        return "unknown"
    return _clean(labels.get(cid) or f"Community {cid}", 80)


def _clean(value: Any, limit: int = 120) -> str:
    text = str(value or "").replace("\n", " ")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return redact_monitor_text(text, limit).strip()


def _git_head(root: Path) -> str:
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root), text=True, capture_output=True, timeout=3)
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        traceback.print_exc()
    return ""


def _emit(
    status: str,
    *,
    root: Path | None = None,
    reason: str = "",
    file_count: int = 0,
    selected: list[dict[str, Any]] | None = None,
) -> None:
    try:
        monitor = get_monitor()
        if not monitor:
            return
        selected = selected or []
        monitor.emit("code_graph_context", {
            "status": redact_monitor_text(status, 80),
            "reason": redact_monitor_text(reason, 160),
            "project": redact_monitor_text(root.name if root else "", 80),
            "source": "structural_graph",
            "file_count": int(file_count or 0),
            "selected_count": len(selected),
            "node_ids": [redact_monitor_text(str(node.get("id") or ""), 160) for node in selected[:10]],
        })
    except Exception:
        traceback.print_exc()
