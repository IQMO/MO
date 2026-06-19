"""Adaptive reasoning: per-turn level (Fable 'auto' thinking mode) + the opt-in
per-provider reasoning_effort seam (default off → not sent, no breakage)."""
from types import SimpleNamespace

from core.agent.agent import Agent
from core.provider.provider import ChatCompletionsProvider, CodexOAuthProvider


# ── adaptive level ──────────────────────────────────────────────────

def _agent(base="high"):
    a = object.__new__(Agent)
    a.reasoning = base
    return a


def test_trivial_turns_drop_to_low():
    a = _agent("high")
    for t in ("hi", "who are you?", "what does VS05 mean?"):
        assert a._adaptive_reasoning_level(t) == "low", t


def test_work_turns_keep_ceiling():
    a = _agent("high")
    for t in ("fix the auth bug", "refactor the session module", "audit this diff"):
        assert a._adaptive_reasoning_level(t) == "high", t


def test_reasoning_context_reflects_adaptive_level():
    a = _agent("high")
    assert "Reasoning level: low" in a._reasoning_context("hi")
    assert "Reasoning level: high" in a._reasoning_context("implement the cache")


def test_base_low_stays_low():
    a = _agent("low")
    assert a._adaptive_reasoning_level("fix the bug") == "low"


# ── provider seam: default off, opt-in on ───────────────────────────

def test_chat_provider_default_does_not_send_reasoning():
    p = ChatCompletionsProvider(name="x", base_url="http://localhost", api_key="k", model="m")
    assert p.reasoning_effort is None


def test_chat_provider_reasoning_effort_opt_in():
    p = ChatCompletionsProvider(name="x", base_url="http://localhost", api_key="k", model="m", reasoning_effort="High")
    assert p.reasoning_effort == "high"  # normalized


def test_provider_from_config_threads_reasoning_effort():
    from core.provider.provider import _provider_from_config
    p = _provider_from_config(
        {"name": "x", "type": "chat_completions", "base_url": "http://localhost",
         "api_key": "k", "reasoning_effort": "medium"},
        model="m",
    )
    assert p.reasoning_effort == "medium"
