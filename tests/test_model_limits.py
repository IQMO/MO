from core.model_limits import context_budget_source, resolve_context_budget_tokens


def test_deepseek_v4_alias_uses_upstream_1m_context_minus_reserve():
    budget = resolve_context_budget_tokens("auto", provider="opencode", model="deepseek-v4-pro")
    source = context_budget_source("auto", provider="opencode", model="deepseek-v4-pro")

    assert budget == 1_000_000 - 16_384
    assert source == "deepseek_api_docs_1m_context"


def test_unknown_deepseek_alias_stays_conservative():
    budget = resolve_context_budget_tokens("auto", provider="opencode", model="deepseek-chat")
    source = context_budget_source("auto", provider="opencode", model="deepseek-chat")

    assert budget == 128_000 - 16_384
    assert source == "deepseek_family_conservative_default"
