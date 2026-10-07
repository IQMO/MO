"""Compact launch rows over existing profile and learning owners (no actions)."""
from __future__ import annotations

import os
import unicodedata
from pathlib import Path
from typing import Any

from .activity import clip_text_to_cells, fit_fragments_to_cells


def _safe_fragment(value: Any) -> str:
    from core.tooling.sandbox import redact_sensitive_text

    text = redact_sensitive_text(str(value or ""))
    return "".join(c for c in text if not unicodedata.category(c).startswith("C"))


def _text(value: Any) -> str:
    return " ".join(_safe_fragment(value).split())


def _row(label: str, text: str, command: str, columns: int, *, style: str = "") -> list[tuple[str, str]]:
    # Reserve the command before clipping the value: the escape hatch survives
    # long project names, learning titles and narrow terminal widths.
    prefix = f"  {label:<10}"
    suffix = f"  {command}" if command else ""
    budget = columns - len(prefix) - len(suffix)
    if budget < 8:
        prefix = f"  {label} " if label else "  "
        suffix = ""
        budget = columns - len(prefix)
    return fit_fragments_to_cells([
        ("class:dim", prefix),
        (style, clip_text_to_cells(text, budget)),
        ("class:info", suffix),
    ], columns)[0]


def startup_overview_fragment_lines(agent: Any, *, columns: int) -> list[list[tuple[str, str]]]:
    """Return compact launch rows over the existing status owners."""
    from core.learning.status import launch_learning_summary

    profile = getattr(agent, "profile", None)
    config = getattr(agent, "config", {}) or {}
    rows: list[list[tuple[str, str]]] = []
    current = str(getattr(agent, "project_cwd", "") or os.environ.get("MO_PROJECT_CWD") or os.getcwd())
    try:
        entries = list(getattr(profile, "projects", {}).values())
        entries.sort(key=lambda item: float(getattr(item, "last_opened", 0.0) or 0.0), reverse=True)
        active_key = os.path.normcase(os.path.abspath(current))
        names: list[str] = []
        for item in entries:
            path = str(getattr(item, "path", "") or "")
            name = _text(getattr(item, "name", "") or Path(path).name)
            if os.path.normcase(os.path.abspath(path)) == active_key:
                names.insert(0, f"{name} *")
            else:
                names.append(name)
        if not names:
            projects = "No recent folders"
        else:
            count = min(len(names), 2 if columns < 80 else 3)
            # Bound each name, retain overflow even when only one name fits.
            while count > 1 and sum(len(n) for n in names[:count]) > columns - 34:
                count -= 1
            overflow = f"  +{len(names) - count}" if len(names) > count else ""
            budget = max(8, columns - 24 - len(overflow))
            projects = clip_text_to_cells(" · ".join(names[:count]), budget) + overflow
        rows.append(_row("Folders", projects, "/projects", columns))
    except (OSError, ValueError, TypeError, AttributeError):
        rows.append(_row("Folders", "Unavailable", "/projects", columns))

    try:
        learning = launch_learning_summary(profile, config=config)
    except Exception:
        learning = {"available": False, "skills": None, "pending": None}
    if not learning.get("available"):
        summary = "Unavailable"
    else:
        count = int(learning.get("skills") or 0)
        noun = "skill" if count == 1 else "skills"
        parts = [f"{count} learned {noun}"]
        pending = int(learning.get("pending") or 0)
        if pending:
            noun = "review" if pending == 1 else "reviews"
            parts.append(f"{pending} {noun}")
        summary = " · ".join(parts)
    rows.append(_row("Learning", summary, "/learning", columns))
    host = _host_line(config)
    if host:
        rows.append(_row("MO host", host, "/everywhere status", columns))
    return rows


def _host_line(config: dict) -> str:
    """The MO host as this PC last saw it (the Everywhere coordinator's record, no network
    call at startup); nothing when MO Everywhere is not set up here."""
    import time

    try:
        from core.state.everywhere_coordinator import host_last_seen

        seen = host_last_seen(config)
    except Exception:
        return ""
    state, at = seen.get("state"), seen.get("at")
    if state in {"off", "unknown"}:
        return ""
    when = ""
    if at:
        minutes = max(0, int((time.time() - float(at)) // 60))
        when = ("just now" if minutes < 1 else f"{minutes} min ago" if minutes < 60
                else f"{minutes // 60} h ago" if minutes < 48 * 60 else time.strftime("%d %b", time.localtime(float(at))))
    if state == "online":
        return f"Online · synced {when}" if when else "Online"
    if state == "blocked":
        return f"Needs attention · {when}" if when else "Needs attention"
    return f"Unreachable · last tried {when}" if when else "Unreachable"
