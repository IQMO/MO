import json
from types import SimpleNamespace

from core.agent.agent import Agent
from core.session.session import Session


def _minimal_agent(messages):
    agent = object.__new__(Agent)
    agent.max_provider_requests = 3
    agent.max_tool_rounds = 1
    agent.tool_result_max_chars = 6000
    agent.context_summary_enabled = False
    agent.context_handoff_enabled = False
    agent.provider_name = "fake"
    agent.model = "model"
    agent.tool_definitions = []
    agent._active_lane = None
    agent.allowed_roots = ["."]
    agent.sandbox_config = {"enabled": False}
    
    agent.session = SimpleNamespace(
        messages=messages,
        get_messages=lambda **_kw: [{"role": "system", "content": "system"}, *messages],
        sanitize_for_provider=lambda **_kw: None,
        add_user=lambda text: messages.append({"role": "user", "content": text}),
        add_assistant=lambda text, **_kw: messages.append({"role": "assistant", "content": text}),
        add_message=lambda msg: messages.append(msg),
        add_tool_result=lambda tid, content: messages.append({"role": "tool", "tool_call_id": tid, "content": content}),
        record_usage=lambda **_kw: None,
        turn_count=0,
    )
    agent.critic = SimpleNamespace(review=lambda text: SimpleNamespace(text=text))
    agent.memory = None
    return agent


