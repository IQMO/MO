import json
from types import SimpleNamespace

import pytest

from core.agent.agent import Agent
from core.backend_monitor import BackendMonitor
from core.provider.provider_capacity import reset_capacity
from core.workers import WorkerRegistry


@pytest.fixture(autouse=True)
def _reset_capacity():
    reset_capacity()


class FakeSession:
    def __init__(self):
        self.extra_context = None
        self.usage = []
        self.messages = []
        self.turn_count = 0
        self.session_id = "fake-session"

    def get_messages(self, extra_context=None):
        self.extra_context = extra_context
        return [{"role": "system", "content": extra_context or ""}]

    def record_usage(self, **kwargs):
        self.usage.append(kwargs)

    def add_user(self, content):
        self.messages.append({"role": "user", "content": content})

    def add_assistant(self, content):
        self.messages.append({"role": "assistant", "content": content})


class FakeProvider:
    api_mode = "chat"

    def __init__(self, name, model, content, *, raises=None, finish_reason="stop"):
        self.name = name
        self.model = model
        self.content = content
        self.raises = raises
        self.finish_reason = finish_reason
        self.calls = 0
        self.tools = None

    def complete(self, **kwargs):
        self.calls += 1
        self.tools = kwargs.get("tools")
        if self.raises:
            raise self.raises
        return SimpleNamespace(content=self.content, usage=None, finish_reason=self.finish_reason)


def make_agent(providers, config=None):
    agent = Agent.__new__(Agent)
    agent.providers = providers
    agent.provider_index = 0
    agent.provider_name = providers[0].name
    agent.model = providers[0].model
    agent.temperature = 0
    agent.max_tokens = 1000
    agent.session = FakeSession()
    agent.config = config or {"agent": {}}
    return agent


def test_ghost_prefers_configured_provider_without_switching_main_model():
    main = FakeProvider("main", "slow-main", "main")
    ghost = FakeProvider("gemini", "gemini-2.5-flash", "ghost proposal")
    agent = make_agent([main, ghost], {"agent": {"ghost_provider": "gemini", "ghost_model": "gemini-2.5-flash"}})

    result = agent.propose_work("build a thing")

    assert result == "ghost proposal"
    assert ghost.calls == 1
    assert main.calls == 0
    assert agent.provider_name == "main"
    assert agent.model == "slow-main"


def test_ghost_falls_back_to_flash_model_when_no_config():
    main = FakeProvider("main", "slow-main", "main")
    ghost = FakeProvider("fast", "some-flash-model", "side check")
    agent = make_agent([main, ghost])

    result = agent._cmd_ghost("check this")

    assert result == "side check"
    assert ghost.calls == 1
    assert main.calls == 0
    assert ghost.tools == []
    records = agent.workers.recent()
    assert records[-1].kind == "ghost"
    assert records[-1].state == "completed"


def test_ghost_status_uses_user_facing_wording():
    main = FakeProvider("main", "slow-main", "main")
    ghost = FakeProvider("fast", "some-flash-model", "side check")
    agent = make_agent([main, ghost])
    agent.ghost_enabled = True

    status = agent._cmd_ghost("")

    assert status.startswith("Ghost:")
    assert "Gateway" not in status
    assert "provider:" not in status
    assert "task progress" in status


def test_ghost_provider_chain_is_flash_pro_codex_only():
    main = FakeProvider("opencode", "deepseek-v4-pro", "main")
    fallback = FakeProvider("openai-codex", "gpt-5.5", "fallback")
    free_flash = FakeProvider("free-router", "deepseek-v4-flash-free", "free")
    flash = FakeProvider("opencode-flash", "deepseek-v4-flash", "flash")
    agent = make_agent([main, fallback, free_flash, flash])

    chain = [(provider.name, provider.model) for provider in agent.providers_for_surface("ghost_panel")]

    assert chain == [
        ("opencode-flash", "deepseek-v4-flash"),
        ("opencode", "deepseek-v4-pro"),
        ("openai-codex", "gpt-5.5"),
    ]
    assert ("free-router", "deepseek-v4-flash-free") not in chain


