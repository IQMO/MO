"""MO theming registry, persistence, and bridge helpers.

Built-ins remain immutable in :mod:`interface.skins`. User-created skins are
validated into the same ``Skin`` model and joined to that registry here, so all
Terminal, Desktop, Everywhere, and code-map projections keep one owner.
"""
from __future__ import annotations

from dataclasses import asdict, fields, replace
import json
import re
from types import MappingProxyType
from typing import Any, Mapping

from .skins import BUILTIN_SKINS, Skin


# ---------------------------------------------------------------------------
# Skin registry + switching
# ---------------------------------------------------------------------------

_SKIN_FILE: str = ""  # set at init time
_CUSTOM_SKIN_FILE: str = ""
_ACTIVE: str = "default"
_CUSTOM_SKINS: dict[str, Skin] = {}
_CUSTOM_SKIN_LABELS: dict[str, str] = {}
_CUSTOM_PALETTE_KEYS = (
    "background", "surface", "accent", "text", "separator",
    "user_message_background", "user_message", "mo_response", "ok", "warn", "error",
)


def _skin_registry() -> dict[str, Skin]:
    return {**BUILTIN_SKINS, **_CUSTOM_SKINS}


def get_skin() -> Skin:
    """Return the currently active built-in or custom skin."""
    return _skin_registry()[_ACTIVE]


def get_skin_name() -> str:
    """Return the stable ID of the currently active skin."""
    return _ACTIVE


def skin_display_name(name: str) -> str:
    """Return the user-facing name without weakening stable registry IDs."""
    if name in _CUSTOM_SKIN_LABELS:
        return _CUSTOM_SKIN_LABELS[name]
    return str(name).replace("_", " ").title()


def is_custom_skin(name: str) -> bool:
    return name in _CUSTOM_SKINS


def set_skin(name: str) -> Skin:
    """Switch to a named skin and persist the selection."""
    global _ACTIVE
    registry = _skin_registry()
    if name not in registry:
        raise KeyError(f"Unknown skin {name!r}. Available: {list(registry)}")
    _ACTIVE = name
    _save_persisted_skin()
    return registry[name]


def refresh_skin_from_disk() -> bool:
    """Reload custom skins and the persisted preference; report visual change."""
    before = (_ACTIVE, get_skin())
    _load_custom_skins()
    _load_persisted_skin()
    return (_ACTIVE, get_skin()) != before


def available_skins() -> list[str]:
    """Return stable IDs for all built-in and custom skins."""
    return list(_skin_registry())


def available_skin_items() -> tuple[tuple[str, Skin], ...]:
    """Return ordered built-in then custom skin pairs for selectors/previews."""
    return tuple(_skin_registry().items())


def registered_skins() -> Mapping[str, Skin]:
    """Return the immutable built-ins or a complete immutable registry snapshot."""
    if not _CUSTOM_SKINS:
        return BUILTIN_SKINS
    return MappingProxyType(_skin_registry())


def skin_palette(skin: Skin | None = None) -> dict[str, str]:
    """Return the semantic Skin roles exposed by the Appearance preview."""
    s = skin or get_skin()
    return {
        "background": s.bg_dark,
        "surface": s.bg_surface,
        "accent": s.brand_primary,
        "text": s.text_bright,
        "separator": s.text_placeholder,
        "user_message_background": s.user_msg_bg,
        "user_message": s.spinner,
        "mo_response": s.response_text,
        "ok": s.accent_green,
        "warn": s.accent_warning,
        "error": s.accent_critical,
    }


