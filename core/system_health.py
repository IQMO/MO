"""Deterministic self-audit reporting for MO backend health.

No provider calls, no writes, no external dependencies. The report only reads known
runtime files, graph artifacts, the learning database, and environment variables.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SystemHealth:
    """Structured health snapshot for renderers and tests."""

    files: dict[str, Any] = field(default_factory=dict)
    graph: dict[str, Any] = field(default_factory=dict)
    learning: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)


_FILE_TARGETS: dict[str, dict[str, int]] = {
    "logs/ghost_audit.jsonl": {"max_bytes": 1_000_000}, "logs/review_audit.jsonl": {"max_bytes": 1_000_000}, "logs/tool_audit.jsonl": {"max_bytes": 2_000_000},
    "memory/profile/learning.md": {"max_entries": 200}, "memory/profile/behavior.md": {"max_entries": 100}, "memory/workflow_candidates.jsonl": {"max_entries": 100},
    "memory/workflow_promoted.jsonl": {"max_entries": 50}, "memory/learning_suggestions.jsonl": {"max_entries": 100}, "skills/": {"max_files": 200}, "memory/goal-runs/": {"max_files": 50},
}

_CONFIG_DEFAULTS = dict(MO_BACKEND_MONITOR="0", MO_CODE_GRAPH="1", MO_CODE_GRAPH_MAX_FILES="1200", MO_GHOST_AUDIT_KEEP_LINES="2000", MO_GHOST_AUDIT_MAX_BYTES="1000000", MO_GOAL_RUNS_KEEP="50", MO_LEARNING_DECAY_DAYS="60", MO_LEARNING_SUGGESTION_TTL_DAYS="7", MO_LEARNING_SUGGESTIONS_ENABLED="1", MO_LEARNING_SUGGESTIONS_MAX="100", MO_PROFILE_BEHAVIOR_MAX_ENTRIES="100", MO_PROFILE_LEARNING_MAX_ENTRIES="200", MO_PROVIDER_AUDIT_MAX_BYTES="1000000", MO_REVIEW_AUDIT_KEEP_LINES="2000", MO_REVIEW_AUDIT_MAX_BYTES="1000000", MO_STRUCTURAL_COMMUNITY_STRATEGY="path", MO_STRUCTURAL_GRAPH="mo", MO_STRUCTURAL_GRAPH_AUTO_UPDATE="1", MO_STRUCTURAL_GRAPH_AUTOBUILD="1", MO_STRUCTURAL_GRAPH_DELTA_LIMIT="24", MO_STRUCTURAL_GRAPH_UPDATE_CMD="", MO_TOKEN_AWARE_TRUNCATION="0", MO_TOOL_AUDIT_KEEP_LINES="5000", MO_TOOL_AUDIT_MAX_BYTES="2000000", MO_WORKFLOW_CANDIDATE_MAX="100", MO_WORKFLOW_CANDIDATE_TTL_DAYS="7", MO_WORKFLOW_PROMOTED_MAX="50")


def check_file_health(root: str = ".") -> dict[str, Any]:
    """Scan known append-only files for size, count, and cap status."""
    out: dict[str, Any] = {}
    for name, caps in _FILE_TARGETS.items():
        path = Path(root) / name
        if not path.exists():
            out[name] = {"exists": False, "bytes": 0, "status": "missing"}
            continue
        if path.is_dir():
            files = sorted(item for item in path.rglob("*") if item.is_file())
            cap = caps.get("max_files")
            count = len(files)
            total = sum(_stat(item)["bytes"] for item in files)
            out[name] = {"exists": True, "is_dir": True, "files": count, "bytes": total, "max_files": cap, "oldest_file": min((_stat(f)["modified"] for f in files), default=0), "newest_file": max((_stat(f)["modified"] for f in files), default=0), "status": "over_cap" if cap is not None and count > cap else "ok"}
            continue
        lines = _read_text(path).splitlines()
        stat = _stat(path)
        max_bytes = caps.get("max_bytes")
        max_entries = caps.get("max_entries")
        entries = _entry_count(name, lines)
        over_bytes = max_bytes is not None and stat["bytes"] > max_bytes
        over_entries = max_entries is not None and entries > max_entries
        out[name] = {"exists": True, "is_dir": False, "bytes": stat["bytes"], "modified": stat["modified"], "lines": len(lines), "entries": entries, "max_bytes": max_bytes, "max_entries": max_entries, "rotation_applied": _has_recent_rotation_marker(lines), "status": "over_cap" if over_bytes or over_entries else "ok"}
    return out


def check_graph_health(root: str = ".") -> dict[str, Any]:
    """Report structural and private graph size, counts, and high-degree nodes."""
    base = Path(root)
    code_paths = [base / "memory/code_graph/v4_index.json", base / "memory/code_graph/knowledge-graph.json"]
    code = next((_graph_summary(path) for path in code_paths if path.exists()), None)
    return {"structural": _graph_summary(base / "memory/structural_graph/graph.json"), "code_graph": code or {"exists": False, "status": "missing"}}


def check_learning_health(root: str = ".") -> dict[str, Any]:
    """Report learning files, cross-reference bridge state, and memory DB state."""
    base = Path(root)
    learning = _read_text(base / "memory/profile/learning.md")
    behavior = _read_text(base / "memory/profile/behavior.md")
    cats = Counter(re.findall(r"^- ([a-zA-Z_][\w-]*):", learning, re.M))
    return {
        "profile_learning": {"entries": len(re.findall(r"^## \S+T\S+Z\s+—\s+profile learning", learning, re.M)), "categories": dict(sorted(cats.items()))},
        "behavior_rules": {"count": len([line for line in behavior.splitlines() if line.startswith("- ")]), "categories": dict(Counter(re.findall(r"^- ([\w-]+):", behavior, re.M)))},
        "workflow": {"candidates": _jsonl_count(base / "memory/workflow_candidates.jsonl"), "promoted": _jsonl_count(base / "memory/workflow_promoted.jsonl")},
        "skills": _skills_summary(base / "skills"),
        "finding_patterns": (_fp := _patterns_summary(_load_json(base / "memory/review_history/patterns.json"))),
        "operator_terms": _terms_summary(base / "memory/profile/terms.md"),
        "memory": _memory_summary(base / "memory/learning.sqlite"),
        # Live truth, not a hardcoded constant: the review→patterns bridge is
        # active once any fixed/ignored feedback has actually been recorded.
        "bridges": {
            "feedback_to_finding_patterns": bool((_fp.get("fixed", 0) or 0) + (_fp.get("ignored", 0) or 0)),
            "learning_to_skills": True,
            "terms_to_provider": False,   # known-pending integration (tracked, not yet wired)
        },
    }


def check_config_coverage() -> dict[str, Any]:
    """Check proposal-defined MO_* environment variables against defaults."""
    out = {}
    for name, default in sorted(_CONFIG_DEFAULTS.items()):
        value = os.environ.get(name)
        out[name] = {"set": value is not None, "value": value if value is not None else f"(default: {default})", "default": default, "matches_default": value is None or value == default}
    return out


def build_health_report(root: str = ".") -> SystemHealth:
    """Assemble a full backend health report."""
    return SystemHealth(files=check_file_health(root), graph=check_graph_health(root), learning=check_learning_health(root), config=check_config_coverage())


def _graph_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "status": "missing"}
    data = _load_json(path)
    stat = _stat(path)
    if not isinstance(data, dict):
        return {"exists": True, "bytes": stat["bytes"], "error": "unreadable"}
    nodes = data.get("nodes") or []
    edges = data.get("edges") or data.get("links") or []
    degree: Counter[str] = Counter()
    for edge in edges:
        if isinstance(edge, dict):
            degree[str(edge.get("source") or "")] += 1
            degree[str(edge.get("target") or edge.get("target_id") or "")] += 1
    god_nodes = []
    for node in nodes:
        if isinstance(node, dict):
            node_id = str(node.get("id") or "")
            god_nodes.append({"id": node_id, "name": node.get("name") or node.get("label") or node_id, "degree": degree.get(node_id, 0)})
    communities = {str(n.get("community")) for n in nodes if isinstance(n, dict) and n.get("community") is not None}
    return {"exists": True, "path": str(path), "bytes": stat["bytes"], "modified": stat["modified"], "version": data.get("version", "unknown"), "built_at": data.get("built_at"), "nodes": len(nodes), "edges": len(edges), "communities": len(communities), "god_nodes": sorted(god_nodes, key=lambda n: (-n["degree"], n["id"]))[:5]}


def _patterns_summary(data: Any) -> dict[str, int]:
    if not isinstance(data, dict):
        return {"categories": 0, "fixed": 0, "ignored": 0, "preferences": 0}
    # finding_patterns stores feedback under operator_preferences[category]
    # = {"fixed": N, "ignored": M}; that is the authoritative source.
    prefs = data.get("operator_preferences") or {}
    fixed = ignored = 0
    if isinstance(prefs, dict):
        for stats in prefs.values():
            if isinstance(stats, dict):
                fixed += int(stats.get("fixed", 0) or 0)
                ignored += int(stats.get("ignored", 0) or 0)
    patterns = data.get("patterns")
    cats = {item.get("category") for item in patterns if isinstance(item, dict)} if isinstance(patterns, list) else set()
    return {"categories": len(cats), "fixed": fixed, "ignored": ignored, "preferences": len(prefs) if isinstance(prefs, dict) else 0}


def _terms_summary(path: Path) -> dict[str, Any]:
    return {"exists": path.exists(), "count": len(re.findall(r"^[-*] `?([^`:]+)`?:", _read_text(path), re.M))}


def _skills_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "packs": 0, "generated": 0}
    packs = list(path.glob("*/SKILL.md"))
    generated = 0
    for skill_path in packs:
        text = _read_text(skill_path)
        if "candidate_id:" in text or "provenance: \"confirmed-learning\"" in text:
            generated += 1
    return {"exists": True, "packs": len(packs), "generated": generated}


def _memory_summary(path: Path) -> dict[str, Any]:
    out = {"exists": path.exists(), "turns": 0, "fts5": False, "miss_terms": 0}
    if not path.exists():
        return out
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        tables = {str(name): str(sql or "") for name, sql in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='table'").fetchall()}
        if "messages" in tables:
            out["turns"] = _table_count(conn, "messages")
        elif "turns" in tables:
            out["turns"] = _table_count(conn, "turns")
        out["fts5"] = any("fts5" in sql.lower() for sql in tables.values())
        if "recall_misses" in tables:
            out["miss_terms"] = _table_count(conn, "recall_misses")
        conn.close()
    except Exception:
        out["error"] = "unreadable"
    return out


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    safe = '"' + table.replace('"', '""') + '"'
    row = conn.execute(f"SELECT COUNT(*) FROM {safe}").fetchone()
    return int(row[0] if row else 0)


def _jsonl_count(path: Path) -> int:
    return len([line for line in _read_text(path).splitlines() if line.strip()])


def _entry_count(name: str, lines: list[str]) -> int:
    text = "\n".join(lines)
    if name.endswith("learning.md"):
        return len(re.findall(r"^## \S+T\S+Z\s+—\s+profile learning", text, re.M))
    if name.endswith("behavior.md"):
        return len([line for line in lines if line.startswith("- [")])
    if name.endswith(".jsonl"):
        return len([line for line in lines if line.strip()])
    return len(lines)


def _has_recent_rotation_marker(lines: list[str]) -> bool:
    return any("# pruned" in line or "# rotated" in line or "truncated" in line for line in lines[-5:])


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _stat(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
        return {"bytes": stat.st_size, "modified": stat.st_mtime}
    except OSError:
        return {"bytes": 0, "modified": 0}
