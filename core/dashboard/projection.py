"""Bounded semantic projection shared by MO dashboard renderers.

The projection data is deliberately read-only and JSON-safe. It does not
discover or persist state: callers supply an already-authorized snapshot and
surface adapters delegate its declarative action identifiers to existing owners.
"""
from __future__ import annotations

import time
from math import isfinite
from typing import Any

from ..tooling.sandbox import redact_sensitive_text


DASHBOARD_PROJECTION_VERSION = "mo-dashboard-projection-v1"
PERSPECTIVES = frozenset({"user", "operations"})
SURFACES = frozenset({"terminal", "html", "desktop", "everywhere", "android"})
TONES = frozenset({"good", "attention", "neutral", "quiet"})

_MANAGEMENT_ACTIONS: tuple[tuple[str, str, str, str], ...] = (
    ("dashboard.open.work", "Work", "command", "/now"),
    ("dashboard.open.goals", "Goals", "command", "/goal status"),
    ("dashboard.open.projects", "Projects", "request", "Manage my projects"),
    ("dashboard.open.learning", "Learning", "command", "/learning pending"),
    ("dashboard.open.schedules", "Schedules", "command", "/schedule"),
    ("dashboard.open.profile", "Profile", "command", "/profile"),
    ("dashboard.open.skills", "Skills", "command", "/skills"),
    ("dashboard.open.connections", "Connections", "command", "/everywhere status"),
    ("dashboard.open.servers", "Servers", "request", "Manage my servers and connections"),
)

_FILE_REQUEST = (
    "dashboard.open.files",
    "Files & folders",
    "request",
    "Manage my files and folders",
)
_MAP_ACTION = ("dashboard.open.map", "Project map", "command", "/dashboard map")
_CHECKS_ACTION = ("dashboard.open.checks", "Project checks", "command", "/dashboard checks")

_LOCAL_ACTIONS = (
    *_MANAGEMENT_ACTIONS,
    _FILE_REQUEST,
    _MAP_ACTION,
    _CHECKS_ACTION,
)

_ACTION_SETS: dict[str, tuple[tuple[str, str, str, str], ...]] = {
    "terminal": (
        ("dashboard.view.user", "Your view", "view", "user"),
        ("dashboard.view.operations", "MO operations", "view", "operations"),
        *_LOCAL_ACTIONS,
    ),
    "html": (
        ("dashboard.view.user", "Your view", "view", "user"),
        ("dashboard.view.operations", "MO operations", "view", "operations"),
        *_LOCAL_ACTIONS,
        ("dashboard.open.profile.notes", "Edit saved notes in MO", "command", "/profile facts"),
    ),
    "desktop": (
        ("dashboard.view.overview", "Overview", "view", "overview"),
        ("dashboard.view.work", "Work", "view", "work"),
        ("dashboard.view.personal", "Personal", "view", "personal"),
        ("dashboard.view.systems", "Systems", "view", "systems"),
        *_MANAGEMENT_ACTIONS,
        ("dashboard.open.files", "Files & folders", "surface", "files"),
        _MAP_ACTION,
        _CHECKS_ACTION,
    ),
    "everywhere": (
        ("dashboard.open.chat", "Talk to MO", "destination", "chat"),
    ),
    "android": (
        *_MANAGEMENT_ACTIONS,
        ("dashboard.open.files", "Files & folders", "surface", "files"),
        _MAP_ACTION,
    ),
}


