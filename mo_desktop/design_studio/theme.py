"""Shared skin and Desktop geometry projection for the webview shell."""
from __future__ import annotations

from typing import Any


def studio_theme(
    config_path: str = "",
    *,
    config: dict[str, Any] | None = None,
    visuals: Any = None,
) -> dict[str, Any]:
    if config is None:
        from mo_desktop.visuals import load_desktop_config

        config = load_desktop_config(config_path=config_path)
    from mo_desktop.settings import load_settings
    from interface.desktop_ui import DesktopVisualState
    from mo_desktop.visuals import desktop_visual_payload, load_desktop_visual_state

    state = visuals or load_desktop_visual_state(config, refresh_skin=True)
    if not isinstance(state, DesktopVisualState):
        raise TypeError("MO Design theme requires DesktopVisualState")
    settings = load_settings(config)
    character = settings.character
    character_style = {
        "size": character.size,
        "cube_count": character.cube_count,
        "glow": character.glow,
        "corner_radius": character.corner_radius,
        "color_mode": character.color_mode,
    }
    return desktop_visual_payload(state, character=character_style)
