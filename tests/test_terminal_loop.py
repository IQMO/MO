import os
import subprocess
import sys

from pathlib import Path
from types import SimpleNamespace

import interface.terminal_loop as terminal_loop


class DummyMonitor:
    def __init__(self):
        self.opened = 0
        self.closed = 0

    def open_window(self):
        self.opened += 1

    def close_window(self):
        self.closed += 1


def test_startup_identity_lines_reports_resolved_runtime_root():
    agent = SimpleNamespace(
        runtime_home=r"C:\Users\example\.mo",
        provider_name="opencode",
        model="deepseek-v4-pro",
    )
    lines = terminal_loop.startup_identity_lines(agent)
    assert lines[0] == "MO Agent"
    # Runtime root is derived from the actually-imported core package, so it
    # points at THIS repo (the one containing core/), never a hardcoded label.
    import core
    expected_root = os.path.dirname(os.path.dirname(os.path.abspath(core.__file__)))
    assert any(line == f"  runtime: {expected_root}" for line in lines)
    assert any("opencode / deepseek-v4-pro" in line for line in lines)
    assert any(r"C:\Users\example\.mo" in line for line in lines)


def test_startup_identity_lines_shows_project_when_cwd_differs(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    lines = terminal_loop.startup_identity_lines(SimpleNamespace())
    assert any(line.startswith("  project: ") for line in lines)


def test_startup_identity_lines_never_raises_on_bare_agent():
    # Bare agent (no attributes) must still produce a banner, not crash.
    lines = terminal_loop.startup_identity_lines(object())
    assert lines and lines[0] == "MO Agent"


def test_terminal_loop_import_does_not_load_main_tui():
    repo = Path(__file__).resolve().parents[1]
    code = "import sys; import interface.terminal_loop; raise SystemExit(1 if 'interface.main_terminal' in sys.modules else 0)"
    proc = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=repo,
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_run_main_loop_uses_native_scroll_by_default_even_when_prompt_toolkit_available(monkeypatch):
    calls = []

    class FakeTui:
        def __init__(self, agent, gateway):
            calls.append(("init", agent, gateway))

        def run(self):
            calls.append(("run",))

    agent = object()
    gateway = SimpleNamespace(monitor=DummyMonitor())

    monkeypatch.setattr(terminal_loop._input_module, "HAS_PROMPT_TOOLKIT", True)
    monkeypatch.setattr(terminal_loop.sys.stdin, "isatty", lambda: True)
    monkeypatch.delenv("MO_TUI", raising=False)
    monkeypatch.delenv("MO_OPEN_BACKEND_MONITOR", raising=False)
    monkeypatch.setattr(terminal_loop, "MoTui", FakeTui)
    monkeypatch.setattr(terminal_loop, "record_session", lambda value: calls.append(("record", value)))
    monkeypatch.setattr(terminal_loop, "run_native_terminal_loop", lambda *_args: calls.append(("native",)))
    monkeypatch.setattr(terminal_loop, "set_terminal_title", lambda title: calls.append(("title", title)))

    terminal_loop.run_main_loop(agent, gateway, console=None, has_rich=False)

    assert ("title", "MO") in calls
    assert ("native",) in calls
    assert ("record", agent) in calls
    assert not any(call[0] == "init" for call in calls)
    assert not any(call == ("run",) for call in calls)
    assert gateway.monitor.opened == 0
    assert gateway.monitor.closed == 0


def test_run_main_loop_uses_prompt_toolkit_tui_when_explicitly_enabled(monkeypatch):
    calls = []

    class FakeTui:
        def __init__(self, agent, gateway):
            calls.append(("init", agent, gateway))

        def run(self):
            calls.append(("run",))

    agent = object()
    gateway = SimpleNamespace(monitor=DummyMonitor())

    monkeypatch.setattr(terminal_loop._input_module, "HAS_PROMPT_TOOLKIT", True)
    monkeypatch.setattr(terminal_loop.sys.stdin, "isatty", lambda: True)
    monkeypatch.setenv("MO_TUI", "1")
    monkeypatch.delenv("MO_OPEN_BACKEND_MONITOR", raising=False)
    monkeypatch.setattr(terminal_loop, "MoTui", FakeTui)
    monkeypatch.setattr(terminal_loop, "record_session", lambda value: calls.append(("record", value)))
    monkeypatch.setattr(terminal_loop, "run_native_terminal_loop", lambda *_args: calls.append(("native",)))
    monkeypatch.setattr(terminal_loop, "set_terminal_title", lambda title: calls.append(("title", title)))

    terminal_loop.run_main_loop(agent, gateway, console=None, has_rich=False)

    assert ("title", "MO") in calls
    assert ("init", agent, gateway) in calls
    assert ("run",) in calls
    assert ("record", agent) in calls
    assert ("native",) not in calls
    assert gateway.monitor.opened == 0
    assert gateway.monitor.closed == 0


def test_run_main_loop_uses_native_fallback_and_closes_opt_in_monitor(monkeypatch):
    calls = []
    agent = object()
    gateway = SimpleNamespace(monitor=DummyMonitor())

    monkeypatch.setattr(terminal_loop._input_module, "HAS_PROMPT_TOOLKIT", False)
    monkeypatch.setattr(terminal_loop.sys.stdin, "isatty", lambda: True)
    monkeypatch.setenv("MO_OPEN_BACKEND_MONITOR", "1")
    monkeypatch.setattr(terminal_loop, "MoTui", lambda *_args: calls.append(("tui",)))
    monkeypatch.setattr(terminal_loop, "record_session", lambda value: calls.append(("record", value)))
    monkeypatch.setattr(terminal_loop, "run_native_terminal_loop", lambda *args: calls.append(("native", args)))
    monkeypatch.setattr(terminal_loop, "set_terminal_title", lambda title: calls.append(("title", title)))

    terminal_loop.run_main_loop(agent, gateway, console="console", has_rich=True)

    assert ("title", "MO") in calls
    assert calls[-2][0] == "native"
    assert calls[-1] == ("record", agent)
    assert gateway.monitor.opened == 1
    assert gateway.monitor.closed == 1
    assert not any(call == ("tui",) for call in calls)