def build_dashboard_projection(
    snapshot: dict[str, Any] | None,
    *,
    perspective: str = "user",
    surface: str = "terminal",
    generated_at: float | None = None,
) -> dict[str, Any]:
    """Return a bounded renderer-neutral dashboard projection.

    ``snapshot`` may be the full private dashboard snapshot or a deliberately
    smaller Everywhere-shaped input. Missing domains degrade to zero/unknown
    instead of making a surface invent another source of truth.
    """
    snap = snapshot if isinstance(snapshot, dict) else {}
    selected_perspective = perspective if perspective in PERSPECTIVES else "user"
    selected_surface = surface if surface in SURFACES else "terminal"
    stamp = _number(generated_at, _number(snap.get("created_at"), time.time()))

    work = _mapping(snap.get("work"))
    profile = _mapping(snap.get("profile"))
    learning = _mapping(snap.get("learning"))
    work_learning = _mapping(snap.get("work_learning"))
    graph = _mapping(snap.get("graph"))
    runtime = _mapping(snap.get("runtime"))
    environment = _mapping(snap.get("environment"))
    presence = _mapping(snap.get("presence"))
    mail = _mapping(snap.get("mail"))
    life = _mapping(snap.get("life"))

    if selected_perspective == "operations":
        metrics = _operations_metrics(work, learning, work_learning, graph, runtime, presence)
        sections = _operations_sections(
            work,
            graph,
            runtime,
            environment,
            presence,
            snap,
            surface=selected_surface,
        )
    else:
        metrics = _user_metrics(work, profile, learning, work_learning, presence)
        sections = _user_sections(work, profile, learning, work_learning, presence,
                                  life if selected_surface == "desktop" else {})
        communication = []
        if selected_surface in {"desktop", "html", "terminal"}:
            communication.append(_item(
                "outlook", "Outlook.com",
                "Recent on click" if selected_surface == "html" else "Agent chat",
                "MO Connected Tab" if selected_surface == "html" else "Uses MO Connected Tab", "quiet",
            ))
        if mail and mail.get("state") != "disabled":
            mail_state = _text(mail.get("state") or "unknown", 40)
            unread = mail.get("unread")
            detail = f"{_int(unread, 1_000_000)} unread" if unread is not None else "Count unavailable"
            communication.append(_item(
                "gmail", "Gmail", mail_state.replace("_", " ").title(), detail,
                "good" if mail_state == "connected" else "quiet",
            ))
        if communication:
            sections.append(_section("communication", "Communication", communication))

    status = _status(work, graph, presence, selected_perspective)
    return {
        "version": DASHBOARD_PROJECTION_VERSION,
        "perspective": selected_perspective,
        "surface": selected_surface,
        "generated_at": stamp,
        "brand": {"kind": "mo-cube", "label": "MO"},
        "status": status,
        "metrics": metrics[:8],
        "sections": sections[:6],
        "actions": _actions(selected_surface),
        "privacy": {
            "read_only": True,
            "content_hidden": ["profile prose", "memory text", "learning text", "mail content", "life details", "credentials", "prompts"],
            "truth_owners": ["taskboard", "profile", "learning", "heartbeat", "structural graph", "mail", "life items"],
        },
        "provenance": _provenance(snap.get("provenance")),
    }


def _user_metrics(
    work: dict[str, Any],
    profile: dict[str, Any],
    learning: dict[str, Any],
    work_learning: dict[str, Any],
    presence: dict[str, Any],
) -> list[dict[str, Any]]:
    completed = _int(work.get("completed"), 1_000_000)
    total = _int(work.get("total"), 1_000_000)
    pending = _pending_learning_review(learning)
    profile_sections = _int(profile.get("sections"), 10_000)
    if not profile_sections:
        profile_sections = min(10_000, len(_sequence(profile.get("profile_files"))))
    active = _int(presence.get("active"), 10_000)
    surface_total = _int(presence.get("total"), 10_000)
    metrics = [
        _metric("open_work", "Open work", _int(work.get("open"), 1_000_000), f"{completed}/{total} complete", "attention" if _int(work.get("open"), 1_000_000) else "good", _ratio(completed, total)),
        _metric("work_state", "Work state", _status_state(work_learning), _first_text(work_learning.get("drivers"), "Direct counts"), _state_tone(work_learning.get("state"))),
        _metric("profile", "Curated profile", profile_sections, "manual + event-driven files; content hidden", "good" if profile_sections else "quiet"),
        _metric("learned_behavior", "Profile learning", _int(learning.get("profile_learning_entries"), 100_000), f"{_int(learning.get('behavior_rules'), 100_000)} active rules · {_int(learning.get('profile_facts'), 100_000)} facts", "good" if _int(learning.get("profile_learning_entries"), 100_000) else "quiet"),
        _metric("learning", "Learning review", pending, f"{_int(learning.get('confirmed_suggestions'), 100_000)} confirmed · {_int(learning.get('generated_learning_skills'), 100_000)} skills", "attention" if pending else "good"),
    ]
    if active or surface_total:
        metrics.append(_metric("surfaces", "Active surfaces", active, f"{surface_total or active} recent on this hub", "good" if active else "quiet"))
    return metrics


