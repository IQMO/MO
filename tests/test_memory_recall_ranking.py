"""Memory recall ranks by relevance (FTS5 bm25), not just recency."""
import time

from core.learning.memory import EpisodicMemory


def test_relevant_old_turn_beats_recent_weak_match(tmp_path):
    mem = EpisodicMemory(path=tmp_path / "learning.sqlite")
    # Older but highly relevant to the query.
    mem.index_turn("t1", "how do I configure the deepseek provider api key in config",
                   "Set api_key_env in the provider block; that is the configuration.")
    time.sleep(0.01)
    # Newer but only weakly related (shares just one common word).
    mem.index_turn("t2", "the provider of lunch today was great",
                   "We had a nice long lunch with the team and talked about plans.")

    top = mem.recall("deepseek provider api key configuration", limit=1)
    assert top, "recall returned nothing"
    assert top[0]["turn_id"] == "t1", "expected the more RELEVANT turn first, not the newer one"


def test_recall_empty_query(tmp_path):
    mem = EpisodicMemory(path=tmp_path / "learning.sqlite")
    assert mem.recall("") == []


def test_recall_returns_matches(tmp_path):
    mem = EpisodicMemory(path=tmp_path / "learning.sqlite")
    mem.index_turn("a", "explain the taskboard contract gate",
                   "The contract gate enforces evidence before a board closes.")
    out = mem.recall("taskboard contract gate", limit=3)
    assert any(r["turn_id"] == "a" for r in out)
