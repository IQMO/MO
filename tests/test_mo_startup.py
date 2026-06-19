import builtins

import mo
import interface.input as input_module


class DummyAgent:
    provider_name = "fake"
    model = "model"
    _active_lane = None
    active_lane = None
    sandbox_config = {"enabled": True}
    session = type("Session", (), {"total_tokens": 0, "token_log": [], "turn_count": 0})()
    profile = type("Profile", (), {"record_session": lambda self, **kw: None})()


class DummyGateway:
    def __init__(self, agent):
        self.agent = agent
        self.monitor = DummyMonitor()


class DummyMonitor:
    opened = False
    closed = False

    def open_window(self):
        type(self).opened = True

    def close_window(self):
        type(self).closed = True


def test_main_help_is_noninteractive(monkeypatch, capsys):
    called = {"agent": False}
    monkeypatch.setattr(mo, "create_agent", lambda _config: called.__setitem__("agent", True))

    mo.main(["--help"])

    out = capsys.readouterr().out
    assert "Usage:" in out
    assert "/help" in out
    assert called["agent"] is False


def test_main_version_is_noninteractive(monkeypatch, capsys):
    called = {"agent": False}
    monkeypatch.setattr(mo, "create_agent", lambda _config: called.__setitem__("agent", True))

    mo.main(["--version"])

    assert "MO v1.0" in capsys.readouterr().out
    assert called["agent"] is False


def test_main_provider_error_is_mo_native_without_traceback(monkeypatch, capsys):
    monkeypatch.setattr(mo.os.path, "exists", lambda _path: True)
    monkeypatch.setattr(mo, "_acquire_lock", lambda: True)
    monkeypatch.setattr(mo, "create_agent", lambda _config: (_ for _ in ()).throw(mo.ProviderError("No providers initialized.")))

    try:
        mo.main([])
    except SystemExit as exc:
        assert exc.code == 2

    captured = capsys.readouterr()
    assert "MO provider error: No providers initialized." in captured.err
    assert "  config: " in captured.err
    assert "Fix provider credentials" in captured.err
    assert "Traceback" not in captured.err


def test_main_does_not_open_runtime_monitor_without_opt_in(monkeypatch):
    DummyMonitor.opened = False
    DummyMonitor.closed = False
    monkeypatch.setattr(mo, "create_agent", lambda _config: DummyAgent())
    monkeypatch.setattr(mo, "Gateway", DummyGateway)
    monkeypatch.setattr(mo, "Console", lambda: None)
    monkeypatch.setattr(mo, "HAS_RICH", False)
    monkeypatch.setattr(input_module, "HAS_PROMPT_TOOLKIT", False)
    monkeypatch.setenv("MO_SKIP_LOCK", "1")
    monkeypatch.delenv("MO_OPEN_BACKEND_MONITOR", raising=False)
    monkeypatch.setattr(builtins, "input", lambda _prompt="": (_ for _ in ()).throw(EOFError()))

    mo.main()

    assert DummyMonitor.opened is False
    assert DummyMonitor.closed is False


def test_main_opens_runtime_monitor_with_bat_opt_in(monkeypatch):
    DummyMonitor.opened = False
    DummyMonitor.closed = False
    monkeypatch.setattr(mo, "create_agent", lambda _config: DummyAgent())
    monkeypatch.setattr(mo, "Gateway", DummyGateway)
    monkeypatch.setattr(mo, "Console", lambda: None)
    monkeypatch.setattr(mo, "HAS_RICH", False)
    monkeypatch.setattr(input_module, "HAS_PROMPT_TOOLKIT", False)
    monkeypatch.setenv("MO_OPEN_BACKEND_MONITOR", "1")
    monkeypatch.setenv("MO_SKIP_LOCK", "1")
    monkeypatch.setattr(builtins, "input", lambda _prompt="": (_ for _ in ()).throw(EOFError()))

    mo.main()

    assert DummyMonitor.opened is True
    assert DummyMonitor.closed is True


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
