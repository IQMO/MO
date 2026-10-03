"""Validated semantic color model shared by every MO surface."""
from __future__ import annotations

from dataclasses import dataclass, fields
import re


_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_OPTIONAL_COLORS = {"dark_rgb_override"}


@dataclass(frozen=True)
class Skin:
    """Complete semantic color tokens for one registered MO skin."""

    # Backgrounds
    bg_deepest: str
    bg_dark: str
    bg_surface: str
    bg_input: str

    # Text
    text_primary: str
    text_secondary: str
    text_dim: str
    text_muted: str
    text_bright: str
    text_placeholder: str
    text_code: str

    # Borders
    border_default: str
    border_focus: str

    # Brand
    brand_primary: str
    brand_glow: str

    # Accents
    accent_blue: str
    accent_green: str
    accent_amber: str
    accent_warning: str
    accent_red: str
    accent_red_soft: str
    accent_critical: str
    accent_purple: str
    accent_yellow: str
    accent_info: str

    # Status semantics
    status_done: str
    status_active: str
    status_blocked: str
    status_pending: str

    # Surface-specific semantic roles
    user_msg_bg: str
    selected_bg: str
    palette_desc: str
    palette_hint: str
    desktop_frame: str
    desktop_gap: str
    desktop_response: str
    response_text: str
    response_subtle: str
    spinner: str

    # Code map
    code_map_line: str
    code_map_badge_bg: str

    # Empty means derive the artistic value from the semantic colors.
    dark_rgb_override: str = ""

    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name in _OPTIONAL_COLORS and value == "":
                continue
            if not isinstance(value, str) or _HEX_COLOR.fullmatch(value) is None:
                raise ValueError(f"Skin token {field.name} must be #RRGGBB")

    @property
    def hex_bg_deepest_rgb(self) -> tuple[int, int, int]:
        return _hex_to_rgb(self.bg_deepest)

    @property
    def hex_brand_primary_rgb(self) -> tuple[int, int, int]:
        return _hex_to_rgb(self.brand_primary)


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    h = value.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
