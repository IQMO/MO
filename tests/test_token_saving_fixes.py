"""Regression tests for the token-saving fixes:

#1 provider cache-hit instrumentation (measure, don't estimate)
#4 code graph exposed as first-class tools (code_search/find_callers/find_callees)
#6 owner-only protocol stop-gates are bounded (no loop to max_provider_requests)
#7 pure greetings skip episodic recall + project-file reads

(#2 — cache-stable trailing dynamic context — is covered in test_session.py.)
"""
from types import SimpleNamespace

import tools
from core.agent.agent import Agent
from core.agent.agent_utils import _usage_cache_tokens, _looks_like_trivial_greeting
from core.backend_monitor import BackendMonitor
from core.session.session import Session


# ── #1 cache instrumentation ────────────────────────────────────────

def test_usage_cache_tokens_handles_each_provider_family():
    deepseek = SimpleNamespace(prompt_cache_hit_tokens=800, prompt_cache_miss_tokens=200)
    assert _usage_cache_tokens(deepseek) == (800, 200)
    openai = {"prompt_tokens_details": {"cached_tokens": 640}}
    assert _usage_cache_tokens(openai) == (640, 0)
    anthropic = SimpleNamespace(cache_read_input_tokens=512, cache_creation_input_tokens=128)
    assert _usage_cache_tokens(anthropic) == (512, 128)


def test_usage_cache_tokens_zero_when_absent():
    assert _usage_cache_tokens(None) == (0, 0)
    assert _usage_cache_tokens(SimpleNamespace(prompt_tokens=10)) == (0, 0)


def test_record_usage_accumulates_cache_counters():
    s = Session("sys")
    s.record_usage(provider="p", model="m", input_tokens=1000, output_tokens=50,
                   total_tokens=1050, cache_hit_tokens=800, cache_miss_tokens=200)
    s.record_usage(provider="p", model="m", input_tokens=500, output_tokens=20,
                   total_tokens=520, cache_hit_tokens=480, cache_miss_tokens=20)
    assert s.input_tokens == 1500
    assert s.cache_hit_tokens == 1280
    assert s.cache_miss_tokens == 220
    # the per-call rows carry the split too
    assert s.token_log[-1]["cache_hit_tokens"] == 480


# ── #4 code graph as first-class tools ──────────────────────────────

def test_graph_tools_registered():
    for name in ("code_search", "find_callers", "find_callees"):
        assert name in tools.TOOL_EXECUTORS
        assert any(d["function"]["name"] == name for d in tools.TOOL_DEFINITIONS)
    # 16 base + computer-use: open_url + capture_screen + 6 browser_* + 6 desktop = 30
    assert len(tools.TOOL_DEFINITIONS) == len(tools.TOOL_EXECUTORS) == 30


def test_graph_tool_executors_return_strings_without_raising():
    # Even with an empty/missing graph these must degrade to a helpful string,
    # never raise (the dispatch layer would otherwise surface a hard error).
    assert isinstance(tools.execute_code_search({"query": "rate limiting"}), str)
    assert isinstance(tools.execute_find_callers({"symbol": "run_turn"}), str)
    assert isinstance(tools.execute_find_callees({"symbol": "run_turn"}), str)
    # missing required arg → guarded error string, not an exception
    assert tools.execute_code_search({}).startswith("Error")
    assert tools.execute_find_callers({}).startswith("Error")


# ── #6 bounded protocol stop-gate ───────────────────────────────────

def _loop_mock_agent(max_provider_requests=20):
    """Minimal agent that runs the finalization path with a no-tool stop text."""
    agent = object.__new__(Agent)
    agent.max_provider_requests = max_provider_requests
    agent.max_tool_rounds = 80
    agent.tool_result_max_chars = 6000
    agent.tool_compress_enabled = False
    agent.tool_compress_min_bytes = 0
    agent.compression_total_saved = 0
    agent.compression_total_ops = 0
    agent.compression_last_pct = 0
    agent.provider_name = "fake"
    agent.model = "model"
    agent.allowed_roots = ["."]
    agent.sandbox_config = {"enabled": False}
    agent.context_handoff_enabled = True
    agent.context_handoff_threshold = 0.70
    agent.context_budget_tokens = 128_000
    agent.context_budget_source = "test"
    agent._handoff_count = 0
    messages = []
    agent.session = SimpleNamespace(
        messages=messages,
        session_id="loop-test",
        created_at=0,
        get_messages=lambda **kw: [{"role": "system", "content": "sys"}, *messages],
        sanitize_for_provider=lambda **_kwargs: None,
        add_user=lambda user_input: messages.append({"role": "user", "content": user_input}),
        add_message=lambda msg: messages.append(msg),
        add_tool_result=lambda tool_call_id, content: None,
        add_assistant=lambda *a, **k: None,
        record_usage=lambda *a, **k: None,
        turn_count=0,
        max_history=50,
        token_log=[],
        total_tokens=0,
        output_tokens=0,
        trimmed_messages_count=0,
    )
    agent.profile = SimpleNamespace(
        user_name="", user_alias="", total_sessions=0, total_turns=0,
        build_profile_context=lambda **kw: "",
    )
    agent.memory = None
    agent.critic = SimpleNamespace(review=lambda text: SimpleNamespace(text=text))
    agent.tool_definitions = [{"name": "shell"}]
    agent.config = {"agent": {"context_handoff_threshold": 0.70}}
    agent._active_lane = None
    agent._thread_state = SimpleNamespace()
    agent._thread_state.provider_surface = "main"
    agent._thread_state.provider_worker_id = ""
    agent._thread_state.session = None
    return agent


def test_protocol_stop_gate_does_not_loop_to_budget(tmp_path, monkeypatch):
    """A owner_maintenance final gate that always rejects must NOT burn the whole
    provider budget — it is capped at a few corrective rounds then allowed to stop.
    """
    monkeypatch.setattr("core.agent.agent_turn.owner_maintenance_final_allows_stop", lambda *a, **k: False)

    calls = {"n": 0}

    def _provider(**kw):
        calls["n"] += 1
        return SimpleNamespace(content="report body", tool_calls=[], usage=None, finish_reason="stop")

    agent = _loop_mock_agent(max_provider_requests=20)
    agent._call_provider = _provider

    result = agent.run_turn("summarize the report", monitor=BackendMonitor(tmp_path / "m.jsonl"))

    assert result  # the turn ended with an answer instead of [MAX PROVIDER REQUESTS]
    # 1 initial + PROTOCOL_STOP_GATE_MAX (2) corrective continuations = 3, well under budget.
    assert calls["n"] <= 4, f"stop-gate looped {calls['n']} times — should be bounded"


# ── #7 greeting predicate ───────────────────────────────────────────

def test_trivial_greeting_predicate():
    for g in ("hi", "Hello", "hey MO", "thanks!", "ok", "yes"):
        assert _looks_like_trivial_greeting(g), g
    for real in ("fix the auth bug", "what do you know about me?", "hi, can you read main.py"):
        assert not _looks_like_trivial_greeting(real), real
