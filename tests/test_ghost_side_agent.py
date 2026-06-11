from types import SimpleNamespace

from core.agent.agent import Agent
from interface.ghost import GHOST_SIDECHAT_SYSTEM, ghost_safe_messages


class FakeSession:
    def __init__(self):
        self.extra_context = None
        self.recorded_usage = False

    def get_messages(self, extra_context=None):
        self.extra_context = extra_context
        return [{"role": "system", "content": "base system\n" + (extra_context or "")}]

    def record_usage(self, **_kwargs):
        self.recorded_usage = True


class FakeProvider:
    name = "fake"
    model = "fake-model"
    api_mode = "chat"

    def __init__(self):
        self.messages = None
        self.tools = None

    def complete(self, **kwargs):
        self.messages = kwargs.get("messages")
        self.tools = kwargs.get("tools")
        joined = "\n".join(str(m.get("content", "")) for m in self.messages)
        assert "separate side agent" in joined
        assert "You are not the main MO" in joined
        assert "Operator side-question for Ghost" in joined
        return SimpleNamespace(content="side check", usage=None)


def test_agent_ghost_safe_messages_strip_tool_calls():
    raw = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "result"},
        {"role": "assistant", "content": "visible"},
    ]

    messages = Agent._ghost_safe_messages(raw, "ask")

    assert all(m["role"] != "tool" for m in messages)
    assert all("tool_calls" not in m for m in messages)
    assert messages[-1]["content"] == "ask"


def test_agent_and_panel_use_shared_ghost_safe_message_builder():
    raw = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "result"},
        {"role": "assistant", "content": "visible"},
    ]

    assert Agent._ghost_safe_messages(raw, "ask") == ghost_safe_messages(raw, "ask")


def test_ghost_side_agent_prompt_does_not_claim_taskboard_authority():
    assert "cannot close" in GHOST_SIDECHAT_SYSTEM.lower()
    assert "gateway" in GHOST_SIDECHAT_SYSTEM.lower()


def test_agent_ghost_command_uses_side_agent_prompt_not_main_mo_identity():
    provider = FakeProvider()
    session = FakeSession()
    agent = Agent.__new__(Agent)
    agent.providers = [provider]
    agent.provider_index = 0
    agent.provider_name = provider.name
    agent.model = provider.model
    agent.temperature = 0
    agent.max_tokens = 800
    agent.session = session
    agent.config = {"agent": {}}

    result = agent._cmd_ghost("was main MO right?")

    assert result == "side check"
    # Ghost uses GHOST_SIDECHAT_SYSTEM in its system prompt
    assert len(provider.messages) > 0
    system_msg = provider.messages[0]
    assert system_msg["role"] == "system"
    assert GHOST_SIDECHAT_SYSTEM in system_msg["content"]
