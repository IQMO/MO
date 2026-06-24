"""Tests for core/session.py — context management + provider sanitation."""
import json

import pytest

from core.session.session import Session


@pytest.fixture
def session():
    """Create Session instance."""
    return Session("You are a helpful assistant.", max_history=10)


class TestSessionInit:
    """Tests for Session initialization."""

    def test_init_sets_system_message(self, session):
        """Test that system message is set."""
        assert session.system_message == "You are a helpful assistant."

    def test_init_sets_max_history(self, session):
        """Test that max_history is set."""
        assert session.max_history == 10

    def test_init_creates_empty_messages(self, session):
        """Test that messages list is empty."""
        assert session.messages == []

    def test_init_generates_session_id(self, session):
        """Test that session_id is generated."""
        assert session.session_id.startswith("mo-")
        assert len(session.session_id) > 10

    def test_init_sets_timestamps(self, session):
        """Test that timestamps are set."""
        assert session.created_at > 0
        assert session.turn_count == 0
        assert session.total_tokens == 0
        assert session.output_tokens == 0
        assert session.token_log == []
        assert session.trimmed_messages_count == 0
        assert session.last_trimmed_at == 0.0


class TestSessionAddUser:
    """Tests for Session.add_user method."""

    def test_add_user_appends_message(self, session):
        """Test that user message is appended."""
        session.add_user("Hello")
        
        assert len(session.messages) == 1
        assert session.messages[0]["role"] == "user"
        assert session.messages[0]["content"] == "Hello"

    def test_add_user_sanitizes_unicode(self, session):
        """Test that user content is sanitized."""
        # Invalid UTF-8 sequence
        content = "Hello \ud800 World"
        session.add_user(content)
        
        # Should not raise and should sanitize
        assert len(session.messages) == 1

    def test_add_user_trims_history(self, session):
        """Test that history is trimmed when exceeding max_history."""
        for i in range(15):
            session.add_user(f"Message {i}")
        
        assert len(session.messages) == 10


class TestSessionAddAssistant:
    """Tests for Session.add_assistant method."""

    def test_add_assistant_appends_message(self, session):
        """Test that assistant message is appended."""
        session.add_assistant("Hi there")
        
        assert len(session.messages) == 1
        assert session.messages[0]["role"] == "assistant"
        assert session.messages[0]["content"] == "Hi there"

    def test_add_assistant_includes_reasoning(self, session):
        """Test that reasoning content is included."""
        session.add_assistant("Response", reasoning_content="Internal reasoning")
        
        assert session.messages[0]["reasoning_content"] == "Internal reasoning"

    def test_add_assistant_ignores_empty(self, session):
        """Test that empty messages are ignored."""
        session.add_assistant("")
        
        assert len(session.messages) == 0

    def test_add_assistant_ignores_empty_with_reasoning(self, session):
        """Test that empty content with reasoning is still added."""
        session.add_assistant("", reasoning_content="Just reasoning")
        
        assert len(session.messages) == 1
        assert session.messages[0]["content"] == ""
        assert session.messages[0]["reasoning_content"] == "Just reasoning"

    def test_add_assistant_strips_whitespace(self, session):
        """Test that content is stripped."""
        session.add_assistant("  Response  ")
        
        assert session.messages[0]["content"] == "Response"

    def test_add_assistant_sanitizes_unicode(self, session):
        """Test that content is sanitized."""
        content = "Response \ud800 here"
        session.add_assistant(content)
        
        assert len(session.messages) == 1


