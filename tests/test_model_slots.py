from types import SimpleNamespace

from core.agent.agent import Agent
from core.model_slots import main_model_selectors, provider_matches_selector, resolve_model_slot


def _provider(name, model, api_mode="chat"):
    return SimpleNamespace(name=name, model=model, api_mode=api_mode)


def test_main_slot_exposes_active_provider_and_config_selectors():
    primary = _provider("primary", "pro")
    fallback = _provider("fallback", "codex")

    resolution = resolve_model_slot(
        "main",
        [primary, fallback],
        active_provider=primary,
        config={"model": {"default": "pro", "fallback": "fallback"}},
    )

    assert resolution.slot == "main"
    assert resolution.providers == (primary,)
    assert resolution.selectors == ("pro", "fallback")
    assert main_model_selectors({"model": {"default": "pro", "fallback": "fallback"}}) == ("pro", "fallback")


def test_ghost_slot_honors_configured_provider_before_default_chain():
    main = _provider("main", "deepseek-v4-pro")
    configured = _provider("anthropic", "claude-haiku-4-5")
    flash = _provider("opencode-flash", "deepseek-v4-flash")
    codex = _provider("openai-codex", "gpt-5.5", "codex_responses")

    resolution = resolve_model_slot(
        "ghost_panel",
        [main, configured, flash, codex],
        active_provider=main,
        config={"agent": {"ghost_provider": "anthropic", "ghost_model": "claude-haiku-4-5"}},
    )

    assert resolution.slot == "ghost"
    assert resolution.source == "ghost_config"
    assert resolution.providers[:4] == (configured, flash, main, codex)


def test_ghost_proposal_slot_uses_pro_not_deepseek_flash_by_default():
    main = _provider("deepseek", "deepseek-v4-pro")
    flash = _provider("opencode-flash", "deepseek-v4-flash")
    codex = _provider("openai-codex", "gpt-5.5", "codex_responses")

    resolution = resolve_model_slot(
        "ghost_proposal",
        [flash, main, codex],
        active_provider=flash,
        config={},
    )

    assert resolution.slot == "ghost_proposal"
    assert resolution.providers == (main, codex)


def test_review_slot_uses_configured_review_chain_builder():
    pro = _provider("opencode-pro", "deepseek-v4-pro")
    codex = _provider("openai-codex", "gpt-5.5", "codex_responses")
    flash = _provider("opencode-flash", "deepseek-v4-flash")

    def builder(providers, *, active_provider, default_model, fallback_model):
        assert default_model == "deepseek-v4-pro"
        assert fallback_model == "codex"
        return [provider for provider in providers if provider is pro or provider is codex]

    resolution = resolve_model_slot(
        "review",
        [flash, codex, pro],
        active_provider=flash,
        config={"prt": {"default_model": "deepseek-v4-pro", "fallback_model": "codex"}},
        review_chain_builder=builder,
    )

    assert resolution.providers == (codex, pro)
    assert resolution.selectors == ("deepseek-v4-pro", "codex")


def test_agent_consumes_model_slot_resolver_for_surface_routing():
    main = _provider("main", "deepseek-v4-pro")
    ghost = _provider("fast", "deepseek-v4-flash")
    agent = Agent.__new__(Agent)
    agent.providers = [main, ghost]
    agent.provider_index = 0
    agent.config = {}

    chain = agent.providers_for_surface("ghost_panel")

    assert chain == [ghost, main]
    assert agent._last_model_slot_resolution.slot == "ghost"


def test_provider_selector_matches_name_model_or_pair():
    provider = _provider("openai-codex", "gpt-5.5")

    assert provider_matches_selector(provider, "openai-codex")
    assert provider_matches_selector(provider, "gpt-5.5")
    assert provider_matches_selector(provider, "openai-codex/gpt-5.5")
    assert not provider_matches_selector(provider, "deepseek-v4-pro")
