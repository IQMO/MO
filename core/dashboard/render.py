"""Terminal and HTML adapters for the bounded MO dashboard projection."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..state.paths import DASHBOARD_HTML_PATH, resolve_state_path
from ..utils.atomic_write import atomic_write_text
from .projection import build_dashboard_projection
from .snapshot import build_dashboard_snapshot


def _collect_user_sections(snap: dict[str, Any]):
    """Register the rich user-home providers bound to this snapshot and collect the sections.

    Fail-open: a provider/registry error never blanks the dashboard (returns [])."""
    try:
        from .providers import register_core_dashboard_defaults
        from .registry import collect

        register_core_dashboard_defaults(snap)
        return collect()
    except Exception:
        return []


def _render_sections_text(sections) -> list[str]:
    out: list[str] = []
    for sec in sections:
        out.append(f"  {sec.title}")
        for row in sec.rows:
            icon = f"{row.icon} " if row.icon else ""
            line = f"    {icon}{row.text}"
            if row.sub:
                line += f" — {row.sub}"
            if row.meta:
                line += f"  [{row.meta}]"
            out.append(line)
    return out


def render_dashboard_text(snapshot: dict[str, Any] | None = None) -> str:
    """Render both canonical perspectives plus unique local evidence."""
    snap = snapshot or build_dashboard_snapshot()
    env = snap.get("environment") or {}
    work = snap.get("work") or {}
    graph = snap.get("graph") or {}
    runtime = snap.get("runtime") or {}
    artifacts = snap.get("artifacts") or {}
    user_projection = build_dashboard_projection(snap, perspective="user", surface="terminal")
    operations_projection = build_dashboard_projection(snap, perspective="operations", surface="terminal")

    lines: list[str] = [
        "MO dashboard:",
        f"  project:   {env.get('project') or '?'}",
        f"  runtime:   {runtime.get('provider') or '?'} / {runtime.get('model') or '?'} · slot {runtime.get('session_slot') or '?'}",
        f"  system:    {env.get('os') or '?'} {env.get('os_release') or ''} · Python {env.get('python') or '?'}",
        "",
    ]
    lines.extend(_projection_text("Your view", user_projection))
    lines.append("")
    lines.extend(_projection_text("MO operations", operations_projection))
    lines.extend(["", "Extended local evidence:"])
    lines.extend(_render_sections_text(_collect_user_sections(snap)))
    recent = list(work.get("recent_boards") or [])[:3]
    if recent:
        scope = work.get("recent_boards_scope") or "all sessions"
        lines.append(f"  recent MO work ({scope}):")
        for board in recent:
            lines.append(
                f"    - {board.get('state') or '?'} · {int(board.get('open') or 0)}/{int(board.get('total') or 0)} open · "
                f"{board.get('title') or '(untitled)'}"
            )
    graph_state = "available" if graph.get("available") else "not built"
    stale = " · stale" if graph.get("stale") else ""
    lines.extend([
        f"  graph:      {graph_state}{stale} · {int(graph.get('nodes') or 0)} nodes · "
        f"{int(graph.get('edges') or 0)} edges · {int(graph.get('groups') or 0)} path groups",
        f"  quality:    {_graph_quality_text(graph.get('quality') or {}) or 'not measured'}",
    ])
    confidence = _count_map_text(graph.get("confidence_breakdown") or {})
    provenance = _count_map_text(graph.get("provenance_breakdown") or {})
    if confidence:
        lines.append(f"  confidence: {confidence}")
    if provenance:
        lines.append(f"  provenance: {provenance}")
    if graph.get("stale"):
        reasons = ", ".join(str(item) for item in list(graph.get("stale_reasons") or [])[:3]) or "unknown"
        lines.append(f"  freshness: stale ({reasons}); {int(graph.get('stale_files') or 0)} changed/new/removed file(s)")
    if artifacts.get("code_map"):
        lines.append("  interactive map: /dashboard map to open in browser")
        lines.append(f"              {artifacts['code_map']}")
    if artifacts.get("dashboard_html"):
        lines.append(f"  html:       {artifacts['dashboard_html']} (/dashboard html)")
    lines.extend([
        "",
        "Trust:",
        "  read-only synthesis; taskboard, heartbeat, profile, learning, and graph remain the source ledgers",
        "  controls delegate to those existing owners; their validation and confirmation rules still apply",
        "  raw profile prose, memory text, secrets, and credential values are not embedded",
    ])
    return "\n".join(lines)


def _projection_text(title: str, projection: dict[str, Any]) -> list[str]:
    status = projection.get("status") if isinstance(projection.get("status"), dict) else {}
    lines = [f"{title}:", f"  status:     {status.get('label') or 'Unknown'} · {status.get('detail') or ''}"]
    for metric in list(projection.get("metrics") or [])[:8]:
        label = str(metric.get("label") or "metric").strip().lower()
        lines.append(f"  {label}: {metric.get('value') or '—'} · {metric.get('detail') or ''}")
    for section in list(projection.get("sections") or [])[:6]:
        lines.append(f"  {section.get('title') or 'Summary'}:")
        for item in list(section.get("items") or [])[:8]:
            lines.append(
                f"    - {item.get('label') or 'Status'}: {item.get('value') or '—'}"
                + (f" · {item.get('detail')}" if item.get("detail") else "")
            )
    controls = [
        action for action in list(projection.get("actions") or [])
        if isinstance(action, dict) and action.get("kind") in {"command", "request"}
    ]
    if controls:
        lines.append("  delegated controls:")
        lines.extend(f"    - {action.get('label')}: {action.get('target')}" for action in controls)
    return lines


def write_dashboard_html(
    snapshot: dict[str, Any] | None = None,
    *,
    output: str | Path | None = None,
) -> dict[str, Any]:
    """Write a self-contained private HTML dashboard artifact."""
    snap = snapshot or build_dashboard_snapshot()
    out = Path(output) if output else Path(resolve_state_path(DASHBOARD_HTML_PATH))
    out.parent.mkdir(parents=True, exist_ok=True)
    html_text = render_dashboard_html(snap)
    atomic_write_text(out, html_text, encoding="utf-8")
    return {"path": str(out), "bytes": out.stat().st_size, "version": snap.get("version", "")}


def render_dashboard_html(snapshot: dict[str, Any], *, connected: bool = False) -> str:
    """One browser presentation for the live adapter and explicit static export."""
    from interface.desktop_brand import cube_mark_css, cube_mark_html, window_chrome_css, window_controls_html

    payload = {
        "connected": connected,
        "snapshot": snapshot,
        "projection": build_dashboard_projection(snapshot, surface="html"),
        "operations": build_dashboard_projection(snapshot, perspective="operations", surface="html"),
        "evidence": [{"title": section.title, "rows": [{"text": row.text, "sub": row.sub, "meta": row.meta} for row in section.rows]} for section in _collect_user_sections(snapshot)] if snapshot else [],
    }
    template = Path(__file__).with_name("dashboard.html").read_text(encoding="utf-8")
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return (template.replace("__MO_SKIN__", _dashboard_skin_css() + cube_mark_css())
            .replace("__MO_STYLE__", Path(__file__).with_name("dashboard.css").read_text(encoding="utf-8") + window_chrome_css())
            .replace("__MO_WINDOW_CONTROLS__", window_controls_html(("minimize", "maximize", "close")))
            .replace("__MO_SCRIPT__", Path(__file__).with_name("dashboard.js").read_text(encoding="utf-8"))
            .replace("__MO_MARK__", cube_mark_html(label="MO"))
            .replace("__MO_DATA__", data))


def _dashboard_skin_css() -> str:
    from interface.theming import contrast_text, get_skin, skin_to_code_map_css
    from interface.desktop_ui import active_desktop_visual_state

    s = get_skin()
    from mo_desktop.design_studio.theme import studio_theme
    from mo_desktop.mo_renderer import _initial_studio_theme_css

    visuals = active_desktop_visual_state()
    metrics = visuals.metrics
    return _initial_studio_theme_css(studio_theme(config={}, visuals=visuals)) + skin_to_code_map_css(s) + f""":root {{
  --scheme: {contrast_text(s.bg_dark, dark='light', light='dark')};
  color-scheme: var(--scheme);
  --button-radius: {metrics.button_corner_radius}px;
  --button-pad: {metrics.button_padding}px;
  --bg: {s.bg_dark};
  --panel: {s.bg_surface};
  --panel2: {s.bg_input};
  --text: {s.text_primary};
  --text-bright: {s.text_bright};
  --muted: {s.text_muted};
  --line: {s.code_map_line};
  --edge: {s.border_default};
  --brand: {s.brand_primary};
  --good: {s.status_done};
  --warn: {s.status_active};
  --bad: {s.status_blocked};
  --chip: {s.code_map_badge_bg};
  --pre: {s.bg_deepest};
}}"""


def _count_map_text(counts: dict[str, int]) -> str:
    if not isinstance(counts, dict):
        return ""
    parts: list[str] = []
    for key, value in sorted(counts.items()):
        try:
            count = int(value or 0)
        except (TypeError, ValueError):
            count = 0
        if count:
            parts.append(f"{key} {count}")
    return ", ".join(parts)


def _graph_quality_text(quality: dict[str, Any]) -> str:
    if not isinstance(quality, dict):
        return ""
    fields = (
        ("qualified_symbol_nodes", "qualified symbols"),
        ("method_nodes", "methods"),
        ("resolved_calls", "resolved calls"),
        ("ambiguous_calls", "ambiguous calls"),
        ("unresolved_project_calls", "unresolved project calls"),
    )
    parts = []
    for key, label in fields:
        try:
            value = int(quality.get(key) or 0)
        except (TypeError, ValueError):
            continue
        if value:
            parts.append(f"{value:,} {label}")
    return " · ".join(parts)
