"""Tests for core/provider_capacity.py — rate-limit tracking and provider capacity."""

import time
from types import SimpleNamespace

from core.provider.provider_capacity import (
    ProviderCapacity,
    get_capacity,
    reset_capacity,
    DEFAULT_ERROR_BLOCK_SECONDS,
    UNKNOWN_RESET_GRACE_SECONDS,
)


# ── Unit tests: ProviderCapacity ───────────────────────────────────────

class TestCanAccept:
    """Tests for can_accept()."""

    def test_unknown_provider_is_accepted(self):
        cap = ProviderCapacity()
        assert cap.can_accept("openai") is True

    def test_after_record_error_provider_is_blocked(self):
        cap = ProviderCapacity()
        cap.record_error("openai", "429 Too Many Requests")
        assert cap.can_accept("openai") is False

    def test_blocked_until_expired_returns_true(self, monkeypatch):
        cap = ProviderCapacity()
        cap.record_error("openai", "429 Too Many Requests")
        # Advance time past the default block
        future = time.time() + DEFAULT_ERROR_BLOCK_SECONDS + 1
        monkeypatch.setattr(time, "time", lambda: future)
        assert cap.can_accept("openai") is True

    def test_blocked_until_not_yet_expired_returns_false(self, monkeypatch):
        cap = ProviderCapacity()
        cap.record_error("openai", "429 Too Many Requests")
        # Advance time to just before expiry
        future = time.time() + DEFAULT_ERROR_BLOCK_SECONDS - 5
        monkeypatch.setattr(time, "time", lambda: future)
        assert cap.can_accept("openai") is False

    def test_remaining_zero_no_reset_blocks_within_grace(self, monkeypatch):
        cap = ProviderCapacity()
        start = time.time()
        monkeypatch.setattr(time, "time", lambda: start)
        cap.record_headers("openai", {"x-ratelimit-remaining-requests": "0"})
        # Still within grace period
        assert cap.can_accept("openai") is False

    def test_remaining_zero_no_reset_allows_after_grace(self, monkeypatch):
        cap = ProviderCapacity()
        start = time.time()
        monkeypatch.setattr(time, "time", lambda: start)
        cap.record_headers("openai", {"x-ratelimit-remaining-requests": "0"})
        # Advance past grace
        monkeypatch.setattr(time, "time", lambda: start + UNKNOWN_RESET_GRACE_SECONDS + 1)
        assert cap.can_accept("openai") is True

    def test_remaining_zero_with_future_reset_blocks(self, monkeypatch):
        cap = ProviderCapacity()
        now = time.time()
        monkeypatch.setattr(time, "time", lambda: now)
        cap.record_headers("openai", {
            "x-ratelimit-remaining-requests": "0",
            "x-ratelimit-reset-requests": str(now + 60),
        })
        # 30s later, still before reset
        monkeypatch.setattr(time, "time", lambda: now + 30)
        assert cap.can_accept("openai") is False

    def test_remaining_zero_reset_passed_allows(self, monkeypatch):
        cap = ProviderCapacity()
        now = time.time()
        monkeypatch.setattr(time, "time", lambda: now)
        cap.record_headers("openai", {
            "x-ratelimit-remaining-requests": "0",
            "x-ratelimit-reset-requests": str(now + 60),
        })
        # After reset
        monkeypatch.setattr(time, "time", lambda: now + 61)
        assert cap.can_accept("openai") is True

    def test_remaining_positive_clears_block(self, monkeypatch):
        cap = ProviderCapacity()
        now = time.time()
        monkeypatch.setattr(time, "time", lambda: now)
        cap.record_error("openai", "429 Too Many Requests")
        assert cap.can_accept("openai") is False
        # Now receive headers with remaining > 0 (no retry-after)
        cap.record_headers("openai", {"x-ratelimit-remaining-requests": "5"})
        assert cap.can_accept("openai") is True

    def test_retry_after_header_blocks_even_with_positive_remaining(self, monkeypatch):
        cap = ProviderCapacity()
        now = time.time()
        monkeypatch.setattr(time, "time", lambda: now)
        cap.record_headers("openai", {
            "x-ratelimit-remaining-requests": "10",
            "retry-after": "30",
        })
        # Retry-After overrides positive remaining
        assert cap.can_accept("openai") is False
        # After Retry-After expires
        monkeypatch.setattr(time, "time", lambda: now + 31)
        assert cap.can_accept("openai") is True

    def test_stale_blocked_until_cleaned_up(self, monkeypatch):
        cap = ProviderCapacity()
        now = time.time()
        monkeypatch.setattr(time, "time", lambda: now)
        cap.record_error("openai", "429 Too Many Requests")
        # blocked_until is set
        assert cap.can_accept("openai") is False
        # Advance past block
        monkeypatch.setattr(time, "time", lambda: now + DEFAULT_ERROR_BLOCK_SECONDS + 1)
        # This should clear stale blocked_until
        cap.can_accept("openai")
        # Verify internal state is clean
        s = cap._state.get("openai")
        assert s is None or s["blocked_until"] is None


