"""In-memory source indexer for MO's canonical structural graph.

This module extracts project files, symbols, and relationships. Persistence,
query context, status, and public graph APIs belong to
``core.graph.structural_graph``. Graph output is orientation only: source reads,
tests, and runtime evidence remain required before edits or claims.
"""
from __future__ import annotations

import ast
import json
import os
import posixpath
import re
import stat
import time
from pathlib import Path
from typing import Any
import traceback

from ..runtime.backend_monitor import redact_monitor_text
from ..runtime.subprocess_flags import run_with_file_capture
from ..utils.env_utils import int_env
from ..utils.markdown import headings, prose_lines
from ..state.paths import mo_home

GRAPH_VERSION = "mo-code-graph-v17"
# Bump when shared admission widens so history revisits excluded commit paths.
SOURCE_PATH_POLICY_VERSION = "2"
# Native builds already run in a bounded helper process. A positive environment
# override can impose a local resource cap; zero keeps whole-project coverage.
DEFAULT_MAX_FILES = 0


def _max_files() -> int:
    return max(0, int_env("MO_CODE_GRAPH_MAX_FILES", DEFAULT_MAX_FILES))

_INDEX_EXTENSIONS = {
    ".py", ".md", ".txt", ".yaml", ".yml", ".json", ".toml", ".ini", ".bat", ".ps1", ".sh", ".html", ".css",
    ".tsx", ".jsx", ".vue", ".svelte", ".scss", ".sass", ".less", ".js", ".ts", ".go", ".rs", ".rb",
    ".kt", ".kts", ".java", ".xml", ".properties", ".gradle", ".pro",
}
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "node_modules", ".venv", "venv", "env", "dist", "build", "coverage", "logs", "memory", ".understand-anything",
    ".ua-src",
}
_SKIP_FILES = {"config.yaml", ".env", ".env.local", ".env.production", "logs/provider_audit.jsonl"}
_SKIP_PATH_PREFIXES: tuple[str, ...] = ()
_GENERATED_DOC_ARTIFACT_RE = re.compile(
    r"^docs/(?:[^/]+/)*(?:_archived|\d{4}-\d{2}-\d{2}T\d{4})(?:/|$)"
)
_GENERATED_PROJECT_MAP_RE = re.compile(
    r"^(?:docs|documentation|doc)/project-map\.md$",
    re.IGNORECASE,
)
_WORK_WORDS = {
    "build", "create", "implement", "make", "write", "add", "new", "fix", "debug", "repair", "solve",
    "review", "investigate", "inspect", "scan", "analyze", "analyse", "verify", "test", "find", "change",
    "modify", "refactor", "design", "visual", "goal", "worker", "provider", "taskboard", "tui",
}
_RATIONALE_RE = re.compile(r"(?:#|//|<!--|/\*|\*)\s*(NOTE|WHY|HACK)\s*:?\s*(.{8,180})", re.I)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]{1,90})\]\(([^)\s]+)[^)]*\)|\[\[([^\]]{1,90})\]\]")
_REQ_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)\s*(?:\[.*?\])?\s*(?:[<>=!~]=?.*)?$")
_TOML_DEP_RE = re.compile(r"[\"']([A-Za-z0-9][A-Za-z0-9_.-]*)\s*(?:\[.*?\])?\s*(?:[<>=!~].*)?[\"']")
_MCP_SERVER_RE = re.compile(r"[\"']([^\"']{1,80})[\"']\s*:\s*\{[^{}\n]*(?:[\"']command[\"']|[\"']package[\"']|[\"']args[\"'])")
_ENV_KEY_RE = re.compile(r"[\"']([A-Z][A-Z0-9_]{2,})[\"']\s*:")
_SEARCH_LITERAL_RE = re.compile(r"^[A-Za-z]+(?:_[A-Za-z]+)+$")


def _discover_files(root: Path) -> list[str]:
    root = root.resolve(strict=False)
    runtime_home = mo_home().resolve(strict=False)
    nested_runtime_home = runtime_home if runtime_home != root and root in runtime_home.parents else None

    resolved_dirs: dict[str, Path] = {}

    def outside_runtime_home(rel: str) -> bool:
        # Resolve each folder once: a thousand files share a few hundred folders, and
        # only a file that is itself a link can point somewhere its folder does not.
        path = root / rel
        folder = rel.rpartition("/")[0]
        parent = resolved_dirs.get(folder)
        if parent is None:
            parent = resolved_dirs[folder] = path.parent.resolve(strict=False)
        try:
            info = path.lstat()
            linked = stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_reparse_tag", 0))
        except OSError:
            linked = True
        candidate = path.resolve(strict=False) if linked else parent / path.name
        return root in candidate.parents and (
            nested_runtime_home is None
            or (candidate != nested_runtime_home and nested_runtime_home not in candidate.parents)
        )

    try:
        kwargs = {
            "cwd": str(root), "timeout": 5,
        }
        # The graph orients active work, not just the last committed tree.  Ask
        # Git for tracked files plus non-ignored untracked files so a newly
        # created product owner is visible before its first commit.  Ignored
        # files remain excluded here; the bounded local QA overlay below is the
        # only deliberate ignored-path exception.
        git_files_command = [
            "git",
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
        ]
        proc = run_with_file_capture(git_files_command, **kwargs)
        if proc.returncode != 0 and "dubious ownership" in str(proc.stderr or "").lower():
            proc = run_with_file_capture(
                [
                    "git",
                    "-c",
                    f"safe.directory={root}",
                    "ls-files",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                ],
                **kwargs,
            )
        if proc.returncode == 0 and proc.stdout.strip():
            candidates = [line.strip().replace("\\", "/") for line in proc.stdout.splitlines() if line.strip()]
            seen = set(candidates)
            for rel in _local_qa_overlay_files(root):
                if rel not in seen:
                    candidates.append(rel)
                    seen.add(rel)
            return [
                p
                for p in candidates
                if indexable_source_path(p)
                and outside_runtime_home(p)
                and (root / p).is_file()
            ]
    except Exception:
        traceback.print_exc()

    result: list[str] = []
    for base, dirs, names in os.walk(root):
        dirs[:] = sorted(
            d
            for d in dirs
            if d not in _SKIP_DIRS
            and not d.startswith(".")
            and outside_runtime_home((Path(base) / d).relative_to(root).as_posix())
        )
        for name in sorted(names):
            rel = Path(base, name).relative_to(root).as_posix()
            if indexable_source_path(rel) and outside_runtime_home(rel):
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
                if indexable_source_path(rel):
                    result.append(rel)
    return result


def indexable_source_path(rel: str) -> bool:
    """Shared source-format exclusions for current and historical orientation."""
    rel = rel.replace("\\", "/").strip("/")
    if not rel or rel in _SKIP_FILES:
        return False
    if any(rel.startswith(prefix) for prefix in _SKIP_PATH_PREFIXES):
        return False
    if _GENERATED_DOC_ARTIFACT_RE.match(rel) or _GENERATED_PROJECT_MAP_RE.match(rel):
        return False
    parts = rel.split("/")
    if any(part in _SKIP_DIRS for part in parts):
        return False
    name = parts[-1].lower()
    if name.startswith(".env") or name.endswith((".pyc", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".zip", ".sqlite", ".db", ".jsonl")):
        return False
    return Path(rel).suffix.lower() in _INDEX_EXTENSIONS or name in {"readme", "license", "dockerfile"}


def is_test_source_path(rel: str) -> bool:
    """Return whether *rel* is test support rather than a product owner.

    Test files remain first-class file nodes for impact/navigation, but their
    individual functions are deliberately collapsed into that file owner. A
    large local QA overlay otherwise contributes thousands of low-value symbol
    nodes while callers only need to know which test file reaches production.
    """
    normalized = str(rel or "").replace("\\", "/").strip("/").casefold()
    parts = normalized.split("/") if normalized else []
    filename = normalized.rsplit("/", 1)[-1]
    in_test_directory = bool(
        normalized.startswith(("test/", "tests/"))
        or any(part in {"test", "tests"} for part in parts[:-1])
    )
    root_test_filename = len(parts) == 1 and (
        filename.startswith("test_")
        or filename.endswith(("_test.py", "_tests.py"))
    )
    return bool(
        in_test_directory
        or root_test_filename
        or filename == "conftest.py"
    )


