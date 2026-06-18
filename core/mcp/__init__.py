"""MCP (Model Context Protocol) integration for MO — local-first, operator-configured.

Off by default. Servers are defined in `config.mcp.servers`; MO bridges their
tools into the model's tool set under `mcp__<server>__<tool>` ids, gated by the
same sandbox as native tools. No marketplace, no model-side install.
"""
from .client import McpClient, McpError
from .manager import McpManager

__all__ = ["McpClient", "McpManager", "McpError"]
