import json
import os

from pathlib import Path

from core.backend_monitor import BackendMonitor, get_monitor, monitor_context, preview_provider_messages, preview_provider_response, redact_monitor_text, set_monitor
from interface.monitor_terminal import has_live_backend_work, read_events, render, resolve_log_path


def test_redact_monitor_text_masks_telegram_bot_tokens():
    text = redact_monitor_text("GET https://api.telegram.org/bot123456:ABCdef_secret/getUpdates")

    assert "123456:ABCdef_secret" not in text
    assert "/bot[redacted]" in text


def test_backend_monitor_writes_safe_jsonl(tmp_path):
    path = tmp_path / "backend_monitor.jsonl"
    monitor = BackendMonitor(path)

    monitor.emit_text("provider request running")

    text = path.read_text(encoding="utf-8")
    assert '"type": "backend_status"' in text
    assert "provider request running" in text


def test_backend_monitor_singleton_for_cross_subsystem_events(tmp_path):
    monitor = BackendMonitor(tmp_path / "backend_monitor.jsonl")

    set_monitor(monitor)

    assert get_monitor() is monitor
    set_monitor(None)



def test_backend_monitor_emit_is_best_effort(monkeypatch, tmp_path):
    monitor = BackendMonitor(tmp_path / "backend_monitor.jsonl")

    def broken_open(*_args, **_kwargs):
        raise OSError("no monitor disk")

    monkeypatch.setattr(Path, "open", broken_open)

    monitor.emit_text("must not raise")



def test_backend_monitor_can_be_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("MO_BACKEND_MONITOR_DISABLED", "1")
    path = tmp_path / "backend_monitor.jsonl"
    monitor = BackendMonitor(path)

    monitor.emit_text("hidden")

    assert not path.exists()



def test_backend_monitor_context_enriches_events_and_resets(tmp_path):
    path = tmp_path / "backend_monitor.jsonl"
    monitor = BackendMonitor(path)

    with monitor_context(turn_id="turn-1", session_id="session-1", surface="main"):
        monitor.emit("turn_start", {"message": "inside"})
    monitor.emit("turn_start", {"message": "outside"})

    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert events[0]["payload"]["turn_id"] == "turn-1"
    assert events[0]["payload"]["session_id"] == "session-1"
    assert "turn_id" not in events[1]["payload"]



