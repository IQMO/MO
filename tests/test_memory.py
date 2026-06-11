"""Tests for core/memory.py — SQLite FTS5 episodic memory."""
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch, Mock

import pytest

from core.learning.memory import EpisodicMemory, _emit_memory_event


@pytest.fixture
def temp_memory_db():
    """Create temporary SQLite database for memory tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_memory.sqlite"
        yield db_path


@pytest.fixture
def memory_instance(temp_memory_db):
    """Create EpisodicMemory instance with temporary database."""
    return EpisodicMemory(temp_memory_db)


class TestEmitMemoryEvent:
    """Tests for _emit_memory_event function."""

    def test_emit_event_with_monitor(self):
        """Test event emission when monitor is available."""
        mock_monitor = Mock()
        
        with patch("core.backend_monitor.get_monitor", return_value=mock_monitor):
            _emit_memory_event("test_event", {"key": "value"})
            
            mock_monitor.emit.assert_called_once_with("test_event", {"key": "value"})

    def test_emit_event_without_monitor(self):
        """Test event emission when monitor is not available."""
        with patch("core.backend_monitor.get_monitor", return_value=None):
            # Should not raise
            _emit_memory_event("test_event", {"key": "value"})

    def test_emit_event_handles_exception(self):
        """Test that event emission handles exceptions gracefully."""
        with patch("core.backend_monitor.get_monitor", side_effect=Exception("Monitor error")):
            # Should not raise
            _emit_memory_event("test_event", {"key": "value"})


class TestEpisodicMemoryInit:
    """Tests for EpisodicMemory initialization."""

    def test_init_creates_database(self, temp_memory_db):
        """Test that initialization creates SQLite database."""
        EpisodicMemory(temp_memory_db)
        
        assert temp_memory_db.exists()
        assert temp_memory_db.is_file()

    def test_init_creates_tables(self, temp_memory_db):
        """Test that initialization creates required tables."""
        EpisodicMemory(temp_memory_db)
        
        with sqlite3.connect(temp_memory_db) as conn:
            # Check turns table
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='turns'")
            assert cursor.fetchone() is not None
            
            # Check turns_fts table (FTS5)
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='turns_fts'")
            assert cursor.fetchone() is not None

    def test_init_creates_parent_directory(self):
        """Test that initialization creates parent directory if needed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "subdir" / "memory.sqlite"
            EpisodicMemory(db_path)
            
            assert db_path.parent.exists()

    def test_init_handles_fts5_unavailable(self, temp_memory_db):
        """Test initialization when FTS5 is not available."""
        # Reset the warning flag
        EpisodicMemory._fts5_warned = False
        
        # Create a mock connection that raises OperationalError on FTS5 creation
        mock_conn = Mock()
        call_count = [0]
        
        def mock_execute(sql, *args):
            call_count[0] += 1
            # First call creates turns table (succeeds)
            if call_count[0] == 1 and "CREATE TABLE" in sql:
                return Mock()
            # Second call creates FTS5 table (fails)
            if call_count[0] == 2 and "turns_fts" in sql:
                raise sqlite3.OperationalError("FTS5 not available")
            return Mock()
        
        mock_conn.execute = mock_execute
        mock_conn.__enter__ = Mock(return_value=mock_conn)
        mock_conn.__exit__ = Mock(return_value=False)
        
        with patch("core.learning.memory.sqlite3.connect", return_value=mock_conn):
            memory = EpisodicMemory(temp_memory_db)
            
            assert memory is not None
            assert EpisodicMemory._fts5_warned is True


