"""MO theming system — single source of truth for all interface colors.

Architecture
    * Skin dataclass holds all semantic color tokens (one source of truth).
    * Built-in skins: MO_DEFAULT (current MO palette), MO_DRACULA (Dracula-inspired).
    * Bridge helpers map skin tokens → surface-specific formats (TUI, UX, Ghost, etc.).
    * Switch at runtime with ``set_skin("default" | "dracula")``.
    * Adding a new skin: define a Skin instance, register it in ``_SKINS``.

Dynamic / animated colors (e.g. moon glow pulse) are parameterised from the
active skin and recomputed at render time — they are NOT static tokens.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Skin definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Skin:
    """Semantic color tokens used by every MO surface."""

    # ── Backgrounds ──
    bg_deepest: str          # darkest bg (chroma key, deepest panels)
    bg_dark: str             # main viewport background
    bg_surface: str          # cards, panels, raised surfaces
    bg_input: str            # text input field background

    # ── Text ──
    text_primary: str        # body text / white-ish
    text_secondary: str      # secondary / body text
    text_dim: str            # dimmed / inactive text (footer, completed tasks)
    text_muted: str          # muted / hint text
    text_bright: str         # brightest text (headings, highlights)
    text_placeholder: str    # input placeholder
    text_code: str           # inline code / monospace

    # ── Borders ──
    border_default: str      # standard panel separators
    border_focus: str        # focused / active border

    # ── Brand ──
    brand_primary: str       # main MO brand colour (cyan in default)
    brand_glow: str          # luminous variant for glow / pulse effects

    # ── Accents ──
    accent_blue: str
    accent_green: str        # success, learning confirmations
    accent_amber: str        # warnings, low-balance, goal running
    accent_warning: str      # highlight amber for notifications / PRT minor
    accent_red: str          # errors, blocked, critical
    accent_red_soft: str     # softer red for non-critical blocked states
    accent_critical: str     # bright urgent red for critical notifications
    accent_purple: str       # PRT, ghost routing
    accent_yellow: str       # highlights, PRT "major"
    accent_info: str         # info cyan / teal (goal detail, PRT info)

    # ── Status semantic ──
    status_done: str         # completed task green
    status_active: str       # active / running amber
    status_blocked: str      # blocked red
    status_pending: str      # pending muted

    # ── Surface-specific overrides ──
    user_msg_bg: str         # user message bubble background
    selected_bg: str         # palette / list selection background
    palette_desc: str        # palette description text
    palette_hint: str        # palette hint text
    ghost_frame: str         # ghost panel border
    ghost_gap: str           # ghost gap filler (usually pure black)
    ghost_response: str      # ghost response body text
    response_text: str       # main MO response body text
    response_subtle: str     # bullet rest / secondary response text
    spinner: str             # spinner colour

    # ── Code map (HTML) ──
    code_map_line: str       # graph edges / panel borders
    code_map_badge_bg: str   # badge / chip background

    # ── Animated effects (base values) ──
    moon_glow_red: int = 255
    moon_glow_green: int = 180
    moon_glow_blue: int = 0

    @property
    def hex_bg_deepest_rgb(self) -> tuple[int, int, int]:
        return _hex_to_rgb(self.bg_deepest)

    @property
    def hex_brand_primary_rgb(self) -> tuple[int, int, int]:
        return _hex_to_rgb(self.brand_primary)


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


# ---------------------------------------------------------------------------
# Built-in skins
# ---------------------------------------------------------------------------

MO_DEFAULT = Skin(
    # Backgrounds
    bg_deepest="#010101",
    bg_dark="#090b10",
    bg_surface="#111722",
    bg_input="#0b1228",
    # Text
    text_primary="#d7dee8",
    text_secondary="#bbccdd",
    text_dim="#666666",
    text_muted="#7d8996",
    text_bright="#ffffff",
    text_placeholder="#555555",
    text_code="#a0c4ff",
    # Borders
    border_default="#314253",
    border_focus="#3f6dd9",
    # Brand
    brand_primary="#00cccc",
    brand_glow="#3fe0e0",
    # Accents
    accent_blue="#7aa2ff",
    accent_green="#68d391",
    accent_amber="#f6ad55",
    accent_warning="#ffae42",
    accent_red="#fc8181",
    accent_red_soft="#cc4444",
    accent_critical="#ff4444",
    accent_purple="#bb86fc",
    accent_yellow="#ffd166",
    accent_info="#66d9ef",
    # Status semantic
    status_done="#00cc88",
    status_active="#ddaa00",
    status_blocked="#cc4444",
    status_pending="#666666",
    # Surface-specific
    user_msg_bg="#1a3a4a",
    selected_bg="#005f5f",
    palette_desc="#bbbbbb",
    palette_hint="#777777",
    ghost_frame="#00cccc",
    ghost_gap="#000000",
    ghost_response="#8fa7b8",
    response_text="#bbccdd",
    response_subtle="#8a949e",
    spinner="#dddddd",
    # Code map
    code_map_line="#26365f",
    code_map_badge_bg="#15224a",
)

MO_DRACULA = Skin(
    # Backgrounds — dark purple-grey (lifted from near-black to readable grey)
    bg_deepest="#22232b",
    bg_dark="#3b3d4b",
    bg_surface="#4a4c5a",
    bg_input="#4a4c5a",
    # Text
    text_primary="#f8f8f2",
    text_secondary="#d6d6d0",
    text_dim="#4b5268",
    text_muted="#6272a4",
    text_bright="#ffffff",
    text_placeholder="#4b5268",
    text_code="#8be9fd",
    # Borders
    border_default="#44475a",
    border_focus="#bd93f9",
    # Brand — Dracula cyan
    brand_primary="#8be9fd",
    brand_glow="#a4f0ff",
    # Accents
    accent_blue="#8be9fd",
    accent_green="#50fa7b",
    accent_amber="#ffb86c",
    accent_warning="#ffcc80",
    accent_red="#ff5555",
    accent_red_soft="#e04444",
    accent_critical="#ff5555",
    accent_purple="#bd93f9",
    accent_yellow="#f1fa8c",
    accent_info="#8be9fd",
    # Status semantic
    status_done="#50fa7b",
    status_active="#ffb86c",
    status_blocked="#ff5555",
    status_pending="#4b5268",
    # Surface-specific
    user_msg_bg="#343746",
    selected_bg="#44475a",
    palette_desc="#bd93f9",
    palette_hint="#6272a4",
    ghost_frame="#8be9fd",
    ghost_gap="#191a21",
    ghost_response="#d6d6d0",
    response_text="#d6d6d0",
    response_subtle="#6272a4",
    spinner="#bd93f9",
    # Code map
    code_map_line="#44475a",
    code_map_badge_bg="#343746",
    # Moon glow — Dracula purple pulse
    moon_glow_red=189,
    moon_glow_green=147,
    moon_glow_blue=249,
)


# ---------------------------------------------------------------------------
# Skin registry + switching
# ---------------------------------------------------------------------------

_SKINS: dict[str, Skin] = {
    "default": MO_DEFAULT,
    "dracula": MO_DRACULA,
}
_SKIN_FILE: str = ""  # set at init time

_ACTIVE: str = "default"


def get_skin() -> Skin:
    """Return the currently active skin."""
    return _SKINS[_ACTIVE]


def get_skin_name() -> str:
    """Return the name (key) of the currently active skin."""
    return _ACTIVE


def set_skin(name: str) -> Skin:
    """Switch to a named skin. Returns the new skin."""
    global _ACTIVE
    if name not in _SKINS:
        raise KeyError(f"Unknown skin {name!r}. Available: {list(_SKINS)}")
    _ACTIVE = name
    _save_persisted_skin()
    return _SKINS[name]


def available_skins() -> list[str]:
    """Return the names of all registered skins."""
    return list(_SKINS)


def _init_skin_file() -> None:
    """Resolve the persisted skin preference file at import time."""
    global _SKIN_FILE
    import os
    _SKIN_FILE = os.path.expanduser("~/.mo/skin")


def _load_persisted_skin() -> None:
    """Load the persisted skin preference (called once at import)."""
    global _ACTIVE
    try:
        name = open(_SKIN_FILE, encoding="utf-8").read().strip()
        if name in _SKINS:
            _ACTIVE = name
    except (OSError, FileNotFoundError):
        pass


def _save_persisted_skin() -> None:
    """Persist the active skin name so it survives restarts."""
    import os
    os.makedirs(os.path.dirname(_SKIN_FILE), exist_ok=True)
    with open(_SKIN_FILE, "w", encoding="utf-8") as f:
        f.write(_ACTIVE)


_init_skin_file()
_load_persisted_skin()


# ---------------------------------------------------------------------------
# Bridge helpers — map Skin → surface-specific formats
# ---------------------------------------------------------------------------

def skin_to_tui_style_dict(skin: Skin | None = None) -> dict[str, str]:
    """Return a ``TUI_STYLE_DICT``-compatible dictionary from *skin*."""
    s = skin or get_skin()
    b = s.brand_primary       # short alias
    return {
        "separator": s.text_placeholder,
        "footer": s.text_dim,
        "spinner": s.spinner,
        "activity": f"{b} bold",
        "goal-detail": f"{s.accent_info} bold",
        "task-done": s.text_dim,
        "task-active": s.status_active,
        "task-blocked": s.status_blocked,
        "task-pending": s.text_dim,
        "task-info": s.text_muted,
        "logo": f"{b} bold",
        "user-msg": f"bg:{s.user_msg_bg} {s.spinner}",
        "mo-marker": f"{b} bold",
        "mo-response": s.response_text,
        "response-heading": f"{b} bold",
        "response-bullet-marker": b,
        "response-bullet-head": f"{s.text_bright} bold",
        "response-bullet-rest": s.response_subtle,
        "response-code": f"{s.text_code} italic",
        "palette-title": f"{b} bold",
        "palette-category": b,
        "palette-selected": f"bg:{s.selected_bg} {s.text_bright} bold",
        "palette-command": f"{b} bold",
        "palette-desc": s.palette_desc,
        "palette-hint": s.palette_hint,
        "ghost-frame": s.ghost_frame,
        "ghost-hint": s.text_muted,
        "ghost-user": f"bg:{s.selected_bg} {s.text_bright}",
        "ghost-thinking": f"{s.spinner} italic",
        "ghost-gap": s.ghost_gap,
        "ghost-response": s.ghost_response,
        "ghost-route": f"{s.accent_purple} bold",
        "ghost-route-blocked": f"{s.accent_red_soft} bold",
        "dim": s.text_dim,
        "diff-add": s.accent_green,
        "diff-del": s.accent_red,
        "reasoning": f"{s.text_dim} italic",
        "info": b,
        "input-placeholder": f"{s.text_placeholder} italic",
        "notification-idle": f"{b} italic",
        "notification-learning": f"{s.accent_green} italic",
        "notification-prt": f"{s.accent_purple} bold",
        "notification-goal": f"{s.accent_warning} bold",
        "notification-worker": s.text_muted,
        "notification-critical": f"{s.accent_critical} bold",
        "low-balance": f"{s.accent_warning} bold",
        "model-fallback": f"{s.accent_warning} bold",
        "prt-header": f"{s.accent_purple} bold",
        "prt-critical": f"{s.accent_critical} bold",
        "prt-major": f"{s.accent_yellow} bold",
        "prt-minor": s.accent_warning,
        "prt-info": s.accent_info,
        "prt-clean": f"{s.status_done} bold",
        "prt-summary": s.palette_desc,
    }


def skin_to_ux_theme(skin: Skin | None = None) -> Any:
    """Return a ``UxTheme`` instance (UX/render/theme.py) from *skin*."""
    from UX.render.theme import UxTheme  # local import — UX surface may not be loaded
    s = skin or get_skin()
    return UxTheme(
        background=s.bg_dark,
        surface=s.bg_surface,
        border=s.border_default,
        text=s.text_primary,
        muted=s.text_muted,
        brand=s.brand_primary,
        blue=s.accent_blue,
        green=s.accent_green,
        amber=s.accent_amber,
        red=s.accent_red,
        violet=s.accent_purple,
    )


def skin_to_ghost_vars(skin: Skin | None = None) -> dict[str, str | tuple[int, int, int]]:
    """Return the module-level colour constants used by the desktop Ghost.

    Keys: CYAN, CARD, TEXT, _ENTRY_BG, _MUTED, _BORDER, _LISTEN,
          _CHROMA, _MOON, _MOON_DARK, _GLOW, _CHROMA_RGB, _MOON_RGB, _DARK_RGB.
    """
    s = skin or get_skin()
    # _MOON_DARK = shadowed limb of crescent (artistic value, darker than bg_deepest)
    # _DARK_RGB = lerp target for moon rim animation (slightly lighter than _MOON_DARK)
    moon_dark = "#04141a" if s is MO_DEFAULT else _darken(s.bg_surface, 0.35)
    dark_rgb_hex = "#0b1418" if s is MO_DEFAULT else _mix(s.bg_surface, s.bg_deepest, 0.5)
    return {
        "CYAN": s.brand_primary,
        "CARD": s.bg_deepest,
        "TEXT": s.text_bright,
        "_ENTRY_BG": s.bg_input,
        "_MUTED": s.text_muted,
        "_BORDER": s.border_default,
        "_LISTEN": s.accent_purple,
        "_CHROMA": s.bg_deepest,
        "_MOON": s.brand_primary,
        "_MOON_DARK": moon_dark,
        "_GLOW": s.brand_glow,
        # RGB tuples for PIL / tkinter
        "_CHROMA_RGB": s.hex_bg_deepest_rgb,
        "_MOON_RGB": s.hex_brand_primary_rgb,
        "_LISTEN_RGB": _hex_to_rgb(s.accent_purple),
        "_DARK_RGB": _hex_to_rgb(dark_rgb_hex),
    }


def skin_to_shell_style_dict(skin: Skin | None = None) -> dict[str, str]:
    """Return a shell TUI style dict (UX/shell/tui.py format)."""
    s = skin or get_skin()
    return {
        "logo": f"{s.accent_blue} bold",
        "signal-faint": _darken(s.bg_dark, 0.6),
        "signal-dim": _mix(s.bg_dark, s.accent_info, 0.3),
        "signal-mid": _mix(s.bg_dark, s.accent_info, 0.6),
        "signal-hot": f"{s.accent_blue} bold",
        "signal-core": f"{s.accent_amber} bold",
        "border": s.border_focus,
        "title": f"{s.accent_blue} bold",
        "hint": f"{s.accent_blue} bold",
        "rule": s.accent_blue,
        "prompt": f"{s.brand_primary} bold",
        "placeholder": s.text_muted,
        "section": f"{s.accent_blue} bold",
        "chip": f"bg:{_dim(s.bg_input, 0.3)} {s.accent_blue} bold",
        "brand": f"{s.brand_primary} bold",
        "blue": f"{s.accent_blue} bold",
        "green": f"{s.accent_green} bold",
        "amber": f"{s.accent_amber} bold",
        "yellow": f"{s.accent_yellow} bold",
        "red": f"{s.accent_red} bold",
        "mo": f"{s.brand_primary} bold",
    }


def skin_to_code_map_css(skin: Skin | None = None) -> str:
    """Return CSS ``:root`` variables + core rules for the code map viewer."""
    s = skin or get_skin()
    return f""":root{{
  --bg:{s.bg_dark};
  --panel:{_rgba(s.bg_deepest, 0.86)};
  --line:{s.code_map_line};
  --text:{s.text_primary};
  --muted:{s.text_muted};
  --cyan:{s.brand_glow};
  --gold:{s.accent_yellow};
  --pink:{s.accent_purple};
  --green:{s.accent_green};
}}
#canvas{{
  display:block;width:100vw;height:100vh;
  background:radial-gradient(circle at center,{_mix(s.bg_dark, s.brand_primary, 0.15)} 0,{s.bg_dark} 62%,{_darken(s.bg_deepest, 0.8)} 100%)
}}
.panel{{
  position:fixed;background:var(--panel);border:1px solid var(--line);
  border-radius:14px;box-shadow:0 0 30px {_rgba(s.brand_primary, 0.14)};
  backdrop-filter:blur(8px)
}}
.badge{{
  display:inline-block;padding:2px 8px;border-radius:999px;
  background:{s.code_map_badge_bg};color:{_tint(s.text_primary, s.accent_blue, 0.6)};font-size:11px
}}
.badge.gold{{background:{_dim(s.accent_yellow, 0.12)};color:var(--gold)}}
.badge.pink{{background:{_dim(s.accent_purple, 0.15)};color:var(--pink)}}
.badge.green{{background:{_dim(s.accent_green, 0.15)};color:var(--green)}}
.badge.dim{{opacity:.55}}
#search{{
  width:100%;box-sizing:border-box;background:{s.bg_input};
  border:1px solid var(--line);border-radius:8px;color:#fff;padding:7px 10px;
  font-size:12px;outline:none
}}
#search:focus{{border-color:{s.border_focus}}}
.chip{{
  cursor:pointer;user-select:none;padding:3px 10px;border-radius:999px;
  border:1px solid var(--line);background:{s.bg_input};color:var(--muted);font-size:11px
}}
.chip.on{{background:{s.code_map_badge_bg};color:{_tint(s.text_primary, s.accent_blue, 0.7)};border-color:{s.border_focus}}}
.section-title{{font-size:10px;color:var(--cyan);text-transform:uppercase;letter-spacing:.8px;margin:4px 0 2px}}
.grow:hover,.grow.sel{{background:{_dim(s.code_map_badge_bg, 0.6)}}}
.witem:hover,.witem.sel{{background:{_dim(s.code_map_badge_bg, 0.6)};border-color:{s.code_map_line}}}
.witem .t{{font-size:12px;color:{_tint(s.text_primary, s.text_bright, 0.8)};overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.witem .m{{font-size:10px;color:var(--muted)}}
#info h3{{margin:0 0 6px;font-size:15px;color:#fff;word-break:break-all}}
"""


def skin_overlay_vars(skin: Skin | None = None) -> dict[str, str]:
    """Return the colour constants for screen_overlay.py."""
    s = skin or get_skin()
    return {
        "CYAN": s.brand_primary,
        "CARD": s.bg_deepest,
        "TEXT": s.text_bright,
    }


# ---------------------------------------------------------------------------
# Colour arithmetic helpers
# ---------------------------------------------------------------------------

def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _darken(hex_color: str, factor: float) -> str:
    """Scale down toward black by *factor* (0.0 = no change, 1.0 = black)."""
    r, g, b = _hex_to_rgb(hex_color)
    scale = max(0.0, min(1.0, 1.0 - factor))
    return _rgb_to_hex(int(r * scale), int(g * scale), int(b * scale))


def _dim(hex_color: str, factor: float) -> str:
    """Darken while preserving hue (lower lightness)."""
    r, g, b = _hex_to_rgb(hex_color)
    scale = max(0.0, min(1.0, 1.0 - factor))
    return _rgb_to_hex(int(r * scale), int(g * scale), int(b * scale))


def _mix(a: str, b: str, t: float) -> str:
    """Linear interpolation between two hex colours."""
    ar, ag, ab = _hex_to_rgb(a)
    br, bg, bb = _hex_to_rgb(b)
    t = max(0.0, min(1.0, t))
    return _rgb_to_hex(
        int(ar + (br - ar) * t),
        int(ag + (bg - ag) * t),
        int(ab + (bb - ab) * t),
    )


def _tint(base: str, tint: str, factor: float) -> str:
    """Mix *tint* into *base* by *factor*."""
    return _mix(base, tint, factor)


def _rgba(hex_color: str, alpha: float) -> str:
    """Return an rgba() string from a hex colour."""
    r, g, b = _hex_to_rgb(hex_color)
    return f"rgba({r},{g},{b},{alpha:.2f})"


# ---------------------------------------------------------------------------
# Hot-reload bridge for moon_visuals.py
# ---------------------------------------------------------------------------

def get_moon_glow_base() -> tuple[int, int, int]:
    """Return (r, g, b) base values for the moon glow animation."""
    s = get_skin()
    return (s.moon_glow_red, s.moon_glow_green, s.moon_glow_blue)
