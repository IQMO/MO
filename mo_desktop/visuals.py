"""One strict visual-state bootstrap and publisher for every Desktop surface."""
from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from interface.desktop_ui import (
    DESKTOP_TYPOGRAPHY,
    DesktopVisualState,
    activate_desktop_visual_state,
    active_desktop_visual_state,
    desktop_visual_state,
)


VisualListener = Callable[[DesktopVisualState], Any]
_LOCK = threading.RLock()
_LISTENERS: list[VisualListener] = []


def load_desktop_config(
    config: Any = None,
    *,
    config_path: str = "",
) -> dict[str, Any]:
    """Resolve Desktop's canonical config without an alternate recovery source.

    A missing private default config is the supported first-run state. An
    explicit path remains strict, and malformed or unreadable files propagate
    the canonical ``ConfigLoadError``.
    """
    if config is not None:
        if not isinstance(config, Mapping):
            raise TypeError("Desktop config must be a mapping")
        return dict(config)
    from core.provider.provider import load_config
    from core.state.paths import default_config_path

    if config_path:
        return load_config(config_path)
    target = Path(default_config_path()).expanduser().resolve(strict=False)
    if not target.exists():
        return {}
    return load_config(str(target))


def load_desktop_visual_state(
    config: Any = None,
    *,
    config_path: str = "",
    refresh_skin: bool = False,
) -> DesktopVisualState:
    """Load the active skin and PanelSettings into one validated projection."""
    config = load_desktop_config(config, config_path=config_path)
    from interface.theming import (
        get_skin_name,
        refresh_skin_from_disk,
        skin_to_desktop_vars,
    )
    from mo_desktop.settings import load_settings

    if refresh_skin:
        refresh_skin_from_disk()
    settings = load_settings(config)
    return desktop_visual_state(
        skin_to_desktop_vars(),
        settings.panel,
        skin_id=get_skin_name(),
    )


def publish_desktop_visual_state(state: DesktopVisualState) -> DesktopVisualState:
    """Activate and synchronously publish one complete state transaction."""
    if not isinstance(state, DesktopVisualState):
        raise TypeError("Desktop visual publisher requires DesktopVisualState")
    with _LOCK:
        current = active_desktop_visual_state()
        if current == state:
            return current
        active = activate_desktop_visual_state(state)
        listeners = tuple(_LISTENERS)
    for listener in listeners:
        listener(active)
    return active


def load_and_publish_desktop_visual_state(
    config: Any = None,
    *,
    config_path: str = "",
    refresh_skin: bool = False,
) -> DesktopVisualState:
    return publish_desktop_visual_state(
        load_desktop_visual_state(
            config,
            config_path=config_path,
            refresh_skin=refresh_skin,
        )
    )


def subscribe_desktop_visual_state(listener: VisualListener) -> Callable[[], None]:
    if not callable(listener):
        raise TypeError("Desktop visual listener must be callable")
    with _LOCK:
        if listener not in _LISTENERS:
            _LISTENERS.append(listener)

    def unsubscribe() -> None:
        with _LOCK:
            try:
                _LISTENERS.remove(listener)
            except ValueError:
                pass

    return unsubscribe


def desktop_visual_payload(
    state: DesktopVisualState,
    *,
    character: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Serialize the exact state for Design and one-shot Desktop processes."""
    from interface.desktop_brand import WINDOW_CHROME

    palette = state.palette
    metrics = state.metrics
    effects = state.effects
    return {
        "chrome": dict(WINDOW_CHROME),
        "tokens": {
            "name": state.skin_id,
            "background": str(state.token("_CHROMA")),
            "surface": palette.card,
            "input": palette.entry,
            "brand": palette.accent,
            "glow": str(state.token("_GLOW")),
            "text": palette.text,
            "muted": palette.muted,
            "border": palette.border,
            "ok": palette.ok,
            "warn": palette.warn,
            "error": palette.error,
            "action": palette.action,
        },
        "panel": {
            "padding": metrics.panel_padding,
            "corner_radius": metrics.panel_corner_radius,
            "button_padding": metrics.button_padding,
            "button_corner_radius": metrics.button_corner_radius,
            "window_effect": effects.style,
            "window_effect_intensity": effects.intensity,
        },
        "typography": {
            "small": {
                "family": DESKTOP_TYPOGRAPHY.small[0],
                "size": DESKTOP_TYPOGRAPHY.small[1],
            },
            "body": {
                "family": DESKTOP_TYPOGRAPHY.body[0],
                "size": DESKTOP_TYPOGRAPHY.body[1],
            },
            "title": {
                "family": DESKTOP_TYPOGRAPHY.title[0],
                "size": DESKTOP_TYPOGRAPHY.title[1],
            },
            "mono": {
                "family": DESKTOP_TYPOGRAPHY.mono[0],
                "size": DESKTOP_TYPOGRAPHY.mono[1],
            },
        },
        "character": dict(character or {}),
    }


__all__ = [
    "desktop_visual_payload",
    "load_and_publish_desktop_visual_state",
    "load_desktop_config",
    "load_desktop_visual_state",
    "publish_desktop_visual_state",
    "subscribe_desktop_visual_state",
]