class TestEpisodicMemoryIndexTurn:
    """Tests for EpisodicMemory.index_turn method."""

    def test_index_turn_success(self, memory_instance):
        """Test successful turn indexing."""
        memory_instance.index_turn("turn-1", "user query", "assistant response with enough length")
        
        with memory_instance._connect() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM turns")
            count = cursor.fetchone()[0]
            assert count == 1

    def test_index_turn_with_fts5(self, memory_instance):
        """Test turn indexing with FTS5 index."""
        memory_instance.index_turn("turn-1", "user query", "assistant response with enough length")
        
        with memory_instance._connect() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM turns_fts")
            count = cursor.fetchone()[0]
            assert count == 1

    def test_index_turn_empty_turn_id(self, memory_instance):
        """Test indexing with empty turn_id is ignored."""
        memory_instance.index_turn("", "user", "assistant response")
        
        with memory_instance._connect() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM turns")
            count = cursor.fetchone()[0]
            assert count == 0

    def test_index_turn_empty_content(self, memory_instance):
        """Test indexing with empty content is ignored."""
        memory_instance.index_turn("turn-1", "", "")
        
        with memory_instance._connect() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM turns")
            count = cursor.fetchone()[0]
            assert count == 0

    def test_index_turn_short_response(self, memory_instance):
        """Test indexing with short response is ignored."""
        memory_instance.index_turn("turn-1", "user", "short")
        
        with memory_instance._connect() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM turns")
            count = cursor.fetchone()[0]
            assert count == 0

    def test_record_miss_tracks_terms(self, memory_instance):
        memory_instance.record_miss("missing foobar capability")
        memory_instance.record_miss("missing foobar capability")

        with memory_instance._connect() as conn:
            row = conn.execute("SELECT count FROM recall_misses WHERE term='foobar'").fetchone()
            assert row[0] == 2

    def test_index_turn_replaces_existing(self, memory_instance):
        """Test that indexing replaces existing turn with same ID."""
        memory_instance.index_turn("turn-1", "user", "first response with enough length")
        memory_instance.index_turn("turn-1", "user", "second response with enough length")
        
        with memory_instance._connect() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM turns")
            count = cursor.fetchone()[0]
            assert count == 1
            
            cursor = conn.execute("SELECT assistant FROM turns WHERE turn_id='turn-1'")
            assistant = cursor.fetchone()[0]
            assert "second" in assistant

    def test_index_turn_handles_fts5_error(self, memory_instance):
        """Test that FTS5 errors are handled gracefully."""
        # First index should succeed
        memory_instance.index_turn("turn-1", "user", "first response with enough length")
        
        # Mock FTS5 to fail on second index
        with patch.object(memory_instance, "_connect") as mock_connect:
            mock_conn = Mock()
            mock_conn.execute = Mock(side_effect=[
                None,  # INSERT OR REPLACE succeeds
                sqlite3.OperationalError("FTS5 error"),  # DELETE fails
            ])
            mock_connect.return_value.__enter__.return_value = mock_conn
            
            # Should not raise
            memory_instance.index_turn("turn-2", "user", "second response with enough length")


class TestEpisodicMemoryCleanup:
    """Tests for EpisodicMemory._cleanup method."""

    def test_cleanup_removes_old_turns(self, memory_instance):
        """Test that cleanup removes turns beyond max_turns."""
        # Index 250 turns (exceeds default max_turns of 200)
        for i in range(250):
            memory_instance.index_turn(f"turn-{i}", "user", f"response {i} with enough length")
        
        with memory_instance._connect() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM turns")
            count = cursor.fetchone()[0]
            assert count == 200

    def test_cleanup_preserves_recent_turns(self, memory_instance):
        """Test that cleanup preserves the most recent turns."""
        # Index 250 turns
        for i in range(250):
            memory_instance.index_turn(f"turn-{i}", "user", f"response {i} with enough length")
        
        with memory_instance._connect() as conn:
            # Check that the most recent turns are preserved
            cursor = conn.execute("SELECT turn_id FROM turns ORDER BY updated_at DESC, rowid DESC LIMIT 10")
            recent_ids = [row[0] for row in cursor.fetchall()]
            
            # Should have the most recent 10 turns
            expected_ids = [f"turn-{i}" for i in range(249, 239, -1)]
            assert recent_ids == expected_ids

    def test_cleanup_no_removal_under_limit(self, memory_instance):
        """Test that no turns are removed when under limit."""
        # Index 100 turns (under default max_turns of 200)
        for i in range(100):
            memory_instance.index_turn(f"turn-{i}", "user", f"response {i} with enough length")
        
        with memory_instance._connect() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM turns")
            count = cursor.fetchone()[0]
            assert count == 100

    def test_cleanup_custom_max_turns(self, memory_instance):
        """Test cleanup with custom max_turns."""
        # Index 50 turns
        for i in range(50):
            memory_instance.index_turn(f"turn-{i}", "user", f"response {i} with enough length")
        
        # Cleanup with max_turns=30
        with memory_instance._connect() as conn:
            removed = memory_instance._cleanup(conn, max_turns=30)
            
            cursor = conn.execute("SELECT COUNT(*) FROM turns")
            count = cursor.fetchone()[0]
            assert count == 30
            assert removed == 20

    def test_cleanup_removes_from_fts5(self, memory_instance):
        """Test that cleanup also removes from FTS5 index."""
        # Index 250 turns
        for i in range(250):
            memory_instance.index_turn(f"turn-{i}", "user", f"response {i} with enough length")
        
        with memory_instance._connect() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM turns_fts")
            count = cursor.fetchone()[0]
            assert count == 200

    def test_cleanup_handles_exception(self, memory_instance):
        """Test that cleanup handles exceptions gracefully."""
        with patch.object(memory_instance, "_connect") as mock_connect:
            mock_conn = Mock()
            mock_conn.execute = Mock(side_effect=sqlite3.OperationalError("Database error"))
            mock_connect.return_value.__enter__.return_value = mock_conn
            
            # Should not raise
            result = memory_instance._cleanup(mock_conn)
            assert result == 0