class TestRecordHeaders:
    """Tests for record_headers()."""

    def test_parses_remaining_requests_header(self):
        cap = ProviderCapacity()
        cap.record_headers("openai", {"x-ratelimit-remaining-requests": "42"})
        s = cap._state["openai"]
        assert s["remaining"] == 42

    def test_parses_legacy_remaining_header(self):
        cap = ProviderCapacity()
        cap.record_headers("openai", {"x-ratelimit-remaining": "10"})
        s = cap._state["openai"]
        assert s["remaining"] == 10

    def test_parses_remaining_tokens_header(self):
        cap = ProviderCapacity()
        cap.record_headers("openai", {"x-ratelimit-remaining-tokens": "5000"})
        s = cap._state["openai"]
        assert s["remaining"] == 5000

    def test_parses_reset_requests_header(self):
        cap = ProviderCapacity()
        cap.record_headers("openai", {"x-ratelimit-reset-requests": "1711234567.0"})
        s = cap._state["openai"]
        assert s["reset_at"] == 1711234567.0

    def test_parses_retry_after_header(self, monkeypatch):
        cap = ProviderCapacity()
        now = 1000000.0
        monkeypatch.setattr(time, "time", lambda: now)
        cap.record_headers("openai", {"retry-after": "15"})
        s = cap._state["openai"]
        assert s["blocked_until"] == now + 15

    def test_none_headers_is_safe(self):
        cap = ProviderCapacity()
        cap.record_headers("openai", None)
        s = cap._state["openai"]
        assert s["remaining"] is None

    def test_invalid_remaining_value_is_ignored(self):
        cap = ProviderCapacity()
        cap.record_headers("openai", {"x-ratelimit-remaining-requests": "nope"})
        s = cap._state["openai"]
        assert s["remaining"] is None

    def test_priority_order_requests_over_remaining_over_tokens(self):
        """First matching header wins (requests > remaining > tokens)."""
        cap = ProviderCapacity()
        cap.record_headers("openai", {
            "x-ratelimit-remaining-tokens": "9000",
            "x-ratelimit-remaining": "50",
            "x-ratelimit-remaining-requests": "12",
        })
        s = cap._state["openai"]
        assert s["remaining"] == 12  # first match wins

    def test_httpx_headers_object(self):
        """Normalizes httpx.Headers-style objects."""
        cap = ProviderCapacity()
        headers = SimpleNamespace(
            items=lambda: [("x-ratelimit-remaining-requests", "7")]
        )
        cap.record_headers("openai", headers)
        s = cap._state["openai"]
        assert s["remaining"] == 7

    def test_exhausted_at_set_when_remaining_zero(self, monkeypatch):
        cap = ProviderCapacity()
        now = 500000.0
        monkeypatch.setattr(time, "time", lambda: now)
        cap.record_headers("openai", {"x-ratelimit-remaining-requests": "0"})
        s = cap._state["openai"]
        assert s["exhausted_at"] == now


class TestRecordError:
    """Tests for record_error()."""

    def test_default_block_duration(self, monkeypatch):
        cap = ProviderCapacity()
        now = 200000.0
        monkeypatch.setattr(time, "time", lambda: now)
        cap.record_error("openai", "429 rate limit exceeded")
        s = cap._state["openai"]
        assert s["blocked_until"] == now + DEFAULT_ERROR_BLOCK_SECONDS

    def test_retry_after_in_error_body(self, monkeypatch):
        cap = ProviderCapacity()
        now = 300000.0
        monkeypatch.setattr(time, "time", lambda: now)
        cap.record_error("openai", "Rate limited. Retry-After: 45 seconds")
        s = cap._state["openai"]
        assert s["blocked_until"] == now + 45

    def test_retry_after_timestamp_in_error_body(self, monkeypatch):
        cap = ProviderCapacity()
        now = 1700000000
        monkeypatch.setattr(time, "time", lambda: now)
        cap.record_error("openai", f"Retry-After: {now + 120}")
        s = cap._state["openai"]
        # Should compute remaining: (now + 120) - now = 120
        assert s["blocked_until"] == now + 120

    def test_last_error_stored(self):
        cap = ProviderCapacity()
        cap.record_error("openai", "Something went very wrong")
        s = cap._state["openai"]
        assert "Something went very wrong" in s["last_error"]

    def test_last_error_truncated(self):
        cap = ProviderCapacity()
        long_msg = "x" * 300
        cap.record_error("openai", long_msg)
        s = cap._state["openai"]
        assert len(s["last_error"]) == 200