def build_custom_skin(colors: Mapping[str, str], *, base: Skin | None = None) -> Skin:
    """Project editable roles back into the complete shared Skin model."""
    source = base or get_skin()
    palette = skin_palette(source)
    for key, value in colors.items():
        if key not in _CUSTOM_PALETTE_KEYS:
            raise KeyError(f"Unknown custom skin color role {key!r}")
        if not isinstance(value, str) or re.fullmatch(r"#[0-9a-fA-F]{6}", value) is None:
            raise ValueError(f"Custom skin role {key} must be #RRGGBB")
        palette[key] = value.lower()
    background, surface = palette["background"], palette["surface"]
    accent, text = palette["accent"], palette["text"]
    separator = palette["separator"] if "separator" in colors else _mix(text, surface, 0.48)
    user_message_background = palette["user_message_background"]
    user_message, mo_response = palette["user_message"], palette["mo_response"]
    ok, warn, error = palette["ok"], palette["warn"], palette["error"]
    muted = _mix(text, surface, 0.55)
    border = _mix(surface, text, 0.22)
    return replace(
        source,
        bg_deepest=_darken(background, 0.62), bg_dark=background,
        bg_surface=surface, bg_input=_mix(surface, background, 0.25),
        text_primary=_mix(text, surface, 0.12), text_secondary=_mix(text, surface, 0.25),
        text_dim=muted, text_muted=muted, text_bright=text,
        text_placeholder=separator, text_code=_mix(text, accent, 0.35),
        border_default=border, border_focus=accent,
        brand_primary=accent, brand_glow=_mix(accent, text, 0.22),
        accent_blue=accent, accent_green=ok, accent_amber=warn,
        accent_warning=warn, accent_red=error, accent_red_soft=_darken(error, 0.18),
        accent_critical=error, accent_purple=_mix(accent, error, 0.35),
        accent_yellow=warn, accent_info=accent,
        status_done=ok, status_active=warn, status_blocked=error, status_pending=muted,
        user_msg_bg=user_message_background, selected_bg=_mix(surface, accent, 0.28),
        palette_desc=muted, palette_hint=muted, desktop_frame=accent,
        desktop_gap=_darken(background, 0.78), desktop_response=muted,
        response_text=mo_response, response_subtle=_mix(mo_response, surface, 0.35),
        spinner=user_message,
        code_map_line=border, code_map_badge_bg=surface,
        dark_rgb_override=_mix(surface, background, 0.55),
    )


def save_custom_skin(label: str, colors: Mapping[str, str], *, base: Skin | None = None) -> str:
    """Create and persist one validated user skin, returning its stable ID."""
    clean_label = re.sub(r"[\r\n]+", " ", str(label or "")).strip()[:40]
    if not clean_label:
        raise ValueError("Custom skin name cannot be blank")
    if any(existing.casefold() == clean_label.casefold() for existing in _CUSTOM_SKIN_LABELS.values()):
        raise ValueError("A custom skin with that name already exists")
    stem = re.sub(r"[^a-z0-9]+", "-", clean_label.casefold()).strip("-") or "custom"
    candidate = f"custom-{stem}"
    suffix = 2
    while candidate in _skin_registry():
        candidate = f"custom-{stem}-{suffix}"
        suffix += 1
    skin = build_custom_skin(colors, base=base)
    _CUSTOM_SKINS[candidate] = skin
    _CUSTOM_SKIN_LABELS[candidate] = clean_label
    try:
        _save_custom_skins()
    except Exception:
        _CUSTOM_SKINS.pop(candidate, None)
        _CUSTOM_SKIN_LABELS.pop(candidate, None)
        raise
    return candidate


def delete_custom_skin(name: str) -> None:
    """Delete a user skin; built-ins are deliberately immutable."""
    global _ACTIVE
    if name in BUILTIN_SKINS:
        raise ValueError("Built-in skins cannot be deleted")
    if name not in _CUSTOM_SKINS:
        raise KeyError(f"Unknown custom skin {name!r}")
    previous_active = _ACTIVE
    skin = _CUSTOM_SKINS.pop(name)
    label = _CUSTOM_SKIN_LABELS.pop(name)
    if previous_active == name:
        _ACTIVE = "default"
    try:
        _save_custom_skins()
        if previous_active == name:
            _save_persisted_skin()
    except Exception:
        _CUSTOM_SKINS[name] = skin
        _CUSTOM_SKIN_LABELS[name] = label
        _ACTIVE = previous_active
        try:
            _save_custom_skins()
            _save_persisted_skin()
        except Exception:
            pass
        raise


