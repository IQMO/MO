"""Skin-backed visual primitives shared by MO Desktop utility windows."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Any, Mapping


DEFAULT_PANEL_PADDING = 16
DEFAULT_PANEL_CORNER_RADIUS = 12
DEFAULT_BUTTON_PADDING = 8
DEFAULT_BUTTON_CORNER_RADIUS = 6
DEFAULT_WINDOW_EFFECT = "hybrid"
DEFAULT_WINDOW_EFFECT_INTENSITY = 55
DESKTOP_WINDOW_EFFECT_TYPES = ("none", "shadow", "glow", "hybrid")


# Fixed numeric radii are permitted only for geometry whose shape communicates
# structure rather than a panel/control skin role. The focused source guard
# keys every numeric ``radius=`` expression to this documented owner list.
DESKTOP_STRUCTURAL_RADIUS_WHITELIST = MappingProxyType({
    ("mo_desktop/cube.py", "_bar_sprite", "h // 2"):
        "the writer character's lines of text are pill-shaped",
    ("mo_desktop/cube_panel.py", "_render_glance_pill", "height // 2"):
        "notifier glance pill: fully rounded ends, half its own height",
    ("interface/desktop_brand.py", "make_four_cube_icon", "max(4, round(7 * scale))"):
        "circular tray-icon backplate",
    ("interface/desktop_brand.py", "make_four_cube_icon", "max(1, round(2 * scale))"):
        "four-cube brand glyph",
    ("interface/desktop_brand.py", "make_glyph_icon", "s * 0.07"):
        "file/folder/phone glyph detail",
    ("interface/desktop_brand.py", "make_glyph_icon", "s * 0.055"):
        "file/folder glyph detail",
    ("interface/desktop_brand.py", "make_glyph_icon", "s * 0.06"):
        "open/copy/delete action glyph detail",
    ("interface/desktop_brand.py", "make_glyph_icon", "s * 0.05"):
        "delete action glyph detail",
    ("mo_desktop/cube_motion.py", "_footstep", "max(0, size * corner) * ss"):
        "shared cube trace follows each actual piece's size and character radius",
    ("mo_desktop/reply_bubble.py", "_draw_search_brand", "round(2.5 * ss)"):
        "YouTube and Translate marks keep their brand tile rounding",
    ("mo_desktop/focus_paint.py", "_draw_row", "SS * 1.5"):
        "the active window's accent bar has round ends",
    ("mo_desktop/focus_paint.py", "cube_face", "2 * SS"):
        "thin dimming slider track has circular end caps",
    ("mo_desktop/mo_renderer.py", "panel", "radius * 2"):
        "active panel radius at the local 2x antialiasing pass",
    ("mo_desktop/mologrthim/app.py", "paint", "r * 3"):
        "active panel or button radius at Mologrthim's local 3x antialiasing pass",
    ("mo_desktop/dashboard_card.py", "render_dashboard_card", "2 * ss"):
        "four-cube brand glyph",
    ("mo_desktop/tray.py", "_render", "max(1, self.owner._visuals.metrics.button_corner_radius // 4)"):
        "tiny app-cube scroll indicator derives rounding from the active button radius",
    ("mo_desktop/reply_bubble.py", "_render", "3 * ss"):
        "checkbox/radio mark geometry",
    ("mo_desktop/reply_bubble.py", "_media_attachment_preview", "3 * ss"):
        "waveform bar geometry",
    ("mo_desktop/reply_bubble.py", "_image_attachment_preview", "6 * ss"):
        "noninteractive overflow-count badge",
    ("mo_desktop/reply_panel_tools.py", "_draw_panel_shortcut", "min(height // 2, int(self._visuals.metrics.button_corner_radius) * ss)"):
        "compact shortcut silhouette bounded by its height and active button radius",
})


# MO Design injects the four live geometry values at startup. Its trusted
# stylesheet is the only visual owner for the chrome; these are the only
# literal radius declarations permitted there, and each describes a glyph or
# intrinsically circular shape rather than a panel or control role.
DESKTOP_STRUCTURAL_CSS_RADIUS_WHITELIST = MappingProxyType({
    ("mo_desktop/design_studio/studio_visual.css", "--radius-glyph: max(1px,calc(var(--button-radius) * .22));"):
        "glyph-detail safety cap derived from the active button radius",
    ("mo_desktop/design_studio/studio_visual.css", "--radius-pill: 999px;"):
        "true noninteractive pill",
    ("mo_desktop/design_studio/studio_visual.css", "border-radius: var(--radius-glyph) var(--radius-glyph) 0 0;"):
        "folder-tab glyph silhouette",
    ("mo_desktop/design_studio/studio_visual.css", "border-radius: 50%;"):
        "brief bullet and route-selection circles",
    ("mo_desktop/files/board.css", "border-radius:2px;"):
        "folder glyph detail, tiny status dots, and progress/active-pane marks",
    ("mo_desktop/files/board.css", "border-radius:2px 2px 0 0;"):
        "folder-tab glyph silhouette",
    ("mo_desktop/files/board.css", "border-radius:50%;"):
        "circular actions and file-size orbs",
    ("mo_desktop/files/board.css", "border-radius:0;"):
        "flat file-list rows",
})


@dataclass(frozen=True)
class DesktopPalette:
    card: str
    accent: str
    text: str
    entry: str
    muted: str
    border: str
    ok: str
    warn: str
    error: str
    error_soft: str
    action: str


@dataclass(frozen=True)
class DesktopVisualMetrics:
    """Normalized user-controlled geometry shared by every Desktop surface."""

    panel_padding: int = DEFAULT_PANEL_PADDING
    panel_corner_radius: int = DEFAULT_PANEL_CORNER_RADIUS
    button_padding: int = DEFAULT_BUTTON_PADDING
    button_corner_radius: int = DEFAULT_BUTTON_CORNER_RADIUS


@dataclass(frozen=True)
class DesktopWindowEffects:
    """Normalized outside treatment shared by every MO-owned window."""

    style: str = DEFAULT_WINDOW_EFFECT
    intensity: int = DEFAULT_WINDOW_EFFECT_INTENSITY


@dataclass(frozen=True)
class DesktopSpacingSnapshot:
    """Immutable semantic spacing derived from one panel-padding value."""

    none: int
    hairline: int
    micro: int
    compact: int
    related: int
    section: int
    item: int
    control: int
    inset: int
    comfortable: int
    content: int
    relaxed: int
    roomy: int
    extended: int
    spacious: int
    wide: int
    full_screen: int
    expanded: int
    page: int
    page_wide: int


@dataclass(frozen=True)
class DesktopVisualState:
    """The one validated runtime skin and panel-geometry projection."""

    skin_id: str
    palette: DesktopPalette
    metrics: DesktopVisualMetrics
    effects: DesktopWindowEffects
    spacing: DesktopSpacingSnapshot
    tokens: tuple[tuple[str, Any], ...]

    def token(self, name: str) -> Any:
        for key, value in self.tokens:
            if key == name:
                return value
        raise DesktopVisualStateError(f"Desktop visual token is unavailable: {name}")


class DesktopVisualStateError(ValueError):
    """Raised when a Desktop skin/state cannot be projected completely."""


class DesktopVisualAdapterError(RuntimeError):
    """Raised when a supported visual adapter cannot honor the active state."""


def _bounded_metric(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not isfinite(number):
        return default
    return max(low, min(high, int(number)))


def desktop_visual_metrics(values: Any = None) -> DesktopVisualMetrics:
    """Project config/dataclass values through the one Desktop geometry contract."""
    if isinstance(values, DesktopVisualMetrics):
        return values

    def read(name: str, default: int) -> Any:
        if isinstance(values, Mapping):
            return values.get(name, default)
        return getattr(values, name, default)

    return DesktopVisualMetrics(
        panel_padding=_bounded_metric(
            read("padding", DEFAULT_PANEL_PADDING), DEFAULT_PANEL_PADDING, 8, 28,
        ),
        panel_corner_radius=_bounded_metric(
            read("corner_radius", DEFAULT_PANEL_CORNER_RADIUS),
            DEFAULT_PANEL_CORNER_RADIUS,
            0,
            30,
        ),
        button_padding=_bounded_metric(
            read("button_padding", DEFAULT_BUTTON_PADDING), DEFAULT_BUTTON_PADDING, 4, 16,
        ),
        button_corner_radius=_bounded_metric(
            read("button_corner_radius", DEFAULT_BUTTON_CORNER_RADIUS),
            DEFAULT_BUTTON_CORNER_RADIUS,
            0,
            16,
        ),
    )


def desktop_window_effects(values: Any = None) -> DesktopWindowEffects:
    """Project config/dataclass values through the one window-effect contract."""
    if isinstance(values, DesktopWindowEffects):
        return values

    def read(name: str, default: Any) -> Any:
        if isinstance(values, Mapping):
            return values.get(name, default)
        return getattr(values, name, default)

    style = str(read("window_effect", DEFAULT_WINDOW_EFFECT) or "").strip().lower()
    if style not in DESKTOP_WINDOW_EFFECT_TYPES:
        style = DEFAULT_WINDOW_EFFECT
    intensity = _bounded_metric(
        read("window_effect_intensity", DEFAULT_WINDOW_EFFECT_INTENSITY),
        DEFAULT_WINDOW_EFFECT_INTENSITY,
        0,
        100,
    )
    return DesktopWindowEffects(style=style, intensity=intensity)


class DesktopSpacing:
    """Read-only proxy onto the active state's immutable spacing snapshot."""

    _BASE = {
        "none": 0,
        "hairline": 1,
        "micro": 2,
        "compact": 3,
        "related": 4,
        "section": 5,
        "item": 6,
        "control": 7,
        "inset": 8,
        "comfortable": 9,
        "content": 10,
        "relaxed": 11,
        "roomy": 12,
        "extended": 13,
        "spacious": 14,
        "wide": 15,
        "full_screen": 16,
        "expanded": 18,
        "page": 20,
        "page_wide": 24,
    }

    def __getattr__(self, name: str) -> int:
        return getattr(active_desktop_visual_state().spacing, name)


