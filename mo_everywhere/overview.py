"""Privacy-bounded mobile overview derived from MO's durable runtime truth."""
from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from core.dashboard.projection import _mapping
from core.runtime.heartbeat import read_recent_heartbeats
from core.runtime.surface_identity import normalize_runtime_surface
from core.state.paths import HEARTBEAT_LEDGER_PATH, resolve_state_path

from .cube_protocol import CubeStream, compose_cube


_DEFAULT_CUBE_STREAM = CubeStream()


def build_overview(
    config: dict[str, Any] | None = None,
    *,
    now: float | None = None,
    principal: Any = None,
    job: Any = None,
    transient: Any = None,
    cube_stream: CubeStream | None = None,
    locally_disabled: bool = False,
    dashboard_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return overview data without transcript, cwd, provider, or profile contents."""
    config = config or {}
    current = float(now if now is not None else time.time())
    block = config.get("consistent_everywhere")
    block = block if isinstance(block, dict) else {}
    api = block.get("api") if isinstance(block.get("api"), dict) else {}
    active_after = max(15.0, float(api.get("heartbeat_active_seconds", 150) or 150))
    heartbeat_path = Path(resolve_state_path(HEARTBEAT_LEDGER_PATH, config))
    rows = read_recent_heartbeats(limit=100, path=heartbeat_path)
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = (str(row.get("instance_id") or "unknown")[:80], _public_surface(row.get("surface")))
        latest[key] = row
    surfaces = [_public_heartbeat(row, current=current, active_after=active_after) for row in latest.values()]
    surfaces.sort(key=lambda item: float(item.get("updated_at") or 0), reverse=True)
    active = sum(1 for item in surfaces if item["state"] == "active")
    status = "active" if active else "idle"
    visual = _visual_state("active" if active else "disconnected", updated_at=current)
    transfer_block = config.get("file_transfer")
    file_transfer_enabled = (
        isinstance(transfer_block, dict)
        and transfer_block.get("enabled") is True
    )
    capabilities = {
        "overview": "view",
        "turn": "control",
        "live_control": "control+remote_control",
    }
    if file_transfer_enabled:
        capabilities["file_transfer"] = "control+file_transfer"
    overview = {
        "version": 1,
        "generated_at": current,
        "presence_scope": "this_hub",
        "status": status,
        "active_agents": active,
        "agents": surfaces[:24],
        "profile": _profile_summary(config),
        "visual": visual,
        "skin": _skin_tokens(config),
        "capabilities": capabilities,
    }
    # Import on request so the Everywhere startup path does not pull the
    # dashboard renderers into the hub process before the overview is used.
    from core.dashboard.projection import build_dashboard_projection

    remote_dashboard = _remote_dashboard_snapshot(
        dashboard_snapshot,
        profile=overview["profile"],
        surfaces=surfaces,
        generated_at=current,
    )
    overview["dashboard"] = build_dashboard_projection(
        remote_dashboard,
        perspective="user",
        surface="android",
        generated_at=current,
    )
    if str(getattr(principal, "capability", "view") or "view") == "control":
        overview["dashboard_operations"] = build_dashboard_projection(
            remote_dashboard,
            perspective="operations",
            surface="android",
            generated_at=current,
        )
    overview["cube"] = compose_cube(
        stream=cube_stream or _DEFAULT_CUBE_STREAM,
        generated_at=current,
        capability=str(getattr(principal, "capability", "view") or "view"),
        scopes=getattr(principal, "scopes", ()),
        visual=visual,
        job=job,
        transient=transient,
        locally_disabled=locally_disabled,
        file_transfer_enabled=file_transfer_enabled,
        stale_after_seconds=_cube_stale_after(api),
    )
    return overview


def _public_heartbeat(row: dict[str, Any], *, current: float, active_after: float) -> dict[str, Any]:
    created = float(row.get("created_at") or 0.0)
    age = max(0.0, current - created) if created else 10**9
    task = row.get("taskboard") if isinstance(row.get("taskboard"), dict) else {}
    goal = row.get("goal") if isinstance(row.get("goal"), list) else []
    return {
        "surface": _public_surface(row.get("surface")),
        "state": "active" if age <= active_after else "idle",
        "updated_at": created,
        "turn_count": _bounded_int(row.get("turn_count"), 1_000_000),
        "task": {
            "active": bool(task.get("active")),
            "state": str(task.get("state") or "")[:32],
            "completed": _bounded_int(task.get("completed"), 10_000),
            "total": _bounded_int(task.get("total"), 10_000),
        },
        "goals_active": sum(1 for item in goal if isinstance(item, dict) and str(item.get("status") or "") == "active"),
    }


def _profile_summary(config: dict[str, Any]) -> dict[str, Any]:
    root = Path(resolve_state_path("memory/profile", config))
    try:
        sections = sum(1 for path in root.glob("*.md") if path.is_file() and not path.is_symlink())
    except OSError:
        sections = 0
    return {"configured": sections > 0, "sections": sections}


def _remote_dashboard_snapshot(
    snapshot: dict[str, Any] | None,
    *,
    profile: dict[str, Any],
    surfaces: list[dict[str, Any]],
    generated_at: float,
) -> dict[str, Any]:
    """Return the counts-only dashboard input allowed across the hub boundary.

    The local snapshot may contain redacted task titles and runtime identifiers
    suitable for private Desktop/terminal views. Everywhere deliberately keeps
    only aggregate owner counts and fixed labels; it never forwards those local
    strings, paths, profile prose, provider/model details, or artifact locations.
    """
    source = _mapping(snapshot)
    work = _mapping(source.get("work"))
    learning = _mapping(source.get("learning"))
    work_learning = _mapping(source.get("work_learning"))
    graph = _mapping(source.get("graph"))
    runtime = _mapping(source.get("runtime"))
    mail = _mapping(source.get("mail"))
    mail_state = str(mail.get("state") or "").strip().lower()
    if mail_state not in {"disabled", "secure_storage_unavailable", "client_missing", "reconnect_required", "disconnected", "connected", "sync_unknown"}:
        mail_state = "disabled"
    public_work = _remote_work_summary(work, surfaces)
    presence = {
        "active": sum(1 for item in surfaces if item.get("state") == "active"),
        "total": len(surfaces),
        "items": [
            {
                "surface": item["surface"],
                "label": _surface_label(item["surface"]),
                "state": item["state"],
                "detail": _surface_detail(item),
            }
            for item in surfaces[:8]
        ],
    }
    return {
        "created_at": generated_at,
        "work": public_work,
        "profile": {
            "configured": bool(profile.get("configured")),
            "sections": _bounded_int(profile.get("sections"), 10_000),
        },
        "learning": {
            "memory_turns": _bounded_int(learning.get("memory_turns"), 10_000_000),
            "profile_learning_entries": _bounded_int(learning.get("profile_learning_entries"), 100_000),
            "behavior_rules": _bounded_int(learning.get("behavior_rules"), 100_000),
            "profile_facts": _bounded_int(learning.get("profile_facts"), 100_000),
            "operator_terms": _bounded_int(learning.get("operator_terms"), 100_000),
            "pending_suggestions": _bounded_int(learning.get("pending_suggestions"), 100_000),
            "confirmed_suggestions": _bounded_int(learning.get("confirmed_suggestions"), 100_000),
            "generated_learning_skills": _bounded_int(learning.get("generated_learning_skills"), 100_000),
            "workflow_candidates": _bounded_int(learning.get("workflow_candidates"), 100_000),
            "suggestions": _bounded_count_map(learning.get("suggestions"), maximum=100_000),
        },
        "work_learning": {
            "state": _work_learning_state(work_learning.get("state")),
            "pending_review": _bounded_int(work_learning.get("pending_review"), 100_000),
            "task_open": _bounded_int(work_learning.get("task_open"), 1_000_000),
            "task_blocked": _bounded_int(work_learning.get("task_blocked"), 1_000_000),
            "boundary_findings": _bounded_int(work_learning.get("boundary_findings"), 100_000),
            "drivers": [_remote_work_driver(public_work)],
        },
        "graph": {
            "available": bool(graph.get("available")),
            "nodes": _bounded_int(graph.get("nodes"), 100_000_000),
            "edges": _bounded_int(graph.get("edges"), 100_000_000),
            "trust": _graph_trust(graph.get("trust")),
            "stale": bool(graph.get("stale")),
            "stale_files": _bounded_int(graph.get("stale_files"), 1_000_000),
        },
        "runtime": {
            "session_turns": _bounded_int(runtime.get("session_turns"), 10_000_000),
        },
        "mail": {
            "state": mail_state,
            "unread": _bounded_int(mail.get("unread"), 1_000_000) if mail_state == "connected" else None,
        },
        "presence": presence,
        "provenance": [
            {"area": "work", "source": "bounded taskboard and heartbeat counts"},
            {"area": "personalization", "source": "profile and learning counts only"},
            {"area": "systems", "source": "bounded graph and runtime counts"},
        ],
    }


def _remote_work_summary(work: dict[str, Any], surfaces: list[dict[str, Any]]) -> dict[str, Any]:
    total = _bounded_int(work.get("total"), 1_000_000)
    completed = _bounded_int(work.get("completed"), 1_000_000)
    open_count = _bounded_int(work.get("open"), 1_000_000)
    if not (total or completed or open_count):
        current = next(
            (
                item.get("task")
                for item in surfaces
                if isinstance(item.get("task"), dict)
                and (
                    item["task"].get("active")
                    or _bounded_int(item["task"].get("total"), 10_000) > 0
                )
            ),
            {},
        )
        current = current if isinstance(current, dict) else {}
        total = _bounded_int(current.get("total"), 10_000)
        completed = min(total, _bounded_int(current.get("completed"), 10_000))
        open_count = max(0, total - completed) if current.get("active") else 0
    completed = min(completed, total) if total else completed
    current_board = {
        "state": "active" if open_count else "ready",
        "title": "Current MO work" if (open_count or total) else "",
        "open": open_count,
        "completed": completed,
        "total": total,
    }
    return {
        "open": open_count,
        "completed": completed,
        "total": total,
        "verdict": "open work visible" if open_count else "no open runtime work visible",
        "live_taskboard": current_board,
    }


def _remote_work_driver(work: dict[str, Any]) -> str:
    total = _bounded_int(work.get("total"), 1_000_000)
    completed = _bounded_int(work.get("completed"), 1_000_000)
    open_count = _bounded_int(work.get("open"), 1_000_000)
    if total:
        return f"tasks {completed}/{total} complete, {open_count} open"
    return "no active taskboard"


def _bounded_count_map(value: Any, *, maximum: int) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    allowed = {"suggested", "pending", "confirmed", "dismissed", "expired", "superseded", "other"}
    return {
        str(key): _bounded_int(count, maximum)
        for key, count in value.items()
        if str(key) in allowed
    }


def _work_learning_state(value: Any) -> str:
    state = str(value or "").strip().lower()
    return state if state in {"idle", "active", "attention", "blocked", "complete"} else "idle"


def _graph_trust(value: Any) -> str:
    trust = str(value or "").strip().lower()
    return trust if trust in {"fresh", "fresh_orientation", "stale", "unknown"} else "unknown"


def _visual_state(phase: str, *, updated_at: float) -> dict[str, Any]:
    """Reuse Desktop's neutral semantic state shape; never copy renderer colours."""
    try:
        from mo_desktop.mcp_visuals import visual_state

        return asdict(visual_state("mo-agent", phase, capability="view", updated_at=updated_at))
    except Exception:
        return {
            "name": "mo-agent", "phase": phase, "capability": "view",
            "label": "active" if phase == "active" else "idle", "detail": "",
            "token": "brand" if phase == "active" else "neutral",
            "formation": "cluster", "event": "", "updated_at": updated_at,
        }


def _skin_tokens(config: dict[str, Any]) -> dict[str, str]:
    """Expose only renderer-neutral skin tokens used by native clients."""
    from interface.theming import registered_skins, skin_to_everywhere_tokens

    try:
        selected = Path(resolve_state_path("skin", config)).read_text(encoding="utf-8").strip()
    except OSError:
        selected = ""
    skins = registered_skins()
    name = selected if selected in skins else "default"
    return skin_to_everywhere_tokens(skins[name], name=name)


def _bounded_int(value: Any, maximum: int) -> int:
    try:
        return max(0, min(maximum, int(value or 0)))
    except (TypeError, ValueError):
        return 0


def _cube_stale_after(api: dict[str, Any]) -> int:
    try:
        return max(5, min(60, int(api.get("cube_stale_after_seconds", 15) or 15)))
    except (TypeError, ValueError):
        return 15


def _public_surface(value: Any) -> str:
    """Map runtime-internal or custom heartbeat labels to a bounded public set."""
    surface = normalize_runtime_surface(str(value or ""))
    aliases = {
        "prt_maintainer": "maintainer",
        "local": "maintainer",
    }
    surface = aliases.get(surface, surface)
    return surface if surface in PUBLIC_HEARTBEAT_SURFACES else "other"


def _surface_label(value: Any) -> str:
    return {
        "terminal": "MO Terminal",
        "telegram": "Telegram",
        "server": "Hub service",
        "api": "Phone/Web",
        "mo_desktop": "MO Desktop",
        "companion": "MO Desktop",
        "scheduler": "Scheduler",
        "cron": "Scheduler",
        "heartbeat": "Hub service",
        "github": "GitHub",
        "maintainer": "Maintainer",
        "other": "Other MO surface",
    }.get(str(value or ""), "Other MO surface")


def _surface_detail(item: dict[str, Any]) -> str:
    task = item.get("task") if isinstance(item.get("task"), dict) else {}
    if task.get("active"):
        return f"Task {_bounded_int(task.get('completed'), 10_000)}/{_bounded_int(task.get('total'), 10_000)}"
    return f"{_bounded_int(item.get('turn_count'), 1_000_000)} turns"


PUBLIC_HEARTBEAT_SURFACES = frozenset({
    "terminal", "telegram", "server", "api", "mo_desktop", "companion",
    "scheduler", "cron", "heartbeat", "github", "maintainer", "other",
})
