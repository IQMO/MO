"""Truthful MO Design completion projection."""
from __future__ import annotations

from typing import Any

from core.design.schema import DesignDocument


def brief_signature(document: DesignDocument) -> tuple[Any, ...]:
    """Return the saved brief fields that can complete a brief-only Design turn."""
    handoff = document.handoff
    return (
        str(handoff.objective or ""),
        tuple(handoff.acceptance),
        tuple(handoff.decisions),
        tuple(handoff.constraints),
        str(handoff.project_root or ""),
        str(handoff.context_query or ""),
        tuple(handoff.files),
        tuple(handoff.symbols),
    )


def delivery_activity(
    document: DesignDocument,
    *,
    result_message: str = "",
    brief_only: bool = False,
) -> tuple[str, str]:
    """Describe the saved Design result without implying implementation."""
    revision = document.meta.revision
    if brief_only:
        label = f"Brief updated · r{revision}"
        detail = "The visual brief was saved with this concept. Keep refining or use final Handoff when approved."
    elif "visual qa" in result_message.casefold() and "unavailable" in result_message.casefold():
        label = f"Visual QA unavailable · r{revision}"
        detail = "The concept was saved, but its rendered pixels still need review before final Handoff."
    elif document.runtime.allow_scripts and str(document.design.script or "").strip():
        label = f"Interactive prototype · r{revision}"
        detail = "The working prototype was saved. Keep refining or use final Handoff when approved."
    else:
        label = f"Static concept · r{revision}"
        detail = "The visual concept was saved. Keep refining or use final Handoff when approved."
    return label, detail