def desktop_spacing_snapshot(
    metrics: DesktopVisualMetrics,
) -> DesktopSpacingSnapshot:
    scale = metrics.panel_padding / DEFAULT_PANEL_PADDING
    values: dict[str, int] = {}
    for name, base in DesktopSpacing._BASE.items():
        if base == 0:
            values[name] = 0
        elif base == 1:
            values[name] = 1
        else:
            values[name] = max(1, int(round(base * scale)))
    return DesktopSpacingSnapshot(**values)


DESKTOP_SPACING = DesktopSpacing()


@dataclass(frozen=True)
class DesktopTypography:
    """Shared font families and point sizes for Desktop visual adapters."""

    tiny: tuple[str, int] = ("Segoe UI", 8)
    small: tuple[str, int] = ("Segoe UI", 9)
    body: tuple[str, int] = ("Segoe UI", 10)
    body_large: tuple[str, int] = ("Segoe UI", 11)
    callout: tuple[str, int] = ("Segoe UI", 12)
    eyebrow: tuple[str, int] = ("Segoe UI Semibold", 8)
    section: tuple[str, int] = ("Segoe UI Semibold", 10)
    title: tuple[str, int] = ("Segoe UI Semibold", 12)
    heading: tuple[str, int] = ("Segoe UI Semibold", 15)
    mono: tuple[str, int] = ("Cascadia Mono", 10)

    def body_weight(self, bold: bool) -> tuple[str, int, str]:
        return (*self.body, "bold" if bold else "normal")


