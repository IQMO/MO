"""Stdio MCP server exposing MO's native structural graph to external agents.

The server is checkout-read-only and dependency-free: tools never edit the
project, while ``mo_build_graph`` is explicitly state-mutating because it may
refresh MO's private graph cache. It binds
to the project root from ``CLAUDE_PROJECT_DIR`` (set by Claude Code for stdio MCP
servers), an explicit ``MO_MCP_PROJECT_ROOT``, or the current working directory,
then exposes the same graph primitives
MO uses internally for orientation and blast-radius checks.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable
import uuid

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "mo-graph"
SERVER_VERSION = "9"
SERVER_INSTRUCTIONS = (
    "MO Graph is orientation, not proof. On first use in a project, try one relevant graph query, "
    "verify one result against source, then briefly report an honest usefulness rating out of 10, "
    "why, and one concrete example. Do not rate from the tool list alone. Build first when status is "
    "unavailable or stale. Prefer qualified symbols when known; a missing qualified symbol fails closed, "
    "and AMBIGUOUS caller/callee edges must be verified before refactoring."
)
_GRAPH_TRUST_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_SERVER_INSTANCE_ID = uuid.uuid4().hex[:12]
_SERVER_STARTED_AT = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
_SEND_LOCK = threading.Lock()
_DEFAULT_QUERY_TIMEOUT_SECONDS = 15.0
_MAX_QUERY_REQUEST_BYTES = 512 * 1024
_MAX_QUERY_RESPONSE_CHARS = 2 * 1024 * 1024


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _project_binding() -> tuple[Path, str]:
    raw = os.getenv("CLAUDE_PROJECT_DIR")
    source = "CLAUDE_PROJECT_DIR"
    if not raw:
        raw = os.getenv("MO_MCP_PROJECT_ROOT")
        source = "MO_MCP_PROJECT_ROOT"
    if not raw:
        raw = os.getcwd()
        source = "process_cwd"
    try:
        return Path(raw).resolve(), source
    except Exception:
        return Path.cwd().resolve(), "process_cwd_fallback"


def _project_root() -> Path:
    return _project_binding()[0]


def _server_script() -> Path:
    return Path(__file__).resolve()


def _toml_string(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _yaml_scalar(value: str) -> str:
    text = str(value)
    if not text:
        return '""'
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def config_snippets(
    project_root: str | Path | None = None,
    *,
    python_executable: str | None = None,
) -> dict[str, str]:
    """Return copy/paste MCP registration snippets for external agents.

    This helper is deliberately print-only. MO should not silently mutate Codex,
    Claude, Cursor, or other agent configuration files from inside the product
    repo; the operator decides where to register the read-only graph server.
    """
    root = Path(project_root).resolve() if project_root else _project_root()
    py = str(python_executable or sys.executable or "python")
    server = str(_server_script())
    env = {"MO_MCP_PROJECT_ROOT": str(root)}
    mcp_json = {
        "mcpServers": {
            SERVER_NAME: {
                "command": py,
                "args": [server],
                "env": env,
            }
        }
    }
    codex = "\n".join([
        f"[mcp_servers.{SERVER_NAME}]",
        f"command = {_toml_string(py)}",
        "args = [",
        f"  {_toml_string(server)},",
        "]",
        f"cwd = {_toml_string(str(root))}",
    ])
    safe_mo_tools = [
        str(tool.get("name") or "") for tool in TOOLS
        if str(tool.get("name") or "") != "mo_build_graph"
    ]
    mo_yaml = "\n".join([
        "mcp:",
        "  enabled: true",
        "  servers:",
        f"    - name: {SERVER_NAME}",
        f"      command: {_yaml_scalar(py)}",
        "      args:",
        f"        - {_yaml_scalar(server)}",
        f"      cwd: {_yaml_scalar(str(root))}",
        "      allow_tools:",
        *(f"        - {name}" for name in safe_mo_tools),
    ])
    return {
        "mcp_json": json.dumps(mcp_json, indent=2, ensure_ascii=False),
        "codex_toml": codex,
        "mo_config_yaml": mo_yaml,
    }


def _print_config_snippets(args: list[str]) -> int:
    root = _project_root()
    py = sys.executable or "python"
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in {"--project-root", "--root"} and i + 1 < len(args):
            root = Path(args[i + 1]).resolve()
            i += 2
            continue
        if arg in {"--python", "--python-executable"} and i + 1 < len(args):
            py = args[i + 1]
            i += 2
            continue
        if arg in {"--print-config", "--config-snippets"}:
            i += 1
            continue
        print(f"Unknown option for {SERVER_NAME}: {arg}", file=sys.stderr)
        return 2
    snippets = config_snippets(root, python_executable=py)
    print("# MO graph MCP server registration snippets")
    print("# Server is checkout-read-only; mo_build_graph writes only MO's private graph cache.")
    print("\n## Generic MCP / Claude .mcp.json")
    print(snippets["mcp_json"])
    print("\n## Codex config.toml")
    print("# Put this in the target project's trusted .codex/config.toml; cwd is intentionally explicit.")
    print(snippets["codex_toml"])
    print("\n## MO ~/.mo/config.yaml")
    print(snippets["mo_config_yaml"])
    return 0


def _git_head_fallback(root: Path) -> str:
    try:
        head = root / ".git" / "HEAD"
        if not head.is_file():
            return ""
        raw = head.read_text(encoding="utf-8", errors="replace").strip()
        if raw.startswith("ref:"):
            ref = raw.split(":", 1)[1].strip().replace("/", os.sep)
            ref_path = root / ".git" / ref
            if ref_path.is_file():
                return ref_path.read_text(encoding="utf-8", errors="replace").strip()
        return raw if raw and " " not in raw else ""
    except Exception:
        return ""


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _json_text(value: Any) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(value, indent=2, ensure_ascii=False, default=_json_default),
            }
        ]
    }


def _plain_text(value: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": str(value or "")}]}


def _tool_error(message: str) -> dict[str, Any]:
    result = _plain_text(message)
    result["isError"] = True
    return result


def _as_int(value: Any, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        result = int(value)
    except Exception:
        result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _as_float(
    value: Any,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        result = float(value)
    except Exception:
        result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _limit_items(items: list[Any], limit: int) -> tuple[list[Any], int, bool]:
    total = len(items)
    capped = list(items[:limit])
    return capped, total, total > len(capped)


def _cap_summary_lists(summary: dict[str, Any], *, limit: int) -> dict[str, Any]:
    capped = dict(summary)
    capped.pop("graph_path", None)
    for key in (
        'changed_files', 'impacted_files', 'inferred_impacted_files', 'affected_tests',
        'cross_community_edges', 'god_files_touched', 'import_cycles',
        'changed_symbols', 'symbol_impacted_files', 'inferred_symbol_impacted_files', 'symbol_coverage',
    ):
        value = capped.get(key)
        if isinstance(value, list):
            items, total, truncated = _limit_items(value, limit)
            capped[key] = items
            capped[f"total_{key}"] = total
            capped[f"returned_{key}"] = len(items)
            capped[f"{key}_truncated"] = truncated
    return capped


def _sanitize_private_state_text(value: str) -> str:
    text = str(value or "")
    home = str(Path.home())
    if home:
        text = text.replace(home, "~")
    return re.sub(r"~[\\/]\.mo[\\/]\S+", "~/.mo/<private-state>", text)


def _public_graph_status(status: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in status.items()
        if key not in {"path", "native_path", "compatibility_path"}
    }


def _graph_trust(root: Path) -> dict[str, Any]:
    from core.graph.structural_graph import graph_status

    cache_key = str(root)
    cached = _GRAPH_TRUST_CACHE.get(cache_key)
    now = time.monotonic()
    if cached and now - cached[0] < 5.0:
        return cached[1]
    status = graph_status(root)
    trust = _trust_from_status(status)
    _GRAPH_TRUST_CACHE[cache_key] = (time.monotonic(), trust)
    return trust


def _trust_from_status(status: dict[str, Any]) -> dict[str, Any]:
    return {
        key: status.get(key)
        for key in (
            "available", "query_loadable", "load_error", "stale", "stale_reasons",
            "trust", "quality", "built_at_commit", "git_head",
        )
        if key in status
    }


def _public_build_result(result: dict[str, Any]) -> dict[str, Any]:
    public = {
        key: value for key, value in result.items()
        if key != "path"
    }
    if "reason" in public:
        public["reason"] = _sanitize_private_state_text(str(public.get("reason") or ""))
    return public


def _status(_args: dict[str, Any]) -> dict[str, Any]:
    from core.graph.structural_graph import (
        DEFAULT_GRAPH_CACHE_IDLE_SECONDS,
        graph_status,
    )

    root, root_source = _project_binding()
    status = graph_status(root)
    if not status.get("git_head"):
        head = _git_head_fallback(root)
        if head:
            status["git_head"] = head
            built = str(status.get("built_at_commit") or "")
            status["stale"] = bool(status.get("stale") or (built and built != head))
            status["trust"] = "stale" if status["stale"] else "fresh_orientation"
    _GRAPH_TRUST_CACHE[str(root)] = (time.monotonic(), _trust_from_status(status))
    return _json_text({
        "project_root": str(root),
        "root_source": root_source,
        "server": {
            "version": SERVER_VERSION,
            "instance_id": _SERVER_INSTANCE_ID,
            "pid": os.getpid(),
            "parent_pid": os.getppid(),
            "started_at": _SERVER_STARTED_AT,
            "query_timeout_seconds": _DEFAULT_QUERY_TIMEOUT_SECONDS,
            "graph_cache_idle_seconds": DEFAULT_GRAPH_CACHE_IDLE_SECONDS,
            "script_mtime_ns": _server_script().stat().st_mtime_ns,
        },
        "status": _public_graph_status(status),
    })


def _build(args: dict[str, Any]) -> dict[str, Any]:
    from core.graph.structural_graph import graph_status, maybe_update_graph_async

    root = _project_root()
    max_files = args.get("max_files")
    status = graph_status(root)
    if status.get("available") and not status.get("stale"):
        result = {"built": False, "status": "current", "reason": "structural graph is already fresh", "stale_files": 0}
    else:
        started = maybe_update_graph_async(
            root=root,
            reason="mcp-explicit",
            max_files=_as_int(max_files, 0, minimum=1) if max_files else None,
            explicit=True,
        )
        result = {
            "built": False,
            "status": "queued" if started else "busy",
            "reason": "structural graph refresh queued" if started else "structural graph refresh already running",
            "stale_files": int(status.get("stale_files") or 0),
        }
    _GRAPH_TRUST_CACHE.pop(str(root), None)
    return _json_text({"project_root": str(root), "result": _public_build_result(result)})


def _search(args: dict[str, Any]) -> dict[str, Any]:
    import sqlite3

    from core.graph.search import search
    from core.graph.history import search_history

    query = str(args.get("query") or "").strip()
    if not query:
        return _tool_error("mo_code_search requires a non-empty 'query'.")
    top_n = _as_int(args.get("top_n"), 8, minimum=1, maximum=30)
    root = _project_root()
    trust = _graph_trust(root)
    results = search(query, cwd=root, top_n=top_n) if trust.get("available") else []
    try:
        history = search_history(query, root, paths=[str(hit.get("source_file") or "") for hit in results[:5]], limit=3)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        history = {"status": {"available": False, "error": type(exc).__name__}, "findings": [], "commits": []}
    return _json_text({
        "project_root": str(root),
        "query": query,
        "graph": trust,
        "query_status": "ok" if trust.get("available") else "unavailable",
        "reason": "" if trust.get("available") else trust.get("load_error") or "graph_unavailable",
        "results": results,
        "history": history,
    })


def _explain(args: dict[str, Any]) -> dict[str, Any]:
    from core.graph.query import explain

    query = str(args.get("query") or "").strip()
    if not query:
        return _tool_error("mo_graph_explain requires a non-empty 'query'.")
    limit = _as_int(args.get("limit"), 12, minimum=1, maximum=50)
    root = _project_root()
    return _json_text({"project_root": str(root), "graph": _graph_trust(root), "result": explain(query, cwd=root, limit=limit)})


def _neighbors(args: dict[str, Any]) -> dict[str, Any]:
    from core.graph.query import neighbors

    query = str(args.get("query") or "").strip()
    if not query:
        return _tool_error("mo_graph_neighbors requires a non-empty 'query'.")
    depth = _as_int(args.get("depth"), 1, minimum=1, maximum=4)
    limit = _as_int(args.get("limit"), 20, minimum=1, maximum=50)
    relation_filter = str(args.get("relation_filter") or "").strip() or None
    root = _project_root()
    return _json_text(
        {
            "project_root": str(root),
            "graph": _graph_trust(root),
            "result": neighbors(query, cwd=root, depth=depth, limit=limit, relation_filter=relation_filter),
        }
    )


def _path(args: dict[str, Any]) -> dict[str, Any]:
    from core.graph.query import shortest_path

    source = str(args.get("source") or "").strip()
    target = str(args.get("target") or "").strip()
    if not source or not target:
        return _tool_error("mo_graph_path requires non-empty 'source' and 'target'.")
    max_hops = _as_int(args.get("max_hops"), 8, minimum=1, maximum=8)
    root = _project_root()
    return _json_text({"project_root": str(root), "graph": _graph_trust(root), "result": shortest_path(source, target, cwd=root, max_hops=max_hops)})


def _graph_stats(args: dict[str, Any]) -> dict[str, Any]:
    from core.graph.query import stats

    limit = _as_int(args.get("limit"), 10, minimum=1, maximum=50)
    root = _project_root()
    return _json_text({"project_root": str(root), "graph": _graph_trust(root), "result": stats(cwd=root, limit=limit)})


def _trace(args: dict[str, Any], direction: str) -> dict[str, Any]:
    from core.graph.callgraph import get_callees, get_callers

    plural = "callers" if direction == "callers" else "callees"
    symbol = str(args.get("symbol") or "").strip()
    if not symbol:
        return _tool_error(f"mo_find_{plural} requires a non-empty 'symbol'.")
    max_depth = _as_int(args.get("max_depth"), 2, minimum=1, maximum=4)
    limit = _as_int(args.get("limit"), 30, minimum=1, maximum=200)
    root = _project_root()
    trace = get_callers if direction == "callers" else get_callees
    match = trace(symbol, cwd=root, max_depth=max_depth, return_meta=True)
    items, total, truncated = _limit_items(match["results"], limit)
    trust = _graph_trust(root)
    available = bool(match.get("available", trust.get("available")))
    return _json_text(
        {
            "project_root": str(root),
            "graph": trust,
            "query_status": "ok" if available else "unavailable",
            "reason": None if available else (match.get("reason") or trust.get("load_error") or "graph_unavailable"),
            "symbol": symbol,
            "match_type": match["match_type"],
            "ambiguous": match["ambiguous"],
            "matched_ids": match["matched_ids"],
            "max_depth": max_depth,
            "limit": limit,
            f"total_{plural}": total,
            f"returned_{plural}": len(items),
            "truncated": truncated,
            plural: items,
        }
    )


def _callers(args: dict[str, Any]) -> dict[str, Any]:
    return _trace(args, "callers")


def _callees(args: dict[str, Any]) -> dict[str, Any]:
    return _trace(args, "callees")


def _context(args: dict[str, Any]) -> dict[str, Any]:
    from core.graph.structural_graph import select_context

    query = str(args.get("query") or "").strip()
    if not query:
        return _tool_error("mo_graph_context requires a non-empty 'query'.")
    max_chars = _as_int(args.get("max_chars"), 3000, minimum=500, maximum=8000)
    max_nodes = _as_int(args.get("max_nodes"), 10, minimum=3, maximum=30)
    root = _project_root()
    context = select_context(query, cwd=root, max_chars=max_chars, max_nodes=max_nodes, build_if_missing=False, allow_stale=True)
    return _plain_text(context or "No structural graph context available; check mo_graph_status and build if needed.")


def _diff_impact(args: dict[str, Any]) -> dict[str, Any]:
    from core.graph.structural_graph import prt_impact_summary

    diff_text = str(args.get("diff_text") or "").strip()
    if not diff_text:
        return _tool_error("mo_diff_impact requires non-empty 'diff_text'.")
    limit = _as_int(args.get("limit"), 50, minimum=1, maximum=300)
    root = _project_root()
    summary = prt_impact_summary(diff_text, root=root)
    impacted = summary.get('impacted_files') or []
    affected = summary.get('affected_tests') or []
    impacted_items, impacted_total, impacted_truncated = _limit_items(impacted, limit)
    affected_items, affected_total, affected_truncated = _limit_items(affected, limit)
    return _json_text(
        {
            "project_root": str(root),
            "graph": {
                'available': bool(summary.get('available')),
                'stale': bool(summary.get('stale')),
                'stale_reasons': summary.get('stale_reasons') or [],
                'trust': 'fresh_orientation' if summary.get('available') else 'unavailable',
                **(summary.get('graph_evidence') or {}),
            },
            "limit": limit,
            "total_impacted_files": impacted_total,
            "returned_impacted_files": len(impacted_items),
            "impacted_files_truncated": impacted_truncated,
            "impacted_files": impacted_items,
            "inferred_impacted_files": (summary.get('inferred_impacted_files') or [])[:limit],
            "total_affected_tests": affected_total,
            "returned_affected_tests": len(affected_items),
            "affected_tests_truncated": affected_truncated,
            "affected_tests": affected_items,
            "summary": _cap_summary_lists(summary, limit=limit),
        }
    )


TOOLS: list[dict[str, Any]] = [
    {
        "name": "mo_graph_status",
        "description": "Show MO structural graph status, indexed/indexable file counts, project-root binding, and server-process identity for transport diagnostics.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "mo_build_graph",
        "description": "State-mutating: queue a refresh of MO's private structural-graph cache for this project root; never edits the checkout. Poll mo_graph_status for freshness, and check indexable_files before setting max_files.",
        "inputSchema": {
            "type": "object",
            "properties": {"max_files": {"type": "integer", "minimum": 1}},
        },
    },
    {
        "name": "mo_code_search",
        "description": "Search distinct likely owning files in the existing MO structural graph, retaining each owner's best matching symbol, plus indexed project history/findings with explicit freshness. History supplies source leads, never current verification. Queue mo_build_graph and check status when the structural graph is unavailable or stale.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_n": {"type": "integer", "minimum": 1, "maximum": 30},
            },
            "required": ["query"],
        },
    },
    {
        "name": "mo_graph_explain",
        "description": "Explain one MO graph node/symbol/file with bounded neighbours and confidence mix.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
            "required": ["query"],
        },
    },
    {
        "name": "mo_graph_neighbors",
        "description": "Return a bounded MO graph neighbourhood for a node/symbol/file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "depth": {"type": "integer", "minimum": 1, "maximum": 4},
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
                "relation_filter": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "mo_graph_path",
        "description": "Find a bounded undirected conceptual path, not runtime causality.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "target": {"type": "string"},
                "max_hops": {"type": "integer", "minimum": 1, "maximum": 8},
            },
            "required": ["source", "target"],
        },
    },
    {
        "name": "mo_graph_stats",
        "description": "Return MO graph audit stats: counts, freshness, index quality, confidence/provenance, hubs, path groups, and surprises.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "minimum": 1, "maximum": 50},
            },
        },
    },
    {
        "name": "mo_find_callers",
        "description": "Find who calls or depends on a qualified symbol with confidence/resolution metadata; missing qualified symbols fail closed and ambiguous bare names return candidates instead of merged callers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "max_depth": {"type": "integer", "minimum": 1, "maximum": 4},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "mo_find_callees",
        "description": "Find what a qualified symbol calls or depends on with confidence/resolution metadata; missing qualified symbols fail closed and ambiguous bare names return candidates instead of merged callees.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "max_depth": {"type": "integer", "minimum": 1, "maximum": 4},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
            },
            "required": ["symbol"],
        },
    },
    {
        "name": "mo_graph_context",
        "description": "Return a compact existing-graph context slice ranked by the shared BM25 index with source-diverse seeds for a review/audit query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_chars": {"type": "integer", "minimum": 500, "maximum": 8000},
                "max_nodes": {"type": "integer", "minimum": 3, "maximum": 30},
            },
            "required": ["query"],
        },
    },
    {
        "name": "mo_diff_impact",
        "description": "Analyze confirmed blast radius and separately report weaker inferred impact plus affected tests from a unified diff.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "diff_text": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 300},
            },
            "required": ["diff_text"],
        },
    },
]

HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "mo_graph_status": _status,
    "mo_build_graph": _build,
    "mo_code_search": _search,
    "mo_graph_explain": _explain,
    "mo_graph_neighbors": _neighbors,
    "mo_graph_path": _path,
    "mo_graph_stats": _graph_stats,
    "mo_find_callers": _callers,
    "mo_find_callees": _callees,
    "mo_graph_context": _context,
    "mo_diff_impact": _diff_impact,
}
_QUERY_TOOL_NAMES = frozenset(HANDLERS) - {"mo_graph_status", "mo_build_graph"}


class _QueryWorker:
    """Keep graph query caches warm without letting one query wedge stdio."""

    def __init__(
        self,
        *,
        command: list[str] | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._command = command or [sys.executable, str(_server_script()), "--query-worker"]
        configured_timeout = (
            os.getenv("MO_GRAPH_QUERY_TIMEOUT_SECONDS")
            if timeout_seconds is None
            else timeout_seconds
        )
        self.timeout_seconds = _as_float(
            configured_timeout,
            _DEFAULT_QUERY_TIMEOUT_SECONDS,
            minimum=0.05,
            maximum=60.0,
        )
        self._process: subprocess.Popen[str] | None = None

    def _start(self) -> subprocess.Popen[str]:
        process = self._process
        if process is not None and process.poll() is None:
            return process
        self.close()
        self._process = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        return self._process

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
        if process.stdout is not None:
            process.stdout.close()

    def call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        request_id = uuid.uuid4().hex
        request = {"id": request_id, "name": name, "arguments": args}
        encoded_request = json.dumps(request, ensure_ascii=False)
        if len(encoded_request.encode("utf-8")) > _MAX_QUERY_REQUEST_BYTES:
            return _tool_error("MO Graph query request is outside the 512 KiB bound.")
        try:
            process = self._start()
        except Exception as exc:
            return _tool_error(f"MO Graph query worker could not start: {type(exc).__name__}: {exc}")
        try:
            assert process.stdin is not None
            process.stdin.write(encoded_request + "\n")
            process.stdin.flush()
        except Exception as exc:
            self.close()
            return _tool_error(f"MO Graph query worker write failed: {type(exc).__name__}: {exc}")

        response_queue: queue.Queue[str] = queue.Queue(maxsize=1)

        def _read_response() -> None:
            try:
                assert process.stdout is not None
                response_queue.put(process.stdout.readline(_MAX_QUERY_RESPONSE_CHARS + 2))
            except Exception:
                response_queue.put("")

        reader = threading.Thread(target=_read_response, daemon=True)
        reader.start()
        try:
            line = response_queue.get(timeout=self.timeout_seconds)
        except queue.Empty:
            timeout_label = f"{self.timeout_seconds:g}"
            self.close()
            return _tool_error(
                f"MO Graph query '{name}' timed out after {timeout_label}s; "
                "the stuck query worker was discarded and will restart on the next request."
            )
        if not line:
            exit_code = process.poll()
            self.close()
            return _tool_error(
                f"MO Graph query worker exited without a response"
                f"{f' (exit {exit_code})' if exit_code is not None else ''}."
            )
        if len(line) > _MAX_QUERY_RESPONSE_CHARS or not line.endswith("\n"):
            self.close()
            return _tool_error(
                "MO Graph query worker response exceeded its 2 MiB transport bound."
            )
        try:
            response = json.loads(line)
        except Exception as exc:
            self.close()
            return _tool_error(f"MO Graph query worker returned invalid JSON: {type(exc).__name__}: {exc}")
        if not isinstance(response, dict) or response.get("id") != request_id:
            self.close()
            return _tool_error("MO Graph query worker returned a mismatched response.")
        result = response.get("result")
        if not isinstance(result, dict):
            self.close()
            return _tool_error("MO Graph query worker returned an invalid tool result.")
        return result


def _send(payload: dict[str, Any]) -> None:
    with _SEND_LOCK:
        sys.stdout.write(json.dumps(payload, ensure_ascii=True) + "\n")
        sys.stdout.flush()


def _response(rid: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _handle(
    msg: dict[str, Any],
    *,
    query_worker: _QueryWorker | None = None,
) -> dict[str, Any] | None:
    method = str(msg.get("method") or "")
    rid = msg.get("id")
    if method == "initialize":
        return _response(
            rid,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": SERVER_INSTRUCTIONS,
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return _response(rid, {"tools": TOOLS})
    if method == "resources/list":
        return _response(rid, {"resources": []})
    if method == "prompts/list":
        return _response(rid, {"prompts": []})
    if method == "tools/call":
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        name = str(params.get("name") or "")
        args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        handler = HANDLERS.get(name)
        if handler is None:
            return _response(rid, _tool_error(f"Unknown MO graph tool: {name}"))
        try:
            if query_worker is not None and name in _QUERY_TOOL_NAMES:
                result = query_worker.call(name, args)
            else:
                result = handler(args)
                if query_worker is not None and name == "mo_build_graph":
                    query_worker.close()
            return _response(rid, result)
        except Exception as exc:
            return _response(rid, _tool_error(f"{type(exc).__name__}: {exc}"))
    if rid is None:
        return None
    return _error(rid, -32601, f"Method not found: {method}")


def _query_worker_main() -> int:
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except Exception:
            continue
        if not isinstance(request, dict):
            continue
        request_id = request.get("id")
        name = str(request.get("name") or "")
        args = request.get("arguments") if isinstance(request.get("arguments"), dict) else {}
        handler = HANDLERS.get(name) if name in _QUERY_TOOL_NAMES else None
        if handler is None:
            result = _tool_error(f"Unknown MO Graph query tool: {name}")
        else:
            try:
                result = handler(args)
            except Exception as exc:
                result = _tool_error(f"{type(exc).__name__}: {exc}")
        _send({"id": request_id, "result": result})
    return 0


def main() -> int:
    repo = _repo_root()
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    args = sys.argv[1:]
    if args:
        if args[0] in {"-h", "--help"}:
            print(
                "usage: python -m core.mcp.mo_graph_server [--print-config "
                "[--project-root PATH] [--python PATH]]"
            )
            return 0
        if args[0] in {"--print-config", "--config-snippets"}:
            return _print_config_snippets(args)
        if args[0] == "--query-worker":
            return _query_worker_main()
        print(f"Unknown option for {SERVER_NAME}: {args[0]}", file=sys.stderr)
        return 2
    query_worker = _QueryWorker()
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue
            response = _handle(msg, query_worker=query_worker)
            if response is not None:
                _send(response)
    finally:
        query_worker.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
