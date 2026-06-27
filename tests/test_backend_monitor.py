import json
import os

from pathlib import Path

from core.backend_monitor import BackendMonitor, SAFE_EVENT_TYPES, get_monitor, monitor_context, preview_provider_messages, preview_provider_response, redact_monitor_text, set_monitor
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


def test_backend_monitor_allows_owner_integrity_audit_reporting_truth_event(tmp_path):
    path = tmp_path / "backend_monitor.jsonl"
    monitor = BackendMonitor(path)

    assert "owner_integrity_audit_reporting_truth" in SAFE_EVENT_TYPES
    monitor.emit("owner_integrity_audit_reporting_truth", {"tool_calls": 50})

    text = path.read_text(encoding="utf-8")
    assert '"type": "owner_integrity_audit_reporting_truth"' in text
    assert '"tool_calls": 50' in text


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
    monkeypatch.chdir(tmp_path)  # project-local state -> tmp, never the repo root
    monkeypatch.setenv("MO_PROJECT_CWD", str(tmp_path))


def test_economy_summary_counts_tool_result_errors(tmp_path):
    """A failed-then-recovered tool shows up as tool_result.error, NOT a tool_error
    event — the economy count must catch it (else a swallowed error reads as 0)."""
    from core.backend_monitor import economy_summary, format_economy_record
    mon = tmp_path / "backend_monitor-x.jsonl"
    rows = [
        {"type": "provider_request", "payload": {}},
        {"type": "provider_response", "payload": {}},
        {"type": "tool_call", "payload": {}},
        {"type": "tool_result", "payload": {"tool": "edit_file", "error": True}},
        {"type": "tool_result", "payload": {"tool": "read_file", "blocked": True}},
        {"type": "tool_compress", "payload": {}},
    ]
    mon.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    s = economy_summary(mon)
    assert s["provider_requests"] == 1
    assert s["tool_calls"] == 1
    assert s["tool_errors"] == 1
    assert s["sandbox_blocked"] == 1
    assert s["compression_events"] == 1
    assert s["error_tools"] == ["edit_file"]
    assert s["blocked_tools"] == ["read_file"]
    record = format_economy_record(s)
    assert "Error tools: edit_file" in record
    assert "Blocked tools: read_file" in record


def test_economy_summary_counts_provider_errors(tmp_path):
    """A provider empty-response retry shows as a provider_error event — the economy must
    count it and surface it in economy.md (T0450: the retry was lost from artifact truth)."""
    from core.backend_monitor import economy_summary, format_economy_record
    mon = tmp_path / "backend_monitor-pe.jsonl"
    rows = [
        {"type": "provider_request", "payload": {}},
        {"type": "provider_response", "payload": {}},
        {"type": "provider_error", "payload": {"reason": "empty provider response"}},
        {"type": "tool_call", "payload": {}},
    ]
    mon.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    s = economy_summary(mon)
    assert s["provider_errors"] == 1
    assert "errors: 1" in format_economy_record(s)  # surfaced in economy.md


