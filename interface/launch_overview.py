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
    from core.profile.server_aliases import configured_server_aliases

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
        aliases = configured_server_aliases()
        if aliases:
            overflow = f" +{len(aliases) - 1}" if len(aliases) > 1 else ""
            suffix = overflow + " · Not checked"
            name = clip_text_to_cells(aliases[0], max(2, columns - 21 - len(suffix)))
            rows.append(_row("Servers", name + suffix, "/status", columns))
    except (OSError, ValueError, UnicodeError):
        rows.append(_row("Servers", "Configuration unavailable", "", columns))

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
    return rows
