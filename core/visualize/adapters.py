"""Input adapters for MO's structure visualizer.

Each adapter turns a supported input into a node/edge ``GraphData`` by reducing it
to a nested Python structure and reusing the core ``parse_json_graph`` engine — so
every input renders through the same proven Mermaid/ASCII path.

Supported now: structured data (JSON/dict/list), Markdown heading outlines, and
directory trees. Code is intentionally out of scope — MO already visualizes code
via core/graph (structural_graph / generate_code_map).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .graph import GraphData, parse_json_graph

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_TREE_SKIP = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", ".idea", ".vscode", "dist", "build", ".tox",
}


# --- structured data (JSON / YAML-or-TOML-parsed dict / list) ----------------

def from_data(content: Any, *, lenient: bool = True) -> GraphData:
    """Graph a JSON string or a native dict/list/scalar."""
    return parse_json_graph(content, lenient=lenient)


# --- markdown heading outline -------------------------------------------------

def _markdown_to_struct(text: str) -> Any:
    """Reduce a Markdown document to a nested heading outline."""
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict]] = [(0, root)]
    found = False
    for raw in str(text or "").splitlines():
        match = _HEADING_RE.match(raw.strip())
        if not match:
            continue
        found = True
        level = len(match.group(1))
        title = match.group(2).strip() or "(untitled)"
        while len(stack) > 1 and stack[-1][0] >= level:
            stack.pop()
        node: dict[str, Any] = {}
        stack[-1][1][title] = node
        stack.append((level, node))
    if not found:
        return {"(no headings)": None}

    def _leaves(node: Any) -> Any:
        if isinstance(node, dict):
            return {key: _leaves(val) for key, val in node.items()} if node else None
        return node

    return _leaves(root)


def from_markdown(text: str) -> GraphData:
    """Graph a Markdown document as its heading/section outline."""
    return parse_json_graph(_markdown_to_struct(text))


# --- directory tree -----------------------------------------------------------

def _tree_to_struct(path: str | Path, *, max_depth: int = 4, max_entries: int = 200) -> Any:
    base = Path(path).expanduser()
    count = [0]

    def _walk(p: Path, depth: int) -> Any:
        if depth > max_depth or count[0] >= max_entries:
            return None
        try:
            entries = sorted(p.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
        except OSError:
            return None
        out: dict[str, Any] = {}
        for entry in entries:
            if count[0] >= max_entries:
                out["…"] = f"truncated at {max_entries} entries"
                break
            if entry.name in _TREE_SKIP or entry.name.startswith("."):
                continue
            count[0] += 1
            if entry.is_dir():
                out[entry.name + "/"] = _walk(entry, depth + 1)
            else:
                out[entry.name] = None
        return out or None

    if not base.exists():
        return {f"(not found: {base.name})": None}
    if base.is_file():
        return {base.name: None}
    return {base.name + "/": _walk(base, 1)}


def from_tree(path: str | Path, *, max_depth: int = 4, max_entries: int = 200) -> GraphData:
    """Graph a directory tree (depth/entry bounded; skips noise dirs)."""
    return parse_json_graph(_tree_to_struct(path, max_depth=max_depth, max_entries=max_entries))
