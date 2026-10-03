"""Optional codebase-memory MCP helpers for supplemental architecture/review context.

MO's first-class graph tools stay bound to the native graph that ``build_graph``
actually refreshes. A configured codebase-memory server remains separately
available to the model through its safe MCP tools and may supplement mapping or
diff review here; it never silently replaces native search/callgraph results.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core.mcp.codebase_memory import SAFE_CODEBASE_MEMORY_TOOLS, looks_like_codebase_memory_server

_MCP_MANAGER: Any | None = None


def register_mcp_manager(manager: Any | None) -> None:
    """Register the live MCP manager for process-local graph tool calls."""
    global _MCP_MANAGER
    _MCP_MANAGER = manager


def clear_mcp_manager() -> None:
    register_mcp_manager(None)


def _client_tools(manager: Any, server_name: str) -> list[dict[str, Any]]:
    client = (getattr(manager, "_clients", {}) or {}).get(server_name)
    return list(getattr(client, "tools", []) or []) if client is not None else []


def _find_tool(tool_name: str) -> str | None:
    if tool_name not in SAFE_CODEBASE_MEMORY_TOOLS:
        return None
    manager = _MCP_MANAGER
    if manager is None:
        return None
    index = getattr(manager, "_index", {}) or {}
    for mcp_name, entry in sorted(index.items()):
        try:
            server_name, raw_tool = entry
        except Exception:
            continue
        if raw_tool != tool_name:
            continue
        if looks_like_codebase_memory_server(str(server_name), _client_tools(manager, str(server_name))):
            return str(mcp_name)
    return None


def _call(tool_name: str, arguments: dict[str, Any]) -> str | None:
    manager = _MCP_MANAGER
    mcp_name = _find_tool(tool_name)
    if manager is None or not mcp_name:
        return None
    try:
        text = str(manager.call(mcp_name, arguments) or "").strip()
    except Exception:
        return None
    if not text or text.startswith("Error:") or text.startswith("[MCP tool error]"):
        return None
    return text


def _json_from_text(text: str) -> Any | None:
    value = text.strip()
    if not value:
        return None
    try:
        return json.loads(value)
    except Exception:
        pass
    start = min((i for i in (value.find("{"), value.find("[")) if i >= 0), default=-1)
    if start < 0:
        return None
    try:
        return json.loads(value[start:])
    except Exception:
        return None


def _items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("results", "items", "matches", "nodes", "functions", "paths", "changes"):
            nested = value.get(key)
            if isinstance(nested, list):
                return nested
    return []


def _pick(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _format_json_hits(text: str, *, kind: str, limit: int = 20) -> str | None:
    data = _json_from_text(text)
    rows = [r for r in _items(data) if isinstance(r, dict)]
    if not rows:
        return None
    lines = [f"[codebase-memory] {kind}"]
    for row in rows[:limit]:
        name = _pick(row, "name", "qualified_name", "function_name", "symbol", "label", "id")
        path = _pick(row, "file", "path", "source_file", "file_path")
        line = _pick(row, "line", "start_line", "source_location", "range")
        rel = _pick(row, "relation", "type", "direction", "edge")
        score = _pick(row, "score", "confidence", "risk")
        parts = [p for p in (
            f"name={name}" if name else "",
            f"file={path}" if path else "",
            f"at={line}" if line else "",
            f"relation={rel}" if rel else "",
            f"score={score}" if score else "",
        ) if p]
        lines.append("- " + (", ".join(parts) if parts else json.dumps(row, ensure_ascii=False)[:300]))
    if len(rows) > limit:
        lines.append(f"... (+{len(rows) - limit} more)")
    return "\n".join(lines)


def _root_arg(root: str | Path | None) -> str:
    try:
        return str(Path(root or ".").resolve())
    except Exception:
        return str(root or ".")


def detect_changes_summary(diff_text: str, *, cwd: str | Path | None = None) -> str | None:
    if not str(diff_text or "").strip():
        return None
    text = _call("detect_changes", {"diff": diff_text, "repo_path": _root_arg(cwd), "limit": 50})
    if text is None:
        return None
    return _format_json_hits(text, kind="detect_changes", limit=20) or f"[codebase-memory] detect_changes\n{text[:4000]}"


def architecture_summary(*, cwd: str | Path | None = None, max_chars: int = 4000) -> str | None:
    text = _call("get_architecture", {"repo_path": _root_arg(cwd), "limit": 50})
    if text is None:
        return None
    return f"[codebase-memory] get_architecture\n{text[:max_chars]}"


def status() -> dict[str, Any]:
    manager = _MCP_MANAGER
    if manager is None:
        return {"available": False, "reason": "no_mcp_manager"}
    servers: list[dict[str, Any]] = []
    for server_name, client in sorted((getattr(manager, "_clients", {}) or {}).items()):
        tools = _client_tools(manager, str(server_name))
        if looks_like_codebase_memory_server(str(server_name), tools):
            exposed = sorted(
                raw for (_full, (srv, raw)) in (getattr(manager, "_index", {}) or {}).items()
                if srv == server_name
            )
            servers.append({"server": server_name, "safe_tools": exposed})
    return {"available": bool(servers), "servers": servers}
