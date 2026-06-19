"""End-to-end: a real MO agent exposes and dispatches MCP tools when configured."""
from __future__ import annotations

import sys
from pathlib import Path

from core.agent.agent import create_agent

MOCK = str((Path(__file__).parent / "fixtures" / "mock_mcp_server.py").resolve())


def test_agent_exposes_and_dispatches_mcp_tools(tmp_path, monkeypatch):
    home = tmp_path / "mo_home"
    home.mkdir()
    monkeypatch.setenv("MO_HOME", str(home))
    # Hermetic: the provider config below references api_key_env OPENCODE_API_KEY for
    # agent construction, but the test never makes a real API call (base_url is invalid
    # and only MCP/native tool dispatch is exercised). Inject a dummy so the suite stays
    # green on a clean clone / CI without the live key in the ambient env.
    monkeypatch.setenv("OPENCODE_API_KEY", "test-dummy-key")

    py = sys.executable.replace("\\", "/")
    mock = MOCK.replace("\\", "/")
    home_fs = str(home).replace("\\", "/")
    (home / "config.yaml").write_text(
        f"""providers:
  - name: opencode
    type: chat_completions
    base_url: https://example.invalid/v1
    api_key_env: OPENCODE_API_KEY
    model: deepseek-v4-pro
model:
  default: deepseek-v4-pro
runtime:
  home: {home_fs}
  state: private
mcp:
  enabled: true
  servers:
    - name: mock
      command: {py}
      args: ["{mock}"]
""",
        encoding="utf-8",
    )

    agent = create_agent(str(home / "config.yaml"))
    try:
        assert agent.mcp_manager is not None
        names = {d["function"]["name"] for d in agent.tool_definitions}
        assert "mcp__mock__echo" in names
        assert "mcp__mock__add" in names
        # dispatch routes through agent -> manager -> live subprocess server
        assert agent._dispatch_tool("mcp__mock__echo", {"text": "ping"}) == "ping"
        assert agent._dispatch_tool("mcp__mock__add", {"a": 1, "b": 2}) == "3"
        # native tools still resolve
        assert "Unknown tool" not in agent._dispatch_tool("git_status", {})
    finally:
        mgr = getattr(agent, "mcp_manager", None)
        if mgr:
            mgr.shutdown()
