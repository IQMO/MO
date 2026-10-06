"""MO Desktop companion: the cube renderer.

Four independent cubes by default, one shade, painted to the shared
``NativeLayeredWindow`` per-pixel-alpha surface on Windows. The visible cube
frame is a PIL RGBA image blitted through ``UpdateLayeredWindow`` so soft glow
and anti-aliased edges blend with the desktop. Smoothness comes from a sprite
cache: the cube shape + glow is rendered
once per brightness level, then each frame only composites cached sprites onto a
transparent frame -- no per-frame shape drawing or blur.

Public surface used by the companion:
(``tick`` / ``point_to`` / ``wake`` / ``set_listening`` / ``set_level`` /
``enable_follow`` / ``center`` / ``set_click_handlers`` / ``apply_visual_state`` /
``destroy`` and the ``_visible`` attr). Character look (size, glow,
colour) comes from ``mo_desktop/settings.py``. GUI-thread only, except
``tick`` which the companion calls each frame.
"""

from __future__ import annotations

from mo_desktop.gui_loop import pointer_position, screen_size

import math
import time
from typing import Any, Callable

from interface.desktop_ui import DesktopVisualAdapterError, DesktopVisualState
from interface.desktop_ui import active_desktop_visual_state
from interface.theming import hex_to_rgb
from mo_desktop.cube_interaction import CubeInteractionMixin
from mo_desktop.cube_motion import (
    CubeMotionMixin,
    paint_cube_trace,
    _CHASE_IDLE_DIM_AFTER as _CHASE_IDLE_DIM_AFTER,
    _CURSOR_REACTION_BRIGHTNESS as _CURSOR_REACTION_BRIGHTNESS,
    _NOMINAL_ACTIVE_FPS as _NOMINAL_ACTIVE_FPS,
    _TRACE_FADE_SECONDS as _TRACE_FADE_SECONDS,
    _time_scaled_ease as _time_scaled_ease,
)
from mo_desktop.cube_panel import (
    CubePanelMixin,
    _DEFAULT_POINT_LABEL as _DEFAULT_POINT_LABEL,
    _guidance_label_text as _guidance_label_text,
)
from mo_desktop.design import (
    DEFAULT_CUBE_FORM,
    DEFAULT_DESKTOP_PANEL_DESIGN,
    DEFAULT_LABEL_BUBBLE_DESIGN,
    CubeFormSpec,
    DesktopPanelDesign,
    LabelBubbleDesign,
    balanced_square,
    coerce_cube_form,
)

_GLIDE_SECONDS = 0.72
_LEVELS = 22  # brightness buckets in the sprite cache
_PASSIVE_FRAME_CACHE_LIMIT = 256
# Sprites land on quarter pixels: the ~3 px idle bob glides (a step every ~85 ms)
# instead of jumping a whole pixel every ~330 ms, which read as ticking.
_SUBPIXEL_STEPS = 4
_SUBPIXEL_CACHE_LIMIT = 512
_MIN_BRIGHT = 0.5
_MAX_BRIGHT = 1.0
_FADE_EASE = 0.12  # how fast the cube dissolves in front of a full-screen window
_SUMMON_TRACE_SECONDS = 3.0  # keep tracing through a Ctrl-Ctrl summon (dash + chase), not just the dash


def _skin_cube_rgb(visuals: DesktopVisualState | None = None) -> tuple[int, int, int]:
    """Return the active skin's brand color for the cube."""
    state = visuals or active_desktop_visual_state()
    if not isinstance(state, DesktopVisualState):
        raise TypeError("Desktop cube colors require DesktopVisualState")
    cube = state.token("_BRAND_RGB")
    return tuple(int(c) for c in cube)  # type: ignore[return-value]


def _skin_label_text_rgb(visuals: DesktopVisualState | None = None) -> tuple[int, int, int]:
    """Label/notice text: the skin's MUTED tone. A glance should be read, not announced."""
    state = visuals or active_desktop_visual_state()
    if not isinstance(state, DesktopVisualState):
        raise TypeError("Desktop label colors require DesktopVisualState")
    return hex_to_rgb(str(state.token("_MUTED")))


def _skin_charge_rgb(visuals: DesktopVisualState | None = None) -> tuple[int, int, int]:
    """The recharge LED's colour: the active skin's warm accent, so it re-themes with `/skin`."""
    state = visuals or active_desktop_visual_state()
    if not isinstance(state, DesktopVisualState):
        raise TypeError("Desktop charge colors require DesktopVisualState")
    amber = state.token("_AMBER_RGB")
    return tuple(int(c) for c in amber)  # type: ignore[return-value]


