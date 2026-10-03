"""Safety policy for graph-oriented MCP servers.

MO can use codebase-memory as a supplemental read/query source, but it should not
expose the server's write/delete/admin tools as ordinary model tools. MO's native
graph tools remain bound to the private graph that ``build_graph`` refreshes.
The repo-scoped mo-graph server is primarily for external coding agents; if it is
also listed in MO, its private-cache writer stays hidden in favor of that native
tool.
Keep this module small and dependency-free so MCP startup stays light.
"""
from __future__ import annotations

from typing import Any


SAFE_CODEBASE_MEMORY_TOOLS = frozenset({
    "list_projects",
    "index_status",
    "search_graph",
    "trace_path",
    "trace_call_path",
    "detect_changes",
    "query_graph",
    "get_graph_schema",
    "get_code_snippet",
    "get_architecture",
    "search_code",
})

SAFE_MO_GRAPH_TOOLS = frozenset({
    "mo_graph_status",
    "mo_code_search",
    "mo_graph_explain",
    "mo_graph_neighbors",
    "mo_graph_path",
    "mo_graph_stats",
    "mo_find_callers",
    "mo_find_callees",
    "mo_graph_context",
    "mo_diff_impact",
})

UNSAFE_CODEBASE_MEMORY_TOOLS = frozenset({
    "index_repository",
    "delete_project",
    "manage_adr",
    "ingest_traces",
})

_SIGNATURE_TOOLS = frozenset({
    "search_graph",
    "trace_path",
    "detect_changes",
    "get_architecture",
    "get_graph_schema",
})

_MO_GRAPH_SIGNATURE_TOOLS = frozenset({
    "mo_graph_status",
    "mo_code_search",
    "mo_graph_context",
    "mo_diff_impact",
})


def _norm(value: object) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def tool_names(tools: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None) -> set[str]:
    return {str(t.get("name") or "") for t in (tools or []) if isinstance(t, dict)}


def looks_like_codebase_memory_server(server_name: str, tools: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None) -> bool:
    """Return True for a codebase-memory MCP server, even if locally renamed."""
    name = _norm(server_name)
    if "codebasememory" in name or name in {"cbm", "codememory"}:
        return True
    names = tool_names(tools)
    return len(names & _SIGNATURE_TOOLS) >= 3


def looks_like_mo_graph_server(server_name: str, tools: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None) -> bool:
    """Recognize MO's graph MCP even when the operator gives it another name."""
    name = _norm(server_name)
    names = tool_names(tools)
    return name == "mograph" or _MO_GRAPH_SIGNATURE_TOOLS <= names


def exposed_tools_for_server(server_name: str, tools: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None) -> set[str] | None:
    """Return the built-in read allowlist for a recognized graph server."""
    if looks_like_mo_graph_server(server_name, tools):
        return SAFE_MO_GRAPH_TOOLS
    if not looks_like_codebase_memory_server(server_name, tools):
        return None
    return SAFE_CODEBASE_MEMORY_TOOLS
