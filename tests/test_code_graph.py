from __future__ import annotations

import json
from types import SimpleNamespace

from core.backend_monitor import BackendMonitor, set_monitor
from core.graph.code_graph import build_code_graph_context, should_include_code_graph_context


def test_code_graph_skips_light_chat(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "app.py").write_text("def hello():\n    return 'hi'\n", encoding="utf-8")

    assert should_include_code_graph_context("hi mo") is False
    assert build_code_graph_context("hi mo", cwd=str(tmp_path)) == ""


def test_code_graph_builds_private_map_and_selects_relevant_nodes(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "ghost_context.py").write_text(
        '"""Safe Ghost context builder."""\n\ndef build_ghost_context():\n    return "ctx"\n',
        encoding="utf-8",
    )
    (tmp_path / "core" / "agent.py").write_text(
        'from core.ghost.ghost_context import build_ghost_context\n\nclass Agent:\n    pass\n',
        encoding="utf-8",
    )

    context = build_code_graph_context("investigate ghost context", cwd=str(tmp_path))

    assert "MO Internal Code Map" in context
    assert "orientation only" in context
    assert "core/ghost_context.py" in context
    assert "build_ghost_context" in context
    assert (tmp_path / "memory" / "code_graph" / "knowledge-graph.json").exists()


