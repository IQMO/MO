"""Canonical structural graph support for MO.

MO builds its native community code map in the active per-project graph cache
(``~/.mo/cache`` by default, project-local ``memory/structural_graph`` only when
local state is explicitly enabled) and can also consume a compatible external
``graphify-out/graph.json`` input when it exists. ``core.graph.code_graph`` is
the in-memory source indexer, not a second persisted or public API authority.
The graph is orientation only.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque
from functools import lru_cache
from pathlib import Path
from typing import Any
import traceback

from ..runtime.lock import RuntimeLock, _live_owner, _recent_lock_claim, release_runtime_lock
from ..profile import active_project_relative_path
from ..utils.atomic_write import atomic_write_json, atomic_write_text
from ..runtime.backend_monitor import get_monitor, monitor_phase, redact_monitor_text
from ..runtime.subprocess_flags import apply_windows_hidden_process_flags, run_with_file_capture
from ..utils.env_utils import int_env
from ..utils.number_utils import as_optional_int as _as_int
from ..state.paths import (
    private_state_enabled,
    project_cache_dir,
)
from ..utils.text_utils import DEFAULT_CONTEXT_STOPWORDS

STRUCTURAL_GRAPH_DIR = Path("memory") / "structural_graph"
COMPAT_STRUCTURAL_GRAPH_DIR = Path("graphify-out")
STRUCTURAL_GRAPH_FILE = "graph.json"
STRUCTURAL_STATUS_FILE = ".structural_status.json"
GRAPH_VERSION = "mo-structural-graph-v4"
COMPATIBLE_GRAPH_VERSIONS = {
    "mo-structural-graph-v1",
    "mo-structural-graph-v2",
    "mo-structural-graph-v3",
    "mo-structural-graph-v4",
    GRAPH_VERSION,
}
DEFAULT_GRAPH_REFRESH_TIMEOUT_SECONDS = 600
DEFAULT_GRAPH_CACHE_IDLE_SECONDS = 300.0
DEFAULT_CONTEXT_CHARS = 1400
DEFAULT_MAX_NODES = 8
SMALL_STALE_UPDATE_LIMIT = 24
CONFIDENCE_EXTRACTED = "EXTRACTED"
CONFIDENCE_INFERRED = "INFERRED"
CONFIDENCE_AMBIGUOUS = "AMBIGUOUS"
LEGACY_CONFIDENCE = "MO_LOCAL"
CONFIDENCE_LABELS = {CONFIDENCE_EXTRACTED, CONFIDENCE_INFERRED, CONFIDENCE_AMBIGUOUS}
NATIVE_EDGE_DEFAULTS = {
    "provenance": "mo-native-structural-graph",
    "direction": "forward",
    "weight": 1.0,
}

_DEPENDENCY_RELATIONS = {
    "calls", "references", "imports", "imports_from", "re_exports", "inherits", "extends",
    "implements", "uses", "mixes_in", "embeds", "semantically_similar_to", "passes_callback",
}
_IMPORT_RELATIONS = {"imports", "imports_from", "re_exports"}
_EXTRACTED_RELATIONS = {
    "contains", "imports", "imports_from", "re_exports", "method", "case_of",
    "doc_references", "declares_dependency", "declares_mcp_server", "references_env_var",
    "references_mcp_package",
}
_INFERRED_RELATIONS = {
    "calls", "references", "inherits", "extends", "implements", "uses", "mixes_in",
    "embeds", "semantically_similar_to", "passes_callback",
}
_SKIP_RELATIONS_FOR_CONTEXT = {"contains", "method", "case_of"}
_SKIP_RELATIONS_FOR_SURPRISES = _SKIP_RELATIONS_FOR_CONTEXT | {"doc_references"}
_STOPWORDS = DEFAULT_CONTEXT_STOPWORDS
_UPDATE_LOCK = threading.RLock()
# A running project's value coalesces a later lifecycle check, not another builder.
_UPDATE_RUNNING: dict[str, bool] = {}
_BUILD_LOCK_GUARD = threading.Lock()
_LOCAL_BUILD_LOCKS: set[str] = set()
_REFRESH_LOCK_GUARD = threading.Lock()
_LOCAL_REFRESH_LOCKS: set[str] = set()


def graph_settings(config: dict | None = None) -> dict:
    """Effective profile choices, with explicit process environment precedence."""
    from ..state.preferences import graph_preferences

    saved = graph_preferences(config)
    result = {}
    for key, env in (("enabled", "MO_STRUCTURAL_GRAPH"),
                     ("auto_build", "MO_STRUCTURAL_GRAPH_AUTOBUILD"),
                     ("context", "MO_CODE_GRAPH")):
        raw = os.environ.get(env)
        result[key] = {"value": saved.get(key, True) if raw is None else
                       raw.strip().lower() not in {"0", "false", "off", "no", "disabled"},
                       "managed_by": env if raw is not None else "", "saved": saved.get(key), "default": True}
    return result


def structural_graph_enabled() -> bool:
    return graph_settings()["enabled"]["value"]


def auto_build_enabled() -> bool:
    """Return True unless MO-native structural graph building is disabled."""
    return graph_settings()["auto_build"]["value"]


def should_include_code_graph_context(user_input: str) -> bool:
    """True when graph orientation may help the current turn."""
    if not graph_settings()["context"]["value"]:
        return False
    text = str(user_input or "").strip().lower()
    if not text:
        return False
    try:
        from ..runtime.turn_intent import classify_turn, looks_like_trivial_greeting

        return not looks_like_trivial_greeting(text) and classify_turn(user_input).include_code_graph_context
    except Exception:
        traceback.print_exc()
        return True


def build_project_orientation(
    user_input: str,
    *,
    cwd: str | None = None,
    max_chars: int = DEFAULT_CONTEXT_CHARS,
    max_nodes: int = DEFAULT_MAX_NODES,
    profile: Any | None = None,
    build_if_missing: bool = True,
) -> dict[str, str]:
    """Prepare bounded orientation, retaining each source's delivery boundary."""
    if not should_include_code_graph_context(user_input):
        return {}
    from .history import history_context
    from ..knowledge import build_project_knowledge_context

    root = project_root(cwd or os.getcwd())
    knowledge = history = structural = ""
    try:
        with monitor_phase("context_project_knowledge"):
            knowledge = build_project_knowledge_context(user_input, root, max_chars=max_chars // 3)
    except Exception:
        traceback.print_exc()
    try:
        with monitor_phase("context_project_history"):
            history = history_context(user_input, root, max_chars=max_chars // 3)
    except Exception:
        traceback.print_exc()
    try:
        status = graph_status(root) if build_if_missing else {}
        if build_if_missing and status.get("available") and status.get("stale"):
            build_structural_graph_isolated(root)
        with monitor_phase("context_code_graph"):
            structural = select_context(
                user_input,
                cwd=root,
                max_chars=max_chars - len(history) - len(knowledge) - 4,
                max_nodes=max_nodes,
                build_if_missing=build_if_missing,
                allow_stale=True,
                profile=profile,
            )
    except Exception:
        traceback.print_exc()
    return {key: value for key, value in (
        ("code_graph", structural),
        ("project_knowledge", knowledge),
        ("project_history", history),
    ) if value}


_REPO_PATH_RE = re.compile(r"(?:core|interface|tools|tests|skills)/[\w./-]+\.[A-Za-z0-9]+")


def relevant_node_paths(
    user_input: str,
    *,
    cwd: str | None = None,
    profile: Any | None = None,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> list[str]:
    """Return explicit and fresh graph-ranked source paths for convention scope."""
    paths: list[str] = []
    for raw in _REPO_PATH_RE.findall(str(user_input or "")):
        path = raw.strip("`*.,;:() ").replace("\\", "/")
        if path and path not in paths:
            paths.append(path)
    try:
        from .search import rank_graph_nodes

        root = project_root(cwd or os.getcwd())
        graph = load_graph_data(root)
        if graph and not graph_status(root).get("stale"):
            for node in rank_graph_nodes(graph, user_input, top_n=max_nodes):
                source_file = str(node.get("source_file") or "").replace("\\", "/")
                if source_file and source_file not in paths:
                    paths.append(source_file)
    except Exception:
        pass
    return paths


_PROJECT_ROOT_CACHE: dict[str, Path] = {}
_PROJECT_ROOT_CACHE_LIMIT = 64


def project_root(cwd: str | Path | None = None) -> Path:
    """Resolve a project root using git when available.

    Memoized by the input cwd: the git toplevel for a directory is stable within
    a session, and the path helpers below (graph_path, native_graph_path,
    graph_status, load_graph_data, ...) call this ~13 times per select_context.
    Without the cache that is ~13 `git rev-parse` subprocess spawns per turn
    (~260ms of pure process overhead) — the real cost behind the "slow" per-turn
    code-graph context, not the node ranking.
    """
    key = str(cwd or os.getcwd())
    cached = _PROJECT_ROOT_CACHE.get(key)
    if cached is not None:
        return cached
    path = Path(key).resolve()
    result = path
    try:
        proc = run_with_file_capture(["git", "rev-parse", "--show-toplevel"], cwd=str(path), timeout=3)
        if proc.returncode == 0 and proc.stdout.strip():
            result = Path(proc.stdout.strip()).resolve()
    except Exception:
        traceback.print_exc()
    if len(_PROJECT_ROOT_CACHE) >= _PROJECT_ROOT_CACHE_LIMIT:
        _PROJECT_ROOT_CACHE.clear()
    _PROJECT_ROOT_CACHE[key] = result
    return result


def native_graph_path(root: str | Path | None = None, *, config: dict[str, Any] | None = None) -> Path:
    root_path = project_root(root)
    # Private-by-default: the native graph cache lives under ~/.mo/cache, never the
    # project tree — unless the user explicitly opted into project-local state.
    if private_state_enabled(config):
        return project_cache_dir("structural_graph", root_path, config=config) / STRUCTURAL_GRAPH_FILE
    return root_path / STRUCTURAL_GRAPH_DIR / STRUCTURAL_GRAPH_FILE


def compatibility_graph_path(root: str | Path | None = None) -> Path:
    return project_root(root) / COMPAT_STRUCTURAL_GRAPH_DIR / STRUCTURAL_GRAPH_FILE


def _prune_stale_graph_caches(
    config: dict[str, Any] | None = None,
    *,
    active_root: str | Path | None = None,
    builder_version: str | None = None,
) -> None:
    """Delete stale canonical caches and retire the former duplicate cache.

    Canonical caches are reproducible, hash-named directories under
    ``~/.mo/cache/structural_graph``. Keep the active project and any project
    with an in-flight build; retire empty/corrupt, deleted-root, and obsolete
    entries. ``cache/code_graph`` was the same logical graph in a raw schema, so
    every entry there is retired after the canonical build lock is held.
    """
    if not private_state_enabled(config):
        return
    import shutil

    structural_cache_dir = project_cache_dir("structural_graph", "", config=config).parent
    active_key = (
        project_cache_dir("structural_graph", active_root, config=config).name
        if active_root is not None else ""
    )
    expected_builder = str(builder_version or "")

    def cache_in_use(directory: Path) -> bool:
        # A refresh owns its directory before the helper acquires the build
        # lock. Pruning must respect both existing lifecycle owners.
        for name, local in ((".build.lock", _LOCAL_BUILD_LOCKS), (".refresh.lock", _LOCAL_REFRESH_LOCKS)):
            lock = directory / name
            if str(lock.resolve(strict=False)) in local or _live_owner(lock) or _recent_lock_claim(lock):
                return True
        return False

    cache_dir = structural_cache_dir
    if cache_dir.is_dir():
        for entry in sorted(cache_dir.iterdir()):
            if (
                not entry.is_dir()
                or entry.is_symlink()
                or entry.name == active_key
                or cache_in_use(entry)
            ):
                continue
            data: dict[str, Any] = {}
            try:
                meta_file = entry / STRUCTURAL_GRAPH_FILE
                if meta_file.is_file():
                    loaded = json.loads(meta_file.read_text(encoding="utf-8"))
                    data = loaded if isinstance(loaded, dict) else {}
            except Exception:
                data = {}
            project = data.get("project") if isinstance(data.get("project"), dict) else {}
            root = str(project.get("root") or "")
            schema_current = (
                not expected_builder
                or (
                    data.get("version") == GRAPH_VERSION
                    and data.get("builder_version") == expected_builder
                )
            )
            if data and root and Path(root).is_dir() and schema_current:
                continue
            try:
                if entry.resolve(strict=False).parent == cache_dir.resolve(strict=False):
                    shutil.rmtree(entry)
            except Exception:
                pass

    legacy_dir = project_cache_dir("code_graph", "", config=config).parent
    if legacy_dir.is_dir():
        for entry in sorted(legacy_dir.iterdir()):
            try:
                if entry.name != active_key and cache_in_use(structural_cache_dir / entry.name):
                    continue
                if (
                    entry.is_dir()
                    and not entry.is_symlink()
                    and entry.resolve(strict=False).parent == legacy_dir.resolve(strict=False)
                ):
                    shutil.rmtree(entry)
            except Exception:
                pass


def graph_path(root: str | Path | None = None, *, config: dict[str, Any] | None = None) -> Path:
    root_path = project_root(root)
    native = native_graph_path(root_path, config=config)
    if native.is_file():
        return native
    compat = compatibility_graph_path(root_path)
    if compat.is_file():
        return compat
    return native


def analysis_path(root: str | Path | None = None) -> Path:
    return graph_path(root).parent / ".structural_analysis.json"


def labels_path(root: str | Path | None = None) -> Path:
    return graph_path(root).parent / ".structural_labels.json"


def graph_exists(root: str | Path | None = None) -> bool:
    return structural_graph_enabled() and graph_path(root).is_file()


def _acquire_graph_build_lock(root_path: Path, *, config: dict[str, Any] | None = None) -> tuple[RuntimeLock | None, int | None]:
    lock_path = native_graph_path(root_path, config=config).parent / ".build.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_key = str(lock_path.resolve(strict=False))
    with _BUILD_LOCK_GUARD:
        if lock_key in _LOCAL_BUILD_LOCKS:
            return None, os.getpid()
        _LOCAL_BUILD_LOCKS.add(lock_key)
    for _attempt in range(2):
        owner = _live_owner(lock_path)
        if owner:
            with _BUILD_LOCK_GUARD:
                _LOCAL_BUILD_LOCKS.discard(lock_key)
            return None, owner
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            owner = _live_owner(lock_path)
            if owner or _recent_lock_claim(lock_path):
                with _BUILD_LOCK_GUARD:
                    _LOCAL_BUILD_LOCKS.discard(lock_key)
                return None, owner or 0
            try:
                lock_path.unlink()
            except OSError:
                with _BUILD_LOCK_GUARD:
                    _LOCAL_BUILD_LOCKS.discard(lock_key)
                return None, 0
            continue
        except OSError:
            with _BUILD_LOCK_GUARD:
                _LOCAL_BUILD_LOCKS.discard(lock_key)
            return None, 0
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        return RuntimeLock(lock_path, os.getpid()), None
    with _BUILD_LOCK_GUARD:
        _LOCAL_BUILD_LOCKS.discard(lock_key)
    return None, 0


def _release_graph_build_lock(lock: RuntimeLock | None) -> None:
    if lock is not None:
        with _BUILD_LOCK_GUARD:
            _LOCAL_BUILD_LOCKS.discard(str(lock.path.resolve(strict=False)))
    release_runtime_lock(lock)


def _graph_refresh_lock_path(root_path: Path) -> Path:
    return native_graph_path(root_path).parent / ".refresh.lock"


def _graph_refresh_in_progress(root_path: Path) -> bool:
    """Report an active refresh without exposing lock ownership details."""
    lock_path = _graph_refresh_lock_path(root_path)
    lock_key = str(lock_path.resolve(strict=False))
    with _REFRESH_LOCK_GUARD:
        if lock_key in _LOCAL_REFRESH_LOCKS:
            return True
    return bool(_live_owner(lock_path) or _recent_lock_claim(lock_path))


def _acquire_graph_refresh_lock(root_path: Path) -> RuntimeLock | None:
    """Claim one cross-process refresh-launch lease for a project."""
    lock_path = _graph_refresh_lock_path(root_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_key = str(lock_path.resolve(strict=False))
    with _REFRESH_LOCK_GUARD:
        if lock_key in _LOCAL_REFRESH_LOCKS:
            return None
        _LOCAL_REFRESH_LOCKS.add(lock_key)
    for _attempt in range(2):
        owner = _live_owner(lock_path)
        if owner:
            with _REFRESH_LOCK_GUARD:
                _LOCAL_REFRESH_LOCKS.discard(lock_key)
            return None
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if _live_owner(lock_path) or _recent_lock_claim(lock_path):
                with _REFRESH_LOCK_GUARD:
                    _LOCAL_REFRESH_LOCKS.discard(lock_key)
                return None
            try:
                lock_path.unlink()
            except OSError:
                with _REFRESH_LOCK_GUARD:
                    _LOCAL_REFRESH_LOCKS.discard(lock_key)
                return None
            continue
        except OSError:
            with _REFRESH_LOCK_GUARD:
                _LOCAL_REFRESH_LOCKS.discard(lock_key)
            return None
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
        return RuntimeLock(lock_path, os.getpid())
    with _REFRESH_LOCK_GUARD:
        _LOCAL_REFRESH_LOCKS.discard(lock_key)
    return None


def _release_graph_refresh_lock(lock: RuntimeLock | None) -> None:
    if lock is not None:
        with _REFRESH_LOCK_GUARD:
            _LOCAL_REFRESH_LOCKS.discard(str(lock.path.resolve(strict=False)))
    release_runtime_lock(lock)


_GRAPH_DATA_CACHE: dict[str, tuple[str, dict[str, Any]]] = {}
_GRAPH_DATA_LOCK = threading.RLock()
_GRAPH_TOPOLOGY_CACHE: dict[int, tuple[
    dict[str, Any],
    list[dict[str, Any]],
    dict[str, list[tuple[str, dict[str, Any], bool]]],
]] = {}
_GRAPH_TOPOLOGY_LOCK = threading.Lock()
_GRAPH_CACHE_LEASE_LOCK = threading.Lock()
_GRAPH_CACHE_RELEASE_TIMER: threading.Timer | None = None
_GRAPH_CACHE_LEASE = 0


def _clear_derived_query_caches() -> None:
    """Drop every object-derived index when the active graph object changes."""
    with _GRAPH_TOPOLOGY_LOCK:
        _GRAPH_TOPOLOGY_CACHE.clear()
    try:
        from . import search as search_module

        search_module.clear_search_index()
    except Exception:
        pass


def _arm_graph_cache_release() -> None:
    """Release the one parsed graph and its indexes after an idle query burst."""
    global _GRAPH_CACHE_LEASE, _GRAPH_CACHE_RELEASE_TIMER
    with _GRAPH_CACHE_LEASE_LOCK:
        _GRAPH_CACHE_LEASE += 1
        lease = _GRAPH_CACHE_LEASE
        if _GRAPH_CACHE_RELEASE_TIMER is not None:
            _GRAPH_CACHE_RELEASE_TIMER.cancel()
        timer = threading.Timer(
            DEFAULT_GRAPH_CACHE_IDLE_SECONDS,
            _release_graph_runtime_caches_if_idle,
            args=(lease,),
        )
        timer.daemon = True
        _GRAPH_CACHE_RELEASE_TIMER = timer
        timer.start()


def _cancel_graph_cache_release() -> None:
    global _GRAPH_CACHE_LEASE, _GRAPH_CACHE_RELEASE_TIMER
    with _GRAPH_CACHE_LEASE_LOCK:
        _GRAPH_CACHE_LEASE += 1
        timer = _GRAPH_CACHE_RELEASE_TIMER
        _GRAPH_CACHE_RELEASE_TIMER = None
        if timer is not None:
            timer.cancel()


def _release_graph_runtime_caches_if_idle(lease: int) -> None:
    """Drop derived memory only when no graph read renewed the lease."""
    global _GRAPH_CACHE_RELEASE_TIMER
    with _GRAPH_CACHE_LEASE_LOCK:
        if lease != _GRAPH_CACHE_LEASE:
            return
        _GRAPH_CACHE_RELEASE_TIMER = None
        # Keep renewal serialized through the clear. A read that arrives while
        # expiry is running will then reload and receive its own full lease.
        with _GRAPH_DATA_LOCK:
            _GRAPH_DATA_CACHE.clear()
        _clear_derived_query_caches()
        try:
            import gc

            gc.collect()
        except Exception:
            pass


def invalidate_graph_runtime_caches(root: str | Path | None = None) -> None:
    """Invalidate parsed graph data and all indexes derived from its object."""
    _cancel_graph_cache_release()
    with _GRAPH_DATA_LOCK:
        if root is None:
            _GRAPH_DATA_CACHE.clear()
        else:
            root_path = project_root(root)
            candidates = {
                str(native_graph_path(root_path)),
                str(compatibility_graph_path(root_path)),
            }
            for key in candidates:
                _GRAPH_DATA_CACHE.pop(key, None)
    _clear_derived_query_caches()

def _fingerprint_stale_summary(data: dict[str, Any], root_path: Path) -> dict[str, Any]:
    """Compare graph fingerprints to live files without rebuilding the graph."""
    stored = data.get("fingerprints") if isinstance(data.get("fingerprints"), dict) else {}
    if not stored:
        return {"stale": False, "changed": 0, "removed": 0, "new": 0, "sample": []}

    changed: list[str] = []
    removed: list[str] = []
    for rel, fingerprint in stored.items():
        normalized = _norm_path(rel)
        if not normalized or _is_structural_graph_artifact(normalized):
            continue
        try:
            st = (root_path / normalized).stat()
        except FileNotFoundError:
            removed.append(normalized)
            continue
        except Exception:
            continue
        current = f"{int(st.st_mtime_ns)}:{int(st.st_size)}"
        if current != str(fingerprint):
            changed.append(normalized)

    new_files: list[str] = []
    try:
        from . import code_graph as private_map

        current_files = {
            _norm_path(path)
            for path in private_map._discover_files(root_path)
            if path and not _is_structural_graph_artifact(path)
        }
        known_files = {_norm_path(path) for path in stored}
        new_files = sorted(path for path in current_files - known_files if path)
    except Exception:
        new_files = []

    sample = sorted(set(changed + removed + new_files))[:8]
    return {
        "stale": bool(changed or removed or new_files),
        "changed": len(changed),
        "removed": len(removed),
        "new": len(new_files),
        "sample": sample,
    }


def load_graph_data(root: str | Path | None = None) -> dict[str, Any] | None:
    """Load and validate the canonical structural graph node-link JSON.

    Memoized by the graph file's (mtime, size). select_context and graph_status
    each call this within a single turn, re-reading + JSON-parsing the same
    multi-MB file every time. The cache self-invalidates the instant the graph is
    rebuilt (new mtime/size), so a stale graph is never served. All callers treat
    the returned dict as read-only.
    """
    if not structural_graph_enabled():
        return None
    path = graph_path(root)
    query_loadable, _load_error, graph_bytes = _graph_load_admission(path)
    if not query_loadable:
        return None
    _arm_graph_cache_release()
    st = path.stat()
    key = str(path)
    fingerprint = f"{st.st_mtime_ns}:{graph_bytes}"
    with _GRAPH_DATA_LOCK:
        cached = _GRAPH_DATA_CACHE.get(key)
        has_cached_graph = bool(_GRAPH_DATA_CACHE)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]
    if cached is not None or has_cached_graph:
        _clear_derived_query_caches()
    try:
        data = _load_graph_json(path)
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("nodes"), list):
        return None
    if not isinstance(_edge_list(data), list):
        return None
    migrated = _migrate_graph(data)
    if migrated is not None and _unique_graph_node_ids(migrated):
        with _GRAPH_DATA_LOCK:
            # One active parsed graph per process. External MCP servers bind to
            # one root, and MO switches roots sequentially; retaining a full
            # expanded graph for every visited project is unbounded.
            _GRAPH_DATA_CACHE.clear()
            _GRAPH_DATA_CACHE[key] = (fingerprint, migrated)
        return migrated
    return None


def _load_graph_json(path: Path) -> dict[str, Any] | None:
    """Load the canonical graph JSON."""
    return json.loads(path.read_text(encoding="utf-8"))


def _graph_load_admission(path: Path) -> tuple[bool, str, int]:
    """Return the cheap query admission verdict without parsing the graph."""
    try:
        if not path.is_file():
            return False, "graph_missing", 0
        graph_bytes = int(path.stat().st_size)
    except OSError:
        return False, "graph_unreadable", 0
    return True, "", graph_bytes


def _graph_for_storage(graph: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical compact persisted form without mutating callers."""
    stored = dict(graph)
    nodes = _node_map(graph)
    defaults = dict(NATIVE_EDGE_DEFAULTS)
    defaults.update(
        {
            str(key): value
            for key, value in (graph.get("edge_defaults") or {}).items()
            if isinstance(key, str)
        }
        if isinstance(graph.get("edge_defaults"), dict)
        else {}
    )
    stored["edge_defaults"] = defaults
    edge_key = "links" if isinstance(graph.get("links"), list) else "edges"
    compact_edges: list[dict[str, Any]] = []
    for edge in _edge_list(graph):
        if not isinstance(edge, dict):
            continue
        compact = dict(edge)
        for key, value in defaults.items():
            if compact.get(key) == value:
                compact.pop(key, None)
        source_node = nodes.get(str(compact.get("source") or ""))
        if source_node:
            # Source metadata belongs to the identified node. Keep only actual
            # edge overrides; readers resolve omitted fields from that node.
            for key in ("source_file", "source_location"):
                if compact.get(key) == source_node.get(key):
                    compact.pop(key, None)
        compact_edges.append(compact)
    stored[edge_key] = compact_edges
    return stored


def _write_native_graph(path: Path, graph: dict[str, Any]) -> int:
    """Serialize compact JSON before atomically replacing the prior graph."""
    payload = json.dumps(
        _graph_for_storage(graph),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    encoded_bytes = len(payload.encode("utf-8"))
    atomic_write_text(path, payload, encoding="utf-8")
    return encoded_bytes


def _status_manifest_path(graph_file: Path) -> Path:
    return graph_file.parent / STRUCTURAL_STATUS_FILE


def _graph_file_fingerprint(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return ""
    return f"{int(stat.st_mtime_ns)}:{int(stat.st_size)}"


def _write_status_manifest(path: Path, graph: dict[str, Any]) -> None:
    """Persist compact derived status metadata beside the canonical graph."""
    communities = {
        node.get("community")
        for node in graph.get("nodes", [])
        if isinstance(node, dict) and node.get("community") is not None
    }
    stored_audit = graph.get("edge_audit") if isinstance(graph.get("edge_audit"), dict) else {}
    audit = stored_audit if _valid_edge_audit(stored_audit) else _edge_audit(graph)
    query_loadable, load_error, graph_bytes = _graph_load_admission(path)
    atomic_write_json(
        _status_manifest_path(path),
        {
            "schema": "mo-structural-status-v2",
            "graph_fingerprint": _graph_file_fingerprint(path),
            "graph_bytes": graph_bytes,
            "query_loadable": query_loadable,
            "load_error": load_error,
            "version": str(graph.get("version") or ""),
            "builder_version": str(graph.get("builder_version") or ""),
            "built_at": str(graph.get("built_at") or ""),
            "built_at_commit": str(graph.get("built_at_commit") or ""),
            "group_strategy": str(graph.get("group_strategy") or "path"),
            "nodes": len(graph.get("nodes", [])),
            "edges": len(_edge_list(graph)),
            "communities": len(communities),
            "fingerprints": dict(graph.get("fingerprints") or {}),
            "quality": dict(graph.get("quality") or {}),
            "edge_audit": audit,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _load_status_manifest(path: Path) -> dict[str, Any] | None:
    manifest_path = _status_manifest_path(path)
    try:
        if not manifest_path.is_file() or manifest_path.stat().st_size > 2 * 1024 * 1024:
            return None
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("schema") not in {
        "mo-structural-status-v1",
        "mo-structural-status-v2",
    }:
        return None
    if str(data.get("graph_fingerprint") or "") != _graph_file_fingerprint(path):
        return None
    if not isinstance(data.get("fingerprints"), dict):
        return None
    return data



def _migrate_graph(data: dict[str, Any]) -> dict[str, Any] | None:
    version = str(data.get("version") or "")
    if version and version not in COMPATIBLE_GRAPH_VERSIONS:
        return None
    if version != GRAPH_VERSION:
        data = dict(data)
        for node in data.get("nodes", []):
            if isinstance(node, dict) and "file_type" not in node and "type" in node:
                node["file_type"] = node.get("type")
        data["version"] = GRAPH_VERSION
        # Explicit older versions remain readable for diagnostics, but query
        # surfaces must not call them fresh after builder semantics change.
        if version:
            data["_requires_rebuild"] = True
    return data


def _unique_graph_node_ids(data: dict[str, Any]) -> bool:
    seen: set[str] = set()
    for node in data.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        if not node_id or node_id in seen:
            return False
        seen.add(node_id)
    return True


def load_analysis(root: str | Path | None = None) -> dict[str, Any]:
    try:
        path = analysis_path(root)
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        traceback.print_exc()
    return {}


def load_labels(root: str | Path | None = None) -> dict[int, str]:
    try:
        path = labels_path(root)
        if not path.is_file():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        return {int(k): str(v) for k, v in raw.items() if str(k).lstrip("-").isdigit()}
    except Exception:
        return {}


def graph_status(root: str | Path | None = None) -> dict[str, Any]:
    root_path = project_root(root)
    active_path = graph_path(root_path)
    query_loadable, load_error, graph_bytes = _graph_load_admission(active_path)
    manifest = _load_status_manifest(active_path)
    data = None if manifest is not None or not query_loadable else load_graph_data(root_path)
    metadata = manifest or data
    if not metadata:
        return {
            "available": False,
            "query_loadable": False,
            "refreshing": _graph_refresh_in_progress(root_path),
            "load_error": load_error,
            "graph_bytes": graph_bytes,
            "path": str(active_path),
            "native_path": str(native_graph_path(root_path)),
            "compatibility_path": str(compatibility_graph_path(root_path)),
            "source_kind": "none",
            "indexed_files": 0,
            "indexable_files": 0,
            "nodes": 0,
            "edges": 0,
            "confidence_breakdown": {label: 0 for label in sorted(CONFIDENCE_LABELS)},
            "provenance_breakdown": {},
            "legacy_confidence_edges": 0,
            "unknown_confidence_edges": 0,
            "stale_reasons": [],
            "stale": False,
            "fingerprint_stale": False,
            "stale_files": 0,
            "stale_sample": [],
            "trust": "unavailable",
        }
    built = str(metadata.get("built_at_commit") or "")
    head = _git_head(root_path)
    commit_stale = bool(built and head and built != head)
    fingerprint = _fingerprint_stale_summary(metadata, root_path)
    stale_reasons: list[str] = []
    if commit_stale:
        stale_reasons.append("git_head_changed")
    if fingerprint.get("stale"):
        stale_reasons.append("file_fingerprint_changed")
    version = str(metadata.get("version") or "")
    if (version and version != GRAPH_VERSION) or metadata.get("_requires_rebuild"):
        stale_reasons.append("graph_schema_changed")
    if not query_loadable:
        stale_reasons.append(load_error or "graph_unavailable")
    try:
        from . import code_graph as private_map

        builder_version = str(metadata.get("builder_version") or "")
        if builder_version and builder_version != private_map.GRAPH_VERSION:
            stale_reasons.append("builder_schema_changed")
    except Exception:
        pass
    stale = bool(stale_reasons)
    if manifest is not None:
        node_count = int(manifest.get("nodes") or 0)
        edge_count = int(manifest.get("edges") or 0)
        community_count = int(manifest.get("communities") or 0)
    else:
        node_count = len(data.get("nodes", []))
        edge_count = len(_edge_list(data))
        community_count = len({
            node.get("community") for node in data.get("nodes", [])
            if isinstance(node, dict) and node.get("community") is not None
        })
    stored_audit = metadata.get("edge_audit") if isinstance(metadata.get("edge_audit"), dict) else {}
    audit = stored_audit if _valid_edge_audit(stored_audit) else _edge_audit(data or {})
    raw_quality = metadata.get("quality") if isinstance(metadata.get("quality"), dict) else {}
    quality = {key: value for key, value in raw_quality.items() if not str(key).endswith("_samples")}
    stored_fingerprints = (
        metadata.get("fingerprints")
        if isinstance(metadata.get("fingerprints"), dict)
        else {}
    )
    indexed_files = len(stored_fingerprints)
    indexable_files = max(
        0,
        indexed_files
        - int(fingerprint.get("removed") or 0)
        + int(fingerprint.get("new") or 0),
    )
    return {
        "available": query_loadable,
        "query_loadable": query_loadable,
        "load_error": load_error,
        "graph_bytes": graph_bytes,
        "path": str(active_path),
        "native_path": str(native_graph_path(root_path)),
        "compatibility_path": str(compatibility_graph_path(root_path)),
        "source_kind": "native" if active_path == native_graph_path(root_path) else "compatibility",
        "indexed_files": indexed_files,
        "indexable_files": indexable_files,
        "nodes": node_count,
        "edges": edge_count,
        "communities": community_count,
        "groups": community_count,
        "group_strategy": str(metadata.get("group_strategy") or "path"),
        "version": version or GRAPH_VERSION,
        "built_at": str(metadata.get("built_at") or ""),
        "built_at_commit": built,
        "git_head": head,
        "stale": stale,
        "stale_reasons": stale_reasons,
        "refreshing": _graph_refresh_in_progress(root_path),
        "fingerprint_stale": bool(fingerprint.get("stale")),
        "stale_files": int(fingerprint.get("changed") or 0) + int(fingerprint.get("removed") or 0) + int(fingerprint.get("new") or 0),
        "stale_sample": list(fingerprint.get("sample") or []),
        "mtime": active_path.stat().st_mtime if active_path.exists() else 0.0,
        "confidence_breakdown": audit["confidence"],
        "provenance_breakdown": audit["provenance"],
        "legacy_confidence_edges": audit["legacy_confidence_edges"],
        "unknown_confidence_edges": audit["unknown_confidence_edges"],
        "quality": quality,
        "trust": "unavailable" if not query_loadable else ("stale" if stale else "fresh_orientation"),
    }


def export_sanitized_graph(
    root: str | Path | None = None,
    *,
    output: str | Path | None = None,
    compatibility: bool = False,
) -> dict[str, Any]:
    """Write an explicit shareable graph artifact with private paths stripped."""
    root_path = project_root(root)
    data = load_or_build_graph_data(root_path, refresh_if_stale=False)
    if not data:
        return {"exported": False, "reason": "graph_unavailable"}
    status = graph_status(root_path)
    if status.get("stale"):
        return {
            "exported": False,
            "reason": "graph_stale",
            "stale_reasons": list(status.get("stale_reasons") or []),
        }

    nodes: list[dict[str, Any]] = []
    included_ids: set[str] = set()
    for node in data.get("nodes", []):
        if not isinstance(node, dict):
            continue
        source = _export_safe_path(node.get("source_file"))
        if source is None:
            continue
        node_id = _clean(node.get("id"), 260)
        if not node_id:
            continue
        item = {
            "id": node_id,
            "label": _clean(node.get("label") or node.get("name") or node_id, 140),
            "type": _clean(node.get("file_type") or node.get("type") or "node", 40),
            "source_file": source,
            "community": _as_int(node.get("community")),
        }
        location = _clean(node.get("source_location"), 40)
        if location:
            item["source_location"] = location
        summary = _export_text(node.get("summary"), 260)
        if summary:
            item["summary"] = summary
        nodes.append(item)
        included_ids.add(node_id)

    source_locations = {node["id"]: node.get("source_location", "") for node in nodes}
    edge_defaults = data.get("edge_defaults")
    edge_defaults = edge_defaults if isinstance(edge_defaults, dict) else {}
    links: list[dict[str, Any]] = []
    for edge in _edge_list(data):
        if not isinstance(edge, dict):
            continue
        source = _clean(edge.get("source"), 260)
        target = _clean(edge.get("target") or edge.get("target_id"), 260)
        if source not in included_ids or target not in included_ids:
            continue
        relation = _clean(edge.get("relation") or edge.get("type") or "related", 60)
        item = {
            "source": source,
            "target": target,
            "relation": relation,
            "confidence": _edge_confidence(edge),
            "provenance": _clean(
                edge.get("provenance")
                or edge_defaults.get("provenance")
                or "mo-native-structural-graph",
                80,
            ),
        }
        location = _clean(edge.get("source_location", source_locations.get(source, "")), 40)
        if location:
            item["source_location"] = location
        links.append(item)

    payload: dict[str, Any] = {
        "schema": "mo-sanitized-graph-v1",
        "generated_by": "mo",
        "orientation_only": True,
        "project": {"name": root_path.name or "project"},
        "source_graph_version": _clean(data.get("version") or GRAPH_VERSION, 40),
        "counts": {"nodes": len(nodes), "links": len(links)},
        "nodes": nodes,
        "links": links,
    }
    if compatibility:
        payload["compatibility"] = {
            "node_link": True,
            "nodes_key": "nodes",
            "links_key": "links",
            "source": "explicit_export",
        }

    out_path = Path(output) if output else root_path / "tmp" / "mo_graph_export.json"
    if not out_path.is_absolute():
        out_path = root_path / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(out_path, payload, indent=2, ensure_ascii=False)
    return {
        "exported": True,
        "path": str(out_path),
        "nodes": len(nodes),
        "links": len(links),
        "compatibility": bool(compatibility),
    }


def load_or_build_graph_data(
    root: str | Path | None = None,
    *,
    build_if_missing: bool = True,
    refresh_if_stale: bool = True,
) -> dict[str, Any] | None:
    """Load the active graph, optionally building or queuing a safe refresh.

    Read paths pass both flags false and never start repository work. A
    build-capable caller may queue the isolated refresh helper for stale data;
    the caller still receives the current snapshot and must honor its stale flag.
    """
    root_path = project_root(root)
    data = load_graph_data(root_path)
    if data and refresh_if_stale:
        try:
            status = graph_status(root_path)
            if status.get("stale"):
                if build_if_missing:
                    maybe_update_graph_async(root=root_path, reason="build-capable-stale")
        except Exception:
            traceback.print_exc()
        return data
    if data:
        return data
    if not build_if_missing or not structural_graph_enabled() or not auto_build_enabled():
        return data
    result = build_structural_graph_isolated(root_path)
    if not result.get("built"):
        _emit("skipped", root=root_path, reason=str(result.get("reason") or "build_failed"))
        return None
    return load_graph_data(root_path)


def build_focused_map(
    root: str | Path | None = None,
    *,
    task_board: Any | None = None,
    query: str = "",
    output: str | Path | None = None,
) -> dict[str, Any]:
    """Write a compact task-aware HTML orientation map.

    This complements ``code_map.html`` without replacing it. The artifact is
    intentionally evidence/orientation only: source truth still comes from file
    reads, trace logs, and tests.
    """
    root_path = project_root(root)
    data = load_or_build_graph_data(root_path, refresh_if_stale=False)
    status = graph_status(root_path)
    # Private-by-default: complement graph.json in its resolved dir (~/.mo/cache
    # when private state is on), never unconditionally in the checkout's memory/.
    focused_map_path = native_graph_path(root_path).parent / "focused_map.html"
    if not data:
        return {"built": False, "reason": "graph_unavailable", "path": str(output or focused_map_path)}
    if status.get("stale"):
        return {
            "built": False,
            "reason": "graph_stale",
            "stale_reasons": list(status.get("stale_reasons") or []),
            "path": str(output or focused_map_path),
        }

    board_summary = _focused_board_summary(task_board)
    files = _focused_files(data, board_summary, query=query)
    out_path = Path(output) if output else focused_map_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(out_path, _render_focused_map(status, board_summary, files, query=query), encoding="utf-8")
    return {
        "built": True,
        "path": str(out_path),
        "nodes": int(status.get("nodes") or 0),
        "edges": int(status.get("edges") or 0),
        "tasks": len(board_summary.get("tasks") or []),
        "files": len(files),
        "bytes": out_path.stat().st_size,
    }


def build_structural_graph(
    root: str | Path | None = None,
    *,
    max_files: int | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build or incrementally refresh MO's native community code map."""
    if not structural_graph_enabled():
        return {"built": False, "reason": "disabled"}
    root_path = project_root(root)
    lock, owner = _acquire_graph_build_lock(root_path, config=config)
    path = native_graph_path(root_path, config=config)
    if lock is None:
        reason = (
            f"structural graph build already running (pid {owner})"
            if owner else "structural graph build lock unavailable"
        )
        return {
            "built": False,
            "status": "busy",
            "reason": reason,
            "path": str(path),
        }
    try:
        from . import code_graph as private_map

        _prune_stale_graph_caches(
            config=config,
            active_root=root_path,
            builder_version=private_map.GRAPH_VERSION,
        )

        files = [path for path in private_map._discover_files(root_path) if not _is_structural_graph_artifact(path)]
        if not files:
            return {"built": False, "reason": "no indexable files", "path": str(native_graph_path(root_path, config=config))}
        configured_limit = (
            max_files
            if max_files is not None
            else getattr(
                private_map,
                "_max_files",
                lambda: getattr(private_map, "DEFAULT_MAX_FILES", 0),
            )()
        )
        limit = max(0, int(configured_limit or 0))
        if limit and len(files) > limit:
            return {"built": False, "reason": f"project has {len(files)} indexable files; limit is {limit}", "path": str(native_graph_path(root_path, config=config)), "files": len(files)}
        fingerprints = private_map._fingerprints(root_path, files)
        existing_graph = _load_graph_json(path) if path.is_file() else None
        private_graph = (
            _private_map_from_structural_graph(existing_graph)
            if isinstance(existing_graph, dict)
            and existing_graph.get("version") == GRAPH_VERSION
            and existing_graph.get("builder_version") == private_map.GRAPH_VERSION
            else None
        )
        stale = private_map._stale_files(private_graph, fingerprints)
        code_map_enabled = os.environ.get("MO_CODE_MAP_AUTOGEN", "1").strip().lower() not in {
            "0", "false", "off", "no", "disabled",
        }
        required_artifacts = [
            path.parent / ".structural_labels.json",
            path.parent / ".structural_analysis.json",
            _status_manifest_path(path),
        ]
        if code_map_enabled:
            required_artifacts.append(path.parent / "code_map.html")
        graph_content_current = (
            private_graph is not None
            and not stale
            and _graph_load_admission(path)[0]
            and isinstance(existing_graph, dict)
            and existing_graph.get("version") == GRAPH_VERSION
            and not existing_graph.get("_requires_rebuild")
            and existing_graph.get("builder_version") == private_map.GRAPH_VERSION
            and existing_graph.get("fingerprints") == fingerprints
            and _valid_edge_audit(existing_graph.get("edge_audit") or {})
            and all(artifact.is_file() for artifact in required_artifacts)
        )
        if graph_content_current:
            current_head = _git_head(root_path)
            if current_head and existing_graph.get("built_at_commit") != current_head:
                existing_graph = dict(existing_graph)
                existing_graph["built_at_commit"] = current_head
                graph_bytes = _write_native_graph(path, existing_graph)
                _write_status_manifest(path, existing_graph)
                invalidate_graph_runtime_caches(root_path)
                _emit("metadata_updated", root=root_path, file_count=len(files))
                return {
                    "built": True,
                    "status": "metadata_updated",
                    "path": str(path),
                    "nodes": len(existing_graph.get("nodes", [])),
                    "edges": len(_edge_list(existing_graph)),
                    "files": len(files),
                    "graph_bytes": graph_bytes,
                    "query_loadable": True,
                    "refreshed_files": 0,
                    "stale_files": 0,
                }
            _emit("up_to_date", root=root_path, file_count=len(files))
            return {
                "built": True,
                "status": "up_to_date",
                "path": str(path),
                "nodes": len(existing_graph.get("nodes", [])),
                "edges": len(_edge_list(existing_graph)),
                "files": len(files),
                "graph_bytes": path.stat().st_size,
                "query_loadable": True,
                "refreshed_files": 0,
                "stale_files": 0,
            }
        delta_limit = int_env("MO_STRUCTURAL_GRAPH_DELTA_LIMIT", SMALL_STALE_UPDATE_LIMIT)
        status = "built"
        if private_graph and private_graph.get("version") == private_map.GRAPH_VERSION and stale and len(stale) <= delta_limit:
            private_graph = private_map._refresh_graph_delta(root_path, private_graph, files, fingerprints, stale)
            status = "incremental"
        elif not private_graph or private_graph.get("version") != private_map.GRAPH_VERSION or stale:
            private_graph = private_map._build_graph(root_path, files, fingerprints)
        refreshed = set(stale)
        for _ in range(2):
            live_files = [
                item for item in private_map._discover_files(root_path)
                if not _is_structural_graph_artifact(item)
            ]
            live_fingerprints = private_map._fingerprints(root_path, live_files)
            unsettled = private_map._stale_files(private_graph, live_fingerprints)
            if not unsettled:
                break
            if not live_files:
                return {"built": False, "reason": "no indexable files", "path": str(path)}
            if limit and len(live_files) > limit:
                return {"built": False, "reason": f"project changed to {len(live_files)} indexable files; limit is {limit}", "path": str(path), "files": len(live_files)}
            refreshed.update(unsettled)
            private_graph = (
                private_map._refresh_graph_delta(root_path, private_graph, live_files, live_fingerprints, unsettled)
                if len(unsettled) <= delta_limit
                else private_map._build_graph(root_path, live_files, live_fingerprints)
            )
            files, fingerprints = live_files, live_fingerprints
        fingerprints = dict(private_graph.get("fingerprints") or fingerprints)
        graph = _structural_graph_from_private_map(private_graph, root_path, fingerprints)
        path.parent.mkdir(parents=True, exist_ok=True)
        graph_bytes = _write_native_graph(path, graph)
        _write_native_sidecars(path, graph)
        _maybe_generate_code_map(path)
        invalidate_graph_runtime_caches(root_path)
        _emit(status, root=root_path, file_count=len(files))
        return {
            "built": True,
            "status": status,
            "path": str(path),
            "nodes": len(graph.get("nodes", [])),
            "edges": len(_edge_list(graph)),
            "files": len(files),
            "graph_bytes": graph_bytes,
            "query_loadable": True,
            "refreshed_files": len(refreshed),
            "stale_files": graph_status(root_path)["stale_files"],
        }
    except Exception as exc:
        return {"built": False, "reason": f"{type(exc).__name__}: {exc}", "path": str(native_graph_path(root_path, config=config))}
    finally:
        _release_graph_build_lock(lock)


def _native_refresh_worker_command(root_path: Path, *, max_files: int | None = None, maintain_indexes: bool = False) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "core.graph.structural_graph",
        "--refresh-worker",
        str(root_path),
    ]
    if max_files is not None:
        command.extend(["--max-files", str(max(0, int(max_files)))])
    if maintain_indexes:
        command.append("--maintain-indexes")
    return command


def seed_structural_graph(source_root: Path, root: Path) -> bool:
    """Reuse native graph nodes in a disposable worktree, retaining stale files.

    Only byte-identical files still matching the original graph receive the
    copy's fingerprint. All others stay stale for the existing incremental
    builder. A successful seed is not a freshness or evidence verdict.
    """
    from . import code_graph

    source_root, root = source_root.resolve(), root.resolve()
    if source_root == root or not structural_graph_enabled():
        return False
    lock, _owner = _acquire_graph_build_lock(root)
    if lock is None:
        return False
    try:
        source_path = native_graph_path(source_root)
        if not _graph_load_admission(source_path)[0]:
            return False
        graph = _load_graph_json(source_path)
        if not graph or graph.get("_requires_rebuild") or graph.get("version") != GRAPH_VERSION or graph.get("builder_version") != code_graph.GRAPH_VERSION:
            return False
        files = [path for path in code_graph._discover_files(root) if not _is_structural_graph_artifact(path)]
        original = graph.get("fingerprints") or {}
        copied = code_graph._fingerprints(root, files)
        shared_files = [path for path in files if path in original]
        source_before = code_graph._fingerprints(source_root, shared_files)
        fingerprints = dict.fromkeys(original, "stale")
        for relative, fingerprint in original.items():
            if relative in copied and source_before.get(relative) == fingerprint:
                try:
                    if (root / relative).read_bytes() == (source_root / relative).read_bytes():
                        fingerprints[relative] = copied[relative]
                except OSError:
                    pass  # A concurrent source edit remains stale, never admitted.
        source_after = code_graph._fingerprints(source_root, shared_files)
        for relative in original:
            if source_after.get(relative) != source_before.get(relative):
                fingerprints[relative] = "stale"
        copied_files = [path for path in code_graph._discover_files(root) if not _is_structural_graph_artifact(path)]
        if code_graph._fingerprints(root, copied_files) != copied:
            return False
        graph = dict(graph, fingerprints=fingerprints, project={"root": str(root), "name": root.name}, built_at_commit=_git_head(root))
        path = native_graph_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_native_graph(path, graph)
        if fingerprints == copied:
            _write_native_sidecars(path, graph)
        else:
            _write_status_manifest(path, graph)
        invalidate_graph_runtime_caches(root)
        return True
    except (OSError, ValueError):
        return False
    finally:
        _release_graph_build_lock(lock)


def build_structural_graph_isolated(
    root: str | Path | None = None,
    *,
    max_files: int | None = None,
    timeout: int = DEFAULT_GRAPH_REFRESH_TIMEOUT_SECONDS,
    wait_for_existing: bool = False,
) -> dict[str, Any]:
    """Build in a bounded helper so long-lived agents do not retain builder heaps."""
    if not structural_graph_enabled():
        return {"built": False, "reason": "disabled"}
    root_path = project_root(root)
    deadline = time.monotonic() + max(1, int(timeout))
    refresh_lock = _acquire_graph_refresh_lock(root_path)
    waited = refresh_lock is None and wait_for_existing
    while refresh_lock is None and wait_for_existing and time.monotonic() < deadline:
        time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        refresh_lock = _acquire_graph_refresh_lock(root_path)
    if refresh_lock is None:
        return {
            "built": False,
            "status": "busy",
            "reason": "structural graph refresh already running",
            "path": str(native_graph_path(root_path)),
        }
    command = _native_refresh_worker_command(root_path, max_files=max_files)
    popen_kwargs: dict[str, Any] = {
        "cwd": str(Path(__file__).resolve().parents[2]),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    apply_windows_hidden_process_flags(
        popen_kwargs,
        new_process_group=True,
        below_normal_priority=True,
    )
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True
    try:
        if waited:
            status = graph_status(root_path)
            if status.get("available") and not status.get("stale"):
                return {"built": False, "reused": True, "path": str(native_graph_path(root_path))}
        proc = subprocess.Popen(command, **popen_kwargs)
        try:
            stdout, stderr = proc.communicate(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return {
                "built": False,
                "reason": f"graph refresh exceeded {int(timeout)}s and was stopped",
                "path": str(native_graph_path(root_path)),
            }
        result = _refresh_worker_result(stdout)
        if not result:
            return {
                "built": False,
                "reason": str(stderr or stdout or f"refresh worker exited {proc.returncode}")[:500],
                "path": str(native_graph_path(root_path)),
            }
        if result.get("built"):
            invalidate_graph_runtime_caches(root_path)
        return result
    except Exception as exc:
        return {
            "built": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "path": str(native_graph_path(root_path)),
        }
    finally:
        _release_graph_refresh_lock(refresh_lock)


def select_context(
    question: str,
    *,
    cwd: str | Path | None = None,
    max_chars: int = DEFAULT_CONTEXT_CHARS,
    max_nodes: int = DEFAULT_MAX_NODES,
    depth: int = 2,
    build_if_missing: bool = False,
    allow_stale: bool = False,
    profile: Any | None = None,
) -> str:
    """Return a compact structural-graph orientation slice, or ``""``.

    The text intentionally uses the same public header as MO's human code map so
    provider and tests keep treating it as orientation-only support context.
    """
    root = project_root(cwd)
    data = load_or_build_graph_data(root) if build_if_missing else load_graph_data(root)
    if not data:
        _emit("skipped", root=root, reason="structural_graph_missing")
        return ""
    status = graph_status(root)
    if status.get("stale") and not allow_stale:
        # Verification consumers fail closed; explicitly stale-aware navigation
        # may reuse the labeled snapshot while maintenance refreshes it.
        _emit("skipped", root=root, reason="structural_graph_stale")
        return ""
    terms = _terms(question)
    if not terms:
        _emit("skipped", root=root, reason="no_terms")
        return ""

    nodes = _node_map(data)
    scored = _rank_context_nodes(
        data,
        question,
        terms,
        profile=profile,
        # Role seeding needs a wider internal candidate pool than the final
        # provider slice. On large graphs an explicitly requested README/test
        # owner can rank just below many connected product symbols; dropping it
        # here means the later eight-node/character bounds never get a chance to
        # preserve that requested scope.
        limit=max(64, max_nodes * 8),
    )
    if not scored:
        _emit("skipped", root=root, reason="no_relevant_nodes", file_count=len(nodes))
        return ""

    requested_roles: list[str] = ["product owner"]
    term_set = set(terms)
    if {"test", "tests", "testing", "pytest", "verify"} & term_set:
        requested_roles.append("verification")
    if {"doc", "docs", "documentation", "readme"} & term_set:
        requested_roles.append("documentation")
    # Keep several independently ranked owners before topology expansion. With
    # the default eight-node slice, three role seeds left every remaining slot
    # to the first seed's densely connected neighbours and could erase the next
    # explicit owner (for example the public tool executor).
    seeds = _context_seed_ids(
        nodes,
        scored,
        count=min(max(1, int(max_nodes)), 4),
        requested_roles=requested_roles,
    )
    selected_ids, selected_edges = _select_subgraph(data, seeds, scored, max_nodes=max_nodes, depth=depth)
    if not selected_ids:
        _emit("skipped", root=root, reason="empty_selection", file_count=len(nodes))
        return ""

    labels = load_labels(root)
    text = _format_context(
        data, selected_ids, selected_edges, root=root, labels=labels,
        status=status, max_chars=max_chars, seed_ids=seeds,
    )
    selected_nodes = [nodes[nid] for nid in selected_ids if nid in nodes]
    _emit("structural_graph", root=root, file_count=len(nodes), selected=selected_nodes)
    return text


def build_structural_summary(
    query: str = "",
    *,
    cwd: str | Path | None = None,
    max_chars: int = 1200,
) -> str:
    """Return handoff/PRT-friendly structural facts from structural graph outputs."""
    root = project_root(cwd)
    data = load_or_build_graph_data(root, build_if_missing=False, refresh_if_stale=False)
    if not data:
        return ""
    status = graph_status(root)
    if status.get("stale"):
        return ""
    analysis = load_analysis(root)
    labels = load_labels(root)
    terms = _terms(query)
    touched = [
        node_id
        for _score, node_id in _rank_context_nodes(data, query, terms, limit=32)[:8]
    ] if terms else []
    node_map = _node_map(data)
    communities = _communities(data)

    community_counts: dict[int, int] = defaultdict(int)
    for nid in touched:
        cid = _as_int(node_map.get(nid, {}).get("community"))
        if cid is not None:
            community_counts[cid] += 1

    lines = [
        "### MO Structural Graph Context - orientation only",
        "Use as a navigation/risk hint only; verify with file reads/tests before claims.",
    ]
    if community_counts:
        best = sorted(community_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
        lines.append(f"- Current likely community: {_community_name(best, labels)} ({len(communities.get(best, []))} node(s)).")
    gods = _analysis_list(analysis, "gods")[:5]
    if gods:
        lines.append("- God nodes: " + "; ".join(
            f"{_clean(item.get('label') or item.get('id'), 60)} degree={int(item.get('degree') or 0)}"
            for item in gods[:3] if isinstance(item, dict)
        ))
    surprises = _analysis_list(analysis, "surprises")[:3]
    if surprises:
        compact = []
        for item in surprises:
            if not isinstance(item, dict):
                continue
            compact.append(f"{_clean(item.get('source'), 50)} -> {_clean(item.get('target'), 50)} [{_clean(item.get('confidence'), 20)}]")
        if compact:
            lines.append("- Surprising connections: " + "; ".join(compact[:3]))
    cycles = find_import_cycles(root=root, changed_files=None, top_n=3)
    if cycles:
        lines.append("- Import cycles detected: " + "; ".join(" -> ".join(c["cycle"][:5]) for c in cycles[:2]))
    text = "\n".join(line for line in lines if line.strip())
    return redact_monitor_text(text, max_chars)


def analyze_diff_impact_details(diff_text: str, root: str | Path | None = None) -> dict[str, Any]:
    """Separate deterministic import/resolved-call impact from weaker inferred impact."""
    root_path = project_root(root)
    data = load_or_build_graph_data(root_path, build_if_missing=False, refresh_if_stale=False)
    if not data or graph_status(root_path).get("stale"):
        return {"confirmed": [], "inferred": []}
    return _analyze_diff_impact_data(diff_text, data)


def _analyze_diff_impact_data(diff_text: str, data: dict[str, Any]) -> dict[str, Any]:
    """Analyze one diff against an already freshness-checked graph snapshot."""
    changes = _diff_file_changes(diff_text)
    changed = {path for item in changes for path in (item['old_path'], item['path']) if path}
    if not changed:
        return {"confirmed": [], "inferred": []}
    nodes_by_file = _nodes_by_source_file(data)
    file_by_node = _source_file_by_node(data)
    changed_nodes = {nid for file in changed for nid in nodes_by_file.get(file, set())}
    node_map = _node_map(data)
    symbol_ids: set[str] = set()
    coverage = []
    for item in changes:
        path = item['path'] or item['old_path']
        candidates = []
        for nid in nodes_by_file.get(path, ()):
            node = node_map[nid]
            if node.get('type') not in {'function', 'class', 'interface'}:
                continue
            match = re.fullmatch(r'L(\d+)-L(\d+)', str(node.get('source_location') or ''))
            if match:
                candidates.append((int(match[1]), int(match[2]), nid))
        matched_lines: set[int] = set()
        if not item['malformed']:
            for line in item['added_lines']:
                owners = [(end - start, nid) for start, end, nid in candidates if start <= line <= end]
                if owners:
                    # A method/body edit belongs to its innermost symbol, not
                    # every other method through their enclosing class span.
                    smallest = min(size for size, _ in owners)
                    symbol_ids.update(nid for size, nid in owners if size == smallest)
                    matched_lines.add(line)
        reasons = []
        if item['malformed']:
            reasons.append('incomplete_hunk')
        if item['removed_lines']:
            reasons.append('removed_lines_require_preimage')
        if item['old_path'] and item['path'] and item['old_path'] != item['path']:
            reasons.append('renamed_source')
        if item['added_lines'] - matched_lines:
            reasons.append('module_or_unmapped_lines')
        if not item['added_lines']:
            reasons.append('no_added_lines')
        coverage.append({'source_file': path, 'status': 'partial' if reasons else 'post_image', 'reasons': reasons})
    confirmed: set[str] = set()
    inferred: set[str] = set()
    symbol_confirmed: set[str] = set()
    symbol_inferred: set[str] = set()
    edges = [edge for edge in _edge_list(data) if isinstance(edge, dict)]
    resolution_aware = any(str(edge.get("resolution") or "") for edge in edges)
    for edge in edges:
        relation = str(edge.get("relation") or edge.get("type") or "")
        if relation and relation not in _DEPENDENCY_RELATIONS:
            continue
        src = str(edge.get("source") or "")
        tgt = str(edge.get("target") or "")
        src_file = file_by_node.get(src) or _norm_path(edge.get("source_file"))
        tgt_file = file_by_node.get(tgt)
        if (tgt in changed_nodes or (tgt_file and tgt_file in changed)) and src_file and src_file not in changed:
            resolution = str(edge.get("resolution") or "")
            is_confirmed = (
                relation in _IMPORT_RELATIONS
                or (relation in {"calls", "inherits"} and resolution in {"imported_symbol", "imported_attribute", "module_attribute"})
                or not resolution_aware
            )
            (confirmed if is_confirmed else inferred).add(src_file)
            if tgt in symbol_ids:
                (symbol_confirmed if is_confirmed else symbol_inferred).add(src_file)
    inferred.difference_update(confirmed)
    symbol_inferred.difference_update(symbol_confirmed)
    return {
        'confirmed': sorted(confirmed), 'inferred': sorted(inferred),
        'changed_symbols': [
            {key: node_map[nid].get(key, '') for key in ('id', 'qualified_name', 'source_file', 'source_location')}
            for nid in sorted(symbol_ids)
        ],
        'symbol_impacted_files': sorted(symbol_confirmed),
        'inferred_symbol_impacted_files': sorted(symbol_inferred),
        'symbol_coverage': coverage,
    }


def analyze_diff_impact(diff_text: str, root: str | Path | None = None) -> list[str]:
    """Return confirmed direct dependants; use details for the separate inferred tier."""
    return analyze_diff_impact_details(diff_text, root=root)["confirmed"]


def affected_tests(diff_text: str, root: str | Path | None = None) -> list[str]:
    return prt_impact_summary(diff_text, root=root).get('affected_tests', [])


def _graph_evidence_identity(status: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded, path-redacted identity for one persisted graph snapshot."""
    raw_path = str(status.get("path") or "")
    path_fingerprint = (
        hashlib.sha256(os.path.normcase(raw_path).encode("utf-8", errors="replace"))
        .hexdigest()[:20]
        if raw_path
        else ""
    )
    return {
        "built_at_commit": str(status.get("built_at_commit") or ""),
        "artifact_path_fingerprint": path_fingerprint,
        "artifact_mtime": float(status.get("mtime") or 0.0),
        "artifact_bytes": int(status.get("graph_bytes") or 0),
        "source_kind": str(status.get("source_kind") or "none"),
    }


def prt_impact_summary(
    diff_text: str, root: str | Path | None = None, *, refresh_if_stale: bool = False,
    allowed_roots: list[str] | None = None,
) -> dict[str, Any]:
    """Return deterministic structural impact hints for PRT."""
    from core.tooling.sandbox import path_allowed

    root_path = project_root(root)
    if not path_allowed(str(root_path), allowed_roots):
        return {"available": False, "error": "outside_allowed_roots", "affected_tests": []}
    artifact_before = _graph_file_fingerprint(graph_path(root_path))
    status = graph_status(root_path)
    if (
        refresh_if_stale
        and structural_graph_enabled()
        and (status.get("available") or auto_build_enabled())
        and (not status.get("available") or status.get("stale"))
    ):
        build_structural_graph_isolated(root_path, wait_for_existing=True)
        artifact_before = _graph_file_fingerprint(graph_path(root_path))
        status = graph_status(root_path)
    graph_evidence = _graph_evidence_identity(status)
    changed = _changed_files_from_diff(diff_text)
    changed_tests = sorted(path for path in changed if _is_test_path(path))
    if status.get("stale"):
        return {
            "available": False,
            "stale": True,
            "stale_reasons": list(status.get("stale_reasons") or []),
            "changed_files": changed,
            "impacted_files": [],
            "inferred_impacted_files": [],
            "affected_tests": changed_tests,
            "graph_evidence": graph_evidence,
        }
    data = load_or_build_graph_data(root_path, build_if_missing=False, refresh_if_stale=False)
    if artifact_before != _graph_file_fingerprint(graph_path(root_path)):
        return {
            'available': False, 'error': 'graph_changed_during_read',
            'changed_files': changed, 'impacted_files': [], 'inferred_impacted_files': [],
            'affected_tests': changed_tests, 'graph_evidence': graph_evidence,
        }
    if not data:
        return {
            "available": False,
            "changed_files": changed,
            "impacted_files": [],
            "inferred_impacted_files": [],
            "affected_tests": changed_tests,
            "graph_evidence": graph_evidence,
        }
    impact = _analyze_diff_impact_data(diff_text, data)
    impacted = impact["confirmed"]
    # Preserve every conservative candidate. Within the existing runner bound,
    # changed tests precede symbol-linked tests, then other file dependants.
    tests = list(dict.fromkeys(
        path for path in changed_tests + impact.get('symbol_impacted_files', []) + impacted
        if _is_test_path(path)
    ))
    nodes_by_file = _nodes_by_source_file(data)
    node_map = _node_map(data)
    touched_nodes = {nid for file in set(changed + impacted) for nid in nodes_by_file.get(file, set())}
    communities = sorted({
        int(node_map[nid]["community"])
        for nid in touched_nodes
        if nid in node_map and _as_int(node_map[nid].get("community")) is not None
    })
    analysis = load_analysis(root_path)
    god_files = _god_files(data, analysis)
    god_nodes_touched = sorted({file for file in set(changed + impacted) if file in god_files})
    cross_edges = _cross_community_edges_for_files(data, set(changed + impacted))
    community_overlap = _diff_community_overlap(data, changed, impacted, root_path)
    cycles = find_import_cycles(root=root_path, changed_files=set(changed), top_n=5)
    risk_score = structural_risk_from_signals(
        community_count=len(communities),
        cross_community_edges=bool(cross_edges),
        god_files_touched=bool(god_nodes_touched),
    )
    return {
        "available": True,
        "changed_files": changed,
        "impacted_files": impacted,
        "inferred_impacted_files": impact["inferred"],
        "affected_tests": tests,
        **{key: impact.get(key, []) for key in (
            'changed_symbols', 'symbol_impacted_files', 'inferred_symbol_impacted_files', 'symbol_coverage',
        )},
        "communities_touched": communities,
        "community_count": len(communities),
        "changed_community_count": len(community_overlap.get("changed_communities") or []),
        "impacted_community_count": len(community_overlap.get("impacted_communities") or []),
        "community_overlap_count": len(community_overlap.get("overlap_communities") or []),
        "external_impact_community_count": len(community_overlap.get("external_impact_communities") or []),
        "community_overlap": community_overlap,
        "cross_community_edges": cross_edges[:10],
        "cross_community_edge_count": len(cross_edges),
        "god_files_touched": god_nodes_touched,
        "import_cycles": cycles,
        "risk_score": risk_score,
        "stale": bool(status.get("stale")),
        "graph_evidence": graph_evidence,
    }


def format_prt_impact(summary: dict[str, Any], *, max_chars: int = 1600) -> str:
    if not summary:
        return ""
    if not summary.get("available") or summary.get("stale"):
        return "### MO structural graph impact - orientation only\n- Graph unavailable or stale; dependency and symbol coverage are unknown. Inspect current source; this is not a clean impact result."
    lines = ["### MO structural graph impact - orientation only"]
    lines.append(f"- Changed files: {len(summary.get('changed_files') or [])}; impacted dependents: {len(summary.get('impacted_files') or [])}.")
    if any(item.get('status') == 'partial' for item in summary.get('symbol_coverage') or []):
        lines.append('- Symbol coverage is partial; retained file dependants are conservative candidates. Removed owners require pre-image source inspection.')
    symbols = summary.get('changed_symbols') or []
    if symbols:
        lines.append('- Edited post-image symbols: ' + ', '.join(
            f"{item['source_file']}:{item['qualified_name']}" for item in symbols[:5]
        ) + '.')
    for label, key in (("Confirmed symbol dependants", "symbol_impacted_files"),
                       ("Inferred symbol dependants", "inferred_symbol_impacted_files"),
                       ("Conservative file dependants", "impacted_files"),
                       ("Affected-test candidates (not results)", "affected_tests")):
        paths = summary.get(key) or []
        if paths:
            lines.append(f"- {label} ({len(paths)}): " + ", ".join(str(path) for path in paths[:5])
                         + ("; additional paths omitted." if len(paths) > 5 else "."))
    if summary.get("communities_touched"):
        lines.append(f"- Communities touched: {', '.join(str(c) for c in summary['communities_touched'][:8])}.")
    overlap = summary.get("community_overlap") if isinstance(summary.get("community_overlap"), dict) else {}
    signals = [str(item) for item in (overlap.get("risk_signals") or []) if str(item).strip()]
    if signals:
        lines.append("- Community overlap: " + "; ".join(signals[:3]) + ".")
    if summary.get("cross_community_edge_count"):
        lines.append(f"- Cross-community coupling edges near diff: {summary['cross_community_edge_count']}.")
    if summary.get("god_files_touched"):
        lines.append("- God-node files touched: " + ", ".join(summary["god_files_touched"][:6]))
    if summary.get("import_cycles"):
        lines.append("- Import cycles: " + "; ".join(" -> ".join(item.get("cycle", [])[:5]) for item in summary["import_cycles"][:3]))
    text = "\n".join(lines)
    return redact_monitor_text(text, max_chars)


def structural_risk_from_signals(*, community_count: int, cross_community_edges: bool, god_files_touched: bool) -> int:
    """The canonical structural-risk formula: an additive boost from graph-topology
    signals. ONE place — ``structural_risk_score`` derives the signals from the graph and
    scores through this; PRT already has the signals from ``prt_impact_summary`` and scores
    through this too, instead of hand-copying the ``+4/+8/+3/+5`` math."""
    boost = 0
    count = int(community_count or 0)
    if count >= 2:
        boost += 4
    if count >= 4:
        boost += 4
    if cross_community_edges:
        boost += 3
    if god_files_touched:
        boost += 5
    return boost


def structural_risk_score(changed_files: list[str], impacted_files: list[str], root: str | Path | None = None) -> int:
    """Return an additive risk boost based on structural graph topology."""
    root_path = project_root(root)
    data = load_or_build_graph_data(root_path, build_if_missing=False, refresh_if_stale=False)
    if not data or graph_status(root_path).get("stale"):
        return 0
    files = {_norm_path(f) for f in list(changed_files or []) + list(impacted_files or []) if _norm_path(f)}
    if not files:
        return 0
    nodes_by_file = _nodes_by_source_file(data)
    node_map = _node_map(data)
    communities = {
        int(node_map[nid]["community"])
        for file in files for nid in nodes_by_file.get(file, set())
        if nid in node_map and _as_int(node_map[nid].get("community")) is not None
    }
    return structural_risk_from_signals(
        community_count=len(communities),
        cross_community_edges=bool(_cross_community_edges_for_files(data, files)),
        god_files_touched=bool(files & _god_files(data, load_analysis(root))),
    )


def find_import_cycles(
    *,
    root: str | Path | None = None,
    changed_files: set[str] | None = None,
    max_cycle_length: int = 5,
    top_n: int = 20,
) -> list[dict[str, Any]]:
    data = load_or_build_graph_data(project_root(root), build_if_missing=False, refresh_if_stale=False)
    if not data:
        return []
    file_by_node = _source_file_by_node(data)
    graph: dict[str, set[str]] = defaultdict(set)
    for edge in _edge_list(data):
        if not isinstance(edge, dict):
            continue
        relation = str(edge.get("relation") or edge.get("type") or "")
        if relation not in _IMPORT_RELATIONS:
            continue
        src = file_by_node.get(str(edge.get("source") or "")) or _norm_path(edge.get("source_file"))
        tgt = file_by_node.get(str(edge.get("target") or ""))
        if src and tgt and src != tgt:
            graph[src].add(tgt)
    if not graph:
        return []

    cycles: set[tuple[str, ...]] = set()

    def visit(start: str, current: str, path: list[str]) -> None:
        if len(path) > max_cycle_length:
            return
        for nxt in sorted(graph.get(current, set())):
            if nxt == start and len(path) > 1:
                cycles.add(_normalize_cycle(path))
            elif nxt not in path:
                visit(start, nxt, path + [nxt])

    for start in sorted(graph):
        visit(start, start, [start])
        if len(cycles) >= top_n * 4:
            break

    changed_norm = {_norm_path(f) for f in (changed_files or set()) if _norm_path(f)}
    result: list[dict[str, Any]] = []
    for cycle in sorted(cycles, key=lambda c: (len(c), c)):
        if changed_norm and not (set(cycle) & changed_norm):
            continue
        result.append({"cycle": list(cycle), "length": len(cycle), "why": "circular dependency"})
        if len(result) >= top_n:
            break
    return result


def maybe_update_graph_async(
    *,
    root: str | Path | None = None,
    reason: str = "",
    timeout: int = DEFAULT_GRAPH_REFRESH_TIMEOUT_SECONDS,
    max_files: int | None = None,
    explicit: bool = False,
    maintain_indexes: bool = False,
) -> bool:
    """Best-effort out-of-process structural graph refresh.

    Returns True when a worker was started. It is a no-op when structural graph
    support is disabled or ``MO_STRUCTURAL_GRAPH_AUTO_UPDATE=0``. Without a
    configured refresh command, a hidden below-normal-priority Python helper
    rebuilds the native community map. The foreground MO process only waits in a
    tiny daemon thread, so CPU-heavy parsing cannot hold its GIL or delay turns.
    Automatic callers may supply ``MO_STRUCTURAL_GRAPH_UPDATE_CMD`` with a
    ``{root}`` placeholder; explicit callers use the bounded native helper.
    Lifecycle callers set ``maintain_indexes``: that same helper checks freshness
    before building and delegates Git history to its existing, separate owner.
    """
    if not structural_graph_enabled():
        return False
    if not explicit and str(os.environ.get("MO_STRUCTURAL_GRAPH_AUTO_UPDATE", "1")).strip().lower() in {"0", "false", "off", "no", "disabled"}:
        return False
    root_path = project_root(root)
    if not explicit and not graph_exists(root_path) and not auto_build_enabled():
        return False
    command = [] if explicit or maintain_indexes else _refresh_command(root_path)
    native_worker = explicit or not bool(command)
    if native_worker:
        command = _native_refresh_worker_command(root_path, max_files=max_files, maintain_indexes=maintain_indexes)
    key = str(root_path)
    with _UPDATE_LOCK:
        if key in _UPDATE_RUNNING:
            # A source mutation that lands after the running worker took its
            # snapshot needs one follow-up. Lifecycle freshness checks carry no
            # new dirty evidence and must not manufacture a second no-op worker.
            if maintain_indexes and str(reason or "").strip().casefold() == "project-edit":
                _UPDATE_RUNNING[key] = True
            return False
        _UPDATE_RUNNING[key] = False
    refresh_lock = _acquire_graph_refresh_lock(root_path)
    if refresh_lock is None:
        with _UPDATE_LOCK:
            _UPDATE_RUNNING.pop(key, None)
        return False

    popen_kwargs: dict[str, Any] = {
        # ``python -m core...`` must start where the imported MO package is
        # resolvable.  The target root is passed explicitly to the worker and
        # may be an unrelated project that has no top-level ``core`` package.
        # Configured external commands retain the target as their working dir.
        "cwd": str(Path(__file__).resolve().parents[2] if native_worker else root_path),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
    }
    apply_windows_hidden_process_flags(
        popen_kwargs,
        new_process_group=True,
        below_normal_priority=True,
    )
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True

    try:
        _emit("structural_graph_update_started", root=root_path, reason=reason)
        proc = subprocess.Popen(command, **popen_kwargs)
    except Exception as exc:
        with _UPDATE_LOCK:
            _UPDATE_RUNNING.pop(key, None)
        _release_graph_refresh_lock(refresh_lock)
        _emit("structural_graph_update_failed", root=root_path, reason=f"{type(exc).__name__}: {exc}")
        return False

    def waiter() -> None:
        try:
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout, stderr = proc.communicate()
                ok = False
                detail = f"graph refresh exceeded {int(timeout)}s and was stopped"
            else:
                result = _refresh_worker_result(stdout) if native_worker else {}
                ok = bool(result.get("ok", result.get("built"))) if native_worker else proc.returncode == 0
                detail = str(
                    result.get("reason")
                    or result.get("path")
                    or stderr
                    or stdout
                    or reason
                )[:500]
            status = "structural_graph_update_ok" if ok else "structural_graph_update_failed"
            _emit(status, root=root_path, reason=detail)
        except Exception as exc:
            _emit("structural_graph_update_failed", root=root_path, reason=f"{type(exc).__name__}: {exc}")
        finally:
            _release_graph_refresh_lock(refresh_lock)
            with _UPDATE_LOCK:
                pending = _UPDATE_RUNNING.pop(key, False)
                if pending:
                    maybe_update_graph_async(
                        root=root_path, reason="coalesced-lifecycle-check",
                        timeout=timeout, max_files=max_files, maintain_indexes=True,
                    )

    threading.Thread(target=waiter, name="mo-structural-graph-waiter", daemon=True).start()
    return True


def _refresh_worker_result(stdout: str) -> dict[str, Any]:
    """Return the final JSON object emitted by the native refresh helper."""
    for line in reversed(str(stdout or "").splitlines()):
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return value
    return {}


def _refresh_worker_main(argv: list[str]) -> int:
    """Internal subprocess entrypoint; stdout is one bounded JSON result."""
    if not argv:
        print(json.dumps({"built": False, "reason": "refresh worker requires one project root"}))
        return 2
    root = argv[0]
    maintain_indexes = "--maintain-indexes" in argv[1:]
    if maintain_indexes:
        argv = list(argv)
        argv.remove("--maintain-indexes")
    max_files: int | None = None
    if len(argv) > 1:
        if len(argv) != 3 or argv[1] != "--max-files":
            print(json.dumps({"built": False, "reason": "invalid refresh worker arguments"}))
            return 2
        try:
            max_files = max(0, int(argv[2]))
        except (TypeError, ValueError):
            print(json.dumps({"built": False, "reason": "invalid --max-files value"}))
            return 2
    if sys.platform != "win32":
        try:
            os.nice(10)
        except (AttributeError, OSError):
            pass
    result = _maintain_project_indexes(project_root(root), max_files=max_files) if maintain_indexes else build_structural_graph(root, max_files=max_files)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok", result.get("built")) else 1


def _maintain_project_indexes(root: Path, *, max_files: int | None = None) -> dict[str, Any]:
    """Worker-only freshness checks; neither index certifies a project's work."""
    from .history import build_history_index, history_status

    outcomes: dict[str, dict[str, Any]] = {}
    try:
        status = graph_status(root)
        if status.get("available") and not status.get("stale"):
            outcomes["graph"] = {"ok": True, "status": "fresh"}
        elif not status.get("available") and not auto_build_enabled():
            outcomes["graph"] = {"ok": True, "status": "autobuild-disabled"}
        else:
            command = _refresh_command(root)
            if command:
                kwargs: dict[str, Any] = {"cwd": str(root), "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
                apply_windows_hidden_process_flags(kwargs, below_normal_priority=True)
                completed = subprocess.run(command, timeout=DEFAULT_GRAPH_REFRESH_TIMEOUT_SECONDS, **kwargs)
                status = graph_status(root)
                ok = completed.returncode == 0 and bool(status.get("available")) and not status.get("stale")
                outcomes["graph"] = {"ok": ok, "status": "refreshed" if ok else "refresh-incomplete"}
            else:
                built = build_structural_graph(root, max_files=max_files)
                outcomes["graph"] = {"ok": bool(built.get("built")), "status": built.get("status") or built.get("reason") or "built"}
    except Exception as exc:
        outcomes["graph"] = {"ok": False, "status": type(exc).__name__}
    try:
        if not _git_head(root):
            outcomes["history"] = {"ok": True, "status": "no-git-head"}
        else:
            status = history_status(root)
            if not status.get("available") and not auto_build_enabled():
                outcomes["history"] = {"ok": True, "status": "autobuild-disabled"}
            else:
                refresh = not status.get("available") or status.get("stale")
                if refresh:
                    status = build_history_index(root)
                outcomes["history"] = {
                    "ok": bool(status.get("available")) and not status.get("stale"),
                    "status": "refreshed" if refresh else "fresh",
                    "indexed_commits": status.get("indexed_commits", 0),
                    "discovered_commits": status.get("discovered_commits", 0),
                }
    except Exception as exc:
        outcomes["history"] = {"ok": False, "status": type(exc).__name__}
    try:
        from ..knowledge import maintain_manifest

        outcomes["knowledge"] = maintain_manifest(root)
    except Exception as exc:
        outcomes["knowledge"] = {"ok": False, "status": type(exc).__name__}
    return {
        "ok": all(value["ok"] for value in outcomes.values()),
        "reason": "; ".join(f"{name}: {value['status']}" for name, value in outcomes.items()),
        **outcomes,
    }


def _refresh_command(root_path: Path) -> list[str]:
    raw = str(os.environ.get("MO_STRUCTURAL_GRAPH_UPDATE_CMD", "") or "").strip()
    if not raw:
        return []
    try:
        parts = shlex.split(raw)
    except ValueError:
        return []
    return [part.replace("{root}", str(root_path)) for part in parts]


def _is_structural_graph_artifact(path: str) -> bool:
    normalized = _norm_path(path)
    return (
        normalized.startswith("graphify-out/")
        or normalized.startswith("memory/structural_graph/")
        # Generated manifests carry no symbols and are rewritten by ordinary
        # verification work, so fingerprinting them reported the whole graph
        # stale for a file that cannot change any node or edge.
        or normalized.endswith("/.mo-test-overlay.json")
        or normalized == ".mo-test-overlay.json"
    )


def _private_map_from_structural_graph(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Adapt the canonical persisted graph back to the indexer's delta shape.

    The adapter exists only in memory. It lets incremental extraction reuse the
    one structural artifact without persisting a second raw representation.
    """
    if not isinstance(data, dict) or not _unique_graph_node_ids(data):
        return None
    builder_version = str(data.get("builder_version") or "")
    if not builder_version:
        return None
    nodes: list[dict[str, Any]] = []
    for node in data.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        source = _norm_path(node.get("source_file") or node.get("filePath"))
        if not node_id:
            continue
        location = str(node.get("source_location") or "")
        line_numbers = [int(value) for value in re.findall(r"\d+", location)[:2]]
        node_type = str(node.get("file_type") or node.get("type") or "node")
        qualified_name = (
            str(node.get("qualified_name") or node.get("qualifiedName") or "")
            if node_type in {"function", "class", "interface"}
            else ""
        )
        raw_search_terms = node.get("search_terms") or node.get("searchTerms") or ""
        search_terms = (
            [str(term) for term in raw_search_terms[:18]]
            if isinstance(raw_search_terms, list)
            else str(raw_search_terms).split()[:18]
        )
        nodes.append({
            "id": node_id,
            "type": node_type,
            "name": str(node.get("label") or node.get("name") or node_id),
            "shortName": str(node.get("short_name") or node.get("shortName") or node.get("label") or ""),
            "qualifiedName": qualified_name,
            "filePath": source,
            "lineRange": line_numbers,
            "summary": str(node.get("summary") or ""),
            "searchTerms": search_terms,
            "tags": list(node.get("tags") or []),
            "symbols": list(node.get("symbols") or []),
        })
    edges: list[dict[str, Any]] = []
    for edge in _edge_list(data):
        if not isinstance(edge, dict):
            continue
        relation = str(edge.get("relation") or edge.get("type") or "related")
        edges.append({
            "source": str(edge.get("source") or ""),
            "target": str(edge.get("target") or ""),
            "type": relation,
            "direction": str(edge.get("direction") or "forward"),
            "weight": float(edge.get("weight") or 1.0),
            "confidence": str(edge.get("confidence") or ""),
            "resolution": str(edge.get("resolution") or ""),
        })
    return {
        "version": builder_version,
        "kind": "mo-graph-indexer-map",
        "project": dict(data.get("project") or {}) if isinstance(data.get("project"), dict) else {},
        "fingerprints": dict(data.get("fingerprints") or {}),
        "nodes": nodes,
        "edges": edges,
        "quality": dict(data.get("quality") or {}),
    }


def _structural_graph_from_private_map(private_graph: dict[str, Any], root: Path, fingerprints: dict[str, str]) -> dict[str, Any]:
    private_nodes = [node for node in private_graph.get("nodes", []) if isinstance(node, dict)]
    private_edges = [edge for edge in private_graph.get("edges", []) if isinstance(edge, dict)]
    source_by_node = {
        str(node.get("id") or ""): _norm_path(node.get("filePath"))
        for node in private_nodes
        if str(node.get("id") or "")
    }
    location_by_node: dict[str, str] = {}
    community_by_key: dict[str, int] = {}

    def community_for(source: str) -> int:
        key = _community_key(source)
        if key not in community_by_key:
            community_by_key[key] = len(community_by_key) + 1
        return community_by_key[key]

    nodes: list[dict[str, Any]] = []
    for node in private_nodes:
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        source = _norm_path(node.get("filePath"))
        line_range = node.get("lineRange") if isinstance(node.get("lineRange"), list) else []
        location = ""
        if len(line_range) >= 2:
            location = f"L{int(line_range[0])}-L{int(line_range[1])}"
        elif len(line_range) == 1:
            location = f"L{int(line_range[0])}"
        location_by_node[node_id] = location
        label = _clean(node.get("name") or source or node_id, 120)
        typ = _clean(node.get("type") or "node", 40)
        qualified_name = (
            _clean(node.get("qualifiedName"), 180)
            if typ in {"function", "class", "interface"}
            else ""
        )
        nodes.append({
            "id": node_id,
            "label": label,
            "type": typ,
            "file_type": typ,
            "source_file": source,
            "source_location": location,
            "summary": _clean(node.get("summary"), 260),
            "search_terms": " ".join(
                _clean(term, 64)
                for term in list(node.get("searchTerms") or [])[:18]
                if _clean(term, 64)
            )[:160],
            "short_name": _clean(node.get("shortName") or node.get("name"), 120),
            "qualified_name": qualified_name,
            "tags": list(node.get("tags") or []),
            "symbols": list(node.get("symbols") or []),
            "community": community_for(source),
        })

    if not _unique_graph_node_ids({"nodes": nodes}):
        raise ValueError("structural graph conversion requires unique non-empty node ids")

    links: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    node_ids = {str(node.get("id") or "") for node in nodes}
    for edge in private_edges:
        src = str(edge.get("source") or "")
        tgt = str(edge.get("target") or "")
        relation = str(edge.get("relation") or edge.get("type") or "related")
        key = (src, tgt, relation)
        if not src or not tgt or src not in node_ids or tgt not in node_ids or key in seen:
            continue
        seen.add(key)
        explicit_confidence = str(edge.get("confidence") or "").strip().upper()
        confidence = (
            explicit_confidence
            if explicit_confidence in CONFIDENCE_LABELS
            else _confidence_for_relation(relation)
        )
        resolution = _clean(edge.get("resolution"), 40)
        links.append({
            "source": src,
            "target": tgt,
            "relation": relation,
            "confidence": confidence,
            "provenance": "mo-native-structural-graph",
            "confidence_reason": _confidence_reason(relation, confidence),
            "source_file": source_by_node.get(src, ""),
            "source_location": location_by_node.get(src, ""),
            "resolution": resolution,
            "direction": str(edge.get("direction") or "forward"),
            "weight": float(edge.get("weight") or 1.0),
        })

    return {
        "directed": True,
        "kind": "mo-structural-code-map",
        "version": GRAPH_VERSION,
        "built_by": "mo",
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "built_at_commit": _git_head(root),
        "builder_version": str(private_graph.get("version") or ""),
        "project": {"root": str(root), "name": root.name},
        "fingerprints": fingerprints,
        "group_strategy": "path",
        "quality": dict(private_graph.get("quality") or {}),
        "edge_audit": _edge_audit_from_edges(links),
        "nodes": nodes,
        "links": links,
    }


def _community_key(source: str) -> str:
    path = _norm_path(source)
    if not path:
        return "root"
    strategy = os.environ.get("MO_STRUCTURAL_COMMUNITY_STRATEGY", "path").strip().lower() or "path"
    if strategy == "path_depth2":
        parts = path.split("/")
        return "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
    if strategy == "module":
        return path.replace("/", ".").rsplit(".", 1)[0] if "." in path else path
    first = path.split("/", 1)[0]
    return first or "root"


def _maybe_generate_code_map(path: Path) -> None:
    raw = os.environ.get("MO_CODE_MAP_AUTOGEN", "1").strip().lower()
    if raw in {"0", "false", "off", "no", "disabled"}:
        return
    try:
        from .generate_code_map import generate_code_map
        generate_code_map(path)
    except Exception:
        return


def _write_native_sidecars(path: Path, graph: dict[str, Any]) -> None:
    try:
        labels: dict[int, str] = {}
        for node in graph.get("nodes", []):
            if not isinstance(node, dict):
                continue
            cid = _as_int(node.get("community"))
            source = _norm_path(node.get("source_file"))
            if cid is not None and cid not in labels:
                labels[cid] = _community_key(source).replace("_", " ").title()
        atomic_write_json(
            path.parent / ".structural_labels.json",
            {str(k): v for k, v in sorted(labels.items())},
            indent=2,
            ensure_ascii=False,
        )
        analysis = _native_analysis(graph)
        atomic_write_json(path.parent / ".structural_analysis.json", analysis, indent=2, ensure_ascii=False)
        _write_status_manifest(path, graph)
    except Exception:
        traceback.print_exc()


def _native_analysis(graph: dict[str, Any]) -> dict[str, Any]:
    node_map = _node_map(graph)
    degree: dict[str, int] = defaultdict(int)
    surprises: list[dict[str, Any]] = []
    for edge in _edge_list(graph):
        if not isinstance(edge, dict):
            continue
        src = str(edge.get("source") or "")
        tgt = str(edge.get("target") or "")
        relation = str(edge.get("relation") or edge.get("type") or "")
        if relation not in _SKIP_RELATIONS_FOR_CONTEXT:
            degree[src] += 1
            degree[tgt] += 1
        src_c = _as_int(node_map.get(src, {}).get("community"))
        tgt_c = _as_int(node_map.get(tgt, {}).get("community"))
        src_node = node_map.get(src, {})
        tgt_node = node_map.get(tgt, {})
        src_file = _norm_path(src_node.get("source_file"))
        tgt_file = _norm_path(tgt_node.get("source_file"))
        if (
            src and tgt and src != tgt
            and src_file and tgt_file and src_file != tgt_file
            and src_c is not None and tgt_c is not None and src_c != tgt_c
            and relation not in _SKIP_RELATIONS_FOR_SURPRISES
        ):
            surprises.append({
                "source": src_node.get("label") or src,
                "target": tgt_node.get("label") or tgt,
                "source_file": src_file,
                "target_file": tgt_file,
                "relation": relation,
                "confidence": _edge_confidence(edge),
            })
    # Verification and documentation remain in the graph for affected-test
    # selection and source navigation, but they are supporting evidence rather
    # than architectural owners.  Letting a large test file become a god node
    # makes health and risk output describe the QA overlay as project direction.
    # Rank only product owners here; the underlying impact edges stay intact.
    gods = [
        {"id": node_id, "label": node_map.get(node_id, {}).get("label") or node_id, "degree": count}
        for node_id, count in sorted(degree.items(), key=lambda item: (-item[1], item[0]))
        if count >= 2
        and _source_role(_norm_path(node_map.get(node_id, {}).get("source_file"))) == "product owner"
    ][:8]
    # Community summary
    communities = _communities(graph)
    labels: dict[int, str] = {}
    for node in node_map.values():
        cid = _as_int(node.get("community"))
        source = _norm_path(node.get("source_file"))
        if cid is not None and cid not in labels:
            labels[cid] = _community_key(source).replace("_", " ").title()
    community_list = sorted(
        [{"id": cid, "label": labels.get(cid) or f"Community {cid}", "size": len(nodes)} for cid, nodes in communities.items()],
        key=lambda c: -c["size"],
    )
    return {"gods": gods, "surprises": surprises[:10], "communities": community_list}


def _edge_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    links = data.get("links") if isinstance(data.get("links"), list) else data.get("edges")
    return links if isinstance(links, list) else []


def _graph_topology(data: dict[str, Any]) -> tuple[
    list[dict[str, Any]],
    dict[str, list[tuple[str, dict[str, Any], bool]]],
]:
    """Return one compact undirected adjacency index for context selection."""
    key = id(data)
    with _GRAPH_TOPOLOGY_LOCK:
        cached = _GRAPH_TOPOLOGY_CACHE.get(key)
        if cached is not None and cached[0] is data:
            return cached[1], cached[2]

    nodes = _node_map(data)
    edges: list[dict[str, Any]] = []
    undirected: dict[str, list[tuple[str, dict[str, Any], bool]]] = defaultdict(list)
    for edge in _edge_list(data):
        if not isinstance(edge, dict):
            continue
        src = str(edge.get("source") or "")
        tgt = str(edge.get("target") or "")
        if not src or not tgt or src not in nodes or tgt not in nodes:
            continue
        edges.append(edge)
        undirected[src].append((tgt, edge, False))
        undirected[tgt].append((src, edge, True))
    for rows in undirected.values():
        rows.sort(key=lambda item: (
            str(item[1].get("relation") or item[1].get("type") or ""),
            item[0],
        ))
    result = (data, edges, dict(undirected))
    with _GRAPH_TOPOLOGY_LOCK:
        _GRAPH_TOPOLOGY_CACHE.clear()
        _GRAPH_TOPOLOGY_CACHE[key] = result
    return result[1], result[2]


def _confidence_for_relation(relation: str) -> str:
    relation = str(relation or "").strip().lower()
    if relation in _EXTRACTED_RELATIONS:
        return CONFIDENCE_EXTRACTED
    if relation in _INFERRED_RELATIONS:
        return CONFIDENCE_INFERRED
    return CONFIDENCE_AMBIGUOUS


def _confidence_reason(relation: str, confidence: str) -> str:
    relation = str(relation or "").strip().lower() or "related"
    if confidence == CONFIDENCE_EXTRACTED:
        return f"deterministic_{relation}"
    if confidence == CONFIDENCE_INFERRED:
        return f"mo_resolved_{relation}"
    return f"ambiguous_{relation}"


def _edge_confidence(edge: dict[str, Any]) -> str:
    raw = str(edge.get("confidence") or "").strip().upper()
    if raw in CONFIDENCE_LABELS:
        return raw
    # v1/v2 native graphs used MO_LOCAL for every edge. Keep those graphs
    # readable, but report a more useful confidence based on the relation.
    if raw == LEGACY_CONFIDENCE:
        return _confidence_for_relation(str(edge.get("relation") or edge.get("type") or ""))
    return _confidence_for_relation(str(edge.get("relation") or edge.get("type") or ""))


def _edge_audit(data: dict[str, Any]) -> dict[str, Any]:
    defaults = data.get("edge_defaults")
    return _edge_audit_from_edges(
        _edge_list(data),
        defaults=defaults if isinstance(defaults, dict) else None,
    )


def _edge_audit_from_edges(
    edges: list[dict[str, Any]],
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    confidence = {label: 0 for label in sorted(CONFIDENCE_LABELS)}
    provenance: dict[str, int] = {}
    legacy_edges = 0
    unknown_edges = 0
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        label = _edge_confidence(edge)
        confidence[label] = confidence.get(label, 0) + 1
        prov = _clean(
            edge.get("provenance")
            or edge.get("built_by")
            or (defaults or {}).get("provenance")
            or "unknown",
            80,
        )
        provenance[prov] = provenance.get(prov, 0) + 1
        raw = str(edge.get("confidence") or "").strip().upper()
        if raw == LEGACY_CONFIDENCE:
            legacy_edges += 1
        elif raw and raw not in CONFIDENCE_LABELS:
            unknown_edges += 1
    return {
        "confidence": confidence,
        "provenance": dict(sorted(provenance.items())),
        "legacy_confidence_edges": legacy_edges,
        "unknown_confidence_edges": unknown_edges,
    }


def _valid_edge_audit(value: dict[str, Any]) -> bool:
    confidence = value.get("confidence")
    provenance = value.get("provenance")
    return (
        isinstance(confidence, dict)
        and all(label in confidence for label in CONFIDENCE_LABELS)
        and isinstance(provenance, dict)
        and isinstance(value.get("legacy_confidence_edges"), int)
        and isinstance(value.get("unknown_confidence_edges"), int)
    )


def _node_map(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(node.get("id")): node
        for node in data.get("nodes", [])
        if isinstance(node, dict) and str(node.get("id") or "")
    }


def _focused_board_summary(task_board: Any | None) -> dict[str, Any]:
    if task_board is None:
        return {"total": 0, "done": 0, "open": 0, "tasks": []}
    try:
        if hasattr(task_board, "summary"):
            summary = task_board.summary()
        elif isinstance(task_board, dict):
            summary = dict(task_board)
        else:
            summary = {}
    except Exception:
        summary = {}
    tasks = summary.get("tasks") if isinstance(summary.get("tasks"), list) else []
    return {
        "title": _clean(summary.get("title") or "Task board", 160),
        "total": int(summary.get("total") or len(tasks)),
        "done": int(summary.get("done") or 0),
        "open": int(summary.get("open") or 0),
        "active_task_id": _clean(summary.get("active_task_id") or "", 40),
        "tasks": [task for task in tasks if isinstance(task, dict)][:12],
    }


def _focused_files(data: dict[str, Any], board_summary: dict[str, Any], *, query: str = "") -> list[dict[str, Any]]:
    scores: dict[str, int] = defaultdict(int)
    for node in data.get("nodes", []):
        if not isinstance(node, dict):
            continue
        source = _clean(node.get("source_file") or "", 220)
        if source:
            scores[source] += 1
    for task in board_summary.get("tasks") or []:
        for evidence in task.get("evidence") or []:
            for source in _evidence_paths(str(evidence)):
                scores[source] += 20
    for term in _terms(query):
        for source in list(scores):
            if term in source.lower():
                scores[source] += 5
    return [
        {"path": path, "score": score}
        for path, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:24]
    ]


def _evidence_paths(text: str) -> list[str]:
    paths: list[str] = []
    for match in re.findall(r"([A-Za-z0-9_.:/\\-]+\.(?:py|md|json|jsonl|toml|yaml|yml|txt|html|css|js|ts))", text):
        normalized = match.replace("\\", "/").strip()
        if ":" in normalized and not re.match(r"^[A-Za-z]:/", normalized):
            normalized = normalized.rsplit(":", 1)[-1]
        if normalized and normalized not in paths:
            paths.append(normalized)
    return paths


def _focused_map_css() -> str:
    from interface.theming import get_skin

    s = get_skin()
    return f""":root {{
      color-scheme: dark;
      --bg: {s.bg_dark};
      --panel: {s.bg_surface};
      --line: {s.code_map_line};
      --chip: {s.code_map_badge_bg};
      --text: {s.text_primary};
      --text-bright: {s.text_bright};
      --muted: {s.text_muted};
    }}"""


def _render_focused_map(
    status: dict[str, Any],
    board_summary: dict[str, Any],
    files: list[dict[str, Any]],
    *,
    query: str = "",
) -> str:
    task_rows = []
    for task in board_summary.get("tasks") or []:
        evidence = ", ".join(_clean(item, 120) for item in (task.get("evidence") or [])[:3])
        task_rows.append(
            "<tr>"
            f"<td>{html.escape(_clean(task.get('id'), 30))}</td>"
            f"<td>{html.escape(_clean(task.get('status'), 30))}</td>"
            f"<td>{html.escape(_clean(task.get('title'), 180))}</td>"
            f"<td>{html.escape(evidence)}</td>"
            "</tr>"
        )
    file_rows = [
        f"<tr><td>{html.escape(_clean(item.get('path'), 220))}</td><td>{int(item.get('score') or 0)}</td></tr>"
        for item in files
    ]
    skin_css = _focused_map_css()
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>MO Focused Map</title>
  <style>
    {skin_css}
    body {{ font-family: system-ui, sans-serif; margin: 24px; color: var(--text); background: var(--bg); }}
    h1, h2 {{ margin: 0 0 12px; }}
    section {{ margin: 0 0 24px; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--panel); }}
    th, td {{ border: 1px solid var(--line); padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: var(--chip); color: var(--text-bright); }}
    .meta {{ display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 16px; }}
    .pill {{ background: var(--chip); border: 1px solid var(--line); padding: 6px 10px; }}
    .note {{ color: var(--muted); }}
  </style>
</head>
<body>
  <h1>MO Focused Map</h1>
  <p class="note">Orientation only. Verify claims with source reads, traces, and tests.</p>
  <section class="meta">
    <div class="pill">Graph: {available}</div>
    <div class="pill">Nodes: {nodes}</div>
    <div class="pill">Edges: {edges}</div>
    <div class="pill">Stale: {stale}</div>
    <div class="pill">Tasks: {done}/{total} done</div>
    <div class="pill">Query: {query}</div>
  </section>
  <section>
    <h2>Task Board</h2>
    <table><thead><tr><th>ID</th><th>Status</th><th>Task</th><th>Evidence</th></tr></thead><tbody>{tasks}</tbody></table>
  </section>
  <section>
    <h2>Focused Files</h2>
    <table><thead><tr><th>Path</th><th>Score</th></tr></thead><tbody>{files}</tbody></table>
  </section>
</body>
</html>
""".format(
        skin_css=skin_css,
        available=html.escape(str(bool(status.get("available")))),
        nodes=int(status.get("nodes") or 0),
        edges=int(status.get("edges") or 0),
        stale=html.escape(str(bool(status.get("stale")))),
        done=int(board_summary.get("done") or 0),
        total=int(board_summary.get("total") or 0),
        query=html.escape(_clean(query, 120)),
        tasks="\n".join(task_rows) or "<tr><td colspan=\"4\">No active taskboard supplied</td></tr>",
        files="\n".join(file_rows) or "<tr><td colspan=\"2\">No focused files found</td></tr>",
    )


def _terms(text: str) -> list[str]:
    raw = re.findall(r"[a-zA-Z0-9_./-]{2,}", str(text or "").lower())
    base_terms: list[str] = []
    for item in raw:
        pieces = [item]
        pieces.extend(re.split(r"[/_.-]+", item))
        for piece in pieces:
            if len(piece) < 2 or piece in _STOPWORDS or piece in base_terms:
                continue
            base_terms.append(piece)
    # Preserve the user's later scope nouns before spending the bounded query
    # budget on plural/stem variants of earlier prose. Long maintenance prompts
    # otherwise lost terms such as ``interface`` and ``documentation`` while
    # retaining low-value pairs such as ``candidate``/``candidates``.
    terms = list(base_terms[:48])
    for piece in base_terms:
        if len(terms) >= 48:
            break
        for variant in _term_variants(piece):
            if variant not in _STOPWORDS and variant not in terms:
                terms.append(variant)
                if len(terms) >= 48:
                    break
    return terms


def _term_variants(term: str) -> list[str]:
    value = str(term or "").lower().strip()
    if not value:
        return []
    variants = [value]
    if value.endswith("ies") and len(value) > 4:
        variants.append(value[:-3] + "y")
    if value.endswith("s") and len(value) > 3:
        variants.append(value[:-1])
    elif len(value) > 2:
        variants.append(value + "s")
    if value.endswith("ing") and len(value) > 5:
        variants.append(value[:-3])
    if value.endswith("ment") and len(value) > 6:
        variants.append(value[:-4])
    return list(dict.fromkeys(v for v in variants if len(v) >= 2))


def _personalized_path_inputs(
    data: dict[str, Any], profile: Any | None,
) -> tuple[tuple[str, ...], str]:
    root = str((data.get("project") or {}).get("root") or "")
    important_paths = tuple(
        str(path).lower().replace("\\", "/").strip("/")
        for path in getattr(profile, "important_paths", []) or []
    )
    active_path = active_project_relative_path(profile, root) if root else ""
    return important_paths, active_path


def _rank_context_nodes(
    data: dict[str, Any],
    query: str,
    terms: list[str],
    profile: Any | None = None,
    *,
    limit: int = 64,
) -> list[tuple[float, str]]:
    """Use the shared BM25 ranker, then add the existing bounded path boost."""
    try:
        # Local import avoids a module cycle: search imports the structural graph
        # loaders, while context selection calls the ranker only after this module
        # has initialized.
        from .search import rank_graph_nodes

        ranked = rank_graph_nodes(data, query, top_n=max(1, int(limit)))
    except Exception:
        traceback.print_exc()
        ranked = []
    if not ranked:
        return _score_nodes(data, terms, profile=profile)

    important_paths, active_path = _personalized_path_inputs(data, profile)
    nodes = _node_map(data)
    scored: list[tuple[float, str]] = []
    for result in ranked:
        node_id = str(result.get("id") or "")
        node = nodes.get(node_id, {})
        if not node_id or not node:
            continue
        source = str(node.get("source_file") or "").lower().replace("\\", "/")
        score = float(result.get("score") or 0.0)
        score += _personalized_boost(source, important_paths, active_path)
        scored.append((score, node_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored


def _score_nodes(data: dict[str, Any], terms: list[str], profile: Any | None = None) -> list[tuple[float, str]]:
    scored: list[tuple[float, str]] = []
    # Compute term variants ONCE, not once per node — _term_variants is pure and
    # this loop runs over every graph node, so per-node recomputation was the
    # dominant cost of context selection (hundreds of thousands of calls).
    variants_by_term = [_term_variants(term) for term in terms]
    term_set = set(terms)
    test_terms_present = bool({"test", "tests", "pytest", "verify"} & term_set)
    doc_terms_present = bool({"doc", "docs", "documentation", "readme"} & term_set)
    important_paths, active_path = _personalized_path_inputs(data, profile)
    for node_id, node in _node_map(data).items():
        label = str(node.get("label") or node.get("name") or node_id).lower()
        bare = label.rstrip("()")
        source = str(node.get("source_file") or "").lower().replace("\\", "/")
        hay = " ".join(str(node.get(key, "")) for key in ("id", "label", "file_type", "source_file", "source_location", "community")).lower()
        node_id_lower = node_id.lower()
        score = 0.0
        for variants in variants_by_term:
            for variant in variants:
                if variant == bare or variant == label or variant == node_id_lower:
                    score += 100.0
                    break
                if bare.startswith(variant):
                    score += 25.0
                    break
                if variant in label:
                    score += 8.0
                    break
                if variant in source:
                    score += 5.0
                    break
                if variant in hay:
                    score += 1.0
                    break
        score += _personalized_boost(source, important_paths, active_path)
        source_role = _source_role(source)
        if source_role == "verification":
            score *= 0.70 if test_terms_present else 0.55
        elif source_role == "documentation":
            score *= 0.70 if doc_terms_present else 0.40
        if score > 0:
            scored.append((score, node_id))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return scored


def _personalized_boost(source: str, important_paths: tuple[str, ...], active_path: str) -> float:
    for path in important_paths:
        if path and (source == path or source.startswith(path + "/")):
            return 3.0
    if active_path and (source == active_path or source.startswith(active_path + "/")):
        return 1.5
    return 1.5 if source.startswith(("core/", "interface/", "tools/")) else 0.0


def _context_seed_ids(
    nodes: dict[str, dict[str, Any]],
    scored: list[tuple[float, str]],
    *,
    count: int,
    requested_roles: list[str] | None = None,
) -> list[str]:
    """Choose distinct owner/evidence seeds before expanding graph neighbors."""
    selected: list[str] = []
    selected_ids: set[str] = set()
    sources: set[str] = set()

    for requested_role in requested_roles or []:
        for _score, node_id in scored:
            source = str(nodes.get(node_id, {}).get("source_file") or "")
            if not source or source in sources or _source_role(source) != requested_role:
                continue
            selected.append(node_id)
            selected_ids.add(node_id)
            sources.add(source)
            break
        if len(selected) >= count:
            return selected

    for _score, node_id in scored:
        source = str(nodes.get(node_id, {}).get("source_file") or "")
        if node_id in selected_ids or (source and source in sources):
            continue
        selected.append(node_id)
        selected_ids.add(node_id)
        if source:
            sources.add(source)
        if len(selected) >= count:
            return selected
    for _score, node_id in scored:
        if node_id not in selected_ids:
            selected.append(node_id)
            selected_ids.add(node_id)
        if len(selected) >= count:
            break
    return selected


@lru_cache(maxsize=8192)
def _source_role(source: str) -> str:
    from .code_graph import is_test_source_path

    normalized = str(source or "").lower().replace("\\", "/")
    if is_test_source_path(normalized):
        return "verification"
    if normalized.endswith((".md", ".txt", ".rst")):
        return "documentation"
    return "product owner"


def _select_subgraph(
    data: dict[str, Any],
    seeds: list[str],
    scored: list[tuple[float, str]],
    *,
    max_nodes: int,
    depth: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    node_map = _node_map(data)
    all_edges, adjacency = _graph_topology(data)
    score_by_id = {node_id: float(score) for score, node_id in scored}

    selected: list[str] = []
    seen: set[str] = set()

    def add(nid: str) -> None:
        if nid in node_map and nid not in seen and len(selected) < max_nodes:
            seen.add(nid)
            selected.append(nid)

    for seed in seeds:
        add(seed)

    top_community = _as_int(node_map.get(seeds[0], {}).get("community")) if seeds else None
    queue: deque[tuple[str, int]] = deque((seed, 0) for seed in seeds if seed in node_map)
    selected_edges: list[dict[str, Any]] = []
    edge_seen: set[tuple[str, str, str]] = set()

    def crosses_file_boundary(edge: dict[str, Any]) -> bool:
        source = _norm_path(node_map.get(str(edge.get("source") or ""), {}).get("source_file"))
        target = _norm_path(node_map.get(str(edge.get("target") or ""), {}).get("source_file"))
        return bool(source and target and source != target)

    while queue and len(selected) < max_nodes:
        current, dist = queue.popleft()
        if dist >= depth:
            continue
        neighbours = sorted(
            adjacency.get(current, []),
            key=lambda item: (
                _as_int(node_map.get(item[0], {}).get("community")) != top_community,
                item[1].get("relation") in _SKIP_RELATIONS_FOR_CONTEXT,
                -score_by_id.get(item[0], 0.0),
                item[0],
            ),
        )
        for neighbour, edge, _reverse in neighbours:
            relation = str(edge.get("relation") or edge.get("type") or "")
            key = (str(edge.get("source") or ""), str(edge.get("target") or ""), relation)
            if (
                relation not in _SKIP_RELATIONS_FOR_CONTEXT
                and key not in edge_seen
                and crosses_file_boundary(edge)
            ):
                selected_edges.append(edge)
                edge_seen.add(key)
            if neighbour not in seen:
                add(neighbour)
                queue.append((neighbour, dist + 1))
            if len(selected) >= max_nodes:
                break

    for _score, nid in scored:
        if len(selected) >= max_nodes:
            break
        if top_community is None or _as_int(node_map.get(nid, {}).get("community")) == top_community:
            add(nid)
    for _score, nid in scored:
        if len(selected) >= max_nodes:
            break
        add(nid)

    selected_set = set(selected)
    for edge in all_edges:
        if len(selected_edges) >= 10:
            break
        if not isinstance(edge, dict):
            continue
        src = str(edge.get("source") or "")
        tgt = str(edge.get("target") or "")
        relation = str(edge.get("relation") or edge.get("type") or "")
        key = (src, tgt, relation)
        if (
            src in selected_set
            and tgt in selected_set
            and relation not in _SKIP_RELATIONS_FOR_CONTEXT
            and key not in edge_seen
            and crosses_file_boundary(edge)
        ):
            selected_edges.append(edge)
            edge_seen.add(key)
    return selected, selected_edges[:10]


def _format_context(
    data: dict[str, Any],
    selected_ids: list[str],
    selected_edges: list[dict[str, Any]],
    *,
    root: Path,
    labels: dict[int, str],
    status: dict[str, Any],
    max_chars: int,
    seed_ids: list[str] | None = None,
) -> str:
    node_map = _node_map(data)
    lines = [
        "### MO Project Code Direction - orientation only",
        f"Source: structural graph; project: {root.name}.",
        "Start with these likely owners. Tests and docs are supporting evidence; verify current source before changing or claiming behavior.",
    ]
    if status.get("stale"):
        lines.append("Graph freshness: may be stale vs current git HEAD.")

    seed_source_order: dict[str, int] = {}
    for index, node_id in enumerate(seed_ids or []):
        source = _norm_path(node_map.get(node_id, {}).get("source_file"))
        if source:
            seed_source_order.setdefault(source, index)

    owner_rows: list[tuple[str, str, str, list[str]]] = []
    seen_sources: set[str] = set()
    for node_id in selected_ids:
        node = node_map.get(node_id, {})
        source = _norm_path(node.get("source_file"))
        if not source:
            continue
        label = _clean(node.get("label") or node.get("name") or "", 90)
        node_type = str(node.get("file_type") or node.get("type") or "").lower()
        if source in seen_sources:
            for index, row in enumerate(owner_rows):
                if row[1] == source and label and node_type != "file" and label not in row[3]:
                    owner_rows[index][3].append(label)
                    break
            continue
        seen_sources.add(source)
        cid = _as_int(node.get("community"))
        community = _community_name(cid, labels) if cid is not None else ""
        focus = [label] if label and node_type != "file" else []
        owner_rows.append((_source_role(source), source, community, focus))
    role_order = {"product owner": 0, "verification": 1, "documentation": 2}
    owner_rows.sort(key=lambda row: (
        0 if row[1] in seed_source_order else 1,
        seed_source_order.get(row[1], role_order.get(row[0], 3)),
        role_order.get(row[0], 3),
    ))
    for role, source, community, focus in owner_rows:
        suffix = f" ({community})" if community else ""
        focus_text = f" — focus: {'; '.join(focus[:3])}" if focus else ""
        lines.append(f"- {role}: `{source}`{suffix}{focus_text}")

    if selected_edges:
        relationships: list[tuple[str, str, str, str]] = []
        seen_relationships: set[tuple[str, str, str]] = set()
        for edge in selected_edges:
            src = str(edge.get("source") or "")
            tgt = str(edge.get("target") or "")
            src_source = _norm_path(node_map.get(src, {}).get("source_file"))
            tgt_source = _norm_path(node_map.get(tgt, {}).get("source_file"))
            if not src_source or not tgt_source or src_source == tgt_source:
                continue
            relation = _clean(edge.get("relation") or edge.get("type") or "related", 50)
            conf = _clean(_edge_confidence(edge), 20)
            key = (src_source, tgt_source, relation)
            if key in seen_relationships:
                continue
            seen_relationships.add(key)
            relationships.append((src_source, relation, conf, tgt_source))
            if len(relationships) >= 8:
                break
        if relationships:
            lines.append("Relevant file relationships:")
            for src_source, relation, conf, tgt_source in relationships:
                suffix = f" [{conf}]" if conf else ""
                lines.append(f"- `{src_source}` --{relation}{suffix}--> `{tgt_source}`")
    text = redact_monitor_text("\n".join(lines), max_chars)
    digest = hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()[:8]
    text += f"\nDirection slice id: structural-{digest}"
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text


def _changed_files_from_diff(diff_text: str) -> list[str]:
    return list(dict.fromkeys(
        item['path'] or item['old_path'] for item in _diff_file_changes(diff_text)
        if item['path'] or item['old_path']
    ))


def _diff_path(value: str) -> str:
    """Decode Git's C-quoted UTF-8 paths, retaining literal spaces."""
    value = value.strip()
    if value.startswith('"'):
        import ast

        try:
            value = ast.literal_eval(value)
            try:
                value = value.encode('latin1').decode('utf-8')
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
        except (SyntaxError, ValueError):
            return ''
    else:
        value = value.split('\t', 1)[0]
    if value == '/dev/null':
        return ''
    if value.startswith(('a/', 'b/')):
        value = value[2:]
    return _norm_path(value)


def _diff_file_changes(diff_text: str) -> list[dict[str, Any]]:
    """Discover files and actual added lines; hunk context is not an edit.

    Removal coordinates belong to the pre-image. They must never be matched
    to a nearby, surviving post-image symbol merely because a line shifted.
    Source-context windows in review.diff_review retain their separate contract.
    """
    result: list[dict[str, Any]] = []
    current = None
    old_left = new_left = line_number = 0
    for line in str(diff_text or '').splitlines():
        if line.startswith('diff --git '):
            if current is not None and (old_left or new_left):
                current['malformed'] = True
            match = re.fullmatch(r'diff --git ("(?:\\.|[^"\\])*"|a/.+) ("(?:\\.|[^"\\])*"|b/.+)', line)
            current = None
            old_left = new_left = 0
            if match:
                current = {
                    'old_path': _diff_path(match[1]), 'path': _diff_path(match[2]),
                    'added_lines': set(), 'removed_lines': 0, 'malformed': False,
                }
                result.append(current)
            continue
        if current is None:
            continue
        if line.startswith('@@ '):
            if old_left or new_left:
                current['malformed'] = True
            match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
            old_left = new_left = 0
            if match:
                old_left = int(match[2]) if match[2] is not None else 1
                line_number = int(match[3])
                new_left = int(match[4]) if match[4] is not None else 1
            else:
                current['malformed'] = True
            continue
        if old_left or new_left:
            if line.startswith(' ') and old_left and new_left:
                old_left -= 1
                new_left -= 1
                line_number += 1
            elif line.startswith('-') and old_left:
                old_left -= 1
                current['removed_lines'] += 1
            elif line.startswith('+') and new_left:
                new_left -= 1
                current['added_lines'].add(line_number)
                line_number += 1
            elif not line.startswith('\\ No newline'):
                current['malformed'] = True
            continue
        if line.startswith('--- '):
            current['old_path'] = _diff_path(line[4:])
        elif line.startswith('+++ '):
            current['path'] = _diff_path(line[4:])
    if current is not None and (old_left or new_left):
        current['malformed'] = True
    return result


def _nodes_by_source_file(data: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    for node_id, node in _node_map(data).items():
        source = _norm_path(node.get("source_file"))
        if source:
            result[source].add(node_id)
    return result


def _source_file_by_node(data: dict[str, Any]) -> dict[str, str]:
    return {
        node_id: source for node_id, node in _node_map(data).items()
        if (source := _norm_path(node.get("source_file")))
    }


def _communities(data: dict[str, Any]) -> dict[int, list[str]]:
    result: dict[int, list[str]] = defaultdict(list)
    for node_id, node in _node_map(data).items():
        cid = _as_int(node.get("community"))
        if cid is not None:
            result[cid].append(node_id)
    return result


def _diff_community_overlap(
    data: dict[str, Any],
    changed_files: list[str],
    impacted_files: list[str],
    root: str | Path | None = None,
) -> dict[str, Any]:
    nodes_by_file = _nodes_by_source_file(data)
    node_map = _node_map(data)
    labels = load_labels(root)

    def add_files(files: list[str]) -> dict[int, set[str]]:
        by_community: dict[int, set[str]] = defaultdict(set)
        for path in files:
            normalized = _norm_path(path)
            if not normalized:
                continue
            for node_id in nodes_by_file.get(normalized, set()):
                cid = _as_int(node_map.get(node_id, {}).get("community"))
                if cid is not None:
                    by_community[cid].add(normalized)
        return by_community

    changed_by_community = add_files(changed_files)
    impacted_by_community = add_files(impacted_files)
    changed = set(changed_by_community)
    impacted = set(impacted_by_community)
    overlap = changed & impacted
    external = impacted - changed
    rows: list[dict[str, Any]] = []
    for cid in sorted(changed | impacted):
        changed_items = sorted(changed_by_community.get(cid, set()))
        impacted_items = sorted(impacted_by_community.get(cid, set()))
        rows.append({
            "community": cid,
            "label": _community_name(cid, labels),
            "changed_count": len(changed_items),
            "impacted_count": len(impacted_items),
            "changed_files": changed_items[:6],
            "impacted_files": impacted_items[:6],
        })

    signals: list[str] = []
    if len(changed) > 1:
        signals.append(f"diff directly changes {len(changed)} communities")
    if overlap:
        signals.append(f"{len(overlap)} changed community also has impacted dependents")
    if external:
        unit = "community" if len(external) == 1 else "communities"
        signals.append(f"impact spills into {len(external)} external {unit}")

    return {
        "changed_communities": sorted(changed),
        "impacted_communities": sorted(impacted),
        "overlap_communities": sorted(overlap),
        "external_impact_communities": sorted(external),
        "communities": rows[:12],
        "risk_signals": signals,
    }


def _cross_community_edges_for_files(data: dict[str, Any], files: set[str]) -> list[dict[str, Any]]:
    node_map = _node_map(data)
    file_by_node = _source_file_by_node(data)
    result: list[dict[str, Any]] = []
    for edge in _edge_list(data):
        if not isinstance(edge, dict):
            continue
        relation = str(edge.get("relation") or edge.get("type") or "")
        if relation in _SKIP_RELATIONS_FOR_CONTEXT:
            continue
        src = str(edge.get("source") or "")
        tgt = str(edge.get("target") or "")
        src_file = file_by_node.get(src)
        tgt_file = file_by_node.get(tgt)
        if src_file not in files and tgt_file not in files:
            continue
        src_c = _as_int(node_map.get(src, {}).get("community"))
        tgt_c = _as_int(node_map.get(tgt, {}).get("community"))
        if src_c is not None and tgt_c is not None and src_c != tgt_c:
            result.append({
                "source_file": src_file,
                "target_file": tgt_file,
                "relation": relation,
                "source_community": src_c,
                "target_community": tgt_c,
                "confidence": _edge_confidence(edge),
            })
    return result


def _god_files(data: dict[str, Any], analysis: dict[str, Any]) -> set[str]:
    node_map = _node_map(data)
    files: set[str] = set()
    for item in _analysis_list(analysis, "gods"):
        if not isinstance(item, dict):
            continue
        node = node_map.get(str(item.get("id") or ""))
        source = _norm_path(node.get("source_file")) if node else ""
        if source:
            files.add(source)
    return files


def _analysis_list(analysis: dict[str, Any], key: str) -> list[Any]:
    value = analysis.get(key)
    return value if isinstance(value, list) else []


def _normalize_cycle(cycle: list[str]) -> tuple[str, ...]:
    if not cycle:
        return tuple()
    best = min(range(len(cycle)), key=lambda idx: cycle[idx])
    return tuple(cycle[best:] + cycle[:best])


def _norm_path(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def _export_safe_path(value: Any) -> str | None:
    path = _norm_path(value)
    if not path:
        return ""
    low = path.lower()
    if Path(path).is_absolute() or low.startswith("../"):
        return None
    if low.startswith((".git/", "memory/", "logs/", ".mo/", "operator/", "personal/")):
        return None
    name = low.rsplit("/", 1)[-1]
    if name.startswith(".env") or any(part in name for part in ("secret", "token", "credential", "private-key")):
        return None
    return path


def _export_text(value: Any, limit: int = 260) -> str:
    text = _clean(value, limit)
    text = re.sub(r"[A-Za-z]:[\\/][^\s`'\"<>]+", "[path]", text)
    text = re.sub(r"/(?:Users|home|opt|var|tmp)/[^\s`'\"<>]+", "[path]", text)
    return redact_monitor_text(text, limit).strip()


def _is_test_path(path: str) -> bool:
    low = _norm_path(path).lower()
    return bool(
        low.startswith(("test/", "tests/"))
        or re.search(r"(^|/)(test_[^/]*|[^/]*_test)\.py$", low)
        or low.endswith((".test.js", ".test.ts", ".test.tsx", ".spec.js", ".spec.ts", ".spec.tsx"))
    )


def _community_name(cid: int | None, labels: dict[int, str]) -> str:
    if cid is None:
        return "unknown"
    return _clean(labels.get(cid) or f"Community {cid}", 80)


def _clean(value: Any, limit: int = 120) -> str:
    text = str(value or "").replace("\n", " ")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    return redact_monitor_text(text, limit).strip()


def _git_head(root: Path) -> str:
    try:
        proc = run_with_file_capture(["git", "rev-parse", "HEAD"], cwd=str(root), timeout=3)
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        traceback.print_exc()
    return ""


def _emit(
    status: str,
    *,
    root: Path | None = None,
    reason: str = "",
    file_count: int = 0,
    selected: list[dict[str, Any]] | None = None,
) -> None:
    try:
        monitor = get_monitor()
        if not monitor:
            return
        selected = selected or []
        monitor.emit("code_graph_context", {
            "status": redact_monitor_text(status, 80),
            "reason": redact_monitor_text(reason, 160),
            "project": redact_monitor_text(root.name if root else "", 80),
            "source": "structural_graph",
            "file_count": int(file_count or 0),
            "selected_count": len(selected),
            "node_ids": [redact_monitor_text(str(node.get("id") or ""), 160) for node in selected[:10]],
        })
    except Exception:
        traceback.print_exc()


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--refresh-worker"]:
        raise SystemExit(_refresh_worker_main(args[1:]))
    raise SystemExit("usage: python -m core.graph.structural_graph --refresh-worker <project-root>")
