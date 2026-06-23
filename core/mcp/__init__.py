"""MCP (Model Context Protocol) integration for MO — local-first, operator-configured.

Enabled by default but inert until servers are listed. MO bridges configured
tools into the model's tool set under `mcp__<server>__<tool>` ids, gated by the
same sandbox as native tools. No marketplace, no model-side install.
"""
from .client import McpClient, McpError
from .manager import McpManager

__all__ = ["McpClient", "McpManager", "McpError"]
