"""Pure canonical identity for MO runtime surfaces."""

from __future__ import annotations


RUNTIME_SURFACE_ALIASES = {
    "": "terminal",
    "user": "terminal",
    "main": "terminal",
    "pc": "terminal",
    "desktop": "mo_desktop",
    "telegram": "telegram",
    "cron": "cron",
    "server": "server",
    "heartbeat": "heartbeat",
}

# The MO Desktop surface family: MO Desktop itself plus its isolated companion
# package. Both run on MO Desktop's own session and must never leak into the
# Main-MO economy record or the terminal lane. This is the single canonical
# membership; gates, continuity, and economy exclusion all reference it instead
# of re-deriving a drifting private set.
DESKTOP_SURFACES = frozenset({"mo_desktop", "companion"})

# MO Desktop's isolated conversation slot: the active slot is `mo-desktop` and
# historical/role snapshots use `mo-desktop-<suffix>` (mo_desktop/MAINTAINING.md).
# Core owns the value so continuity, session naming and dispatch never re-spell
# it; the companion package imports it, core never depends on mo_desktop.
DESKTOP_SESSION_SLOT = "mo-desktop"

# Auxiliary structured-review calls share the no-tools recovery semantics in
# ``Agent.complete_no_tools``. Keep this surface contract beside canonical
# normalization so callers do not duplicate a drifting string whitelist.
STRUCTURED_REVIEW_SURFACES = frozenset({"review", "review_standard", "review_confirm"})


def normalize_runtime_surface(surface: object) -> str:
    """Return the open-ended canonical policy identity for a runtime surface."""
    value = str(surface or "").strip().lower().replace(" ", "_").replace("-", "_")
    return RUNTIME_SURFACE_ALIASES.get(value, value or "terminal")


def is_structured_review_surface(surface: object) -> bool:
    """Whether a normalized surface belongs to the bounded PRT review lane."""
    return normalize_runtime_surface(surface) in STRUCTURED_REVIEW_SURFACES


__all__ = [
    "RUNTIME_SURFACE_ALIASES",
    "STRUCTURED_REVIEW_SURFACES",
    "DESKTOP_SURFACES",
    "DESKTOP_SESSION_SLOT",
    "is_structured_review_surface",
    "normalize_runtime_surface",
]