DESKTOP_TYPOGRAPHY = DesktopTypography()


_PALETTE_TOKEN_FIELDS = {
    "card": "CARD",
    "accent": "CYAN",
    "text": "TEXT",
    "entry": "_ENTRY_BG",
    "muted": "_MUTED",
    "border": "_BORDER",
    "ok": "_OK",
    "warn": "_WARN",
    "error": "_ERR",
    "error_soft": "_ERR_SOFT",
    "action": "_DO",
}


def palette_from_tokens(tokens: Mapping[str, Any]) -> DesktopPalette:
    """Project a complete canonical Desktop token mapping without aliases."""
    if not isinstance(tokens, Mapping):
        raise DesktopVisualStateError("Desktop skin tokens must be a mapping")
    missing = [
        token for token in _PALETTE_TOKEN_FIELDS.values()
        if not isinstance(tokens.get(token), str) or not str(tokens[token]).strip()
    ]
    if missing:
        raise DesktopVisualStateError(
            "Desktop skin is missing canonical tokens: " + ", ".join(missing)
        )
    invalid = []
    for token in _PALETTE_TOKEN_FIELDS.values():
        value = str(tokens[token]).strip()
        try:
            valid = len(value) == 7 and value.startswith("#") and int(value[1:], 16) >= 0
        except ValueError:
            valid = False
        if not valid:
            invalid.append(token)
    if invalid:
        raise DesktopVisualStateError(
            "Desktop skin has invalid color tokens: " + ", ".join(invalid)
        )
    values = {
        field_name: str(tokens[token_name]).strip()
        for field_name, token_name in _PALETTE_TOKEN_FIELDS.items()
    }
    return DesktopPalette(**values)


