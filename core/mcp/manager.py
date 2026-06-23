"""MCP manager — lifecycle + tool bridging for operator-configured MCP servers.

Reads `config.mcp`, starts enabled servers, aggregates their tools under a
namespaced id (`mcp__<server>__<tool>`), exposes them as MO tool definitions, and
routes calls. A server that fails to start is reported degraded and skipped — it
never crashes MO. Explicit `mcp.enabled: false` disables the bridge.
"""
from __future__ import annotations

import json
from typing import Any

from .client import McpClient


def _render_result(result: Any) -> str:
    """Flatten an MCP tools/call result to text for the model context."""
    if not isinstance(result, dict):
        return str(result)
    parts: list[str] = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
        elif isinstance(item, dict):
            parts.append(json.dumps(item))
        else:
            parts.append(str(item))
    text = "\n".join(parts) if parts else json.dumps(result)
    if result.get("isError"):
        return f"[MCP tool error] {text}"
    return text


class McpManager:
    def __init__(self, clients: list[McpClient] | None = None):
        self._clients: dict[str, McpClient] = {c.name: c for c in (clients or [])}
        self._index: dict[str, tuple[str, str]] = {}  # mcp_name -> (server, tool)
        self.degraded: list[str] = []  # server names that failed to start
        self._build_index()

    @classmethod
    def from_config(cls, config: dict | None) -> "McpManager":
        mcp_cfg = ((config or {}).get("mcp") or {}) if isinstance(config, dict) else {}
        mgr = cls([])
        if mcp_cfg.get("enabled") is False:
            return mgr
        for spec in mcp_cfg.get("servers") or []:
            if not isinstance(spec, dict) or spec.get("enabled") is False:
                continue
            name = str(spec.get("name") or "").strip()
            command = spec.get("command")
            if not name or not command:
                continue
            client = McpClient(
                name=name,
                command=command,
                args=spec.get("args") or [],
                env=spec.get("env") or {},
                timeout=float(spec.get("timeout", 30.0) or 30.0),
            )
            try:
                client.start()
                mgr._clients[name] = client
            except Exception:
                mgr.degraded.append(name)  # degraded: skip, never crash MO
        mgr._build_index()
        return mgr

    def _build_index(self) -> None:
        self._index = {}
        for server_name, client in self._clients.items():
            for tool in client.tools:
                tname = str(tool.get("name") or "")
                if tname:
                    self._index[f"mcp__{server_name}__{tname}"] = (server_name, tname)

    # ---- surface ------------------------------------------------------------
    def is_mcp_tool(self, name: str) -> bool:
        """True only for a registered MCP tool (empty when MCP is off)."""
        return name in self._index

    def tool_definitions(self) -> list[dict[str, Any]]:
        defs: list[dict[str, Any]] = []
        for mcp_name, (server_name, tname) in sorted(self._index.items()):
            client = self._clients[server_name]
            tool = next((t for t in client.tools if t.get("name") == tname), {})
            defs.append(
                {
                    "type": "function",
                    "function": {
                        "name": mcp_name,
                        "description": f"[MCP:{server_name}] " + str(tool.get("description") or tname),
                        "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
                    },
                }
            )
        return defs

    def call(self, name: str, arguments: dict | None) -> str:
        entry = self._index.get(name)
        if not entry:
            return f"Error: unknown MCP tool '{name}'"
        server_name, tname = entry
        client = self._clients.get(server_name)
        if not client:
            return f"Error: MCP server '{server_name}' is not available"
        try:
            result = client.call_tool(tname, arguments or {})
        except Exception as exc:
            return f"Error: MCP tool '{name}' failed: {exc}"
        return _render_result(result)

    def shutdown(self) -> None:
        for client in self._clients.values():
            client.stop()
        self._clients = {}
        self._index = {}