class TestAllExhausted:
    """Tests for all_exhausted()."""

    def test_empty_list_returns_true(self):
        cap = ProviderCapacity()
        assert cap.all_exhausted([]) is True

    def test_all_blocked_returns_true(self):
        cap = ProviderCapacity()
        cap.record_error("a", "429")
        cap.record_error("b", "429")
        assert cap.all_exhausted(["a", "b"]) is True

    def test_one_clear_returns_false(self):
        cap = ProviderCapacity()
        cap.record_error("a", "429")
        # b is fresh
        assert cap.all_exhausted(["a", "b"]) is False

    def test_all_clear_returns_false(self):
        cap = ProviderCapacity()
        assert cap.all_exhausted(["openai", "anthropic"]) is False


class TestClear:
    """Tests for clear()."""

    def test_clear_single_provider(self):
        cap = ProviderCapacity()
        cap.record_error("openai", "429")
        cap.clear("openai")
        assert "openai" not in cap._state

    def test_clear_all_providers(self):
        cap = ProviderCapacity()
        cap.record_error("a", "429")
        cap.record_error("b", "429")
        cap.clear()
        assert cap._state == {}

    def test_clear_nonexistent_provider_is_safe(self):
        cap = ProviderCapacity()
        cap.clear("ghost")  # should not raise
        assert cap._state == {}


class TestNormalizeHeaders:
    """Tests for _normalize_headers()."""

    def test_none_returns_empty(self):
        result = ProviderCapacity._normalize_headers(None)
        assert result == {}

    def test_plain_dict_lowercases_keys(self):
        result = ProviderCapacity._normalize_headers({"X-RateLimit-Remaining": "5"})
        assert result == {"x-ratelimit-remaining": "5"}

    def test_httpx_headers_like_object(self):
        obj = SimpleNamespace(
            items=lambda: [("Retry-After", "30")]
        )
        result = ProviderCapacity._normalize_headers(obj)
        assert result == {"retry-after": "30"}

    def test_unusable_object_returns_empty(self):
        result = ProviderCapacity._normalize_headers(42)
        assert result == {}

    def test_values_are_stringified(self):
        result = ProviderCapacity._normalize_headers({"x-ratelimit-remaining-requests": 10})
        assert result == {"x-ratelimit-remaining-requests": "10"}


class TestSingleton:
    """Tests for the module-level singleton."""

    def test_get_capacity_returns_same_instance(self):
        reset_capacity()
        a = get_capacity()
        b = get_capacity()
        assert a is b

    def test_reset_capacity_creates_new_instance(self):
        reset_capacity()
        a = get_capacity()
        reset_capacity()
        b = get_capacity()
        assert a is not b
        assert isinstance(b, ProviderCapacity)


# ── Integration tests: Capacity-aware fallback in Agent ────────────────

from core.agent.agent import Agent


def _minimal_agent_with_providers(messages, provider_names=("primary", "fallback")):
    """Build a minimal agent with named providers for fallback testing."""
    agent = object.__new__(Agent)
    agent.max_provider_requests = 3
    agent.max_tool_rounds = 1
    agent.tool_result_max_chars = 6000
    agent.context_summary_enabled = False
    agent.context_handoff_enabled = False
    agent.provider_name = provider_names[0]
    agent.model = "model"
    agent.tool_definitions = []
    agent._active_lane = None
    agent.allowed_roots = ["."]
    agent.sandbox_config = {"enabled": False}
    agent.tool_compress_enabled = False

    agent.providers = [
        SimpleNamespace(name=name, model=f"{name}-model", api_mode="chat")
        for name in provider_names
    ]
    agent.provider_index = 0

    agent.session = SimpleNamespace(
        messages=messages,
        get_messages=lambda **_kw: [{"role": "system", "content": "system"}, *messages],
        sanitize_for_provider=lambda **_kw: None,
        add_user=lambda text: messages.append({"role": "user", "content": text}),
        add_assistant=lambda text, **_kw: messages.append({"role": "assistant", "content": text}),
        add_message=lambda msg: messages.append(msg),
        add_tool_result=lambda tid, content: messages.append(
            {"role": "tool", "tool_call_id": tid, "content": content}
        ),
        record_usage=lambda **_kw: None,
        turn_count=0,
    )
    agent.critic = SimpleNamespace(review=lambda text: SimpleNamespace(text=text))
    agent.memory = None
    agent.last_fallback_notice = ""
    agent.context_budget_config = "auto"
    agent.context_reserve_tokens = 1000
    return agent


