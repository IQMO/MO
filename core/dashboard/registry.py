"""Neutral rich-section extension registry for generated MO dashboards.

A dashboard is a vertical stack of SECTIONS, each a small titled list of rows. Sections come
from a registry of PROVIDERS. Providers return plain data and are collected as the extended
local detail under the generated terminal/HTML dashboard. Desktop may register action-backed
rows here for that extended view, but its compact panel is rendered from the bounded semantic
projection in ``core.dashboard.projection``.

This compatibility extension lives in ``core`` so existing providers and Desktop imports keep
working without becoming a second state or projection owner. Pure data + registry: no GUI /
provider imports, so it stays cheap and cannot drag dependencies onto the light path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class DashboardRow:
    """One line in a section. ``meta`` is right-aligned (a time or a badge); ``dim``
    renders a muted 'coming' style for not-yet-wired integrations."""

    text: str
    icon: str = ""
    sub: str = ""
    meta: str = ""
    dim: bool = False
    action: Callable[[], None] | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class DashboardSection:
    title: str
    rows: tuple[DashboardRow, ...] = ()
    order: int = 100


Provider = Callable[[], "DashboardSection | None"]

_PROVIDERS: list[tuple[int, str, Provider]] = []


def register(provider: Provider, *, order: int = 100, key: str = "") -> None:
    """Register a section provider. ``order`` sorts sections top→bottom; a non-empty
    ``key`` replaces any previously-registered provider with the same key (so a live
    feed can supersede a built-in default cleanly)."""
    name = str(key or getattr(provider, "__name__", "") or "")
    if name:
        _PROVIDERS[:] = [p for p in _PROVIDERS if p[1] != name]
    _PROVIDERS.append((int(order), name, provider))


def collect() -> list[DashboardSection]:
    """Every registered section with at least one row, in ``order``. A provider that
    raises or returns ``None`` is skipped — one bad feed never blanks the dashboard."""
    out: list[DashboardSection] = []
    for _order, _name, prov in sorted(_PROVIDERS, key=lambda p: p[0]):
        try:
            section = prov()
        except Exception:
            section = None
        if section and section.rows:
            out.append(section)
    return out
