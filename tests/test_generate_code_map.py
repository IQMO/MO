from __future__ import annotations

import json
from pathlib import Path

from core.graph.generate_code_map import build_task_annotations, generate_code_map
from core.graph.structural_graph import build_structural_graph


def _write_graph(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "version": "mo-structural-graph-v1",
                "built_at": "2026-06-01T00:00:00Z",
                "built_at_commit": "abc123",
                "nodes": [
                    {"id": "file:core/agent.py", "label": "agent.py", "type": "file", "community": 5, "source_file": "core/agent.py"},
                    {"id": "function:core/agent.py:run", "label": "run", "type": "function", "community": 5, "source_file": "core/agent.py"},
                    {"id": "file:tests/test_agent.py", "label": "test_agent.py", "type": "file", "community": 14, "source_file": "tests/test_agent.py"},
                ],
                "links": [
                    {"source": "file:core/agent.py", "target": "function:core/agent.py:run", "relation": "contains"},
                    {"source": "file:tests/test_agent.py", "target": "file:core/agent.py", "relation": "references"},
                ],
            }
        ),
        encoding="utf-8",
    )


def test_generate_code_map_writes_html_annotations_and_cache(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    graph = tmp_path / "memory" / "structural_graph" / "graph.json"
    _write_graph(graph)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "tool_audit.jsonl").write_text(
        json.dumps({"ts": 10, "surface": "goal", "worker_id": "run-1", "tool": "edit_file", "arguments": {"path": "core/agent.py"}}) + "\n",
        encoding="utf-8",
    )

    first = generate_code_map(graph, iterations=1)
    second = generate_code_map(graph, iterations=1)

    html = Path(first["path"]).read_text(encoding="utf-8")
    annotations = json.loads(Path(first["annotations_path"]).read_text(encoding="utf-8"))
    assert first["nodes"] == 3
    assert first["links"] == 2
    assert first["bytes"] < 5_000_000
    assert second["cache_hit"] is True
    assert "Canvas" in html or "canvas" in html
    assert "core/agent.py" in html
    assert annotations["best_effort"] is True
    assert annotations["tasks"]["run-1"] == ["core/agent.py"]


def test_task_annotations_merge_goal_evidence_and_tool_audit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    goal_dir = tmp_path / "memory" / "goal-runs"
    goal_dir.mkdir(parents=True)
    (goal_dir / "run-2.json").write_text(
        json.dumps(
            {
                "run_id": "run-2",
                "started_at": 5,
                "finished_at": 15,
                "steps": [{"evidence": ["read_file:core/goal.py", "content:120chars"]}],
            }
        ),
        encoding="utf-8",
    )
    audit = tmp_path / "logs" / "tool_audit.jsonl"
    audit.parent.mkdir()
    audit.write_text(
        json.dumps({"ts": 10, "surface": "goal", "worker_id": "w-1", "tool": "write_file", "arguments": {"path": "tests/test_goal.py"}}) + "\n",
        encoding="utf-8",
    )

    annotations = build_task_annotations(goal_dir=goal_dir, tool_audit=audit, root=tmp_path)

    assert annotations["tasks"]["run-2"] == ["core/goal.py", "tests/test_goal.py"]
    assert annotations["tasks"]["w-1"] == ["tests/test_goal.py"]


def test_unified_map_embeds_groups_boards_and_work_overlay(tmp_path, monkeypatch):
    """The redesigned map carries package groups and taskboard work context."""
    monkeypatch.chdir(tmp_path)
    graph = tmp_path / "memory" / "structural_graph" / "graph.json"
    _write_graph(graph)
    ledger = tmp_path / "memory" / "taskboards" / "taskboards.jsonl"
    ledger.parent.mkdir(parents=True)
    monkeypatch.setenv("MO_TASKBOARD_LEDGER_PATH", str(ledger))
    ledger.write_text(
        json.dumps({
            "board_id": "board-test1",
            "title": "Fix agent loop",
            "state": "completed",
            "event": "completed",
            "created_at": 100.0,
            "updated_at": 200.0,
            "tasks": [{"id": "1", "title": "Verify fix", "status": "completed", "kind": "verify", "evidence": ["edit_file:core/agent.py"], "depends_on": []}],
        }) + "\n",
        encoding="utf-8",
    )

    result = generate_code_map(graph, iterations=1)
    html = Path(result["path"]).read_text(encoding="utf-8")

    assert "MO Agent — Unified Map" in html
    assert '"board_id":"board-test1"' in html
    assert "Fix agent loop" in html
    assert '"groups":[' in html
    assert '"name":"core"' in html  # package grouping
    assert '"name":"tests"' in html
    assert "orientation only" in html.lower()


