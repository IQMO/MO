"""Tests for core/sessions.py — session persistence manager."""
import json
import tempfile
from pathlib import Path
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from core.session.sessions import SessionManager


@pytest.fixture
def temp_sessions_dir():
    """Create temporary directory for session files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def session_manager(temp_sessions_dir):
    """Create SessionManager instance with temporary directory."""
    return SessionManager(str(temp_sessions_dir))


@pytest.fixture
def mock_session():
    """Create mock session object."""
    session = SimpleNamespace()
    session.session_id = "test-session-123"
    session.turn_count = 5
    session.messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there"},
    ]
    session.total_tokens = 1000
    session.output_tokens = 500
    session.token_log = [{"tokens": 100}, {"tokens": 200}]
    session.sanitize_for_provider = Mock(return_value={})
    session.clear = Mock()
    return session


class TestSessionManagerInit:
    """Tests for SessionManager initialization."""

    def test_init_creates_directory(self, temp_sessions_dir):
        """Test that initialization creates sessions directory."""
        new_dir = temp_sessions_dir / "new_sessions"
        SessionManager(str(new_dir))
        
        assert new_dir.exists()
        assert new_dir.is_dir()

    def test_init_default_name(self, session_manager):
        """Test that default session name is 'main'."""
        assert session_manager.current_name == "main"


class TestSessionManagerPath:
    """Tests for SessionManager._path method."""

    def test_path_sanitizes_name(self, session_manager):
        """Test that session name is sanitized."""
        path = session_manager._path("test/session:name")
        
        # Should remove invalid characters
        assert ":" not in path.stem
        assert "/" not in path.stem

    def test_path_truncates_long_name(self, session_manager):
        """Test that long session names are truncated."""
        long_name = "a" * 100
        path = session_manager._path(long_name)
        
        assert len(path.stem) == 64

    def test_path_empty_name_defaults(self, session_manager):
        """Test that empty name defaults to 'session'."""
        path = session_manager._path("")
        assert path.stem == "session"

    def test_path_preserves_valid_characters(self, session_manager):
        """Test that valid characters are preserved."""
        path = session_manager._path("test-session_123")
        assert path.stem == "test-session_123"


class TestSessionManagerSave:
    """Tests for SessionManager.save method."""

    def test_save_creates_file(self, session_manager, mock_session):
        """Test that save creates session file."""
        session_manager.save("test", mock_session)
        
        path = session_manager._path("test")
        assert path.exists()

    def test_save_writes_json(self, session_manager, mock_session):
        """Test that save writes valid JSON."""
        session_manager.save("test", mock_session)
        
        path = session_manager._path("test")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        
        assert data["name"] == "test"
        assert data["session_id"] == "test-session-123"
        assert data["turn_count"] == 5
        assert len(data["messages"]) == 2

    def test_save_event_reports_saved_not_live_message_count(self, session_manager, mock_session, monkeypatch):
        """The monitor count must match disk after unfinished-tail quarantine."""
        events = []
        monitor = SimpleNamespace(emit=lambda event_type, payload: events.append((event_type, payload)))
        monkeypatch.setattr("core.backend_monitor.get_monitor", lambda: monitor)
        mock_session.messages = [
            {"role": "user", "content": "stale build"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call-1", "function": {"name": "read_file"}}]},
            {"role": "tool", "tool_call_id": "call-1", "content": "file"},
        ]

        result = session_manager.save("test", mock_session)

        saved = json.loads(session_manager._path("test").read_text(encoding="utf-8"))
        event = events[-1][1]
        assert saved["messages"] == []
        assert event["messages"] == 0
        assert event["saved_messages"] == 0
        assert event["live_messages"] == 3
        assert event["quarantined"] is True
        assert "0 messages" in result

    def test_save_updates_current_name(self, session_manager, mock_session):
        """Test that save updates current session name."""
        session_manager.save("test", mock_session)
        assert session_manager.current_name == "test"

    def test_save_includes_timestamp(self, session_manager, mock_session):
        """Test that save includes timestamp."""
        session_manager.save("test", mock_session)
        
        path = session_manager._path("test")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        
        assert "saved_at" in data
        assert isinstance(data["saved_at"], (int, float))

    def test_save_with_extra_meta(self, session_manager, mock_session):
        """Test that save includes extra metadata."""
        extra = {"custom_field": "custom_value"}
        session_manager.save("test", mock_session, extra_meta=extra)
        
        path = session_manager._path("test")
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        
        assert data["meta"]["custom_field"] == "custom_value"

    def test_save_empty_name_defaults_to_main(self, session_manager, mock_session):
        """Test that empty name defaults to 'main'."""
        session_manager.save("", mock_session)
        assert session_manager.current_name == "main"

    def test_save_strips_whitespace_from_name(self, session_manager, mock_session):
        """Test that whitespace is stripped from name."""
        session_manager.save("  test  ", mock_session)
        assert session_manager.current_name == "test"


class TestSessionManagerSaveSnapshot:
    """Tests for SessionManager.save_snapshot method."""

    def test_save_snapshot_creates_file(self, session_manager, mock_session):
        """Test that save_snapshot creates session file."""
        session_manager.save_snapshot("snapshot", mock_session)
        
        path = session_manager._path("snapshot")
        assert path.exists()

    def test_save_snapshot_does_not_change_current(self, session_manager, mock_session):
        """Test that save_snapshot doesn't change current session name."""
        initial_name = session_manager.current_name
        session_manager.save_snapshot("snapshot", mock_session)
        
        assert session_manager.current_name == initial_name

    def test_save_snapshot_empty_name_defaults(self, session_manager, mock_session):
        """Test that empty name defaults to 'snapshot'."""
        session_manager.save_snapshot("", mock_session)
        
        path = session_manager._path("snapshot")
        assert path.exists()