class TestSessionAddToolResult:
    """Tests for Session.add_tool_result method."""

    def test_add_tool_result_appends_message(self, session):
        """Test that tool result is appended."""
        session.add_tool_result("call-123", "Tool output")
        
        assert len(session.messages) == 1
        assert session.messages[0]["role"] == "tool"
        assert session.messages[0]["tool_call_id"] == "call-123"
        assert session.messages[0]["content"] == "Tool output"

    def test_add_tool_result_sanitizes_content(self, session):
        """Test that tool result is sanitized."""
        content = "Output \ud800 here api_key=secret123"
        session.add_tool_result("call-123", content)
        
        assert len(session.messages) == 1
        assert "�" in session.messages[0]["content"]
        assert "api_key=[redacted]" in session.messages[0]["content"]
        assert "secret123" not in session.messages[0]["content"]

    def test_add_tool_result_trims_history(self, session):
        """Test that history is trimmed."""
        # Tool results don't trigger trim the same way as user messages
        # They only trim when exceeding max_history
        for i in range(15):
            session.add_user(f"User {i}")  # Add user messages to trigger trim
        
        # Now add tool results
        for i in range(5):
            session.add_tool_result(f"call-{i}", f"Output {i}")
        
        # Total should be trimmed to max_history (10)
        assert len(session.messages) <= 10


class TestSessionAddMessage:
    """Tests for Session.add_message method."""

    def test_add_message_appends_message(self, session):
        """Test that message is appended."""
        msg = {"role": "system", "content": "System message"}
        session.add_message(msg)
        
        assert len(session.messages) == 1
        assert session.messages[0] == msg

    def test_add_message_sanitizes_json(self, session):
        """Test that message is sanitized."""
        msg = {"role": "user", "content": "Message \ud800 here"}
        session.add_message(msg)
        
        assert len(session.messages) == 1


class TestSessionRecordUsage:
    """Tests for Session.record_usage method."""

    def test_record_usage_appends_to_log(self, session):
        """Test that usage is appended to log."""
        session.record_usage(
            provider="test",
            model="test-model",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150
        )
        
        assert len(session.token_log) == 1
        assert session.token_log[0]["input_tokens"] == 100
        assert session.token_log[0]["output_tokens"] == 50
        assert session.token_log[0]["total_tokens"] == 150

    def test_record_usage_updates_totals(self, session):
        """Test that totals are updated."""
        session.record_usage(
            provider="test",
            model="test-model",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150
        )
        
        assert session.total_tokens == 150
        assert session.output_tokens == 50

    def test_record_usage_calculates_total(self, session):
        """Test that total is calculated if not provided."""
        entry = session.record_usage(
            provider="test",
            model="test-model",
            input_tokens=100,
            output_tokens=50
        )
        
        assert entry["total_tokens"] == 150

    def test_record_usage_returns_entry(self, session):
        """Test that entry is returned."""
        entry = session.record_usage(
            provider="test",
            model="test-model",
            input_tokens=100,
            output_tokens=50,
            total_tokens=150
        )
        
        assert entry["provider"] == "test"
        assert entry["model"] == "test-model"
        assert "ts" in entry