def test_economy_writer_writes_runtime_manifest_under_bound_dir(tmp_path, monkeypatch):
    """The economy writer also projects a runtime-owned manifest.json into the bound dir
    with economy counts matching economy.md (acceptance criteria 1, 3)."""
    import json as _json
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("MO_BACKEND_MONITOR_DIR", str(tmp_path / "logs" / "monitor"))
    mondir = tmp_path / "logs" / "monitor"
    mondir.mkdir(parents=True)
    (mondir / "backend_monitor-1.jsonl").write_text("\n".join(_json.dumps(r) for r in [
        {"type": "provider_request", "payload": {"session_id": "mo-x", "route_source": "user"}},
        {"type": "provider_error", "payload": {"session_id": "mo-x", "route_source": "user"}},
        {"type": "tool_call", "payload": {"session_id": "mo-x", "route_source": "user"}},
        {"type": "tool_result", "payload": {"session_id": "mo-x", "route_source": "user", "error": True}},
    ]), encoding="utf-8")
    active = tmp_path / "memory" / "devmode" / "2026-01-07T0000"
    active.mkdir(parents=True)
    agent = _devmode_board_agent()
    agent._active_devmode_session_dir = active
    agent._devmode_run_session_ids = {"mo-x"}
    agent._write_devmode_economy_record()
    manifest = _json.loads((active / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "active"
    assert manifest["economy"]["tool_errors"] == 1   # equals economy.md
    assert manifest["economy"]["provider_errors"] == 1
    assert "errors: 1" in (active / "economy.md").read_text(encoding="utf-8")
    assert "provider_error_retry_present" in manifest["warnings"]
    # manifest landed under MO_STATE_HOME (the bound dir), not anywhere else
    assert (active / "manifest.json").is_file()


def test_manifest_tool_errors_equal_frozen_not_live(tmp_path, monkeypatch):
    """The manifest's tool_errors MUST equal economy.md (the frozen count) even when a
    later hook recomputes the LIVE monitor count — otherwise the manifest disagrees with
    economy.md, the exact cross-artifact drift it exists to prevent (observed live T1047:
    manifest said 5 live while economy.md/summary said the frozen 4)."""
    import json as _json
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("MO_BACKEND_MONITOR_DIR", str(tmp_path / "logs" / "monitor"))
    mondir = tmp_path / "logs" / "monitor"
    mondir.mkdir(parents=True)
    # Live monitor shows 5 tool errors...
    rows = [{"type": "provider_request", "payload": {"session_id": "mo-x", "route_source": "user"}}]
    rows += [{"type": "tool_result", "payload": {"session_id": "mo-x", "route_source": "user", "error": True}}
             for _ in range(5)]
    (mondir / "backend_monitor-1.jsonl").write_text("\n".join(_json.dumps(r) for r in rows), encoding="utf-8")
    active = tmp_path / "memory" / "devmode" / "2026-01-10T0000"
    active.mkdir(parents=True)
    agent = _devmode_board_agent()
    agent._active_devmode_session_dir = active
    agent._devmode_run_session_ids = {"mo-x"}
    agent._devmode_closeout_frozen_errors = 4  # ...but the run froze at 4
    # The complete-hook recomputes live (5) but must apply the frozen 4.
    agent._write_devmode_manifest_record(status="complete")
    m = _json.loads((active / "manifest.json").read_text(encoding="utf-8"))
    assert m["economy"]["tool_errors"] == 4   # frozen, equals economy.md — NOT the live 5
    assert m["economy"]["frozen_tool_errors"] == 4
    assert m["status"] == "complete"


def test_economy_summary_excludes_ghost_and_groups_handoff_segments(tmp_path):
    """Logical-run scoping (amendment #5): one per-process monitor file holds the Main-MO
    run + its handoff segment + interleaved Ghost/desktop turns. Excluding Ghost surfaces
    must drop the desktop turns while KEEPING both user-route segments (run + handoff)."""
    from core.backend_monitor import GHOST_SURFACES, economy_summary
    mon = tmp_path / "backend_monitor-run.jsonl"
    rows = [
        # Main-MO devmode run, first session segment
        {"type": "provider_request", "payload": {"session_id": "mo-1", "route_source": "user"}},
        {"type": "tool_call", "payload": {"session_id": "mo-1", "route_source": "user"}},
        # a Ghost/desktop turn interleaved (must be excluded)
        {"type": "provider_request", "payload": {"session_id": "mo-1", "route_source": "desktop"}},
        {"type": "tool_call", "payload": {"session_id": "mo-1", "route_source": "desktop"}},
        {"type": "tool_result", "payload": {"session_id": "mo-1", "route_source": "desktop", "error": True}},
        # same run continues after a context handoff (new session id, still route=user)
        {"type": "provider_request", "payload": {"session_id": "mo-handoff-2", "route_source": "user"}},
        {"type": "tool_call", "payload": {"session_id": "mo-handoff-2", "route_source": "user"}},
    ]
    mon.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    # Whole file (back-compat default) counts everything including the Ghost turn.
    whole = economy_summary(mon)
    assert whole["provider_requests"] == 3 and whole["tool_calls"] == 3 and whole["tool_errors"] == 1
    # Excluding Ghost surfaces keeps both user-route segments, drops the desktop turn + its error.
    scoped = economy_summary(mon, exclude_surfaces=GHOST_SURFACES)
    assert scoped["provider_requests"] == 2 and scoped["tool_calls"] == 2 and scoped["tool_errors"] == 0
    # Restricting to one segment's session_id counts only that segment.
    seg = economy_summary(mon, session_ids={"mo-1"}, exclude_surfaces=GHOST_SURFACES)
    assert seg["provider_requests"] == 1 and seg["tool_calls"] == 1


def test_economy_summary_excludes_events_by_surface_when_route_source_missing(tmp_path):
    """Some monitor events are surface-tagged but not route_source-tagged; those still
    must be excluded from a Main-MO run's economy."""
    from core.backend_monitor import GHOST_SURFACES, economy_summary
    mon = tmp_path / "backend_monitor-surface.jsonl"
    rows = [
        {"type": "provider_request", "payload": {"session_id": "mo-1", "surface": "terminal"}},
        {"type": "tool_call", "payload": {"session_id": "mo-1", "surface": "desktop"}},
        {"type": "tool_result", "payload": {"session_id": "mo-1", "surface": "desktop", "error": True}},
    ]
    mon.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    scoped = economy_summary(mon, session_ids={"mo-1"}, exclude_surfaces=GHOST_SURFACES)

    assert scoped["provider_requests"] == 1
    assert scoped["tool_calls"] == 0
    assert scoped["tool_errors"] == 0


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
    agent = _devmode_board_agent()
    agent._bind_active_devmode_dir_from_write({"path": str(sess / "summary.md")})
    agent._write_devmode_economy_record()
    eco = sess / "economy.md"
    assert eco.is_file()
    text = eco.read_text(encoding="utf-8")
    assert "Provider requests: 1" in text
    assert "errors: 1" in text


def test_devmode_economy_uses_active_monitor_not_mtime_latest(tmp_path, monkeypatch):
    """Multiple MO processes can leave monitor files in the same dir. The economy writer
    must read this process's active monitor, not whichever file has newest mtime."""
    from core.backend_monitor import BackendMonitor, set_monitor
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("MO_BACKEND_MONITOR_DIR", str(tmp_path / "logs" / "monitor"))
    mondir = tmp_path / "logs" / "monitor"
    mondir.mkdir(parents=True)
    active_log = mondir / "backend_monitor-active.jsonl"
    latest_log = mondir / "backend_monitor-latest.jsonl"
    active_log.write_text(json.dumps({"type": "provider_request", "payload": {}}) + "\n", encoding="utf-8")
    latest_log.write_text(json.dumps({"type": "tool_result", "payload": {"error": True}}) + "\n", encoding="utf-8")
    latest_log.touch()
    monitor = BackendMonitor(active_log)
    set_monitor(monitor)
    try:
        sess = tmp_path / "memory" / "devmode" / "2026-01-01T0100"
        sess.mkdir(parents=True)
        agent = _devmode_board_agent()
        agent._bind_active_devmode_dir_from_write({"path": str(sess / "summary.md")})
        agent._write_devmode_economy_record()
    finally:
        set_monitor(None)

    text = (sess / "economy.md").read_text(encoding="utf-8")
    assert "Provider requests: 1" in text
    assert "errors: 0" in text


def _devmode_board_agent():
    """Minimal AgentTaskBoard instance for exercising the economy writer/binder."""
    from core.tasking.agent_taskboard import AgentTaskBoard
    return AgentTaskBoard.__new__(AgentTaskBoard)


def test_devmode_runtime_creates_and_advertises_session_dir(tmp_path, monkeypatch):
    """DEVMODE output dirs are runtime-owned before the model writes any artifact."""
    import core.tasking.agent_taskboard as atb
    from core.tasking.devmode_manifest import SESSION_ARTIFACT_NAMES
    from datetime import datetime as real_datetime

    class FixedDatetime:
        @classmethod
        def now(cls):
            return real_datetime(2026, 1, 2, 3, 4)

    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(atb, "datetime", FixedDatetime)
    agent = _devmode_board_agent()

    target = agent._ensure_devmode_session_dir()

    assert target == tmp_path / "memory" / "devmode" / "2026-01-02T0304"
    assert target.is_dir()
    assert (target / "manifest.json").is_file()
    ctx = agent._devmode_runtime_output_context("start OWNER_MAINTENANCE")
    assert str(target) in ctx
    assert "do not create another" in ctx.lower()
    for name in SESSION_ARTIFACT_NAMES:
        assert str(target / name) in ctx


def test_devmode_output_blocks_wrong_session_dir(tmp_path, monkeypatch):
    """Wrong DEVMODE artifact dirs are blocked before they can create polluted outputs."""
    import core.tasking.agent_taskboard as atb
    from datetime import datetime as real_datetime

    class FixedDatetime:
        @classmethod
        def now(cls):
            return real_datetime(2026, 1, 2, 3, 4)

    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path))
    monkeypatch.setattr(atb, "datetime", FixedDatetime)
    agent = _devmode_board_agent()
    active = agent._ensure_devmode_session_dir()

    assert agent._devmode_output_path_block_reason(
        "start OWNER_MAINTENANCE", "write_file", {"path": str(active / "summary.md")}
    ) is None
    assert agent._devmode_output_path_block_reason(
        "start OWNER_MAINTENANCE", "edit_file", {"path": str(tmp_path / "memory" / "devmode" / "2026-01-02T0000" / "summary.md")}
    )
    from core.path_defaults import repo_root
    assert agent._devmode_output_path_block_reason(
        "start OWNER_MAINTENANCE", "write_file", {"path": str(Path(repo_root()) / "memory" / "devmode" / active.name / "summary.md")}
    )
    assert agent._devmode_output_path_block_reason(
        "start OWNER_MAINTENANCE", "edit_file", {"path": str(tmp_path / "memory" / "devmode" / "longitudinal.md")}
    ) is None
    assert agent._devmode_output_path_block_reason(
        "start OWNER_MAINTENANCE", "edit_file", {"path": str(tmp_path / "operator" / "devmode" / "OWNER_MAINTENANCE" / "adversarial-rotation.json")}
    ) is None