def test_code_graph_refreshes_small_stale_delta(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("def old_name():\n    return 1\n", encoding="utf-8")
    first = build_code_graph_context("inspect old_name", cwd=str(tmp_path))
    assert "old_name" in first

    source.write_text("def new_name():\n    return 2\n", encoding="utf-8")
    second = build_code_graph_context("inspect new_name", cwd=str(tmp_path))

    assert "Status: incremental" in second
    assert "new_name" in second


def test_code_graph_can_be_disabled_by_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MO_CODE_GRAPH", "0")
    (tmp_path / "app.py").write_text("def hidden_when_disabled():\n    pass\n", encoding="utf-8")

    assert should_include_code_graph_context("investigate app") is False
    assert build_code_graph_context("investigate app", cwd=str(tmp_path)) == ""


def test_private_code_graph_env_no_longer_controls_feature(tmp_path, monkeypatch):
    monkeypatch.delenv("MO_CODE_GRAPH", raising=False)
    monkeypatch.setenv("MO_PRIVATE_CODE_GRAPH", "0")
    (tmp_path / "app.py").write_text("def still_visible():\n    pass\n", encoding="utf-8")

    assert should_include_code_graph_context("investigate app") is True


def test_code_graph_emits_safe_private_monitor_event(tmp_path):
    monitor = BackendMonitor(tmp_path / "monitor.jsonl")
    set_monitor(monitor)
    try:
        (tmp_path / "core").mkdir()
        (tmp_path / "core" / "ghost_routing.py").write_text("def recommend_ghost_route():\n    return None\n", encoding="utf-8")

        context = build_code_graph_context("investigate ghost routing", cwd=str(tmp_path))
    finally:
        set_monitor(None)

    assert "ghost_routing.py" in context
    events = [json.loads(line) for line in (tmp_path / "monitor.jsonl").read_text(encoding="utf-8").splitlines()]
    graph_events = [event for event in events if event["type"] == "code_graph_context"]
    assert graph_events
    payload = graph_events[-1]["payload"]
    assert payload["status"] in {"built", "fresh", "incremental"}
    assert payload["selected_count"] >= 1
    assert any("ghost_routing.py" in node_id for node_id in payload["node_ids"])


def test_code_graph_injects_for_all_non_greeting_queries(tmp_path):
    (tmp_path / "interface").mkdir()
    (tmp_path / "interface" / "main_terminal.py").write_text("class MoTui:\n    pass\n", encoding="utf-8")

    # Graph now injects for all non-greeting queries — even standalone builds.
    # The map shows existing code structure so MO doesn't recreate things.
    standalone = build_code_graph_context("build polished visual html page", cwd=str(tmp_path))
    assert standalone != "", "Graph should inject for standalone builds too"
    existing = build_code_graph_context("build current TUI component polish", cwd=str(tmp_path))
    assert "main_terminal.py" in existing or "MoTui" in existing

    # Greetings still skip
    from core.graph.code_graph import should_include_code_graph_context
    assert should_include_code_graph_context("hi") is False
    assert should_include_code_graph_context("hello mo") is False


def test_code_graph_is_project_root_specific(tmp_path):
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    (one / "alpha.py").write_text("def only_alpha():\n    pass\n", encoding="utf-8")
    (two / "beta.py").write_text("def only_beta():\n    pass\n", encoding="utf-8")

    alpha = build_code_graph_context("inspect alpha", cwd=str(one))
    beta = build_code_graph_context("inspect beta", cwd=str(two))

    assert "only_alpha" in alpha
    assert "only_beta" not in alpha
    assert "only_beta" in beta
    assert "only_alpha" not in beta


def test_code_graph_refuses_large_stale_delta(tmp_path):
    for index in range(30):
        (tmp_path / f"file_{index}.py").write_text(f"def shared_{index}():\n    return {index}\n", encoding="utf-8")
    first = build_code_graph_context("inspect shared", cwd=str(tmp_path))
    assert "shared" in first

    for index in range(30):
        (tmp_path / f"file_{index}.py").write_text(f"def changed_{index}():\n    return {index}\n", encoding="utf-8")

    assert build_code_graph_context("inspect changed", cwd=str(tmp_path)) == ""


def test_code_graph_does_not_index_secret_or_memory_files(tmp_path):
    (tmp_path / ".env").write_text("API_KEY=secret\n", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "note.py").write_text("def hidden():\n    pass\n", encoding="utf-8")
    (tmp_path / "visible.py").write_text("def public_func():\n    pass\n", encoding="utf-8")

    context = build_code_graph_context("inspect public hidden secret", cwd=str(tmp_path))

    assert "public_func" in context
    assert "secret" not in context.lower()
    assert "hidden" not in context


def test_code_graph_excludes_generated_protocol_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("MO_CODE_GRAPH_MAX_FILES", "2")
    (tmp_path / "core").mkdir()
    (tmp_path / "docs" / "comparisons" / "vs05" / "2026-06-08T0300").mkdir(parents=True)
    (tmp_path / "docs" / "devmode" / "2026-06-08T0400").mkdir(parents=True)
    (tmp_path / "core" / "agent.py").write_text("def real_source():\n    pass\n", encoding="utf-8")
    (tmp_path / "docs" / "comparisons" / "vs05" / "2026-06-08T0300" / "summary.md").write_text("generated artifact", encoding="utf-8")
    (tmp_path / "docs" / "devmode" / "2026-06-08T0400" / "summary.md").write_text("generated artifact", encoding="utf-8")

    context = build_code_graph_context("inspect real source generated artifact", cwd=str(tmp_path))

    assert "real_source" in context
    assert "generated artifact" not in context


def test_code_graph_indexes_typescript_symbols(tmp_path):
    (tmp_path / "ui.ts").write_text(
        "export function renderView() { return null }\nclass Panel {}\nconst useThing = () => null\n",
        encoding="utf-8",
    )

    context = build_code_graph_context("inspect renderView Panel useThing", cwd=str(tmp_path))

    assert "renderView" in context
    assert "Panel" in context
    assert "useThing" in context


def test_code_graph_max_files_env_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("MO_CODE_GRAPH_MAX_FILES", "1")
    (tmp_path / "a.py").write_text("def shared_a():\n    pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def shared_b():\n    pass\n", encoding="utf-8")

    assert build_code_graph_context("inspect shared", cwd=str(tmp_path)) == ""


def test_code_graph_profile_important_paths_boost_selection(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "core" / "target.py").write_text("def shared():\n    pass\n", encoding="utf-8")
    (tmp_path / "docs" / "target.py").write_text("def shared():\n    pass\n", encoding="utf-8")
    profile = SimpleNamespace(important_paths=["docs"], active_project=lambda: None)

    context = build_code_graph_context("inspect target shared", cwd=str(tmp_path), max_nodes=1, profile=profile)

    assert "docs/target.py" in context


import pytest as _pytest_state_lane


@_pytest_state_lane.fixture(autouse=True)
def _legacy_state_lane(monkeypatch):
    """This module asserts legacy project-relative state behavior; opt out of
    the conftest MO_STATE_HOME isolation (tests here chdir to tmp paths)."""
    monkeypatch.delenv("MO_STATE_HOME", raising=False)