def test_ghost_provider_chain_keeps_pro_before_codex_if_main_switches():
    pro = FakeProvider("opencode", "deepseek-v4-pro", "pro")
    codex = FakeProvider("openai-codex", "gpt-5.5", "codex")
    flash = FakeProvider("opencode-flash", "deepseek-v4-flash", "flash")
    agent = make_agent([pro, codex, flash])
    agent.provider_index = 1
    agent.provider_name = codex.name
    agent.model = codex.model

    chain = [(provider.name, provider.model) for provider in agent.providers_for_surface("ghost_panel")]

    assert chain[:3] == [
        ("opencode-flash", "deepseek-v4-flash"),
        ("opencode", "deepseek-v4-pro"),
        ("openai-codex", "gpt-5.5"),
    ]


def test_review_provider_chain_is_pro_and_codex_only():
    current = FakeProvider("opencode", "deepseek-v4-flash", "current")
    bigpickle = FakeProvider("opencode-bigpickle", "big-pickle", "big")
    anthropic = FakeProvider("anthropic", "claude-opus-4-7", "anthropic")
    codex = FakeProvider("openai-codex", "gpt-5.5", "codex")
    flash = FakeProvider("opencode-flash", "deepseek-v4-flash", "flash")
    pro = FakeProvider("opencode-pro", "deepseek-v4-pro", "pro")
    agent = make_agent(
        [current, bigpickle, anthropic, codex, flash, pro],
        {"prt": {"default_model": "deepseek-v4-pro", "fallback_model": "codex"}, "model": {"fallback": "openai-codex"}},
    )

    chain = [(provider.name, provider.model) for provider in agent.providers_for_surface("review")]

    assert chain == [
        ("opencode-pro", "deepseek-v4-pro"),
        ("openai-codex", "gpt-5.5"),
    ]


def test_review_no_tools_skips_anthropic_and_falls_back_to_codex_after_deepseek():
    current = FakeProvider("opencode", "deepseek-v4-flash", "", finish_reason="length")
    anthropic = FakeProvider("anthropic", "claude-opus-4-7", "anthropic")
    codex = FakeProvider("openai-codex", "gpt-5.5", "[]")
    flash = FakeProvider("opencode-flash", "deepseek-v4-flash", "", finish_reason="length")
    pro = FakeProvider("opencode-pro", "deepseek-v4-pro", "", finish_reason="length")
    agent = make_agent(
        [current, anthropic, codex, flash, pro],
        {"prt": {"default_model": "deepseek-v4-pro", "fallback_model": "codex"}, "model": {"fallback": "openai-codex"}},
    )

    response, provider = agent.complete_ghost_no_tools(
        surface="review",
        request="review-test",
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "diff"}],
        max_tokens=100,
    )

    assert provider is codex
    assert response.content == "[]"
    assert pro.calls == 1
    assert current.calls == 0
    assert flash.calls == 0
    assert anthropic.calls == 0
    assert codex.calls == 1


def test_main_provider_fallback_skips_flash_when_codex_is_configured_fallback():
    pro = FakeProvider("opencode", "deepseek-v4-pro", "pro")
    codex = FakeProvider("openai-codex", "gpt-5.5", "codex")
    flash = FakeProvider("opencode-flash", "deepseek-v4-flash", "flash")
    duplicate_pro = FakeProvider("opencode-pro", "deepseek-v4-pro", "duplicate pro")
    agent = make_agent(
        [pro, codex, flash, duplicate_pro],
        {"model": {"default": "deepseek-v4-pro", "fallback": "openai-codex"}, "agent": {}},
    )
    agent.context_budget_config = "auto"
    agent.context_reserve_tokens = 1000
    agent.tool_compress_enabled = False

    assert agent._next_provider("test") is True
    assert agent.active_provider is codex
    assert agent._next_provider("test-again") is False
    assert agent.active_provider is codex
    assert flash.calls == 0
    assert duplicate_pro.calls == 0


def test_prt_adaptive_action_suggests_large_and_medium_without_auto_run():
    assert Agent._prt_adaptive_action("simple", "low", {"auto_review_large": True}) == ""
    assert Agent._prt_adaptive_action("moderate", "low", {"ghost_suggest_medium": True}) == "suggest"
    assert Agent._prt_adaptive_action("simple", "medium", {"ghost_suggest_medium": True}) == "suggest"
    assert Agent._prt_adaptive_action("complex", "low", {"auto_review_large": True}) == "suggest"
    assert Agent._prt_adaptive_action("simple", "high", {"auto_review_large": True}) == "suggest"
    assert Agent._prt_adaptive_action("complex", "low", {"ghost_suggest_medium": False}) == ""
    assert Agent._prt_adaptive_action("moderate", "low", {"ghost_suggest_medium": False}) == ""


