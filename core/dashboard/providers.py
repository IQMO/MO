"""Core dashboard providers — the user's OWN view (a rich profile home, not a code map).

MO organizes richly for code (the 7MB code map) but the user's own surface was a thin metrics
strip. These provider sections turn the read-only snapshot into a "your own view": who you are,
your curated profile, your projects, what MO is learning, and your work/memory pulse.
Registered into ``core.dashboard.registry`` for the generated terminal/HTML detail view. The
cross-surface summary contract is owned separately by ``core.dashboard.projection``; Desktop's
compact renderer consumes that projection rather than collecting this registry directly.

Providers are closures over ONE snapshot so a section never triggers its own expensive read.
Each returns a section or None (empty sections are skipped by ``collect``).
"""
from __future__ import annotations

import time
from typing import Any, Callable

from .registry import DashboardRow, DashboardSection, register


def _n(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _ago(ts: Any) -> str:
    try:
        delta = time.time() - float(ts or 0)
    except (TypeError, ValueError):
        return ""
    if delta < 0 or not ts:
        return ""
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def _kb(nbytes: Any) -> str:
    n = _n(nbytes)
    return f"{n / 1024:.1f} KB" if n >= 1024 else f"{n} B"


def _you_section(snap: dict) -> DashboardSection | None:
    profile = snap.get("profile") if isinstance(snap.get("profile"), dict) else {}
    env = snap.get("environment") if isinstance(snap.get("environment"), dict) else {}
    name = str(profile.get("display_name") or profile.get("alias") or "Operator").strip() or "Operator"
    rows = [DashboardRow(name, icon="◐", sub="your local profile")]
    rows.append(DashboardRow(
        f"{_n(profile.get('sessions'))} sessions · {_n(profile.get('turns'))} turns", icon="◇"))
    prefs = _n(profile.get("preferred_tools"))
    paths = _n(profile.get("important_paths"))
    if prefs or paths:
        rows.append(DashboardRow(f"{prefs} preferred tools · {paths} important paths", icon="✦"))
    surface = str(env.get("surface") or "terminal").strip()
    if surface:
        rows.append(DashboardRow(f"surface: {surface}", icon="·", dim=True))
    return DashboardSection("You", tuple(rows), order=10)


def _knowledge_section(snap: dict) -> DashboardSection | None:
    """Index the curated profile files without equating template age with knowledge."""
    profile = snap.get("profile") if isinstance(snap.get("profile"), dict) else {}
    files = [f for f in list(profile.get("profile_files") or []) if isinstance(f, dict)]
    if not files:
        return DashboardSection(
            "Curated profile files",
            (DashboardRow("Profile files are not initialized", icon="○", sub="use /profile to create or edit them", dim=True),),
            order=20,
        )
    rows = []
    for f in sorted(files, key=lambda x: str(x.get("name") or "")):
        name = str(f.get("name") or "").strip()
        if not name:
            continue
        age = _ago(f.get("updated_at"))
        meta = " · ".join(item for item in (age, _kb(f.get("bytes"))) if item)
        rows.append(DashboardRow(
            name,
            icon="▪",
            meta=meta,
            sub=str(f.get("update_mode") or "curated profile file").strip(),
        ))
    return DashboardSection("Curated profile files", tuple(rows), order=20) if rows else None


def _projects_section(snap: dict) -> DashboardSection | None:
    profile = snap.get("profile") if isinstance(snap.get("profile"), dict) else {}
    count = _n(profile.get("projects"))
    recent = [str(p).strip() for p in list(profile.get("recent_projects") or []) if str(p).strip()]
    if not count and not recent:
        return None
    rows = [DashboardRow(f"{count} declared project{'' if count == 1 else 's'}", icon="◈")]
    for name in list(profile.get("project_names") or [])[:5]:
        rows.append(DashboardRow(str(name), icon="▹"))
    for name in recent[:5]:
        rows.append(DashboardRow(name, icon="▹", sub="recent launch folder, not a project declaration"))
    return DashboardSection("Your projects", tuple(rows), order=30)


def _learning_section(snap: dict) -> DashboardSection | None:
    learning = snap.get("learning") if isinstance(snap.get("learning"), dict) else {}
    rows = []
    turns = _n(learning.get("memory_turns"))
    if learning.get("memory_inspection") == "unavailable":
        rows.append(DashboardRow("Memory index unavailable", icon="!", sub="counts are not being reported as empty"))
    else:
        rows.append(DashboardRow(f"{turns} memory turns indexed", icon="◇"))
    rows.append(DashboardRow(
        f"{_n(learning.get('profile_facts'))} facts · {_n(learning.get('operator_terms'))} terms",
        icon="◆",
        sub="durable knowledge captured by its existing owners",
    ))
    rows.append(DashboardRow(
        f"{_n(learning.get('profile_learning_entries'))} learning events · {_n(learning.get('behavior_rules'))} active profile rules",
        icon="✓",
        sub="accepted profile learning and its compact active projection",
    ))
    rows.append(DashboardRow(
        f"{_n(learning.get('pending_suggestions')) + _n(learning.get('workflow_candidates'))} pending · {_n(learning.get('confirmed_suggestions'))} confirmed suggestions · {_n(learning.get('generated_learning_skills'))} skills",
        icon="◈",
        sub=(
            f"{_n(learning.get('workflow_candidates'))} workflow candidates await approval · "
            f"{_n(learning.get('promoted_workflows'))} workflow packs active"
        ),
    ))
    return DashboardSection("Learning about you", tuple(rows), order=40) if rows else None


def _work_section(snap: dict) -> DashboardSection | None:
    work = snap.get("work") if isinstance(snap.get("work"), dict) else {}
    graph = snap.get("graph") if isinstance(snap.get("graph"), dict) else {}
    rows = []
    open_count = _n(work.get("open"))
    rows.append(DashboardRow(
        f"{open_count} open" + (f" · {_n(work.get('completed'))} done" if _n(work.get("completed")) else ""),
        icon="◱" if open_count else "◰", sub=str(work.get("verdict") or "").strip() or None))
    if _n(graph.get("nodes")):
        meta = "stale" if graph.get("stale") else ""
        rows.append(DashboardRow(f"{_n(graph.get('nodes'))} code graph nodes", icon="◆", meta=meta))
    return DashboardSection("Work & memory", tuple(rows), order=50)


def register_core_dashboard_defaults(snapshot: dict | Callable[[], dict]) -> None:
    """Register rich sections from either a fixed or refreshable snapshot."""
    def current() -> dict:
        value = snapshot() if callable(snapshot) else snapshot
        return value if isinstance(value, dict) else {}

    register(lambda: _you_section(current()), order=10, key="you")
    register(lambda: _knowledge_section(current()), order=20, key="knowledge")
    register(lambda: _projects_section(current()), order=30, key="projects")
    register(lambda: _learning_section(current()), order=40, key="learning")
    register(lambda: _work_section(current()), order=50, key="work")
    register(lambda: _mail_section(current()), order=60, key="mail")


def _mail_section(snap: dict) -> DashboardSection | None:
    mail = snap.get("mail") if isinstance(snap.get("mail"), dict) else {}
    rows = [DashboardRow("Outlook.com", icon="O", sub="Ask MO in chat · signed-in Connected Tab required", meta="chat")]
    if mail and mail.get("state") != "disabled":
        state = str(mail.get("state") or "unknown").replace("_", " ")
        count = mail.get("unread")
        sub = f"{_n(count)} unread" if count is not None else "count unavailable"
        rows.append(DashboardRow("Gmail", icon="G", sub=sub, meta=state))
    return DashboardSection("Communication", tuple(rows), order=60)