class TestSessionManagerLoad:
    """Tests for SessionManager.load method."""

    def test_load_existing_session(self, session_manager, mock_session):
        """Test loading an existing session."""
        session_manager.save("test", mock_session)
        
        data = session_manager.load("test")
        
        assert data is not None
        assert data["name"] == "test"
        assert data["session_id"] == "test-session-123"

    def test_load_nonexistent_session(self, session_manager):
        """Test loading a non-existent session returns None."""
        data = session_manager.load("nonexistent")
        assert data is None

    def test_load_invalid_json(self, session_manager):
        """Test loading invalid JSON returns None."""
        path = session_manager._path("invalid")
        path.write_text("not valid json", encoding="utf-8")
        
        data = session_manager.load("invalid")
        assert data is None

    def test_load_cleans_messages(self, session_manager, mock_session):
        """Test that load cleans messages."""
        # Add message with reasoning_content
        mock_session.messages.append({
            "role": "assistant",
            "content": "response",
            "reasoning_content": "internal reasoning"
        })
        session_manager.save("test", mock_session)
        
        data = session_manager.load("test")
        
        # reasoning_content should be removed
        for msg in data["messages"]:
            assert "reasoning_content" not in msg


class TestSessionManagerSwitch:
    """Tests for SessionManager.switch method."""

    def test_switch_to_existing_session(self, session_manager, mock_session):
        """Test switching to an existing session."""
        # Save initial session
        session_manager.save("session1", mock_session)
        
        # Create and save another session
        session2 = Mock()
        session2.session_id = "session-2"
        session2.turn_count = 10
        session2.messages = [{"role": "user", "content": "Different"}]
        session2.total_tokens = 2000
        session2.output_tokens = 1000
        session2.token_log = []
        session2.sanitize_for_provider = Mock()
        session_manager.save("session2", session2)
        
        # Switch back to session1
        result = session_manager.switch("session1", mock_session)
        
        assert "Switched to 'session1'" in result
        assert session_manager.current_name == "session1"
        assert mock_session.session_id == "test-session-123"

    def test_switch_to_new_session(self, session_manager, mock_session):
        """Test switching to a new session creates it."""
        result = session_manager.switch("new_session", mock_session)
        
        assert "Created new session" in result
        assert session_manager.current_name == "new_session"
        mock_session.clear.assert_called_once()

    def test_switch_saves_current_session(self, session_manager, mock_session):
        """Test that switch saves current session before switching."""
        session_manager.save("session1", mock_session)
        
        # Modify session
        mock_session.turn_count = 10
        
        # Switch to new session
        session_manager.switch("session2", mock_session)
        
        # Load session1 and verify it was saved
        data = session_manager.load("session1")
        assert data["turn_count"] == 10


class TestSessionManagerRemove:
    """Tests for SessionManager.remove method."""

    def test_remove_existing_session(self, session_manager, mock_session):
        """Test removing an existing session."""
        session_manager.save("test", mock_session)
        
        result = session_manager.remove("test")
        
        assert "Removed session" in result
        path = session_manager._path("test")
        assert not path.exists()

    def test_remove_nonexistent_session(self, session_manager):
        """Test removing a non-existent session."""
        result = session_manager.remove("nonexistent")
        assert "Session not found" in result

    def test_remove_current_session_resets_to_main(self, session_manager, mock_session):
        """Test that removing current session resets to 'main'."""
        session_manager.save("test", mock_session)
        # Save sets current_name to "test", so we're already on "test"
        
        session_manager.remove("test")
        
        assert session_manager.current_name == "main"


