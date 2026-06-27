"""Private lightweight code graph for MO context selection.

This is not a user-facing dashboard or command system. It is a small, local,
stale-aware map that helps MO choose likely-relevant files/functions before it
uses tools. The graph is orientation only: file reads/tests/tool evidence remain
required before edits or claims.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any
import traceback

from ..utils.atomic_write import atomic_write_json

from ..runtime.backend_monitor import get_monitor, redact_monitor_text
from ..utils.env_utils import int_env
from ..state.paths import private_state_enabled, project_cache_dir
from ..utils.text_utils import DEFAULT_CONTEXT_STOPWORDS

GRAPH_VERSION = "mo-code-graph-v1"
DEFAULT_MAX_FILES = 1200
SMALL_STALE_UPDATE_LIMIT = 24
MAX_CONTEXT_CHARS = 1400


def _max_files() -> int:
    return max(1, int_env("MO_CODE_GRAPH_MAX_FILES", DEFAULT_MAX_FILES))

_INDEX_EXTENSIONS = {
    ".py", ".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".ini", ".bat", ".ps1", ".sh", ".html", ".css",
    ".tsx", ".jsx", ".vue", ".svelte", ".scss", ".sass", ".less", ".js", ".ts", ".go", ".rs", ".rb",
}
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "node_modules", ".venv", "venv", "env", "dist", "build", "coverage", "logs", "memory", ".understand-anything",
    ".ua-src",
}
_SKIP_FILES = {"config.yaml", ".env", ".env.local", ".env.production", "logs/provider_audit.jsonl", "logs/ghost_audit.jsonl"}
_SKIP_PATH_PREFIXES: tuple[str, ...] = ()
_GENERATED_DOC_ARTIFACT_RE = re.compile(
    r"^docs/(?:[^/]+/)*(?:_archived|\d{4}-\d{2}-\d{2}T\d{4})(?:/|$)"
)
_STOPWORDS = DEFAULT_CONTEXT_STOPWORDS
_WORK_WORDS = {
    "build", "create", "implement", "make", "write", "add", "new", "fix", "debug", "repair", "solve",
    "review", "investigate", "inspect", "scan", "analyze", "analyse", "verify", "test", "find", "change",
    "modify", "refactor", "design", "visual", "goal", "ghost", "worker", "provider", "taskboard", "tui",
}


def should_include_code_graph_context(user_input: str) -> bool:
    """True when a private code map may help; false only for greetings."""
    if not _code_graph_enabled():
        return False
    text = str(user_input or "").strip().lower()
    if not text:
        return False
    # Only skip the graph for literal greetings — everything else gets a map.
    # The graph is cheap orientation (50-100 tokens min); never harmful to have.
    if text in {"hi", "hello", "hey", "yo", "hi mo", "hello mo", "hey mo", "thanks", "ok", "okay", "yes", "no", "y", "n"}:
        return False
    return True


def build_code_graph_context(
    user_input: str,
    *,
    cwd: str | None = None,
    max_chars: int = MAX_CONTEXT_CHARS,
    max_nodes: int = 8,
    profile: Any | None = None,
) -> str:
    """Return a compact provider-facing graph slice, or "" when not useful.

    The function silently builds or incrementally refreshes a local graph for
    small/medium projects. If the graph is too stale or too large, it returns no
    context rather than polluting the provider payload.
    """
    if not should_include_code_graph_context(user_input):
        _emit_graph_event("skipped", reason="disabled_or_irrelevant")
        return ""
    root = _project_root(cwd or os.getcwd())
    # Prefer MO's richer persisted structural graph when present. This keeps
    # the public code_graph API stable while preserving the legacy fallback.
    try:
        from .structural_graph import select_context as _select_structural_context
        structural_context = _select_structural_context(user_input, cwd=root, max_chars=max_chars, max_nodes=max_nodes, build_if_missing=False, profile=profile)
        if structural_context:
            return structural_context
    except Exception:
        traceback.print_exc()

    files = _discover_files(root)
    if not files:
        _emit_graph_event("skipped", root=root, reason="no_indexable_files")
        return ""
    max_files = _max_files()
    if len(files) > max_files:
        _emit_graph_event("skipped", root=root, reason=f"project_too_large>{max_files}", file_count=len(files))
        return ""

    graph_path = _graph_path(root)
    current_fps = _fingerprints(root, files)
    graph = _load_graph(graph_path)
    stale_files = _stale_files(graph, current_fps)

    if not graph or graph.get("version") != GRAPH_VERSION:
        graph = _build_graph(root, files, current_fps)
        _save_graph(graph_path, graph)
        status = "built"
    elif stale_files:
        if len(stale_files) <= SMALL_STALE_UPDATE_LIMIT:
            graph = _refresh_graph_delta(root, graph, files, current_fps, stale_files)
            _save_graph(graph_path, graph)
            status = "incremental"
        else:
            _emit_graph_event("skipped", root=root, reason="too_stale", file_count=len(files), stale_count=len(stale_files))
            return ""
    else:
        status = "fresh"

    selected = _select_nodes(graph, user_input, max_nodes=max_nodes, profile=profile)
    if not selected:
        _emit_graph_event(status, root=root, reason="no_relevant_nodes", file_count=len(files), stale_count=len(stale_files), selected=[])
        return ""
    _emit_graph_event(status, root=root, file_count=len(files), stale_count=len(stale_files), selected=selected)
    return _format_context(graph, selected, status=status, root=root, max_chars=max_chars)


_REPO_PATH_RE = re.compile(r"(?:core|interface|tools|tests|skills)/[\w./-]+\.[A-Za-z0-9]+")


def relevant_node_paths(user_input: str, *, cwd: str | None = None, profile: Any | None = None, max_nodes: int = 8) -> list[str]:
    """File-paths of the code in scope this turn — used to surface location-scoped
    conventions (skills whose scope globs these files). Two cheap signals:
    (1) repo paths the request names explicitly, (2) best-effort graph-selected node
    paths. LOAD-ONLY (never builds the graph) so it costs nothing when absent."""
    paths: list[str] = []
    for raw in _REPO_PATH_RE.findall(str(user_input or "")):
        p = raw.strip("`*.,;:() ").replace("\\", "/")
        if p and p not in paths:
            paths.append(p)
    try:
        root = _project_root(cwd or os.getcwd())
        graph = _load_graph(_graph_path(root))
        if graph:
            for node in _select_nodes(graph, user_input, max_nodes=max_nodes, profile=profile):
                fp = str(node.get("filePath") or "").replace("\\", "/")
                if fp and fp not in paths:
                    paths.append(fp)
    except Exception:
        pass
    return paths


def _code_graph_enabled() -> bool:
    value = str(os.environ.get("MO_CODE_GRAPH", "1")).strip().lower()
    return value not in {"0", "false", "off", "no", "disabled"}


def _emit_graph_event(
    status: str,
    *,
    root: Path | None = None,
    reason: str = "",
    file_count: int = 0,
    stale_count: int = 0,
    selected: list[dict[str, Any]] | None = None,
) -> None:
    try:
        monitor = get_monitor()
        if not monitor:
            return
        selected = selected or []
        monitor.emit("code_graph_context", {
            "status": redact_monitor_text(status, 40),
            "reason": redact_monitor_text(reason, 120),
            "project": redact_monitor_text(root.name if root else "", 80),
            "file_count": int(file_count or 0),
            "stale_count": int(stale_count or 0),
            "selected_count": len(selected),
            "node_ids": [redact_monitor_text(str(node.get("id") or ""), 160) for node in selected[:10]],
        })
    except Exception:
        traceback.print_exc()


def _project_root(cwd: str) -> Path:
    path = Path(cwd).resolve()
    try:
        proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=str(path), text=True, capture_output=True, timeout=3)
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(proc.stdout.strip()).resolve()
    except Exception:
        traceback.print_exc()
    return path


def _graph_path(root: Path, config: dict[str, Any] | None = None) -> Path:
    # Private-by-default: cache under ~/.mo/cache, not the project tree.
    if private_state_enabled(config):
        return project_cache_dir("code_graph", root, config=config) / "knowledge-graph.json"
    return root / "memory" / "code_graph" / "knowledge-graph.json"


def _discover_files(root: Path) -> list[str]:
    try:
        proc = subprocess.run(["git", "ls-files"], cwd=str(root), text=True, capture_output=True, timeout=5)
        if proc.returncode == 0 and proc.stdout.strip():
            candidates = [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]
            seen = set(candidates)
            for rel in _local_qa_overlay_files(root):
                if rel not in seen:
                    candidates.append(rel)
                    seen.add(rel)
            return [p for p in candidates if _indexable_path(p) and (root / p).is_file()]
    except Exception:
        traceback.print_exc()

    result: list[str] = []
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        for name in sorted(names):
            rel = Path(base, name).relative_to(root).as_posix()
            if _indexable_path(rel):
                result.append(rel)
    return result


def _local_qa_overlay_files(root: Path) -> list[str]:
    """Ignored maintainer-local QA files that still support local PRT/test impact."""
    result: list[str] = []
    for overlay in ("tests",):
        base = root / overlay
        if not base.is_dir():
            continue
        for current, dirs, names in os.walk(base):
            dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
            for name in sorted(names):
                rel = Path(current, name).relative_to(root).as_posix()
                if _indexable_path(rel):
                    result.append(rel)
    return result


def _indexable_path(rel: str) -> bool:
    rel = rel.replace("\\", "/").strip("/")
    if not rel or rel in _SKIP_FILES:
        return False
    if any(rel.startswith(prefix) for prefix in _SKIP_PATH_PREFIXES):
        return False
    if _GENERATED_DOC_ARTIFACT_RE.match(rel):
        return False
    parts = rel.split("/")
    if any(part in _SKIP_DIRS for part in parts):
        return False
    name = parts[-1].lower()
    if name.startswith(".env") or name.endswith((".pyc", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".zip", ".sqlite", ".db", ".jsonl")):
        return False
    return Path(rel).suffix.lower() in _INDEX_EXTENSIONS or name in {"readme", "license"}


# Process-scoped graph memo: {root: (fingerprint_signature, graph)}. Lets a
# turn-heavy process (or the test suite) reuse a just-built graph instead of
# re-reading/rebuilding it when no source file changed. Keyed by fingerprints so
# it self-invalidates the moment any file's mtime/size changes.
_GRAPH_MEMO: dict[str, tuple[str, dict[str, Any]]] = {}


def _fingerprints(root: Path, files: list[str]) -> dict[str, str]:
    fps: dict[str, str] = {}
    for rel in files:
        try:
            st = (root / rel).stat()
        except Exception:
            continue
        fps[rel] = f"{int(st.st_mtime_ns)}:{int(st.st_size)}"
    return fps


def _fps_signature(fingerprints: dict[str, str]) -> str:
    h = hashlib.sha256()
    for rel in sorted(fingerprints):
        h.update(rel.encode("utf-8", "replace"))
        h.update(b"\0")
        h.update(fingerprints[rel].encode("utf-8", "replace"))
        h.update(b"\0")
    return h.hexdigest()


def _load_graph(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        return None
    return data


def _save_graph(path: Path, graph: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, graph, indent=2, ensure_ascii=False)
    except Exception:
        traceback.print_exc()


def _stale_files(graph: dict[str, Any] | None, current_fps: dict[str, str]) -> list[str]:
    if not graph:
        return list(current_fps)
    old = graph.get("fingerprints") if isinstance(graph.get("fingerprints"), dict) else {}
    changed = [path for path, fp in current_fps.items() if old.get(path) != fp]
    removed = [path for path in old if path not in current_fps]
    return sorted(set(changed + removed))


def _build_graph(root: Path, files: list[str], fingerprints: dict[str, str]) -> dict[str, Any]:
    """Build the code graph, memoized per process on (root, file fingerprints).

    _build_graph is a pure function of its inputs, and it is the single hotspot
    shared by BOTH graph paths (the legacy code-graph slice and the structural
    summary via load_or_build_graph_data). AST-parsing the whole repo costs
    ~10s; memoizing here means a turn-heavy process — and the test suite, which
    resets the on-disk cache home every test — parses the repo once instead of
    once per turn. The key is the fingerprints, so any file edit (new mtime/size)
    misses the memo and triggers a real rebuild — a stale graph is never served.
    """
    sig = _fps_signature(fingerprints)
    memo = _GRAPH_MEMO.get(str(root))
    if memo is not None and memo[0] == sig:
        return memo[1]
    graph = _build_graph_uncached(root, files, fingerprints)
    _GRAPH_MEMO[str(root)] = (sig, graph)
    return graph


def _build_graph_uncached(root: Path, files: list[str], fingerprints: dict[str, str]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    module_to_file = _module_index(files)

    for rel in files:
        file_node = _file_node(root, rel)
        nodes.append(file_node)
        file_nodes, file_edges = _file_structure(root, rel, module_to_file)
        nodes.extend(file_nodes)
        edges.extend(file_edges)

    edge_seen = {(edge["source"], edge["target"], edge["type"]) for edge in edges}
    for rel in files:
        if not rel.endswith(".py"):
            continue
        for target in _python_import_targets(root, rel, module_to_file):
            item = (f"file:{rel}", f"file:{target}", "imports")
            if item not in edge_seen:
                edges.append({"source": item[0], "target": item[1], "type": item[2], "direction": "forward", "weight": 0.8})
                edge_seen.add(item)

    for edge in _python_relationship_edges(root, files, nodes):
        item = (edge["source"], edge["target"], edge["type"])
        if item not in edge_seen:
            edges.append(edge)
            edge_seen.add(item)

    return {
        "version": GRAPH_VERSION,
        "kind": "mo-private-code-map",
        "project": {"root": str(root), "name": root.name, "builtAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
        "fingerprints": fingerprints,
        "nodes": nodes,
        "edges": edges,
    }


def _refresh_graph_delta(root: Path, graph: dict[str, Any], files: list[str], fingerprints: dict[str, str], stale_files: list[str]) -> dict[str, Any]:
    """Refresh changed/added/removed file nodes and recompute import edges.

    This keeps small updates cheap while preserving graph integrity. Import edges
    are global and inexpensive for MO-sized Python projects, so they are
    recomputed after the per-file node merge.
    """
    current_files = set(files)
    stale_set = set(stale_files)
    existing_nodes = [node for node in graph.get("nodes", []) if isinstance(node, dict)]

    def keep_node(node: dict[str, Any]) -> bool:
        path = str(node.get("filePath") or "")
        if path in stale_set:
            return False
        if node.get("type") == "file" and path not in current_files:
            return False
        return True

    nodes = [node for node in existing_nodes if keep_node(node)]
    for rel in files:
        if rel not in stale_set:
            continue
        nodes.append(_file_node(root, rel))
        file_nodes, _file_edges = _file_structure(root, rel, _module_index(files))
        nodes.extend(file_nodes)

    node_ids = {str(node.get("id")) for node in nodes}
    # Preserve non-import edges between remaining nodes except stale contains edges;
    # fresh contains edges for changed files are re-added below.
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in graph.get("edges", []):
        # imports + relationship edges are recomputed globally below.
        if not isinstance(edge, dict) or edge.get("type") in ("imports", "calls", "inherits"):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source not in node_ids or target not in node_ids:
            continue
        item = (source, target, str(edge.get("type") or ""))
        if item not in seen:
            edges.append(edge)
            seen.add(item)

    module_to_file = _module_index(files)
    for rel in stale_set:
        if rel not in current_files or not rel.endswith(".py"):
            continue
        for edge in _file_structure(root, rel, module_to_file)[1]:
            item = (edge["source"], edge["target"], edge["type"])
            if edge["source"] in node_ids and edge["target"] in node_ids and item not in seen:
                edges.append(edge)
                seen.add(item)

    for rel in files:
        if not rel.endswith(".py"):
            continue
        for target in _python_import_targets(root, rel, module_to_file):
            item = (f"file:{rel}", f"file:{target}", "imports")
            if item[0] in node_ids and item[1] in node_ids and item not in seen:
                edges.append({"source": item[0], "target": item[1], "type": item[2], "direction": "forward", "weight": 0.8})
                seen.add(item)

    for edge in _python_relationship_edges(root, files, nodes):
        item = (edge["source"], edge["target"], edge["type"])
        if edge["source"] in node_ids and edge["target"] in node_ids and item not in seen:
            edges.append(edge)
            seen.add(item)

    graph = dict(graph)
    graph["project"] = {"root": str(root), "name": root.name, "builtAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    graph["fingerprints"] = fingerprints
    graph["nodes"] = nodes
    graph["edges"] = edges
    return graph


def _file_node(root: Path, rel: str) -> dict[str, Any]:
    path = root / rel
    suffix = path.suffix.lower().lstrip(".") or "text"
    symbols: list[str] = _file_symbols(root, rel)
    summary = f"Project file {rel}."
    if rel.endswith(".py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            doc = ast.get_docstring(tree)
            if doc:
                summary = " ".join(doc.split())[:240]
        except Exception:
            traceback.print_exc()
    elif path.suffix.lower() in {".md", ".txt"}:
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip("# ").strip()
                if stripped:
                    summary = stripped[:180]
                    break
        except Exception:
            traceback.print_exc()
    return {
        "id": f"file:{rel}",
        "type": "file",
        "name": Path(rel).name,
        "filePath": rel,
        "summary": redact_monitor_text(summary, 260),
        "tags": [suffix, rel.split("/", 1)[0] if "/" in rel else "root"],
        "symbols": symbols,
    }


def _file_symbols(root: Path, rel: str) -> list[str]:
    if rel.endswith(".py"):
        try:
            tree = ast.parse((root / rel).read_text(encoding="utf-8", errors="replace"))
            return [getattr(node, "name", "") for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))][:16]
        except Exception:
            return []
    try:
        return [name for _typ, name, _line in _regex_symbols((root / rel).read_text(encoding="utf-8", errors="replace"), Path(rel).suffix.lower())][:16]
    except Exception:
        return []


def _file_structure(root: Path, rel: str, module_to_file: dict[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if rel.endswith(".py"):
        return _python_structure(root, rel, module_to_file)
    return _regex_structure(root, rel)


def _python_structure(root: Path, rel: str, module_to_file: dict[str, str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    try:
        tree = ast.parse((root / rel).read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return nodes, edges
    file_id = f"file:{rel}"
    for item in tree.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            typ = "class" if isinstance(item, ast.ClassDef) else "function"
            node_id = f"{typ}:{rel}:{item.name}"
            line_range = [int(getattr(item, "lineno", 1)), int(getattr(item, "end_lineno", getattr(item, "lineno", 1)) or getattr(item, "lineno", 1))]
            nodes.append({
                "id": node_id,
                "type": typ,
                "name": item.name,
                "filePath": rel,
                "lineRange": line_range,
                "summary": f"{typ.title()} {item.name} in {rel}, lines {line_range[0]}-{line_range[1]}.",
                "tags": ["python", typ],
            })
            edges.append({"source": file_id, "target": node_id, "type": "contains", "direction": "forward", "weight": 1.0})
    return nodes, edges


def _regex_structure(root: Path, rel: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    text = (root / rel).read_text(encoding="utf-8", errors="replace")
    file_id = f"file:{rel}"
    for typ, name, line_no in _regex_symbols(text, Path(rel).suffix.lower())[:24]:
        node_id = f"{typ}:{rel}:{name}"
        nodes.append({
            "id": node_id,
            "type": typ,
            "name": name,
            "filePath": rel,
            "lineRange": [line_no, line_no],
            "summary": f"{typ.title()} {name} in {rel}, line {line_no}.",
            "tags": [Path(rel).suffix.lower().lstrip("."), typ],
        })
        edges.append({"source": file_id, "target": node_id, "type": "contains", "direction": "forward", "weight": 1.0})
    return nodes, edges


def _regex_symbols(text: str, suffix: str) -> list[tuple[str, str, int]]:
    patterns: list[tuple[str, re.Pattern[str]]] = []
    if suffix in {".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte"}:
        patterns = [("function", re.compile(r"\b(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)")), ("class", re.compile(r"\b(?:export\s+)?class\s+([A-Za-z_$][\w$]*)")), ("function", re.compile(r"\b(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(?[^=;]*?\)?\s*=>")), ("interface", re.compile(r"\b(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)"))]
    elif suffix == ".go":
        patterns = [("function", re.compile(r"\bfunc\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)")), ("class", re.compile(r"\btype\s+([A-Za-z_]\w*)\s+(?:struct|interface)\b"))]
    elif suffix == ".rs":
        patterns = [("function", re.compile(r"\bfn\s+([A-Za-z_]\w*)")), ("class", re.compile(r"\b(?:struct|enum|trait)\s+([A-Za-z_]\w*)")), ("class", re.compile(r"\bimpl\s+([A-Za-z_]\w*)"))]
    elif suffix == ".rb":
        patterns = [("function", re.compile(r"\bdef\s+([A-Za-z_]\w*[!?=]?)")), ("class", re.compile(r"\b(?:class|module)\s+([A-Za-z_]\w*)"))]
    found: list[tuple[str, str, int]] = []
    for index, line in enumerate(text.splitlines(), start=1):
        for typ, pattern in patterns:
            match = pattern.search(line)
            if match:
                found.append((typ, match.group(1), index))
                break
    return found


def _module_index(files: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for rel in files:
        if not rel.endswith(".py"):
            continue
        path = rel[:-3]
        parts = path.split("/")
        dotted = ".".join(parts)
        result[dotted] = rel
        if parts[-1] == "__init__":
            result[".".join(parts[:-1])] = rel
        result.setdefault(parts[-1], rel)
    return {key: value for key, value in result.items() if key}


def _python_import_targets(root: Path, rel: str, module_to_file: dict[str, str]) -> list[str]:
    try:
        tree = ast.parse((root / rel).read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    targets: list[str] = []
    current_pkg = rel[:-3].replace("/", ".").split(".")[:-1]
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                prefix = current_pkg[:max(0, len(current_pkg) - node.level + 1)]
                base = ".".join(prefix + ([base] if base else []))
            modules.append(base)
        for mod in modules:
            candidates = [mod]
            parts = mod.split(".")
            while len(parts) > 1:
                parts.pop()
                candidates.append(".".join(parts))
            for candidate in candidates:
                target = module_to_file.get(candidate)
                if target and target != rel and target not in targets:
                    targets.append(target)
                    break
    return targets


def _symbol_node_index(nodes: list[dict[str, Any]]) -> dict[str, list[str]]:
    """name -> ids of function/class nodes (targets for call/inherit edges)."""
    index: dict[str, list[str]] = {}
    for node in nodes:
        if isinstance(node, dict) and node.get("type") in ("function", "class") and node.get("name"):
            index.setdefault(str(node["name"]), []).append(str(node["id"]))
    return index


def _call_target_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _base_class_name(base: ast.AST) -> str:
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Attribute):
        return base.attr
    return ""


def _resolve_relationship_targets(name: str, rel: str, index: dict[str, list[str]]) -> list[str]:
    candidates = index.get(name) or []
    if len(candidates) <= 1:
        return candidates
    # Prefer a same-file definition to cut cross-file name collisions.
    same_file = [c for c in candidates if c.split(":", 2)[1:2] == [rel]]
    return same_file or candidates


def _python_relationship_edges(
    root: Path, files: list[str], nodes: list[dict[str, Any]], *, max_per_symbol: int = 40
) -> list[dict[str, Any]]:
    """Resolve intra-project ``calls`` and ``inherits`` edges between symbol nodes.

    Resolution is name-based (orientation-grade, matching how MO documents graph
    hints as "verify with file reads"): a name resolved to a same-file symbol
    wins; otherwise it links to every project symbol of that name. Per-symbol
    call-edge count is capped to keep the graph bounded.
    """
    index = _symbol_node_index(nodes)
    if not index:
        return []
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def emit(src_id: str, name: str, relation: str, rel: str, count_state: list[int]) -> None:
        if not name:
            return
        for tgt in _resolve_relationship_targets(name, rel, index):
            if tgt == src_id:
                continue
            key = (src_id, tgt, relation)
            if key in seen:
                continue
            seen.add(key)
            edges.append({
                "source": src_id, "target": tgt, "type": relation,
                "direction": "forward", "weight": 0.6 if relation == "calls" else 0.9,
            })
            count_state[0] += 1

    for rel in files:
        if not rel.endswith(".py"):
            continue
        try:
            tree = ast.parse((root / rel).read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for item in tree.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            typ = "class" if isinstance(item, ast.ClassDef) else "function"
            src_id = f"{typ}:{rel}:{item.name}"
            if isinstance(item, ast.ClassDef):
                for base in item.bases:
                    emit(src_id, _base_class_name(base), "inherits", rel, [0])
            count_state = [0]
            for sub in ast.walk(item):
                if count_state[0] >= max_per_symbol:
                    break
                if isinstance(sub, ast.Call):
                    emit(src_id, _call_target_name(sub.func), "calls", rel, count_state)
    return edges


def _terms(text: str) -> list[str]:
    raw = re.findall(r"[a-zA-Z0-9_./-]{3,}", str(text or "").lower())
    terms: list[str] = []
    for item in raw:
        pieces = [item]
        pieces.extend(re.split(r"[/_.-]+", item))
        for piece in pieces:
            if len(piece) >= 3 and piece not in _STOPWORDS and piece not in terms:
                terms.append(piece)
    return terms[:32]


def _select_nodes(graph: dict[str, Any], query: str, *, max_nodes: int, profile: Any | None = None) -> list[dict[str, Any]]:
    terms = _terms(query)
    if not terms:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    wants_tests = bool({"test", "tests", "pytest", "verify", "verification"} & set(terms))
    for node in graph.get("nodes", []):
        if not isinstance(node, dict):
            continue
        path = str(node.get("filePath") or "").lower()
        name = str(node.get("name") or "").lower()
        hay = " ".join(
            str(node.get(key, "")) for key in ("id", "type", "name", "filePath", "summary", "tags", "symbols")
        ).lower()
        score = 0
        for term in terms:
            if term in hay:
                score += 4 if term == name else 1
                if term in path:
                    score += 1
        score += _personalized_boost(path, profile)
        if path.startswith("tests/") and not wants_tests:
            score -= max(2, score // 2)
        if score > 0:
            scored.append((score, node))
    scored.sort(key=lambda item: (-item[0], str(item[1].get("type")) != "file", str(item[1].get("id", ""))))
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    file_ids = {n.get("id") for _, n in scored if n.get("type") == "file"}
    for _score, node in scored:
        node_id = str(node.get("id") or "")
        if node_id in seen:
            continue
        if node.get("type") != "file":
            parent = f"file:{node.get('filePath')}"
            if parent not in file_ids and len(selected) < max_nodes:
                parent_node = _node_by_id(graph, parent)
                if parent_node and parent not in seen:
                    selected.append(parent_node)
                    seen.add(parent)
        selected.append(node)
        seen.add(node_id)
        if len(selected) >= max_nodes:
            break
    return selected[:max_nodes]


def _personalized_boost(path: str, profile: Any | None = None) -> int:
    paths = [str(item).lower().replace("\\", "/").strip("/") for item in getattr(profile, "important_paths", []) or []]
    if any(item and (path == item or path.startswith(item + "/")) for item in paths):
        return 4
    active = None
    try:
        active = profile.active_project() if profile else None
    except Exception:
        active = None
    active_path = str(getattr(active, "path", "") or "").lower().replace("\\", "/").strip("/")
    if active_path and path.startswith(active_path):
        return 2
    return 2 if path.startswith(("core/", "interface/", "tools/")) else 0


def _node_by_id(graph: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    for node in graph.get("nodes", []):
        if isinstance(node, dict) and node.get("id") == node_id:
            return node
    return None


def _format_context(graph: dict[str, Any], selected: list[dict[str, Any]], *, status: str, root: Path, max_chars: int) -> str:
    selected_ids = {str(node.get("id")) for node in selected}
    lines = [
        "### MO Internal Code Map - orientation only",
        f"Status: {status}; project: {root.name}; selected {len(selected)} of {len(graph.get('nodes', []))} nodes.",
        "Use this to choose likely files only. It is not proof; read files and verify before editing or claiming completion.",
    ]
    for node in selected:
        typ = str(node.get("type") or "node")
        name = str(node.get("name") or node.get("id") or "")
        path = str(node.get("filePath") or "")
        symbols = node.get("symbols") if isinstance(node.get("symbols"), list) else []
        sym_text = f" symbols={', '.join(str(s) for s in symbols[:6])}" if symbols else ""
        summary = redact_monitor_text(str(node.get("summary") or ""), 180)
        loc = f" `{path}`" if path else ""
        lines.append(f"- {typ}: {name}{loc}{sym_text} - {summary}")
    rels = []
    for edge in graph.get("edges", []):
        if not isinstance(edge, dict):
            continue
        if edge.get("source") in selected_ids and edge.get("target") in selected_ids and edge.get("type") != "contains":
            rels.append(f"- {edge.get('source')} --{edge.get('type')}--> {edge.get('target')}")
        if len(rels) >= 6:
            break
    if rels:
        lines.append("Relevant relationships:")
        lines.extend(rels)
    text = "\n".join(lines)
    text = redact_monitor_text(text, max_chars)
    digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:8]
    text += f"\nMap slice id: {digest}"
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def analyze_diff_impact(diff_text: str, root: str = "") -> list[str]:
    """Lightweight code graph impact scan for a git diff."""
    root_path = Path(root).resolve() if root else _project_root(os.getcwd())

    # Structural graph path: richer directed dependency traversal and
    # community-aware data for PRT when the persisted map exists.
    try:
        from .structural_graph import analyze_diff_impact as _structural_impact
        structural_impacted = _structural_impact(diff_text, root_path)
        if structural_impacted:
            return structural_impacted
    except Exception:
        traceback.print_exc()

    graph_path = _graph_path(root_path)
    graph = _load_graph(graph_path)
    if not graph:
        return []

    changed_files = set()
    for match in re.finditer(r"^diff --git a/(.+?) b/(.+)$", diff_text, flags=re.MULTILINE):
        changed_files.add(match.group(2).replace("\\", "/"))

    changed_file_ids = {f"file:{path}" for path in changed_files}
    impacted = set()
    edges = graph.get("edges", [])

    # Find files that depend on the changed files. Legacy graph edges use
    # file:<path> node IDs, not raw paths.
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if target in changed_file_ids and source.startswith("file:"):
            path = source.removeprefix("file:")
            if path not in changed_files:
                impacted.add(path)

    return sorted(list(impacted))


def affected_tests(diff_text: str, root: str = "") -> list[str]:
    """Identify tests affected by the given diff."""
    try:
        from .structural_graph import affected_tests as _structural_affected_tests
        structural_tests = _structural_affected_tests(diff_text, root or None)
        if structural_tests:
            return structural_tests
    except Exception:
        traceback.print_exc()
    impacted = analyze_diff_impact(diff_text, root)
    changed = [match.group(2).replace("\\", "/") for match in re.finditer(r"^diff --git a/(.+?) b/(.+)$", diff_text, flags=re.MULTILINE)]
    candidates = sorted(set(impacted + changed))
    return [f for f in candidates if "test" in f.lower() or re.search(r"(^|/)(test_.*|.*_test)\.py$|^(tests?)/", f.lower()) or f.endswith((".test.js", ".test.ts", ".spec.js", ".spec.ts", ".spec.tsx"))]

def risk_score(changed_files: list[str], impacted_files: list[str]) -> str:
    """Assess risk based on module and impact."""
    if not changed_files and not impacted_files:
        return "low"
    
    score = len(changed_files) * 1 + len(impacted_files) * 2
    try:
        from .structural_graph import structural_risk_score
        score += structural_risk_score(changed_files, impacted_files)
    except Exception:
        traceback.print_exc()
    
    for f in changed_files + impacted_files:
        if str(f).startswith("core/"):
            score += 5
        elif str(f).startswith("utils/"):
            score += 1
            
    if score >= 15:
        return "high"
    elif score >= 6:
        return "medium"
    return "low"