def _fingerprints(root: Path, files: list[str]) -> dict[str, str]:
    fps: dict[str, str] = {}
    for rel in files:
        try:
            st = (root / rel).stat()
        except Exception:
            continue
        fps[rel] = f"{int(st.st_mtime_ns)}:{int(st.st_size)}"
    return fps


def _stale_files(graph: dict[str, Any] | None, current_fps: dict[str, str]) -> list[str]:
    if not graph:
        return list(current_fps)
    old = graph.get("fingerprints") if isinstance(graph.get("fingerprints"), dict) else {}
    changed = [path for path, fp in current_fps.items() if old.get(path) != fp]
    removed = [path for path in old if path not in current_fps]
    return sorted(set(changed + removed))


def _build_graph(root: Path, files: list[str], fingerprints: dict[str, str]) -> dict[str, Any]:
    """Build one in-memory extractor graph.

    The canonical builder already returns before extraction when the persisted
    graph matches the current fingerprints. Retaining another full graph per
    project root therefore duplicates the canonical cache without providing a
    normal production hit.
    """
    return _build_graph_uncached(root, files, fingerprints)


def _build_graph_uncached(root: Path, files: list[str], fingerprints: dict[str, str]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    module_to_file = _module_index(files)
    python_trees = _parse_python_trees(root, files)

    for rel in files:
        file_node = _file_node(root, rel, python_trees=python_trees)
        nodes.append(file_node)
        file_nodes, file_edges = _file_structure(
            root, rel, module_to_file, files, python_trees=python_trees,
        )
        nodes.extend(file_nodes)
        edges.extend(file_edges)

    edge_seen = {(edge["source"], edge["target"], edge["type"]) for edge in edges}
    jvm_index = _jvm_symbol_index(root, files, nodes)
    for rel in files:
        if Path(rel).suffix.lower() not in {".kt", ".kts", ".java"}:
            continue
        for target in _jvm_import_targets(root, rel, jvm_index):
            item = (f"file:{rel}", f"file:{target}", "imports")
            if item not in edge_seen:
                edges.append({"source": item[0], "target": item[1], "type": item[2], "direction": "forward", "weight": 0.8})
                edge_seen.add(item)

    relationship_edges, relationship_quality = _python_relationship_edges(
        root, files, nodes, module_to_file, python_trees=python_trees,
    )
    for edge in relationship_edges:
        item = (edge["source"], edge["target"], edge["type"])
        if item not in edge_seen:
            edges.append(edge)
            edge_seen.add(item)

    duplicate_ids = _duplicate_node_ids(nodes)
    if duplicate_ids:
        raise ValueError(f"graph indexer produced duplicate node ids: {', '.join(duplicate_ids[:8])}")

    return {
        "version": GRAPH_VERSION,
        "kind": "mo-private-code-map",
        "project": {"root": str(root), "name": root.name, "builtAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
        "fingerprints": fingerprints,
        "nodes": nodes,
        "edges": edges,
        "quality": relationship_quality,
    }


def _refresh_graph_delta(root: Path, graph: dict[str, Any], files: list[str], fingerprints: dict[str, str], stale_files: list[str]) -> dict[str, Any]:
    """Refresh changed/added/removed file nodes and recompute global edges.

    Imports and call relationships are recomputed globally for each changed
    language after the per-file node merge. Python shares one parsed source set
    across both analyses; unchanged-language edges remain intact.
    """
    current_files = set(files)
    stale_set = set(stale_files)
    python_changed = any(path.lower().endswith(".py") for path in stale_set)
    jvm_changed = any(Path(path).suffix.lower() in {".kt", ".kts", ".java"} for path in stale_set)
    existing_nodes = [node for node in graph.get("nodes", []) if isinstance(node, dict)]
    stale_node_ids = {
        str(node.get("id") or "")
        for node in existing_nodes
        if str(node.get("filePath") or "") in stale_set
    }

    def keep_node(node: dict[str, Any]) -> bool:
        path = str(node.get("filePath") or "")
        if path in stale_set:
            return False
        return node.get("type") != "file" or path in current_files

    nodes = [node for node in existing_nodes if keep_node(node)]
    module_to_file = _module_index(files)
    python_trees = _parse_python_trees(
        root,
        files if python_changed else [path for path in files if path in stale_set],
    )
    stale_file_edges: list[dict[str, Any]] = []
    for rel in files:
        if rel not in stale_set:
            continue
        nodes.append(_file_node(root, rel, python_trees=python_trees))
        file_nodes, file_edges = _file_structure(
            root, rel, module_to_file, files, python_trees=python_trees,
        )
        nodes.extend(file_nodes)
        stale_file_edges.extend(file_edges)

    node_ids = {str(node.get("id")) for node in nodes}
    # Preserve non-import edges between remaining nodes except stale file-owned edges;
    # fresh contains edges for changed files are re-added below.
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for edge in graph.get("edges") or graph.get("links") or []:
        # Python imports + relationship edges need a global recompute only when
        # Python changed. Documentation/manifest deltas cannot alter those
        # targets, so preserve them instead of reparsing every Python file.
        if not isinstance(edge, dict) or (
            edge.get("type") == "imports" and (
                (python_changed and str(edge.get("source", "")).endswith(".py"))
                or (jvm_changed and str(edge.get("source", "")).endswith((".kt", ".kts", ".java")))
            )
        ) or (
            python_changed and edge.get("type") in ("calls", "inherits")
        ):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source in stale_node_ids or target in stale_node_ids:
            continue
        if source not in node_ids or target not in node_ids:
            continue
        relation = str(edge.get("type") or edge.get("relation") or "")
        item = (source, target, relation)
        if item not in seen:
            edges.append({**edge, "type": relation})
            seen.add(item)

    for edge in stale_file_edges:
        item = (edge["source"], edge["target"], edge["type"])
        if edge["source"] in node_ids and edge["target"] in node_ids and item not in seen:
            edges.append(edge)
            seen.add(item)

    relationship_quality = dict(graph.get("quality") or {})
    if jvm_changed:
        jvm_index = _jvm_symbol_index(root, files, nodes)
        for rel in files:
            if Path(rel).suffix.lower() not in {".kt", ".kts", ".java"}:
                continue
            for target in _jvm_import_targets(root, rel, jvm_index):
                item = (f"file:{rel}", f"file:{target}", "imports")
                if item[0] in node_ids and item[1] in node_ids and item not in seen:
                    edges.append({"source": item[0], "target": item[1], "type": item[2], "direction": "forward", "weight": 0.8})
                    seen.add(item)

    if python_changed:
        relationship_edges, relationship_quality = _python_relationship_edges(
            root, files, nodes, module_to_file, python_trees=python_trees,
        )
        for edge in relationship_edges:
            item = (edge["source"], edge["target"], edge["type"])
            if edge["source"] in node_ids and edge["target"] in node_ids and item not in seen:
                edges.append(edge)
                seen.add(item)

    graph = dict(graph)
    graph["project"] = {"root": str(root), "name": root.name, "builtAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    graph["fingerprints"] = fingerprints
    graph["nodes"] = nodes
    graph["edges"] = edges
    graph["quality"] = relationship_quality
    duplicate_ids = _duplicate_node_ids(nodes)
    if duplicate_ids:
        raise ValueError(f"incremental graph indexer produced duplicate node ids: {', '.join(duplicate_ids[:8])}")
    return graph


def _parse_python_trees(root: Path, files: list[str]) -> dict[str, ast.Module]:
    """Parse each Python source once for every extractor stage in this build."""
    trees: dict[str, ast.Module] = {}
    for rel in files:
        if not rel.endswith(".py"):
            continue
        try:
            trees[rel] = ast.parse((root / rel).read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError, UnicodeError):
            continue
    return trees


def _file_node(
    root: Path,
    rel: str,
    *,
    python_trees: dict[str, ast.Module] | None = None,
) -> dict[str, Any]:
    path = root / rel
    suffix = path.suffix.lower().lstrip(".") or "text"
    symbols: list[str] = _file_symbols(root, rel, python_trees=python_trees)
    search_terms: list[str] = []
    summary = f"Project file {rel}."
    if rel.endswith(".py"):
        tree = (
            python_trees.get(rel)
            if python_trees is not None
            else _parse_python_trees(root, [rel]).get(rel)
        )
        if tree is not None:
            doc = ast.get_docstring(tree)
            if doc:
                summary = " ".join(doc.split())[:240]
            search_terms = _python_file_search_terms(tree)
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
        "searchTerms": search_terms,
        "tags": [suffix, rel.split("/", 1)[0] if "/" in rel else "root"],
        "symbols": symbols,
    }


def _python_file_search_terms(tree: ast.Module) -> list[str]:
    """Index bounded public registry keys on their owning file node.

    Module dictionaries commonly map public tool/event names to the adapter
    that presents or dispatches them. Persisting a node for every key would add
    graph noise; a small file-level vocabulary instead lets project direction
    find the existing owner. Only identifier-like top-level string keys qualify.
    """
    terms: list[str] = []
    chars = 0
    for statement in tree.body:
        if isinstance(statement, ast.Assign):
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            value = statement.value
        else:
            continue
        if not isinstance(value, ast.Dict):
            continue
        for key in value.keys:
            raw = key.value if isinstance(key, ast.Constant) else None
            term = str(raw or "").strip()
            if not _SEARCH_LITERAL_RE.fullmatch(term) or term in terms:
                continue
            cost = len(term) + (1 if terms else 0)
            if len(terms) >= 18 or chars + cost > 160:
                return terms
            terms.append(term)
            chars += cost
    return terms


def _file_symbols(
    root: Path,
    rel: str,
    *,
    python_trees: dict[str, ast.Module] | None = None,
) -> list[str]:
    if rel.endswith(".py"):
        tree = (
            python_trees.get(rel)
            if python_trees is not None
            else _parse_python_trees(root, [rel]).get(rel)
        )
        if tree is None:
            return []
        return [
            getattr(node, "name", "")
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ][:16]
    try:
        return [name for _typ, name, _line in _regex_symbols((root / rel).read_text(encoding="utf-8", errors="replace"), Path(rel).suffix.lower())][:16]
    except Exception:
        return []


def _file_structure(
    root: Path,
    rel: str,
    module_to_file: dict[str, str],
    files: list[str],
    *,
    python_trees: dict[str, ast.Module] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if rel.endswith(".py") and not is_test_source_path(rel):
        nodes, edges = _python_structure(
            root, rel, module_to_file, python_trees=python_trees,
        )
    elif rel.endswith(".py"):
        # The file node already carries a bounded symbol summary. Relationship
        # extraction below aggregates calls from every test function back to
        # this file, retaining test impact without persisting test internals.
        nodes, edges = [], []
    else:
        nodes, edges = _regex_structure(root, rel)
    extra_nodes, extra_edges = _deterministic_sidecar_structure(root, rel, files)
    nodes.extend(extra_nodes)
    edges.extend(extra_edges)
    return nodes, edges


class _PythonSymbolVisitor(ast.NodeVisitor):
    """Route Python symbol node kinds through a collector's semantic owner."""

    def _visit_symbol(self, item: ast.AST, typ: str) -> None:
        raise NotImplementedError

    def visit_FunctionDef(self, item: ast.FunctionDef) -> None:
        self._visit_symbol(item, "function")

    def visit_AsyncFunctionDef(self, item: ast.AsyncFunctionDef) -> None:
        self._visit_symbol(item, "function")

    def visit_ClassDef(self, item: ast.ClassDef) -> None:
        self._visit_symbol(item, "class")


def _python_structure(
    root: Path,
    rel: str,
    module_to_file: dict[str, str],
    *,
    python_trees: dict[str, ast.Module] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    tree = (
        python_trees.get(rel)
        if python_trees is not None
        else _parse_python_trees(root, [rel]).get(rel)
    )
    if tree is None:
        return nodes, edges
    file_id = f"file:{rel}"

    class SymbolCollector(_PythonSymbolVisitor):
        def __init__(self) -> None:
            self.scope: list[tuple[str, str, str]] = []
            self.seen_ids: set[str] = set()

        def _visit_symbol(self, item: ast.AST, typ: str) -> None:
            name = str(getattr(item, "name", ""))
            qualified = f"{self.scope[-1][0]}.{name}" if self.scope else name
            line = int(getattr(item, "lineno", 1))
            base_id = f"{typ}:{rel}:{qualified}"
            node_id = _unique_symbol_id(base_id, line, self.seen_ids, item=item)
            self.seen_ids.add(node_id)
            start = min([line, *(
                int(decorator.lineno) for decorator in getattr(item, 'decorator_list', ())
            )])
            line_range = [start, int(getattr(item, "end_lineno", line) or line)]
            nodes.append({
                "id": node_id,
                "type": typ,
                "name": qualified,
                "shortName": name,
                "qualifiedName": qualified,
                "filePath": rel,
                "lineRange": line_range,
                "summary": f"{typ.title()} {qualified} in {rel}, lines {line_range[0]}-{line_range[1]}.",
                "searchTerms": _python_symbol_search_terms(item),
                "tags": ["python", typ, "method" if self.scope and self.scope[-1][2] == "class" and typ == "function" else "symbol"],
            })
            parent_id = self.scope[-1][1] if self.scope else file_id
            relation = "method" if self.scope and self.scope[-1][2] == "class" and typ == "function" else "contains"
            edges.append({"source": parent_id, "target": node_id, "type": relation, "direction": "forward", "weight": 1.0})
            self.scope.append((qualified, node_id, typ))
            self.generic_visit(item)
            self.scope.pop()

    SymbolCollector().visit(tree)
    return nodes, edges


def _python_symbol_search_terms(item: ast.AST) -> list[str]:
    """Extract bounded, non-prose vocabulary owned by one Python symbol.

    Names, attribute names, keyword names, and enum-like underscore literals
    make behavioral queries useful without copying function bodies into the
    persisted graph. Nested symbol bodies remain owned by their own nodes.
    """
    buckets: dict[str, list[str]] = {
        "literal": [], "attribute": [], "keyword": [], "name": [],
    }
    seen: set[str] = set()

    def add(kind: str, value: str) -> None:
        text = str(value or "").strip()
        if 1 < len(text) <= 64 and text not in seen:
            seen.add(text)
            buckets[kind].append(text)

    class Vocabulary(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name) -> None:
            add("name", node.id)

        def visit_Attribute(self, node: ast.Attribute) -> None:
            add("attribute", node.attr)
            self.visit(node.value)

        def visit_keyword(self, node: ast.keyword) -> None:
            if node.arg:
                add("keyword", node.arg)
            self.visit(node.value)

        def visit_Constant(self, node: ast.Constant) -> None:
            value = node.value
            if isinstance(value, str) and _SEARCH_LITERAL_RE.fullmatch(value):
                add("literal", value)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if node is item:
                self.generic_visit(node)
            else:
                add("name", node.name)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.visit_FunctionDef(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            if node is item:
                self.generic_visit(node)
            else:
                add("name", node.name)

    Vocabulary().visit(item)
    ordered = [
        *buckets["literal"][:8],
        *buckets["attribute"][:6],
        *buckets["keyword"][:4],
        *buckets["name"][:6],
    ]
    bounded: list[str] = []
    chars = 0
    for term in ordered:
        cost = len(term) + (1 if bounded else 0)
        if len(bounded) >= 18 or chars + cost > 160:
            break
        bounded.append(term)
        chars += cost
    return bounded


def _regex_structure(root: Path, rel: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    text = (root / rel).read_text(encoding="utf-8", errors="replace")
    file_id = f"file:{rel}"
    seen_ids: set[str] = set()
    for typ, name, line_no in _regex_symbols(text, Path(rel).suffix.lower())[:24]:
        node_id = _unique_symbol_id(f"{typ}:{rel}:{name}", line_no, seen_ids)
        seen_ids.add(node_id)
        nodes.append({
            "id": node_id,
            "type": typ,
            "name": name,
            "shortName": name,
            "qualifiedName": name,
            "filePath": rel,
            "lineRange": [line_no, line_no],
            "summary": f"{typ.title()} {name} in {rel}, line {line_no}.",
            "tags": [Path(rel).suffix.lower().lstrip("."), typ],
        })
        edges.append({"source": file_id, "target": node_id, "type": "contains", "direction": "forward", "weight": 1.0})
    return nodes, edges


def _deterministic_sidecar_structure(root: Path, rel: str, files: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    path = root / rel
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return [], []
    if len(text) > 300_000:
        text = text[:300_000]
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    file_id = f"file:{rel}"
    suffix = path.suffix.lower()
    known_files = set(files)

    def add_node(node_id: str, typ: str, name: str, line_no: int, summary: str, tags: list[str], edge_type: str = "contains") -> None:
        nodes.append({
            "id": node_id,
            "type": typ,
            "name": redact_monitor_text(name, 120),
            "filePath": rel,
            "lineRange": [line_no, line_no],
            "summary": redact_monitor_text(summary, 240),
            "tags": tags,
        })
        edges.append({"source": file_id, "target": node_id, "type": edge_type, "direction": "forward", "weight": 0.7})

    if suffix == ".md":
        lines = prose_lines(text)
        for heading in headings(lines):
            title, line_no, level = heading["title"], heading["line"], heading["level"]
            add_node(
                f"doc_heading:{rel}:{_slug(str(line_no) + '-' + title)}",
                "doc_heading", title, line_no,
                f"Markdown heading level {level} in {rel}, line {line_no}.",
                ["markdown", "heading", f"h{level}"],
            )
        for _line_no, line in lines:
            for link in _MARKDOWN_LINK_RE.finditer(line):
                raw_target = link.group(2) or link.group(3) or ""
                target = _resolve_markdown_target(rel, raw_target, known_files)
                if target:
                    edges.append({"source": file_id, "target": f"file:{target}", "type": "doc_references", "direction": "forward", "weight": 0.6})

    rationale_count = 0
    manifest_count = 0
    mcp_count = 0
    if _looks_like_mcp_config(rel):
        for server_name, package_refs, env_names, line_no in _mcp_config_items(text):
            mcp_count += 1
            add_node(
                f"mcp_server:{rel}:{_slug(server_name)}",
                "mcp_server",
                server_name,
                line_no,
                f"MCP server {server_name} declared in {rel}, line {line_no}.",
                ["mcp", "server"],
                edge_type="declares_mcp_server",
            )
            for package_ref in package_refs:
                mcp_count += 1
                add_node(
                    f"mcp_package:{rel}:{_slug(package_ref)}",
                    "mcp_package",
                    package_ref,
                    line_no,
                    f"MCP package reference {package_ref} declared in {rel}, line {line_no}.",
                    ["mcp", "package"],
                    edge_type="references_mcp_package",
                )
                if mcp_count >= 80:
                    break
            for env_name in env_names:
                mcp_count += 1
                add_node(
                    f"env_var:{rel}:{_slug(env_name)}",
                    "env_var",
                    env_name,
                    line_no,
                    f"Environment variable name {env_name} referenced by MCP config in {rel}, line {line_no}; value is not indexed.",
                    ["mcp", "env"],
                    edge_type="references_env_var",
                )
                if mcp_count >= 80:
                    break
            if mcp_count >= 80:
                break
    for line_no, line in enumerate(text.splitlines(), start=1):
        if rationale_count < 20:
            match = _RATIONALE_RE.search(line)
            if match:
                marker = match.group(1).upper()
                snippet = " ".join(match.group(2).strip(" -*/<>").split())[:150]
                if snippet:
                    rationale_count += 1
                    slug = _slug(f"{line_no}-{marker}-{snippet}")
                    add_node(
                        f"rationale:{rel}:{slug}",
                        "rationale",
                        f"{marker}: {snippet}",
                        line_no,
                        f"{marker} rationale in {rel}, line {line_no}: {snippet}",
                        ["rationale", marker.lower()],
                    )

        dep = _manifest_dependency_from_line(rel, line, suffix)
        if dep and manifest_count < 80:
            manifest_count += 1
            add_node(
                f"dependency:{rel}:{_slug(dep)}",
                "dependency",
                dep,
                line_no,
                f"Dependency {dep} declared in {rel}, line {line_no}.",
                ["manifest", "dependency"],
                edge_type="declares_dependency",
            )

        if _looks_like_mcp_config(rel) and mcp_count < 80:
            server = _mcp_server_name(line)
            if server:
                mcp_count += 1
                add_node(
                    f"mcp_server:{rel}:{_slug(server)}",
                    "mcp_server",
                    server,
                    line_no,
                    f"MCP server {server} declared in {rel}, line {line_no}.",
                    ["mcp", "server"],
                    edge_type="declares_mcp_server",
                )
            for env_name in _mcp_env_names(line):
                mcp_count += 1
                add_node(
                    f"env_var:{rel}:{_slug(env_name)}",
                    "env_var",
                    env_name,
                    line_no,
                    f"Environment variable name {env_name} referenced by MCP config in {rel}, line {line_no}; value is not indexed.",
                    ["mcp", "env"],
                    edge_type="references_env_var",
                )
                if mcp_count >= 80:
                    break

    return _dedupe_nodes_edges(nodes, edges)


def _slug(value: str, max_chars: int = 90) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-._").lower()
    return (slug or "item")[:max_chars]


def _resolve_markdown_target(rel: str, raw_target: str, known_files: set[str]) -> str:
    target = str(raw_target or "").strip().strip("<>")
    if not target or target.startswith(("#", "http://", "https://", "mailto:", "tel:")):
        return ""
    target = target.split("#", 1)[0].split("?", 1)[0].replace("\\", "/").strip()
    if not target:
        return ""
    base = posixpath.dirname(rel)
    normalized = posixpath.normpath(posixpath.join(base, target)).lstrip("./")
    candidates = [normalized]
    if not Path(normalized).suffix:
        candidates.extend([f"{normalized}.md", posixpath.join(normalized, "README.md")])
    for candidate in candidates:
        if candidate in known_files:
            return candidate
    return ""


def _manifest_dependency_from_line(rel: str, line: str, suffix: str) -> str:
    name = Path(rel).name.lower()
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", "-r ", "--", "[")):
        return ""
    if name.startswith("requirements") and name.endswith(".txt"):
        match = _REQ_NAME_RE.match(stripped.split(";", 1)[0])
        return match.group(1) if match else ""
    if name == "pyproject.toml" and ("dependencies" in stripped or stripped.startswith(("\"", "'"))):
        match = _TOML_DEP_RE.search(stripped)
        return match.group(1) if match else ""
    return ""


def _looks_like_mcp_config(rel: str) -> bool:
    name = Path(rel).name.lower()
    return name in {".mcp.json", "mcp.json"} or "mcp" in rel.lower().split("/")


def _mcp_server_name(line: str) -> str:
    match = _MCP_SERVER_RE.search(line)
    return match.group(1).strip() if match else ""


def _mcp_env_names(line: str) -> list[str]:
    if "env" not in line.lower() and not _looks_like_env_line(line):
        return []
    return sorted({match.group(1).strip() for match in _ENV_KEY_RE.finditer(line)})


def _mcp_config_items(text: str) -> list[tuple[str, list[str], list[str], int]]:
    try:
        data = json.loads(text)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    raw_servers = data.get("mcpServers")
    servers = raw_servers if isinstance(raw_servers, dict) else data
    result: list[tuple[str, list[str], list[str], int]] = []
    for name, config in servers.items():
        if not isinstance(name, str) or not isinstance(config, dict):
            continue
        if not ({"command", "package", "args", "env"} & set(config.keys())):
            continue
        env = config.get("env") if isinstance(config.get("env"), dict) else {}
        env_names = sorted(str(key) for key in env if re.match(r"^[A-Z][A-Z0-9_]{2,}$", str(key)))
        package = str(config.get("package") or "").strip()
        package_refs = [package] if package and not re.search(r"[:/\\]", package) else []
        result.append((name, package_refs[:5], env_names[:20], _line_number_for(text, name)))
    return result[:40]


def _line_number_for(text: str, needle: str) -> int:
    quoted = f'"{needle}"'
    for index, line in enumerate(text.splitlines(), start=1):
        if quoted in line or needle in line:
            return index
    return 1


def _looks_like_env_line(line: str) -> bool:
    return bool(re.search(r"[\"']env[\"']\s*:", line, re.I))


def _dedupe_nodes_edges(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    seen_nodes: set[str] = set()
    out_nodes: list[dict[str, Any]] = []
    for node in nodes:
        node_id = str(node.get("id") or "")
        if node_id and node_id not in seen_nodes:
            seen_nodes.add(node_id)
            out_nodes.append(node)
    seen_edges: set[tuple[str, str, str]] = set()
    out_edges: list[dict[str, Any]] = []
    for edge in edges:
        key = (str(edge.get("source") or ""), str(edge.get("target") or ""), str(edge.get("type") or ""))
        if key[0] and key[1] and key not in seen_edges:
            seen_edges.add(key)
            out_edges.append(edge)
    return out_nodes, out_edges


def _unique_symbol_id(base_id: str, line: int, seen: set[str], *, item: ast.AST | None = None) -> str:
    """Keep stable symbol ids where possible and disambiguate real collisions."""
    if base_id not in seen:
        return base_id
    suffix = ""
    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
        for decorator in item.decorator_list:
            if isinstance(decorator, ast.Attribute) and decorator.attr in {"setter", "deleter", "getter"}:
                suffix = decorator.attr
                break
    base_suffix = suffix or f"L{int(line)}"
    candidate = f"{base_id}@{base_suffix}"
    ordinal = 2
    while candidate in seen:
        candidate = f"{base_id}@{base_suffix}-{ordinal}"
        ordinal += 1
    return candidate


def _duplicate_node_ids(nodes: list[dict[str, Any]]) -> list[str]:
    counts: dict[str, int] = {}
    for node in nodes:
        node_id = str(node.get("id") or "")
        if node_id:
            counts[node_id] = counts.get(node_id, 0) + 1
    return sorted(node_id for node_id, count in counts.items() if count > 1)


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
    elif suffix in {".kt", ".kts"}:
        modifiers = r"(?:(?:public|private|protected|internal|open|sealed|data|enum|annotation|value|abstract|final|actual|expect|inline|inner|fun|suspend|operator|infix|tailrec|override|external)\s+)*"
        patterns = [
            ("class", re.compile(rf"^\s*{modifiers}(?:class|interface|object)\s+([A-Za-z_]\w*)")),
            ("function", re.compile(rf"^\s*{modifiers}fun\s+(?:<[^>]+>\s*)?(?:[A-Za-z_]\w*(?:[?.<>]+\w*)*\.)?([A-Za-z_]\w*)\s*\(")),
            ("class", re.compile(rf"^\s*{modifiers}typealias\s+([A-Za-z_]\w*)")),
        ]
    elif suffix == ".java":
        modifiers = r"(?:(?:public|private|protected|static|final|abstract|sealed|non-sealed|synchronized|native|strictfp|default)\s+)*"
        patterns = [
            ("class", re.compile(rf"^\s*{modifiers}(?:class|interface|enum|record|@interface)\s+([A-Za-z_$][\w$]*)")),
            ("function", re.compile(rf"^\s*{modifiers}(?:<[^>]+>\s*)?[A-Za-z_$][\w$<>,.?\[\]]*\s+([A-Za-z_$][\w$]*)\s*\(")),
        ]
    found: list[tuple[str, str, int]] = []
    for index, line in enumerate(text.splitlines(), start=1):
        for typ, pattern in patterns:
            match = pattern.search(line)
            if match:
                found.append((typ, match.group(1), index))
                break
    return found


def _jvm_symbol_index(
    root: Path,
    files: list[str],
    nodes: list[dict[str, Any]],
) -> dict[str, set[str]]:
    """Map exact Kotlin/Java package symbols to their project file(s)."""
    packages: dict[str, str] = {}
    for rel in files:
        if Path(rel).suffix.lower() not in {".kt", ".kts", ".java"}:
            continue
        try:
            text = (root / rel).read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        match = re.search(r"(?m)^\s*package\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*;?", text)
        if match:
            packages[rel] = match.group(1)
    result: dict[str, set[str]] = {}
    for rel, package in packages.items():
        result.setdefault(f"{package}.{Path(rel).stem}", set()).add(rel)
    for node in nodes:
        rel = str(node.get("filePath") or "")
        package = packages.get(rel)
        name = str(node.get("shortName") or node.get("name") or "")
        if package and name and node.get("type") in {"class", "function"}:
            result.setdefault(f"{package}.{name.rsplit('.', 1)[-1]}", set()).add(rel)
    return result


def _jvm_import_targets(root: Path, rel: str, index: dict[str, set[str]]) -> list[str]:
    """Resolve only unambiguous project-local Kotlin/Java imports."""
    try:
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    targets: list[str] = []
    for match in re.finditer(
        r"(?m)^\s*import\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+)(?:\s+as\s+[A-Za-z_$][\w$]*)?\s*;?",
        text,
    ):
        candidate = match.group(1)
        while "." in candidate:
            matches = index.get(candidate, set())
            if len(matches) == 1:
                target = next(iter(matches))
                if target != rel and target not in targets:
                    targets.append(target)
                break
            candidate = candidate.rsplit(".", 1)[0]
    return targets


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


def _symbol_node_index(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Indexes that preserve both qualified identity and convenient short names."""
    qualified: dict[tuple[str, str], list[str]] = {}
    top_level: dict[tuple[str, str], str] = {}
    short: dict[tuple[str, str], list[str]] = {}
    global_short: dict[str, list[str]] = {}
    for node in nodes:
        if not isinstance(node, dict) or node.get("type") not in ("function", "class"):
            continue
        node_id = str(node.get("id") or "")
        rel = str(node.get("filePath") or "")
        name = str(node.get("qualifiedName") or node.get("name") or "")
        bare = str(node.get("shortName") or name.rsplit(".", 1)[-1])
        if not node_id or not rel or not name:
            continue
        qualified.setdefault((rel, name), []).append(node_id)
        if "." not in name:
            top_level[(rel, bare)] = node_id
        short.setdefault((rel, bare), []).append(node_id)
        global_short.setdefault(bare, []).append(node_id)
    return {"qualified": qualified, "top_level": top_level, "short": short, "global_short": global_short}


def _attribute_parts(node: ast.AST) -> list[str]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    # Keep the known suffix even when the receiver is a call/subscript whose
    # concrete type is not statically available. Dropping it made
    # ``factory().method()`` disappear from both edges and quality counters.
    return list(reversed(parts))


def _module_target(module: str, module_to_file: dict[str, str]) -> str:
    parts = str(module or "").split(".")
    while parts:
        target = module_to_file.get(".".join(parts))
        if target:
            return target
        parts.pop()
    return ""


def _python_import_bindings(
    tree: ast.AST, rel: str, module_to_file: dict[str, str]
) -> tuple[dict[str, str], dict[str, tuple[str, str]], list[str]]:
    """Resolve aliases and file import targets in one walk of the source tree."""
    modules: dict[str, str] = {}
    symbols: dict[str, tuple[str, str]] = {}
    targets: set[str] = set()
    current_pkg = rel[:-3].replace("/", ".").split(".")[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = _module_target(alias.name, module_to_file)
                if target:
                    modules[alias.asname or alias.name.split(".", 1)[0]] = target
                    targets.add(target)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                prefix = current_pkg[:max(0, len(current_pkg) - node.level + 1)]
                base = ".".join(prefix + ([base] if base else []))
            target = _module_target(base, module_to_file)
            if not target:
                continue
            targets.add(target)
            for alias in node.names:
                if alias.name != "*":
                    symbols[alias.asname or alias.name] = (target, alias.name)
    return modules, symbols, sorted(targets - {rel})


def _python_relationship_edges(
    root: Path,
    files: list[str],
    nodes: list[dict[str, Any]],
    module_to_file: dict[str, str],
    *,
    python_trees: dict[str, ast.Module] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Resolve imports, calls and inheritance from one parsed Python source set."""
    index = _symbol_node_index(nodes)
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    ambiguous_calls = 0
    dynamic_attribute_calls = 0
    unresolved_factory_calls = 0
    unresolved_calls = 0
    resolved_callback_edges = 0
    registry_dispatch_edges = 0
    ambiguous_samples: list[dict[str, Any]] = []
    dynamic_samples: list[dict[str, Any]] = []
    # self.x() calls whose provider may be a subclass; resolved after the file
    # loop, once every ``inherits`` edge is known.
    pending_self_calls: list[dict[str, Any]] = []
    node_by_id = {
        str(node.get("id") or ""): node
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    }
    return_types: dict[str, str] = {}
    registry_members: dict[tuple[str, str], tuple[str, ...]] = {}
    trees = python_trees if python_trees is not None else _parse_python_trees(root, files)
    imports = {}
    exports: dict[tuple[str, str], tuple[str, str]] = {}
    for rel in files:
        if not rel.endswith(".py"):
            continue
        tree = trees.get(rel)
        if tree is None:
            continue
        modules, symbols, imported_files = _python_import_bindings(tree, rel, module_to_file)
        imports[rel] = (modules, symbols)
        for imported_file in imported_files:
            edge = (f"file:{rel}", f"file:{imported_file}", "imports")
            if edge not in seen:
                edges.append({"source": edge[0], "target": edge[1], "type": edge[2], "direction": "forward", "weight": 0.8})
                seen.add(edge)
        candidates: dict[str, set[tuple[str, str]]] = {}
        assigned: set[str] = set()
        for statement in tree.body:
            if isinstance(statement, ast.ImportFrom):
                _, bindings, _ = _python_import_bindings(statement, rel, module_to_file)
                for name, binding in bindings.items():
                    candidates.setdefault(name, set()).add(binding)
            elif not isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                assigned.update(node.id for node in ast.walk(statement) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store))
        exports.update({(rel, name): next(iter(bindings)) for name, bindings in candidates.items()
                        if len(bindings) == 1 and name not in assigned})

    if not index["qualified"]:
        return edges, {}

    def target(rel: str, qualified: str) -> str:
        visited: set[tuple[str, str]] = set()
        while (rel, qualified) not in visited:
            visited.add((rel, qualified))
            matches = index["qualified"].get((rel, qualified)) or []
            if matches:
                return str(matches[0]) if len(matches) == 1 else ""
            name, dot, suffix = qualified.partition(".")
            binding = exports.get((rel, name))
            if not binding:
                break
            rel, name = binding
            qualified = name + (dot + suffix if dot else "")
        return ""

    # Exact top-level string->callable registries are a common deliberate
    # dispatch boundary (notably tools.TOOL_EXECUTORS). Static `.get(name)`
    # cannot identify the selected receiver, but every literal member is a
    # proven possible target. Keep only direct named functions; factories and
    # computed keys remain explicitly unresolved.
    for registry_rel, registry_tree in trees.items():
        for statement in registry_tree.body:
            if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
                continue
            assignment_targets = (
                statement.targets if isinstance(statement, ast.Assign) else [statement.target]
            )
            registry_name = next(
                (item.id for item in assignment_targets if isinstance(item, ast.Name)),
                "",
            )
            value = statement.value
            if not registry_name or not isinstance(value, ast.Dict):
                continue
            members: list[str] = []
            for key_node, value_node in zip(value.keys, value.values):
                if not (
                    isinstance(key_node, ast.Constant)
                    and isinstance(key_node.value, str)
                    and isinstance(value_node, ast.Name)
                ):
                    continue
                member = target(registry_rel, value_node.id)
                if member and node_by_id.get(member, {}).get("type") == "function":
                    members.append(member)
            if members:
                registry_members[(registry_rel, registry_name)] = tuple(dict.fromkeys(members))

    def resolve(
        func: ast.AST,
        rel: str,
        scope: list[tuple[str, str, str]],
        modules: dict[str, str],
        symbols: dict[str, tuple[str, str]],
        inherited_bases: list[str] | None = None,
        receiver_bindings: dict[str, tuple[str, str]] | None = None,
    ) -> tuple[str, str, str]:
        if isinstance(func, ast.Name):
            name = func.id
            # Closures first, then a module-level definition.
            for qualified, _node_id, typ in reversed(scope):
                if typ != "function":
                    continue
                found = target(rel, f"{qualified}.{name}")
                if found:
                    return found, "lexical", name
            found = str(index["top_level"].get((rel, name)) or "")
            if found:
                return found, "same_file", name
            imported = symbols.get(name)
            if imported:
                found = target(imported[0], imported[1])
                if found:
                    return found, "imported_symbol", name
            return "", "", name

        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call):
            factory_id, _factory_resolution, factory_name = resolve(
                func.value.func, rel, scope, modules, symbols, inherited_bases,
                receiver_bindings,
            )
            return_class_id = return_types.get(factory_id, "")
            return_class = node_by_id.get(return_class_id, {})
            class_rel = str(return_class.get("filePath") or "")
            class_name = str(return_class.get("qualifiedName") or return_class.get("name") or "")
            found = target(class_rel, f"{class_name}.{func.attr}") if class_rel and class_name else ""
            display = f"{factory_name or 'factory'}().{func.attr}"
            if found:
                return found, "return_type", display
            return "", "", display

        parts = _attribute_parts(func)
        if len(parts) < 2:
            return "", "", parts[-1] if parts else ""
        display = ".".join(parts)
        if parts[0] in {"self", "cls"}:
            owner = next((qualified for qualified, _node_id, typ in reversed(scope) if typ == "class"), "")
            found = target(rel, f"{owner}.{'.'.join(parts[1:])}") if owner else ""
            if found:
                return found, "class_method", display
            suffix = ".".join(parts[1:])
            for base_id in inherited_bases or []:
                base = node_by_id.get(base_id, {})
                base_rel = str(base.get("filePath") or "")
                base_name = str(base.get("qualifiedName") or base.get("name") or "")
                found = target(base_rel, f"{base_name}.{suffix}") if base_rel and base_name else ""
                if found:
                    return found, "inherited_method", display
            return "", "", display
        receiver = (receiver_bindings or {}).get(parts[0])
        if receiver and len(parts) == 2:
            class_id, resolution = receiver
            class_node = node_by_id.get(class_id, {})
            class_rel = str(class_node.get("filePath") or "")
            class_name = str(class_node.get("qualifiedName") or class_node.get("name") or "")
            found = target(class_rel, f"{class_name}.{parts[1]}") if class_rel and class_name else ""
            if found:
                return found, resolution, display
        # Fully-qualified module access is deterministic even without an alias.
        module_file = _module_target(".".join(parts[:-1]), module_to_file)
        if module_file:
            found = target(module_file, parts[-1])
            if found:
                return found, "module_attribute", display
        module_file = modules.get(parts[0], "")
        if module_file:
            found = target(module_file, ".".join(parts[1:])) or target(module_file, parts[-1])
            if found:
                return found, "module_attribute", display
        imported = symbols.get(parts[0])
        if imported:
            found = target(imported[0], ".".join([imported[1], *parts[1:]]))
            if found:
                return found, "imported_attribute", display
        found = target(rel, display)
        return found, "same_file_qualified" if found else "", display

    def receiver_class(
        value: ast.AST | None,
        rel: str,
        scope: list[tuple[str, str, str]],
        modules: dict[str, str],
        symbols: dict[str, tuple[str, str]],
        receiver_bindings: dict[str, tuple[str, str]],
    ) -> tuple[str, str]:
        """Resolve only values that deterministically produce one project class."""
        if not isinstance(value, ast.Call):
            return "", ""
        found, _resolution, _display = resolve(
            value.func, rel, scope, modules, symbols,
            receiver_bindings=receiver_bindings,
        )
        if node_by_id.get(found, {}).get("type") == "class":
            return found, "local_constructor"
        returned = return_types.get(found, "")
        if node_by_id.get(returned, {}).get("type") == "class":
            return returned, "local_factory"
        return "", ""

    def function_receiver_bindings(
        item: ast.FunctionDef | ast.AsyncFunctionDef,
        rel: str,
        scope: list[tuple[str, str, str]],
        modules: dict[str, str],
        symbols: dict[str, tuple[str, str]],
        inherited: dict[str, tuple[str, str]],
    ) -> dict[str, tuple[str, str]]:
        """Conservatively infer typed locals and closure receivers for one function.

        A name is retained only when every assignment visible in this function
        agrees on one project class. Unknown or conflicting reassignment removes
        the binding instead of inventing a call edge.
        """
        bindings: dict[str, tuple[str, str]] = {}
        for name, (class_id, origin) in inherited.items():
            closure_origin = origin if origin.startswith("closure_") else f"closure_{origin.removeprefix('local_')}"
            bindings[name] = (class_id, closure_origin)

        args = [*item.args.posonlyargs, *item.args.args, *item.args.kwonlyargs]
        if item.args.vararg is not None:
            args.append(item.args.vararg)
        if item.args.kwarg is not None:
            args.append(item.args.kwarg)
        for arg in args:
            class_id = annotation_class(arg.annotation, rel, scope, modules, symbols)
            if class_id:
                bindings[arg.arg] = (class_id, "parameter_type")

        invalid: set[str] = set()

        def assigned_names(node: ast.AST) -> list[str]:
            if isinstance(node, ast.Name):
                return [node.id]
            if isinstance(node, (ast.Tuple, ast.List)):
                return [name for item in node.elts for name in assigned_names(item)]
            if isinstance(node, ast.Starred):
                return assigned_names(node.value)
            return []

        def bind(name: str, class_id: str, origin: str) -> None:
            if not name or name in invalid:
                return
            existing = bindings.get(name)
            if not class_id or (existing and existing[0] != class_id):
                bindings.pop(name, None)
                invalid.add(name)
                return
            bindings[name] = (class_id, origin)

        class Assignments(ast.NodeVisitor):
            def visit_Assign(self, node: ast.Assign) -> None:
                class_id, origin = receiver_class(
                    node.value, rel, scope, modules, symbols, bindings
                )
                for assignment_target in node.targets:
                    names = assigned_names(assignment_target)
                    for name in names:
                        bind(name, class_id if len(names) == 1 else "", origin)
                self.generic_visit(node.value)

            def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
                if not isinstance(node.target, ast.Name):
                    return
                annotated = annotation_class(node.annotation, rel, scope, modules, symbols)
                constructed, origin = receiver_class(
                    node.value, rel, scope, modules, symbols, bindings
                )
                class_id = constructed or annotated
                if annotated and constructed and annotated != constructed:
                    class_id = ""
                bind(node.target.id, class_id, origin or "local_annotation")
                if node.value is not None:
                    self.generic_visit(node.value)

            def visit_AugAssign(self, node: ast.AugAssign) -> None:
                if isinstance(node.target, ast.Name):
                    bind(node.target.id, "", "")

            def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
                if isinstance(node.target, ast.Name):
                    class_id, origin = receiver_class(
                        node.value, rel, scope, modules, symbols, bindings
                    )
                    bind(node.target.id, class_id, origin)
                self.generic_visit(node.value)

            def visit_Delete(self, node: ast.Delete) -> None:
                for deleted in node.targets:
                    for name in assigned_names(deleted):
                        bind(name, "", "")

            def visit_For(self, node: ast.For) -> None:
                for name in assigned_names(node.target):
                    bind(name, "", "")
                self.generic_visit(node.iter)
                for statement in [*node.body, *node.orelse]:
                    self.visit(statement)

            def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
                self.visit_For(node)

            def visit_With(self, node: ast.With) -> None:
                for item_node in node.items:
                    if item_node.optional_vars is not None:
                        for name in assigned_names(item_node.optional_vars):
                            bind(name, "", "")
                    self.visit(item_node.context_expr)
                for statement in node.body:
                    self.visit(statement)

            def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
                self.visit_With(node)

            def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
                if node.name:
                    bind(str(node.name), "", "")
                for statement in node.body:
                    self.visit(statement)

            def visit_comprehension(self, node: ast.comprehension) -> None:
                for name in assigned_names(node.target):
                    bind(name, "", "")
                self.generic_visit(node)

            def visit_FunctionDef(self, _node: ast.FunctionDef) -> None:
                return

            def visit_AsyncFunctionDef(self, _node: ast.AsyncFunctionDef) -> None:
                return

            def visit_ClassDef(self, _node: ast.ClassDef) -> None:
                return

            def visit_Lambda(self, _node: ast.Lambda) -> None:
                return

        visitor = Assignments()
        for statement in item.body:
            visitor.visit(statement)
        return bindings

    def emit(src_id: str, tgt: str, relation: str, resolution: str) -> None:
        if not tgt or tgt == src_id:
            return
        key = (src_id, tgt, relation)
        if key in seen:
            return
        seen.add(key)
        edges.append({
            "source": src_id,
            "target": tgt,
            "type": relation,
            "direction": "forward",
            "weight": 0.6 if relation == "calls" else 0.9,
            "resolution": resolution,
        })

    def annotation_class(
        annotation: ast.AST | None,
        rel: str,
        scope: list[tuple[str, str, str]],
        modules: dict[str, str],
        symbols: dict[str, tuple[str, str]],
    ) -> str:
        if annotation is None:
            return ""
        if isinstance(annotation, ast.Constant) and isinstance(annotation.value, str):
            try:
                annotation = ast.parse(annotation.value, mode="eval").body
            except (SyntaxError, ValueError):
                return ""
        if isinstance(annotation, ast.Subscript):
            # A project class may itself be generic (``Service[T]``). Otherwise
            # unwrap only wrappers whose value really is the returned object;
            # treating ``list[Service]`` as ``Service`` invents a false caller.
            found, _resolution, _display = resolve(
                annotation.value, rel, scope, modules, symbols
            )
            if node_by_id.get(found, {}).get("type") == "class":
                return found
            wrapper = _attribute_parts(annotation.value)
            wrapper_name = wrapper[-1].lower() if wrapper else ""
            values = (
                list(annotation.slice.elts)
                if isinstance(annotation.slice, ast.Tuple)
                else [annotation.slice]
            )
            if wrapper_name == "annotated":
                values = values[:1]
            elif wrapper_name not in {"optional", "union"}:
                return ""
            candidates = {
                annotation_class(value, rel, scope, modules, symbols)
                for value in values
            }
            candidates.discard("")
            return next(iter(candidates)) if len(candidates) == 1 else ""
        if isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            candidates = {
                annotation_class(annotation.left, rel, scope, modules, symbols),
                annotation_class(annotation.right, rel, scope, modules, symbols),
            }
            candidates.discard("")
            return next(iter(candidates)) if len(candidates) == 1 else ""
        found, _resolution, _display = resolve(annotation, rel, scope, modules, symbols)
        return found if node_by_id.get(found, {}).get("type") == "class" else ""

    def returned_constructor_class(
        item: ast.FunctionDef | ast.AsyncFunctionDef,
        rel: str,
        scope: list[tuple[str, str, str]],
        modules: dict[str, str],
        symbols: dict[str, tuple[str, str]],
    ) -> str:
        candidates: set[str] = set()
        invalid = False

        class Returns(ast.NodeVisitor):
            def visit_Return(self, node: ast.Return) -> None:
                nonlocal invalid
                if node.value is None or (
                    isinstance(node.value, ast.Constant) and node.value.value is None
                ):
                    return
                if not isinstance(node.value, ast.Call):
                    invalid = True
                    return
                found, _resolution, _display = resolve(
                    node.value.func, rel, scope, modules, symbols
                )
                if node_by_id.get(found, {}).get("type") == "class":
                    candidates.add(found)
                else:
                    invalid = True

            def visit_FunctionDef(self, _node: ast.FunctionDef) -> None:
                return

            def visit_AsyncFunctionDef(self, _node: ast.AsyncFunctionDef) -> None:
                return

            def visit_ClassDef(self, _node: ast.ClassDef) -> None:
                return

            def visit_Lambda(self, _node: ast.Lambda) -> None:
                return

        visitor = Returns()
        for statement in item.body:
            visitor.visit(statement)
        return next(iter(candidates)) if not invalid and len(candidates) == 1 else ""

    # Resolve project factory return types once before collecting call edges.
    # Explicit annotations win; a function with only one constructed return
    # class is the conservative annotation-free fallback.
    for rel, tree in trees.items():
        modules, symbols = imports[rel]

        class ReturnTypeCollector(ast.NodeVisitor):
            def __init__(self) -> None:
                self.scope: list[tuple[str, str, str]] = []

            def _visit_symbol(self, item: ast.AST, typ: str) -> None:
                name = str(getattr(item, "name", ""))
                qualified = f"{self.scope[-1][0]}.{name}" if self.scope else name
                node_id = target(rel, qualified)
                # Calling an async function yields a coroutine, not its annotated
                # result. Direct ``async_factory().method()`` must not inherit the
                # awaited value's class.
                if isinstance(item, ast.FunctionDef) and node_id:
                    class_id = annotation_class(
                        item.returns, rel, self.scope, modules, symbols
                    ) or returned_constructor_class(
                        item, rel, self.scope, modules, symbols
                    )
                    if class_id:
                        return_types[node_id] = class_id
                self.scope.append((qualified, node_id, typ))
                self.generic_visit(item)
                self.scope.pop()

            def visit_FunctionDef(self, item: ast.FunctionDef) -> None:
                self._visit_symbol(item, "function")

            def visit_AsyncFunctionDef(self, item: ast.AsyncFunctionDef) -> None:
                self._visit_symbol(item, "function")

            def visit_ClassDef(self, item: ast.ClassDef) -> None:
                self._visit_symbol(item, "class")

        ReturnTypeCollector().visit(tree)

    for rel, tree in trees.items():
        modules, symbols = imports[rel]
        collapsed_test_file = is_test_source_path(rel)

        class RelationshipCollector(_PythonSymbolVisitor):
            def __init__(self) -> None:
                self.scope: list[tuple[str, str, str]] = []
                self.class_bases: list[list[str]] = []
                self.receiver_bindings: list[dict[str, tuple[str, str]]] = []

            def _visit_symbol(self, item: ast.AST, typ: str) -> None:
                nonlocal ambiguous_calls, unresolved_calls
                name = str(getattr(item, "name", ""))
                qualified = f"{self.scope[-1][0]}.{name}" if self.scope else name
                src_id = f"file:{rel}" if collapsed_test_file else target(rel, qualified)
                base_targets: list[str] = []
                if isinstance(item, ast.ClassDef) and src_id:
                    for base in item.bases:
                        tgt, resolution, _display = resolve(base, rel, self.scope, modules, symbols)
                        if not collapsed_test_file:
                            emit(src_id, tgt, "inherits", resolution)
                        if tgt:
                            base_targets.append(tgt)
                self.scope.append((qualified, src_id, typ))
                if isinstance(item, ast.ClassDef):
                    self.class_bases.append(base_targets)
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    inherited = self.receiver_bindings[-1] if self.receiver_bindings else {}
                    self.receiver_bindings.append(function_receiver_bindings(
                        item, rel, self.scope[:-1], modules, symbols, inherited
                    ))
                self.generic_visit(item)
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.receiver_bindings.pop()
                if isinstance(item, ast.ClassDef):
                    self.class_bases.pop()
                self.scope.pop()

            def visit_Call(self, item: ast.Call) -> None:
                nonlocal ambiguous_calls, dynamic_attribute_calls
                nonlocal unresolved_calls, unresolved_factory_calls
                nonlocal resolved_callback_edges, registry_dispatch_edges
                if self.scope and self.scope[-1][1]:
                    call_parts = _attribute_parts(item.func)
                    if len(call_parts) == 2 and call_parts[1] == "get":
                        registry_ref = symbols.get(call_parts[0])
                        registry_key = (
                            registry_ref if registry_ref is not None
                            else (rel, call_parts[0])
                        )
                        for member in registry_members.get(registry_key, ()):
                            before = len(edges)
                            emit(
                                self.scope[-1][1],
                                member,
                                "calls",
                                "registry_exact_member",
                            )
                            if len(edges) > before:
                                registry_dispatch_edges += 1
                    tgt, resolution, display = resolve(
                        item.func,
                        rel,
                        self.scope,
                        modules,
                        symbols,
                        self.class_bases[-1] if self.class_bases else None,
                        self.receiver_bindings[-1] if self.receiver_bindings else None,
                    )
                    if tgt:
                        emit(self.scope[-1][1], tgt, "calls", resolution)
                    else:
                        candidates = index["global_short"].get(display.rsplit(".", 1)[-1], [])
                        # A mixin calling self.x() where x is supplied by the class
                        # that mixes it in cannot resolve through its own bases —
                        # the provider is a SUBclass. Defer these until every
                        # ``inherits`` edge exists, then resolve downward.
                        self_parts = (
                            _attribute_parts(item.func) if isinstance(item.func, ast.Attribute) else []
                        )
                        if len(self_parts) >= 2 and self_parts[0] in {"self", "cls"}:
                            owner_id = next(
                                (nid for _q, nid, typ in reversed(self.scope) if typ == "class" and nid),
                                "",
                            )
                            if owner_id:
                                pending_self_calls.append({
                                    "source": self.scope[-1][1],
                                    "owner": owner_id,
                                    "suffix": ".".join(self_parts[1:]),
                                    "counted_dynamic": bool(
                                        candidates and isinstance(item.func, ast.Attribute)
                                    ),
                                })
                        if len(candidates) > 1 and isinstance(item.func, ast.Name):
                            ambiguous_calls += 1
                            if len(ambiguous_samples) < 20:
                                ambiguous_samples.append({"source": self.scope[-1][0], "file": rel, "call": display, "candidates": len(candidates)})
                        elif candidates and isinstance(item.func, ast.Attribute):
                            dynamic_attribute_calls += 1
                            if isinstance(item.func.value, ast.Call):
                                unresolved_factory_calls += 1
                            if len(dynamic_samples) < 20:
                                dynamic_samples.append({"source": self.scope[-1][0], "file": rel, "call": display, "candidates": len(candidates)})
                        elif candidates:
                            unresolved_calls += 1
                    # A callable passed without invocation is a deterministic
                    # deferred dependency (GUI callbacks, framework handlers,
                    # presenter injection). Keep it distinct from an immediate
                    # call while making it traversable by caller/callee tools.
                    callback_values = [*item.args, *(kw.value for kw in item.keywords)]
                    for value in callback_values:
                        if not isinstance(value, (ast.Name, ast.Attribute)):
                            continue
                        callback, callback_resolution, _callback_display = resolve(
                            value,
                            rel,
                            self.scope,
                            modules,
                            symbols,
                            self.class_bases[-1] if self.class_bases else None,
                            self.receiver_bindings[-1] if self.receiver_bindings else None,
                        )
                        if node_by_id.get(callback, {}).get("type") != "function":
                            continue
                        before = len(edges)
                        emit(
                            self.scope[-1][1],
                            callback,
                            "passes_callback",
                            f"callback_{callback_resolution}"[:40],
                        )
                        if len(edges) > before:
                            resolved_callback_edges += 1
                self.generic_visit(item)

        RelationshipCollector().visit(tree)

    # ── Mixin-host pass ───────────────────────────────────────────────────
    # ``self.x()`` inside a mixin resolves upward through bases only. MO's own
    # architecture inverts that: Agent(AgentTaskBoard, ..., AgentTurn) supplies
    # methods that the mixins call on self, so AgentTurn._call_provider ->
    # Agent._provider_tool_definitions was invisible and the caller query
    # returned only test callers. Resolve downward once every ``inherits`` edge
    # exists. A method reachable from two unrelated hosts stays unresolved
    # rather than inventing one owner.
    subclasses: dict[str, list[str]] = {}
    for edge in edges:
        if edge.get("type") == "inherits":
            subclasses.setdefault(str(edge.get("target") or ""), []).append(str(edge.get("source") or ""))
    mixin_host_calls = 0
    for pending in pending_self_calls:
        seen_hosts: set[str] = set()
        queue = list(subclasses.get(pending["owner"], []))
        found: set[str] = set()
        while queue:
            host_id = queue.pop()
            if not host_id or host_id in seen_hosts:
                continue
            seen_hosts.add(host_id)
            host = node_by_id.get(host_id, {})
            host_rel = str(host.get("filePath") or "")
            host_name = str(host.get("qualifiedName") or host.get("name") or "")
            if host_rel and host_name:
                hit = target(host_rel, f"{host_name}.{pending['suffix']}")
                if hit:
                    found.add(hit)
            queue.extend(subclasses.get(host_id, []))
        if len(found) != 1:
            continue
        before = len(edges)
        emit(pending["source"], found.pop(), "calls", "mixin_host_method")
        if len(edges) > before:
            mixin_host_calls += 1
            if pending["counted_dynamic"]:
                dynamic_attribute_calls -= 1

    symbol_nodes = sum(1 for node in nodes if isinstance(node, dict) and node.get("type") in ("function", "class"))
    quality = {
        "symbol_nodes": symbol_nodes,
        "qualified_symbol_nodes": sum(1 for node in nodes if isinstance(node, dict) and node.get("qualifiedName")),
        "method_nodes": sum(1 for node in nodes if isinstance(node, dict) and "method" in (node.get("tags") or [])),
        "resolved_calls": sum(1 for edge in edges if edge.get("type") == "calls"),
        "ambiguous_calls": ambiguous_calls,
        "dynamic_attribute_calls": dynamic_attribute_calls,
        "unresolved_receiver_calls": dynamic_attribute_calls,
        "mixin_host_calls": mixin_host_calls,
        "unresolved_factory_calls": unresolved_factory_calls,
        "unresolved_project_calls": unresolved_calls,
        "resolved_callback_edges": resolved_callback_edges,
        "registry_dispatch_edges": registry_dispatch_edges,
        "ambiguous_samples": ambiguous_samples,
        "dynamic_samples": dynamic_samples,
    }
    return edges, quality
