import hashlib
import os
import shutil
import sys
import tempfile
import time
import pytest
from pathlib import Path


# Cache test-run bytecode OUTSIDE the checkout (keeps the tree clean — the prior
# `dont_write_bytecode` intent) instead of disabling it. Disabling it recompiled
# every module on every `pytest` invocation; a stable out-of-tree prefix caches
# across runs, so the suite starts far faster while the checkout stays cache-free.
sys.pycache_prefix = os.path.join(tempfile.gettempdir(), "mo-test-pycache")


@pytest.fixture(scope="session", autouse=True)
def _shared_repo_code_graph_cache():
    """Build the repo code/structural graph ONCE per run into a stable shared
    cache so every test and every xdist worker LOADS it instead of rebuilding.

    The graph cache keys off mo_home(), which the per-test state isolation resets
    every test, so every run_turn/handoff test re-parsed the whole repo (~14s of
    AST compile). Under -n auto the FIRST graph test in each test file triggered a
    cold build, and those builds ran concurrently across workers at startup —
    thrashing the CPU so each ballooned to 40-60s. Building once (one worker holds
    an exclusive lock, the rest wait for the ready marker and load) replaces that
    storm with a single ~25s build. The on-disk graph self-validates by file
    fingerprint, so the shared cache is safe.

    Scope is the REPO ROOT only — tests that build graphs on their own tmp project
    trees keep their real per-test paths, so staleness/incremental tests are
    unaffected. The cache dir is keyed by the repo's file fingerprints: any edit
    yields a fresh dir (a stale graph is never served) and an unchanged tree across
    runs reuses it (warm = no build at all)."""
    from core.path_defaults import repo_root
    from core.graph import code_graph
    from core.graph import structural_graph as sg

    root = Path(repo_root()).resolve()
    try:
        files = code_graph._discover_files(root)
    except Exception:
        yield
        return
    if not files or len(files) > code_graph._max_files():
        yield  # too large / nothing to index: leave production paths untouched
        return

    sig = code_graph._fps_signature(code_graph._fingerprints(root, files))
    digest = hashlib.sha256(root.as_posix().lower().encode("utf-8", "replace")).hexdigest()[:16]
    cache_dir = Path(tempfile.gettempdir()) / "mo-test-graph-cache" / f"{digest}-{sig[:16]}"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cg_file = cache_dir / "knowledge-graph.json"
    native_file = cache_dir / sg.STRUCTURAL_GRAPH_FILE
    ready = cache_dir / ".ready"

    real_cg_path = code_graph._graph_path
    real_native_path = sg.native_graph_path

    def _cg_path(root_, config=None):
        return cg_file if Path(root_).resolve() == root else real_cg_path(root_, config=config)

    def _native_path(root_=None, *, config=None):
        return native_file if root_ is not None and Path(root_).resolve() == root else real_native_path(root_, config=config)

    mp = pytest.MonkeyPatch()
    mp.setattr(code_graph, "_graph_path", _cg_path, raising=True)
    mp.setattr(sg, "native_graph_path", _native_path, raising=True)

    if not ready.exists():
        lock = cache_dir / ".build.lock"
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
        except FileExistsError:
            for _ in range(1200):  # ~120s: another worker is building
                if ready.exists():
                    break
                time.sleep(0.1)
        else:
            try:
                fps = code_graph._fingerprints(root, files)
                code_graph._save_graph(cg_file, code_graph._build_graph(root, files, fps))
                try:
                    sg.build_structural_graph(root)  # writes to the patched native path
                except Exception:
                    pass  # handoff tests fall back to on-demand build if this skips
                ready.write_text("ok", encoding="utf-8")
            finally:
                try:
                    lock.unlink()
                except OSError:
                    pass
    yield
    mp.undo()


# State must live under ~/.mo (or MO_STATE_HOME), NEVER the project checkout.
# Every state writer routes its default through resolve_state_path; this guard
# is the permanent backstop. If a checkout `memory/`/`logs/` appears during the
# run, a NEW writer bypassed the resolver — we FAIL the session loudly (not the
# old silent create-then-remove, which hid the problem) and then clean up so the
# tree is left tidy. A failing run points straight at the offending writer.
_WATCHED_STATE_DIRS = ("memory", "logs")
_GENERATED_CACHE_DIRS = ("__pycache__", ".pytest_cache", ".ruff_cache")


