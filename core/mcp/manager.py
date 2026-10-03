"""MCP manager — lifecycle + tool bridging for operator-configured MCP servers.

Reads `config.mcp`, starts enabled servers when the agent asks for the MCP tool
catalog, aggregates their tools under a namespaced id
(`mcp__<server>__<tool>`), exposes them as MO tool definitions, and routes
calls. A server that fails to start is reported degraded and skipped — it never
crashes MO. Explicit `mcp.enabled: false` disables the bridge.
"""
from __future__ import annotations

from core.state.configuration_defaults import DEFAULT_PREFERENCES

import json
from typing import Any

from .codebase_memory import exposed_tools_for_server
from .client import McpClient
from ..state.secrets import resolve_secret


_SECRET_ENV_MARKERS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "AUTH",
    "COOKIE",
    "CREDENTIAL",
)


def _looks_like_secret_env_name(name: Any) -> bool:
    upper = str(name or "").strip().upper()
    return any(marker in upper for marker in _SECRET_ENV_MARKERS)


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
    def __init__(self, clients: list[McpClient] | None = None,
                 tool_allowlists: dict[str, set[str] | frozenset[str]] | None = None):
        self._clients: dict[str, McpClient] = {c.name: c for c in (clients or [])}
        self._tool_allowlists: dict[str, frozenset[str]] = {
            str(name): frozenset(str(tool) for tool in tools if str(tool).strip())
            for name, tools in (tool_allowlists or {}).items()
        }
        self._index: dict[str, tuple[str, str]] = {}  # mcp_name -> (server, tool)
        self.degraded: list[str] = []  # server names that failed to start
        self._build_index()

    @classmethod
    def from_config(cls, config: dict | None) -> "McpManager":
        mcp_cfg = ((config or {}).get("mcp") or {}) if isinstance(config, dict) else {}
        mgr = cls([])
        if mcp_cfg.get("enabled", DEFAULT_PREFERENCES["mcp.enabled"]) is False:
            return mgr
        for spec in mcp_cfg.get("servers") or []:
            if not isinstance(spec, dict) or spec.get("enabled") is False:
                continue
            name = str(spec.get("name") or "").strip()
            command = spec.get("command")
            if not name or not command:
                continue
            if "allow_tools" in spec:
                raw_allow = spec.get("allow_tools")
                if isinstance(raw_allow, str):
                    raw_allow = [raw_allow]
                if isinstance(raw_allow, (list, tuple, set, frozenset)):
                    mgr._tool_allowlists[name] = frozenset(
                        str(tool).strip() for tool in raw_allow if str(tool).strip()
                    )
                else:
                    # A malformed explicit allowlist must fail closed rather than
                    # accidentally exposing every tool from the server.
                    mgr._tool_allowlists[name] = frozenset()
            raw_env = spec.get("env") or {}
            if not isinstance(raw_env, dict):
                mgr.degraded.append(name)
                continue
            if any(_looks_like_secret_env_name(key) for key in raw_env):
                # Secrets belong in the service's canonical profile file and
                # must be selected through secret_env. Never let a config value
                # become a second child-credential source.
                mgr.degraded.append(name)
                continue
            child_env = {
                str(key): str(value)
                for key, value in raw_env.items()
            }
            # Secret values are selected one-by-one and injected only into this
            # child process. They never enter the model-visible config or MO's
            # global process environment.
            secret_env = spec.get("secret_env") or {}
            if isinstance(secret_env, dict):
                service = str(spec.get("secret_service") or name).strip()
                injected = False
                for child_name, logical_key in secret_env.items():
                    value = resolve_secret(
                        str(logical_key or ""),
                        config=config,
                        service=service,
                    )
                    if value:
                        child_env[str(child_name)] = value
                        injected = True
                if injected:
                    # Standalone servers can distinguish broker delivery from
                    # arbitrary inherited shell credentials. The marker carries
                    # no secret value and safe_env() never inherits it.
                    child_env["MO_SCOPED_CREDENTIAL_SERVICE"] = service
            client_kwargs: dict[str, Any] = {
                "name": name,
                "command": command,
                "args": spec.get("args") or [],
                "env": child_env,
                "timeout": float(spec.get("timeout", 30.0) or 30.0),
            }
            if spec.get("cwd"):
                client_kwargs["cwd"] = str(spec.get("cwd"))
            client = McpClient(
                **client_kwargs,
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
            tools = list(getattr(client, "tools", []) or [])
            allowed = exposed_tools_for_server(server_name, tools)
            configured = self._tool_allowlists.get(server_name)
            if configured is not None:
                allowed = configured if allowed is None else allowed.intersection(configured)
            for tool in client.tools:
                tname = str(tool.get("name") or "")
                if allowed is not None and tname not in allowed:
                    continue
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
        self._tool_allowlists = {}