def test_devmode_economy_writes_only_to_bound_dir_not_mtime_latest(tmp_path, monkeypatch):
    """The economy record must land ONLY in the EXPLICIT active session dir bound from
    this run's own artifact writes — never the newest dir by mtime. The mtime heuristic
    let an aborted later run overwrite a PRIOR session's economy (corrupted T2121's 29/66
    with a stray 23/43). Here a prior dir is newer by mtime, but the binding points at the
    real active dir: the record lands in the bound dir and the prior dir is untouched."""
    import os, time
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("MO_BACKEND_MONITOR_DIR", str(tmp_path / "logs" / "monitor"))
    mondir = tmp_path / "logs" / "monitor"
    mondir.mkdir(parents=True)
    (mondir / "backend_monitor-1.jsonl").write_text(
        "\n".join(json.dumps(r) for r in [
            {"type": "tool_result", "payload": {"error": True}},
            {"type": "tool_result", "payload": {"error": True}},
        ]),
        encoding="utf-8",
    )
    dm = tmp_path / "memory" / "devmode"
    active = dm / "2026-01-01T0000"   # the run we are actually in
    prior = dm / "2026-01-02T0000"    # an UNRELATED dir that is NEWER by mtime
    active.mkdir(parents=True)
    prior.mkdir(parents=True)
    os.utime(prior, (time.time() + 10, time.time() + 10))  # prior is the mtime-latest
    agent = _devmode_board_agent()
    agent._bind_active_devmode_dir_from_write({"path": str(active / "summary.md")})
    agent._write_devmode_economy_record()
    assert (active / "economy.md").is_file()           # bound dir written
    assert not (prior / "economy.md").exists()          # mtime-latest NOT touched
    assert "errors: 2" in (active / "economy.md").read_text(encoding="utf-8")


