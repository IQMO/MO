"""MO Desktop settings — the single, isolated home for companion customization.

Everything tunable about the current companion (size, character, and behavior)
lives here as typed defaults. User overrides come from MO's own config block
(`mo_desktop:`) via
``mo_desktop.desktop_launch.mo_desktop_config_block`` — this module does NOT
add a parallel config loader or a second settings file. Edit `config.yaml`'s
`mo_desktop:` block (or pass overrides) to customize; the schema and defaults are here.

Example (config.yaml):

    mo_desktop:
      enabled: true
      action_receipt_seconds: 300  # bounded same-session action continuation
      character:
        size: 96          # cube-cluster edge in px
        cube_count: 4     # default balanced form; 5 adds a center cube
        glow: 0.6
      behavior:
        default_mode: free
      panel:
        window_effect: hybrid
        window_effect_intensity: 55
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from math import isfinite
import ntpath
import re
from typing import Any

from interface.desktop_ui import (
    DEFAULT_BUTTON_CORNER_RADIUS,
    DEFAULT_BUTTON_PADDING,
    DEFAULT_PANEL_CORNER_RADIUS,
    DEFAULT_PANEL_PADDING,
    DEFAULT_WINDOW_EFFECT,
    DEFAULT_WINDOW_EFFECT_INTENSITY,
    desktop_visual_metrics,
    desktop_window_effects,
)


_MAX_KEEP_ABOVE_APPS = 24
_MAX_EXECUTABLE_NAME_CHARS = 96
_APP_LIST_SPLIT_RE = re.compile(r"[,;\n]+")


@dataclass
class CharacterSettings:
    """The cube character's look. One shade; glow/formation carry expression."""
    size: int = 84                 # cube-cluster edge in px (primary size knob)
    cube_count: int = 4            # default balanced form; 5 adds a center cube
    color_mode: str = "skin"       # "skin" follows the active MO skin, or a "#rrggbb"
    glow: float = 0.5              # 0..1 shine/bloom intensity
    corner_radius: float = 0.22    # cube rounding as a fraction of cube edge


@dataclass
class BehaviorSettings:
    """Free-move vs cursor-chase and how motion feels."""
    default_mode: str = "free"     # "free" | "lock"
    follow_ease: float = 0.16      # chase spring 0..1 — lower = more lag/delay
    follow_distance: float = 64.0  # trailing gap behind the cursor in px
    keep_above_apps: list[str] = field(default_factory=list)
    # Exact executable basenames whose topmost overlays MO may rise above. Empty = normal Z-order.


@dataclass
class PanelSettings:
    """The shared Desktop window, panel, and control visual projection."""
    padding: int = DEFAULT_PANEL_PADDING
    corner_radius: int = DEFAULT_PANEL_CORNER_RADIUS
    button_padding: int = DEFAULT_BUTTON_PADDING
    button_corner_radius: int = DEFAULT_BUTTON_CORNER_RADIUS
    window_effect: str = DEFAULT_WINDOW_EFFECT
    window_effect_intensity: int = DEFAULT_WINDOW_EFFECT_INTENSITY


@dataclass
class DesktopSettings:
    """Top-level MO Desktop settings. `voice` and `tray` reuse the existing config block."""
    enabled: bool = False
    tray_enabled: bool = True
    character: CharacterSettings = field(default_factory=CharacterSettings)
    behavior: BehaviorSettings = field(default_factory=BehaviorSettings)
    panel: PanelSettings = field(default_factory=PanelSettings)


def _apply(target: Any, overrides: Any) -> None:
    """Shallow-merge a dict of overrides onto a dataclass, recursing into nested
    dataclasses. Unknown keys are ignored (forward/backward-compatible)."""
    if not isinstance(overrides, dict):
        return
    known = {f.name: f for f in fields(target)}
    for key, value in overrides.items():
        f = known.get(key)
        if f is None:
            continue
        current = getattr(target, f.name)
        if is_dataclass(current) and isinstance(value, dict):
            _apply(current, value)
        elif not is_dataclass(current):
            setattr(target, f.name, value)


def _bounded_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(low, min(high, number)) if isfinite(number) else default


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def normalize_keep_above_apps(value: Any) -> list[str]:
    """Return a bounded, de-duplicated list of executable basenames.

    Settings-panel text may be comma/semicolon/newline separated while YAML naturally
    supplies a list. Paths are reduced to basenames so private machine paths never become
    part of Desktop's matching state or diagnostics.
    """
    if isinstance(value, str):
        candidates = _APP_LIST_SPLIT_RE.split(value)
    elif isinstance(value, (list, tuple, set)):
        candidates = list(value)
    else:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        name = ntpath.basename(str(candidate or "").strip().strip('"')).strip()
        if not name or len(name) > _MAX_EXECUTABLE_NAME_CHARS:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(name)
        if len(normalized) >= _MAX_KEEP_ABOVE_APPS:
            break
    return normalized


def _normalize(settings: DesktopSettings) -> None:
    defaults = DesktopSettings()
    character = settings.character
    character.size = _bounded_int(character.size, defaults.character.size, 56, 140)
    character.cube_count = _bounded_int(character.cube_count, defaults.character.cube_count, 4, 5)
    character.glow = _bounded_float(character.glow, defaults.character.glow, 0.0, 1.0)
    character.corner_radius = _bounded_float(character.corner_radius, defaults.character.corner_radius, 0.0, 0.5)
    color = str(character.color_mode or "").strip().lower()
    try:
        valid_color = color == "skin" or (len(color) == 7 and color.startswith("#") and int(color[1:], 16) >= 0)
    except ValueError:
        valid_color = False
    character.color_mode = color if valid_color else defaults.character.color_mode

    behavior = settings.behavior
    behavior.default_mode = "lock" if str(behavior.default_mode).strip().lower() == "lock" else "free"
    behavior.follow_distance = _bounded_float(behavior.follow_distance, defaults.behavior.follow_distance, 20.0, 140.0)
    behavior.follow_ease = _bounded_float(behavior.follow_ease, defaults.behavior.follow_ease, 0.04, 0.4)
    behavior.keep_above_apps = normalize_keep_above_apps(behavior.keep_above_apps)
    panel = settings.panel
    metrics = desktop_visual_metrics(panel)
    panel.padding = metrics.panel_padding
    panel.corner_radius = metrics.panel_corner_radius
    panel.button_padding = metrics.button_padding
    panel.button_corner_radius = metrics.button_corner_radius
    effects = desktop_window_effects(panel)
    panel.window_effect = effects.style
    panel.window_effect_intensity = effects.intensity
    settings.enabled = settings.enabled if isinstance(settings.enabled, bool) else defaults.enabled
    settings.tray_enabled = settings.tray_enabled if isinstance(settings.tray_enabled, bool) else defaults.tray_enabled


def load_settings(config: Any) -> DesktopSettings:
    """Return DesktopSettings with user overrides from MO's config block merged over
    the defaults. Reuses ``mo_desktop_config_block`` — the single config source."""
    settings = DesktopSettings()
    from mo_desktop.desktop_launch import mo_desktop_config_block

    block = mo_desktop_config_block(config)
    _apply(settings, block)
    _normalize(settings)
    return settings


def persist_mo_desktop_settings(config: Any, overrides: dict) -> bool:
    """Save only Desktop's block through the shared authored-config writer."""
    if not isinstance(config, dict) or not isinstance(overrides, dict) or not overrides:
        return False
    from core.state.configuration import persist_configuration
    return persist_configuration(config, {"mo_desktop": overrides})