def _contrast_outline_rgb(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """A quiet keyline that stays visible on both light and dark wallpapers."""
    luminance = (0.2126 * rgb[0]) + (0.7152 * rgb[1]) + (0.0722 * rgb[2])
    return (15, 20, 24) if luminance >= 145 else (244, 248, 250)


def _subpixel(value: float) -> tuple[int, int]:
    """Whole pixel and quarter-pixel remainder (0..3) of a sprite position."""
    whole = math.floor(value)
    quarter = round((value - whole) * _SUBPIXEL_STEPS)
    if quarter == _SUBPIXEL_STEPS:
        return whole + 1, 0
    return int(whole), int(quarter)


# While MO Terminal uses the computer the cubes grow by this much and keep this far from the
# screen's edges in the corner they take.
_WORKING_SCALE = 1.6
_WORKING_MARGIN = 18
_CORNER_RECHECK_SECONDS = 3.0
# Voice: the level (recorder/RMS scale) that reads as a full lift, how fast the cubes rise to a
# syllable and settle in a pause, how much voice they remember, the per-cube delay, and the lift in
# bob amplitudes (2.4 x bob stays inside the travel the `jump` emote already proves safe).
_VOICE_FULL_LEVEL = 0.10
_VOICE_RISE_SECONDS = 0.035
_VOICE_FALL_SECONDS = 0.16
_VOICE_TRAIL_SECONDS = 0.5
_VOICE_LISTEN_STAGGER = 0.07
_VOICE_SPEAK_STAGGER = 0.03
_VOICE_LIFT = 2.4
_VOICE_SWELL = 0.10   # speaking: how far a full syllable opens the cluster (heartbeat uses .07)
# Their heartbeat meanwhile: a lub-dub every _HEARTBEAT_SECONDS; the cubes swell outward by
# _HEARTBEAT_SWELL of their offset from the centre at the top of a beat.
_HEARTBEAT_SECONDS = 1.1
_HEARTBEAT_SWELL = .07


def _heartbeat(elapsed: float) -> float:
    """0..1: a strong beat, a softer one 0.22 s later, then rest (one heart cycle)."""
    phase = (max(0.0, elapsed) % _HEARTBEAT_SECONDS) / _HEARTBEAT_SECONDS

    def bump(centre: float, width: float) -> float:
        return math.exp(-((phase - centre) / width) ** 2)

    return min(1.0, bump(.08, .055) + .62 * bump(.28, .06))

class DesktopCube(CubeInteractionMixin, CubeMotionMixin, CubePanelMixin):
    """A native sprite-cached cube driven by the resident's existing GUI clock."""

    def __init__(
        self,
        gui: Any,
        *,
        visuals: DesktopVisualState,
        post: Callable[[Callable[[], None]], Any],
        character: Any = None,
        size: int | None = None,
        form: CubeFormSpec | dict | None = None,
        label_design: LabelBubbleDesign | None = None,
        panel_design: DesktopPanelDesign | None = None,
        volume_controls: bool = True,
        label_side_provider: Callable[[], str | None] | None = None,
    ) -> None:
        from mo_desktop.layered import NativeLayeredWindow

        self._gui = gui
        self._post_gui = post
        self._launcher_input: Any = None

        if not isinstance(visuals, DesktopVisualState):
            raise TypeError("DesktopCube requires DesktopVisualState")
        self._visuals = visuals
        self._app_pulse_until = 0.0
        self._app_pulse_restore_rgb = None
        self._launcher_active = False
        self._game_session: dict[str, Any] = {"state": "inactive"}
        self._game_session_origin: dict[str, Any] | None = None
        self._game_session_dock: tuple[float, float] | None = None
        # --- settings (size / glow / colour) ---
        if character is None:
            from mo_desktop.settings import CharacterSettings

            character = CharacterSettings()
        cube_count = character.cube_count
        self._form = coerce_cube_form(form, cube_count=cube_count)
        self._panel_design = panel_design or DEFAULT_DESKTOP_PANEL_DESIGN
        self._label_design = label_design or self._panel_design.label or DEFAULT_LABEL_BUBBLE_DESIGN
        self._volume_controls = bool(volume_controls)
        self._size = int(size if size is not None else character.size)
        self._glow = float(character.glow)
        self._corner = float(character.corner_radius)
        color_mode = str(character.color_mode)
        self._color_rgb = _skin_cube_rgb(visuals)
        self._charge_rgb = _skin_charge_rgb(visuals)
        self._label_text_rgb = _skin_label_text_rgb(visuals)
        if color_mode.startswith("#") and len(color_mode) == 7:
            try:
                self._color_rgb = (int(color_mode[1:3], 16), int(color_mode[3:5], 16), int(color_mode[5:7], 16))
            except Exception:
                pass

        # Label and reply reuse the same alpha-card renderer and visual state.
        self._label_img: Any = None
        self._label_variant = "glance"
        self._label_kind = "bubble"
        self._pending_glance: tuple[str, float, str | None, str, str] | None = None
        self._label_side_override: str | None = None
        self._label_side_provider = label_side_provider
        self._trace_points: list[tuple[float, float, float]] = []
        self._trace_last_at = 0.0
        self._summon_trace_until = 0.0

        # --- position + animation state ---
        self._x = float(screen_size()[0]) / 2.0
        self._y = float(screen_size()[1]) / 2.0
        self._from = self._to = (self._x, self._y)
        self._wander_until = 0.0
        self._wander_to = (self._x, self._y)
        self._glide_start = 0.0
        self._glide_dur = 0.0
        self._visible = False
        self._listening = False
        self._speaking = False
        self._thinking = False
        self._drag = False  # a file is being dragged over the cube
        self._mouth = 0.0  # eased gape, 0 shut .. 1 wide open
        self._mouth_target = 0.0  # nearness of the dragged file, set per drag event
        self._drag_seen_at = 0.0  # last drag event, for the staleness net
        self._drag_leave_at = 0.0  # a DropLeave awaiting confirmation, 0 when none
        self._fade = 0.0  # 0 solid .. 1 gone: a full-screen film is not ours to sit on
        self._fade_target = 0.0
        self._notice_title = ""  # retained while detail hover temporarily replaces the title
        self._notice_detail = ""  # the short summary a notice reveals when glanced at
        self._notice_until = 0.0  # ...only while the glance window is open
        self._notice_hard_until = 0.0
        self._notice_expanded = False
        self._notice_action: Callable[[], None] | None = None
        self._notice_action_until = 0.0
        self._pending_notice_action: Callable[[], None] | None = None
        self._label_rect = (0, 0, 0, 0)
        self._drag_dist = 1e9  # how far the dragged file is from the cube centre, in px
        self._drag_armed = False  # a button is held on the cube: fill its transparent gaps for DnD
        self._press_on_cube = False  # a click is press+release on US, not a release that wandered in
        self._home = None  # dock point inside MO's terminal, when the cursor is there
        self._charging = False  # docked at home: topping up
        self._phone_charging_until = 0.0  # verified paired-phone USB charge, monotonic deadline
        self._held = False
        self._level = 0.0
        self._hide_at = 0.0
        self._label_value = ""
        self._label_until = 0.0
        self._last_geometry = ""
        self._follow_enabled = True
        self._resident = False
        self._actuation_yield = False
        self._follow_distance = 64.0  # trailing gap behind the cursor (px) — a setting
        self._follow_ease = 0.16  # chase spring 0..1 (lower = more lag) — a setting
        self._follow_pause_until = 0.0
        self._terminal_working: dict | None = None   # where the cubes were before MO Terminal took the PC
        self._working_terminal: dict | None = None   # that Terminal (instance, pid, cwd), for Esc
        self._last_ptr: tuple[float, float] | None = None  # for catch-detection
        self._cursor_reactions: list[tuple[float, float, float]] = []
        self._active_cube_index: int | None = None
        self._pointer_sample: tuple[float, float] | None = None
        self._pointer_sample_at = -1.0
        self._pointer_sample_ready = False
        self._cursor_idle_since: float | None = None
        self._idle_dim_after = _CHASE_IDLE_DIM_AFTER
        self._last_tick_at = 0.0
        self._frame_dt = 1.0 / _NOMINAL_ACTIVE_FPS
        self._left_click: Callable[[], None] | None = None
        self._right_click: Callable[[], None] | None = None
        self._left_double: Callable[[], None] | None = None
        self._escape: Callable[[], None] | None = None
        self._cube_click: Callable[[int, str], bool | None] | None = None
        self._capture_hold: Callable[[], None] | None = None
        self._focus_hold: Callable[[], None] | None = None
        self._cube_hold_after: Any = None
        self._cube_hold_started_at = 0.0
        self._pressed_cube_index: int | None = None
        self._last_click = ("", 0.0)
        self._double_at = 0.0
        self._emote: Any = None  # active transient emote: (fn/spec, start, dur)
        self._formation = self._form.default_formation or "cluster"

        # --- cube layout + sprite cache ---
        self._layout()
        if self._formation != "cluster":
            self.set_formation(self._formation)
        self._sprites: list[Any] = []
        self._sprite_px = 0
        self._last_layered_frame_signature: tuple[Any, ...] | None = None
        self._passive_layered_frame_cache: dict[tuple[Any, ...], Any] = {}
        self._build_sprites()
        try:
            self._win = NativeLayeredWindow(title="MO cubes", on_event=self._on_native_event, post=post)
            self._ulw = self._win
            self._label_win = NativeLayeredWindow(on_event=self._on_label_event, post=post, activate=False)
            self._label = self._label_win
            self._label.set_click_through(True)
            self._trace_win = NativeLayeredWindow()
            self._trace = self._trace_win
        except Exception:
            self.destroy()
            raise

    # ------------------------------------------------------------------ helpers
    def _plate_color(self) -> tuple[int, int, int]:
        """The label backing color, a dark tint of the current cube shade."""
        return tuple(max(8, int(c * 0.16)) for c in self._color_rgb)

    def _layout(self) -> None:
        s = self._size
        form = getattr(self, "_form", DEFAULT_CUBE_FORM)
        self._cube_edge = s * float(getattr(form, "edge_ratio", 0.22) or 0.22)
        gap = s * float(getattr(form, "gap_ratio", 0.10) or 0.10)
        step = (self._cube_edge + gap) / 2.0
        cx = cy = s / 2.0
        # base centres of the cubes, and a per-cube phase for stagger
        positions = getattr(form, "positions", DEFAULT_CUBE_FORM.positions) or DEFAULT_CUBE_FORM.positions
        self._bases = [(cx + float(px) * step, cy + float(py) * step) for px, py in positions]
        self._bob_amp = s * float(getattr(form, "bob_ratio", 0.035) or 0.035)
        reactions = list(getattr(self, "_cursor_reactions", []) or [])
        self._cursor_reactions = (reactions + [(0.0, 0.0, 0.0)] * len(self._bases))[: len(self._bases)]

    def set_formation(self, name: str) -> None:
        """Rearrange the cubes: 'cluster' default, 'row', or 'face'.

        Extra cubes beyond the four face anchors keep using the active form positions,
        which lets a 5-cube form keep its center cube.
        """
        s = self._size
        cx = cy = s / 2.0
        form = getattr(self, "_form", DEFAULT_CUBE_FORM)
        count = max(1, len(getattr(form, "positions", ()) or DEFAULT_CUBE_FORM.positions))
        if name == "row":
            self._formation = "row"
            step = s * float(getattr(form, "row_step_ratio", 0.22) or 0.22)
            self._bases = [(cx + (i - ((count - 1) / 2.0)) * step, cy) for i in range(count)]
        elif name == "face":
            self._formation = "face"
            ex = s * float(getattr(form, "face_eye_x_ratio", 0.17) or 0.17)
            ey = s * float(getattr(form, "face_eye_y_ratio", 0.12) or 0.12)
            mx = s * float(getattr(form, "face_mouth_x_ratio", 0.13) or 0.13)
            my = s * float(getattr(form, "face_mouth_y_ratio", 0.18) or 0.18)
            self._bases = [(cx - ex, cy - ey), (cx + ex, cy - ey), (cx - mx, cy + my), (cx + mx, cy + my)]
            if count > 4:
                fallback = balanced_square(count).positions
                step = (self._cube_edge + s * float(getattr(form, "gap_ratio", 0.10) or 0.10)) / 2.0
                extras = list(getattr(form, "positions", fallback))[4:] or list(fallback)[4:]
                self._bases.extend((cx + float(px) * step, cy + float(py) * step) for px, py in extras[: count - 4])
        else:
            self._formation = "cluster"
            self._layout()

    def _build_sprites(self) -> None:
        """Render the cube+glow once per brightness level (the sprite cache), in MO's own colour
        and again in the fixed charge colour — the LED that rides the ring while recharging."""
        self._sprites = self._render_sprite_set(self._color_rgb)
        charge = getattr(self, "_charge_rgb", None) or _skin_charge_rgb()
        self._accent_sprites = self._render_sprite_set(charge) if self._sprites else []
        self._sprite_px = self._sprites[0].width
        cache = getattr(self, "_passive_layered_frame_cache", None)
        if cache is not None:
            cache.clear()
        self._subpixel_sprites = {}
        self._last_layered_frame_signature = None

    def _render_sprite_set(self, rgb: tuple[int, int, int], *, edge: int | None = None) -> list:
        try:
            from PIL import Image, ImageDraw, ImageFilter
        except Exception as exc:
            raise DesktopVisualAdapterError("MO Desktop cube requires the Pillow renderer") from exc
        ss = 2
        e = int((self._cube_edge if edge is None else edge) * ss)
        # Keep the glow inside each cached sprite's allocation.
        pad = int(e * (0.30 * self._glow + 0.12))
        box = e + pad * 2
        radius = max(1, int(e * self._corner))
        sprites = []
        for i in range(_LEVELS):
            b = _MIN_BRIGHT + (_MAX_BRIGHT - _MIN_BRIGHT) * (i / (_LEVELS - 1))
            col = tuple(min(255, int(c * b)) for c in rgb)
            img = Image.new("RGBA", (box, box), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            c0 = box / 2.0
            d.rounded_rectangle([c0 - e / 2, c0 - e / 2, c0 + e / 2, c0 + e / 2], radius=radius, fill=(*col, 255))
            cube_alpha = img.split()[3]
            if self._glow > 0:
                # Pure-colour glow: blur only to shape a soft ALPHA halo, then repaint it
                # solid cube-colour so the feather never fades toward black (a black
                # feather reads as a dark ring once composited over the desktop).
                blur = float(getattr(getattr(self, "_form", DEFAULT_CUBE_FORM), "glow_blur_ratio", 0.10) or 0.10)
                halo = img.filter(ImageFilter.GaussianBlur(e * blur * (0.3 + self._glow)))
                glow = Image.new("RGBA", img.size, (*col, 0))
                glow.putalpha(halo.split()[3])
                img = Image.alpha_composite(glow, img)
            # The transparent layered renderer has no backing plate. Add a
            # one-pixel contrast keyline beneath the cube so bright skins do
            # not disappear into a light wallpaper (and vice versa).
            expanded = cube_alpha.filter(ImageFilter.MaxFilter(5))
            ring = expanded.point(lambda alpha: max(0, alpha - 145))
            outline = Image.new("RGBA", img.size, (*_contrast_outline_rgb(rgb), 0))
            outline.putalpha(ring)
            img = Image.alpha_composite(outline, img)
            small = img.resize((box // ss, box // ss), Image.LANCZOS)
            sprites.append(small)
        self._Image = Image
        return sprites

    def apply_visual_state(self, visuals: DesktopVisualState) -> None:
        if not isinstance(visuals, DesktopVisualState):
            raise TypeError("DesktopCube requires DesktopVisualState")
        self._visuals = visuals
        self._app_pulse_until = 0.0
        self._app_pulse_restore_rgb = None
        self._color_rgb = _skin_cube_rgb(visuals)
        self._charge_rgb = _skin_charge_rgb(visuals)  # the LED re-themes with /skin too
        self._label_text_rgb = _skin_label_text_rgb(visuals)
        self._label_img = None  # re-render the label with the new colour on next show
        self._build_sprites()
        if getattr(self, "_label_value", ""):
            if getattr(self, "_label_kind", "bubble") == "running":
                self._refresh_running_label()
            else:
                self._set_label(self._label_value, until=self._label_until,
                                side=getattr(self, "_label_side_override", None))
        if self._visible:
            self._render(time.perf_counter())

    # ------------------------------------------------------------------ public control
    def set_click_handlers(
        self,
        *,
        left: Callable[[], None] | None = None,
        right: Callable[[], None] | None = None,
        left_double: Callable[[], None] | None = None,
        escape: Callable[[], None] | None = None,
    ) -> None:
        self._left_click = left
        self._right_click = right
        self._left_double = left_double
        self._escape = escape

    def set_cube_click_handler(self, callback: Callable[[int, str], bool | None] | None) -> None:
        """Optionally handle a specific rendered cube before the shared click action.

        Returning true consumes the click.  With no handler, every cube preserves
        the established left/right behavior of the companion as a whole.
        """
        self._cube_click = callback

    def game_session_active(self) -> bool:
        """Return whether the cube is projecting an active or recoverable game session."""
        return str(getattr(self, "_game_session", {}).get("state") or "") in {
            "active", "recovery_required",
        }

    def set_game_session(self, session: dict[str, Any] | None) -> None:
        """Project SystemCare's journal as one docked cube without owning its state."""
        payload = dict(session or {"state": "inactive"})
        was_active = self.game_session_active()
        will_be_active = str(payload.get("state") or "") in {"active", "recovery_required"}
        if will_be_active and not was_active:
            self._game_session_origin = {
                "position": (float(self._x), float(self._y)),
                "follow": bool(self._follow_enabled),
                "resident": bool(self._resident),
                "home": getattr(self, "_home", None),
            }
        self._game_session = payload
        if will_be_active:
            from interface.desktop_widgets import _monitor_work_area

            area = _monitor_work_area(self._win)
            if not area:
                area = (0, 0, int(screen_size()[0]), int(screen_size()[1]))
            left, top, right, bottom = area
            margin = max(10, round(self._size * .16))
            dock = (
                max(left + self._size / 2, right - self._size / 2 - margin),
                min(bottom - self._size / 2, top + self._size / 2 + margin),
            )
            self._game_session_dock = dock
            self._follow_enabled = False
            self._resident = True
            self._glide_dur = 0.0
            self._x, self._y = dock
            self._from = self._to = dock
            self._clear_trace()
            self._hide_at = 0.0
            self._show()
        elif was_active:
            origin = self._game_session_origin or {}
            point = origin.get("position")
            if isinstance(point, tuple) and len(point) == 2:
                self._x, self._y = float(point[0]), float(point[1])
                self._from = self._to = (self._x, self._y)
            self._follow_enabled = bool(origin.get("follow", self._follow_enabled))
            self._resident = bool(origin.get("resident", self._resident))
            self._home = origin.get("home")
            self._game_session_origin = None
            self._game_session_dock = None
            if getattr(self, "_label_kind", "") == "running":
                self._hide_label()
        self._last_layered_frame_signature = None
        self._last_geometry = ""
        if self._visible:
            self._reposition()
            self._render(time.perf_counter())

    def set_hold_handlers(self, *, capture: Callable[[], None], focus: Callable[[], None]) -> None:
        """Share the stationary hold gesture between the two lower cubes."""
        self._capture_hold, self._focus_hold = capture, focus

    def hold_actuation_yield(self, owner: str, on: bool) -> None:
        """Yield while any owner needs it: a companion action, bound terminal activity,
        or a screen selection. One owner's release never ends another owner's yield."""
        holders = set(getattr(self, "_actuation_yield_holders", ()) or ())
        before = bool(holders)
        if on:
            holders.add(str(owner))
        else:
            holders.discard(str(owner))
        self._actuation_yield_holders = holders
        if bool(holders) != before:
            self.set_actuation_yield(bool(holders))

    def actuation_yield_held_by(self, owner: str) -> bool:
        return str(owner) in (getattr(self, "_actuation_yield_holders", ()) or ())

    def set_actuation_yield(self, on: bool) -> None:
        """Yield input and capture while keeping the normal cube visible when supported."""
        self._actuation_yield = bool(on)
        focus = getattr(self, "_focus_controller", None)
        if focus is not None:
            focus.set_actuation_yield(bool(on))
        surface = getattr(self, "_ulw", None)
        self._activity_cube_visible = bool(surface and surface.set_click_through(bool(on))
                                           and surface.exclude_from_capture(bool(on)))
        label = getattr(self, "_label_win", None)     # always click-through; out of captures meanwhile
        if label is not None:
            try:
                label.exclude_from_capture(bool(on))
            except Exception:
                pass
        if on and not self._activity_cube_visible:
            self._hide()
        else:
            self._show()

    def set_terminal_working(self, on: bool) -> None:
        """MO Terminal is using the computer: no overlay of its own. The cubes grow, glide to the
        emptiest corner of their screen and keep their working motion until it is done, then
        come back to where they were (row 13, 2026-10-06)."""
        saved = getattr(self, "_terminal_working", None)
        if bool(on) == (saved is not None):
            if on:
                self._fit_working_to_desktop()       # Desktop may have become busy or idle since
            return
        if on:
            self._terminal_working = {"since": time.perf_counter(), "moved": False}
            self._fit_working_to_desktop()
            self._show()
            return
        self._terminal_working = None
        if not saved.get("moved"):
            return
        self.set_size(saved["size"])
        # Home again. The working cubes were click-through all along, so nobody dragged them; a
        # position check misread a glide still on its way to the corner as a hand move.
        if self._follow_enabled:
            self.set_home(saved["home"])
        else:
            self.summon_to(*saved["at"], chase=False)

    def _desktop_busy(self) -> bool:
        """Desktop itself is in use: a docked face, a panel holding the cubes, the launcher,
        expanded Focus, a Desktop turn or its own acting. MO Terminal's corner waits for that."""
        focus = getattr(self, "_focus_controller", None)
        holders = set(getattr(self, "_actuation_yield_holders", ()) or ()) - {"terminal"}
        return bool(any(getattr(self, name, None) is not None for name in ("_composer_controller", "_dashboard_controller"))
                    or (focus is not None and not getattr(focus, "_collapsed", True))
                    or getattr(self, "_held", False) or getattr(self, "_launcher_active", False)
                    or getattr(self, "_thinking", False) or holders)

    def _fit_working_to_desktop(self, *, busy: bool | None = None) -> None:
        """While Desktop is idle the working cubes grow into the emptiest corner; while it is in
        use they go back to their normal size where they are, so its panels dock as always
        (a docking face calls this with ``busy=True`` before it measures the cubes). The first
        place and size are kept for the return when the work ends."""
        working = getattr(self, "_terminal_working", None)
        if working is None:
            return
        if self._desktop_busy() if busy is None else busy:
            if working.get("moved"):
                working["moved"] = False
                self.set_size(working["size"])
            return
        if working.get("moved"):
            self._working_corner_still_free(working, time.perf_counter())
            return
        corner = self._empty_corner()
        if corner is None:
            return
        working.setdefault("size", self._size)
        working.setdefault("home", getattr(self, "_home", None))
        working.setdefault("at", (self._x, self._y))
        working.update(moved=True, corner=corner, checked_at=time.perf_counter())
        self.set_size(round(working["size"] * _WORKING_SCALE))
        if self._follow_enabled:
            self.set_home(corner)                    # home holds it off the cursor's tail
        else:
            self.summon_to(*corner, chase=False)

    def _heartbeat_shown(self) -> bool:
        """The heartbeat shows while MO Terminal acts and Desktop itself is idle: Desktop's own
        states (acting, thinking, listening, speaking, a docked face) always show instead."""
        if getattr(self, "_terminal_working", None) is None:
            return False
        return not (self._desktop_busy() or getattr(self, "_listening", False) or getattr(self, "_speaking", False))

    def _empty_corner(self) -> tuple[float, float] | None:
        """The centre for the cubes in the freest corner of their screen's work area, any of the
        four: one the window MO is working in (the foreground) leaves free first, then the one other
        windows cover least, then the one farthest from the pointer MO is moving."""
        try:
            import win32api
            import win32gui
            from mo_desktop.focus_native import native_handle
            from mo_desktop.phone.trackpad import switchable_windows

            left, top, right, bottom = win32api.GetMonitorInfo(
                win32api.MonitorFromWindow(native_handle(self._win), 2))["Work"]
            foreground = int(win32gui.GetForegroundWindow() or 0)
            rects, focus = [], None
            for handle, _title, _active in switchable_windows():
                if not win32gui.IsIconic(handle):
                    rect = win32gui.GetWindowRect(handle)
                    rects.append(rect)
                    if handle == foreground:
                        focus = rect
            try:
                pointer = tuple(win32api.GetCursorPos())
            except Exception:
                pointer = (self._x, self._y)
        except Exception:
            return None
        half = self._size / 2 + _WORKING_MARGIN
        centres = ((left + half, top + half), (right - half, top + half),
                   (left + half, bottom - half), (right - half, bottom - half))

        def overlap(centre: tuple[float, float], rect: tuple[int, int, int, int]) -> float:
            x0, y0, x1, y1 = centre[0] - half, centre[1] - half, centre[0] + half, centre[1] + half
            return max(0, min(x1, rect[2]) - max(x0, rect[0])) * max(0, min(y1, rect[3]) - max(y0, rect[1]))

        def score(centre: tuple[float, float]) -> tuple[float, float, float]:
            return (overlap(centre, focus) if focus else 0.0,
                    sum(overlap(centre, rect) for rect in rects),
                    -((centre[0] - pointer[0]) ** 2 + (centre[1] - pointer[1]) ** 2))

        return min(centres, key=score)

    def _working_corner_still_free(self, working: dict, now: float) -> None:
        """Every few seconds while MO Terminal works: if a clearly freer corner exists (the work
        or the pointer came to this one), glide there; otherwise stay put (no fidgeting)."""
        if now - float(working.get("checked_at", 0.0) or 0.0) < _CORNER_RECHECK_SECONDS:
            return
        working["checked_at"] = now
        corner = self._empty_corner()
        current = working.get("corner")
        if corner is None or current is None or corner == current:
            return
        working["corner"] = corner
        if self._follow_enabled:
            self.set_home(corner)
        else:
            self.summon_to(*corner, chase=False)

    def enable_follow(self, enabled: bool = True) -> None:
        if self.game_session_active():
            self._follow_enabled = False
            return
        self._follow_enabled = bool(enabled)
        self._follow_pause_until = 0.0
        self._last_ptr = None
        self._cursor_idle_since = None
        if not self._follow_enabled:
            self.set_home(None)

    def set_home(self, point: tuple[float, float] | None) -> None:
        """Give the cube somewhere to be instead of trailing the cursor: MO's own terminal,
        which is where the character lives. ``None`` puts it back on the cursor's tail.

        Arriving and leaving both glide with the Ctrl-Ctrl summon trace — the same dash and the
        same fading footsteps, reused, so attaching to home reads as one movement."""
        target = (float(point[0]), float(point[1])) if point else None
        was, self._home = getattr(self, "_home", None), target
        if was == target:
            return  # the poll repeats; only the transition is a movement
        now = time.perf_counter()
        if target is not None:  # attaching: dash home, trailing footsteps
            self._from = (self._x, self._y)
            self._to = target
            self._glide_start = now
            self._glide_dur = _GLIDE_SECONDS
            self._hide_at = 0.0
        else:  # detaching: streak back out to the cursor
            self._charging = False
            self._emote = None  # stop recharging the moment it leaves home
        self._start_trace(now)
        self._summon_trace_until = now + _SUMMON_TRACE_SECONDS

    def set_character(
        self, *, glow: float | None = None, color_mode: str | None = None, corner_radius: float | None = None
    ) -> None:
        """Live look change from the settings panel: glow intensity, colour (``"skin"`` or
        ``"#rrggbb"``), and/or cube rounding. Rebuilds the sprite cache and repaints.
        Size is owned separately by :meth:`set_size` and is also applied live."""
        changed = False
        if glow is not None:
            try:
                value = max(0.0, float(glow))
                if value != self._glow:
                    self._glow = value
                    changed = True
            except Exception:
                pass
        if corner_radius is not None:
            try:
                # 0 is a hard square, 0.5 is a circle at this cube edge; beyond that the
                # rounded-rect radius exceeds the half-edge and the sprite degenerates.
                value = max(0.0, min(0.5, float(corner_radius)))
                if value != self._corner:
                    self._corner = value
                    changed = True
            except Exception:
                pass
        if color_mode is not None:
            self._app_pulse_until = 0.0
            self._app_pulse_restore_rgb = None
            cm = str(color_mode or "skin")
            if cm.startswith("#") and len(cm) == 7:
                try:
                    value = (int(cm[1:3], 16), int(cm[3:5], 16), int(cm[5:7], 16))
                    if value != self._color_rgb:
                        self._color_rgb = value
                        changed = True
                except Exception:
                    pass
            else:
                color = _skin_cube_rgb(self._visuals)
                if color != self._color_rgb:
                    self._color_rgb = color
                    changed = True
        if not changed:
            return
        self._build_sprites()
        if self._visible:
            self._render(time.perf_counter())

    def apply_form(self, form: CubeFormSpec | dict) -> None:
        """Apply a cube form/design spec live for studio previews or vetted product defaults."""
        self._form = coerce_cube_form(form)
        current = self._formation if getattr(self, "_formation", "cluster") else "cluster"
        self._layout()
        if current != "cluster":
            self.set_formation(current)
        self._build_sprites()
        self._last_geometry = ""
        if self._visible:
            self._reposition()
            self._render(time.perf_counter())

    def apply_label_design(self, design: LabelBubbleDesign) -> None:
        self._label_design = design or DEFAULT_LABEL_BUBBLE_DESIGN
        self._label_img = None
        if self._label_value:
            if getattr(self, "_label_kind", "bubble") == "running":
                self._refresh_running_label()
            else:
                self._set_label(self._label_value, until=self._label_until,
                                side=getattr(self, "_label_side_override", None))

    def apply_panel_design(self, design: DesktopPanelDesign | None) -> None:
        self._panel_design = design or DEFAULT_DESKTOP_PANEL_DESIGN
        self.apply_label_design(self._panel_design.label)

    def set_size(self, size: int) -> None:
        """Live-resize the cube cluster (settings Size slider). Relayouts + rebuilds the
        sprite cache; the layered buffer reallocs on the next paint, and the position is
        centre-anchored so it grows/shrinks in place. Clamped to a sane range."""
        try:
            n = max(40, min(200, int(float(size))))
        except Exception:
            return
        if n == self._size:
            return
        self._size = n
        formation = getattr(self, "_formation", "cluster") or "cluster"
        self._layout()
        if formation != "cluster":
            self.set_formation(formation)
        self._build_sprites()
        self._last_geometry = ""  # force _reposition to re-apply geometry at the new size
        if self._visible:
            self._reposition()
            self._render(time.perf_counter())

    def set_follow_params(self, distance: float | None = None, ease: float | None = None) -> None:
        """Chase feel (settings): trailing gap in px + spring 0..1 (lower = more delay)."""
        if distance is not None:
            try:
                self._follow_distance = max(0.0, float(distance))
            except Exception:
                pass
        if ease is not None:
            try:
                self._follow_ease = min(1.0, max(0.02, float(ease)))
            except Exception:
                pass

    def set_resident(self, on: bool = True) -> None:
        """Keep the companion always on screen (a resident companion, not a transient
        pointer): auto-show and never auto-hide while on."""
        self._resident = bool(on)
        if on:
            self._hide_at = 0.0
            self._show()
        elif not self._listening and not getattr(self, "_speaking", False):
            self._hide()

    def center(self) -> tuple[int, int]:
        return int(round(self._x)), int(round(self._y))

    def capture_launch_origin(self, app: str) -> dict | None:
        """Paint and hold one real pose until its app acknowledges launch."""
        import base64
        import io

        if getattr(self, "_app_launch_origin", None) is not None or self.game_session_active():
            return None
        count = self._cube_count()
        if count < (2 if app == "shell" else 4) or not self._visible or self._ulw is None:
            return None
        now = time.perf_counter()
        if not self._render(now):
            return None
        pieces = []
        indices = range(count - 2, count) if app == "shell" else range(min(4, count))
        for index in indices:
            center, sprite = self.launch_piece(index, now)
            pixels = io.BytesIO()
            sprite.save(pixels, format="PNG")
            pieces.append({"center": list(center), "png": base64.b64encode(pixels.getvalue()).decode("ascii")})
        from interface.desktop_widgets import _monitor_work_area
        origin = {"app": app, "pieces": pieces, "edge": self._cube_edge, "color": self._color_rgb,
                  "work_area": _monitor_work_area(self._win)}
        self._app_launch_origin = origin
        return origin

    def launch_piece(self, index: int, now: float) -> tuple[tuple[float, float], Any]:
        """One source of actual sprite pixels for Focus, launcher, and app entrances."""
        focus = getattr(self, "_focus_controller", None)
        if index == 3 and focus is not None and not self._launcher_active:
            return focus.launch_piece()
        composer = getattr(self, "_composer_controller", None)
        if index == 1 and composer is not None and not self._launcher_active:
            return composer.launch_piece()
        dashboard = getattr(self, "_dashboard_controller", None)
        if index in (0, 2) and dashboard is not None and not self._launcher_active:
            return dashboard.launch_piece(upper=index == 0)
        cx, cy, brightness, alpha = self._cube_state(index, now)
        level = max(0, min(len(self._sprites) - 1, round(brightness * (len(self._sprites) - 1))))
        sprite = self._sprite_for(index, level, now).copy()
        if alpha < .999:
            sprite.putalpha(sprite.getchannel("A").point(lambda value: int(value * alpha)))
        size = self._hit_size() if self._ulw is not None else self._size
        left, top = round(self._x-size/2), round(self._y-size/2)
        offset = (size-self._size)/2
        center = (left+int(cx+offset-sprite.width/2)+sprite.width/2,
                  top+int(cy+offset-sprite.height/2)+sprite.height/2)
        return center, sprite

    def release_launch_origin(self, origin: dict) -> None:
        if getattr(self, "_app_launch_origin", None) is origin:
            self._app_launch_origin = None
            self._last_tick_at = time.perf_counter()

    def cube_center(self, index: int, *, now: float | None = None) -> tuple[float, float] | None:
        """Screen-space centre of one rendered cube in the current formation."""
        try:
            cube_index = int(index)
            current = time.perf_counter() if now is None else float(now)
            cx, cy, _bright, _alpha = self._cube_state(cube_index, current)
        except (IndexError, TypeError, ValueError):
            return None
        offset = float(getattr(self, "_size", 84) or 84) / 2.0
        return float(self._x) + cx - offset, float(self._y) + cy - offset

    def cube_at_screen(self, x: float, y: float, *, now: float | None = None) -> int | None:
        """Return the independently rendered cube under a screen point, if any."""
        current = time.perf_counter() if now is None else float(now)
        edge = max(4.0, float(getattr(self, "_cube_edge", 1.0) or 1.0))
        half = edge * 0.62  # include the quiet glow while excluding formation gaps
        best: tuple[float, int] | None = None
        for index in range(self._cube_count()):
            if self.game_session_active() and index != 0:
                continue
            centre = self.cube_center(index, now=current)
            if centre is None:
                continue
            dx, dy = float(x) - centre[0], float(y) - centre[1]
            if abs(dx) <= half and abs(dy) <= half:
                distance = math.hypot(dx, dy)
                if best is None or distance < best[0]:
                    best = (distance, index)
        return best[1] if best is not None else None

    def set_level(self, level: float) -> None:
        try:
            self._level = max(0.0, float(level or 0.0))
        except Exception:
            self._level = 0.0

    def play_emote(self, name: str) -> None:
        """Play a transient emote from the emote library (poke/jump/happy/moody/...)."""
        try:
            from mo_desktop.emotes import get

            emote, dur = get(name)
        except Exception:
            emote, dur = None, 0.0
        if emote:
            self._emote = (emote, time.perf_counter(), float(dur))

    def react(self, event: str) -> None:
        """The single event→emote choke point: play the emote mapped to a desktop event."""
        from mo_desktop import emotes

        name = emotes.for_event(event)
        if name:
            self.play_emote(name)

    def pulse_app_color(self, color: str) -> None:
        """One finite, skin-derived heartbeat when a Desktop-owned app opens."""
        if getattr(self, "_app_launch_origin", None) is not None:
            return
        from PIL import ImageColor

        if self._app_pulse_restore_rgb is None:
            self._app_pulse_restore_rgb = self._color_rgb
        self._color_rgb = ImageColor.getrgb(str(color))
        self._app_pulse_until = time.perf_counter() + 0.9
        self._build_sprites()
        self.play_emote("app_heartbeat")

    def set_phone_charging(self, seconds: float) -> None:
        """A paired phone verifiably charges over USB: play the same recharge loop
        the dock uses, for as long as the bounded presence stays current. The
        deadline is monotonic; expiry ends the loop at a cycle boundary and a
        fresh presence report simply extends it."""
        self._phone_charging_until = time.perf_counter() + max(0.0, float(seconds))
        if self._emote is None and not self._thinking:
            self.play_emote("charging")

    def clear_phone_charging(self) -> None:
        """The phone stopped charging or its presence aged out: let the loop end."""
        self._phone_charging_until = 0.0

    def set_hold(self, on: bool) -> None:
        """Freeze the cubes in place — used while a reply/input bubble is anchored to
        them, so the pair stays cohesive instead of the cubes wandering/following off."""
        self._held = bool(on)

    def _stop_looping_emote(self) -> None:
        emote = getattr(self, "_emote", None)
        if emote is not None and self._emote_loops(emote[0]):
            self._emote = None

    def set_thinking(self, on: bool) -> None:
        """Sustained working motion through the existing thinking emote,
        driven while a turn runs — the cube IS the thinking/responding indicator, so turn
        status never opens a surface (it is recorded to the desktop log instead)."""
        if on and not self._thinking:
            self._thinking_started_at = time.perf_counter()
        self._thinking = bool(on)
        if on:
            self._stop_looping_emote()  # the recharge loop yields to the working spinner
            self._hide_at = 0.0
            self._show()

    def set_faded(self, on: bool) -> None:
        """Dissolve out of the way, or come back. Eased, never a snap."""
        self._fade_target = 1.0 if on else 0.0

    def _ease_fade(self) -> None:
        target = float(getattr(self, "_fade_target", 0.0) or 0.0)
        current = float(getattr(self, "_fade", 0.0) or 0.0)
        ease = _time_scaled_ease(_FADE_EASE, getattr(self, "_frame_dt", 1.0 / _NOMINAL_ACTIVE_FPS))
        self._fade = target if abs(target - current) < 0.002 else current + (target - current) * ease

    def set_listening(self, on: bool) -> None:
        self._listening = bool(on)
        self._voice_env, self._voice_trail = 0.0, []
        if on:
            self._hide_at = 0.0
            self._hide_label()
            self._show()
        elif not self._is_gliding():
            self._hide_label()
            self._hide_at = time.perf_counter() + 0.6

    def set_speaking(self, on: bool) -> None:
        """MO's own voice moves the cubes while it is audible (see ``_voice_lift``)."""
        self._speaking = bool(on)
        self._voice_env, self._voice_trail = 0.0, []
        if on:
            self._hide_at = 0.0
            self._show()
        elif not self._listening and not self._is_gliding():
            self._hide_at = time.perf_counter() + 0.6

    def point_to(self, x: int, y: int, label: str = _DEFAULT_POINT_LABEL, seconds: float = 4.0) -> bool:
        now = time.perf_counter()
        hold = max(0.5, float(seconds or 4.0))
        if getattr(self, "_label_kind", "bubble") == "notice" and bool(getattr(self, "_label_value", "")):
            remaining = max(0.5, float(getattr(self, "_label_until", now) or now) - now)
            self._pending_glance = (
                str(getattr(self, "_notice_title", "") or self._label_value),
                remaining,
                getattr(self, "_label_side_override", None),
                str(getattr(self, "_notice_detail", "") or ""),
                "notice",
            )
        self._notice_title = ""
        self._notice_detail = ""
        self._notice_until = 0.0
        self._notice_hard_until = 0.0
        self._notice_expanded = False
        self._from = (self._x, self._y)
        self._to = (float(x), float(y))
        self._glide_start = now
        self._glide_dur = _GLIDE_SECONDS
        self._hide_at = now + _GLIDE_SECONDS + hold
        self._follow_pause_until = self._hide_at
        self._start_trace(now)
        self._label_variant = "guidance"
        self._label_kind = "guidance"
        self._set_label(
            _guidance_label_text(label or _DEFAULT_POINT_LABEL),
            until=self._hide_at,
            side=self._label_side(),
        )
        self._show()
        return True

    def wake(self, x: int | None = None, y: int | None = None, seconds: float = 2.4) -> bool:
        now = time.perf_counter()
        if x is not None and y is not None:
            self._x, self._y = float(x), float(y)
            self._from = self._to = (self._x, self._y)
        self._glide_dur = 0.0
        self._follow_pause_until = 0.0
        self._hide_at = 0.0 if self._follow_enabled else now + max(0.5, float(seconds or 2.4))
        self._clear_trace()
        self._hide_label()
        self._show()
        return True

    def summon_to(self, x: int | None = None, y: int | None = None, *, chase: bool = True) -> bool:
        """Keyboard summon (double-tap Ctrl): dash to the cursor with the glide trace,
        then chase it. Pure reuse — the point-glide leaves the same fading footsteps as
        ``point_to`` and ``chase`` is the existing follow spring; this only wires the
        gesture, no new animation. ``x``/``y`` default to the current pointer."""
        now = time.perf_counter()
        if x is None or y is None:
            try:
                x, y = pointer_position()
            except Exception:
                return False
        self._from = (self._x, self._y)
        self._to = (float(x), float(y))
        self._glide_start = now
        self._glide_dur = _GLIDE_SECONDS
        self._hide_at = 0.0
        self._start_trace(now)
        self._summon_trace_until = now + _SUMMON_TRACE_SECONDS  # trace the dash AND the chase
        if chase:
            self.enable_follow(True)  # trail the cursor once the dash lands
        else:
            self._follow_pause_until = now + _GLIDE_SECONDS
        self._show()
        return True

    def destroy(self) -> None:
        self._cancel_cube_hold()
        self._cancel_running_hide()
        for surf in (getattr(self, "_ulw", None), getattr(self, "_label", None), getattr(self, "_trace", None)):
            if surf is not None and hasattr(surf, "destroy"):
                try:
                    surf.destroy()
                except Exception:
                    pass
        self._ulw = None

    def _cube_count(self) -> int:
        return max(1, len(getattr(self, "_bases", ()) or ()))

    def _cube_state(self, i: int, now: float, *, include_cursor: bool = True,
                    projected: bool = False) -> tuple[float, float, float, float]:
        """(centre_x, centre_y, brightness 0..1, alpha 0..1) for cube i in window px — idle
        breathe + bob, with any active emote layered on top. Alpha < 1 fades a cube toward
        transparent (used by the listening ripple to fade + trail-shade the cubes)."""
        if self.game_session_active():
            center = self._size / 2.0
            if i != 0:
                return center, center, .45, 0.0
            recovery = str(self._game_session.get("state") or "") == "recovery_required"
            wave = .5 + .5 * math.sin(now * (2.2 if recovery else 1.35))
            brightness = (.64 + .12 * wave) if recovery else (.49 + .07 * wave)
            alpha = (.94 if recovery else .76) * float(getattr(self, "_fade", 1.0) or 0.0)
            return center, center - self._bob_amp * .16 * wave, brightness, alpha
        if getattr(self, "_cube_hold_after", None) is not None and i == getattr(self, "_pressed_cube_index", None):
            now = float(getattr(self, "_cube_hold_started_at", now))
        bx, by = self._bases[i]
        phase = i * 0.62
        alpha = 1.0
        holds_still = self._emote_holds_still()
        if self._heartbeat_shown():
            # MO Terminal is using the computer: one heart for all four cubes, a lub-dub of
            # light with a slight outward swell, unlike Desktop's own acting wave or thinking.
            beat = _heartbeat(now - float(self._terminal_working.get("since", now)))
            center = self._size / 2.0
            bright, alpha = .68 + .32 * beat, .86 + .14 * beat
            cx = bx + (bx - center) * _HEARTBEAT_SWELL * beat
            cy = by + (by - center) * _HEARTBEAT_SWELL * beat
        elif getattr(self, "_actuation_yield", False):
            wave = .5+.5*math.sin(now*3-i*math.pi/2)
            bright, alpha = .72+.28*wave, .78+.22*wave
            cx, cy = bx, by-self._bob_amp*.25*wave
        elif self._thinking:
            from mo_desktop.emotes import get, sample
            emote, duration = get("thinking")
            elapsed = (now - getattr(self, "_thinking_started_at", 0.0)) % duration
            dx, dy, db, _ = sample(emote, i, elapsed, duration, cube_count=self._cube_count())
            bright = .90 + db
            cx, cy = bx + dx * self._cube_edge, by + dy * self._cube_edge
        elif getattr(self, "_speaking", False):
            # MO talking: its own voice lifts and lights the cubes word by word (a near-unison
            # with a slight stagger), and they settle in every pause. Fully present throughout.
            voice = self._voice_lift(i, now, _VOICE_SPEAK_STAGGER)
            sway = 0.5 + 0.5 * math.sin(now * 7.0 + i * math.pi)
            lift = voice * (0.82 + 0.18 * sway)
            bright = 0.74 + 0.26 * min(1.0, voice * 1.25)
            # Each syllable also opens the cluster a little (like a speaker cone), so MO talking
            # reads differently from MO listening, where the voice travels cube to cube.
            center = self._size / 2.0
            cx = bx + (bx - center) * _VOICE_SWELL * voice
            cy = by + (by - center) * _VOICE_SWELL * voice - self._bob_amp * _VOICE_LIFT * 0.6 * lift
        elif self._listening:
            # Listening: quiet keeps the slow travelling ripple (crest reveals each cube while
            # its trail fades); the operator's voice rises through the four cubes in turn, each
            # answering a moment after the one before. Continuous alpha keeps motion smooth
            # between the sprite cache's bounded brightness levels.
            voice = self._voice_lift(i, now, _VOICE_LISTEN_STAGGER)
            wave = max(0.0, math.sin(now * (0.8 + 1.0 * voice) - i * 0.9))
            lift = max(voice, 0.35 * wave)
            bright = 0.60 + 0.40 * max(wave, min(1.0, voice * 1.2))
            alpha = 0.06 + max(wave * 0.5, min(1.0, voice * 1.3)) * (0.94 if voice > wave * 0.5 else 1.0)
            cx = bx
            cy = by - self._bob_amp * _VOICE_LIFT * lift
        else:
            # Subtle idle shimmer that stays in the BRIGHT band (≈0.86–1.0) so the four
            # cubes read as bright, same-shade cyan — not a dim teal half-brightness.
            # An emote can declare itself STILL (recharging does): then the idle bob is
            # suppressed and only its own light moves.
            breathe = 1.0 if holds_still else 0.93 + 0.07 * math.sin(now * 2.0 + phase)
            bright = breathe
            cx = bx
            # A docked panel holds the cubes: they keep breathing but stop bobbing, so
            # the cubes beside the composer or reply never drift against its edge.
            docked = bool(getattr(self, "_held", False))
            cy = by if holds_still or docked else by + self._bob_amp * math.sin(now * 1.6 + phase)
            if not holds_still and self._chase_idle_dimmed(now):
                bright = 0.60 + 0.06 * math.sin(now * 1.2 + phase)
                alpha = 0.68
        reactions = getattr(self, "_cursor_reactions", []) or []
        if include_cursor and i < len(reactions) and not holds_still and not getattr(self, "_drag", False):
            cursor_x, cursor_y, cursor_level = reactions[i]
            cx += float(cursor_x)
            cy += float(cursor_y)
            bright += float(cursor_level) * _CURSOR_REACTION_BRIGHTNESS
        mouth = float(getattr(self, "_mouth", 0.0) or 0.0)
        if getattr(self, "_drag", False) or mouth > 0.002:
            e = self._cube_edge
            mid = self._size / 2.0
            if getattr(self, "_drag", False):
                # A file is held over MO: a slight, constant twitch of interest.
                cx += 0.10 * e * math.sin(now * 20.0 + phase)
            if mouth > 0.002:
                # The jaw: the top pair lifts, the bottom pair drops, and both spread
                # outward — the cluster opens around the file it is about to swallow.
                # 0.62/0.22 keeps the widest gape inside the same travel envelope the
                # `jump` emote already proves safe, so the glow never clips the window.
                sy = 0.0 if abs(by - mid) < 0.5 else (-1.0 if by < mid else 1.0)
                sx = 0.0 if abs(bx - mid) < 0.5 else (-1.0 if bx < mid else 1.0)
                cy += sy * mouth * e * 0.62
                cx += sx * mouth * e * 0.22
                bright += 0.12 * mouth
        if self._emote is not None:
            emote, t0, dur = self._emote
            try:
                from mo_desktop.emotes import sample

                dx, dy, db, da = sample(emote, i, now - t0, dur, cube_count=self._cube_count())
            except Exception:
                dx = dy = db = da = 0.0
            gain = float(getattr(getattr(self, "_form", DEFAULT_CUBE_FORM), "emote_gain", 1.0) or 1.0)
            cx += dx * self._cube_edge * gain
            cy += dy * self._cube_edge * gain
            bright += db * gain
            alpha += da * gain
        alpha *= 1.0 - float(getattr(self, "_fade", 0.0) or 0.0)
        if not getattr(self, "_actuation_yield", False):
            alpha *= float(getattr(self, "_focus_opacity", 1.0))
            if not projected and i == 3 and getattr(self, "_focus_controller", None) is not None and not self._launcher_active:
                alpha = 0.0
            if i == 1 and getattr(self, "_composer_controller", None) is not None and not self._launcher_active:
                alpha = 0.0
            if i in (0, 2) and getattr(self, "_dashboard_controller", None) is not None and not self._launcher_active:
                alpha = 0.0   # the docked Dashboard consumes the two left cubes
            if i >= 4 and self._any_face_docked() and not self._launcher_active:
                alpha = 0.0   # a 5-cube form's centre cube would sit inside the docked block
        return cx, cy, max(0.0, min(1.0, bright)), max(0.0, min(1.0, alpha))

    def _emote_loops(self, fn: Any) -> bool:
        try:
            from mo_desktop.emotes import loops

            return loops(fn)
        except Exception:
            return False

    def _emote_holds_still(self) -> bool:
        emote = getattr(self, "_emote", None)
        if emote is None:
            return False
        try:
            from mo_desktop.emotes import holds_still

            return holds_still(emote[0])
        except Exception:
            return False

    def _sprite_for(self, i: int, idx: int, now: float):
        """Blend the skin's charge accent for an emote or capture hold.

        Cached sprites are never mutated.
        """
        sprite = self._sprites[idx]
        emote = getattr(self, "_emote", None)
        accents = getattr(self, "_accent_sprites", None)
        if not accents:
            return sprite
        blend = self._cube_hold_progress(i, now)
        if emote is not None:
            fn, t0, dur = emote
            try:
                from mo_desktop.emotes import accent

                lit = accent(fn, i, now - t0, dur)
                blend = max(blend, lit * lit)
            except Exception:
                pass
        if blend <= 0.01:
            return sprite
        # Squaring emote light above keeps its travelling midpoint clean; hold
        # progress already ramps into the same skin accent.
        led = accents[idx].copy()
        led.putalpha(led.split()[3].point(lambda p, k=blend: int(p * k)))
        return self._Image.alpha_composite(sprite, led)

    def _ease_voice(self, now: float) -> None:
        """Once per frame: follow the live voice level with a quick rise and a softer fall (a
        syllable lands at once, a pause settles), and keep half a second of it so each cube can
        answer a moment after the one before."""
        if not (getattr(self, "_listening", False) or getattr(self, "_speaking", False)):
            return
        dt = float(getattr(self, "_frame_dt", 1.0 / _NOMINAL_ACTIVE_FPS) or 0.0)
        target = min(1.0, max(0.0, float(getattr(self, "_level", 0.0) or 0.0)) / _VOICE_FULL_LEVEL) ** 0.75
        env = float(getattr(self, "_voice_env", 0.0) or 0.0)
        rate = _VOICE_RISE_SECONDS if target > env else _VOICE_FALL_SECONDS
        env += (target - env) * (1.0 - math.exp(-dt / rate)) if dt > 0 else 0.0
        self._voice_env = env
        trail = getattr(self, "_voice_trail", None)
        if trail is None:
            trail = self._voice_trail = []
        trail.append((now, env))
        while trail and now - trail[0][0] > _VOICE_TRAIL_SECONDS:
            trail.pop(0)

    def _voice_lift(self, i: int, now: float, delay: float) -> float:
        """The voice envelope as cube ``i`` hears it: cube 0 now, each next one ``delay`` later."""
        trail = getattr(self, "_voice_trail", None) or []
        when = now - i * delay
        for stamp, value in reversed(trail):
            if stamp <= when:
                return value
        return trail[0][1] if trail else 0.0

    def _ease_mouth(self, now: float) -> None:
        """Chase the drag nearness once per frame so the gape follows the file smoothly
        instead of snapping between OS drag events (which arrive irregularly)."""
        self._ease_fade()
        self._ease_voice(now)
        self._settle_drag(now)
        target = float(getattr(self, "_mouth_target", 0.0) or 0.0)
        current = float(getattr(self, "_mouth", 0.0) or 0.0)
        ease = _time_scaled_ease(0.28, getattr(self, "_frame_dt", 1.0 / _NOMINAL_ACTIVE_FPS))
        self._mouth = target if abs(target - current) < 0.002 else current + (target - current) * ease

    def _render(self, now: float) -> bool:
        if self._launcher_active or getattr(self, "_app_launch_origin", None) is not None:
            return False
        self._ease_mouth(now)
        return self._paint_layered(now)

    def _subpixel_sprite(self, sprite: Any, qx: int, qy: int) -> Any:
        """``sprite`` moved right/down by ``qx``/``qy`` quarter pixels (cached; edges resampled
        in premultiplied alpha so a shifted glow never gains a dark fringe)."""
        if not qx and not qy:
            return sprite
        cache = getattr(self, "_subpixel_sprites", None)
        if cache is None:
            cache = self._subpixel_sprites = {}
        key = (id(sprite), qx, qy)
        shifted = cache.get(key)
        if shifted is None:
            from PIL import Image  # ~0.6 ms once per 27 px sprite and quarter offset

            width, height = sprite.size
            shifted = sprite.convert("RGBa").transform(
                (width + 1, height + 1), Image.Transform.AFFINE,
                (1, 0, -qx / _SUBPIXEL_STEPS, 0, 1, -qy / _SUBPIXEL_STEPS),
                resample=Image.Resampling.BILINEAR,
            ).convert("RGBA")
            cache[key] = shifted
            if len(cache) > _SUBPIXEL_CACHE_LIMIT:
                cache.pop(next(iter(cache)))
        return shifted

    def _paint_layered(self, now: float) -> bool:
        """Composite the sprites (glow + cube, NO plate) onto a transparent frame and
        blit it to the shared layered window — the cubes float with smooth edges."""
        surface = self._ulw
        if surface is None or not self._sprites:
            raise DesktopVisualAdapterError("MO Desktop layered renderer is unavailable")
        try:
            from PIL import Image, ImageChops, ImageDraw

            W = self._hit_size()
            off = (W - self._size) / 2.0
            drag_catch = bool(getattr(self, "_drag", False) or getattr(self, "_drag_armed", False))
            half = self._sprite_px / 2.0
            left = int(round(self._x - W / 2.0))
            top = int(round(self._y - W / 2.0))
            # The window itself moves in whole pixels; carry its remainder into the sprites.
            shift_x, shift_y = self._x - W / 2.0 - left, self._y - W / 2.0 - top
            items: list[tuple[tuple[int, int], Any, float]] = []
            signature_items: list[tuple[int, int, int, float]] = []
            for i in range(self._cube_count()):
                cx, cy, b, a = self._cube_state(i, now)
                idx = max(0, min(_LEVELS - 1, int(round(b * (_LEVELS - 1)))))
                sprite = self._sprite_for(i, idx, now)
                (dx, qx), (dy, qy) = (_subpixel(cx + off - half + shift_x),
                                      _subpixel(cy + off - half + shift_y))
                placed = self._subpixel_sprite(sprite, qx, qy)
                items.append(((dx, dy), placed, a))
                signature_items.append((dx, dy, id(placed), round(a, 6)))
            content_signature = (W, drag_catch, self._color_rgb, *signature_items)
            if content_signature == getattr(self, "_last_layered_frame_signature", None):
                return True

            passive = not any(
                bool(getattr(self, name, False))
                for name in ("_listening", "_speaking", "_thinking", "_drag", "_drag_armed")
            ) and getattr(self, "_emote", None) is None
            frame_cache = getattr(self, "_passive_layered_frame_cache", None)
            if frame_cache is None:
                frame_cache = {}
                self._passive_layered_frame_cache = frame_cache
            if passive and content_signature in frame_cache:
                frame = frame_cache.pop(content_signature)
                frame_cache[content_signature] = frame
                published = surface.blit(frame, left, top, premultiplied=True, position=False)
                if published:
                    self._last_layered_frame_signature = content_signature
                return published

            if drag_catch:
                # A layered window hit-tests by ALPHA, and the cubes are only ~35% of this
                # window — the gaps between them, and the hole the open mouth makes, are
                # alpha 0, so a drag crossing them passes straight through to the desktop.
                # That bounced DropEnter/DropLeave (the cube "wiggling randomly") and made
                # the centre of the open mouth undroppable. While a file is over MO, paint
                # the cube-sized circle at alpha 1: invisible (0.4%), but never zero, so
                # the OS keeps routing the drag through the mouth gap. The window never
                # expands beyond the visible character and the fill disappears at drag end.
                frame = Image.new("RGBA", (W, W), (0, 0, 0, 0))
                ImageDraw.Draw(frame).ellipse((0, 0, W - 1, W - 1), fill=(*self._color_rgb, 1))
            else:
                frame = Image.new("RGBA", (W, W), (0, 0, 0, 0))
            for dest, sprite, a in items:
                if a < 0.999:  # fade the cube toward transparent (COPY the
                    faded = sprite.copy()  # cached sprite so the cache isn't corrupted)
                    faded.putalpha(faded.split()[3].point(lambda p, a=a: int(p * a)))
                    sprite = faded
                try:
                    frame.alpha_composite(sprite, dest=dest)  # correct over-compositing
                except Exception:
                    frame.paste(sprite, dest, sprite)  # clips near edges
            red, green, blue, alpha = frame.split()
            frame = Image.merge(
                "RGBA",
                (
                    ImageChops.multiply(red, alpha),
                    ImageChops.multiply(green, alpha),
                    ImageChops.multiply(blue, alpha),
                    alpha,
                ),
            )
            if passive:
                frame_cache[content_signature] = frame
                if len(frame_cache) > _PASSIVE_FRAME_CACHE_LIMIT:
                    frame_cache.pop(next(iter(frame_cache)))
            published = surface.blit(frame, left, top, premultiplied=True, position=False)
            if published:
                self._last_layered_frame_signature = content_signature
            return published
        except Exception as exc:
            raise DesktopVisualAdapterError("MO Desktop layered renderer could not apply the active visual state") from exc

    def _trace_layout(self) -> tuple[list[tuple[float, float]], list[float]]:
        half = self._size/2
        edge = float(self._cube_edge)
        offsets = [(float(x)-half, float(y)-half) for x, y in self._bases]
        sizes = [edge]*len(offsets)
        focus = getattr(self, "_focus_controller", None)
        consumed: set[int] = set()
        if focus is not None and len(offsets) == 4:
            if focus._collapsed and focus._layout_motion is None:
                width, height = focus._target_size
                dx, dy = focus._face_offset
                offsets[3], sizes[3] = (dx+width/2, dy+height/2), focus.COLLAPSED_EDGE
            else:
                consumed.add(3)
        if getattr(self, "_composer_controller", None) is not None and len(offsets) > 1:
            consumed.add(1)
        if getattr(self, "_dashboard_controller", None) is not None and len(offsets) > 2:
            consumed.update((0, 2))
        if self._any_face_docked():
            consumed.update(range(4, len(offsets)))
        for index in sorted(consumed, reverse=True):   # drop from the end so indices stay valid
            offsets.pop(index)
            sizes.pop(index)
        return offsets, sizes

    def _any_face_docked(self) -> bool:
        return any(getattr(self, name, None) is not None
                   for name in ("_composer_controller", "_focus_controller", "_dashboard_controller"))

    def relayout_docked_faces(self, source: Any = None) -> None:
        """A docked face opened, closed or changed size: place the others again, so the
        composer, Focus and Dashboard always form the same block."""
        if getattr(self, "_relayout_active", False):
            return
        self._relayout_active = True
        try:
            for name in ("_composer_controller", "_dashboard_controller"):
                face = getattr(self, name, None)
                if face is not None and face is not source and getattr(face, "_visible", False):
                    face._repaint()
            focus = getattr(self, "_focus_controller", None)
            if focus is not None and focus is not source:
                focus._fit_visible_rows()
                focus._place_face()
        finally:
            self._relayout_active = False

    def docked_faces(self, *, exclude: str = "") -> list[tuple[float, float, int, int]]:
        """Every panel docked into the cube group as ``(dx, dy, w, h)`` from the cube centre:
        the composer (cube 1), Focus (cube 3) and the Dashboard (cubes 0 and 2). One list, so
        each owner clamps the same whole block on screen."""
        faces: list[tuple[float, float, int, int]] = []
        composer = getattr(self, "_composer_controller", None)
        if composer is not None and exclude != "composer":
            faces.append(tuple(composer.cube_extent()))
        focus = getattr(self, "_focus_controller", None)
        if focus is not None and exclude != "focus":
            faces.append((*focus._face_offset, *focus._target_size))
        dashboard = getattr(self, "_dashboard_controller", None)
        if dashboard is not None and exclude != "dashboard":
            faces.append(tuple(dashboard.cube_extent()))
        return faces

    def _trace_bounds(self, points: list[tuple[float, float, float]]) -> tuple[int, int, int, int]:
        offsets, sizes = self._trace_layout()
        edge = max(6.0, max(sizes)*.72)
        xs: list[float] = []
        ys: list[float] = []
        for px, py, _t in points:
            for ox, oy in offsets:
                xs.extend((px + ox - edge, px + ox + edge))
                ys.extend((py + oy - edge, py + oy + edge))
        pad = max(8.0, edge)
        left = math.floor(min(xs) - pad)
        top = math.floor(min(ys) - pad)
        right = math.ceil(max(xs) + pad)
        bottom = math.ceil(max(ys) + pad)
        return left, top, max(1, right - left), max(1, bottom - top)

    def _paint_trace(self, now: float) -> None:
        """Draw passive fading cube footsteps for explicit point glides only."""
        surface = getattr(self, "_trace", None)
        if surface is None or not getattr(surface, "available", lambda: False)():
            return
        points = self._active_trace_points(now)
        if not points:
            try:
                self._trace_win.hide()
            except Exception:
                pass
            return
        try:
            from PIL import Image, ImageDraw

            left, top, width, height = self._trace_bounds(points)
            frame = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            base_alpha = max(
                0,
                min(
                    255,
                    int(
                        getattr(getattr(self, "_panel_design", DEFAULT_DESKTOP_PANEL_DESIGN), "trace_alpha", 110) or 110
                    ),
                ),
            )
            offsets, sizes = self._trace_layout()
            edge = float(self._cube_edge)
            paint_cube_trace(frame, points, now=now, origin=(left, top), offsets=offsets,
                             edge=edge, color=self._color_rgb, alpha=base_alpha, sizes=sizes, corner=self._corner)
            if surface.blit(frame, left, top):
                self._trace_win.show()
        except Exception:
            pass

    # --- show/hide + clicks ---
    def _show(self) -> None:
        if not self._visible:
            self._visible = True
            self._last_layered_frame_signature = None
            try:
                self._reposition()
                self._render(time.perf_counter())
                self._win.show()
            except Exception:
                pass

    def _hide(self) -> None:
        self._visible = False
        self._last_layered_frame_signature = None
        self._hide_at = 0.0
        self._clear_trace()
        self._hide_label()
        try:
            self._win.hide()
        except Exception:
            pass