class TestSessionManagerList:
    """Tests for SessionManager.list_sessions method."""

    def test_list_sessions_empty(self, session_manager):
        """Test listing sessions when none exist."""
        sessions = session_manager.list_sessions()
        assert sessions == []

    def test_list_sessions_multiple(self, session_manager, mock_session):
        """Test listing multiple sessions."""
        for i in range(3):
            mock_session.session_id = f"session-{i}"
            session_manager.save(f"session{i}", mock_session)
        
        sessions = session_manager.list_sessions()
        
        assert len(sessions) == 3
        names = [s["name"] for s in sessions]
        assert "session0" in names
        assert "session1" in names
        assert "session2" in names

    def test_list_sessions_includes_metadata(self, session_manager, mock_session):
        """Test that list includes session metadata."""
        session_manager.save("test", mock_session)
        
        sessions = session_manager.list_sessions()
        
        assert len(sessions) == 1
        session = sessions[0]
        assert session["name"] == "test"
        assert session["turns"] == 5
        assert session["messages"] == 2
        assert "saved_at" in session
        assert "current" in session

    def test_list_sessions_marks_current(self, session_manager, mock_session):
        """Test that current session is marked."""
        session_manager.save("session1", mock_session)
        session_manager.save("session2", mock_session)
        
        sessions = session_manager.list_sessions()
        
        current_sessions = [s for s in sessions if s["current"]]
        assert len(current_sessions) == 1
        assert current_sessions[0]["name"] == "session2"

    def test_list_sessions_ordered_by_time(self, session_manager, mock_session):
        """Test that sessions are ordered by save time (newest first)."""
        import time
        
        for i in range(3):
            mock_session.session_id = f"session-{i}"
            session_manager.save(f"session{i}", mock_session)
            if i < 2:
                time.sleep(0.01)  # Ensure different timestamps
        
        sessions = session_manager.list_sessions()
        
        # Newest should be first
        assert sessions[0]["name"] == "session2"


class TestSessionManagerLatest:
    """Tests for SessionManager.latest method."""

    def test_latest_with_sessions(self, session_manager, mock_session):
        """Test getting latest session when sessions exist."""
        import time
        
        session_manager.save("session1", mock_session)
        time.sleep(0.01)
        session_manager.save("session2", mock_session)
        
        latest = session_manager.latest()
        
        assert latest == "session2"

    def test_latest_no_sessions(self, session_manager):
        """Test getting latest session when none exist."""
        latest = session_manager.latest()
        assert latest is None


class TestSessionManagerRenderList:
    """Tests for SessionManager.render_list method."""

    def test_render_list_empty(self, session_manager):
        """Test rendering empty session list."""
        result = session_manager.render_list()
        assert "No saved sessions" in result

    def test_render_list_with_sessions(self, session_manager, mock_session):
        """Test rendering session list with sessions."""
        session_manager.save("test", mock_session)
        
        result = session_manager.render_list()
        
        assert "1 sessions:" in result
        assert "test" in result
        assert "5 turns" in result

    def test_render_list_marks_current(self, session_manager, mock_session):
        """Test that current session is marked with asterisk."""
        session_manager.save("test", mock_session)
        
        result = session_manager.render_list()
        
        assert "*" in result  # Current session marker


class TestSessionManagerCleanMessages:
    """Tests for SessionManager._clean_messages_with_meta method."""

    def test_clean_removes_reasoning_content(self, session_manager):
        """Test that reasoning_content is removed from messages."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi", "reasoning_content": "internal"},
        ]
        
        cleaned, meta = session_manager._clean_messages_with_meta(messages)
        
        assert "reasoning_content" not in cleaned[1]

    def test_clean_strips_unfinished_tool_tail(self, session_manager):
        """Test that unfinished tool tail is stripped."""
        messages = [
            {"role": "user", "content": "Do something"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1", "function": {"name": "test"}}]},
            {"role": "tool", "content": "result"},
        ]
        
        cleaned, meta = session_manager._clean_messages_with_meta(messages)
        
        assert meta["changed"] is True
        assert len(cleaned) < len(messages)

    def test_clean_strips_unanswered_user_tail(self, session_manager):
        """Test that unanswered user tail is stripped."""
        messages = [
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "Question without answer"},
        ]
        
        cleaned, meta = session_manager._clean_messages_with_meta(messages)
        
        assert meta["changed"] is True
        assert len(cleaned) == 1


class TestSessionManagerAgeText:
    """Tests for SessionManager._age_text method."""

    def test_age_text_just_now(self, session_manager):
        """Test age text for recent timestamp."""
        import time
        ts = time.time()
        
        result = session_manager._age_text(ts)
        
        assert result == "just now"

    def test_age_text_minutes(self, session_manager):
        """Test age text for minutes ago."""
        import time
        ts = time.time() - 120  # 2 minutes ago
        
        result = session_manager._age_text(ts)
        
        assert "2m ago" in result

    def test_age_text_hours(self, session_manager):
        """Test age text for hours ago."""
        import time
        ts = time.time() - 7200  # 2 hours ago
        
        result = session_manager._age_text(ts)
        
        assert "2h ago" in result

    def test_age_text_days(self, session_manager):
        """Test age text for days ago."""
        import time
        ts = time.time() - 172800  # 2 days ago
        
        result = session_manager._age_text(ts)
        
        assert "2d ago" in result

    def test_age_text_zero_timestamp(self, session_manager):
        """Test age text for zero timestamp."""
        result = session_manager._age_text(0)
        assert result == "unknown"
