"""Tests for unified knowledge store."""
from __future__ import annotations


import pytest

from core.learning.knowledge_store import KnowledgeStore


class TestKnowledgeStore:
    """Core KnowledgeStore CRUD and query tests."""

    @pytest.fixture
    def store(self, tmp_path):
        db = tmp_path / "test_learning.sqlite"
        return KnowledgeStore(db)

    def test_record_and_query_by_kind(self, store):
        store.record("feedback", "communication", "be more concise", {"turn": "abc"})
        store.record("workflow", "tasking", "always verify after edit", {"confidence": 0.9})
        store.record("feedback", "scope_control", "don't broaden scope", {"turn": "def"})

        feedback = store.get_by_kind("feedback")
        assert len(feedback) == 2
        assert feedback[0]["kind"] == "feedback"
        assert feedback[1]["kind"] == "feedback"

        workflow = store.get_by_kind("workflow")
        assert len(workflow) == 1
        assert workflow[0]["content"] == "always verify after edit"

    def test_query_by_category(self, store):
        store.record("feedback", "communication", "msg A", {})
        store.record("workflow", "communication", "msg B", {})
        store.record("trace", "tool_efficiency", "msg C", {})

        comms = store.get_by_category("communication")
        assert len(comms) == 2
        kinds = {e["kind"] for e in comms}
        assert kinds == {"feedback", "workflow"}

        tools = store.get_by_category("tool_efficiency")
        assert len(tools) == 1

    def test_query_text_search(self, store):
        store.record("feedback", "communication", "be more concise in replies", {})
        store.record("workflow", "tasking", "always verify after edit", {})
        store.record("trace", "tool_efficiency", "use batch reads", {"pattern": "batch"})

        results = store.query(text="concise")
        assert len(results) == 1
        assert results[0]["content"] == "be more concise in replies"

        results = store.query(text="verify")
        assert len(results) == 1

        results = store.query(text="batch")
        assert len(results) == 1
        assert results[0]["metadata"]["pattern"] == "batch"

    def test_get_recent(self, store):
        for i in range(5):
            store.record("test", "cat", f"entry {i}", {"seq": i})
        recent = store.get_recent(limit=3)
        assert len(recent) == 3
        # Most recent first
        assert recent[0]["content"] == "entry 4"

    def test_get_categories_and_kinds(self, store):
        store.record("feedback", "communication", "a", {})
        store.record("workflow", "tasking", "b", {})
        store.record("trace", "tool_efficiency", "c", {})

        cats = store.get_categories()
        assert set(cats) == {"communication", "tasking", "tool_efficiency"}

        kinds = store.get_kinds()
        assert set(kinds) == {"feedback", "workflow", "trace"}

    def test_count(self, store):
        assert store.count() == 0
        store.record("a", "x", "1", {})
        store.record("a", "y", "2", {})
        store.record("b", "x", "3", {})
        assert store.count() == 3
        assert store.count(kind="a") == 2
        assert store.count(category="x") == 2
        assert store.count(kind="b", category="x") == 1

    def test_prune(self, store):
        for i in range(10):
            store.record("test", "cat", f"entry {i}", {"seq": i})
        assert store.count() == 10
        removed = store.prune(max_entries=5)
        assert removed == 5
        assert store.count() == 5
        # Oldest removed — only entries 5-9 remain
        recent = store.get_recent(limit=10)
        contents = {e["content"] for e in recent}
        assert contents == {f"entry {i}" for i in range(5, 10)}

    def test_metadata_roundtrip(self, store):
        meta = {"confidence": 0.95, "source": "feedback", "tags": ["a", "b"]}
        store.record("test", "cat", "content with metadata", meta)
        results = store.get_by_kind("test")
        assert len(results) == 1
        assert results[0]["metadata"] == meta

    def test_empty_store(self, store):
        assert store.get_recent() == []
        assert store.get_categories() == []
        assert store.get_kinds() == []
        assert store.count() == 0
        assert store.prune() == 0


class TestKnowledgeStoreSingleton:
    """Singleton get_knowledge_store tests."""

    def test_singleton_returns_same_instance(self):
        import core.learning.knowledge_store as ks
        ks._store = None
        s1 = ks.get_knowledge_store()
        s2 = ks.get_knowledge_store()
        assert s1 is s2
        ks._store = None  # cleanup
