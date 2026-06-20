"""State writers must route default paths to the private state home, never cwd.

Regression guard for the recurring "memory/ keeps appearing in the project
checkout" problem: every subsystem that defaults to a `memory/...` path must
resolve it through resolve_state_path (→ ~/.mo or MO_STATE_HOME), so default
construction NEVER writes into the project working directory. The autouse
conftest fixture sets MO_STATE_HOME to a tmp dir, so these defaults must land
under it.
"""
import os
from pathlib import Path

import pytest


def _state_home() -> Path:
    return Path(os.environ["MO_STATE_HOME"]).resolve()


def _assert_under_state_home(p, label):
    rp = Path(p).resolve()
    home = _state_home()
    cwd = Path.cwd().resolve()
    assert str(rp).startswith(str(home)), f"{label} -> {rp} is not under state home {home}"
    assert "memory" not in rp.relative_to(rp.anchor).parts or str(rp).startswith(str(home)), label
    # Hard rule: must not be inside the project checkout.
    assert not str(rp).startswith(str(cwd / "memory")), f"{label} -> {rp} polluted the checkout cwd"


def test_episodic_memory_default_routes_to_state_home():
    from core.learning.memory import EpisodicMemory
    _assert_under_state_home(EpisodicMemory().path, "EpisodicMemory()")


def test_knowledge_store_default_routes_to_state_home():
    from core.learning.knowledge_store import KnowledgeStore
    _assert_under_state_home(KnowledgeStore().path, "KnowledgeStore()")


def test_finding_pattern_store_default_routes_to_state_home():
    from core.review.finding_patterns import FindingPatterns
    _assert_under_state_home(FindingPatterns().history_dir, "FindingPatterns()")


def test_write_learning_suggestions_default_routes_to_state_home(tmp_path):
    from core.learning.proactive_learning import write_learning_suggestions
    out = write_learning_suggestions([])  # empty is fine; we only check the path
    _assert_under_state_home(out, "write_learning_suggestions()")


def test_focused_map_default_routes_to_state_home(tmp_path):
    # Regression for the [STATE-POLLUTION] finding (DEVMODE05 2026-06-20): the
    # focused-map writer used root/memory/structural_graph unconditionally,
    # ignoring private-by-default, so focused_map.html landed in the checkout
    # even though graph.json went to ~/.mo/cache.
    from core.graph.structural_graph import build_focused_map, build_structural_graph

    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "agent.py").write_text(
        "from .provider import call\n\ndef run():\n    return call()\n", encoding="utf-8")
    (tmp_path / "core" / "provider.py").write_text(
        "def call():\n    return 1\n", encoding="utf-8")
    build_structural_graph(tmp_path)

    result = build_focused_map(tmp_path, query="runtime provider")
    _assert_under_state_home(result["path"], "build_focused_map()")
    assert not (tmp_path / "memory").exists(), "focused map polluted the checkout with memory/"


def test_resolve_state_path_never_returns_cwd_memory():
    from core.path_defaults import resolve_state_path
    for rel in ("memory/learning.sqlite", "memory/goal-runs", "memory/review_history"):
        resolved = Path(resolve_state_path(rel)).resolve()
        assert not str(resolved).startswith(str((Path.cwd() / "memory").resolve())), \
            f"resolve_state_path({rel!r}) -> {resolved} points into the checkout"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