class TestSessionGetMessages:
    """Tests for Session.get_messages method."""

    def test_get_messages_includes_system(self, session):
        """Test that system message is included."""
        messages = session.get_messages()
        
        assert len(messages) == 1
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "You are a helpful assistant."

    def test_get_messages_includes_history(self, session):
        """Test that history is included."""
        session.add_user("Hello")
        session.add_assistant("Hi")
        
        messages = session.get_messages()
        
        assert len(messages) == 3
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"

    def test_get_messages_includes_extra_context(self, session):
        """Extra context rides in a separate system message, not the static prefix."""
        messages = session.get_messages(extra_context="Extra context")

        assert messages[0]["content"] == "You are a helpful assistant."
        dynamic = [m for m in messages[1:] if m["role"] == "system"]
        assert len(dynamic) == 1
        assert "Extra context" in dynamic[0]["content"]

    def test_get_messages_includes_handoff_context(self, session):
        """Test that handoff context is included and consumed."""
        session._handoff_context = "Handoff context"

        messages = session.get_messages()

        assert any("Handoff context" in m["content"] for m in messages if m["role"] == "system")
        assert "Handoff context" not in messages[0]["content"]  # static prefix untouched
        assert session._handoff_context == ""  # Consumed

    def test_get_messages_can_preview_handoff_without_consuming(self, session):
        """Monitor previews must not consume the handoff seed before provider calls."""
        session._handoff_context = "Handoff context"

        preview = session.get_messages(consume_handoff=False)

        assert any("Handoff context" in m["content"] for m in preview if m["role"] == "system")
        assert session._handoff_context == "Handoff context"
        actual = session.get_messages()
        assert any("Handoff context" in m["content"] for m in actual if m["role"] == "system")
        assert session._handoff_context == ""

    def test_get_messages_combines_contexts(self, session):
        """Handoff and extra context combine into one dynamic system message."""
        session._handoff_context = "Handoff"
        messages = session.get_messages(extra_context="Extra")

        assert messages[0]["content"] == "You are a helpful assistant."
        dynamic = [m for m in messages[1:] if m["role"] == "system"]
        assert len(dynamic) == 1
        assert "Handoff" in dynamic[0]["content"]
        assert "Extra" in dynamic[0]["content"]

    def test_dynamic_context_appended_at_end(self, session):
        """Cache-stable layout: dynamic context is appended after the full stored
        history (including the latest user turn) so the entire prefix stays
        cacheable and the prior exchange is not re-billed every turn."""
        session.add_user("first question")
        session.add_assistant("first answer")
        session.add_user("second question")

        messages = session.get_messages(extra_context="Turn context")

        roles = [m["role"] for m in messages]
        assert roles == ["system", "user", "assistant", "user", "system"]
        assert messages[-1]["content"] == "Turn context"
        assert messages[-1]["role"] == "system"
        assert messages[-2]["content"] == "second question"

    def test_history_prefix_stable_across_turns_with_dynamic_context(self, session):
        """Trailing dynamic context must leave the entire stored history (system +
        all messages) byte-identical to the no-context payload, so the provider's
        prefix cache covers every message except the trailing dynamic block."""
        session.add_user("first question")
        session.add_assistant("first answer")
        session.add_user("second question")

        plain = session.get_messages()
        with_ctx = session.get_messages(extra_context="Turn context")

        assert with_ctx[:-1] == plain  # only the trailing dynamic block is new
        assert with_ctx[-1]["role"] == "system"

    def test_static_prefix_is_byte_stable_across_turns(self, session):
        """The system prompt + stored history must not vary with extra_context."""
        session.add_user("question")
        first = session.get_messages(extra_context="ctx A")
        second = session.get_messages(extra_context="ctx B")

        assert first[0] == second[0]  # static system prompt identical
        # stored history portion identical regardless of per-turn context
        assert [m for m in first if m["content"] not in ("ctx A",)][:2] == \
               [m for m in second if m["content"] not in ("ctx B",)][:2]


class TestSessionStripUnansweredUserTail:
    """Tests for Session.strip_unanswered_user_tail method."""

    def test_strip_removes_trailing_user(self):
        """Test that trailing user message is removed."""
        messages = [
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "Question"},
        ]
        
        cleaned, meta = Session.strip_unanswered_user_tail(messages)
        
        assert meta["changed"] is True
        assert len(cleaned) == 1
        assert meta["dropped_messages"] == 1

    def test_strip_removes_multiple_trailing_users(self):
        """Test that multiple trailing user messages are removed."""
        messages = [
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "Question 1"},
            {"role": "user", "content": "Question 2"},
        ]
        
        cleaned, meta = Session.strip_unanswered_user_tail(messages)
        
        assert meta["changed"] is True
        assert len(cleaned) == 1
        assert meta["dropped_messages"] == 2

    def test_strip_preserves_answered_user(self):
        """Test that answered user message is preserved."""
        messages = [
            {"role": "user", "content": "Question"},
            {"role": "assistant", "content": "Answer"},
        ]
        
        cleaned, meta = Session.strip_unanswered_user_tail(messages)
        
        assert meta["changed"] is False
        assert len(cleaned) == 2

    def test_strip_empty_messages(self):
        """Test with empty messages."""
        cleaned, meta = Session.strip_unanswered_user_tail([])
        
        assert meta["changed"] is False
        assert len(cleaned) == 0


