"""Computer-use Step 1: capture_screen perception wiring (headless, no real grab)."""
from core.provider.provider import ChatCompletionsProvider, CodexOAuthProvider
from core.session.session import Session

import tools


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


def test_chat_completions_flattens_image_to_text():
    sess = Session("sys")
    sess.add_tool_result("c1", "[screen captured]", image_data_uri="data:image/png;base64,AAAA")
    norm = ChatCompletionsProvider._normalize_messages(sess.messages)
    assert isinstance(norm[-1]["content"], str)
    assert "image omitted" in norm[-1]["content"]


def test_string_tool_result_unchanged_backward_compat():
    sess = Session("sys")
    sess.add_tool_result("c1", "plain text result")
    assert sess.messages[-1]["content"] == "plain text result"
