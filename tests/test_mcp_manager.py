"""Tests for the MCP manager: config gating, tool namespacing, routing, degraded."""
from __future__ import annotations

import sys
import atexit
from pathlib import Path

from core.agent.agent import Agent
from core.mcp import McpManager
from core.tool_registry import DeferredToolRegistry

MOCK = str(Path(__file__).parent / "fixtures" / "mock_mcp_server.py")


def _cfg(enabled=True, command=None, args=None):
    mcp = {
        "servers": [
            {"name": "mock", "command": command or sys.executable,
             "args": args if args is not None else [MOCK]}
        ],
    }
    if enabled is not None:
        mcp["enabled"] = enabled
    return {"mcp": mcp}


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


def test_mcp_config_defaults_enabled_but_empty_servers_are_inert():
    mgr = McpManager.from_config({"mcp": {"servers": []}})
    assert mgr.tool_definitions() == []
    mgr.shutdown()


def test_mcp_servers_do_not_require_enabled_true():
    mgr = McpManager.from_config(_cfg(enabled=None))
    try:
        names = {d["function"]["name"] for d in mgr.tool_definitions()}
        assert "mcp__mock__echo" in names
    finally:
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


def test_agent_starts_mcp_lazily_when_provider_tools_are_requested(monkeypatch):
    calls = []
    registered = []
    mcp_def = {
        "type": "function",
        "function": {"name": "mcp__mock__echo", "description": "[MCP:mock] echo", "parameters": {"type": "object"}},
    }

    class FakeManager:
        degraded = []

        def tool_definitions(self):
            return [mcp_def]

        def shutdown(self):
            calls.append("shutdown")

    fake_manager = FakeManager()

    def fake_from_config(cls, config):
        calls.append(config)
        return fake_manager

    monkeypatch.setattr(McpManager, "from_config", classmethod(fake_from_config))
    monkeypatch.setattr(atexit, "register", lambda fn: registered.append(fn))

    agent = Agent.__new__(Agent)
    agent.config = _cfg()
    agent.tool_definitions = [
        {
            "type": "function",
            "function": {"name": "read_file", "description": "read", "parameters": {"type": "object"}},
        }
    ]
    agent.deferred_tool_registry_enabled = True
    agent._tool_registry = DeferredToolRegistry(agent.tool_definitions)
    agent.mcp_manager = None
    agent._mcp_manager_initialized = False

    assert agent.mcp_manager is None
    defs = Agent._provider_tool_definitions(agent)

    assert defs
    assert "mcp__mock__echo" in agent._tool_registry.catalog_names()
    assert agent.mcp_manager is fake_manager
    assert registered == [fake_manager.shutdown]
    assert calls == [agent.config]
