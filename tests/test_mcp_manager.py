"""Tests for the MCP manager: config gating, tool namespacing, routing, degraded."""
from __future__ import annotations

import sys
from pathlib import Path

from core.mcp import McpManager

MOCK = str(Path(__file__).parent / "fixtures" / "mock_mcp_server.py")


def _cfg(enabled=True, command=None, args=None):
    return {
        "mcp": {
            "enabled": enabled,
            "servers": [
                {"name": "mock", "command": command or sys.executable,
                 "args": args if args is not None else [MOCK]}
            ],
        }
    }


def test_disabled_is_empty():
    mgr = McpManager.from_config(_cfg(enabled=False))
    try:
        assert mgr.tool_definitions() == []
        assert not mgr.is_mcp_tool("mcp__mock__echo")
    finally:
        mgr.shutdown()


def test_no_mcp_config_is_empty():
    mgr = McpManager.from_config({})
    assert mgr.tool_definitions() == []
    mgr.shutdown()


def test_exposes_namespaced_tools():
    mgr = McpManager.from_config(_cfg())
    try:
        defs = mgr.tool_definitions()
        names = {d["function"]["name"] for d in defs}
        assert "mcp__mock__echo" in names and "mcp__mock__add" in names
        assert mgr.is_mcp_tool("mcp__mock__echo")
        assert not mgr.is_mcp_tool("read_file")
        echo_def = next(d for d in defs if d["function"]["name"] == "mcp__mock__echo")
        assert echo_def["function"]["description"].startswith("[MCP:mock]")
        assert echo_def["function"]["parameters"]["type"] == "object"
    finally:
        mgr.shutdown()


def test_routes_calls_to_server():
    mgr = McpManager.from_config(_cfg())
    try:
        assert mgr.call("mcp__mock__echo", {"text": "yo"}) == "yo"
        assert mgr.call("mcp__mock__add", {"a": 4, "b": 5}) == "9"
        assert "unknown MCP tool" in mgr.call("mcp__mock__missing", {})
    finally:
        mgr.shutdown()


def test_bad_server_is_degraded_not_crash():
    cfg = {"mcp": {"enabled": True, "servers": [
        {"name": "broken", "command": "definitely_not_a_real_cmd_xyz", "args": []}
    ]}}
    mgr = McpManager.from_config(cfg)  # must NOT raise
    try:
        assert mgr.tool_definitions() == []
        assert "broken" in mgr.degraded
    finally:
        mgr.shutdown()