def test_default_backend_monitor_uses_fresh_run_logs(monkeypatch, tmp_path):
    monkeypatch.delenv("MO_BACKEND_MONITOR_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    first = BackendMonitor()
    second = BackendMonitor()

    assert first.path != second.path
    assert first.path.parent == Path("logs/monitor")
    assert first.path.name.startswith("backend_monitor-")
    assert first.path.suffix == ".jsonl"


def test_default_backend_monitor_honors_isolated_log_dir(monkeypatch, tmp_path):
    isolated = tmp_path / "isolated-monitor"
    monkeypatch.setenv("MO_BACKEND_MONITOR_DIR", str(isolated))

    monitor = BackendMonitor()

    assert monitor.path.parent == isolated
    assert monitor.path.name.startswith("backend_monitor-")


def test_default_backend_monitor_cleanup_preserves_recent_live_logs(monkeypatch, tmp_path):
    monkeypatch.delenv("MO_BACKEND_MONITOR_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    runtime_dir = Path("logs/monitor")
    runtime_dir.mkdir(parents=True)
    for index in range(55):
        path = runtime_dir / f"backend_monitor-recent-{index:02d}.jsonl"
        path.write_text("{}\n", encoding="utf-8")

    BackendMonitor()

    assert len(list(runtime_dir.glob("backend_monitor-recent-*.jsonl"))) == 55


def test_explicit_backend_monitor_path_does_not_cleanup_runtime_logs(monkeypatch, tmp_path):
    monkeypatch.delenv("MO_BACKEND_MONITOR_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    runtime_dir = Path("logs/monitor")
    runtime_dir.mkdir(parents=True)
    old_log = runtime_dir / "backend_monitor-keep.jsonl"
    old_log.write_text("{}\n", encoding="utf-8")

    BackendMonitor(tmp_path / "isolated.jsonl")

    assert old_log.exists()


def test_open_window_requires_explicit_opt_in(monkeypatch, tmp_path):
    calls = []

    monkeypatch.delenv("MO_BACKEND_MONITOR_PATH", raising=False)
    monkeypatch.delenv("MO_OPEN_BACKEND_MONITOR", raising=False)
    monkeypatch.setattr("core.backend_monitor.subprocess.Popen", lambda *args, **kwargs: calls.append((args, kwargs)))
    monitor = BackendMonitor(tmp_path / "backend_monitor.jsonl")

    monitor.open_window()

    assert "MO_BACKEND_MONITOR_PATH" not in os.environ
    assert calls == []


def test_close_window_terminates_owned_process(tmp_path):
    calls = []

    class FakeProcess:
        def __init__(self):
            self.running = True

        def poll(self):
            return None if self.running else 0

        def terminate(self):
            calls.append("terminate")
            self.running = False

        def wait(self, timeout=None):
            calls.append(("wait", timeout))
            return 0

    monitor = BackendMonitor(tmp_path / "backend_monitor.jsonl")
    monitor.process = FakeProcess()

    monitor.close_window()

    assert calls == ["terminate", ("wait", 2)]


def test_monitor_cli_resolves_explicit_log_path():
    assert resolve_log_path(["logs/monitor/custom.jsonl"]) == Path("logs/monitor/custom.jsonl")


def test_monitor_accepts_live_events_and_drops_retired_types(tmp_path):
    path = tmp_path / "backend_monitor.jsonl"
    monitor = BackendMonitor(path)

    for event_type in (
        "memory_index", "memory_recall",
        "memory_cleanup", "memory_fts5_warning", "sandbox_guard",
        "sandbox_blocked", "goal_step", "goal_auditor", "goal_finish", "worker_event",
        "code_graph_context", "tool_compress", "live_steer", "session_quarantine",
        "turn_start", "turn_context", "turn_health", "turn_intercept", "turn_end", "turn_error",
        "session_event", "session_compact", "context_handoff", "ghost_event",
    ):
        monitor.emit(event_type, {"message": event_type})

    text = path.read_text(encoding="utf-8")
    assert "sandbox_blocked" in text
    assert "goal_finish" in text
    assert "live_steer" in text
    assert "session_quarantine" in text
    assert "turn_start" in text
    assert "turn_health" in text
    assert "session_compact" in text
    assert "context_handoff" in text
    assert "ghost_event" in text

    # Retired event types (emitter modules deleted, or never wired) must NOT pass the write gate.
    for retired in ("gateway_template", "gateway_audit", "design_quality", "lane_detect", "turn_route"):
        monitor.emit(retired, {"message": retired})
    body = path.read_text(encoding="utf-8")
    assert "gateway_template" not in body
    assert "turn_route" not in body



def test_provider_message_preview_hides_system_and_redacts_secrets():
    preview = preview_provider_messages([
        {"role": "system", "content": "private system prompt"},
        {"role": "user", "content": "use api_key=SECRET123 and token=SECRET456"},
    ])

    assert "system: [system prompt hidden]" in preview
    assert "private system prompt" not in preview
    assert "SECRET123" not in preview
    assert "SECRET456" not in preview
    assert "api_key=[redacted]" in preview
    assert "token=[redacted]" in preview


def test_provider_response_preview_includes_text_and_tool_names_without_secret():
    preview = preview_provider_response(
        "done with password=hunter2",
        [{"function": {"name": "read_file", "arguments": "{}"}}],
    )

    assert "done with password=[redacted]" in preview
    assert "hunter2" not in preview
    assert "tool calls: read_file" in preview


def test_monitor_repaints_only_for_live_backend_work():
    assert has_live_backend_work([]) is False
    assert has_live_backend_work([{"type": "provider_request", "payload": {}}]) is True
    assert has_live_backend_work([
        {"type": "provider_request", "payload": {}},
        {"type": "provider_response", "payload": {}},
    ]) is False
    assert has_live_backend_work([
        {"type": "tool_call", "payload": {}},
    ]) is True
    assert has_live_backend_work([
        {"type": "tool_call", "payload": {}},
        {"type": "tool_result", "payload": {}},
    ]) is False



def test_monitor_renders_ghost_surface_events(tmp_path, monkeypatch, capsys):
    path = tmp_path / "backend_monitor.jsonl"
    monitor = BackendMonitor(path)
    monitor.emit("ghost_event", {"kind": "ask", "route": "steer", "user_preview": "is mo stuck?"})
    monitor.emit("provider_request", {"surface": "ghost_panel", "request": "ghost-panel-1", "provider": "fast", "model": "m", "messages": 2, "tools": 0})
    monitor.emit("provider_response", {"surface": "ghost_panel", "request": "ghost-panel-1", "finish_reason": "stop", "tool_calls": 0, "content_chars": 12, "preview": "not stuck"})
    monitor.emit("tool_call", {"surface": "ghost_panel", "request": "ghost-scout", "tool": "git_status", "summary": ""})
    monitor.emit("tool_result", {"surface": "ghost_panel", "request": "ghost-scout", "tool": "git_status", "blocked": False, "error": False, "chars": 20})

    monkeypatch.setattr("interface.monitor_terminal.clear_screen", lambda: None)
    render(read_events(path))

    output = capsys.readouterr().out
    assert "ghost ask: route=steer user=is mo stuck?" in output
    assert "ghost_panel provider request #ghost-panel-1" in output
    assert "ghost_panel provider response #ghost-panel-1" in output
    assert "ghost_panel tool call #ghost-scout" in output



def test_monitor_renders_diagnostic_spine_events(tmp_path, monkeypatch, capsys):
    path = tmp_path / "backend_monitor.jsonl"
    monitor = BackendMonitor(path)
    monitor.emit("turn_start", {"template": "simple_chat", "route_source": "user", "messages": 2, "input": "hi"})
    monitor.emit("turn_context", {"extra_context_chars": 42, "flags": {"memory": True, "profile": False}})
    monitor.emit("session_event", {"kind": "autosave", "name": "main", "turns": 1, "messages": 3})
    monitor.emit("session_compact", {"stage": "pre_turn", "compacted_chains": 1, "saved_chars": 1200, "before_messages": 40, "after_messages": 36})
    monitor.emit("context_handoff", {"reason": "pressure", "new_session_id": "mo-handoff"})
    monitor.emit("turn_end", {"status": "ok", "duration_ms": 5, "result_chars": 12, "has_task_board": False})

    monkeypatch.setattr("interface.monitor_terminal.clear_screen", lambda: None)
    render(read_events(path))

    output = capsys.readouterr().out
    assert "turn start: simple_chat" in output
    assert "turn context: chars=42 active=memory" in output
    assert "session: autosave" in output
    assert "session compact: pre_turn" in output
    assert "context handoff: pressure" in output
    assert "turn end: ok" in output



def test_monitor_keeps_taskboard_visible_after_many_backend_events(tmp_path, monkeypatch, capsys):
    path = tmp_path / "backend_monitor.jsonl"
    monitor = BackendMonitor(path)
    monitor.emit("taskboard", {"rendered": "5 tasks (1 done, 4 open)\n→ Inspect files"})
    for index in range(100):
        monitor.emit("provider_request", {"request": index, "provider": "fake", "model": "m", "messages": 1, "tools": 1})

    monkeypatch.setattr("interface.monitor_terminal.clear_screen", lambda: None)
    render(read_events(path))

    output = capsys.readouterr().out
    assert "5 tasks (1 done, 4 open)" in output
    assert "→ Inspect files" in output
    assert "provider request #99" in output


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


def test_economy_summary_counts_tool_result_errors(tmp_path):
    """A failed-then-recovered tool shows up as tool_result.error, NOT a tool_error
    event — the economy count must catch it (else a swallowed error reads as 0)."""
    from core.backend_monitor import economy_summary
    mon = tmp_path / "backend_monitor-x.jsonl"
    rows = [
        {"type": "provider_request", "payload": {}},
        {"type": "provider_response", "payload": {}},
        {"type": "tool_call", "payload": {}},
        {"type": "tool_result", "payload": {"error": True}},   # failed edit_file, later recovered
        {"type": "tool_result", "payload": {"blocked": True}},
        {"type": "tool_compress", "payload": {}},
    ]
    mon.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    s = economy_summary(mon)
    assert s["provider_requests"] == 1
    assert s["tool_calls"] == 1
    assert s["tool_errors"] == 1
    assert s["sandbox_blocked"] == 1
    assert s["compression_events"] == 1


def test_devmode_closeout_writes_authoritative_economy(tmp_path, monkeypatch):
    """The runtime writes economy.md to the active session dir at closeout — the
    model never authors economy numbers (it faked them at T1734)."""
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("MO_BACKEND_MONITOR_DIR", str(tmp_path / "logs" / "monitor"))
    mondir = tmp_path / "logs" / "monitor"
    mondir.mkdir(parents=True)
    (mondir / "backend_monitor-1.jsonl").write_text(
        "\n".join(json.dumps(r) for r in [
            {"type": "provider_request", "payload": {}},
            {"type": "tool_call", "payload": {}},
            {"type": "tool_result", "payload": {"error": True}},
        ]),
        encoding="utf-8",
    )
    sess = tmp_path / "memory" / "devmode" / "2026-01-01T0000"
    sess.mkdir(parents=True)
    from core.tasking.agent_taskboard import AgentTaskBoard
    AgentTaskBoard._write_devmode_economy_record()
    eco = sess / "economy.md"
    assert eco.is_file()
    text = eco.read_text(encoding="utf-8")
    assert "Provider requests: 1" in text
    assert "errors: 1" in text
