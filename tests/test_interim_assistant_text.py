"""Regression: prose that accompanies a tool call must reach the UI.

Providers often answer the user's question in prose AND call a tool in the
same response. That interim prose used to be stored to history and emitted
only to the monitor (livelog), never surfaced to the main transcript — so
direct answers were silently lost. `on_assistant_text` fixes that.
"""
from types import SimpleNamespace

from core.agent.agent import Agent
from core.backend_monitor import BackendMonitor


def _mock_agent():
    agent = object.__new__(Agent)
    agent.max_provider_requests = 3
    agent.max_tool_rounds = 2
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
        session_id="interim-test",
        created_at=0,
        get_messages=lambda **kw: [{"role": "system", "content": "private"}, *messages],
        sanitize_for_provider=lambda **_kwargs: None,
        add_user=lambda user_input: messages.append({"role": "user", "content": user_input}),
        add_message=lambda msg: messages.append(msg),
        add_tool_result=lambda tool_call_id, content: messages.append(
            {"role": "tool", "tool_call_id": tool_call_id, "content": content}
        ),
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
    return agent, messages


def test_interim_prose_with_tool_call_is_surfaced(tmp_path):
    agent, _messages = _mock_agent()
    seatbelt = "The seatbelt only blocks writes outside allowed roots | reads stay open."

    responses = iter([
        SimpleNamespace(
            content=seatbelt,
            tool_calls=[{"id": "c1", "function": {"name": "shell", "arguments": '{"command":"ls"}'}}],
            usage=None, finish_reason="tool_calls",
        ),
        SimpleNamespace(content="done", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **kw: next(responses)
    agent._dispatch_tool = lambda name, arguments: "file_a\nfile_b"

    surfaced = []
    monitor = BackendMonitor(tmp_path / "m.jsonl")
    result = agent.run_turn(
        "explain the seatbelt",
        monitor=monitor,
        on_assistant_text=lambda text: surfaced.append(text),
    )

    assert result == "done"
    assert surfaced == [seatbelt], "interim prose alongside tool call must reach the UI callback"

    log = (tmp_path / "m.jsonl").read_text(encoding="utf-8")
    assert '"type": "assistant_text"' in log


def test_empty_response_fails_over_to_next_provider(tmp_path):
    """A provider's empty-response blip should fail over once, not fail the turn.

    Regression: same-provider retries used to exhaust and give up without ever
    trying the next provider in the chain — so a single opencode empty blip
    failed the whole turn even when a healthy fallback could answer.
    """
    agent, _messages = _mock_agent()
    agent.max_provider_requests = 12
    agent.max_tool_rounds = 12
    agent.providers = [SimpleNamespace(name="a"), SimpleNamespace(name="b")]
    switched = []

    def fake_next(reason=""):
        switched.append(reason)
        agent.provider_name = "b"
        return True

    agent._next_provider = fake_next

    responses = iter([
        SimpleNamespace(content="", tool_calls=[], usage=None, finish_reason="stop"),
        SimpleNamespace(content="", tool_calls=[], usage=None, finish_reason="stop"),
        SimpleNamespace(content="", tool_calls=[], usage=None, finish_reason="stop"),
        SimpleNamespace(content="recovered answer", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **kw: next(responses)

    result = agent.run_turn("hi", monitor=BackendMonitor(tmp_path / "m.jsonl"))

    assert result == "recovered answer"
    assert switched == ["empty_response"], "should fail over exactly once after retries exhausted"
    log = (tmp_path / "m.jsonl").read_text(encoding="utf-8")
    assert '"type": "provider_fallback"' in log


def test_empty_response_gives_up_when_no_fallback_available(tmp_path):
    """With no healthy provider left, MO returns the honest give-up message (once)."""
    agent, _messages = _mock_agent()
    agent.max_provider_requests = 12
    agent.max_tool_rounds = 12
    agent.providers = [SimpleNamespace(name="a")]
    calls = []
    agent._next_provider = lambda reason="": (calls.append(reason) or False)
    agent._record_turn_memory_and_learning = lambda *a, **k: []
    agent._append_after_turn_notes = lambda text, notes: text

    responses = iter([
        SimpleNamespace(content="", tool_calls=[], usage=None, finish_reason="stop")
        for _ in range(6)
    ])
    agent._call_provider = lambda **kw: next(responses)

    result = agent.run_turn("hi", monitor=BackendMonitor(tmp_path / "m.jsonl"))

    assert result == "Provider returned no visible answer after retry; try again or switch model."
    assert calls == ["empty_response"], "failover attempted exactly once before giving up"


def test_no_tool_call_response_does_not_double_emit(tmp_path):
    agent, _messages = _mock_agent()
    responses = iter([
        SimpleNamespace(content="just a plain answer", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **kw: next(responses)

    surfaced = []
    result = agent.run_turn(
        "hi",
        monitor=BackendMonitor(tmp_path / "m.jsonl"),
        on_assistant_text=lambda text: surfaced.append(text),
    )
    assert result == "just a plain answer"
    assert surfaced == [], "final no-tool answer is returned, not pushed through on_assistant_text"
