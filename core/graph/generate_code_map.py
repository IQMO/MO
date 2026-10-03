"""Generate a self-contained HTML visualization for MO's structural graph.

The generator is deterministic, dependency-free, and writes only artifacts next
to the active structural graph cache by default:

- ``code_map.html``: MO Agent unified map — a skin-themed interactive 3D code
  graph clustered by package, overlaid with work context (taskboards, recent
  commits, file ops, goal/worker touched files) plus a counts-only dashboard
  brain layer for Memory / Profile / Learning / Tasks.
- ``task_annotations.json``: best-effort goal/worker -> touched files mapping
- ``.layout_cache.json``: graph fingerprint -> stable positions

All overlays are best-effort orientation, never proof; live files/tests win.
The browser starts with package cubes aggregated from those same relationships,
then drills into files/symbols through one source/work navigation path. The
adjacent HTML/JS are embedded into this self-contained artifact; colours still
come from the shared skin. Redraws are interaction-driven or finite navigation
transitions unless drift is explicitly enabled; reduced-motion disables both.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from ..utils.atomic_write import atomic_write_json, atomic_write_text
from ..runtime.subprocess_flags import apply_windows_hidden_process_flags
from ..tooling.tool_constants import FILE_MUTATION_TOOLS

HTML_NAME = "code_map.html"
ANNOTATIONS_NAME = "task_annotations.json"
LAYOUT_CACHE_NAME = ".layout_cache.json"
LAYOUT_VERSION = "package_clusters_v3"
MAX_BOARDS = 8
MAX_COMMITS = 12


def generate_code_map(
    path: str | Path | None = None,
    *,
    output: str | Path | None = None,
    iterations: int = 50,
) -> dict[str, Any]:
    """Generate the unified map HTML and task annotations from a structural graph."""
    from ..state.paths import resolve_state_path
    graph_path = Path(path) if path is not None else _default_graph_path()
    data = _load_graph(graph_path)
    nodes, links, degrees = _normalize_graph(data)
    out_path = Path(output) if output else graph_path.parent / HTML_NAME
    annotations_path = out_path.parent / ANNOTATIONS_NAME
    cache_path = out_path.parent / LAYOUT_CACHE_NAME
    root = _project_root_from_graph(data, graph_path)
    annotations = build_task_annotations(
        goal_dir=Path(resolve_state_path("memory/work/goals")),
        tool_audit=Path(resolve_state_path("logs/tool_audit.jsonl")),
        root=root,
    )
    positions, cache_hit = _layout_with_cache(nodes, links, data, cache_path, iterations=iterations)
    payload = _payload(nodes, links, degrees, positions, annotations, root=root)
    _merge_brain_index(payload)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(annotations_path, annotations, indent=2, sort_keys=True)
    atomic_write_text(out_path, _render_html(payload), encoding="utf-8")
    return {
        "generated": True,
        "path": str(out_path),
        "annotations_path": str(annotations_path),
        "cache_hit": cache_hit,
        "nodes": len(nodes),
        "links": len(links),
        "bytes": out_path.stat().st_size,
    }


def _default_graph_path() -> Path:
    """Return the active structural graph path for the current project."""
    from ..state.paths import project_cwd
    from .structural_graph import graph_path

    # graph_path() already owns private-vs-project-local resolution and its
    # documented external-input lookup. Falling back here to a bare obsolete memory path can
    # silently read or recreate the wrong graph store.
    return graph_path(project_cwd())


def _project_root_from_graph(data: dict[str, Any], graph_path: Path) -> Path:
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    root = str((project or {}).get("root") or "").strip()
    if root:
        return Path(root).expanduser().resolve(strict=False)
    resolved = graph_path.resolve(strict=False)
    parent = resolved.parent
    if parent.name == "structural_graph" and parent.parent.name == "memory":
        return parent.parent.parent
    if parent.name == "graphify-out":
        return parent.parent
    from ..state.paths import project_cwd

    return project_cwd()


def build_task_annotations(
    *,
    goal_dir: str | Path | None = None,
    tool_audit: str | Path | None = None,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """Return best-effort goal/worker touched-file annotations."""
    from ..state.paths import resolve_state_path, project_cwd

    goal_path = Path(goal_dir) if goal_dir is not None else Path(resolve_state_path("memory/work/goals"))
    audit_path = Path(tool_audit) if tool_audit is not None else Path(resolve_state_path("logs/tool_audit.jsonl"))
    root_path = Path(root).resolve() if root is not None else project_cwd()
    mapping: dict[str, set[str]] = {}
    windows = _goal_windows(goal_path, mapping)
    _scan_tool_audit(audit_path, mapping, windows, root_path)
    return {
        "best_effort": True,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "tasks": {key: sorted(values) for key, values in sorted(mapping.items()) if values},
    }


def _load_graph(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        raise ValueError(f"invalid structural graph: {path}")
    return data


def _node_group(source_file: str) -> str:
    """Categorize a node by package: two levels under core/, one elsewhere."""
    source = str(source_file or "").replace("\\", "/").strip("/")
    if not source:
        return "root"
    parts = source.split("/")
    if len(parts) == 1:
        return "root"
    if parts[0] == "core" and len(parts) > 2:
        return f"core/{parts[1]}"
    return parts[0]


def _normalize_graph(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    raw_nodes = [node for node in data.get("nodes", []) if isinstance(node, dict)]
    raw_links = [edge for edge in (data.get("links") or data.get("edges") or []) if isinstance(edge, dict)]
    edge_defaults = data.get("edge_defaults")
    edge_defaults = edge_defaults if isinstance(edge_defaults, dict) else {}
    ids = {str(node.get("id") or "") for node in raw_nodes}
    degrees = {node_id: 0 for node_id in ids if node_id}
    links: list[dict[str, Any]] = []
    for edge in raw_links:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or edge.get("target_id") or "")
        if source not in ids or target not in ids:
            continue
        degrees[source] += 1
        degrees[target] += 1
        link = {
            "source": source,
            "target": target,
            "relation": str(edge.get("relation") or edge.get("type") or "related"),
            "confidence": _edge_confidence_label(edge),
        }
        provenance = str(
            edge.get("provenance") or edge_defaults.get("provenance") or ""
        ).strip()
        if provenance:
            link["provenance"] = provenance
        links.append(link)
    nodes = []
    for node in raw_nodes:
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        source_file = str(node.get("source_file") or node.get("filePath") or "")
        nodes.append({
            "id": node_id,
            "label": str(node.get("label") or node.get("name") or node_id),
            "type": str(node.get("type") or node.get("file_type") or "node"),
            "community": int(node.get("community") or 0),
            "source_file": source_file,
            "group": _node_group(source_file),
            "degree": int(degrees.get(node_id, 0)),
        })
    return nodes, links, degrees


def _edge_confidence_label(edge: dict[str, Any]) -> str:
    """Return the normalized structural edge confidence without importing at module load."""
    try:
        from .structural_graph import _edge_confidence

        return _edge_confidence(edge)
    except Exception:
        raw = str(edge.get("confidence") or "").strip().upper()
        if raw in {"EXTRACTED", "INFERRED", "AMBIGUOUS"}:
            return raw
        return "AMBIGUOUS"


def _graph_insights(nodes: list[dict[str, Any]], links: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {str(node.get("id") or ""): node for node in nodes}
    confidence: dict[str, int] = {"AMBIGUOUS": 0, "EXTRACTED": 0, "INFERRED": 0}
    provenance: dict[str, int] = {}
    for edge in links:
        label = str(edge.get("confidence") or "").strip().upper()
        if label:
            confidence[label] = confidence.get(label, 0) + 1
        source = str(edge.get("provenance") or "").strip()
        if source:
            provenance[source] = provenance.get(source, 0) + 1

    hubs = [
        {
            "id": str(node.get("id") or ""),
            "label": str(node.get("label") or node.get("id") or ""),
            "type": str(node.get("type") or ""),
            "group": str(node.get("group") or ""),
            "source_file": str(node.get("source_file") or ""),
            "degree": int(node.get("degree") or 0),
        }
        for node in sorted(nodes, key=lambda item: (-int(item.get("degree") or 0), str(item.get("label") or "")))
        if int(node.get("degree") or 0) >= 2
    ][:8]

    surprises: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in sorted(links, key=lambda item: str(item.get("relation") or "")):
        source = by_id.get(str(edge.get("source") or ""))
        target = by_id.get(str(edge.get("target") or ""))
        if not source or not target:
            continue
        source_community = int(source.get("community") or 0)
        target_community = int(target.get("community") or 0)
        relation = str(edge.get("relation") or "related")
        source_id = str(source.get("id") or "")
        target_id = str(target.get("id") or "")
        source_file = str(source.get("source_file") or "").replace("\\", "/")
        target_file = str(target.get("source_file") or "").replace("\\", "/")
        if (
            not source_id or not target_id or source_id == target_id
            or not source_file or not target_file or source_file == target_file
            or source_community == target_community
            or relation in {"contains", "method", "case_of", "doc_references"}
        ):
            continue
        key = (source_id, target_id, relation)
        if key in seen:
            continue
        seen.add(key)
        surprises.append({
            "source": str(source.get("label") or source.get("id") or ""),
            "target": str(target.get("label") or target.get("id") or ""),
            "source_file": source_file,
            "target_file": target_file,
            "source_group": str(source.get("group") or ""),
            "target_group": str(target.get("group") or ""),
            "relation": relation,
            "confidence": str(edge.get("confidence") or ""),
            "source_id": source_id,
            "target_id": target_id,
        })
        if len(surprises) >= 10:
            break

    questions: list[str] = []
    if hubs:
        questions.append(f"Why is {hubs[0]['label']} connected to {hubs[0]['degree']} graph nodes?")
    if surprises:
        questions.append(f"What links {surprises[0]['source']} to {surprises[0]['target']} across communities?")
    if confidence.get("INFERRED", 0) > 0:
        questions.append("Which inferred graph relationships need file verification first?")
    if confidence.get("AMBIGUOUS", 0) > 0:
        questions.append("Which ambiguous graph relationships should be rebuilt or ignored?")

    return {
        "confidence": confidence,
        "provenance": dict(sorted(provenance.items())),
        "godNodes": hubs,
        "surprises": surprises,
        "suggestedQuestions": questions,
    }


def _layout_with_cache(
    nodes: list[dict[str, Any]],
    links: list[dict[str, Any]],
    graph: dict[str, Any],
    cache_path: Path,
    *,
    iterations: int,
) -> tuple[dict[str, tuple[float, float]], bool]:
    fingerprint = _graph_fingerprint(nodes, links, graph)
    cached = _load_cache(cache_path)
    if cached.get("fingerprint") == fingerprint and isinstance(cached.get("positions"), dict):
        positions = {
            node_id: (float(pos[0]), float(pos[1]))
            for node_id, pos in cached["positions"].items()
            if isinstance(pos, (list, tuple)) and len(pos) >= 2
        }
        if len(positions) >= len(nodes):
            return positions, True
    positions = _package_cluster_layout(nodes, links, iterations=iterations)
    atomic_write_json(
        cache_path,
        {"fingerprint": fingerprint, "positions": {k: [round(v[0], 4), round(v[1], 4)] for k, v in positions.items()}},
        sort_keys=True,
    )
    return positions, False


def _load_cache(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _graph_fingerprint(nodes: list[dict[str, Any]], links: list[dict[str, Any]], graph: dict[str, Any]) -> str:
    material = {
        "layout": LAYOUT_VERSION,
        "version": graph.get("version"),
        "built_at": graph.get("built_at"),
        "built_at_commit": graph.get("built_at_commit"),
        "nodes": [node["id"] for node in nodes],
        "links": [(edge["source"], edge["target"], edge["relation"]) for edge in links],
    }
    return hashlib.sha1(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()


def _group_order_penalty(name: str) -> int:
    """Product code sits central; tests/docs/root are pushed to the outside."""
    if name in ("tests", "docs"):
        return 2
    if name == "root":
        return 1
    return 0


def _package_cluster_layout(nodes: list[dict[str, Any]], links: list[dict[str, Any]], *, iterations: int = 50) -> dict[str, tuple[float, float]]:
    """Deterministic, collision-free package-cluster layout.

    Nodes are grouped by package; each group forms its own golden-angle
    (sunflower) spiral. Group centers walk outward on golden-angle rays and
    step further out until they clear every previously placed cluster, so
    hulls never overlap. Product packages are ordered before tests/docs so
    the code map's center is the product, not the test suite.
    """
    golden_angle = math.pi * (3 - math.sqrt(5))
    spacing = 16.0
    margin = 60.0
    groups: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        groups.setdefault(str(node.get("group") or "root"), []).append(node)
    ordered = sorted(groups.items(), key=lambda item: (_group_order_penalty(item[0]), -len(item[1]), item[0]))
    positions: dict[str, tuple[float, float]] = {}
    placed: list[tuple[float, float, float]] = []  # (cx, cy, radius)
    for index, (_name, members) in enumerate(ordered):
        cluster_r = spacing * 0.85 * math.sqrt(len(members)) + spacing + 24.0
        if index == 0:
            cx, cy = 0.0, 0.0
        else:
            theta = index * golden_angle
            dist = placed[0][2] + cluster_r + margin
            while True:
                cx, cy = dist * math.cos(theta), dist * math.sin(theta)
                if all(math.hypot(cx - px, cy - py) >= pr + cluster_r + margin for px, py, pr in placed):
                    break
                dist += spacing
        placed.append((cx, cy, cluster_r))
        members_sorted = sorted(members, key=lambda n: (-n.get("degree", 0), n["id"]))
        for i, node in enumerate(members_sorted):
            theta = i * golden_angle
            radius = spacing * 0.85 * math.sqrt(i + 1)
            positions[node["id"]] = (cx + radius * math.cos(theta), cy + radius * math.sin(theta))
    return positions


def _group_geometry(nodes: list[dict[str, Any]], positions: dict[str, tuple[float, float]]) -> list[dict[str, Any]]:
    """Per-group center/radius/count for hulls, legend, and zoom targets."""
    buckets: dict[str, list[tuple[float, float]]] = {}
    counts: dict[str, dict[str, int]] = {}
    for node in nodes:
        group = str(node.get("group") or "root")
        buckets.setdefault(group, []).append(positions.get(node["id"], (0.0, 0.0)))
        kind = "file" if node.get("type") == "file" else "symbol"
        counts.setdefault(group, {"file": 0, "symbol": 0})[kind] += 1
    geometry = []
    for group, points in buckets.items():
        cx = sum(p[0] for p in points) / len(points)
        cy = sum(p[1] for p in points) / len(points)
        radius = max((math.hypot(p[0] - cx, p[1] - cy) for p in points), default=10.0) + 24.0
        geometry.append({
            "name": group,
            "count": len(points),
            "files": counts[group]["file"],
            "symbols": counts[group]["symbol"],
            "cx": round(cx, 2),
            "cy": round(cy, 2),
            "r": round(radius, 2),
        })
    geometry.sort(key=lambda g: (-g["count"], g["name"]))
    return geometry


def _board_snapshots(root: Path) -> list[dict[str, Any]]:
    """Latest snapshot per board from the ACTIVE taskboard ledger (most recent first).

    Resolves the same way the runtime does (env override / private state home),
    falling back to the project-relative ledger only for standalone CLI use —
    otherwise the map would show stale or test-polluted boards from the
    checkout instead of the operator's real work.
    """
    try:
        from ..tasking.task_board import _resolve_ledger_path
        resolved = _resolve_ledger_path(None)
    except Exception:
        resolved = None
    if resolved is None:
        return []  # ledger disabled
    ledger = resolved if resolved.is_absolute() else root / resolved
    if not ledger.exists():
        return []
    latest: dict[str, dict[str, Any]] = {}
    try:
        for line in ledger.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if not isinstance(entry, dict):
                continue
            board_id = str(entry.get("board_id") or "")
            if not board_id:
                continue
            stamp = float(entry.get("updated_at") or entry.get("created_at") or 0.0)
            current = latest.get(board_id)
            if current is None or stamp >= float(current.get("_stamp") or 0.0):
                entry["_stamp"] = stamp
                latest[board_id] = entry
    except Exception:
        return []
    boards = sorted(latest.values(), key=lambda e: -float(e.get("_stamp") or 0.0))[:MAX_BOARDS]
    rendered = []
    for entry in boards:
        tasks = []
        for task in entry.get("tasks") or []:
            if not isinstance(task, dict):
                continue
            tasks.append({
                "id": str(task.get("id") or ""),
                "title": str(task.get("title") or ""),
                "status": str(task.get("status") or ""),
                "kind": str(task.get("kind") or ""),
                "evidence": [str(e) for e in (task.get("evidence") or []) if e],
                "depends_on": [str(d) for d in (task.get("depends_on") or []) if d],
            })
        stamp = float(entry.get("_stamp") or 0.0)
        rendered.append({
            "board_id": str(entry.get("board_id") or ""),
            "title": str(entry.get("title") or entry.get("objective") or ""),
            "state": str(entry.get("state") or ""),
            "event": str(entry.get("event") or ""),
            "updated_at": datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M") if stamp else "",
            "tasks": tasks,
        })
    return rendered


def _recent_commits(root: Path, limit: int = MAX_COMMITS) -> list[dict[str, Any]]:
    """Recent git commits with touched files; best-effort, empty without git."""
    try:
        run_kwargs = {
            "cwd": str(root), "capture_output": True, "text": True, "timeout": 10,
            "encoding": "utf-8", "errors": "replace",
        }
        apply_windows_hidden_process_flags(run_kwargs)
        out = subprocess.run(
            ["git", "log", f"-n{limit}", "--name-only", "--pretty=format:@%h|%ad|%s", "--date=format:%Y-%m-%d %H:%M"],
            **run_kwargs,
        )
        if out.returncode != 0:
            return []
    except Exception:
        return []
    commits: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in (out.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("@"):
            parts = line[1:].split("|", 2)
            current = {
                "hash": parts[0] if parts else "",
                "when": parts[1] if len(parts) > 1 else "",
                "subject": parts[2] if len(parts) > 2 else "",
                "files": [],
            }
            commits.append(current)
        elif line and current is not None:
            current["files"].append(line.replace("\\", "/"))
    return commits


def _payload(
    nodes: list[dict[str, Any]],
    links: list[dict[str, Any]],
    degrees: dict[str, int],
    positions: dict[str, tuple[float, float]],
    annotations: dict[str, Any],
    *,
    root: Path,
) -> dict[str, Any]:
    max_degree = max(degrees.values(), default=1) or 1
    rendered_nodes = []
    group_counter: dict[str, int] = {}
    node_rank: dict[str, int] = {}  # per-group file-label priority (0 = most connected)
    for node in sorted(nodes, key=lambda n: (n.get("group", ""), -n.get("degree", 0))):
        if node.get("type") == "file":
            group = str(node.get("group") or "root")
            node_rank[node["id"]] = group_counter.get(group, 0)
            group_counter[group] = group_counter.get(group, 0) + 1
        x, y = positions.get(node["id"], (0.0, 0.0))
        rendered_nodes.append({
            **node,
            "x": round(x, 2),
            "y": round(y, 2),
            "size": round(4 + 16 * (node["degree"] / max_degree), 2),
            "lrank": node_rank.get(node["id"], 9999),
        })
    all_x = [n["x"] for n in rendered_nodes]
    all_y = [n["y"] for n in rendered_nodes]
    max_extent = 1.0
    if all_x and all_y:
        far = max(max(abs(min(all_x)), abs(max(all_x))), max(abs(min(all_y)), abs(max(all_y))))
        max_extent = max(1.0, far)
    return {
        "kind": "mo-agent-unified-map-v2",
        "meta": {
            "project": root.name or "MO Agent",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
        "nodes": rendered_nodes,
        "links": links,
        "groups": _group_geometry(nodes, positions),
        "insights": _graph_insights(rendered_nodes, links),
        "annotations": annotations,
        "fileOps": _file_ops_payload(),
        "work": {
            "boards": _board_snapshots(root),
            "commits": _recent_commits(root),
        },
        "maxDegree": max_degree,
        "spiralExtent": max_extent,
    }


def _merge_brain_index(payload: dict[str, Any]) -> None:
    """Merge MO's cognition surfaces (Memory/Profile/Learning/Tasks) into the map
    payload as reachable nodes, placed on a golden-angle ring beyond the code extent.

    USER-FACING MAP ONLY: brain nodes live in the code_map payload, never in the graph
    spine, MO's in-prompt context, or the terminal panel — so MO's own view stays light
    and code-only. Boundary-safe: brain_index consumes the redacted snapshot (counts
    only), so nothing raw enters the map. Best-effort — a failure leaves the map code-only.
    """
    try:
        from ..dashboard.brain_index import build_brain_index

        index = build_brain_index()
    except Exception:
        return
    bnodes = list(index.get("nodes") or [])
    if not bnodes:
        return
    extent = float(payload.get("spiralExtent") or 1.0)
    ring = max(1.0, extent) * 1.35 + 220.0
    golden = math.pi * (3.0 - math.sqrt(5.0))
    groups = payload.setdefault("groups", [])
    seen_groups = {str(g.get("name") or "") for g in groups}
    for i, node in enumerate(bnodes):
        angle = i * golden
        x = round(math.cos(angle) * ring, 2)
        y = round(math.sin(angle) * ring, 2)
        payload["nodes"].append({
            "id": node["id"],
            "label": node["label"],
            "type": "brain",
            "group": node["group"],
            "layer": "brain",
            "source_file": "",
            "x": x,
            "y": y,
            "size": 15.0,
            "degree": 0,
            "lrank": 9999,
            "stats": node.get("stats") or {},
            "drill": node.get("drill") or "",
        })
        if node["group"] not in seen_groups:
            groups.append({"name": node["group"], "cx": x, "cy": y, "r": 60.0,
                           "files": 0, "symbols": 1, "brain": True})
            seen_groups.add(node["group"])
    # New list (not .extend) — payload["links"] aliases the caller's code-graph link
    # list; mutating it would corrupt the returned code-link count. Brain edges overlay.
    payload["links"] = list(payload.get("links") or []) + list(index.get("edges") or [])
    payload["brainLayer"] = True


def _file_ops_payload() -> dict[str, dict[str, Any]]:
    try:
        from core.tooling.file_operations import accumulated_files
        data = accumulated_files(limit=50)
    except Exception:
        return {}
    return {
        path: {
            "reads": int(info.get("reads", 0) or 0),
            "modifies": int(info.get("modifies", 0) or 0),
            "last_session": str(info.get("last_session") or ""),
        }
        for path, info in data.items()
    }


def _render_html(payload: dict[str, Any]) -> str:
    from interface.theming import skin_to_code_map_css

    assets = Path(__file__).parent
    template = (assets / "code_map.html").read_text(encoding="utf-8")
    script = (assets / "code_map.js").read_text(encoding="utf-8")
    graph_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (
        template.replace("__CSS__", skin_to_code_map_css())
        .replace("__SCRIPT__", script)
        .replace("__GRAPH_JSON__", graph_json)
    )


def _goal_windows(goal_dir: Path, mapping: dict[str, set[str]]) -> list[tuple[str, float, float]]:
    windows: list[tuple[str, float, float]] = []
    for path in sorted(goal_dir.glob("*.json")) if goal_dir.exists() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        run_id = str(data.get("run_id") or path.stem)
        start = float(data.get("started_at") or 0.0)
        finish = float(data.get("finished_at") or path.stat().st_mtime or time.time())
        if start:
            windows.append((run_id, start, max(finish, start)))
        for step in data.get("steps") or []:
            if isinstance(step, dict):
                for item in step.get("evidence") or []:
                    path_value = _path_from_evidence(str(item or ""))
                    if path_value:
                        mapping.setdefault(run_id, set()).add(path_value)
    return windows


def _scan_tool_audit(tool_audit: Path, mapping: dict[str, set[str]], windows: list[tuple[str, float, float]], root: Path) -> None:
    if not tool_audit.exists():
        return
    for line in tool_audit.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except Exception:
            continue
        if entry.get("tool") not in FILE_MUTATION_TOOLS:
            continue
        args = entry.get("arguments") if isinstance(entry.get("arguments"), dict) else {}
        path_value = _normalize_task_path(str(args.get("path") or args.get("file_path") or ""), root)
        if not path_value:
            continue
        worker_id = str(entry.get("worker_id") or "").strip()
        if worker_id:
            mapping.setdefault(worker_id, set()).add(path_value)
        if entry.get("surface") == "goal":
            ts = float(entry.get("ts") or 0.0)
            for run_id, start, finish in windows:
                if start <= ts <= finish:
                    mapping.setdefault(run_id, set()).add(path_value)


def _path_from_evidence(value: str) -> str:
    match = re.match(r"(?:read_file|write_file|edit_file):(.+)$", value.strip())
    if match:
        return match.group(1).strip()
    if re.search(r"[\\/].+\.[A-Za-z0-9]+$|\.(?:py|md|json|yaml|yml|txt|html|css|js|ts|tsx)$", value):
        return value.strip()
    return ""


def _normalize_task_path(value: str, root: Path) -> str:
    if not value:
        return ""
    path = Path(value)
    try:
        if path.is_absolute():
            value = path.resolve().relative_to(root).as_posix()
        else:
            value = path.as_posix()
    except Exception:
        value = str(value).replace("\\", "/")
    value = value.strip().lstrip("./")
    if not value or value.startswith(("memory/", "logs/")):
        return ""
    return value
