import pytest
from pathlib import Path


def pytest_collection_modifyitems(config, items):
    """Auto-tier tests by file size so -m smoke/unit/full works without per-file tags."""
    for item in items:
        try:
            size = Path(item.fspath).stat().st_size
        except OSError:
            continue
        if size < 3000:
            item.add_marker(pytest.mark.smoke)
        elif size < 15000:
            item.add_marker(pytest.mark.unit)
        else:
            item.add_marker(pytest.mark.full)


@pytest.fixture(autouse=True)
def _isolate_default_backend_monitor_logs(monkeypatch, tmp_path):
    """Keep tests that use BackendMonitor() from polluting live logs/monitor."""
    monkeypatch.setenv("MO_BACKEND_MONITOR_DIR", str(tmp_path / "monitor"))


@pytest.fixture(autouse=True)
def _neutralize_import_time_project_cwd(monkeypatch):
    """mo.py setdefaults MO_PROJECT_CWD at collection import; a long pytest cwd
    then leaks into footer rendering and breaks 80-column footer tests. Tests
    that need the env var set it explicitly via monkeypatch.setenv."""
    monkeypatch.delenv("MO_PROJECT_CWD", raising=False)


@pytest.fixture(autouse=True)
def _operator_protocols_available(monkeypatch):
    """Protocol activation requires the operator's private (untracked) pack;
    the suite must pass on a clean clone, so tests force installed-state."""
    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")


@pytest.fixture(autouse=True)
def _isolate_runtime_state_home(monkeypatch, tmp_path):
    """Relative state writers (taskboard ledger, learning stores, goal runs,
    file ops) fall back to cwd-relative paths when no private home is set,
    which polluted the dev checkout's memory/ during suite runs (observed:
    junk 'MO AGENT is working' boards in memory/taskboards/taskboards.jsonl).
    Every test gets an isolated private state home; tests asserting
    legacy-relative behavior delete MO_STATE_HOME explicitly."""
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path / "state-home"))
