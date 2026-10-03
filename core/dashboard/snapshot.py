"""Read-only dashboard snapshot for MO's user-facing brain view.

The dashboard is a synthesis surface, not a source of truth. It reads existing
runtime/profile/graph ledgers, redacts display text, and returns a small,
serializable snapshot that terminal and HTML renderers can share.
"""
from __future__ import annotations

import os
import platform
import time
from pathlib import Path
from typing import Any

from ..state.paths import DASHBOARD_HTML_PATH, resolve_state_path
from ..tooling.sandbox import redact_sensitive_text

DASHBOARD_VERSION = "mo-dashboard-v1"

_PROFILE_FILE_UPDATE_MODES = {
    "operator.md": "curated profile; name can be captured from an explicit introduction",
    "thinking_model.md": "curated reasoning preferences",
    "terms.md": "explicit operator term definitions",
    "learning.md": "explicit corrections and approved workflow learning",
    "behavior.md": "compact mirror of accepted profile learning",
    "facts.md": "validated durable fact capture and profile fact edits",
}


def build_dashboard_snapshot(agent: Any = None, *, root: str | Path | None = None,
                             graph_summary: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a sanitized, read-only snapshot of MO work/profile/runtime state."""
    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    active_root = agent._effective_project_cwd() if hasattr(agent, "_effective_project_cwd") else getattr(agent, "project_cwd", "")
    project_root = Path(root or active_root or os.getcwd()).expanduser().resolve(strict=False)
    created_at = time.time()
    continuity = _continuity_snapshot(agent)
    recent_boards = _recent_boards(limit=8)
    graph = _graph_summary(project_root) if graph_summary is None else graph_summary
    learning_status = _learning_status(agent, cfg)
    learning = learning_status.as_dict()
    memory = getattr(agent, "memory", None)
    retrieval_status = getattr(memory, "retrieval_status", None)
    if callable(retrieval_status):
        try:
            status = retrieval_status()
        except Exception:
            status = {"mode": "unavailable", "reason": "status_error", "fts5_available": False}
    else:
        status = {"mode": "unavailable", "reason": "memory_not_loaded", "fts5_available": False}
    learning["episodic_retrieval"] = {
        "mode": _safe_text(status.get("mode"), 40),
        "reason": _safe_text(status.get("reason"), 80),
        "fts5_available": bool(status.get("fts5_available")),
    }
    snapshot = {
        "version": DASHBOARD_VERSION,
        "created_at": created_at,
        "generated_at": _iso(created_at),
        "environment": _environment(agent, project_root),
        "work": _work_summary(continuity, recent_boards),
        "profile": _profile_summary(agent, cfg),
        "learning": learning,
        "work_learning": _work_learning_summary(agent, learning_status),
        "graph": graph,
        "lsp": agent.lsp_manager.status(str(project_root)) if getattr(agent, "lsp_manager", None) else {"state": "unavailable"},
        "runtime": _runtime_summary(agent, continuity),
        "mail": _mail_status(cfg),
        "life": _life_status(cfg),
        "artifacts": _artifact_summary(graph, cfg),
        "provenance": _provenance(),
    }
    return _redact_snapshot(snapshot)


def _mail_status(config: dict) -> dict[str, Any]:
    from ..mail.service import MailService

    return MailService(config).status()


def _life_status(config: dict) -> dict[str, Any]:
    from ..life.items import summary

    try:
        return {"available": True, **summary(config=config)}
    except (OSError, ValueError):
        return {"available": False}


def _continuity_snapshot(agent: Any) -> dict[str, Any]:
    try:
        from ..runtime.continuity import build_current_work_snapshot

        snap = build_current_work_snapshot(agent)
        return snap if isinstance(snap, dict) else {}
    except Exception as exc:
        return {"error": type(exc).__name__}


def _environment(agent: Any, project_root: Path) -> dict[str, Any]:
    from ..runtime.surface_identity import normalize_runtime_surface

    return {
        "product": "MO Agent",
        "surface": _safe_text(
            normalize_runtime_surface(getattr(agent, "_current_runtime_surface", "terminal")),
            80,
        ),
        "project": _safe_text(str(project_root), 260),
        "os": _safe_text(platform.system() or os.name, 80),
        "os_release": _safe_text(platform.release(), 80),
        "python": _safe_text(platform.python_version(), 40),
        "instance": _safe_text(getattr(agent, "instance_id", ""), 120),
    }


def _work_summary(continuity: dict[str, Any], recent_boards: list[dict[str, Any]]) -> dict[str, Any]:
    from ..runtime.continuity import distinct_board_totals

    live = continuity.get("live_taskboard") if isinstance(continuity.get("live_taskboard"), dict) else {}
    latest = continuity.get("latest_taskboard") if isinstance(continuity.get("latest_taskboard"), dict) else {}
    resumable = continuity.get("resumable_board") if isinstance(continuity.get("resumable_board"), dict) else {}
    counts = distinct_board_totals(live, latest, resumable)
    open_count = counts["open"]
    completed = counts["completed"]
    total = counts["total"]
    return {
        "open": open_count,
        "completed": completed,
        "total": total,
        "available": bool(continuity) and not bool(continuity.get("error")),
        "verdict": "work unavailable" if not continuity or continuity.get("error") else "open work visible" if open_count else "no open runtime work visible",
        "live_taskboard": _compact_board(live),
        "latest_taskboard": _compact_board(latest),
        "resumable_board": _compact_board(resumable),
        "recent_boards_scope": "all sessions",
        "recent_boards": recent_boards,
    }


def _compact_board(board: dict[str, Any]) -> dict[str, Any]:
    if not board or (board.get("state") == "none" and not any(board.get(key) for key in ("board_id", "title", "objective"))):
        return {}
    return {
        "board_id": _safe_text(board.get("board_id"), 80),
        "session_id": _safe_text(board.get("session_id"), 120),
        "turn_id": _safe_text(board.get("turn_id"), 80),
        "state": _safe_text(board.get("state") or "none", 80),
        "title": _safe_text(board.get("title") or board.get("objective") or "", 180),
        "open": int(board.get("open") or 0),
        "completed": int(board.get("completed") or 0),
        "total": int(board.get("total") or 0),
        "open_titles": [_safe_text(item, 140) for item in list(board.get("open_titles") or [])[:5]],
        "open_tasks": [{key: _safe_text(task.get(key), 240) for key in ("id", "title", "status", "blocker")}
                       for task in list(board.get("open_tasks") or [])[:6] if isinstance(task, dict)],
    }


def _recent_boards(*, limit: int = 8) -> list[dict[str, Any]]:
    try:
        from ..tasking.task_board import read_recent_snapshots

        rows = read_recent_snapshots(limit=60)
    except Exception:
        return []
    latest: dict[str, dict[str, Any]] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        board_id = str(item.get("board_id") or item.get("turn_id") or item.get("title") or len(latest))
        latest[board_id] = item
    ordered = sorted(
        latest.values(),
        key=lambda item: float(item.get("updated_at") or item.get("created_at") or 0.0),
        reverse=True,
    )
    out: list[dict[str, Any]] = []
    for item in ordered[: max(0, int(limit or 8))]:
        tasks = [task for task in list(item.get("tasks") or []) if isinstance(task, dict)]
        open_count = sum(1 for task in tasks if str(task.get("status") or "") in {"pending", "active", "blocked"})
        out.append(
            {
                "board_id": _safe_text(item.get("board_id"), 80),
                "session_id": _safe_text(item.get("session_id"), 120),
                "turn_id": _safe_text(item.get("turn_id"), 80),
                "title": _safe_text(item.get("title") or item.get("objective") or "(untitled board)", 160),
                "state": _safe_text(item.get("state") or item.get("event") or "", 80),
                "open": open_count,
                "completed": sum(1 for task in tasks if str(task.get("status") or "") == "completed"),
                "total": len(tasks),
                "updated_at": float(item.get("updated_at") or item.get("created_at") or 0.0),
                "tasks": [
                    {
                        "id": _safe_text(task.get("id"), 40),
                        "title": _safe_text(task.get("title"), 140),
                        "status": _safe_text(task.get("status"), 40),
                        "kind": _safe_text(task.get("kind"), 60),
                        "blocked_reason": _safe_text(task.get("blocker"), 240),
                    }
                    for task in tasks[:6]
                ],
                "source": "taskboard ledger (all sessions)",
            }
        )
    return out


def _profile_summary(agent: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    profile = getattr(agent, "profile", None)
    profile_path = Path(
        getattr(profile, "_path", "")
        or resolve_state_path((cfg.get("paths") or {}).get("memory_file", "memory/mo.db"), cfg)
    )
    profile_dir = profile_path.parent / "profile"
    files = []
    if profile_dir.is_dir():
        for path in sorted(profile_dir.glob("*.md")):
            try:
                stat = path.stat()
            except OSError:
                continue
            files.append(
                {
                    "name": path.name,
                    "bytes": stat.st_size,
                    "updated_at": stat.st_mtime,
                    "update_mode": _PROFILE_FILE_UPDATE_MODES.get(path.name, "curated profile file"),
                    "source": "profile file",
                }
            )
    recent_folders = list(getattr(profile, "projects", {}).values()) if profile is not None else []
    recent_projects = sorted(recent_folders, key=lambda item: float(getattr(item, "last_opened", 0.0) or 0.0), reverse=True)[:5]
    locations = getattr(profile, "project_locations", None)
    projects = list(locations()) if callable(locations) else []
    return {
        "display_name": _safe_text(getattr(profile, "user_name", ""), 120) if profile is not None else "",
        "alias": _safe_text(getattr(profile, "user_alias", ""), 80) if profile is not None else "",
        "sessions": int(getattr(profile, "total_sessions", 0) or 0) if profile is not None else 0,
        "turns": int(getattr(profile, "total_turns", 0) or 0) if profile is not None else 0,
        "projects": len(projects),
        "project_names": [_safe_text(getattr(item, "name", ""), 80) for item in projects[:32]],
        "recent_projects_scope": "recent launch folders, not ownership declarations",
        "recent_projects": [_safe_text(getattr(item, "name", "") or Path(str(getattr(item, "path", ""))).name, 80) for item in recent_projects],
        "preferred_tools": len(list(getattr(profile, "preferred_tools", []) or [])) if profile is not None else 0,
        "important_paths": len(list(getattr(profile, "important_paths", []) or [])) if profile is not None else 0,
        "profile_files": files,
        "raw_profile_hidden": True,
        "source": "profile db and markdown index",
    }


def _learning_status(agent: Any, cfg: dict[str, Any]):
    from ..learning.status import build_learning_status

    return build_learning_status(getattr(agent, "profile", None), config=cfg)


def _work_learning_summary(agent: Any, learning_status: Any) -> dict[str, Any]:
    try:
        from ..runtime.work_learning_status import build_work_learning_status

        return build_work_learning_status(agent, learning_status=learning_status).as_dict()
    except Exception as exc:
        return {
            "available": False,
            "error": type(exc).__name__,
            "raw_content_hidden": True,
            "source": "taskboard/session counts and durable learning counts",
        }


def _graph_summary(root: Path) -> dict[str, Any]:
    try:
        from ..graph.structural_graph import graph_status

        status = graph_status(root)
    except Exception as exc:
        return {"available": False, "error": type(exc).__name__, "source": "structural graph"}
    quality = status.get("quality") if isinstance(status.get("quality"), dict) else {}
    return {
        "available": bool(status.get("available")),
        "source_kind": _safe_text(status.get("source_kind") or "none", 80),
        "nodes": int(status.get("nodes") or 0),
        "edges": int(status.get("edges") or 0),
        "communities": int(status.get("communities") or 0),
        "groups": int(status.get("groups") or status.get("communities") or 0),
        "group_strategy": _safe_text(status.get("group_strategy") or "path", 40),
        "trust": _safe_text(status.get("trust") or "unknown", 60),
        "quality": {
            key: int(quality.get(key) or 0)
            for key in ("symbol_nodes", "qualified_symbol_nodes", "method_nodes", "resolved_calls", "ambiguous_calls", "dynamic_attribute_calls", "unresolved_project_calls")
        },
        "stale": bool(status.get("stale")),
        "stale_reasons": [_safe_text(item, 80) for item in list(status.get("stale_reasons") or [])[:5]],
        "stale_files": int(status.get("stale_files") or 0),
        "stale_sample": [_safe_text(item, 160) for item in list(status.get("stale_sample") or [])[:8]],
        "fingerprint_stale": bool(status.get("fingerprint_stale")),
        "confidence_breakdown": _safe_count_map(status.get("confidence_breakdown") or {}),
        "provenance_breakdown": _safe_count_map(status.get("provenance_breakdown") or {}),
        "legacy_confidence_edges": int(status.get("legacy_confidence_edges") or 0),
        "unknown_confidence_edges": int(status.get("unknown_confidence_edges") or 0),
        "path": _safe_text(status.get("path") or "", 260),
        "code_map_path": _safe_text(str(Path(str(status.get("path") or "")).with_name("code_map.html")) if status.get("path") else "", 260),
        "source": "structural graph cache",
    }


def _runtime_summary(agent: Any, continuity: dict[str, Any]) -> dict[str, Any]:
    session = continuity.get("current_session") if isinstance(continuity.get("current_session"), dict) else {}
    heartbeat = continuity.get("heartbeat") if isinstance(continuity.get("heartbeat"), dict) else {}
    return {
        "provider": _safe_text(getattr(agent, "provider_name", ""), 80),
        "model": _safe_text(getattr(agent, "model", ""), 120),
        "session_slot": _safe_text(session.get("slot") or "", 80),
        "session_turns": int(session.get("turn_count") or 0),
        "session_messages": int(session.get("message_count") or 0),
        "heartbeat_event": _safe_text(heartbeat.get("event") or "", 80),
        "heartbeat_surface": _safe_text(heartbeat.get("surface") or "", 80),
        "source": "agent runtime and heartbeat",
    }


def _artifact_summary(graph: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    code_map = str(graph.get("code_map_path") or "")
    try:
        from ..state.attachments import attachment_summary

        desktop = attachment_summary(config)
    except Exception:
        desktop = {"total": 0, "indexed": 0, "bytes": 0, "categories": {}}
    return {
        "code_map": code_map if code_map and Path(code_map).is_file() else "",
        "dashboard_html": str(Path(resolve_state_path(DASHBOARD_HTML_PATH, config))),
        "mo_desktop": desktop,
    }


def _provenance() -> list[dict[str, str]]:
    return [
        {"area": "work", "source": "Gateway taskboard + taskboard ledger + continuity snapshot"},
        {"area": "profile", "source": "profile database + profile markdown filenames only"},
        {"area": "learning", "source": "learning sqlite counts + suggestions status counts"},
        {"area": "work_learning", "source": "direct taskboard/session counts + durable learning counts"},
        {"area": "graph", "source": "structural graph status; orientation only"},
        {"area": "runtime", "source": "current Agent fields + heartbeat ledger"},
    ]


def _safe_count_map(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    out: dict[str, int] = {}
    for key, count in value.items():
        clean_key = _safe_text(key, 80)
        if not clean_key:
            continue
        try:
            out[clean_key] = int(count or 0)
        except (TypeError, ValueError):
            out[clean_key] = 0
    return out


def _redact_snapshot(value: Any) -> Any:
    if isinstance(value, dict):
        return {_safe_key(k): _redact_snapshot(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_snapshot(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_snapshot(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value


def _safe_text(value: Any, limit: int = 200) -> str:
    text = " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())
    text = redact_sensitive_text(text)
    max_len = max(0, int(limit or 0))
    if max_len and len(text) > max_len:
        return text[: max(0, max_len - 1)].rstrip() + "…"
    return text


def _safe_key(value: Any) -> str:
    key = redact_sensitive_text(str(value or ""))
    return key if key.strip() else "redacted"


def _iso(ts: float) -> str:
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(ts)))
    except Exception:
        return ""