def test_economy_writer_freezes_error_count_across_closeout_edits(tmp_path, monkeypatch):
    """Freeze: the FIRST closeout write captures the tool-error count; later writes — after
    more errors accumulate from closeout artifact edits — keep reporting the FROZEN count,
    so economy.md and the terminal gate never chase a moving number (the mo-1782179985
    N->N+1 loop that exhausted the turn budget)."""
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("MO_BACKEND_MONITOR_DIR", str(tmp_path / "logs" / "monitor"))
    mondir = tmp_path / "logs" / "monitor"
    mondir.mkdir(parents=True)
    monpath = mondir / "backend_monitor-1.jsonl"

    def write_monitor(n_err):
        rows = [{"type": "provider_request", "payload": {"session_id": "mo-x", "route_source": "user"}}]
        rows += [{"type": "tool_result", "payload": {"session_id": "mo-x", "route_source": "user", "error": True}}
                 for _ in range(n_err)]
        monpath.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")

    active = tmp_path / "memory" / "devmode" / "2026-01-05T0000"
    active.mkdir(parents=True)
    agent = _devmode_board_agent()
    agent._active_devmode_session_dir = active
    agent._devmode_run_session_ids = {"mo-x"}

    write_monitor(8)              # first closeout write sees 8 errors -> freezes at 8
    agent._write_devmode_economy_record()
    assert agent._devmode_closeout_frozen_errors == 8
    assert "errors: 8" in (active / "economy.md").read_text(encoding="utf-8")

    write_monitor(10)             # 2 more errors from closeout edits...
    agent._write_devmode_economy_record()
    eco = (active / "economy.md").read_text(encoding="utf-8")
    assert "errors: 8" in eco and "errors: 10" not in eco  # still the FROZEN 8


