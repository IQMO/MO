"""End-to-end tests for the MCP stdio client against a real mock server."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.mcp import McpClient

MOCK = str(Path(__file__).parent / "fixtures" / "mock_mcp_server.py")


def _client():
    return McpClient("mock", sys.executable, [MOCK], timeout=10)


def test_start_lists_tools():
    c = _client()
    try:
        c.start()
        names = {t["name"] for t in c.tools}
        assert {"echo", "add"} <= names
    finally:
        c.stop()


def test_call_echo_and_add():
    c = _client()
    try:
        c.start()
        echo = c.call_tool("echo", {"text": "hi"})
        assert any(x.get("text") == "hi" for x in echo.get("content", []))
        added = c.call_tool("add", {"a": 2, "b": 3})
        assert any(x.get("text") == "5" for x in added.get("content", []))
    finally:
        c.stop()


def test_mcp_server_env_is_sanitized(monkeypatch):
    monkeypatch.setenv("MO_TEST_SECRET_TOKEN", "visible-secret")
    c = _client()
    try:
        c.start()
        value = c.call_tool("env", {"name": "MO_TEST_SECRET_TOKEN"})
        texts = [x.get("text") for x in value.get("content", [])]
        assert "visible-secret" not in texts
        assert "missing" in texts
    finally:
        c.stop()


def test_bad_command_raises_on_start():
    c = McpClient("broken", "definitely_not_a_real_cmd_xyz", [], timeout=5)
    with pytest.raises(Exception):
        c.start()
