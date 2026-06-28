"""MO structure visualizer — turn structured content into a diagram.

Reduces supported inputs to a node/edge graph and renders them as a Mermaid
flowchart or an ASCII tree:

- structured data — JSON / YAML / TOML / dict / list
- Markdown documents — heading/section outline
- directory trees

Out of scope by design: source code (use core/graph — structural_graph /
generate_code_map) and free prose/binary (not meaningfully diagrammable).

    from core.visualize import visualize
    print(visualize(open("config.yaml").read(), kind="yaml"))
    print(visualize(readme_text, kind="markdown", format="ascii"))
    print(visualize("core", kind="tree"))
"""
from __future__ import annotations

from typing import Any

from .graph import GraphData, NodeData, EdgeData, NodeRow, parse_json_graph, graph_to_dict
from .render import to_mermaid, to_ascii_tree
from .adapters import from_data, from_markdown, from_tree

__all__ = [
    "visualize",
    "GraphData", "NodeData", "EdgeData", "NodeRow",
    "from_data", "from_markdown", "from_tree",
    "to_mermaid", "to_ascii_tree", "graph_to_dict",
    "SUPPORTED_KINDS",
]

SUPPORTED_KINDS = ("auto", "data", "json", "yaml", "toml", "markdown", "tree")


def _parse_yaml(text: str) -> Any:
    import yaml  # PyYAML; optional
    return yaml.safe_load(text)


def _parse_toml(text: str) -> Any:
    try:
        import tomllib  # Python 3.11+
    except ImportError:
        import tomli as tomllib  # optional backport
    return tomllib.loads(text)


def _looks_like_markdown(text: str) -> bool:
    return any(line.lstrip().startswith("#") for line in text.splitlines())


def _build_graph(content: Any, kind: str, *, allow_fs: bool = True) -> GraphData:
    kind = (kind or "auto").lower()

    if kind == "tree":
        if not allow_fs:
            raise ValueError("kind='tree' reads the filesystem; use the /visualize <path> command instead")
        return from_tree(content)
    if kind in ("markdown", "md"):
        return from_markdown(str(content))
    if kind in ("yaml", "yml"):
        return from_data(_parse_yaml(str(content)))
    if kind == "toml":
        return from_data(_parse_toml(str(content)))
    if kind in ("data", "json"):
        return from_data(content)

    # auto-detect
    if isinstance(content, (dict, list)):
        return from_data(content)
    text = str(content or "")
    stripped = text.strip()
    # an existing directory path -> tree (only when filesystem access is allowed)
    if allow_fs:
        try:
            from pathlib import Path
            if stripped and len(stripped) < 1024 and Path(stripped).expanduser().is_dir():
                return from_tree(stripped)
        except OSError:
            pass
    # JSON first (most common structured input)
    if stripped[:1] in ("{", "["):
        graph = from_data(text)
        if graph.nodes:
            return graph
    if _looks_like_markdown(text):
        return from_markdown(text)
    # last resort: try data (handles lenient JSON), else markdown
    graph = from_data(text)
    return graph if graph.nodes else from_markdown(text)


def visualize(content: Any, *, kind: str = "auto", format: str = "mermaid", title: str = "", allow_fs: bool = True) -> str:
    """Render ``content`` as a diagram.

    Args:
        content: JSON/YAML/TOML text or dict/list, Markdown text, or a directory path.
        kind: one of SUPPORTED_KINDS. ``auto`` detects by shape/content/path.
        format: ``mermaid`` (default) or ``ascii``.
        title: optional diagram title (Mermaid only).
        allow_fs: when False, the filesystem (``tree``) path is refused — used by the
            pure model-facing tool so it never reads the filesystem.
    Returns:
        A diagram string, or a one-line error message on failure.
    """
    try:
        graph = _build_graph(content, kind, allow_fs=allow_fs)
    except ImportError as exc:
        return f"visualize error: parser unavailable for kind={kind!r} ({exc}). Install the optional dependency or use kind=json."
    except Exception as exc:
        return f"visualize error: {exc}"

    if not graph.nodes:
        detail = "; ".join(graph.errors) if graph.errors else "no renderable structure found"
        return f"visualize error: {detail}"

    if (format or "mermaid").lower() in ("ascii", "tree", "text"):
        return to_ascii_tree(graph)
    return to_mermaid(graph, title=title)
