from __future__ import annotations

import json
from pathlib import Path

from core.graph.code_graph import build_code_graph_context
from core.graph.structural_graph import analyze_diff_impact, build_focused_map, build_structural_graph, graph_path, prt_impact_summary, select_context
from core.tasking.task_board import TaskBoard, TaskItem
from core.learning.workflow_learning import stage_structural_graph_candidates


def _write_graph(root: Path, *, compatibility: bool = False) -> None:
    out = root / ("graphify-out" if compatibility else "memory/structural_graph")
    out.mkdir(parents=True)
    graph = {
        "directed": True,
        "nodes": [
            {"id": "core_agent", "label": "Agent", "file_type": "code", "source_file": "core/agent.py", "source_location": "L30", "community": 1},
            {"id": "core_provider", "label": "Provider", "file_type": "code", "source_file": "core/provider.py", "source_location": "L10", "community": 1},
            {"id": "interface_tui", "label": "TUI", "file_type": "code", "source_file": "interface/tui.py", "source_location": "L5", "community": 2},
            {"id": "test_agent", "label": "test agent", "file_type": "code", "source_file": "tests/test_agent.py", "source_location": "L1", "community": 3},
        ],
        "links": [
            {"source": "interface_tui", "target": "core_agent", "relation": "calls", "confidence": "EXTRACTED", "source_file": "interface/tui.py"},
            {"source": "test_agent", "target": "core_agent", "relation": "references", "confidence": "EXTRACTED", "source_file": "tests/test_agent.py"},
            {"source": "core_agent", "target": "core_provider", "relation": "imports_from", "confidence": "EXTRACTED", "source_file": "core/agent.py"},
            {"source": "core_provider", "target": "core_agent", "relation": "imports_from", "confidence": "EXTRACTED", "source_file": "core/provider.py"},
        ],
    }
    (out / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    (out / ".structural_labels.json").write_text(json.dumps({"1": "Core Runtime", "2": "Interface"}), encoding="utf-8")
    (out / ".structural_analysis.json").write_text(json.dumps({
        "gods": [{"id": "core_agent", "label": "Agent", "degree": 3}],
        "surprises": [{"source": "TUI", "target": "Agent", "confidence": "EXTRACTED"}],
    }), encoding="utf-8")


def test_select_context_uses_structural_graph(tmp_path):
    _write_graph(tmp_path)

    context = select_context("investigate agent provider", cwd=tmp_path, max_chars=1800, max_nodes=4)

    assert "MO Internal Code Map" in context
    assert "Source: structural graph" in context
    assert "[Code map:" in context
    assert "Agent" in context
    assert "Core Runtime" in context
    assert "EXTRACTED" in context
    assert "Map slice id: structural-" in context


def test_code_graph_public_api_delegates_to_structural_graph_when_available(tmp_path):
    _write_graph(tmp_path)

    context = build_code_graph_context("inspect agent runtime", cwd=str(tmp_path))

    assert "Source: structural graph" in context
    assert "Agent" in context


def test_build_focused_map_combines_graph_and_taskboard(tmp_path):
    _write_graph(tmp_path)
    board = TaskBoard(tasks=[
        TaskItem("1", "Inspect runtime", "active", evidence=["read_file:core/agent.py"]),
        TaskItem("2", "Verify provider path", "pending"),
    ])

    result = build_focused_map(tmp_path, task_board=board, query="runtime provider")
    html = Path(result["path"]).read_text(encoding="utf-8")

    assert result["built"] is True
    assert result["tasks"] == 2
    assert "MO Focused Map" in html
    assert "Orientation only" in html
    assert "Inspect runtime" in html
    assert "core/agent.py" in html


def test_structural_graph_diff_impact_and_prt_summary(tmp_path):
    _write_graph(tmp_path)
    diff = "diff --git a/core/agent.py b/core/agent.py\n@@ -1 +1 @@\n-old\n+new\n"

    impacted = analyze_diff_impact(diff, root=tmp_path)
    summary = prt_impact_summary(diff, root=tmp_path)

    assert "interface/tui.py" in impacted
    assert "tests/test_agent.py" in impacted
    assert summary["available"] is True
    assert summary["affected_tests"] == ["tests/test_agent.py"]
    assert summary["community_count"] >= 2
    assert summary["import_cycles"]


def test_stage_structural_graph_candidates_is_inert(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_graph(tmp_path)

    result = stage_structural_graph_candidates(None, root=tmp_path)

    assert result["added"] >= 1
    path = Path(result["path"])
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["status"] == "candidate"
    assert records[0]["promotion"] == "requires explicit operator approval before active use"
    assert "graph" in records[0]["id"]


def test_native_build_creates_structural_map_and_handles_session_query(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "sessions.py").write_text(
        '"""Session management helpers."""\n\nclass SessionManager:\n    pass\n',
        encoding="utf-8",
    )
    (tmp_path / "core" / "agent.py").write_text(
        "from core.session.sessions import SessionManager\n\ndef start():\n    return SessionManager()\n",
        encoding="utf-8",
    )

    result = build_structural_graph(tmp_path)
    context = select_context("how does session management work", cwd=tmp_path, max_chars=1800, max_nodes=4)

    assert result["built"] is True
    assert "memory/structural_graph/graph.json" in result["path"].replace("\\", "/")
    assert "sessions.py" in context
    assert "Source: structural graph" in context


def test_compatibility_input_path_is_supported(tmp_path):
    _write_graph(tmp_path, compatibility=True)

    context = select_context("inspect agent runtime", cwd=tmp_path, max_chars=1800, max_nodes=4)

    assert graph_path(tmp_path).as_posix().endswith("graphify-out/graph.json")
    assert "Agent" in context


def test_structural_graph_community_strategy_depth2(tmp_path, monkeypatch):
    monkeypatch.setenv("MO_STRUCTURAL_COMMUNITY_STRATEGY", "path_depth2")
    (tmp_path / "core" / "alpha").mkdir(parents=True)
    (tmp_path / "core" / "beta").mkdir(parents=True)
    (tmp_path / "core" / "alpha" / "a.py").write_text("def alpha():\n    pass\n", encoding="utf-8")
    (tmp_path / "core" / "beta" / "b.py").write_text("def beta():\n    pass\n", encoding="utf-8")

    result = build_structural_graph(tmp_path)
    data = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    communities = {node["community"] for node in data["nodes"] if node["source_file"].startswith("core/")}

    assert result["built"] is True
    assert len(communities) == 2


def test_structural_graph_refreshes_small_delta(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("def old_name():\n    return 1\n", encoding="utf-8")
    first = build_structural_graph(tmp_path)
    assert first["built"] is True

    source.write_text("def new_name():\n    return 2\n", encoding="utf-8")
    second = build_structural_graph(tmp_path)
    data = json.loads(Path(second["path"]).read_text(encoding="utf-8"))

    assert second["status"] == "incremental"
    assert any(node.get("label") == "new_name" for node in data["nodes"])


def test_structural_graph_migrates_v1_graph_on_load(tmp_path):
    _write_graph(tmp_path)
    from core.graph.structural_graph import load_graph_data

    data = load_graph_data(tmp_path)

    assert data is not None
    assert data["version"] == "mo-structural-graph-v2"
    assert all("file_type" in node for node in data["nodes"])


def test_fuzzy_search_ranks_nodes_by_relevance(tmp_path):
    _write_graph(tmp_path)

    from core.graph.search import search

    results = search("agent provider", cwd=tmp_path)
    # Agent and Provider should rank top since both terms appear in their labels/files
    assert len(results) >= 2
    assert results[0]["label"] in ("Agent", "Provider")
    assert all("score" in r and "source_file" in r for r in results)


def test_fuzzy_search_returns_empty_when_no_match(tmp_path):
    _write_graph(tmp_path)

    from core.graph.search import search

    results = search("zzz_nonexistent_term_xyz", cwd=tmp_path)
    assert results == []


def test_get_callers_finds_upstream_dependencies(tmp_path):
    _write_graph(tmp_path)

    from core.graph.callgraph import get_callers

    # The test graph has: interface_tui -> core_agent (calls) and
    # test_agent -> core_agent (references), so core_agent has 2 callers
    results = get_callers("Agent", cwd=tmp_path)
    assert len(results) >= 1
    caller_labels = {r["caller_label"] for r in results}
    assert "TUI" in caller_labels
    assert all(r["callee_label"] == "Agent" for r in results)


def test_get_callees_finds_downstream_dependencies(tmp_path):
    _write_graph(tmp_path)

    from core.graph.callgraph import get_callees

    # interface_tui calls core_agent, so TUI's callees include Agent
    results = get_callees("TUI", cwd=tmp_path)
    assert len(results) >= 1
    assert any(r["callee_label"] == "Agent" for r in results)


def test_get_callers_returns_empty_for_unknown_symbol(tmp_path):
    _write_graph(tmp_path)

    from core.graph.callgraph import get_callers

    results = get_callers("nonexistent_symbol_xyz", cwd=tmp_path)
    assert results == []


def test_callgraph_builds_real_call_and_inherit_edges_from_source(tmp_path):
    """End-to-end guard: the BUILDER must emit call/inherit edges from real
    source, not just traverse hand-written fixture edges. Previously the builder
    produced only file-import edges, so caller/callee was empty on real code
    while the fixture-based tests still passed — this catches that regression.
    """
    (tmp_path / "mod_a.py").write_text(
        "class Base:\n    pass\n\n\ndef helper():\n    return 1\n",
        encoding="utf-8",
    )
    (tmp_path / "mod_b.py").write_text(
        "from mod_a import Base, helper\n\n\n"
        "class Child(Base):\n    def run(self):\n        return helper()\n",
        encoding="utf-8",
    )

    from core.graph.callgraph import get_callers

    helper_callers = get_callers("helper", cwd=tmp_path)
    assert helper_callers, "builder produced no caller edges for a real function call"
    assert any(r["relation"] == "calls" for r in helper_callers)

    base_callers = get_callers("Base", cwd=tmp_path)
    assert any(r["relation"] == "inherits" for r in base_callers), \
        "builder produced no inheritance edge for a real subclass"


def test_search_and_callgraph_importable_from_package(tmp_path):
    _write_graph(tmp_path)

    from core.graph import fuzzy_search, get_callers, get_callees

    assert callable(fuzzy_search)
    assert callable(get_callers)
    assert callable(get_callees)


import pytest as _pytest_state_lane


@_pytest_state_lane.fixture(autouse=True)
def _legacy_state_lane(monkeypatch, tmp_path):
    """This module asserts legacy project-relative state behavior; opt out of
    the conftest MO_STATE_HOME isolation (tests here chdir to tmp paths)."""
    monkeypatch.delenv("MO_STATE_HOME", raising=False)
    monkeypatch.delenv("MO_HOME", raising=False)
    monkeypatch.setenv("MO_STATE_LOCAL", "1")  # explicit project-local opt-out (state is private-by-default)
    monkeypatch.chdir(tmp_path)  # project-local state -> tmp, never the repo root
    monkeypatch.setenv("MO_PROJECT_CWD", str(tmp_path))