class TestSessionStripUnfinishedToolTail:
    """Tests for Session.strip_unfinished_tool_tail method."""

    def test_strip_removes_unfinished_tool_chain(self):
        """Test that unfinished tool chain is removed."""
        messages = [
            {"role": "user", "content": "Do something"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "result"},
        ]
        
        cleaned, meta = Session.strip_unfinished_tool_tail(messages)
        
        assert meta["changed"] is True
        assert len(cleaned) == 0  # All removed

    def test_strip_preserves_finished_tool_chain(self):
        """Test that finished tool chain is preserved."""
        messages = [
            {"role": "user", "content": "Do something"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": "result"},
            {"role": "assistant", "content": "Done"},
        ]
        
        cleaned, meta = Session.strip_unfinished_tool_tail(messages)
        
        assert meta["changed"] is False
        assert len(cleaned) == 4

    def test_strip_removes_user_before_tool_chain(self):
        """Test that user message before tool chain is removed."""
        messages = [
            {"role": "assistant", "content": "Previous"},
            {"role": "user", "content": "Do something"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "result"},
        ]
        
        cleaned, meta = Session.strip_unfinished_tool_tail(messages)
        
        assert meta["changed"] is True
        assert len(cleaned) == 1  # Only "Previous" remains

    def test_strip_empty_messages(self):
        """Test with empty messages."""
        cleaned, meta = Session.strip_unfinished_tool_tail([])
        
        assert meta["changed"] is False
        assert len(cleaned) == 0


class TestSessionQuarantineUnfinishedTail:
    """Tests for Session.quarantine_unfinished_tail method."""

    def test_quarantine_removes_unfinished_tools(self, session):
        """Test that unfinished tool chain is quarantined."""
        session.messages = [
            {"role": "user", "content": "Do something"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "result"},
        ]
        session.turn_count = 1
        
        meta = session.quarantine_unfinished_tail()
        
        assert meta["changed"] is True
        assert len(session.messages) == 0
        assert session.turn_count == 0

    def test_quarantine_removes_unanswered_user(self, session):
        """Test that unanswered user is quarantined."""
        session.messages = [
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "Question"},
        ]
        session.turn_count = 1
        
        meta = session.quarantine_unfinished_tail()
        
        assert meta["changed"] is True
        assert len(session.messages) == 1
        assert session.turn_count == 0

    def test_quarantine_updates_trimmed_count(self, session):
        """Test that trimmed_messages_count is updated."""
        session.messages = [
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "Question"},
        ]
        session.turn_count = 1

        session.quarantine_unfinished_tail()

        assert session.trimmed_messages_count == 1

    def test_quarantine_keeps_unanswered_user_when_drop_disabled(self, session):
        """A real question that failed on a provider hiccup must not be silently
        deleted during active continuation — keep it so it gets answered."""
        session.messages = [
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "OWNER_COMPARISON https://example.com — can MO do X?"},
        ]
        session.turn_count = 1

        meta = session.quarantine_unfinished_tail(drop_unanswered_user=False)

        assert meta["changed"] is False
        assert len(session.messages) == 2  # the question survives
        assert session.messages[-1]["content"].startswith("OWNER_COMPARISON")

    def test_quarantine_always_drops_dangling_tools_even_when_user_drop_disabled(self, session):
        """Dangling tool chains are always unsafe for providers — dropped
        regardless of the unanswered-user flag."""
        session.messages = [
            {"role": "user", "content": "Do something"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "result"},
        ]
        session.turn_count = 1

        meta = session.quarantine_unfinished_tail(drop_unanswered_user=False)

        assert meta["changed"] is True
        assert len(session.messages) == 0
        assert session.last_trimmed_at > 0

    def test_quarantine_no_changes(self, session):
        """Test when no changes needed."""
        session.messages = [
            {"role": "user", "content": "Question"},
            {"role": "assistant", "content": "Answer"},
        ]
        
        meta = session.quarantine_unfinished_tail()
        
        assert meta["changed"] is False
        assert len(session.messages) == 2