def test_economy_writer_freezes_error_tool_names_across_closeout_edits(tmp_path, monkeypatch):
    """The frozen terminal economy includes error_tools, not only tool_errors.

    Otherwise economy.md can say the frozen count while the closeout gate reads a later
    live error tool from the monitor, forcing the summary to own an error that was not in
    the frozen terminal ledger.
    """
    import json as _json
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("MO_BACKEND_MONITOR_DIR", str(tmp_path / "logs" / "monitor"))
    mondir = tmp_path / "logs" / "monitor"
    mondir.mkdir(parents=True)
    monpath = mondir / "backend_monitor-1.jsonl"

    def write_monitor(tools):
        rows = [{"type": "provider_request", "payload": {"session_id": "mo-x", "route_source": "user"}}]
        rows += [
            {"type": "tool_result", "payload": {"session_id": "mo-x", "route_source": "user", "tool": tool, "error": True}}
            for tool in tools
        ]
        monpath.write_text("\n".join(_json.dumps(r) for r in rows), encoding="utf-8")

    active = tmp_path / "memory" / "devmode" / "2026-01-05T0000"
    active.mkdir(parents=True)
    agent = _devmode_board_agent()
    agent._active_devmode_session_dir = active
    agent._devmode_run_session_ids = {"mo-x"}

    write_monitor(["shell"])
    agent._write_devmode_economy_record()
    assert agent._devmode_closeout_frozen_economy["error_tools"] == ["shell"]

    write_monitor(["shell", "edit_file"])
    agent._write_devmode_economy_record()
    assert agent._devmode_closeout_frozen_economy["error_tools"] == ["shell"]
    assert "Error tools: shell" in (active / "economy.md").read_text(encoding="utf-8")
    manifest = _json.loads((active / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["economy"]["error_tools"] == ["shell"]


def test_devmode_economy_isolates_one_main_run_from_another_in_same_file(tmp_path, monkeypatch):
    """Logical-run scoping must isolate one Main/user DEVMODE run from ANOTHER Main/user
    run that shares the same per-process monitor file — not just exclude Ghost/desktop.
    Run B's events (incl. its error) must never leak into run A's economy record, while
    run A's handoff segment IS counted with the original (grouped run)."""
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("MO_BACKEND_MONITOR_DIR", str(tmp_path / "logs" / "monitor"))
    mondir = tmp_path / "logs" / "monitor"
    mondir.mkdir(parents=True)
    rows = [
        # Run A (this run) — original segment + a handoff segment, all route=user
        {"type": "provider_request", "payload": {"session_id": "mo-A", "route_source": "user"}},
        {"type": "tool_call", "payload": {"session_id": "mo-A", "route_source": "user"}},
        {"type": "provider_request", "payload": {"session_id": "mo-handoff-A", "route_source": "user"}},
        # Run B — a DIFFERENT Main/user run in the same file; must be excluded
        {"type": "provider_request", "payload": {"session_id": "mo-B", "route_source": "user"}},
        {"type": "tool_call", "payload": {"session_id": "mo-B", "route_source": "user"}},
        {"type": "tool_result", "payload": {"session_id": "mo-B", "route_source": "user", "error": True}},
        # A Ghost/desktop turn tagged with run A's id — excluded by surface
        {"type": "tool_call", "payload": {"session_id": "mo-A", "route_source": "desktop"}},
    ]
    (mondir / "backend_monitor-1.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    active = tmp_path / "memory" / "devmode" / "2026-01-03T0000"
    active.mkdir(parents=True)
    agent = _devmode_board_agent()
    agent._active_devmode_session_dir = active
    agent._devmode_run_session_ids = {"mo-A", "mo-handoff-A"}  # run A's logical segments
    agent._write_devmode_economy_record()
    eco = (active / "economy.md").read_text(encoding="utf-8")
    assert "Provider requests: 2" in eco   # mo-A + mo-handoff-A, NOT mo-B
    assert "Tool calls: 1" in eco           # mo-A user tool only; mo-B + desktop excluded
    assert "errors: 0" in eco               # run B's error must NOT be counted


def test_devmode_economy_refuses_without_active_dir_binding(tmp_path, monkeypatch):
    """The exact T2121 corruption: an aborted run that created no dir of its own must
    NOT fall back to the newest existing dir. With no binding, the writer refuses and
    the prior session's dir is left completely untouched."""
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("MO_BACKEND_MONITOR_DIR", str(tmp_path / "logs" / "monitor"))
    mondir = tmp_path / "logs" / "monitor"
    mondir.mkdir(parents=True)
    (mondir / "backend_monitor-1.jsonl").write_text(
        json.dumps({"type": "provider_request", "payload": {}}), encoding="utf-8"
    )
    prior = tmp_path / "memory" / "devmode" / "2026-01-01T0000"
    prior.mkdir(parents=True)
    (prior / "economy.md").write_text("ORIGINAL — must not be overwritten", encoding="utf-8")
    agent = _devmode_board_agent()  # no binding set → simulates the boot-stalled aborted run
    agent._write_devmode_economy_record()
    assert (prior / "economy.md").read_text(encoding="utf-8") == "ORIGINAL — must not be overwritten"


def test_devmode_economy_binder_ignores_operator_pack_paths(tmp_path, monkeypatch):
    """The private operator devmode pack is NOT a session dir, even when referenced
    through a private-home operator/devmode-shaped path."""
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path))
    agent = _devmode_board_agent()
    agent._bind_active_devmode_dir_from_write({"path": str(tmp_path / "operator" / "devmode" / "OWNER_MAINTENANCE" / "x.md")})
    assert getattr(agent, "_active_devmode_session_dir", None) is None


def test_devmode_economy_reconciles_stale_summary_counts(tmp_path, monkeypatch):
    """The model writes summary.md BEFORE the closeout finishes, so its hand-counted
    economy numbers go stale by the closeout delta (observed live mo-1782155959:
    summary said 26 provider / 63 tools, authoritative economy.md said 29 / 66).
    When the runtime writes the authoritative economy.md it must also overwrite the
    stale counts on the summary's economy line — preserving any narration — so the
    two files can never disagree (single source of truth, runtime-owned)."""
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path))
    monkeypatch.setenv("MO_BACKEND_MONITOR_DIR", str(tmp_path / "logs" / "monitor"))
    mondir = tmp_path / "logs" / "monitor"
    mondir.mkdir(parents=True)
    rows = [{"type": "provider_request", "payload": {}} for _ in range(29)]
    rows += [{"type": "tool_call", "payload": {}} for _ in range(66)]
    rows += [{"type": "tool_result", "payload": {"error": True}}]
    rows += [{"type": "tool_compress", "payload": {}} for _ in range(5)]
    (mondir / "backend_monitor-1.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8"
    )
    active = tmp_path / "memory" / "devmode" / "2026-01-02T0000"
    active.mkdir(parents=True)
    (active / "summary.md").write_text(
        "# Summary\n"
        "- **Economy:** 26 provider requests, 63 tool calls, 0 tool errors "
        "(BENIGN — exploratory probe), 5 compressions\n"
        "## Closeout\n"
        "- [OWNER_MAINTENANCE COMPLETE] HEALTHY. 0 tool errors, all recovered.\n"
        "- **Tests:** 1 targeted test passes\n",
        encoding="utf-8",
    )
    agent = _devmode_board_agent()
    agent._bind_active_devmode_dir_from_write({"path": str(active / "summary.md")})
    agent._write_devmode_economy_record()
    out = (active / "summary.md").read_text(encoding="utf-8")
    assert "29 provider requests" in out
    assert "66 tool calls" in out
    assert "26 provider requests" not in out and "63 tool calls" not in out
    # The tool-error count is normalized on EVERY line that mentions it — the economy
    # line AND the closeout marker — so summary can't disagree with economy.md (watcher
    # T0450: economy line said 4 while ledger/closeout still said 3). Monitor has 1 error.
    assert "0 tool errors" not in out          # both the economy line AND closeout fixed
    assert out.count("1 tool error") >= 2      # both lines now show the authoritative 1
    assert "(BENIGN — exploratory probe)" in out  # narration preserved
    assert "1 targeted test passes" in out  # unrelated lines untouched
