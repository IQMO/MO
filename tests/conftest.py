import shutil
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def _no_checkout_state_pollution():
    """Keep test artifacts out of the project checkout. The PRODUCT never writes state
    to the cwd (it is private-by-default → ~/.mo). But some tests deliberately exercise
    project-local mode (cwd-relative state) and don't all chdir to a tmp dir, so they
    create memory/ or logs/ in the repo root. Left behind, that misleads a future dev
    into thinking it is real MO state. Remove any such folder a test newly created —
    only when absent before the test, so a folder a dev intentionally keeps is never
    touched. Cleanup of test artifacts, not a product behavior."""
    from core.path_defaults import repo_root
    root = Path(repo_root())
    watched = ("memory", "logs")
    before = {d for d in watched if (root / d).exists()}
    yield
    for d in watched:
        if d not in before and (root / d).exists():
            shutil.rmtree(root / d, ignore_errors=True)


def pytest_collection_modifyitems(config, items):
    """Auto-tier tests by file size so -m smoke/unit/full works without per-file tags."""
    for item in items:
        try:
            size = Path(item.fspath).stat().st_size
        except OSError:
            # Unknown size: default to the unit tier so the test still runs in
            # tiered sweeps instead of being silently excluded from every
            # -m smoke/unit/full filter.
            item.add_marker(pytest.mark.unit)
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
    Every test gets an isolated private state home. State is now private-by-default,
    so tests that want project-local behavior must opt out explicitly (MO_STATE_LOCAL=1
    via the per-module _legacy_state_lane fixture) — that opt-out resolves to cwd, never
    the real ~/.mo, so no global MO_HOME net is needed (and MO_HOME would wrongly
    override a test's own MO_STATE_HOME, since mo_home() prefers MO_HOME)."""
    monkeypatch.setenv("MO_STATE_HOME", str(tmp_path / "state-home"))