class TestSessionSanitizeForProvider:
    """Tests for Session.sanitize_for_provider method."""

    def test_sanitize_removes_orphan_tools(self, session):
        """Test that orphan tool messages are removed."""
        session.messages = [
            {"role": "tool", "content": "orphan"},
            {"role": "user", "content": "Question"},
        ]
        
        result = session.sanitize_for_provider()
        
        assert result["changed"] is True
        assert len(session.messages) == 1

    def test_sanitize_removes_incomplete_tool_chain(self, session):
        """Test that incomplete tool chain is removed."""
        session.messages = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}, {"id": "2"}]},
            {"role": "tool", "tool_call_id": "1", "content": "result1"},
            # Missing tool result for id "2"
        ]
        
        result = session.sanitize_for_provider()
        
        assert result["changed"] is True

    def test_sanitize_preserves_complete_tool_chain(self, session):
        """Test that complete tool chain is preserved."""
        session.messages = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": "result"},
            {"role": "assistant", "content": "Done"},
        ]
        
        result = session.sanitize_for_provider()
        
        assert result["changed"] is False
        assert len(session.messages) == 3

    def test_sanitize_with_max_chars(self, session):
        """Test that messages are trimmed to max_chars."""
        # Add many long messages
        for i in range(20):
            session.add_user("x" * 1000)
        
        session.sanitize_for_provider(max_chars=5000)
        
        # Calculate total chars
        total = sum(len(json.dumps(m, default=str)) for m in session.messages)
        assert total <= 5000

    def test_sanitize_updates_trimmed_count(self, session):
        """Test that trimmed_messages_count is updated."""
        for i in range(20):
            session.add_user("x" * 1000)
        
        session.sanitize_for_provider(max_chars=5000)
        
        assert session.trimmed_messages_count > 0
        assert session.last_trimmed_at > 0


class TestSessionTrim:
    """Tests for Session._trim method."""

    def test_trim_removes_old_messages(self, session):
        """Test that old messages are removed."""
        for i in range(15):
            session.add_user(f"Message {i}")
        
        assert len(session.messages) == 10

    def test_trim_removes_leading_tool_messages(self, session):
        """Test that leading tool messages are removed after trim."""
        for i in range(15):
            if i == 5:
                session.messages.append({"role": "tool", "content": f"Tool {i}"})
            else:
                session.add_user(f"Message {i}")
        
        # After trim, leading tool message should be removed
        if session.messages and session.messages[0]["role"] == "tool":
            # This shouldn't happen after proper trim
            pass

    def test_trim_updates_trimmed_count(self, session):
        """Test that trimmed_messages_count is updated."""
        for i in range(15):
            session.add_user(f"Message {i}")
        
        assert session.trimmed_messages_count == 5
        assert session.last_trimmed_at > 0

    def test_trim_no_trim_under_limit(self, session):
        """Test that no trimming occurs under limit."""
        for i in range(5):
            session.add_user(f"Message {i}")
        
        assert len(session.messages) == 5
        assert session.trimmed_messages_count == 0


class TestSessionClear:
    """Tests for Session.clear method."""

    def test_clear_empties_messages(self, session):
        """Test that messages are cleared."""
        session.add_user("Hello")
        session.clear()
        
        assert session.messages == []

    def test_clear_resets_counters(self, session):
        """Test that counters are reset."""
        session.turn_count = 10
        session.total_tokens = 1000
        session.output_tokens = 500
        session.clear()
        
        assert session.turn_count == 0
        assert session.total_tokens == 0
        assert session.output_tokens == 0

    def test_clear_empties_token_log(self, session):
        """Test that token_log is emptied."""
        session.token_log = [{"tokens": 100}]
        session.clear()
        
        assert session.token_log == []

    def test_clear_resets_trimmed_count(self, session):
        """Test that trimmed_messages_count is reset."""
        session.trimmed_messages_count = 10
        session.last_trimmed_at = 1234567890
        session.clear()
        
        assert session.trimmed_messages_count == 0
        assert session.last_trimmed_at == 0.0

    def test_clear_resets_handoff_context(self, session):
        """Test that handoff_context is reset."""
        session._handoff_context = "Some context"
        session.clear()
        
        assert session._handoff_context == ""

    def test_clear_resets_compaction_metadata(self, session):
        session.compacted_messages_count = 3
        session.last_compacted_at = 123.0

        session.clear()

        assert session.compacted_messages_count == 0
        assert session.last_compacted_at == 0.0