def test_ghost_no_tools_falls_back_from_flash_error_to_deepseek_pro():
    main = FakeProvider("opencode", "deepseek-v4-pro", "main ok")
    flash = FakeProvider("opencode-flash", "deepseek-v4-flash", "", raises=RuntimeError("500"))
    fallback = FakeProvider("openai-codex", "gpt-5.5", "fallback ok")
    agent = make_agent([main, fallback, flash])

    response, provider = agent.complete_ghost_no_tools(
        surface="ghost_panel",
        request="ghost-test",
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "ask"}],
        max_tokens=100,
    )

    assert provider is main
    assert response.content == "main ok"
    assert flash.calls == 1
    assert main.calls == 1
    assert fallback.calls == 0


def test_ghost_no_tools_emits_backend_monitor_surface(tmp_path):
    ghost = FakeProvider("opencode-flash", "deepseek-v4-flash", "ghost ok")
    agent = make_agent([ghost])
    monitor = BackendMonitor(tmp_path / "backend_monitor.jsonl")

    response, provider = agent.complete_ghost_no_tools(
        surface="ghost_panel",
        request="ghost-test",
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "ask"}],
        max_tokens=100,
        monitor=monitor,
    )

    assert provider is ghost
    assert response.content == "ghost ok"
    rows = [json.loads(line) for line in monitor.path.read_text(encoding="utf-8").splitlines()]
    assert rows[0]["type"] == "provider_request"
    assert rows[0]["payload"]["surface"] == "ghost_panel"
    assert rows[0]["payload"]["tools"] == 0
    assert rows[1]["type"] == "provider_response"
    assert rows[1]["payload"]["surface"] == "ghost_panel"



def test_ghost_no_tools_falls_back_from_empty_flash_to_deepseek_pro():
    main = FakeProvider("opencode", "deepseek-v4-pro", "main ok")
    flash = FakeProvider("opencode-flash", "deepseek-v4-flash", "", finish_reason="length")
    agent = make_agent([main, flash])

    response, provider = agent.complete_ghost_no_tools(
        surface="ghost_panel",
        request="ghost-test",
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "ask"}],
        max_tokens=100,
    )

    assert provider is main
    assert response.content == "main ok"
    assert flash.calls == 1
    assert main.calls == 1

    second_response, second_provider = agent.complete_ghost_no_tools(
        surface="ghost_panel",
        request="ghost-test-2",
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "ask again"}],
        max_tokens=100,
    )

    assert second_provider is main
    assert second_response.content == "main ok"
    assert flash.calls == 1
    assert main.calls == 2


def test_ghost_proposal_unavailable_emits_status_and_continues():
    class Monitor:
        def __init__(self):
            self.events = []

        def emit(self, typ, payload):
            self.events.append((typ, payload))

    ghost = FakeProvider("opencode-flash", "deepseek-v4-flash", "", finish_reason="length")
    agent = make_agent([ghost])
    monitor = Monitor()

    result = agent.propose_work("build a thing", monitor=monitor)

    assert result == ""
    assert ("backend_status", {"message": "ghost intent handoff unavailable; continuing without proposal"}) in monitor.events


def test_agent_live_steer_is_provider_context_only_and_consumed_once():
    class Monitor:
        def __init__(self):
            self.events = []

        def emit(self, typ, payload):
            self.events.append((typ, payload))

    agent = Agent.__new__(Agent)
    agent.workers = WorkerRegistry()
    record = agent.workers.create(kind="queue", source="ghost", route="queue", objective="make it keyboard only", state="accepted")
    monitor = Monitor()

    steer_id = agent.add_live_steer("make it keyboard only", source="ghost", worker_id=record.id)
    context = agent._consume_live_steer_context(monitor=monitor)
    second = agent._consume_live_steer_context(monitor=monitor)

    assert steer_id == record.id
    assert "Live Operator Steering Update" in context
    assert "make it keyboard only" in context
    assert "provider context only" in context
    assert second == ""
    assert agent.workers.get(record.id).state == "completed"
    assert monitor.events and monitor.events[0][0] == "live_steer"