def _checkout_state_leaks(root: Path, before: set[str]) -> list[str]:
    return [d for d in _WATCHED_STATE_DIRS if d not in before and (root / d).exists()]


def _format_state_leak_message(root: Path, leaked: list[str]) -> str:
    sample = []
    for d in leaked:
        for f in sorted((root / d).rglob("*")):
            if f.is_file():
                sample.append(str(f.relative_to(root)))
    env_bits = {
        "cwd": str(Path.cwd()),
        "MO_STATE_HOME": os.environ.get("MO_STATE_HOME", ""),
        "MO_HOME": os.environ.get("MO_HOME", ""),
        "MO_STATE_LOCAL": os.environ.get("MO_STATE_LOCAL", ""),
        "MO_PROJECT_CWD": os.environ.get("MO_PROJECT_CWD", ""),
    }
    return (
        f"[STATE-POLLUTION] Test run created {leaked} in the project checkout "
        f"({root}). A state writer bypassed resolve_state_path() and wrote to cwd "
        "instead of ~/.mo. Find it and route its default through resolve_state_path.\n"
        "  env: " + ", ".join(f"{k}={v}" for k, v in env_bits.items()) + "\n"
        "  files: " + ", ".join(sample[:40])
    )


def _remove_checkout_state_dirs(root: Path, leaked: list[str]) -> None:
    for d in leaked:
        shutil.rmtree(root / d, ignore_errors=True)


def _remove_checkout_generated_caches(root: Path) -> None:
    for name in _GENERATED_CACHE_DIRS:
        if name == "__pycache__":
            targets = [p for p in root.rglob(name) if p.is_dir()]
        else:
            targets = [root / name] if (root / name).exists() else []
        for target in targets:
            shutil.rmtree(target, ignore_errors=True)


def pytest_configure(config):
    from core.path_defaults import repo_root
    root = Path(repo_root())
    config._state_dirs_before = {d for d in _WATCHED_STATE_DIRS if (root / d).exists()}


@pytest.fixture(autouse=True)
def _fail_fast_on_checkout_state_pollution(request, monkeypatch):
    """Fail at the offending test instead of only at session end."""
    _ = monkeypatch  # ensure this guard tears down before monkeypatch restores cwd/env
    if hasattr(request.config, "workerinput"):
        yield
        return
    from core.path_defaults import repo_root
    root = Path(repo_root())
    before = getattr(request.config, "_state_dirs_before", set())
    yield
    leaked = _checkout_state_leaks(root, before)
    if leaked:
        message = _format_state_leak_message(root, leaked)
        _remove_checkout_state_dirs(root, leaked)
        pytest.fail(message)


@pytest.fixture(autouse=True)
def _reset_module_state_singletons():
    """The knowledge-store singleton caches its resolved db path. Under the
    per-test state isolation, a singleton built in one test (esp. a legacy
    project-local lane, where the path resolves RELATIVE) would otherwise bleed
    into a later test with a different cwd and re-create memory/learning.sqlite
    in the checkout. Reset it around every test so each resolves its own path."""
    try:
        from core.learning import knowledge_store as _ks
        _ks._store = None
    except Exception:
        pass
    yield
    try:
        from core.learning import knowledge_store as _ks
        _ks._store = None
    except Exception:
        pass


def pytest_sessionfinish(session, exitstatus):
    _ = exitstatus
    # Only the xdist controller (or a non-xdist run) adjudicates the shared cwd.
    if hasattr(session.config, "workerinput"):
        return
    from core.path_defaults import repo_root
    root = Path(repo_root())
    before = getattr(session.config, "_state_dirs_before", set())
    leaked = _checkout_state_leaks(root, before)
    if leaked:
        print("\n" + _format_state_leak_message(root, leaked))
        _remove_checkout_state_dirs(root, leaked)
        session.exitstatus = 1
    _remove_checkout_generated_caches(root)


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
    # Agent startup can schedule an async graph refresh. Tests that exercise graph
    # refresh behavior call it directly; the rest of the suite keeps background
    # state writers off so failures are deterministic and never leak into the
    # checkout after a test's monkeypatch context changes.
    monkeypatch.setenv("MO_STRUCTURAL_GRAPH_AUTO_UPDATE", "0")
