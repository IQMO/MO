"""Theme tokens for the isolated UX surface."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UxTheme:
    background: str = "#090b10"
    surface: str = "#111722"
    border: str = "#314253"
    text: str = "#d7dee8"
    muted: str = "#7d8996"
    brand: str = "#39d0c8"
    blue: str = "#7aa2ff"
    green: str = "#68d391"
    amber: str = "#f6ad55"
    red: str = "#fc8181"
    violet: str = "#b794f4"


DEFAULT_THEME = UxTheme()


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