def _operations_metrics(
    work: dict[str, Any],
    learning: dict[str, Any],
    work_learning: dict[str, Any],
    graph: dict[str, Any],
    runtime: dict[str, Any],
    presence: dict[str, Any],
) -> list[dict[str, Any]]:
    completed = _int(work.get("completed"), 1_000_000)
    total = _int(work.get("total"), 1_000_000)
    return [
        _metric("work", "Task evidence", f"{completed}/{total}", f"{_int(work.get('open'), 1_000_000)} open", "attention" if _int(work.get("open"), 1_000_000) else "good", _ratio(completed, total)),
        _metric("graph", "Graph nodes", _int(graph.get("nodes"), 100_000_000), _text(graph.get("trust") or "not built", 80), "attention" if graph.get("stale") else ("good" if graph.get("available") else "quiet")),
        _metric("work_state", "Work state", _status_state(work_learning), f"{_int(work_learning.get('pending_review'), 100_000)} pending review", _state_tone(work_learning.get("state"))),
        _metric("runtime", "Session turns", _int(runtime.get("session_turns"), 10_000_000), _text(runtime.get("heartbeat_event") or "runtime", 100), "neutral"),
        _metric("learning", "Memory turns", _int(learning.get("memory_turns"), 10_000_000), "durable learning index", "neutral"),
        _metric("surfaces", "Active surfaces", _int(presence.get("active"), 10_000), f"{_int(presence.get('total'), 10_000)} recent", "good" if _int(presence.get("active"), 10_000) else "quiet"),
    ]


def _user_sections(
    work: dict[str, Any],
    profile: dict[str, Any],
    learning: dict[str, Any],
    work_learning: dict[str, Any],
    presence: dict[str, Any],
    life: dict[str, Any],
) -> list[dict[str, Any]]:
    latest = _mapping(work.get("live_taskboard")) or _mapping(work.get("latest_taskboard"))
    work_items = [
        _item("current", "Current work", latest.get("title") or work.get("verdict") or "No open runtime work visible", f"{_int(latest.get('open'), 10_000)} open · {_int(latest.get('completed'), 10_000)} complete", "attention" if _int(latest.get("open"), 10_000) else "good"),
    ]
    for index, board in enumerate(_sequence(work.get("recent_boards"))[:3]):
        row = _mapping(board)
        work_items.append(_item(f"recent_{index}", row.get("title") or "Recent work", row.get("state") or "", f"{_int(row.get('open'), 10_000)}/{_int(row.get('total'), 10_000)} open", "neutral"))

    pending = _pending_learning_review(learning)
    personal_items = [
        _item("profile", "Curated profile", f"{_profile_sections(profile)} files", "Manual + event-driven · manage through Profile", "good" if _profile_sections(profile) else "quiet"),
        _item("knowledge", "Durable knowledge", f"{_int(learning.get('profile_facts'), 100_000)} facts · {_int(learning.get('operator_terms'), 100_000)} terms", "Validated capture; private content stays hidden", "good" if _int(learning.get("profile_facts"), 100_000) or _int(learning.get("operator_terms"), 100_000) else "quiet"),
        _item("behavior", "Learned behavior", f"{_int(learning.get('profile_learning_entries'), 100_000)} events · {_int(learning.get('behavior_rules'), 100_000)} rules", "Explicit corrections and approved learning", "good" if _int(learning.get("profile_learning_entries"), 100_000) else "quiet"),
        _item("learning", "Learning review", f"{pending} pending", f"{_int(learning.get('confirmed_suggestions'), 100_000)} confirmed · {_int(learning.get('generated_learning_skills'), 100_000)} skills · {_int(learning.get('workflow_candidates'), 100_000)} workflow candidates", "attention" if pending else "good"),
        _item("work_state", "Work and learning state", _status_state(work_learning), _first_text(work_learning.get("drivers"), "Direct counts"), _state_tone(work_learning.get("state"))),
    ]
    open_life = _int(life.get("open"), 1_000) if life.get("available") else 0
    if open_life:
        overdue = _int(life.get("overdue"), 1_000)
        soon = _int(life.get("due_soon"), 1_000)
        personal_items.insert(0, _item("life", "Life", f"{open_life} open",
            f"{overdue} overdue · {soon} due within 7 days",
            "attention" if overdue or soon else "neutral"))
    sections = [_section("work", "Your work", work_items), _section("personal", "MO for you", personal_items)]
    if presence:
        surface_items = []
        for index, surface in enumerate(_sequence(presence.get("items"))[:8]):
            row = _mapping(surface)
            surface_items.append(_item(f"surface_{index}", row.get("label") or row.get("surface") or "MO surface", row.get("state") or "idle", row.get("detail") or "", "good" if row.get("state") == "active" else "quiet"))
        if surface_items:
            sections.append(_section("surfaces", "MO surfaces", surface_items))
    return sections


