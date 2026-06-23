"""Computer-use Step 1: capture_screen perception wiring (headless, no real grab)."""
from types import SimpleNamespace

from core.provider.provider import (
    ChatCompletionsProvider,
    CodexOAuthProvider,
    first_vision_provider_index,
)
from core.session.session import Session

import tools


class _StubProvider:
    def __init__(self, name, supports_vision):
        self.name = name
        self.supports_vision = supports_vision


def test_capture_screen_registered():
    assert "capture_screen" in tools.TOOL_EXECUTORS
    assert any(d["function"]["name"] == "capture_screen" for d in tools.TOOL_DEFINITIONS)


def test_tool_result_image_becomes_content_parts():
    sess = Session("sys")
    sess.add_tool_result("c1", "[screen captured 1280x720]", image_data_uri="data:image/png;base64,AAAA")
    content = sess.messages[-1]["content"]
    assert isinstance(content, list)
    assert any(p.get("type") == "image" for p in content)
    assert any(p.get("type") == "text" for p in content)


def test_codex_builder_maps_image_to_input_image():
    sess = Session("sys")
    sess.add_tool_result("c1", "[screen captured]", image_data_uri="data:image/png;base64,AAAA")
    _instr, items = CodexOAuthProvider._to_instructions_and_input(sess.messages)
    parts = [p for item in items for p in item.get("content", [])]
    assert any(p.get("type") == "input_image" for p in parts)


def test_chat_completions_flattens_image_to_text_when_text_only():
    sess = Session("sys")
    sess.add_tool_result("c1", "[screen captured]", image_data_uri="data:image/png;base64,AAAA")
    norm = ChatCompletionsProvider._normalize_messages(sess.messages, supports_vision=False)
    assert isinstance(norm[-1]["content"], str)
    assert "image omitted" in norm[-1]["content"]


def test_chat_completions_delivers_image_when_vision_capable():
    sess = Session("sys")
    sess.add_tool_result("c1", "[screen captured]", image_data_uri="data:image/png;base64,AAAA")
    norm = ChatCompletionsProvider._normalize_messages(sess.messages, supports_vision=True)
    # tool message keeps text only; image rides in a trailing user message.
    assert isinstance(norm[-2]["content"], str)
    assert norm[-1]["role"] == "user"
    img_parts = [p for p in norm[-1]["content"] if p.get("type") == "image_url"]
    assert img_parts and img_parts[0]["image_url"]["url"].startswith("data:image/png")


def test_provider_vision_capability_flags():
    codex = CodexOAuthProvider.__new__(CodexOAuthProvider)
    assert codex.supports_vision is True
    text_only = ChatCompletionsProvider(
        name="p", base_url="http://x", api_key="k", model="m")
    assert text_only.supports_vision is False
    vision = ChatCompletionsProvider(
        name="p", base_url="http://x", api_key="k", model="m", supports_vision=True)
    assert vision.supports_vision is True


def test_first_vision_provider_index_picks_capable():
    providers = [_StubProvider("text", False), _StubProvider("vision", True)]
    assert first_vision_provider_index(providers) == 1
    assert first_vision_provider_index([_StubProvider("text", False)]) is None
    # capacity gate excludes a vision provider that can't accept.
    assert first_vision_provider_index(
        providers, can_accept=lambda n: n != "vision") is None


def test_string_tool_result_unchanged_backward_compat():
    sess = Session("sys")
    sess.add_tool_result("c1", "plain text result")
    assert sess.messages[-1]["content"] == "plain text result"


# ---------------------------------------------------------------- R2: vision switch
# A capture_screen on a text-only provider flips the SHARED agent onto a vision
# provider for the turn's continuation. That flip must be undone at the turn
# boundary so it never pollutes the next turn (or a concurrent surface), while a
# deliberate, sticky error-driven fallback (_next_provider) is left untouched.

class _RichProvider:
    def __init__(self, name, model, api_mode, supports_vision):
        self.name = name
        self.model = model
        self.api_mode = api_mode
        self.supports_vision = supports_vision


def _make_vision_agent(monkeypatch):
    from core.agent.agent import Agent
    import core.agent.agent as agent_mod

    monkeypatch.setattr(agent_mod, "append_provider_audit", lambda *a, **k: None)

    class _Cap:
        def can_accept(self, _name):
            return True

        def record_error(self, *a, **k):
            pass

    monkeypatch.setattr(agent_mod, "get_capacity", lambda: _Cap())

    a = Agent.__new__(Agent)
    text = _RichProvider("text-prov", "text-model", "chat_completions", False)
    vision = _RichProvider("vision-prov", "vision-model", "codex_responses", True)
    a.providers = [text, vision]
    a.provider_index = 0
    a.provider_name = text.name
    a.model = text.model
    a.api_mode = text.api_mode
    a.context_budget_config = "auto"
    a.context_reserve_tokens = 16384
    a.context_budget_tokens = 123
    a.context_budget_source = "test-source"
    a.last_fallback_notice = ""
    a.session = SimpleNamespace(session_id="s-vis")
    a._current_route_source = ""
    return a


def test_vision_switch_snapshots_and_restore_reverts(monkeypatch):
    a = _make_vision_agent(monkeypatch)
    assert a.switch_to_vision_provider() is True
    assert a.provider_index == 1 and a.provider_name == "vision-prov"
    snap = a._pre_vision_provider
    assert snap is not None and snap[0] == 0 and snap[1] == "text-prov"

    assert a.restore_vision_provider() is True
    assert a.provider_index == 0 and a.provider_name == "text-prov"
    assert a.model == "text-model" and a.api_mode == "chat_completions"
    # budget fields are restored exactly (switch had recomputed them)
    assert a.context_budget_tokens == 123 and a.context_budget_source == "test-source"
    assert a._pre_vision_provider is None


def test_restore_vision_provider_noop_without_switch(monkeypatch):
    a = _make_vision_agent(monkeypatch)
    assert a.restore_vision_provider() is False
    assert a.provider_index == 0 and a.provider_name == "text-prov"


def test_restore_does_not_revert_sticky_fallback(monkeypatch):
    """A turn that fell back to another provider WITHOUT a screenshot must keep
    that provider — restore is a no-op because no vision snapshot was taken."""
    a = _make_vision_agent(monkeypatch)
    a.provider_index = 1
    a.provider_name = "vision-prov"
    a.model = "vision-model"
    assert a.restore_vision_provider() is False
    assert a.provider_index == 1 and a.provider_name == "vision-prov"


def test_vision_switch_snapshot_taken_once_per_turn(monkeypatch):
    a = _make_vision_agent(monkeypatch)
    a.switch_to_vision_provider()
    first_snap = a._pre_vision_provider
    a.switch_to_vision_provider()  # active is already vision -> early return, no re-snapshot
    assert a._pre_vision_provider == first_snap
