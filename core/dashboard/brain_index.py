"""Brain-index — MO's Memory / Profile / Learning / Tasks as reachable nodes.

Turns the dashboard snapshot into a compact set of "brain layer" nodes (one hub
per cognition surface) with stat rows, a drill-down command, and a code-affinity
hint, plus a few intra-brain edges. The unified map (and any future view) merges
these with the structural code graph so the operator can reach — and click into —
MO's memory/profile/learning/tasks, not just its code.

Boundary (the one that must not slip): this consumes ONLY the already-sanitized
dashboard snapshot (``build_dashboard_snapshot`` -> ``_redact_snapshot``). It never
re-reads raw profile prose, memory text, secrets, or credential values, and it
surfaces counts/labels only — never content. Content stays behind the drill-down
command. So no raw/secret material can enter the map through this layer.
"""
from __future__ import annotations

from typing import Any

from .snapshot import build_dashboard_snapshot

BRAIN_LAYER = "brain"


def build_brain_index(snapshot: dict[str, Any] | None = None, *, agent: Any = None) -> dict[str, Any]:
    """Return ``{"layer","nodes","edges"}`` for MO's cognition surfaces.

    Pass a pre-built (redacted) snapshot to avoid a second gather; otherwise one is
    built from ``agent``. Stats are drawn from snapshot counts only.
    """
    snap = snapshot if isinstance(snapshot, dict) else build_dashboard_snapshot(agent)
    work = snap.get("work") if isinstance(snap.get("work"), dict) else {}
    profile = snap.get("profile") if isinstance(snap.get("profile"), dict) else {}
    learning = snap.get("learning") if isinstance(snap.get("learning"), dict) else {}
    artifacts = snap.get("artifacts") if isinstance(snap.get("artifacts"), dict) else {}
    desktop = artifacts.get("mo_desktop") if isinstance(artifacts.get("mo_desktop"), dict) else {}
    desktop_categories = desktop.get("categories") if isinstance(desktop.get("categories"), dict) else {}
    suggestions = learning.get("suggestions") if isinstance(learning.get("suggestions"), dict) else {}
    sug_total = sum(int(v or 0) for v in suggestions.values())

    nodes = [
        _node("brain:memory", "Memory", drill="/dashboard map", affinity="core/learning", stats={
            "episodic turns": int(learning.get("memory_turns") or 0),
            "fts5": "yes" if learning.get("fts5") else "no",
            "recall": str(learning.get("recall_mode") or ("bm25" if learning.get("fts5") else "substring fallback")),
        }),
        _node("brain:profile", "Profile", drill="/profile", affinity="core/profile", stats={
            "sessions": int(profile.get("sessions") or 0),
            "turns": int(profile.get("turns") or 0),
            "projects": int(profile.get("projects") or 0),
            "profile files": len(profile.get("profile_files") or []),
        }),
        _node("brain:learning", "Learning", drill="/learning", affinity="core/learning", stats={
            "suggestions": sug_total,
            "confirmed": int(suggestions.get("confirmed") or 0),
            "pending": int(suggestions.get("pending") or 0),
            "workflow candidates": int(learning.get("workflow_candidates") or 0),
        }),
        _node("brain:tasks", "Tasks", drill="/now", affinity="core/tasking", stats={
            "open": int(work.get("open") or 0),
            "completed": int(work.get("completed") or 0),
            "recent boards": len(work.get("recent_boards") or []),
        }),
        _node("brain:desktop", "Desktop", drill="/desktop", affinity="mo_desktop", stats={
            "attachments": int(desktop.get("total") or 0),
            "indexed": int(desktop.get("indexed") or 0),
            "gallery": int(desktop_categories.get("gallery") or 0),
        }),
    ]
    edges = [
        {"source": "brain:memory", "target": "brain:learning", "relation": "feeds"},
        {"source": "brain:profile", "target": "brain:learning", "relation": "shapes"},
        {"source": "brain:tasks", "target": "brain:memory", "relation": "records"},
        {"source": "brain:desktop", "target": "brain:memory", "relation": "feeds"},
    ]
    return {"layer": BRAIN_LAYER, "nodes": nodes, "edges": edges}


def _node(node_id: str, label: str, *, drill: str, affinity: str, stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node_id,
        "label": label,
        "group": label,          # each brain surface is its own cluster
        "layer": BRAIN_LAYER,
        "type": "brain",
        "source_file": "",        # synthetic: no file backing (keeps file-only overlays clean)
        "drill": drill,           # command the operator runs to open the real surface
        "affinity": affinity,     # code cluster this surface sits near in the map
        "stats": dict(stats),     # counts/labels only — never content
    }