class TestCapacityAwareFallback:
    """Integration tests: capacity-exhausted providers trigger fallback."""

    def test_skips_exhausted_primary_and_uses_fallback(self):
        """When primary is rate-limited, agent falls back to the next provider."""
        from core.provider.provider_capacity import get_capacity, reset_capacity

        reset_capacity()
        get_capacity().record_error("primary", "429 Too Many Requests")

        messages = []
        agent = _minimal_agent_with_providers(messages, provider_names=("primary", "fallback"))

        def fake_call(**_kw):
            return SimpleNamespace(
                content="Fallback answer.",
                tool_calls=[],
                usage=None,
                finish_reason="stop",
            )

        agent._call_provider = fake_call

        result = agent.run_turn("hello")
        assert result == "Fallback answer."
        assert agent.provider_name == "fallback"

    def test_records_rate_limit_error_and_falls_back(self):
        """When a provider raises a rate-limit error, it's recorded and fallback used."""
        from core.provider.provider_capacity import get_capacity, reset_capacity

        reset_capacity()

        messages = []
        agent = _minimal_agent_with_providers(messages, provider_names=("bad", "good"))

        call_count = [0]

        def fake_call(**_kw):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("429 Too Many Requests: rate limit exceeded. Retry-After: 5")
            return SimpleNamespace(
                content="Good answer.",
                tool_calls=[],
                usage=None,
                finish_reason="stop",
            )

        agent._call_provider = fake_call

        result = agent.run_turn("hello")
        assert result == "Good answer."
        assert agent.provider_name == "good"
        # Verify capacity was recorded
        assert not get_capacity().can_accept("bad")

    def test_all_exhausted_agent_still_produces_output(self):
        """When all providers are exhausted, agent falls through to try the
        current provider anyway (no fallback available). Should not crash."""
        from core.provider.provider_capacity import get_capacity, reset_capacity

        reset_capacity()
        get_capacity().record_error("primary", "429")
        get_capacity().record_error("fallback", "429")

        messages = []
        agent = _minimal_agent_with_providers(messages, provider_names=("primary", "fallback"))

        call_count = [0]

        def fake_call(**_kw):
            call_count[0] += 1
            return SimpleNamespace(
                content=f"Answer from call {call_count[0]}.",
                tool_calls=[],
                usage=None,
                finish_reason="stop",
            )

        agent._call_provider = fake_call

        result = agent.run_turn("hello")
        assert result == "Answer from call 1."
        # The agent tried the current (exhausted) provider since fallback
        # was also exhausted — _next_provider returned False, so we fell through.
        # The mock provider still succeeded, simulating a transient recovery.

    def test_agent_wraps_around_to_earlier_provider(self):
        """When provider_index points to later provider(s) and those ahead are
        exhausted, _next_provider wraps around to the start and picks a
        healthy earlier provider."""
        from core.provider.provider_capacity import get_capacity, reset_capacity

        reset_capacity()
        # Only the middle provider is exhausted
        get_capacity().record_error("middle", "429")

        messages = []
        agent = _minimal_agent_with_providers(
            messages, provider_names=("first", "middle", "last")
        )
        # Start on the exhausted provider
        agent.provider_index = 1  # "middle"
        agent.provider_name = "middle"
        agent.model = "middle-model"

        def fake_call(**_kw):
            return SimpleNamespace(
                content="Answer.",
                tool_calls=[],
                usage=None,
                finish_reason="stop",
            )

        agent._call_provider = fake_call

        result = agent.run_turn("hello")
        assert result == "Answer."
        # Should have fallen back off the exhausted provider
        # wrap-around goes: index 2 (last) then index 0 (first)
        assert agent.provider_name in ("last", "first")
