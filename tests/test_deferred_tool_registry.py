import json

import tools
from core.agent.agent import Agent
from core.tool_registry import DeferredToolRegistry


def _names(definitions):
    return [
        (definition.get("function") or {}).get("name") or definition.get("name")
        for definition in definitions
    ]


def test_deferred_tool_registry_starts_with_small_core():
    registry = DeferredToolRegistry(tools.TOOL_DEFINITIONS)

    active = registry.active_names()

    assert "tool_search" in active
    assert "read_file" in active
    assert "grep" in active
    assert "complete_task" in active
    assert "edit_file" not in active
    assert "shell" not in active
    assert len(active) < len(tools.TOOL_DEFINITIONS)


def test_tool_search_activates_matching_deferred_tools():
    registry = DeferredToolRegistry(tools.TOOL_DEFINITIONS)

    result = json.loads(registry.search({"tools": ["edit_file", "test_runner"]}))

    assert result["activated"] == ["edit_file", "test_runner"]
    assert "edit_file" in registry.active_names()
    assert "test_runner" in registry.active_names()
    assert registry.snapshot()["activated_tools"] == ["edit_file", "test_runner"]


def test_tool_search_query_ranks_and_activates_shell_tool():
    registry = DeferredToolRegistry(tools.TOOL_DEFINITIONS)

    result = json.loads(registry.search({"query": "run shell command", "activate_limit": 1}))

    assert result["activated"]
    assert result["activated"][0] == "shell"
    assert "shell" in registry.active_names()


def test_agent_provider_tools_use_deferred_registry_and_reset_per_turn():
    agent = object.__new__(Agent)
    agent.deferred_tool_registry_enabled = True
    agent.tool_definitions = list(tools.TOOL_DEFINITIONS)
    agent._tool_registry = DeferredToolRegistry(agent.tool_definitions)

    active = _names(agent._provider_tool_definitions())
    assert "tool_search" in active
    assert "edit_file" not in active

    json.loads(agent._execute_tool_search({"tools": ["edit_file"]}))
    assert "edit_file" in _names(agent._provider_tool_definitions())

    agent._reset_deferred_tools_for_turn()
    assert "edit_file" not in _names(agent._provider_tool_definitions())


def test_agent_respects_explicit_tool_definition_override():
    agent = object.__new__(Agent)
    agent.deferred_tool_registry_enabled = True
    agent.tool_definitions = [
        {
            "type": "function",
            "function": {
                "name": "shell",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    agent._tool_registry = DeferredToolRegistry(tools.TOOL_DEFINITIONS)

    assert _names(agent._provider_tool_definitions()) == ["shell"]