def test_agent_blocks_raw_tool_payload_text_and_reasks():
    messages = []
    agent = _minimal_agent(messages)

    responses = iter([
        SimpleNamespace(
            content='{"path":"examples/retro_menu.py","old_text":"x","new_text":"y"}[tool calls requested]\nedit_file({})',
            tool_calls=[],
            usage=None,
            finish_reason="stop",
        ),
        SimpleNamespace(content="Clean final answer.", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **_kw: next(responses)

    result = agent.run_turn("fix menu")

    assert result == "Clean final answer."
    assert any("TOOL PAYLOAD RETRY" in m.get("content", "") for m in messages)
    assert not any("RAW TOOL PAYLOAD BLOCKED" in m.get("content", "") for m in messages)
    assert not any("old_text" in m.get("content", "") and "new_text" in m.get("content", "") for m in messages if m.get("role") == "assistant")


def test_provider_retry_guidance_is_audited_for_raw_tool_payload(tmp_path, monkeypatch):
    from core.provider import provider_audit

    audit_path = tmp_path / "provider_audit.jsonl"
    monkeypatch.setattr(provider_audit, "LOG_PATH", audit_path)
    monkeypatch.setenv("MO_PROVIDER_AUDIT_FORCE", "1")
    messages = []
    agent = _minimal_agent(messages)

    responses = iter([
        SimpleNamespace(content='edit_file({"path":"x"}) [tool calls requested]', tool_calls=[], usage=None, finish_reason="stop"),
        SimpleNamespace(content="Clean final answer.", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **_kw: next(responses)

    result = agent.run_turn("fix menu")

    assert result == "Clean final answer."
    rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert any(row.get("event") == "provider_retry_guidance" and row.get("reason") == "raw_tool_payload" for row in rows)


def test_agent_falls_back_after_repeated_raw_tool_payload():
    messages = []
    agent = _minimal_agent(messages)
    agent.max_provider_requests = 4
    agent.providers = [
        SimpleNamespace(name="primary", model="bad-model", api_mode="chat"),
        SimpleNamespace(name="fallback", model="good-model", api_mode="chat"),
    ]
    agent.provider_index = 0
    agent.context_budget_config = "auto"
    agent.context_reserve_tokens = 1000
    agent.tool_compress_enabled = False
    calls = []

    def fake_provider(**_kw):
        calls.append(agent.provider_index)
        if agent.provider_index == 0:
            return SimpleNamespace(content='edit_file({"path":"x"}) [tool calls requested]', tool_calls=[], usage=None, finish_reason="stop")
        return SimpleNamespace(content="Fallback answered normally.", tool_calls=[], usage=None, finish_reason="stop")

    agent._call_provider = fake_provider

    result = agent.run_turn("fix menu")

    assert result == "Fallback answered normally."
    assert calls == [0, 0, 1]
    assert agent.provider_name == "fallback"
    assert "raw_tool_payload" in agent.last_fallback_notice


def test_agent_retries_empty_stop_response_without_storing_blank_assistant():
    messages = []
    agent = _minimal_agent(messages)
    responses = iter([
        SimpleNamespace(content="", tool_calls=[], usage=None, finish_reason="stop"),
        SimpleNamespace(content="Visible answer.", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **_kw: next(responses)

    result = agent.run_turn("what you mean?")

    assert result == "Visible answer."
    assert any("PROVIDER EMPTY" in m.get("content", "") for m in messages)
    assert not any(m.get("role") == "assistant" and m.get("content") == "" for m in messages)


def test_agent_blocks_invalid_tool_arguments_without_dispatching(tmp_path, monkeypatch):
    from core.provider import provider_audit

    audit_path = tmp_path / "provider_audit.jsonl"
    monkeypatch.setattr(provider_audit, "LOG_PATH", audit_path)
    monkeypatch.setenv("MO_PROVIDER_AUDIT_FORCE", "1")
    messages = []
    agent = _minimal_agent(messages)
    agent.tool_definitions = [{"name": "write_file"}]
    dispatched = []
    agent._dispatch_tool = lambda name, args: dispatched.append((name, args)) or "should not run"
    responses = iter([
        SimpleNamespace(
            content="",
            tool_calls=[{"id": "call-1", "function": {"name": "write_file", "arguments": '{"path":"examples/game.html","content":"unterminated'}}],
            usage=None,
            finish_reason="tool_calls",
        ),
        SimpleNamespace(content="I will split the edit.", tool_calls=[], usage=None, finish_reason="stop"),
    ])
    agent._call_provider = lambda **_kw: next(responses)

    result = agent.run_turn("upgrade game")

    assert result == "I will split the edit."
    assert dispatched == []
    assert any("TOOL ARGUMENTS INVALID" in m.get("content", "") for m in messages)
    assert not any(m.get("role") == "tool" for m in messages)
    rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert any(row.get("event") == "provider_retry_guidance" and row.get("reason") == "invalid_tool_arguments" for row in rows)


def test_agent_blocks_length_truncated_tool_call_before_dispatching():
    messages = []
    agent = _minimal_agent(messages)
    agent.tool_definitions = [{"name": "write_file"}]
    dispatched = []
    calls = []
    agent._dispatch_tool = lambda name, args: dispatched.append((name, args)) or "should not run"

    def fake_provider(**_kw):
        calls.append(True)
        return SimpleNamespace(
            content="",
            tool_calls=[{"id": "call-1", "function": {"name": "write_file", "arguments": '{"path":"examples/game.html","content":"ok"}'}}],
            usage=None,
            finish_reason="length",
        )

    agent._call_provider = fake_provider

    result = agent.run_turn("upgrade game")

    # New behavior: truncation retries with edit_file guidance, not stops.
    # The mock provider keeps returning truncated calls until max requests.
    assert "MAX TOOL ROUNDS" in result or "MAX PROVIDER REQUESTS" in result or "truncated" in result.lower()
    assert dispatched == []  # truncated tool calls never execute


def test_agent_silently_parks_unfinished_tool_tail_and_provider_answers_greeting():
    agent = _minimal_agent([])
    session = Session("system")
    session.messages = [
        {"role": "assistant", "content": "previous done"},
        {"role": "user", "content": "old build request"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"x"}'}}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "file"},
    ]
    session.turn_count = 1
    agent.session = session
    seen_messages = []
    seen_context = []

    def fake_provider(**_kw):
        seen_messages.extend(agent.session.messages)
        seen_context.append(str(_kw.get("extra_context") or ""))
        return SimpleNamespace(content="hello", tool_calls=[], usage=None, finish_reason="stop")

    agent._call_provider = fake_provider

    result = agent.run_turn("hi")

    assert result == "hello"
    assert getattr(agent, "_pending_interrupted_work", {}).get("user") == "old build request"
    assert "old build request" not in [m.get("content") for m in seen_messages]
    assert "Paused Interrupted Work" in seen_context[0]
    assert "old build request" in seen_context[0]
    assert "Do not continue" in seen_context[0]
    assert "do not inventory the workspace just to guess" in seen_context[0]
    assert [m.get("content") for m in seen_messages if m.get("role") == "user"] == ["hi"]


def test_agent_silently_parks_unanswered_user_tail_on_return_greeting():
    agent = _minimal_agent([])
    session = Session("system")
    session.messages = [{"role": "user", "content": "add weapon rewards to the game"}]
    session.turn_count = 1
    agent.session = session
    called = []
    seen_context = []

    def fake_provider(**_kw):
        called.append(True)
        seen_context.append(str(_kw.get("extra_context") or ""))
        return SimpleNamespace(content="natural hello", tool_calls=[], usage=None, finish_reason="stop")

    agent._call_provider = fake_provider

    result = agent.run_turn("hi mo")

    assert result == "natural hello"
    assert getattr(agent, "_pending_interrupted_work", {}).get("user") == "add weapon rewards to the game"
    assert called == [True]
    assert "Paused Interrupted Work" in seen_context[0]
    assert "add weapon rewards to the game" in seen_context[0]
    assert [m.get("content") for m in agent.session.messages if m.get("role") == "user"] == ["hi mo"]


def test_agent_silently_parks_stalled_loaded_session_when_user_returns():
    messages = [
        {"role": "user", "content": "add rewards to gather"},
        {"role": "user", "content": "hi mo"},
        {"role": "assistant", "content": "Let me inspect", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"x"}'}}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "file"},
        {"role": "assistant", "content": "[PROVIDER EMPTY] Response had no visible text and no tool calls. Answer the user directly."},
    ]
    agent = _minimal_agent(messages)
    seen_messages = []

    def fake_provider(**_kw):
        seen_messages.extend(agent.session.messages)
        return SimpleNamespace(content="hey", tool_calls=[], usage=None, finish_reason="stop")

    agent._call_provider = fake_provider

    result = agent.run_turn("hi")

    assert result == "hey"
    assert getattr(agent, "_pending_interrupted_work", {}).get("user") == "add rewards to gather"
    assert "add rewards to gather" not in [m.get("content") for m in seen_messages]


def test_agent_parks_devmode05_blocked_tail_as_interrupted_work():
    messages = [
        {"role": "user", "content": "START DEVMODE05"},
        {"role": "assistant", "content": "Investigating trace", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "grep", "arguments": '{"pattern":"turn_limit"}'}}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "turn_limit found"},
        {"role": "assistant", "content": "[DEVMODE05 BLOCKED] Tool budget exhausted. Next: resume from finding A1."},
    ]
    agent = _minimal_agent(messages)
    seen_messages = []

    def fake_provider(**_kw):
        seen_messages.extend(agent.session.messages)
        return SimpleNamespace(content="hi", tool_calls=[], usage=None, finish_reason="stop")

    agent._call_provider = fake_provider

    result = agent.run_turn("hi")

    assert result == "hi"
    assert getattr(agent, "_pending_interrupted_work", {}).get("user") == "START DEVMODE05"
    assert "START DEVMODE05" not in [m.get("content") for m in seen_messages]


def test_agent_recovers_original_request_from_live_truncated_resume_session_without_canned_reply():
    messages = [
        {"role": "user", "content": "add rewards and weapon upgrades"},
        {"role": "user", "content": "hi mo"},
        {"role": "assistant", "content": "Let me inspect", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"x"}'}}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "file"},
        {"role": "assistant", "content": "[PROVIDER EMPTY] Response had no visible text and no tool calls."},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Legacy notice: found unfinished work from the previous turn: \"add rewards and weapon upgrades\"."},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Hey there."},
        {"role": "user", "content": "contuine working on the unfinished work"},
        {"role": "assistant", "content": "[TOOL ARGUMENTS TRUNCATED] Provider hit the output limit while emitting tool calls."},
    ]
    agent = _minimal_agent(messages)
    seen_messages = []

    def fake_provider(**_kw):
        seen_messages.extend(agent.session.messages)
        return SimpleNamespace(content="hello again", tool_calls=[], usage=None, finish_reason="stop")

    agent._call_provider = fake_provider

    result = agent.run_turn("hi")

    assert result == "hello again"
    assert getattr(agent, "_pending_interrupted_work", {}).get("user") == "add rewards and weapon upgrades"
    provider_user_text = [m.get("content") for m in seen_messages if m.get("role") == "user"]
    assert "add rewards and weapon upgrades" not in provider_user_text
    assert "contuine working on the unfinished work" not in provider_user_text


def test_agent_keeps_pending_interrupted_work_silent_on_second_greeting():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Hey."},
    ]
    agent = _minimal_agent(messages)
    agent._pending_interrupted_work = {"changed": True, "dropped_messages": 0, "user": "add weapon rewards to the game"}
    called = []
    seen_context = []

    def fake_provider(**_kw):
        called.append(True)
        seen_context.append(str(_kw.get("extra_context") or ""))
        return SimpleNamespace(content="natural reply", tool_calls=[], usage=None, finish_reason="stop")

    agent._call_provider = fake_provider

    result = agent.run_turn("hi")

    assert result == "natural reply"
    assert getattr(agent, "_pending_interrupted_work", {}).get("user") == "add weapon rewards to the game"
    assert called == [True]
    assert "Paused Interrupted Work" in seen_context[0]
    assert "add weapon rewards to the game" in seen_context[0]


def test_agent_keeps_pending_interrupted_context_for_ambiguous_followup():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "Hey."},
    ]
    agent = _minimal_agent(messages)
    agent._pending_interrupted_work = {"changed": True, "dropped_messages": 17, "user": "add zombie rewards and weapon upgrades"}
    seen_context = []

    def fake_provider(**_kw):
        seen_context.append(str(_kw.get("extra_context") or ""))
        return SimpleNamespace(content="provider answer", tool_calls=[], usage=None, finish_reason="stop")

    agent._call_provider = fake_provider

    result = agent.run_turn("you tell me")

    assert result == "provider answer"
    assert "Paused Interrupted Work" in seen_context[0]
    assert "add zombie rewards and weapon upgrades" in seen_context[0]
    assert "ambiguous follow-up" in seen_context[0]
    assert "do not inventory the workspace just to guess" in seen_context[0]


def test_agent_pending_context_treats_finish_this_work_as_resume():
    agent = _minimal_agent([])
    agent._pending_interrupted_work = {"changed": True, "user": "add zombie rewards"}

    context = agent._pending_interrupted_work_context("yes lets finish this work if not already")

    assert "explicitly resume" in context
    assert "Do not continue" not in context



def test_agent_pending_context_treats_yes_proceed_with_them_as_resume():
    agent = _minimal_agent([])
    agent._pending_interrupted_work = {"changed": True, "user": "add zombie rewards"}

    context = agent._pending_interrupted_work_context("yes proceed with them")

    assert "explicitly resume" in context
    assert "targeted edit_file replacements" in context
    assert "Do not continue" not in context



def test_agent_pending_context_treats_focus_again_as_resume():
    agent = _minimal_agent([])
    agent._pending_interrupted_work = {"changed": True, "user": "add zombie rewards"}

    context = agent._pending_interrupted_work_context("lets focus again on what was left")

    assert "explicitly resume" in context
    assert "targeted edit_file replacements" in context
    assert "Do not continue" not in context



def test_agent_parks_saved_interrupted_turn_silently_on_return_greeting():
    messages = []
    agent = _minimal_agent(messages)
    agent._last_interrupted_turn = {"changed": True, "dropped_messages": 3, "user": "build old thing"}
    called = []
    agent._call_provider = lambda **_kw: called.append(True) or SimpleNamespace(content="welcome", tool_calls=[], usage=None, finish_reason="stop")

    result = agent.run_turn("I'm back")

    assert result == "welcome"
    assert getattr(agent, "_pending_interrupted_work", {}).get("user") == "build old thing"
    assert called == [True]
    assert messages[-1]["role"] == "assistant"


def test_agent_quarantines_unfinished_tool_tail_before_new_non_greeting_work():
    agent = _minimal_agent([])
    session = Session("system")
    session.messages = [
        {"role": "user", "content": "old build request"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"x"}'}}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "file"},
    ]
    session.turn_count = 1
    agent.session = session
    seen_messages = []

    def fake_provider(**_kw):
        seen_messages.extend(agent.session.messages)
        return SimpleNamespace(content="new work started", tool_calls=[], usage=None, finish_reason="stop")

    agent._call_provider = fake_provider

    result = agent.run_turn("build a different app")

    assert result == "new work started"
    assert "old build request" not in [m.get("content") for m in seen_messages]
    assert [m.get("content") for m in seen_messages if m.get("role") == "user"] == ["build a different app"]
