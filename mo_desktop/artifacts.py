"""MO Desktop attachment presentation helpers.

Catalog paths, imports, provenance, and summaries belong to
``core.state.attachments``. This module owns only Desktop panel presentation and
allowed-root display behavior.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.state.attachments import (
    IMAGE_ATTACHMENT_SUFFIXES,
    MEDIA_ATTACHMENT_SUFFIXES,
)

ATTACHMENT_NAME_MAX_CHARS = 34

def allowed_roots_with_artifacts(
    dest: Path,
    current_roots: list[str] | tuple[str, ...] | None,
) -> list[str]:
    """Include the artifact home without narrowing an unrestricted caller."""
    roots = list(current_roots or [])
    if not roots:
        return roots

    def append_unique(path: Path) -> None:
        text = str(path)
        if not any(str(root).casefold() == text.casefold() for root in roots):
            roots.append(text)

    append_unique(dest)
    return roots


def attachment_panel_state(paths: list[str | Path]):
    """Return the reply-panel state for a set of attached files."""
    from mo_desktop.design import PanelState

    suffixes = [Path(path).suffix.lower() for path in paths]
    if suffixes and all(suffix in IMAGE_ATTACHMENT_SUFFIXES for suffix in suffixes):
        return PanelState.IMAGE
    if any(suffix in MEDIA_ATTACHMENT_SUFFIXES for suffix in suffixes):
        return PanelState.MEDIA
    return PanelState.FILE


def attachment_panel_text(paths: list[Any]) -> str:
    """Compact human-visible attachment summary for the docked Desktop bubble."""
    names = [str(getattr(path, "name", "") or path).strip() for path in paths]
    names = [name for name in names if name]
    count = len(names)
    if not names:
        return "No attachment details"
    first = _compact_attachment_name(names[0])
    if count == 1:
        return first
    return f"{count} files · {first} · +{count - 1}"


def _compact_attachment_name(value: Any, max_chars: int = ATTACHMENT_NAME_MAX_CHARS) -> str:
    name = str(value or "").strip()
    limit = max(12, int(max_chars))
    if len(name) <= limit:
        return name
    suffix = Path(name).suffix
    if len(suffix) > 10:
        suffix = ""
    stem = name[:-len(suffix)] if suffix else name
    available = max(3, limit - len(suffix) - 1)
    head = max(1, (available * 2) // 3)
    tail = max(1, available - head)
    return f"{stem[:head]}…{stem[-tail:]}{suffix}"
