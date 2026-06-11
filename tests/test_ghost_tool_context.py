import json
from types import SimpleNamespace

from core.backend_monitor import BackendMonitor, set_monitor
from core.ghost.ghost_tool_context import build_ghost_tool_context
from core.ghost.ghost_routing import recommend_ghost_route


def test_ghost_tool_context_runs_readonly_game_scout_and_audits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "games").mkdir()
    (tmp_path / "games" / "zombie_game.py").write_text("# game\n", encoding="utf-8")
    audit_log = tmp_path / "logs" / "tool_audit.jsonl"
    agent = SimpleNamespace(
        allowed_roots=[str(tmp_path)],
        sandbox_config={"enabled": True, "audit_log": str(audit_log)},
        _provider_worker_id=lambda: "ghost-1",
    )
    suggestion = recommend_ghost_route("I want zombie game", main_busy=False)

    context = build_ghost_tool_context(agent, "I want zombie game", route_suggestion=suggestion)

    assert "Ghost read-only tool scout" in context
    assert "find_files:." in context
    assert "zombie_game.py" in context
    assert "read_file:" not in context
    rows = [json.loads(line) for line in audit_log.read_text(encoding="utf-8").splitlines()]
    assert rows
    assert {row["surface"] for row in rows} == {"ghost_panel"}
    assert {row["worker_id"] for row in rows} == {"ghost-1"}
    assert {row["tool"] for row in rows} <= {"git_status", "find_files"}
    assert not any(row["tool"] in {"write_file", "edit_file", "shell"} for row in rows)


def test_ghost_tool_context_emits_backend_monitor_tool_events(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "games").mkdir()
    (tmp_path / "games" / "zombie_game.py").write_text("# game\n", encoding="utf-8")
    audit_log = tmp_path / "logs" / "tool_audit.jsonl"
    agent = SimpleNamespace(
        allowed_roots=[str(tmp_path)],
        sandbox_config={"enabled": True, "audit_log": str(audit_log)},
    )
    monitor = BackendMonitor(tmp_path / "backend_monitor.jsonl")
    set_monitor(monitor)
    try:
        suggestion = recommend_ghost_route("I want zombie game", main_busy=False)
        build_ghost_tool_context(agent, "I want zombie game", route_suggestion=suggestion)
    finally:
        set_monitor(None)

    rows = [json.loads(line) for line in monitor.path.read_text(encoding="utf-8").splitlines()]
    assert any(row["type"] == "tool_call" and row["payload"].get("surface") == "ghost_panel" for row in rows)
    assert any(row["type"] == "tool_result" and row["payload"].get("request") == "ghost-scout" for row in rows)



def test_ghost_tool_context_skips_light_chat(tmp_path):
    agent = SimpleNamespace(allowed_roots=[str(tmp_path)], sandbox_config={"enabled": True})

    assert build_ghost_tool_context(agent, "hi ghost") == ""