def require_desktop_tokens(
    tokens: Mapping[str, Any],
    names: tuple[str, ...],
) -> Mapping[str, Any]:
    """Validate a renderer's exact canonical token inputs without completing it."""
    if not isinstance(tokens, Mapping):
        raise DesktopVisualStateError("Desktop skin tokens must be a mapping")
    missing = [name for name in names if tokens.get(name) in (None, "")]
    if missing:
        raise DesktopVisualStateError(
            "Desktop skin is missing canonical tokens: " + ", ".join(missing)
        )
    return tokens


def desktop_visual_state(
    tokens: Mapping[str, Any],
    values: Any = None,
    *,
    skin_id: str = "",
) -> DesktopVisualState:
    required = tuple(_PALETTE_TOKEN_FIELDS.values()) + (
        "_CHROMA",
        "_GLOW",
        "_BRAND_RGB",
        "_CHROMA_RGB",
        "_AMBER_RGB",
    )
    missing = [
        name for name in required
        if tokens.get(name) in (None, "")
    ]
    if missing:
        raise DesktopVisualStateError(
            "Desktop state is missing canonical tokens: " + ", ".join(missing)
        )
    invalid_rgb = [
        name for name in ("_BRAND_RGB", "_CHROMA_RGB", "_AMBER_RGB")
        if not (
            isinstance(tokens.get(name), tuple)
            and len(tokens[name]) == 3
            and all(isinstance(value, int) and 0 <= value <= 255 for value in tokens[name])
        )
    ]
    if invalid_rgb:
        raise DesktopVisualStateError(
            "Desktop state has invalid RGB tokens: " + ", ".join(invalid_rgb)
        )
    metrics = desktop_visual_metrics(values)
    effects = desktop_window_effects(values)
    return DesktopVisualState(
        skin_id=str(skin_id or "").strip(),
        palette=palette_from_tokens(tokens),
        metrics=metrics,
        effects=effects,
        spacing=desktop_spacing_snapshot(metrics),
        tokens=tuple(sorted((str(name), value) for name, value in tokens.items())),
    )


_ACTIVE_DESKTOP_VISUAL_STATE: DesktopVisualState | None = None


def activate_desktop_visual_state(
    state: DesktopVisualState | None = None,
    *,
    tokens: Mapping[str, Any] | None = None,
    values: Any = None,
    skin_id: str = "",
) -> DesktopVisualState:
    """Atomically activate one already-complete Desktop visual projection."""
    global _ACTIVE_DESKTOP_VISUAL_STATE
    if state is None:
        if tokens is None:
            from interface.theming import get_skin_name, skin_to_desktop_vars

            tokens = skin_to_desktop_vars()
            skin_id = skin_id or get_skin_name()
        state = desktop_visual_state(tokens, values, skin_id=skin_id)
    if not isinstance(state, DesktopVisualState):
        raise DesktopVisualStateError("Desktop visual state has an invalid type")
    _ACTIVE_DESKTOP_VISUAL_STATE = state
    return state


def active_desktop_visual_state() -> DesktopVisualState:
    state = _ACTIVE_DESKTOP_VISUAL_STATE
    return state if state is not None else activate_desktop_visual_state()


def desktop_palette() -> DesktopPalette:
    """Return the exact palette from the one active Desktop visual state."""
    return active_desktop_visual_state().palette


__all__ = [
    "DesktopPalette",
    "DesktopSpacing",
    "DesktopSpacingSnapshot",
    "DesktopTypography",
    "DesktopVisualAdapterError",
    "DesktopVisualMetrics",
    "DesktopVisualState",
    "DesktopVisualStateError",
    "DesktopWindowEffects",
    "DESKTOP_WINDOW_EFFECT_TYPES",
    "DESKTOP_SPACING",
    "DESKTOP_TYPOGRAPHY",
    "DEFAULT_BUTTON_CORNER_RADIUS",
    "DEFAULT_BUTTON_PADDING",
    "DEFAULT_PANEL_CORNER_RADIUS",
    "DEFAULT_PANEL_PADDING",
    "DEFAULT_WINDOW_EFFECT",
    "DEFAULT_WINDOW_EFFECT_INTENSITY",
    "DESKTOP_STRUCTURAL_RADIUS_WHITELIST",
    "DESKTOP_STRUCTURAL_CSS_RADIUS_WHITELIST",
    "activate_desktop_visual_state",
    "active_desktop_visual_state",
    "desktop_palette",
    "desktop_spacing_snapshot",
    "desktop_visual_state",
    "desktop_visual_metrics",
    "desktop_window_effects",
    "palette_from_tokens",
    "require_desktop_tokens",
]