class TestEpisodicMemoryRecall:
    """Tests for EpisodicMemory.recall method."""

    def test_recall_with_fts5(self, memory_instance):
        """Test recall using FTS5 search."""
        memory_instance.index_turn("turn-1", "python programming", "Python is a great programming language")
        memory_instance.index_turn("turn-2", "javascript", "JavaScript is used for web development")
        
        results = memory_instance.recall("python")
        
        assert len(results) == 1
        assert results[0]["turn_id"] == "turn-1"
        assert "Python" in results[0]["assistant"]

    def test_recall_fallback_to_substring(self, memory_instance):
        """Test recall falls back to substring search when FTS5 fails."""
        memory_instance.index_turn("turn-1", "python programming", "Python is a great programming language")
        
        # Mock the connection's execute method to fail on FTS5 query
        mock_conn = Mock()
        call_count = [0]
        
        def mock_execute(sql, *args):
            call_count[0] += 1
            # Fail on FTS5 MATCH query
            if "turns_fts MATCH" in sql:
                raise sqlite3.OperationalError("FTS5 error")
            # For other queries, return a mock cursor with results
            if "turns" in sql and "MATCH" not in sql:
                cursor = Mock()
                cursor.fetchall.return_value = [
                    {"turn_id": "turn-1", "user": "python programming", "assistant": "Python is a great programming language"}
                ]
                return cursor
            return Mock()
        
        mock_conn.execute = mock_execute
        mock_conn.__enter__ = Mock(return_value=mock_conn)
        mock_conn.__exit__ = Mock(return_value=False)
        
        with patch("core.learning.memory.sqlite3.connect", return_value=mock_conn):
            results = memory_instance.recall("python")
            
            assert len(results) == 1

    def test_recall_empty_query(self, memory_instance):
        """Test recall with empty query returns empty list."""
        results = memory_instance.recall("")
        assert results == []

    def test_recall_short_terms_ignored(self, memory_instance):
        """Test that short search terms (<=2 chars) are ignored."""
        memory_instance.index_turn("turn-1", "python", "Python programming")
        
        results = memory_instance.recall("py")  # Too short
        
        assert results == []

    def test_recall_limit_results(self, memory_instance):
        """Test that recall respects the limit parameter."""
        for i in range(10):
            memory_instance.index_turn(f"turn-{i}", "python", f"Python response {i}")
        
        results = memory_instance.recall("python", limit=5)
        
        assert len(results) == 5

    def test_recall_multiple_terms(self, memory_instance):
        """Test recall with multiple search terms."""
        memory_instance.index_turn("turn-1", "python programming", "Python is great for programming")
        memory_instance.index_turn("turn-2", "python web", "Python for web development")
        
        results = memory_instance.recall("python programming")
        
        # Should match turn-1 (has both terms)
        assert len(results) >= 1
        assert any("Python" in r["assistant"] and "programming" in r["assistant"] for r in results)

    def test_recall_sanitizes_query(self, memory_instance):
        """Test that recall sanitizes query to prevent FTS5 syntax errors."""
        memory_instance.index_turn("turn-1", "test", "test response with quotes")
        
        # Query with quotes should be sanitized
        results = memory_instance.recall('"test"')
        
        # Should not raise and should return results
        assert isinstance(results, list)

    def test_recall_handles_exception(self, memory_instance):
        """Test that recall handles exceptions gracefully."""
        with patch.object(memory_instance, "_connect", side_effect=Exception("Database error")):
            results = memory_instance.recall("test")
            assert results == []
