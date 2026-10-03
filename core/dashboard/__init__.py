"""Bounded MO dashboard projections and presentation adapters."""

from .snapshot import build_dashboard_snapshot
from .projection import DASHBOARD_PROJECTION_VERSION, build_dashboard_projection
from .render import render_dashboard_text, write_dashboard_html

__all__ = [
    "DASHBOARD_PROJECTION_VERSION",
    "build_dashboard_projection",
    "build_dashboard_snapshot",
    "render_dashboard_text",
    "write_dashboard_html",
]