def _operations_sections(
    work: dict[str, Any],
    graph: dict[str, Any],
    runtime: dict[str, Any],
    environment: dict[str, Any],
    presence: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    surface: str,
) -> list[dict[str, Any]]:
    if surface in {"everywhere", "android"}:
        runtime_items = [
            _item(
                "presence",
                "Hub presence",
                f"{_int(presence.get('active'), 10_000)} active",
                f"{_int(presence.get('total'), 10_000)} recent surfaces",
                "good" if _int(presence.get("active"), 10_000) else "quiet",
            ),
            _item(
                "turns",
                "Current session",
                f"{_int(runtime.get('session_turns'), 10_000_000)} turns",
                "Counts only",
                "neutral",
            ),
        ]
    else:
        runtime_items = [
            _item("provider", "Provider", runtime.get("provider") or "Not reported", runtime.get("model") or "", "neutral"),
            _item("session", "Session", runtime.get("session_slot") or "Not reported", f"{_int(runtime.get('session_messages'), 10_000_000)} messages", "neutral"),
            _item("system", "System", environment.get("os") or "Unknown", environment.get("python") and f"Python {_text(environment.get('python'), 40)}" or "", "neutral"),
        ]
    graph_items = [
        _item("structure", "Structural graph", "Available" if graph.get("available") else "Not built", f"{_int(graph.get('nodes'), 100_000_000)} nodes · {_int(graph.get('edges'), 100_000_000)} edges", "attention" if graph.get("stale") else ("good" if graph.get("available") else "quiet")),
        _item("trust", "Graph trust", graph.get("trust") or "Unknown", f"{_int(graph.get('stale_files'), 1_000_000)} stale files", "attention" if graph.get("stale") else "neutral"),
    ]
    evidence_items = [
        _item("taskboard", "Taskboard evidence", work.get("verdict") or "No work reported", f"{_int(work.get('completed'), 1_000_000)}/{_int(work.get('total'), 1_000_000)} complete", "attention" if _int(work.get("open"), 1_000_000) else "good"),
        _item("provenance", "Projection", "Read-only synthesis", f"{len(_sequence(snapshot.get('provenance')))} source notes", "good"),
    ]
    lsp = _mapping(snapshot.get("lsp"))
    if lsp:
        evidence_items.append(_item("lsp", "Project LSP", lsp.get("state") or "unavailable", "Configuration is not verification; open Project checks for evidence", "neutral"))
    sections = [
        _section("runtime", "Runtime", runtime_items),
        _section("graph", "Understanding systems", graph_items),
        _section("evidence", "Evidence", evidence_items),
    ]
    systemcare = _mapping(snapshot.get("systemcare"))
    if systemcare.get("available"):
        state = _text(systemcare.get("state") or "not_scanned", 32)
        tone = "attention" if state in {"attention", "review", "unavailable"} else ("good" if state == "ready" else "neutral")
        sections.append(_section("systemcare", "SystemCare", [
            _item(
                "status",
                "SystemCare",
                systemcare.get("label") or "Calibrate & scan",
                "Read-only status · open the exact owner for actions",
                tone,
            )
        ]))
    return sections