def test_board_snapshots_keep_latest_entry_per_board(tmp_path, monkeypatch):
    from core.graph.generate_code_map import _board_snapshots

    ledger = tmp_path / "memory" / "taskboards" / "taskboards.jsonl"
    ledger.parent.mkdir(parents=True)
    monkeypatch.setenv("MO_TASKBOARD_LEDGER_PATH", str(ledger))
    lines = [
        json.dumps({"board_id": "b1", "title": "old", "state": "active", "created_at": 10.0, "updated_at": 10.0, "tasks": []}),
        json.dumps({"board_id": "b1", "title": "new", "state": "completed", "created_at": 10.0, "updated_at": 20.0, "tasks": []}),
        json.dumps({"board_id": "b2", "title": "other", "state": "active", "created_at": 15.0, "updated_at": 15.0, "tasks": []}),
    ]
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

    boards = _board_snapshots(tmp_path)

    assert [b["board_id"] for b in boards] == ["b1", "b2"]  # newest first
    assert boards[0]["title"] == "new"
    assert boards[0]["state"] == "completed"


def test_cluster_layout_is_collision_free_and_product_centered():
    """Clusters must never overlap, and product code (core) must sit nearer
    the center than tests/docs regardless of node counts."""
    import math
    from core.graph.generate_code_map import _package_cluster_layout

    nodes = []
    for i in range(60):  # tests is the biggest group
        nodes.append({"id": f"file:tests/t{i}.py", "type": "file", "group": "tests", "degree": 1})
    for i in range(20):
        nodes.append({"id": f"file:core/c{i}.py", "type": "file", "group": "core", "degree": 2})
    for i in range(15):
        nodes.append({"id": f"file:docs/d{i}.md", "type": "file", "group": "docs", "degree": 0})

    positions = _package_cluster_layout(nodes, [], iterations=1)

    def center(group):
        pts = [positions[n["id"]] for n in nodes if n["group"] == group]
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    core_c, tests_c, docs_c = center("core"), center("tests"), center("docs")
    assert math.hypot(*core_c) < math.hypot(*tests_c)
    assert math.hypot(*core_c) < math.hypot(*docs_c)
    # no two clusters share territory: distance between centers exceeds both spreads
    def spread(group, c):
        return max(math.hypot(positions[n["id"]][0] - c[0], positions[n["id"]][1] - c[1]) for n in nodes if n["group"] == group)
    for g1, c1 in (("core", core_c), ("tests", tests_c)):
        for g2, c2 in (("tests", tests_c), ("docs", docs_c)):
            if g1 == g2:
                continue
            assert math.hypot(c1[0] - c2[0], c1[1] - c2[1]) > spread(g1, c1) + spread(g2, c2)


def test_node_group_categorization():
    from core.graph.generate_code_map import _node_group

    assert _node_group("core/agent/agent_turn.py") == "core/agent"
    assert _node_group("core/paths.py") == "core"
    assert _node_group("interface/tui.py") == "interface"
    assert _node_group("tests/test_x.py") == "tests"
    assert _node_group("mo.py") == "root"
    assert _node_group("") == "root"


def test_structural_graph_build_triggers_code_map(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.py").write_text("def hello():\n    return 1\n", encoding="utf-8")

    result = build_structural_graph(tmp_path)

    assert result["built"] is True
    assert (tmp_path / "memory" / "structural_graph" / "code_map.html").exists()
    assert (tmp_path / "memory" / "structural_graph" / "task_annotations.json").exists()


import pytest as _pytest_state_lane


@_pytest_state_lane.fixture(autouse=True)
def _legacy_state_lane(monkeypatch, tmp_path):
    """This module asserts legacy project-relative state behavior; opt out of
    the conftest MO_STATE_HOME isolation (tests here chdir to tmp paths)."""
    monkeypatch.delenv("MO_STATE_HOME", raising=False)
    monkeypatch.delenv("MO_HOME", raising=False)
    monkeypatch.setenv("MO_STATE_LOCAL", "1")  # explicit project-local opt-out (state is private-by-default)
    from core.path_defaults import repo_root as _rr
    monkeypatch.setenv("MO_PROJECT_CWD", str(_rr()))
    monkeypatch.chdir(tmp_path)  # project-local state -> tmp, never the repo root