def chart_palette(skin: Skin | None = None) -> tuple[str, ...]:
    """Cohesive data-series colours drawn from the ACTIVE skin.

    Multi-series visuals (bar charts, etc.) should read as one designed palette
    that belongs to MO's skin — brand teal first, then the skin's accent family
    in a cool→warm order — not a clashing generic rainbow. Because it derives
    from the skin, switching skins (e.g. Dracula) re-themes every chart for free.
    """
    s = skin or get_skin()
    return (
        s.brand_glow,     # luminous teal (brand)
        s.accent_info,    # cyan / teal
        s.accent_blue,
        s.accent_green,
        s.accent_purple,
        s.accent_amber,   # single warm highlight to close the ramp
    )


def _init_skin_files() -> None:
    """Resolve both small skin state files from the canonical state owner."""
    global _SKIN_FILE, _CUSTOM_SKIN_FILE
    from core.state.paths import resolve_state_path

    _SKIN_FILE = resolve_state_path("skin")
    _CUSTOM_SKIN_FILE = resolve_state_path("skins.json")


def _load_custom_skins() -> None:
    """Replace the in-memory custom registry with validated persisted records."""
    loaded_skins: dict[str, Skin] = {}
    loaded_labels: dict[str, str] = {}
    try:
        with open(_CUSTOM_SKIN_FILE, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        payload = {}
    records = payload.get("skins", {}) if isinstance(payload, dict) else {}
    if isinstance(records, dict):
        valid_fields = {field.name for field in fields(Skin)}
        for skin_id, record in records.items():
            if not isinstance(skin_id, str) or not skin_id.startswith("custom-"):
                continue
            if not isinstance(record, dict) or not isinstance(record.get("tokens"), dict):
                continue
            tokens = record["tokens"]
            if set(tokens) != valid_fields:
                continue
            try:
                skin = Skin(**tokens)
            except (TypeError, ValueError):
                continue
            label = re.sub(r"[\r\n]+", " ", str(record.get("label") or "")).strip()[:40]
            if not label:
                continue
            loaded_skins[skin_id] = skin
            loaded_labels[skin_id] = label
    _CUSTOM_SKINS.clear()
    _CUSTOM_SKINS.update(loaded_skins)
    _CUSTOM_SKIN_LABELS.clear()
    _CUSTOM_SKIN_LABELS.update(loaded_labels)


def _load_persisted_skin() -> None:
    """Load the active preference after the complete registry is available."""
    global _ACTIVE
    try:
        name = open(_SKIN_FILE, encoding="utf-8").read().strip()
    except OSError:
        name = ""
    _ACTIVE = name if name in _skin_registry() else "default"


def _save_custom_skins() -> None:
    from core.utils.atomic_write import atomic_write_json

    atomic_write_json(
        _CUSTOM_SKIN_FILE,
        {
            "version": 1,
            "skins": {
                skin_id: {"label": _CUSTOM_SKIN_LABELS[skin_id], "tokens": asdict(skin)}
                for skin_id, skin in _CUSTOM_SKINS.items()
            },
        },
        indent=2,
        ensure_ascii=False,
    )


def _save_persisted_skin() -> None:
    """Atomically persist the active skin ID so it survives restarts."""
    from core.utils.atomic_write import atomic_write_text

    atomic_write_text(_SKIN_FILE, _ACTIVE, encoding="utf-8")


_init_skin_files()
_load_custom_skins()
_load_persisted_skin()


# ---------------------------------------------------------------------------
# Bridge helpers — map Skin → surface-specific formats
# ---------------------------------------------------------------------------

def skin_to_tui_style_dict(skin: Skin | None = None) -> dict[str, str]:
    """Return the prompt-toolkit style dictionary for *skin*."""
    s = skin or get_skin()
    b = s.brand_primary       # short alias
    return {
        "app-bg": f"bg:{s.bg_dark} {s.text_primary}",
        "surface-bg": f"bg:{s.bg_surface} {s.text_primary}",
        "input-bg": f"bg:{s.bg_input} {s.text_primary}",
        "separator": s.text_placeholder,
        "footer": s.text_dim,
        "spinner": s.spinner,
        "activity": f"{b} bold",
        "status-done": f"{s.status_done} bold",
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
        "completion-menu": f"bg:{s.bg_surface} {s.text_primary}",
        "completion-menu.completion": f"bg:{s.bg_surface} {s.text_primary}",
        "completion-menu.completion.current": f"bg:{s.selected_bg} {s.text_bright} bold",
        "completion-menu.meta.completion": f"bg:{s.bg_surface} {s.text_muted}",
        "completion-menu.meta.completion.current": f"bg:{s.selected_bg} {s.text_bright}",
        "completion-menu.multi-column-meta": f"bg:{s.bg_surface} {s.text_muted}",
        "desktop-frame": s.desktop_frame,
        "dim": s.text_dim,
        "diff-add": s.accent_green,
        "diff-del": s.accent_red,
        "reasoning": f"{s.text_dim} italic",
        "pending-steer": f"bg:{s.user_msg_bg} {s.spinner} italic",
        "info": b,
        "tool-chip": f"{s.accent_blue} bold",  # bracketed tool label, fg-only (no bg → terminal-safe)
        "input-placeholder": f"{s.text_placeholder} italic",
        "notification-idle": f"{b} italic",
        "notification-learning": f"{s.accent_green} italic",
        "notification-prt": f"{b} bold",
        "notification-goal": f"{s.accent_warning} bold",
        "notification-worker": s.text_muted,
        "notification-critical": f"{s.accent_critical} bold",
        "low-balance": f"{s.accent_warning} bold",
        "model-fallback": f"{s.accent_warning} bold",
        "prt-header": f"{b} bold",
        "prt-critical": f"{s.accent_critical} bold",
        "prt-major": f"{s.status_active} bold",
        "prt-minor": s.accent_warning,
        "prt-info": s.accent_info,
        "prt-clean": f"{s.status_done} bold",
        "prt-summary": s.palette_desc,
        # workspace sources — each TRUE worker type reads as its own source at a
        # glance. Reuses existing skin tokens so it re-themes with /skin and needs
        # no new Skin fields (dedicated codex/claude hues land with those features).
        "source-goal": f"{s.status_active} bold",
        "source-worker": s.accent_blue,
        "source-prt": f"{s.accent_purple} bold",
        "source-generic": s.text_muted,
        # Split terminal workspace — the six title accents mirror MO Phone's
        # bounded terminal palette while deriving from the active skin.
        "workspace-title": s.text_muted,
        "workspace-title-1": f"bg:{_mix(s.bg_dark, s.brand_primary, 0.36)} {s.text_bright}",
        "workspace-title-2": f"bg:{_mix(s.bg_dark, s.accent_blue, 0.52)} {s.text_bright}",
        "workspace-title-3": f"bg:{_mix(s.bg_dark, s.accent_purple, 0.36)} {s.text_bright}",
        "workspace-title-4": f"bg:{_mix(s.bg_dark, s.accent_amber, 0.36)} {s.text_bright}",
        "workspace-title-5": f"bg:{_mix(s.bg_dark, s.accent_green, 0.36)} {s.text_bright}",
        "workspace-title-6": f"bg:{_mix(s.bg_dark, s.accent_red_soft, 0.36)} {s.text_bright}",
        "workspace-focused": "bold underline",
        "workspace-body": "",
        "workspace-terminal": "",
        "workspace-error": f"{s.accent_critical} bold",
        "workspace-border": s.text_placeholder,
        "workspace-launcher": "",
        "workspace-launcher-title": f"{b} bold",
        "workspace-launcher-selected": f"bg:{s.selected_bg} {s.text_bright} bold underline",
    }


def skin_to_desktop_vars(skin: Skin | None = None) -> dict[str, str | tuple[int, int, int]]:
    """Return the canonical color-token input for ``DesktopVisualState``.

    Keys: CYAN, CARD, TEXT, _ENTRY_BG, _MUTED, _BORDER, _LISTEN,
          _CHROMA, _GLOW, _CHROMA_RGB, _BRAND_RGB, _DARK_RGB,
          plus semantic status tokens _OK, _WARN, _ERR, _ERR_SOFT, _DO so the
          MO Desktop status text re-themes with ``/skin`` like every surface.
    """
    s = skin or get_skin()
    dark_rgb_hex = s.dark_rgb_override or _mix(s.bg_surface, s.bg_deepest, 0.5)
    return {
        "CYAN": s.brand_primary,
        "CARD": s.bg_surface,
        "TEXT": s.text_primary,
        "_ENTRY_BG": s.bg_input,
        "_MUTED": s.text_muted,
        "_BORDER": s.border_default,
        "_LISTEN": s.accent_purple,
        "_CHROMA": s.bg_deepest,
        "_GLOW": s.brand_glow,
        # Semantic status tokens — MO Desktop status text follows the active skin.
        "_OK": s.accent_green,
        "_WARN": s.accent_warning,
        "_ERR": s.accent_critical,
        "_ERR_SOFT": s.accent_red,
        "_DO": s.accent_amber,
        # RGB tuples for PIL / tkinter
        "_CHROMA_RGB": s.hex_bg_deepest_rgb,
        "_BRAND_RGB": s.hex_brand_primary_rgb,
        "_LISTEN_RGB": _hex_to_rgb(s.accent_purple),
        "_DARK_RGB": _hex_to_rgb(dark_rgb_hex),
        # The skin's warm accent, used for the cube's recharge LED so it re-themes with /skin.
        "_AMBER_RGB": _hex_to_rgb(s.accent_amber),
    }


def skin_to_everywhere_tokens(skin: Skin | None = None, *, name: str = "default") -> dict[str, str]:
    """Return the renderer-neutral palette serialized by Everywhere clients.

    Terminal and MO Desktop keep their richer surface-specific tokens. Native clients
    consume this deliberately small projection so a skin
    switch has one authored source without exporting renderer implementation
    details.
    """
    colors = skin_to_desktop_vars(skin)
    return {
        "name": str(name or "default"),
        "background": str(colors["_CHROMA"]),
        "surface": str(colors["CARD"]),
        "input": str(colors["_ENTRY_BG"]),
        "brand": str(colors["CYAN"]),
        "glow": str(colors["_GLOW"]),
        "text": str(colors["TEXT"]),
        "muted": str(colors["_MUTED"]),
        "border": str(colors["_BORDER"]),
        "ok": str(colors["_OK"]),
        "warn": str(colors["_WARN"]),
        "error": str(colors["_ERR"]),
        "action": str(colors["_DO"]),
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
  border:1px solid var(--line);border-radius:8px;color:{s.text_bright};padding:7px 10px;
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
#info h3{{margin:0 0 6px;font-size:15px;color:{s.text_bright};word-break:break-all}}
"""


# ---------------------------------------------------------------------------
# Colour arithmetic helpers
# ---------------------------------------------------------------------------

def hex_to_rgb(value: Any, fallback: tuple[int, int, int] = (0, 0, 0)) -> tuple[int, int, int]:
    """Parse ``#rrggbb`` (with or without the hash) into RGB, or return ``fallback``.

    The one implementation. mo_desktop's cube and reply panel each carried their own.
    """
    try:
        return _hex_to_rgb(str(value))
    except Exception:
        return fallback


def contrast_text(background: Any, dark: str = "#0b1417", light: str = "#ffffff") -> str:
    """Choose the supplied text color with the higher luminance contrast."""
    def luminance(value: Any) -> float:
        channels = [channel / 255 for channel in hex_to_rgb(value)]
        linear = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4
                  for channel in channels]
        return sum(channel * weight for channel, weight in zip(linear, (.2126, .7152, .0722)))

    fill = luminance(background)
    def contrast(value: str) -> float:
        ink = luminance(value)
        return (max(fill, ink) + .05) / (min(fill, ink) + .05)
    return dark if contrast(dark) >= contrast(light) else light


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
    """Alias of :func:`_darken` (RGB scale toward black) — a separate name kept for
    call-site readability at the code-map / shell theming sites."""
    return _darken(hex_color, factor)


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
# Hot-reload bridge for visual effects
# ---------------------------------------------------------------------------
def get_method_glow_base() -> tuple[int, int, int]:
    """Return the active skin's warm accent for MO-owned activity animation."""
    return _hex_to_rgb(get_skin().accent_amber)