def _status(work: dict[str, Any], graph: dict[str, Any], presence: dict[str, Any], perspective: str) -> dict[str, str]:
    open_work = _int(work.get("open"), 1_000_000)
    active = _int(presence.get("active"), 10_000)
    if perspective == "operations" and graph.get("stale"):
        return {"label": "Attention", "detail": "Structural orientation is stale", "tone": "attention"}
    if open_work:
        return {"label": "In progress", "detail": f"{open_work} open work item{'s' if open_work != 1 else ''}", "tone": "attention"}
    if active:
        return {"label": "Active", "detail": f"{active} active MO surface{'s' if active != 1 else ''}", "tone": "good"}
    if not work or work.get("available") is False:
        return {"label": "Unknown", "detail": "Current work is unavailable", "tone": "quiet"}
    return {"label": "Idle", "detail": "No open runtime work is visible; not a health check", "tone": "neutral"}


def _actions(surface: str) -> list[dict[str, str]]:
    owners = {
        "view": "dashboard",
        "command": "agent-slash",
        "request": "gateway-turn",
        "surface": "mo-files",
        "destination": "everywhere",
    }
    actions: list[dict[str, str]] = []
    for action_id, label, kind, target in _ACTION_SETS.get(surface, _ACTION_SETS["terminal"]):
        actions.append({
            "id": action_id,
            "label": label,
            "kind": kind,
            "target": target,
            "owner": owners.get(kind, "dashboard"),
            "confirmation": "none" if kind == "view" else "owner",
        })
    return actions[:17]


def _metric(identifier: str, label: str, value: Any, detail: Any, tone: str, meter: float | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"id": _text(identifier, 48), "label": _text(label, 60), "value": _display(value, 60), "detail": _text(detail, 140), "tone": tone if tone in TONES else "neutral"}
    if meter is not None:
        item["meter"] = max(0.0, min(1.0, float(meter)))
    return item


def _section(identifier: str, title: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"id": _text(identifier, 48), "title": _text(title, 80), "items": items[:8]}


def _item(identifier: str, label: Any, value: Any, detail: Any, tone: str) -> dict[str, str]:
    return {"id": _text(identifier, 48), "label": _text(label, 80), "value": _display(value, 140), "detail": _text(detail, 180), "tone": tone if tone in TONES else "neutral"}


def _provenance(value: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in _sequence(value)[:12]:
        item = _mapping(row)
        if not item:
            continue
        out.append({"area": _text(item.get("area"), 60), "source": _text(item.get("source"), 180)})
    return out


def _profile_sections(profile: dict[str, Any]) -> int:
    return _int(profile.get("sections"), 10_000) or min(10_000, len(_sequence(profile.get("profile_files"))))


def _pending_learning_review(learning: dict[str, Any]) -> int:
    suggestion_counts = _mapping(learning.get("suggestions"))
    pending = _int(suggestion_counts.get("pending"), 100_000)
    pending += _int(suggestion_counts.get("suggested"), 100_000)
    pending = max(pending, _int(learning.get("pending_suggestions"), 100_000))
    return pending + _int(learning.get("workflow_candidates"), 100_000)


def _first_text(value: Any, fallback: str) -> str:
    rows = _sequence(value)
    return _text(rows[0], 140) if rows else fallback


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _sequence(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _int(value: Any, maximum: int) -> int:
    try:
        return max(0, min(maximum, int(value or 0)))
    except (OverflowError, TypeError, ValueError):
        return 0


def _number(value: Any, fallback: float) -> float:
    try:
        number = float(value)
        return number if isfinite(number) and number >= 0 else fallback
    except (OverflowError, TypeError, ValueError):
        return fallback


def _ratio(done: int, total: int) -> float:
    return 0.0 if total <= 0 else max(0.0, min(1.0, done / total))


def _status_state(value: dict[str, Any]) -> str:
    state = str(value.get("state") or "idle").strip().lower()
    return state if state in {"idle", "active", "attention", "blocked", "complete"} else "idle"


def _state_tone(value: Any) -> str:
    state = str(value or "idle").strip().lower()
    if state == "blocked":
        return "attention"
    if state in {"attention", "active"}:
        return "attention"
    return "good" if state == "complete" else "quiet"


def _display(value: Any, limit: int) -> str:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return _text(value, limit)


def _text(value: Any, limit: int) -> str:
    raw = "" if value is None else value
    text = " ".join(str(raw).replace("\r", " ").replace("\n", " ").split())
    text = redact_sensitive_text(text)
    if len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text
