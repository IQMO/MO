"""Generate a self-contained HTML visualization for MO's structural graph.

The generator is deterministic, dependency-free, and writes only artifacts under
``memory/structural_graph`` by default:

- ``code_map.html``: MO Agent unified map — code structure clustered by
  package, overlaid with work context (taskboards, recent commits, file ops,
  goal/worker touched files) so MO and the operator can reference the tree and
  the files touched during work or major changes.
- ``task_annotations.json``: best-effort goal/worker -> touched files mapping
- ``.layout_cache.json``: graph fingerprint -> stable positions

All overlays are best-effort orientation, never proof; live files/tests win.
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

DEFAULT_GRAPH = Path("memory/structural_graph/graph.json")
HTML_NAME = "code_map.html"
ANNOTATIONS_NAME = "task_annotations.json"
LAYOUT_CACHE_NAME = ".layout_cache.json"
LAYOUT_VERSION = "package_clusters_v3"
WRITE_TOOLS = {"write_file", "edit_file"}
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
    # Default to the private-state graph location, never cwd/memory.
    graph_path = Path(path) if path is not None else Path(resolve_state_path(str(DEFAULT_GRAPH)))
    data = _load_graph(graph_path)
    nodes, links, degrees = _normalize_graph(data)
    out_path = Path(output) if output else graph_path.parent / HTML_NAME
    annotations_path = out_path.parent / ANNOTATIONS_NAME
    cache_path = out_path.parent / LAYOUT_CACHE_NAME
    root = graph_path.resolve().parents[2] if len(graph_path.resolve().parents) > 2 else Path(".")
    annotations = build_task_annotations(
        goal_dir=Path(resolve_state_path("memory/goal-runs")),
        tool_audit=Path(resolve_state_path("logs/tool_audit.jsonl")),
        root=root,
    )
    positions, cache_hit = _layout_with_cache(nodes, links, data, cache_path, iterations=iterations)
    payload = _payload(nodes, links, degrees, positions, annotations, root=root)
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


def build_task_annotations(
    *,
    goal_dir: str | Path = "memory/goal-runs",
    tool_audit: str | Path = "logs/tool_audit.jsonl",
    root: str | Path = ".",
) -> dict[str, Any]:
    """Return best-effort goal/worker touched-file annotations."""
    root_path = Path(root).resolve()
    mapping: dict[str, set[str]] = {}
    windows = _goal_windows(Path(goal_dir), mapping)
    _scan_tool_audit(Path(tool_audit), mapping, windows, root_path)
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
        links.append({"source": source, "target": target, "relation": str(edge.get("relation") or edge.get("type") or "related")})
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
        out = subprocess.run(
            ["git", "log", f"-n{limit}", "--name-only", "--pretty=format:@%h|%ad|%s", "--date=format:%Y-%m-%d %H:%M"],
            cwd=str(root), capture_output=True, text=True, timeout=10, encoding="utf-8", errors="replace",
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
        "annotations": annotations,
        "fileOps": _file_ops_payload(),
        "work": {
            "boards": _board_snapshots(root),
            "commits": _recent_commits(root),
        },
        "maxDegree": max_degree,
        "spiralExtent": max_extent,
    }


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
    graph_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    css = skin_to_code_map_css()
    return _HTML_TEMPLATE.replace("__GRAPH_JSON__", graph_json).replace("__CSS__", css)


_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>MO Agent — Unified Map</title>
<style>__CSS__
html,body{margin:0;height:100%;background:var(--bg);color:var(--text);font:13px ui-sans-serif,system-ui,"Segoe UI",sans-serif;overflow:hidden}
#side{left:16px;top:16px;bottom:16px;width:300px;padding:14px;display:flex;flex-direction:column;gap:10px;overflow:hidden;z-index:2}
#side h1{font-size:16px;margin:0;color:#fff}
#side .sub{color:var(--muted);font-size:11px}
#stats{display:flex;flex-wrap:wrap;gap:4px}
.filters{display:flex;gap:6px;flex-wrap:wrap}
.section-title{font-size:10px;color:var(--cyan);text-transform:uppercase;letter-spacing:.8px;margin:4px 0 2px}
#groups,#work{overflow-y:auto;min-height:0}
#groups{flex:1.1}#work{flex:1}
.grow{display:flex;align-items:center;gap:7px;padding:3px 6px;border-radius:7px;cursor:pointer;font-size:12px}
.grow:hover,.grow.sel{background:#13204a}
.dot{width:9px;height:9px;border-radius:50%;flex:none}
.grow .n{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.grow .c{color:var(--muted);font-size:10px}
.witem{padding:5px 7px;border-radius:8px;cursor:pointer;border:1px solid transparent;margin-bottom:3px}
.witem:hover,.witem.sel{background:#13204a;border-color:#26365f}
.witem .t{font-size:12px;color:#eaf2ff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.witem .m{font-size:10px;color:var(--muted)}
#info{right:16px;top:16px;width:360px;max-height:calc(100vh - 32px);overflow-y:auto;padding:14px 16px;font-size:12px;z-index:3;display:none}
#info.show{display:block}
#info h3{margin:0 0 6px;font-size:15px;color:#fff;word-break:break-all}
#info .row{margin:3px 0;font-size:11px;line-height:1.45;word-break:break-all}
#info .section{margin-top:10px;padding-top:8px;border-top:1px solid rgba(255,255,255,.08)}
#info .muted{color:var(--muted)}
#info .link{cursor:pointer;color:#9fc4ff}.link:hover{text-decoration:underline}
#hint{position:fixed;right:16px;bottom:12px;color:var(--muted);font-size:11px;z-index:2;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:4px 10px}
::-webkit-scrollbar{width:8px}::-webkit-scrollbar-thumb{background:#1d2c55;border-radius:4px}::-webkit-scrollbar-track{background:transparent}
</style></head><body>
<canvas id="canvas"></canvas>
<div id="side" class="panel">
  <div><h1>MO Agent — Unified Map</h1><div class="sub" id="meta"></div></div>
  <div id="stats"></div>
  <input id="search" placeholder="Search files, symbols, paths… (Enter zooms)" autocomplete="off">
  <div class="filters">
    <span class="chip on" data-f="file">files</span>
    <span class="chip on" data-f="symbol">symbols</span>
    <span class="chip" data-f="touched">touched only</span>
  </div>
  <div class="section-title">Packages</div>
  <div id="groups"></div>
  <div class="section-title">Work — taskboards &amp; recent commits</div>
  <div id="work"></div>
  <div class="sub">Overlays are best-effort orientation, not proof. Verify with files/tests.</div>
</div>
<div id="info" class="panel"></div>
<div id="hint">drag pan · wheel zoom · click node/package · Esc clears</div>
<script>
const DATA=__GRAPH_JSON__;
const PALETTE=['#7df9ff','#ffcc33','#ff5bea','#7bd88f','#a78bfa','#f78c6c','#80cbc4','#82aaff','#c792ea','#ffcb6b','#89ddff','#f07178','#c3e88d','#d7aefb','#b2ccd6','#e0e0e0'];
const canvas=document.getElementById('canvas'),ctx=canvas.getContext('2d'),info=document.getElementById('info');
const nodes=DATA.nodes||[],links=DATA.links||[],groups=DATA.groups||[],byId=new Map(nodes.map(n=>[n.id,n]));
const esc=s=>{const d=document.createElement('div');d.textContent=s??'';return d.innerHTML;};
const groupColor=new Map(groups.map((g,i)=>[g.name,PALETTE[i%PALETTE.length]]));
const neighbors=new Map();
for(const e of links){if(!neighbors.has(e.source))neighbors.set(e.source,[]);if(!neighbors.has(e.target))neighbors.set(e.target,[]);neighbors.get(e.source).push(e.target);neighbors.get(e.target).push(e.source);}
// reverse indexes: file path -> node, path -> tasks, path -> commits
const fileNode=new Map();for(const n of nodes){if(n.type==='file'&&n.source_file)fileNode.set(n.source_file,n);}
const pathTasks=new Map();const ann=(DATA.annotations&&DATA.annotations.tasks)||{};
for(const t in ann){for(const p of ann[t]){if(!pathTasks.has(p))pathTasks.set(p,[]);pathTasks.get(p).push(t);}}
const pathCommits=new Map();
for(const c of (DATA.work&&DATA.work.commits)||[]){for(const p of c.files||[]){if(!pathCommits.has(p))pathCommits.set(p,[]);pathCommits.get(p).push(c);}}
const ops=DATA.fileOps||{};
const touchedPaths=new Set([...Object.keys(ops),...pathTasks.keys(),...pathCommits.keys()]);
// ---- state ----
let scale=.3,ox=0,oy=0,hover=null,locked=null,drag=false,moved=false,last=null;
let query='',selGroup=null,highlightPaths=new Set(),selWorkEl=null;
const filters={file:true,symbol:true,touched:false};
function isSymbol(n){return n.type!=='file';}
function visible(n){
  if(n.type==='file'&&!filters.file)return false;
  if(isSymbol(n)&&!filters.symbol)return false;
  if(filters.touched&&!touchedPaths.has(n.source_file))return false;
  return true;
}
function matches(n){if(!query)return false;return [n.label,n.source_file,n.id].some(v=>(v||'').toLowerCase().includes(query));}
function highlighted(n){
  if(locked&&locked.id===n.id)return true;
  if(hover&&hover.id===n.id)return true;
  if(query&&matches(n))return true;
  if(highlightPaths.size&&highlightPaths.has(n.source_file))return true;
  return false;
}
// ---- drawing ----
function sidebarPx(){return 332*devicePixelRatio;}
function fitView(){const sb=sidebarPx();const w=canvas.width-sb;ox=sb+w/2;oy=canvas.height/2;const fit=Math.min(w,canvas.height)/2.3/(DATA.spiralExtent||1);scale=Math.max(.03,Math.min(.5,fit));}
function labelBudget(){if(scale<.12)return 0;if(scale<.3)return 2;if(scale<.6)return 8;if(scale<1)return 25;return 1e9;}
function resize(){canvas.width=innerWidth*devicePixelRatio;canvas.height=innerHeight*devicePixelRatio;draw();}
addEventListener('resize',resize);
function draw(){
  ctx.setTransform(1,0,0,1,0,0);ctx.clearRect(0,0,canvas.width,canvas.height);
  ctx.setTransform(scale,0,0,scale,ox,oy);
  // package hulls + labels
  for(const g of groups){
    const col=groupColor.get(g.name)||'#82aaff';const sel=selGroup===g.name;
    ctx.beginPath();ctx.fillStyle=col;ctx.globalAlpha=sel?.10:.045;ctx.arc(g.cx,g.cy,g.r,0,Math.PI*2);ctx.fill();
    ctx.globalAlpha=sel?.5:.22;ctx.strokeStyle=col;ctx.lineWidth=1.4/scale;ctx.stroke();ctx.globalAlpha=1;
    ctx.fillStyle=sel?'#ffffff':'rgba(220,231,255,.78)';
    ctx.font=`600 ${Math.max(11,15/scale)}px ui-sans-serif`;
    ctx.textAlign='center';ctx.fillText(g.name,g.cx,g.cy-g.r-8/scale);ctx.textAlign='start';
  }
  // links
  const anyFocus=Boolean(locked||hover||query||highlightPaths.size);
  for(const e of links){
    const a=byId.get(e.source),b=byId.get(e.target);
    if(!a||!b||!visible(a)||!visible(b))continue;
    const hi=highlighted(a)||highlighted(b);
    if(anyFocus&&!hi){ctx.strokeStyle='rgba(110,137,190,.05)';}
    else ctx.strokeStyle=hi?'rgba(137,221,255,.5)':'rgba(110,137,190,.09)';
    ctx.lineWidth=1/scale;ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();
  }
  // nodes
  for(const n of nodes){
    if(!visible(n))continue;
    const hi=highlighted(n),r=hi?n.size+5:n.size;
    const col=groupColor.get(n.group)||'#82aaff';
    ctx.beginPath();ctx.fillStyle=col;
    ctx.globalAlpha=anyFocus&&!hi?.18:(hi?1:(n.type==='file'?.85:.55));
    ctx.shadowColor=hi?'#bde7ff':col;ctx.shadowBlur=hi?22:6;
    ctx.arc(n.x,n.y,r,0,Math.PI*2);ctx.fill();
    ctx.shadowBlur=0;
    const op=ops[n.source_file];
    if(n.type==='file'&&op&&op.modifies>0){ctx.strokeStyle='rgba(255,204,51,.85)';ctx.lineWidth=2/scale;ctx.beginPath();ctx.arc(n.x,n.y,r+3/scale,0,Math.PI*2);ctx.stroke();}
    if(n.degree>=30){ctx.strokeStyle='rgba(255,255,255,.5)';ctx.lineWidth=1.6/scale;ctx.beginPath();ctx.arc(n.x,n.y,r,0,Math.PI*2);ctx.stroke();}
    ctx.globalAlpha=1;
    if(n.type==='file'&&(hi||n.lrank<labelBudget())){
      ctx.fillStyle=hi?'#ffffff':'rgba(234,242,255,.72)';
      ctx.font=`${Math.max(8,10/scale)}px ui-sans-serif`;
      ctx.fillText(n.label,n.x+r+4/scale,n.y+3/scale);
    }
  }
}
// ---- interaction ----
function world(e){return{x:(e.clientX*devicePixelRatio-ox)/scale,y:(e.clientY*devicePixelRatio-oy)/scale};}
function nearest(e){const p=world(e);let best=null,bd=Infinity;for(const n of nodes){if(!visible(n))continue;const d=Math.hypot(n.x-p.x,n.y-p.y);if(d<Math.max(16,n.size+9)&&d<bd){best=n;bd=d;}}return best;}
canvas.onmousedown=e=>{drag=true;moved=false;last={x:e.clientX*devicePixelRatio,y:e.clientY*devicePixelRatio};};
canvas.onmousemove=e=>{
  if(drag){const nx=e.clientX*devicePixelRatio,ny=e.clientY*devicePixelRatio;if(Math.abs(nx-last.x)+Math.abs(ny-last.y)>3)moved=true;ox+=nx-last.x;oy+=ny-last.y;last={x:nx,y:ny};draw();return;}
  const n=nearest(e);if(n!==hover){hover=n;if(!locked)showNode(hover);draw();}
};
canvas.onmouseup=e=>{drag=false;if(moved)return;const n=nearest(e);locked=n;showNode(n);if(!n){clearWorkSel();highlightPaths=new Set();}draw();};
canvas.onwheel=e=>{e.preventDefault();const f=e.deltaY<0?1.13:.885;const p=world(e);scale=Math.max(.02,Math.min(4,scale*f));ox=e.clientX*devicePixelRatio-p.x*scale;oy=e.clientY*devicePixelRatio-p.y*scale;draw();};
addEventListener('keydown',e=>{if(e.key==='Escape'){locked=null;query='';document.getElementById('search').value='';highlightPaths=new Set();selGroup=null;clearWorkSel();renderGroups();showNode(null);draw();}});
function zoomTo(x,y,targetScale){const sb=sidebarPx();scale=targetScale;ox=sb+(canvas.width-sb)/2-x*scale;oy=canvas.height/2-y*scale;draw();}
// ---- search ----
const searchEl=document.getElementById('search');
searchEl.oninput=()=>{query=searchEl.value.trim().toLowerCase();draw();};
searchEl.onkeydown=e=>{
  if(e.key!=='Enter'||!query)return;
  const m=nodes.find(n=>visible(n)&&matches(n));
  if(m){locked=m;showNode(m);zoomTo(m.x,m.y,Math.max(scale,.7));}
};
// ---- filters ----
for(const chip of document.querySelectorAll('.chip')){
  chip.onclick=()=>{const f=chip.dataset.f;filters[f]=!filters[f];chip.classList.toggle('on',filters[f]);draw();};
}
// ---- panels ----
document.getElementById('meta').textContent=`${DATA.meta.project} · generated ${DATA.meta.generated_at} · orientation only`;
const fileCount=nodes.filter(n=>n.type==='file').length;
const boards=(DATA.work&&DATA.work.boards)||[],commits=(DATA.work&&DATA.work.commits)||[];
document.getElementById('stats').innerHTML=
  `<span class="badge">${fileCount} files</span><span class="badge">${nodes.length-fileCount} symbols</span>`+
  `<span class="badge">${links.length} links</span><span class="badge">${groups.length} packages</span>`+
  `<span class="badge gold">${boards.length} boards</span><span class="badge pink">${commits.length} commits</span>`;
function renderGroups(){
  document.getElementById('groups').innerHTML=groups.map(g=>
    `<div class="grow${selGroup===g.name?' sel':''}" data-g="${esc(g.name)}">`+
    `<span class="dot" style="background:${groupColor.get(g.name)}"></span>`+
    `<span class="n">${esc(g.name)}</span><span class="c">${g.files}f · ${g.symbols}s</span></div>`).join('');
  for(const el of document.querySelectorAll('.grow')){
    el.onclick=()=>{const g=groups.find(x=>x.name===el.dataset.g);if(!g)return;
      selGroup=selGroup===g.name?null:g.name;renderGroups();
      if(selGroup)zoomTo(g.cx,g.cy,Math.min(1.4,Math.max(.18,Math.min(canvas.width,canvas.height)/(2.6*g.r))));else draw();};
  }
}
renderGroups();
function clearWorkSel(){if(selWorkEl){selWorkEl.classList.remove('sel');selWorkEl=null;}}
function stateBadge(s){const cls=s==='completed'?'green':(s==='active'?'gold':(s==='blocked'?'pink':'dim'));return `<span class="badge ${cls}">${esc(s||'?')}</span>`;}
function renderWork(){
  let h='';
  for(let i=0;i<boards.length;i++){
    const b=boards[i];
    h+=`<div class="witem" data-k="board" data-i="${i}"><div class="t">${esc(b.title||b.board_id)} ${stateBadge(b.state)}</div>`+
       `<div class="m">${esc(b.board_id)} · ${b.tasks.length} task(s) · ${esc(b.updated_at)}</div></div>`;
  }
  for(let i=0;i<commits.length;i++){
    const c=commits[i];
    h+=`<div class="witem" data-k="commit" data-i="${i}"><div class="t">${esc(c.subject)}</div>`+
       `<div class="m"><span class="badge pink">${esc(c.hash)}</span> ${esc(c.when)} · ${c.files.length} file(s)</div></div>`;
  }
  document.getElementById('work').innerHTML=h||'<div class="m" style="color:var(--muted)">No taskboards or commits found.</div>';
  for(const el of document.querySelectorAll('.witem')){
    el.onclick=()=>{
      const same=selWorkEl===el;clearWorkSel();highlightPaths=new Set();
      if(same){showNode(null);draw();return;}
      selWorkEl=el;el.classList.add('sel');
      if(el.dataset.k==='commit'){const c=commits[+el.dataset.i];for(const p of c.files)highlightPaths.add(p);showCommit(c);}
      else{const b=boards[+el.dataset.i];for(const t of b.tasks)for(const ev of t.evidence){const p=evidencePath(ev);if(p)highlightPaths.add(p);}showBoard(b);}
      locked=null;draw();
    };
  }
}
renderWork();
function evidencePath(ev){
  const m=/^(?:read_file|write_file|edit_file|file):(.+)$/.exec(String(ev||'').trim());
  if(m)return m[1].trim().replace(/\\/g,'/');
  if(/[\/\\].+\.[A-Za-z0-9]+$/.test(ev))return String(ev).replace(/\\/g,'/');
  return '';
}
// ---- inspector ----
function show(html){info.innerHTML=html;info.classList.add('show');}
function hide(){info.classList.remove('show');}
function fileLink(p){return `<span class="link" data-p="${esc(p)}">${esc(p)}</span>`;}
function bindFileLinks(){
  for(const el of info.querySelectorAll('.link[data-p]')){
    el.onclick=()=>{const n=fileNode.get(el.dataset.p);if(n){locked=n;showNode(n);zoomTo(n.x,n.y,Math.max(scale,.8));}};
  }
}
function showNode(n){
  if(!n){hide();return;}
  const col=groupColor.get(n.group)||'#82aaff';
  let h=`<h3>${esc(n.label)}</h3>`;
  h+=`<div class="row"><span class="badge" style="color:${col}">${esc(n.type)}</span>`+
     `<span class="badge">${esc(n.group)}</span><span class="badge">degree ${n.degree}</span></div>`;
  if(n.source_file)h+=`<div class="row muted">${esc(n.source_file)}</div>`;
  const op=ops[n.source_file];
  if(op)h+=`<div class="row"><span class="badge gold">${op.reads} reads · ${op.modifies} writes</span> <span class="muted">last session ${esc(op.last_session||'?')}</span></div>`;
  // contained symbols (for files)
  if(n.type==='file'){
    const kids=(neighbors.get(n.id)||[]).map(id=>byId.get(id)).filter(x=>x&&x.source_file===n.source_file&&isSymbol(x));
    if(kids.length){
      h+=`<div class="section"><div class="section-title">Symbols (${kids.length})</div>`;
      for(const k of kids.slice(0,14))h+=`<div class="row">${esc(k.label)} <span class="muted">${esc(k.type)}</span></div>`;
      if(kids.length>14)h+=`<div class="row muted">+${kids.length-14} more</div>`;
      h+=`</div>`;
    }
    const tks=pathTasks.get(n.source_file)||[];
    if(tks.length){
      h+=`<div class="section"><div class="section-title">Touched by work</div>`;
      for(const t of tks.slice(0,8))h+=`<div class="row"><span class="badge gold">${esc(t)}</span></div>`;
      h+=`</div>`;
    }
    const cms=pathCommits.get(n.source_file)||[];
    if(cms.length){
      h+=`<div class="section"><div class="section-title">Recent commits (${cms.length})</div>`;
      for(const c of cms.slice(0,6))h+=`<div class="row"><span class="badge pink">${esc(c.hash)}</span> ${esc(c.subject)}</div>`;
      h+=`</div>`;
    }
  } else if(n.source_file){
    h+=`<div class="section"><div class="section-title">Defined in</div><div class="row">${fileLink(n.source_file)}</div></div>`;
  }
  // For symbols, the parent file already appears under "Defined in" — skip it here.
  const topN=(neighbors.get(n.id)||[]).map(id=>byId.get(id)).filter(Boolean)
    .filter(t=>n.type==='file'||!(t.type==='file'&&t.source_file===n.source_file))
    .sort((a,b)=>(b.degree||0)-(a.degree||0)).slice(0,8);
  if(topN.length){
    h+=`<div class="section"><div class="section-title">Connected</div>`;
    for(const t of topN)h+=`<div class="row">${t.type==='file'&&t.source_file?fileLink(t.source_file):esc(t.label)} <span class="badge">${t.degree||0}</span></div>`;
    h+=`</div>`;
  }
  h+=`<div class="section muted">Orientation only — verify with file reads/tests.</div>`;
  show(h);bindFileLinks();
}
function showBoard(b){
  let h=`<h3>${esc(b.title||b.board_id)}</h3>`;
  h+=`<div class="row">${stateBadge(b.state)} <span class="badge">${esc(b.board_id)}</span> <span class="muted">${esc(b.updated_at)}</span></div>`;
  h+=`<div class="section"><div class="section-title">Task tree (${b.tasks.length})</div>`;
  for(const t of b.tasks){
    const icon=t.status==='completed'?'✓':(t.status==='active'||t.status==='in_progress')?'→':t.status==='blocked'?'✗':'○';
    h+=`<div class="row">${icon} <b>${esc(t.id)}</b> ${esc(t.title)} ${stateBadge(t.status)}${t.kind?` <span class="badge dim">${esc(t.kind)}</span>`:''}</div>`;
    if(t.depends_on.length)h+=`<div class="row muted" style="padding-left:14px">depends on: ${esc(t.depends_on.join(', '))}</div>`;
    for(const ev of t.evidence.slice(0,6)){
      const p=evidencePath(ev);
      h+=`<div class="row muted" style="padding-left:14px">· ${p?fileLink(p):esc(ev)}</div>`;
    }
  }
  h+=`</div><div class="section muted">Highlighted nodes are this board's evidence files (best-effort).</div>`;
  show(h);bindFileLinks();
}
function showCommit(c){
  let h=`<h3>${esc(c.subject)}</h3>`;
  h+=`<div class="row"><span class="badge pink">${esc(c.hash)}</span> <span class="muted">${esc(c.when)}</span></div>`;
  h+=`<div class="section"><div class="section-title">Files touched (${c.files.length})</div>`;
  for(const p of c.files.slice(0,30))h+=`<div class="row">${fileNode.has(p)?fileLink(p):esc(p)}</div>`;
  if(c.files.length>30)h+=`<div class="row muted">+${c.files.length-30} more</div>`;
  h+=`</div><div class="section muted">Highlighted nodes are this commit's files.</div>`;
  show(h);bindFileLinks();
}
// ---- boot ----
canvas.width=innerWidth*devicePixelRatio;canvas.height=innerHeight*devicePixelRatio;
fitView();draw();
const hash=decodeURIComponent(location.hash.slice(1)).toLowerCase();
if(hash){searchEl.value=hash;query=hash;const m=nodes.find(n=>matches(n));if(m){locked=m;showNode(m);zoomTo(m.x,m.y,.8);}}
</script></body></html>
"""


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
        if entry.get("tool") not in WRITE_TOOLS:
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
