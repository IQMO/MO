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
    # Regression for a state-pollution bug: the focused-map writer used
    # root/memory/structural_graph unconditionally, ignoring private-by-default,
    # so focused_map.html landed in the checkout
    # even though graph.json went to ~/.mo/cache.
    from core.graph.structural_graph import build_focused_map, build_structural_graph

    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "agent.py").write_text(
        "from .provider import call\n\ndef run():\n    return call()\n", encoding="utf-8")
    (tmp_path / "core" / "provider.py").write_text(
        "def call():\n    return 1\n", encoding="utf-8")
    build_result = build_structural_graph(tmp_path)
    _assert_under_state_home(build_result["path"], "build_structural_graph()")

    result = build_focused_map(tmp_path, query="runtime provider")
    _assert_under_state_home(result["path"], "build_focused_map()")
    assert not (tmp_path / "memory").exists(), "focused map polluted the checkout with memory/"


def test_resolve_state_path_never_returns_cwd_memory():
    from core.path_defaults import resolve_state_path
    for rel in ("memory/learning.sqlite", "memory/goal-runs", "memory/review_history"):
        resolved = Path(resolve_state_path(rel)).resolve()
        assert not str(resolved).startswith(str((Path.cwd() / "memory").resolve())), \
            f"resolve_state_path({rel!r}) -> {resolved} points into the checkout"


def test_workflow_candidate_without_profile_routes_to_state_home():
    from core.learning.workflow_learning import record_workflow_candidate

    recorded = record_workflow_candidate(
        None,
        "From now on when I ask for audit work, verify files before reporting.",
        "ok",
    )

    out = _state_home() / "memory" / "workflow_candidates.jsonl"
    assert recorded is True
    assert out.is_file()
    _assert_under_state_home(out, "workflow candidate fallback")
    assert not (Path.cwd() / "memory" / "workflow_candidates.jsonl").exists()


def test_review_audit_default_routes_to_state_home(monkeypatch):
    from core.review.diff_review import ReviewReport, append_review_audit

    monkeypatch.setenv("MO_REVIEW_AUDIT_FORCE", "1")
    report = ReviewReport(
        diff_ref="HEAD",
        files_changed=0,
        additions=0,
        deletions=0,
        findings=[],
        score=5.0,
        unresolved_count=0,
        affected_tests=[],
        created_at=1.0,
    )

    append_review_audit(report)

    out = _state_home() / "logs" / "review_audit.jsonl"
    assert out.is_file()
    _assert_under_state_home(out, "review audit fallback")
    assert not (Path.cwd() / "logs" / "review_audit.jsonl").exists()


def test_generate_code_map_uses_state_home_tool_audit():
    import json

    from core.graph.generate_code_map import generate_code_map

    graph = _state_home() / "memory" / "structural_graph" / "graph.json"
    graph.parent.mkdir(parents=True, exist_ok=True)
    graph.write_text(
        json.dumps(
            {
                "nodes": [{"id": "file:core/agent.py", "type": "file", "source_file": "core/agent.py"}],
                "links": [],
            }
        ),
        encoding="utf-8",
    )
    audit = _state_home() / "logs" / "tool_audit.jsonl"
    audit.parent.mkdir(parents=True, exist_ok=True)
    audit.write_text(
        json.dumps(
            {
                "ts": 1.0,
                "surface": "goal",
                "worker_id": "route-test",
                "tool": "edit_file",
                "arguments": {"path": "core/agent.py"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = generate_code_map(graph, iterations=1)

    annotations = json.loads(Path(result["annotations_path"]).read_text(encoding="utf-8"))
    assert annotations["tasks"]["route-test"] == ["core/agent.py"]
    _assert_under_state_home(result["annotations_path"], "code map annotations")
    assert not (Path.cwd() / "logs" / "tool_audit.jsonl").exists()


def test_async_structural_graph_refresh_keeps_admitted_state_home(tmp_path, monkeypatch):
    """A background graph refresh can outlive temporary env changes; it must use
    the state home active when the refresh was admitted, not whatever env exists
    when the thread body finally runs."""
    import core.graph.structural_graph as sg

    monkeypatch.setenv("MO_STRUCTURAL_GRAPH_AUTO_UPDATE", "1")
    admitted_home = _state_home()
    later_home = tmp_path / "later-home"
    calls = []

    monkeypatch.setattr(sg, "graph_exists", lambda _root: True)
    monkeypatch.setattr(sg, "_refresh_command", lambda _root: [])

    def fake_build(_root, *, config=None, **_kwargs):
        calls.append(config)
        return {"built": True, "path": str(tmp_path / "graph.json")}

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            self.target = target

        def start(self):
            monkeypatch.setenv("MO_STATE_HOME", str(later_home))
            self.target()

    monkeypatch.setattr(sg, "build_structural_graph", fake_build)
    monkeypatch.setattr(sg.threading, "Thread", FakeThread)

    assert sg.maybe_update_graph_async(root=tmp_path, reason="test") is True
    assert calls
    assert Path(calls[0]["runtime"]["home"]).resolve() == admitted_home


def test_devmode_manifest_routes_to_state_home():
    """The runtime-owned DEVMODE manifest.json must land under the private state home
    (the bound session dir), never in the checkout."""
    from core.path_defaults import mo_home
    from core.tasking.agent_taskboard import AgentTaskBoard
    agent = AgentTaskBoard.__new__(AgentTaskBoard)
    sess = mo_home() / "memory" / "devmode" / "2026-01-08T0000"
    sess.mkdir(parents=True, exist_ok=True)
    agent._bind_active_devmode_dir_from_write({"path": str(sess / "summary.md")})
    agent._write_devmode_manifest_record(status="active", economy={"tool_errors": 0})
    manifest = sess / "manifest.json"
    assert manifest.is_file()
    _assert_under_state_home(manifest, "devmode manifest")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
