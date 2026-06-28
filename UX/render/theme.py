"""Theme tokens for the isolated UX surface.

Colour values are kept in-sync with ``interface.theming`` (single source of
truth) by the adapter layer (``UX/runtime/adapters``), but the render layer
must not import from outside UX — these defaults mirror MO_DEFAULT.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UxTheme:
    background: str
    surface: str
    border: str
    text: str
    muted: str
    brand: str
    blue: str
    green: str
    amber: str
    red: str
    violet: str


DEFAULT_THEME: UxTheme = UxTheme(
    background="#090b10",
    surface="#111722",
    border="#314253",
    text="#d7dee8",
    muted="#7d8996",
    brand="#00cccc",
    blue="#7aa2ff",
    green="#68d391",
    amber="#f6ad55",
    red="#fc8181",
    violet="#bb86fc",
)


STATUS_STYLE = {
    "completed": "green",
    "active": "amber",
    "blocked": "red",
    "pending": "muted",
}

LANE_STYLE = {
    "running": "amber",
    "ready": "blue",
    "idle": "muted",
    "blocked": "red",
    "done": "green",
}
