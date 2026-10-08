"""MO Desktop — the reply/input bubble: rendered as a layered PIL surface.

Same rendering language as the 4-cube (``cube.py`` / ``layered.py``): a per-pixel-alpha
layered window painted from a PIL image, so the panel floats with SMOOTH anti-aliased
corners and a real soft shadow — not a flat tkinter card with hard ``SetWindowRgn``
edges. It floats next to the cubes with all four corners rounded and dismisses on
click-away.

Two modes, ONE surface (no tkinter dialog anywhere):
- ``show(text)``       — display MO's reply. Only a GENUINE walkthrough passes
                         ``controls=False`` (footerless, pointing); a normal reply that merely
                         used tools keeps its Reply/history/Submit. Option cards ALWAYS render
                         their Submit (the footer force-includes options).
- ``show_input(cb)``   — a compact text input; keystrokes are captured on the layered
                         window and the typed text + caret are drawn straight into the
                         card, so the input has the same smooth feel as the reply. An unsent
                         draft is preserved across close/reopen (``_stash_input_draft`` on
                         click-away or a mid-compose reply; restored here), so clicking away
                         never deletes what the operator was typing. Submit clears it; Escape
                         discards.
- ``show_attachment`` reuses the same cube-attached panel for file/image/media
  confirmations, including image previews/galleries and a media preview card.
  An image preview can morph in place to a quick edit-tools grid behind Tools
  affordance — same panel, no new window. This is optional BEHAVIORALLY: the tools
  stay on the side behind Tools and never change the normal preview; the feature is ON
  by default (``mo_desktop.panel_tools`` remains only as a kill-switch, set false to hide).
"""
from __future__ import annotations

from mo_desktop.gui_loop import pointer_position, screen_size, clipboard_text, set_clipboard_text

import re
import time
from typing import Any, Callable

from interface.desktop_ui import DesktopVisualState
from mo_desktop import card
from mo_desktop.image_crop import CropSelection
from mo_desktop.reply_panel_tools import (
    _PANEL_TOOL_CHIPS,
    ReplyPanelToolsMixin,
)
from mo_desktop.reply_secondary_views import ReplySecondaryViewsMixin
from mo_desktop.options import parse_options
from mo_desktop.visual_text import parse_inline_emphasis, plain_spans, shape_line
from mo_desktop.design import (
    DEFAULT_BUBBLE_DESIGN,
    DEFAULT_DESKTOP_PANEL_DESIGN,
    BubbleDesign,
    DesktopPanelDesign,
    PanelState,
    coerce_panel_state,
    split_token_to_width,
)

_SS = card.SS         # one supersample factor for every desktop card (anti-aliased edges + text)
# Panel-tools morph (ON by default; mo_desktop.panel_tools is a kill-switch, false to hide).
# Optional BEHAVIORALLY: the image preview is unchanged until the operator taps Tools
# affordance, which swaps the preview slot to this quick-tool grid; each tile is a drawn


# The composer's earlier-message browse: room kept for the three dots above Send, the dim behind
# the composer, and the cross-fade between messages.
_COMPOSER_DOTS_RESERVE = 16
_GENERATE_ROW_H = 28          # one row of a picked kind's choice pills (none under Auto)
_RESULT_ROW_H = 34            # one row of the compact Saved results list
_RESULT_ROWS = 4              # rows shown at once; the list scrolls past them
_GENERATE_STATUS_H = 23       # the progress line, only while a job or Refine reports something
_BROWSE_DIM_ALPHA = 205    # row 31: how dark (black) the panel goes around the browsed message's line
_CLICK_AWAY_POLL_MS = 30   # how often an open panel reads the left button for a click away
BLUR_CARD_ALPHA = 226    # row 32: the card's see-through over Windows' blur (text stays crisp)
_BROWSE_FADE_SECONDS = 0.15

class ReplyBubble(ReplyPanelToolsMixin, ReplySecondaryViewsMixin):
    """MO's reply/input, rendered as a smooth floating card on a layered window."""

    def __init__(self, gui: Any, cube: Any, visuals: DesktopVisualState,
                 design: BubbleDesign | None = None,
                 panel_design: DesktopPanelDesign | None = None, *, face: str = "panel") -> None:
        from mo_desktop.layered import NativeLayeredWindow

        self._gui = gui
        self._cube = cube
        # "panel": the one living panel (composer, replies, status). "dashboard": the compact
        # Dashboard docked as its own face over the two left cubes, beside composer and Focus.
        self._face = face
        self._panel_design = panel_design or DEFAULT_DESKTOP_PANEL_DESIGN
        self._design = design or self._panel_design.bubble or DEFAULT_BUBBLE_DESIGN
        self._apply_visual_state(visuals)

        self._last_error = ""
        self._visible = False
        self._on_visibility_changed: Callable[[bool], None] | None = None
        self._mode = "reply"          # "reply" | "input" | specialized panel mode
        self._panel_state = PanelState.REPLY
        self._body = ""               # reply text or input buffer
        self._input_draft = ""        # an unsent composer draft, preserved across close/reopen
        self._cursor = 0
        self._select_all = False
        self._on_submit: Callable[[str], None] | None = None
        self._reply_history: list[dict[str, Any]] = []  # canonical replies, oldest->newest
        self._reply_idx = 0
        self._browse_idx: int | None = None   # an earlier reply shown IN the composer (Up/Down, dots)
        self._browse_veil: Any = None         # the dim behind the composer while it shows one
        self._controls_enabled = True
        self._copied = False
        self._hover = ""            # which footer control the pointer is over
        self._keyboard_hit = ""     # custom-drawn control reached with Tab/arrow keys
        self._dismissible = True    # a walkthrough must survive a click that follows it
        self._dock_side = "right"
        self._attachment_preview_paths: list[str] = []
        self._attachment_tools_allowed = True
        self._attachment_preview_cache_key: tuple[tuple[str, ...], int, int, int, str] | None = None
        self._attachment_preview_cache: Any = None
        self._panel_tools_enabled = False    # mo_desktop.panel_tools gate (companion sets it)
        self._panel_tools_open = False       # morph: image preview <-> edit-tools grid
        self._panel_tool_active = ""         # a parametric tool's chip row (grid -> chips)
        self._on_panel_tool: Callable[..., None] | None = None    # (tool, arg, source) seam
        self._crop = CropSelection()         # interactive crop rectangle (Phase 4)
        self._crop_disp_box: tuple[float, float, float, float] | None = None  # shown-image window rect
        self._transition_started_at = 0.0
        self._transition_pending_start = False
        self._transition_after: Any = None
        self._scroll_line = 0                # mouse-wheel body scroll; cube wheel remains volume
        self._max_scroll_line = 0
        self._caret = True
        self._blink_after: Any = None
        self._input_base_image: Any = None
        self._input_caret_rect: tuple[int, int, int, int] | None = None
        self._last_input_blit_position: tuple[int, int] | None = None
        self._last_layered_geometry: tuple[int, int, int, int] | None = None
        self._bounds = (0, 0, 0, 0)   # card rect on screen for click-away hit test
        self._watch_after: Any = None
        self._font_cache: dict[int, tuple] = {}
        self._ss = _SS                 # active supersample (drops during motion, settles back)
        self._last_render_at = 0.0
        self._settle_after: Any = None
        self._transition_base: Any = None      # finished 1x card, reused for every morph frame
        self._transition_base_key: Any = None
        self._transition_repaint_deferred = False
        self._transition_finish_base = False
        self._transition_callbacks: list[Callable[[], None]] = []
        self._font, self._bfont, self._sfont, self._ifont = self._fonts(_SS)
        self._on_reply: Callable[[int], None] | None = None  # companion sets this (reply/history control)
        self._on_footer_action: Callable[[], None] | None = None
        self._footer_action_label = ""
        self._on_session_history: Callable[[], None] | None = None
        self._on_web_search: Callable[[str, str], bool] | None = None
        self._search_provider = ""  # "" | "google" | "youtube"; composer-only routing state
        self._session_history_items: list[dict[str, Any]] = []
        self._session_history_page = 0
        self._on_session_select: Callable[[str], None] | None = None
        self._on_session_new: Callable[[], None] | None = None
        self._on_session_back: Callable[[], None] | None = None
        self._options: list[Any] = []
        self._option_mode = "multi"
        self._selected_options: set[int] = set()
        self._options_submitted = False
        self._on_options_submit: Callable[[list[str]], bool | None] | None = None
        self._dashboard_data: dict | None = None                      # compact dashboard data (tiles/toggle)
        self._dashboard_compact_actions: dict[str, Callable[[], None]] = {}
        self._dashboard_view = "overview"
        self._hit: dict[str, tuple[int, int, int, int]] = {}  # window-space rects of footer controls
        self._win = NativeLayeredWindow(
            title="MO Desktop", on_event=self._on_native_event, post=cube._post_gui,
        )
        self._layered = self._win

    def _on_native_event(self, kind: str, event: Any) -> None:
        if kind in {"key", "text"}:
            if event.keysym in {"Up", "Down"} and getattr(self, "_menu", None) is None:
                self._nav(-1 if event.keysym == "Up" else 1)
            elif kind == "text" or event.keysym != "space":
                self._on_key(event)
        elif kind == "wheel":
            self._on_wheel(event)
        elif kind == "release" and event.num == 1:
            self._on_click(event)
        elif kind == "press" and event.num == 1:
            self._on_press(event)
        elif kind == "motion":
            if event.dragging:
                self._on_drag(event)
            self._on_motion(event)
        elif kind == "leave":
            self._on_leave(event)
        elif kind == "blur":
            self._on_blur()
        elif kind == "close":
            self.hide()

    def available(self) -> bool:
        return self._layered.available()

    def failure_detail(self) -> str:
        detail = str(getattr(self, "_last_error", "") or "").strip()
        if detail:
            return detail[:160]
        getter = getattr(self._layered, "failure_detail", None)
        if callable(getter):
            return str(getter() or "layered panel unavailable")[:160]
        return "layered panel unavailable"

    def apply_design(self, design: BubbleDesign) -> None:
        self._design = design or DEFAULT_BUBBLE_DESIGN
        if self._visible:
            self._repaint()

    def apply_panel_design(self, design: DesktopPanelDesign | None) -> None:
        self._panel_design = design or DEFAULT_DESKTOP_PANEL_DESIGN
        self.apply_design(self._panel_design.bubble)

    def _apply_visual_state(self, visuals: DesktopVisualState) -> None:
        from interface.theming import contrast_text, hex_to_rgb

        if not isinstance(visuals, DesktopVisualState):
            raise TypeError("ReplyBubble requires DesktopVisualState")
        self._visuals = visuals
        palette = visuals.palette
        self._card = hex_to_rgb(palette.card)
        self._cyan = hex_to_rgb(palette.accent)
        self._text = hex_to_rgb(palette.text)
        self._entry = hex_to_rgb(palette.entry)
        self._muted = hex_to_rgb(palette.muted)
        self._edge = hex_to_rgb(palette.border)
        # Specialized panels and status glances use the skin's own OK/DO/ERR, so
        # they re-theme with /skin like the rest of the surface.
        self._ok = hex_to_rgb(palette.ok)
        self._amber = hex_to_rgb(palette.action)
        self._err = hex_to_rgb(palette.error)
        self._accent_ink = hex_to_rgb(
            contrast_text(palette.accent),
            self._card,
        )

    def apply_visual_state(self, visuals: DesktopVisualState) -> None:
        self._apply_visual_state(visuals)
        if self._visible:
            self._repaint()

    def _fonts(self, ss: int) -> tuple:
        """Fonts for a given supersample, cached. Glyph size must track ``ss`` or the
        text no longer matches the geometry it is laid out against."""
        key = int(ss)
        cache = getattr(self, "_font_cache", None)
        if cache is None:
            cache = {}
            self._font_cache = cache
        fonts = cache.get(key)
        if fonts is None:
            fonts = (self._load_font(13 * key), self._load_bold_font(13 * key),
                     self._load_font(10 * key), self._load_icon_font(12 * key))
            cache[key] = fonts
        return fonts

    @staticmethod
    def _load_font(size: int) -> Any:
        from mo_desktop.fonts import load_font

        return load_font(("segoeui.ttf", "arial.ttf"), size)

    @staticmethod
    def _load_bold_font(size: int) -> Any:
        from mo_desktop.fonts import load_font

        return load_font(("segoeuib.ttf", "seguisb.ttf", "arialbd.ttf", "segoeui.ttf", "arial.ttf"), size)

    @staticmethod
    def _load_icon_font(size: int) -> Any:
        from mo_desktop.fonts import load_font

        return load_font(("seguisym.ttf", "segoeui.ttf", "arial.ttf"), size)

    # ------------------------------------------------------------------ rendering
    def _text_width(self, draw: Any, text: str, font: Any) -> float:
        """Width of one span; typing re-measures the same words on every keystroke."""
        cache = getattr(self, "_text_widths", None)
        if cache is None or len(cache) >= 4096:
            cache = self._text_widths = {}
        key = (id(font), getattr(font, "size", 0), getattr(draw, "fontmode", ""), text)
        width = cache.get(key)
        if width is None:
            width = cache[key] = float(draw.textlength(text, font=font))
        return width

    def _line_width(self, draw: Any, spans: list[tuple[str, bool]]) -> float:
        bold_font = getattr(self, "_bfont", getattr(self, "_font", None))
        font = getattr(self, "_font", bold_font)
        if getattr(self, "_chips", None):   # Generate: a reference name is as wide as its chip
            return sum(self._chip_width(draw, name) if name else self._text_width(draw, part, bold_font if bold else font)
                       for text, bold in spans if text for part, name in self._chip_parts(text) if part or name)
        return sum(self._text_width(draw, text, bold_font if bold else font)
                   for text, bold in spans if text)

    # ---- in-sentence references (Generate): [Video1] sits in the sentence as a chip ----------------
    def _reference_chip_map(self, generate: bool) -> dict[str, dict]:
        """The composer's attached files by their in-sentence name; a role shows only in Generate."""
        from mo_desktop.generate_controls import reference_tokens

        paths = [str(p) for p in getattr(self, "_attachment_preview_paths", []) or []]
        roles = getattr(self, "_generate_reference_roles", {}) or {}
        return {name: {"path": path, "index": paths.index(path), "generate": generate,
                       "role": roles.get(path, "reference") if generate else "",
                       "kind": name[1:-1].rstrip("0123456789").lower()}
                for name, path in reference_tokens(paths).items()}

    def _chip_parts(self, text: str) -> list[tuple[str, str]]:
        """Plain parts and the reference names drawn as chips, in order: ``(text, "")`` or ``("", name)``."""
        chips = getattr(self, "_chips", None)
        if not chips:
            return [(text, "")]
        from mo_desktop.generate_controls import REFERENCE_TOKEN

        parts, last = [], 0
        for match in REFERENCE_TOKEN.finditer(text):
            if match.group(0) in chips:
                if match.start() > last:
                    parts.append((text[last:match.start()], ""))
                parts.append(("", match.group(0)))
                last = match.end()
        if last < len(text):
            parts.append((text[last:], ""))
        return parts or [(text, "")]

    def _chip_texts(self, name: str) -> tuple[str, str]:
        info = self._chips[name]
        number = name[1:-1][len(info["kind"]):]
        role = {"first_frame": "first frame", "last_frame": "last frame"}.get(info["role"], info["role"])
        return f"{info['kind'].title()} {number}", role

    def _chip_width(self, draw: Any, name: str) -> float:
        ss = int(getattr(self, "_ss", _SS) or _SS)
        font = getattr(self, "_sfont", None) or self._font
        label, role = self._chip_texts(name)
        role_width = (5 * ss + self._text_width(draw, role, font)) if role else 0
        return (4 + 16 + 5 + 7) * ss + self._text_width(draw, label, font) + role_width

    def _chip_thumbnail(self, path: str, size: int) -> Any:
        cache = self.__dict__.setdefault("_chip_thumbs", {})
        if (path, size) not in cache:
            try:
                from PIL import Image, ImageOps

                with Image.open(path) as source:
                    thumb = source.convert("RGBA")
                cache[(path, size)] = ImageOps.fit(thumb, (size, size), Image.LANCZOS)   # fills its square
            except Exception:
                cache[(path, size)] = None
        return cache[(path, size)]

    def _draw_reference_chip(self, d: Any, img: Any, x: float, y: int, name: str) -> float:
        """One reference chip at the text position: its picture (or MO's play mark for clips and sound), its
        name and, in Generate, its role. Clicking it opens Generate's References menu (role or remove), else Remove."""
        ss = int(getattr(self, "_ss", _SS) or _SS)
        design = getattr(self, "_design", DEFAULT_BUBBLE_DESIGN)
        font = getattr(self, "_sfont", None) or self._font
        info = self._chips[name]
        label, role = self._chip_texts(name)
        width = self._chip_width(d, name)
        top, bottom = y + 1 * ss, y + int(design.line_height) * ss - 2 * ss
        d.rounded_rectangle((x, top, x + width - 2 * ss, bottom),
                            radius=int(self._visuals.metrics.button_corner_radius) * ss, fill=(*self._entry, 255),
                            outline=(*self._cyan, 150), width=max(1, ss))
        size = 14 * ss
        tx, ty = int(x + 4 * ss), int(top + (bottom - top - size) / 2)
        thumb = self._chip_thumbnail(info["path"], size) if info["kind"] == "image" else None
        if thumb is not None:
            img.alpha_composite(thumb, (tx + (size - thumb.width) // 2, ty + (size - thumb.height) // 2))
        elif info["kind"] == "file":
            from interface.desktop_brand import make_glyph_icon

            img.alpha_composite(make_glyph_icon("file", size, color="#%02x%02x%02x" % tuple(self._cyan[:3])), (tx, ty))
        else:
            self._play_mark(d, tx, ty, size)
        lx = x + (4 + 16 + 5) * ss
        d.text((lx, y + 3 * ss), label, font=font, fill=(*self._text, 255))
        d.text((lx + self._text_width(d, label, font) + 5 * ss, y + 3 * ss), role, font=font, fill=(*self._cyan, 255))
        self._hit[f"chip:{info['index']}"] = (int(x / ss), int(top / ss), int((x + width) / ss), int(bottom / ss))
        return width

    # ---- Generate's controls: filled pills with no stroke, like the role menu ----------------------
    def _pill_width(self, draw: Any, key: str, text: str) -> float:
        ss = int(getattr(self, "_ss", _SS) or _SS)
        return draw.textlength(str(text), font=self._sfont) + (16 if key == "credits" else 26) * ss

    def _flow_pills(self, draw: Any, pills: list[tuple[str, str]], width: int) -> list[list[tuple[str, str, float]]]:
        """A picked kind's choice pills in rows, each row taking what fits the composer's width."""
        ss = int(getattr(self, "_ss", _SS) or _SS)
        rows: list[list[tuple[str, str, float]]] = []
        row: list[tuple[str, str, float]] = []
        used, gap, limit = 0.0, 5 * ss, max(1, int(width)) * ss
        for key, text in pills:
            pill = min(self._pill_width(draw, key, text), limit)
            if row and used + gap + pill > limit:
                rows.append(row)
                row, used = [], 0.0
            used += (gap if row else 0) + pill
            row.append((key, text, pill))
        return rows + ([row] if row else [])

    def _draw_generate_pill(self, d: Any, key: str, text: str, left: float, top: float, width: float, height: float,
                            menu_owner: str) -> tuple[int, int, int, int]:
        ss = int(getattr(self, "_ss", _SS) or _SS)
        hit = "generate:" + key
        colour = self._cyan if (menu_owner == hit or self._hovering(hit)) else self._text
        d.rounded_rectangle((left, top, left + width, top + height),
                            radius=int(self._visuals.metrics.button_corner_radius) * ss, fill=(*self._entry, 255))
        menu_pill = key != "credits"                           # the balance refreshes on a click; no menu
        label = card.fit_text(d, str(text), width - (24 if menu_pill else 16) * ss, self._sfont)
        d.text((left + 8 * ss, top + (height - 14 * ss) / 2), label, font=self._sfont, fill=(*colour, 255))
        if menu_pill:
            cx, cyc = left + width - 11 * ss, top + height / 2
            d.line([(cx - 3 * ss, cyc - 1.5 * ss), (cx, cyc + 1.5 * ss), (cx + 3 * ss, cyc - 1.5 * ss)],
                   fill=(*colour, 255), width=max(1, ss), joint="curve")
        return int(left / ss), int(top / ss), int((left + width) / ss), int((top + height) / ss)

    def _draw_privacy_mark(self, d: Any, x: float, y: float, size: float) -> None:
        """Generate's privacy: a small '!' in a ring, with the icons by Send."""
        ss = int(getattr(self, "_ss", _SS) or _SS)
        on = self._hovering("generate:privacy") or bool(getattr(self, "_privacy_open", False))
        colour = (*(self._cyan if on else self._muted), 255)
        d.ellipse((x, y, x + size, y + size), outline=colour, width=max(1, ss))
        cx = x + size / 2
        d.line([(cx, y + size * 0.26), (cx, y + size * 0.58)], fill=colour, width=max(1, int(1.3 * ss)))
        d.ellipse((cx - 0.8 * ss, y + size * 0.7, cx + 0.8 * ss, y + size * 0.7 + 1.6 * ss), fill=colour)

    # ---- Generate's credits detail and the compact Saved results list ------------------------------
    def show_credit_detail(self, text: str, seconds: float = 4.0) -> None:
        """A click on the credits pill: the pill itself shows the exact balance for a few seconds,
        then the short balance again."""
        self._credit_detail, self._credit_detail_until = str(text or ""), time.monotonic() + seconds
        self._repaint_open_composer()
        try:
            self._gui.schedule(int(seconds * 1000) + 60, self._repaint_open_composer)
        except Exception:
            pass

    def _repaint_open_composer(self) -> None:
        """Repaint only while the composer is still on screen: a timer must never bring a closed panel
        back (an empty reply card appeared that way, his screenshot 2026-10-08)."""
        if getattr(self, "_visible", False) and getattr(self, "_mode", "") == "input":
            self._repaint()

    def show_results(self, items: list[dict] | None) -> None:
        """Open Saved results as a compact list in the composer's text area (``None`` closes it)."""
        self._results_open = items is not None
        self._results = list(items or [])
        self._results_scroll = 0
        self._menu = None
        self._repaint_open_composer()

    def add_glance(self, glance: dict[str, dict]) -> None:
        self._chip_glance = {**(getattr(self, "_chip_glance", None) or {}), **(glance or {})}
        if getattr(self, "_results_open", False):
            self._repaint_open_composer()

    def _draw_results(self, d: Any, img: Any, left: float, top: float, width: float) -> None:
        """Rows of the compact Saved results list: a click on a row plays it; its icons save a copy,
        continue it (clips and songs that can) and open the job's own card."""
        from interface.desktop_brand import make_glyph_icon
        from PIL import Image, ImageOps

        ss = int(getattr(self, "_ss", _SS) or _SS)
        items = list(getattr(self, "_results", None) or [])
        font = getattr(self, "_sfont", None) or self._font
        if not items:
            d.text((left, top + 6 * ss), "No results in this conversation yet.", font=font, fill=(*self._muted, 255))
            return
        first = int(getattr(self, "_results_scroll", 0))
        radius = int(self._visuals.metrics.button_corner_radius) * ss
        thumb_size, row_h = 26 * ss, _RESULT_ROW_H * ss
        for slot, number in enumerate(range(first, min(len(items), first + _RESULT_ROWS))):
            item, y = items[number], top + slot * row_h
            play_key, right = f"result_play:{number}", left + width
            if self._hovering(play_key):
                d.rounded_rectangle((left - 4 * ss, y, right, y + row_h - 4 * ss), radius=radius, fill=(*self._entry, 255))
            tx, ty = int(left), int(y + (row_h - 4 * ss - thumb_size) / 2)
            glance = (getattr(self, "_chip_glance", None) or {}).get(item["path"], {})
            picture = None
            if item["kind"] == "image":
                picture = self._chip_thumbnail(item["path"], thumb_size)
            elif item["kind"] == "video" and glance.get("frame") is not None:
                picture = ImageOps.fit(glance["frame"], (thumb_size, thumb_size), Image.LANCZOS)
            if picture is not None:
                img.alpha_composite(picture, (tx, ty))
                if item["kind"] == "video":
                    self._play_mark(d, tx + thumb_size / 2 - 5 * ss, ty + thumb_size / 2 - 5 * ss, 10 * ss)
            elif item["kind"] in {"video", "audio"}:
                self._play_mark(d, tx + 3 * ss, ty + 3 * ss, thumb_size - 6 * ss)
            else:
                d.ellipse((tx + 10 * ss, ty + 10 * ss, tx + 16 * ss, ty + 16 * ss), fill=(*self._muted, 255))
            icons = [("details", f"result_open:{number}")]
            if item.get("continue"):
                icons.insert(0, ("chevron_right", f"result_continue:{number}"))
            if item["path"]:
                icons.insert(0, ("copy", f"result_save:{number}"))
            ix = right - len(icons) * 22 * ss
            text_left, text_right = left + thumb_size + 8 * ss, ix - 6 * ss
            d.text((text_left, y + 2 * ss), card.fit_text(d, item["title"], text_right - text_left, font), font=font,
                   fill=(*self._text, 255))
            d.text((text_left, y + 15 * ss), card.fit_text(d, item["detail"], text_right - text_left, font), font=font,
                   fill=(*self._muted, 255))
            for glyph, key in icons:
                colour = self._cyan if self._hovering(key) else self._muted
                img.alpha_composite(make_glyph_icon(glyph, 14 * ss, color="#%02x%02x%02x" % tuple(colour[:3])),
                                    (int(ix + 4 * ss), int(y + (row_h - 4 * ss - 14 * ss) / 2)))
                self._hit[key] = (int(ix / ss), int(y / ss), int((ix + 22 * ss) / ss), int((y + row_h - 4 * ss) / ss))
                ix += 22 * ss
            if item["path"] or item["job"]:
                self._hit[play_key if item["path"] else f"result_open:{number}"] = (
                    int(left / ss), int(y / ss), int(text_right / ss), int((y + row_h - 4 * ss) / ss))
        if len(items) > _RESULT_ROWS:                            # the list scrolls; a thin thumb shows where
            track = _RESULT_ROWS * row_h - 8 * ss
            thumb = max(8 * ss, track * _RESULT_ROWS / len(items))
            y = top + (track - thumb) * first / max(1, len(items) - _RESULT_ROWS)
            d.rounded_rectangle((left + width + 3 * ss, y, left + width + 4 * ss, y + thumb), radius=ss, fill=(*self._muted, 180))

    def _draw_privacy_band(self, img: Any, box: Any, panel_radius: int, panel_padding: int, ss: int) -> None:
        """Generate's privacy, the way browsing earlier replies looks: the panel keeps its size and dims
        (_dim_around_browse_line) around one lit band in the middle that says plainly what happens to
        attached files. Any click or key returns."""
        from PIL import ImageDraw
        from mo_desktop.generate_controls import PRIVACY_LINES

        step = 15 * ss
        block = step * len(PRIVACY_LINES)
        top = int((box[1] + box[3]) / 2 - block / 2)
        d = ImageDraw.Draw(img)
        d.rectangle((box[0] + 2 * ss, top - 4 * ss, box[2] - 2 * ss, top + block + 4 * ss), fill=(*self._card, 255))   # the border stays
        self._dim_around_browse_line(img, box, panel_radius * ss, top, block, ss)
        width = box[2] - box[0] - 2 * panel_padding * ss
        for index, text in enumerate(PRIVACY_LINES):
            d.text((box[0] + panel_padding * ss, top + index * step), card.fit_text(d, text, width, self._sfont),
                   font=self._sfont, fill=(*self._text, 255))
        self._hit = {"privacy_close": (int(box[0] / ss), int(box[1] / ss), int(box[2] / ss), int(box[3] / ss))}

    # ---- the empty composer's hint: each role's own words, in italic, a little dimmer ---------------
    _SEARCH_HINTS = {"google": "Search Google…", "youtube": "Search YouTube…", "translate": "Translate with Google…"}
    _DEFAULT_HINT = "Type a message…"                              # the default composer, unchanged

    def _composer_hint(self) -> str:
        """The search mode's words, else the chosen role's own hint, else the default "Type a message…"."""
        search = self._composer_search_provider()
        if search:
            return self._SEARCH_HINTS[search]
        role = str(getattr(self, "_role_label", "") or "")
        return str((getattr(self, "_role_hints", None) or {}).get(role, "") or self._DEFAULT_HINT)

    def _hint_fonts(self, ss: int) -> tuple[Any, Any]:
        from mo_desktop.fonts import load_font

        cache = self.__dict__.setdefault("_hint_font_cache", {})
        if ss not in cache:
            italic = load_font(("segoeuii.ttf", "ariali.ttf", "segoeui.ttf", "arial.ttf"), 13 * ss)
            try:
                from PIL import ImageFont

                emoji = ImageFont.truetype("seguiemj.ttf", 12 * ss)
            except OSError:
                emoji = None                                       # no colour emoji font: the words alone
            cache[ss] = (italic, emoji)
        return cache[ss]

    def _draw_composer_hint(self, d: Any, x: float, y: float, text: str, width: float) -> None:
        ss = int(getattr(self, "_ss", _SS) or _SS)
        italic, emoji = self._hint_fonts(ss)
        colour = (*self._muted, 200)                               # a little dimmer than other muted text
        runs, current, is_emoji = [], "", None
        for ch in str(text or ""):
            pictograph = ord(ch) >= 0x2190 and (ord(ch) >= 0x1F000 or 0x2190 <= ord(ch) <= 0x2BFF) or ch in "\ufe0f\u200d"
            if is_emoji is not None and pictograph != is_emoji:
                runs.append((current, is_emoji))
                current = ""
            current, is_emoji = current + ch, pictograph
        if current:
            runs.append((current, bool(is_emoji)))
        right = x + width
        for part, pictograph in runs:
            if pictograph:
                if emoji is None:
                    continue
                d.text((x, y + 1 * ss), part, font=emoji, embedded_color=True)
                x += d.textlength(part, font=emoji)
            else:
                fitted = card.fit_text(d, part, max(1, right - x), italic)
                d.text((x, y), fitted, font=italic, fill=colour)
                x += d.textlength(fitted, font=italic)

    def _open_attachment_file(self) -> None:
        """The media card's play mark: open the clip or song in the system's local player, as Saved
        results' Open / play does."""
        import os
        from pathlib import Path

        for raw in getattr(self, "_attachment_preview_paths", None) or []:
            path = Path(str(raw))
            if path.is_file():
                try:
                    os.startfile(str(path))  # type: ignore[attr-defined]
                except OSError:
                    pass
                return

    def _play_mark(self, d: Any, x: float, y: float, size: float) -> None:
        """MO's media mark (the media card's play disc) at any size."""
        d.ellipse((x, y, x + size, y + size), fill=(*self._cyan, 235))
        d.polygon([(x + size * .36, y + size * .25), (x + size * .36, y + size * .75), (x + size * .79, y + size / 2)],
                  fill=(*self._card, 255))

    def _draw_chip_glance(self, d: Any, img: Any, box: Any, rect: tuple[int, int, int, int], index: int) -> None:
        """A quick look at the file under the pointer, drawn in the card like the hover labels (no Windows
        tooltip window, no timer) above or below its chip: the picture itself, a clip's first frame or MO's media
        mark, with its name and what it is beside it."""
        from pathlib import Path
        from PIL import Image, ImageDraw

        paths = [str(p) for p in getattr(self, "_attachment_preview_paths", []) or []]
        if not 0 <= index < len(paths):
            return
        path, ss = paths[index], int(getattr(self, "_ss", _SS) or _SS)
        name = next((n for n, info in (self._chips or {}).items() if info["index"] == index), "")
        kind = self._chips[name]["kind"] if name else "file"
        glance = self.__dict__.setdefault("_chip_glance", {}).setdefault(path, {})
        info = glance.get("info") or {}
        if not info and kind == "image":
            try:
                with Image.open(path) as source:              # header only; the pixels load once, cached below
                    info = glance["info"] = {"width": source.width, "height": source.height}
            except OSError:
                pass
        margin, pad, gap = 6 * ss, 6 * ss, 8 * ss
        x0, y0, x1, y1 = (int(v) * ss for v in rect)
        above, below = y0 - box[1] - 2 * margin, box[3] - y1 - 2 * margin
        height = min(84 * ss, max(above, below))
        if height < 34 * ss:
            return
        side = (height - 2 * pad) // ss                       # the picture's logical box: as tall as fits
        picture = None
        if kind == "image":
            picture = self._image_attachment_preview((path,), min(side * 16 // 9, 150), side)
        elif kind == "video" and glance.get("frame") is not None:
            cache = self.__dict__.setdefault("_glance_frames", {})
            key = (path, side, ss)
            if key not in cache:
                frame = glance["frame"].copy()
                frame.thumbnail((min(side * 16 // 9, 150) * ss, side * ss), Image.LANCZOS)
                mark = max(12 * ss, min(frame.width, frame.height) // 3)
                self._play_mark(ImageDraw.Draw(frame), (frame.width - mark) / 2, (frame.height - mark) / 2, mark)
                cache[key] = frame
            picture = cache[key]
        try:
            size = int(info.get("bytes") or Path(path).stat().st_size)
        except OSError:
            size = 0
        facts = [kind.title()]
        if info.get("duration"):
            seconds = int(round(float(info["duration"])))
            facts.append(f"{seconds // 60}:{seconds % 60:02d}")
        if info.get("width") and info.get("height"):
            facts.append(f"{info['width']}×{info['height']}")
        if size:
            facts.append(f"{size / 1048576:.1f} MB" if size >= 1048576 else f"{max(1, size // 1024)} KB")
        details = " · ".join(facts)
        mark_size = min(side, 36) * ss
        pic_w = picture.width if picture is not None else (mark_size if kind in {"video", "audio"} else 0)
        pic_h = picture.height if picture is not None else mark_size
        font = getattr(self, "_sfont", None) or self._font
        close_w = 18 * ss                                     # the X that removes this file, top right
        room = box[2] - box[0] - 2 * margin - 2 * pad - (pic_w + gap if pic_w else 0) - close_w
        title = card.fit_text(d, Path(path).name, room, font)
        details = card.fit_text(d, details, room, font)
        text_w = max(self._text_width(d, title, font), self._text_width(d, details, font))
        width = 2 * pad + (pic_w + gap if pic_w else 0) + text_w + close_w
        left = max(box[0] + margin, min(x0, box[2] - margin - width))
        top = y0 - margin - height if above >= below else y1 + margin
        d.rounded_rectangle((left, top, left + width, top + height), radius=int(self._visuals.metrics.button_corner_radius) * ss,
                            fill=(*self._card, 255), outline=(*self._edge, 255), width=max(1, ss))
        px, py = int(left + pad), int(top + (height - pic_h) / 2)
        if picture is not None:
            img.alpha_composite(picture.convert("RGBA"), (px, py))
        elif kind in {"video", "audio"}:
            self._play_mark(d, px, py, mark_size)
        tx = left + pad + (pic_w + gap if pic_w else 0)
        d.text((tx, top + height / 2 - 15 * ss), title, font=font, fill=(*self._text, 255))
        d.text((tx, top + height / 2 + 1 * ss), details, font=font, fill=(*self._muted, 255))
        from interface.desktop_brand import make_glyph_icon

        remove_key = f"glance_remove:{index}"
        cx, cy = int(left + width - pad - 10 * ss), int(top + pad - 1 * ss)
        colour = self._text if self._hovering(remove_key) else self._muted
        img.alpha_composite(make_glyph_icon("close", 10 * ss, color="#%02x%02x%02x" % tuple(colour[:3])), (cx, cy))
        # The preview takes the pointer like the drop-down does: nothing under it is hit, the pointer can
        # cross from the chip onto it (the gap between them belongs to it) and stay while it reaches the X.
        panel = (left / ss, top / ss, (left + width) / ss, (top + height) / ss)
        self._hit = {key: bounds for key, bounds in self._hit.items() if key == f"chip:{index}" or not (
            bounds[0] < panel[2] and bounds[2] > panel[0] and bounds[1] < panel[3] and bounds[3] > panel[1])}
        self._hit[remove_key] = (int(cx / ss) - 7, int(cy / ss) - 7, int(cx / ss) + 17, int(cy / ss) + 17)
        bridge_top, bridge_bottom = (panel[1], y0 / ss) if top < y0 else (y1 / ss, panel[3])
        self._hit[f"glance:{index}"] = (int(panel[0]), int(bridge_top), int(panel[2]), int(bridge_bottom))

    def _reference_edge(self, cursor: int, step: int) -> tuple[int, int] | None:
        """The reference name just before (step -1) or after (+1) the cursor, as ``(start, end)``."""
        if not getattr(self, "_chips", None):
            return None
        from mo_desktop.generate_controls import REFERENCE_TOKEN

        for match in REFERENCE_TOKEN.finditer(str(self._body or "")):
            if match.group(0) in self._chips and (match.end() == cursor if step < 0 else match.start() == cursor):
                return match.start(), match.end()
        return None

    def place_new_references(self, previous: list[str]) -> None:
        """Each newly attached file sits in the sentence at the caret as its name ([Video1], [Image1]); a clip
        defaults to driving the motion and a picture to being the subject when a video is chosen."""
        from mo_desktop.generate_controls import default_role, reference_tokens

        paths = [str(p) for p in getattr(self, "_attachment_preview_paths", []) or []]
        names = {path: name for name, path in reference_tokens(paths).items()}
        roles = dict(getattr(self, "_generate_reference_roles", {}) or {})
        operation = (getattr(self, "_generate_selection", None) or {}).get("operation")
        known = {str(p) for p in previous}
        for path in paths:
            if path in known or path not in names:
                continue
            roles.setdefault(path, default_role(path, operation))
            body = str(getattr(self, "_body", "") or "")
            cursor = max(0, min(len(body), int(getattr(self, "_cursor", len(body)) or 0)))
            insert = ("" if not body[:cursor] or body[:cursor].endswith(" ") else " ") + names[path] + " "
            self._body = body[:cursor] + insert + body[cursor:]
            self._cursor = cursor + len(insert)
        self._generate_reference_roles = roles
        self._refresh_chips(True)

    def _remove_reference(self, index: int) -> None:
        """One attached file leaves the composer: its name leaves the sentence and every other name stays on
        its own file (removing [Image1] makes [Image2] the new [Image1])."""
        from mo_desktop.generate_controls import reference_tokens, renumber_references

        paths = [str(p) for p in getattr(self, "_attachment_preview_paths", []) or []]
        if not 0 <= index < len(paths):
            return
        before = reference_tokens(paths)
        path = paths.pop(index)
        after = reference_tokens(paths)
        body = str(getattr(self, "_body", "") or "")
        cursor = max(0, min(len(body), int(getattr(self, "_cursor", len(body)) or 0)))
        self._body = renumber_references(body, before, after)
        self._cursor = min(len(self._body), len(renumber_references(body[:cursor], before, after)))
        self._attachment_preview_paths = paths
        self._input_attachments = list(paths)
        roles = dict(getattr(self, "_generate_reference_roles", {}) or {})
        roles.pop(path, None)
        self._generate_reference_roles = roles
        self._refresh_chips(getattr(self, "_mode", "") == "input")   # the names moved: never act on old ones

    def _forget_references_left_out(self) -> None:
        """A chip deleted from the sentence (Backspace, Delete, cut or typing over everything) takes its file with
        it, so what is sent is always what the sentence shows."""
        chips = getattr(self, "_chips", None) or {}
        shown = getattr(self, "_chip_names_shown", None) or set()
        if not chips or not shown:
            return
        from mo_desktop.generate_controls import REFERENCE_TOKEN

        present = {match.group(0) for match in REFERENCE_TOKEN.finditer(str(getattr(self, "_body", "") or ""))}
        for index in sorted((chips[name]["index"] for name in shown - present if name in chips), reverse=True):
            self._remove_reference(index)

    def _refresh_chips(self, composing: bool) -> None:
        """The composer's chips for this draw, and which of them the sentence currently shows."""
        from mo_desktop.generate_controls import REFERENCE_TOKEN

        generate = str(getattr(self, "_role_label", "")).casefold() == "generate"
        self._chips = self._reference_chip_map(generate) if composing else {}
        self._chip_names_shown = {match.group(0) for match in REFERENCE_TOKEN.finditer(str(getattr(self, "_body", "") or ""))
                                  if match.group(0) in self._chips}

    def _wrap_spans(self, draw: Any, text: str, *, rich: bool = False, width: int | None = None) -> list[list[tuple[str, bool]]]:
        limit = int(width if width is not None else self._content_width(draw, text, rich=rich)) * int(getattr(self, '_ss', _SS) or _SS)
        lines: list[list[tuple[str, bool]]] = []
        for para in (str(text).splitlines() or [""]):
            parsed = parse_inline_emphasis(para) if rich else [(para, False)]
            words: list[tuple[str, bool]] = []
            for chunk, bold in parsed:
                for match in re.finditer(r"\S+", chunk):
                    word = match.group(0)
                    parts = split_token_to_width(
                        word, limit, lambda part, b=bold: self._line_width(draw, [(part, b)])
                    )
                    words.extend((part, bold) for part in parts)
            if not words:
                lines.append([("", False)])
                continue
            cur: list[tuple[str, bool]] = []
            cur_width = 0.0
            space = self._line_width(draw, [(" ", False)])
            for word, bold in words:
                # Line width is the sum of its spans, so extend it instead of
                # re-measuring the whole line for every candidate word.
                word_width = self._line_width(draw, [(word, bold)])
                trial_width = cur_width + (space if cur else 0.0) + word_width
                if trial_width <= limit or not cur:
                    cur = cur + ([(" ", False)] if cur else []) + [(word, bold)]
                    cur_width = trial_width
                else:
                    lines.append(cur)
                    cur = [(word, bold)]
                    cur_width = word_width
            lines.append(cur)
        return lines or [[("", False)]]

    def _content_width(self, draw: Any | None, text: str, *, rich: bool = False) -> int:
        design = getattr(self, "_design", DEFAULT_BUBBLE_DESIGN)
        state = getattr(self, "_panel_state", PanelState.REPLY)
        reply_width = getattr(design, "reply_content_width", None)
        if (
            getattr(self, "_mode", "reply") == "reply"
            and (state in {PanelState.REPLY, PanelState.FOOTERLESS} or self._controls_enabled)
            and reply_width
        ):
            return max(int(design.content_width), int(reply_width))
        return int(design.content_width)

    def _attachment_preview_surface(self, max_width: int, max_height: int) -> Any:
        state = getattr(self, "_panel_state", None)
        paths = tuple(
            path for path in (str(p or "").strip() for p in getattr(self, "_attachment_preview_paths", []))
            if path
        )
        if state == PanelState.IMAGE and paths:
            return self._image_attachment_preview(paths, max_width, max_height)
        if state == PanelState.MEDIA:
            return self._media_attachment_preview(max_width, max_height)
        return None

    def _image_attachment_preview(self, paths: tuple[str, ...], max_width: int, max_height: int) -> Any:
        ss = int(getattr(self, "_ss", _SS) or _SS)
        key = (paths[:4], int(max_width), int(max_height), ss, "image")
        if key == getattr(self, "_attachment_preview_cache_key", None):
            cached = getattr(self, "_attachment_preview_cache", None)
            return cached.copy() if cached is not None else None
        try:
            from PIL import Image, ImageDraw

            max_w = max(1, int(max_width)) * ss
            max_h = max(1, int(max_height)) * ss
            selected = paths[:4]
            if len(selected) == 1:
                with Image.open(selected[0]) as src:
                    source_mode = src.mode
                    img = src.convert("RGBA")
                if img.size[0] <= 0 or img.size[1] <= 0:
                    return None
                # Quantize in logical pixels before supersampling. The same image
                # must occupy identical card geometry on 1x/2x/3x render passes.
                scale = min(max_width / img.size[0], max_height / img.size[1])
                target = (
                    max(1, int(round(img.size[0] * scale))) * ss,
                    max(1, int(round(img.size[1] * scale))) * ss,
                )
                if target != img.size:
                    resample = Image.NEAREST if source_mode in {"1", "P"} else Image.LANCZOS
                    img = img.resize(target, resample)
                if img.size[0] <= 0 or img.size[1] <= 0:
                    return None
                surface = img
            else:
                gap = 6 * ss
                cols = 2
                rows = 2 if len(selected) > 2 else 1
                tile_w = max(1, (max_w - gap * (cols - 1)) // cols)
                tile_h = max(1, min((max_h - gap * (rows - 1)) // rows, tile_w))
                surface = Image.new("RGBA", (max_w, rows * tile_h + gap * (rows - 1)), (0, 0, 0, 0))
                draw = ImageDraw.Draw(surface)
                for idx, path in enumerate(selected):
                    with Image.open(path) as src:
                        tile = src.convert("RGBA")
                    tile.thumbnail((tile_w, tile_h), Image.LANCZOS)
                    col = idx % cols
                    row = idx // cols
                    x = col * (tile_w + gap) + max(0, (tile_w - tile.size[0]) // 2)
                    y = row * (tile_h + gap) + max(0, (tile_h - tile.size[1]) // 2)
                    draw.rounded_rectangle(
                        [col * (tile_w + gap), row * (tile_h + gap),
                         col * (tile_w + gap) + tile_w, row * (tile_h + gap) + tile_h],
                        radius=int(self._visuals.metrics.panel_corner_radius) * ss,
                        fill=(*self._edge, 80),
                    )
                    surface.alpha_composite(tile, (x, y))
                if len(paths) > len(selected):
                    draw.rounded_rectangle(
                        [surface.size[0] - 40 * ss, surface.size[1] - 20 * ss,
                         surface.size[0] - 4 * ss, surface.size[1] - 4 * ss],
                        radius=6 * ss,
                        fill=(*self._card, 235),
                        outline=(*self._edge, 255),
                    )
                    draw.text(
                        (surface.size[0] - 35 * ss, surface.size[1] - 18 * ss),
                        f"+{len(paths) - len(selected)}",
                        font=getattr(self, "_sfont", self._font),
                        fill=(*self._text, 255),
                    )
            if surface.size[0] <= 0 or surface.size[1] <= 0:
                return None
            self._attachment_preview_cache_key = key
            self._attachment_preview_cache = surface.copy()
            return surface
        except Exception:
            self._attachment_preview_cache_key = None
            self._attachment_preview_cache = None
            return None

    def _media_attachment_preview(self, max_width: int, max_height: int) -> Any:
        paths = tuple(
            path for path in (str(p or "").strip() for p in getattr(self, "_attachment_preview_paths", []))
            if path
        )
        ss = int(getattr(self, "_ss", _SS) or _SS)
        key = (paths[:4], int(max_width), int(max_height), ss, "media")
        if key == getattr(self, "_attachment_preview_cache_key", None):
            cached = getattr(self, "_attachment_preview_cache", None)
            return cached.copy() if cached is not None else None
        try:
            from PIL import Image, ImageDraw

            w = max(150, int(max_width)) * ss
            h = min(max(62, int(max_height)), 96) * ss
            surface = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(surface)
            draw.rounded_rectangle(
                [0, 0, w - 1, h - 1],
                radius=int(self._visuals.metrics.panel_corner_radius) * ss,
                fill=(*self._edge, 95),
            )
            cx, cy = 34 * ss, h // 2
            draw.ellipse([cx - 17 * ss, cy - 17 * ss, cx + 17 * ss, cy + 17 * ss],
                         fill=(*self._cyan, 235))
            draw.polygon(
                [(cx - 5 * ss, cy - 8 * ss), (cx - 5 * ss, cy + 8 * ss), (cx + 9 * ss, cy)],
                fill=(*self._card, 255),
            )
            bar_x = 68 * ss
            bar_w = max(20 * ss, w - bar_x - 18 * ss)
            for idx, level in enumerate((0.25, 0.62, 0.42, 0.84, 0.34, 0.56, 0.72, 0.30)):
                x = bar_x + idx * (bar_w / 8.5)
                bh = max(5 * ss, int((h - 28 * ss) * level))
                draw.rounded_rectangle(
                    [int(x), cy - bh // 2, int(x + 7 * ss), cy + bh // 2],
                    radius=3 * ss,
                    fill=(*self._cyan, 170),
                )
            self._attachment_preview_cache_key = key
            self._attachment_preview_cache = surface.copy()
            return surface
        except Exception:
            self._attachment_preview_cache_key = None
            self._attachment_preview_cache = None
            return None

    def _render(self) -> Any:
        ss = int(getattr(self, "_ss", _SS) or _SS)
        self._font, self._bfont, self._sfont, self._ifont = self._fonts(ss)
        if getattr(self, "_panel_state", None) == PanelState.HISTORY:
            return self._render_session_history()
        if getattr(self, "_panel_state", None) == PanelState.DASHBOARD:
            return self._render_dashboard()
        if getattr(self, "_panel_state", None) == PanelState.STATUS:
            return self._render_status()
        from PIL import Image, ImageDraw, ImageChops
        design = getattr(self, "_design", DEFAULT_BUBBLE_DESIGN)
        button_pad = int(self._visuals.metrics.button_padding) * ss
        button_radius = int(self._visuals.metrics.button_corner_radius) * ss
        probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
        is_input = self._mode == "input"
        generate_mode = is_input and str(getattr(self, "_role_label", "")).casefold() == "generate"
        self._refresh_chips(is_input)
        generate_top: list[tuple[str, str]] = []
        generate_pills: list[tuple[str, str]] = []
        if generate_mode and getattr(self, "_generate_selection", None):
            from mo_desktop.generate_controls import option_pills, top_pills
            credit = str(getattr(self, "_generate_credit", "") or "")
            if time.monotonic() < float(getattr(self, "_credit_detail_until", 0.0) or 0.0):
                credit = str(getattr(self, "_credit_detail", "") or credit)      # a click: the full balance, briefly
            generate_top = top_pills(self._generate_selection, credit)
            generate_pills = option_pills(self._generate_selection, len(getattr(self, "_attachment_preview_paths", [])))
        caret_rect: tuple[int, int, int, int] | None = None
        shown = self._body if (self._body or not is_input) else ""
        # Browsing MO's earlier replies happens IN the composer (Up/Down, the three dots).
        reply_history = list(getattr(self, "_reply_history", None) or []) if is_input else []
        browse_idx = getattr(self, "_browse_idx", None) if is_input else None
        browsing = browse_idx is not None and 0 <= browse_idx < len(reply_history)
        # Browsing earlier messages keeps the composer exactly as the draft sized it: the message's
        # head takes the first line and the panel dims around it (row 31, his correction).
        browse_head = (" ".join(str(reply_history[browse_idx].get("content") or "").split()) or "…") if browsing else ""
        placeholder = is_input and not shown
        dots = is_input and bool(reply_history)
        attachment_caption = (
            not is_input
            and not bool(getattr(self, "_controls_enabled", False))
            and getattr(self, "_panel_state", None)
            in {PanelState.FILE, PanelState.IMAGE, PanelState.MEDIA}
        )
        image_card = attachment_caption and self._panel_state == PanelState.IMAGE
        panel_padding = int(self._visuals.metrics.panel_padding)
        image_inset = max(6, panel_padding//2)
        body_top = image_inset if image_card else int(design.accent_top)+13
        generate_rows = (self._flow_pills(probe, generate_pills, int(design.content_width) - (_COMPOSER_DOTS_RESERVE if dots else 0))
                         if generate_pills else [])
        if generate_mode:   # only the rows that hold something: a picked kind's choices, references, progress
            progress = bool(getattr(self, "_generate_progress", ""))
            body_top += ((_GENERATE_ROW_H * len(generate_rows) + 9) if (generate_rows or progress) else 0) + \
                (_GENERATE_STATUS_H if progress else 0)
        rich_text = (not is_input) and (not placeholder) and not attachment_caption
        hint = self._composer_hint() if placeholder else ""
        wrap_text = shown if not placeholder else hint
        mail_reply = rich_text and shown.startswith(("**Gmail / ", "**Outlook / "))
        # The three dots sit above Send, so the composer's text wraps short of them.
        wrap_width = (self._content_width(probe, wrap_text, rich=rich_text) - _COMPOSER_DOTS_RESERVE) if dots else None
        wrap_key = (wrap_text, rich_text, ss, self._content_width(probe, wrap_text, rich=rich_text),
                    id(self._font), id(self._bfont), wrap_width)
        cached = getattr(self, "_wrapped_body", None)
        if cached is None or cached[0] != wrap_key:
            cached = (wrap_key, self._wrap_spans(probe, wrap_text, rich=rich_text, width=wrap_width))
            self._wrapped_body = cached
        all_lines = cached[1]
        content_width = self._content_width(probe, shown if not placeholder else hint,
                                            rich=rich_text)
        option_rows = list(getattr(self, "_options", []) or []) if not is_input else []
        option_key = (tuple((option.label, option.detail) for option in option_rows), content_width, ss, id(self._font))
        wrapped_options = getattr(self, "_wrapped_options", None)
        if wrapped_options is None or wrapped_options[0] != option_key:
            option_layout = []
            for option in option_rows:
                label_lines = self._wrap_spans(probe, option.label, width=content_width - 48)
                detail_lines = self._wrap_spans(probe, option.detail, width=content_width - 48) if option.detail else []
                height = max(36, 10 + len(label_lines) * 16 + len(detail_lines) * 14)
                option_layout.append((label_lines, detail_lines, height))
            wrapped_options = (option_key, option_layout)
            self._wrapped_options = wrapped_options
        option_layout = wrapped_options[1]
        options_content_h = sum(height + 6 for _label, _detail, height in option_layout)
        try:
            card_budget = max(320, int(screen_size()[1]) - 100)
        except Exception:
            card_budget = 668
        preview_height = min(232 if not option_rows else 168, max(64, card_budget - options_content_h - 120))
        preview_width = content_width+2*(panel_padding-image_inset) if image_card else content_width
        preview = None if generate_mode else self._attachment_preview_surface(preview_width, preview_height)
        # The preview slot morphs to the quick edit-tools grid when opened (image only,
        # gated). It occupies the SAME slot, so sizing/draw both key off ``tools_open``.
        tools_open = (
            preview is not None
            and self._panel_state == PanelState.IMAGE
            and bool(getattr(self, "_panel_tools_enabled", False))
            and bool(getattr(self, "_attachment_tools_allowed", True))
            and bool(getattr(self, "_panel_tools_open", False))
        )
        if preview is not None and not image_card:
            content_width = max(int(content_width), int(preview.size[0] / ss))
        preview_h = int(preview.size[1] / ss) if preview is not None else 0
        actions_h = 30 if preview is not None and self._panel_state == PanelState.IMAGE and bool(getattr(self, "_attachment_tools_allowed", True)) and not tools_open else 0
        if tools_open:
            preview_h = int(self._panel_tools_layout(
                int(content_width) * ss, ss, preview_h=preview.size[1],
            )["total_h"] / ss)
        preview_gap = 8 if preview is not None else 0
        has_footer_action = bool(
            str(getattr(self, "_footer_action_label", "") or "").strip()
            and callable(getattr(self, "_on_footer_action", None))
        )
        # Replies keep ↑ n/n ↓; the composer browses with the three dots above Send instead.
        nav_controls = bool(getattr(self, "_controls_enabled", True)) and not is_input
        # Options always retain their Submit footer, including while scrolling.
        footer = is_input or bool(option_rows) or (bool(self._body) and (nav_controls or has_footer_action))
        frame_h = (
            body_top + actions_h + preview_gap + 14
            + (int(design.footer_height) if footer else 0) + 2 * int(design.shadow_pad)
        )
        line_h = max(1, int(design.line_height))
        minimum_text = max(line_h, int(design.min_text_height))
        available_h = max(minimum_text, card_budget - preview_h - frame_h)
        # Replies keep one card size through a turn: text and options share what the fixed height
        # leaves. A notice outside any turn keeps its own size (his day-1 look, row 165 is per turn).
        fixed_reply = (not is_input and preview is None and not attachment_caption
                       and not getattr(self, "_fit_reply", False)
                       and getattr(self, "_panel_state", None) in (PanelState.REPLY, PanelState.FOOTERLESS))
        if fixed_reply:
            available_h = max(minimum_text, int(design.reply_card_height) - (frame_h - 2 * int(design.shadow_pad)))
        options_h = min(options_content_h + 8, max(48, available_h - minimum_text)) if option_rows else 0
        self._max_options_scroll = max(0, options_content_h - (options_h - 8)) if option_rows else 0
        self._options_scroll = max(0, min(int(getattr(self, "_options_scroll", 0)), self._max_options_scroll))
        self._options_region = None
        text_budget = (max(minimum_text, available_h - options_h) if fixed_reply
                       else max(minimum_text, min(int(design.max_text_height), available_h - options_h)))
        max_visible = max(1, text_budget // line_h)
        visible_count = max(1, min(len(all_lines), max_visible))
        self._max_scroll_line = max(0, len(all_lines) - visible_count)
        self._scroll_line = max(0, min(int(getattr(self, "_scroll_line", 0) or 0), self._max_scroll_line))
        cursor = max(0, min(len(shown), int(getattr(self, "_cursor", 0))))
        if not is_input:
            caret_lines = []
        elif cursor == len(shown) and not placeholder and not rich_text:
            caret_lines = all_lines  # the same text and settings the body was just wrapped with
        else:
            caret_lines = self._wrap_spans(probe, shown[:cursor], rich=False, width=wrap_width)
        caret_line = max(0, len(caret_lines) - 1)
        if is_input:
            if caret_line < self._scroll_line:
                self._scroll_line = caret_line
            elif caret_line >= self._scroll_line + visible_count:
                self._scroll_line = caret_line - visible_count + 1
        lines = all_lines[self._scroll_line:self._scroll_line + visible_count] or [""]
        if browse_head:
            head = card.fit_text(probe, browse_head, max(1, int(wrap_width or content_width)) * ss, self._font)
            lines = [[(head, False)]] + [[] for _line in lines[1:]]
        privacy = generate_mode and bool(getattr(self, "_privacy_open", False))   # drawn last, at this size
        results = generate_mode and bool(getattr(self, "_results_open", False)) and not privacy
        if results:   # Saved results: a compact list in the text area while it is open
            shown_rows = max(1, min(len(getattr(self, "_results", None) or []), _RESULT_ROWS))
            lines = [[] for _row in range(-(-shown_rows * _RESULT_ROW_H // line_h))]
            visible_count = len(lines)
        text_h = (text_budget if fixed_reply else
                  max(int(design.min_text_height), min(int(design.max_text_height), visible_count * line_h)))
        panel_radius = int(self._visuals.metrics.panel_corner_radius)
        card_w = int(content_width) + 2 * panel_padding
        card_h = (
            body_top + preview_h + actions_h + preview_gap + text_h
            + options_h + (int(design.footer_height) if footer else 0) + 14
        )
        open_menu = getattr(self, "_menu", None) if is_input else None
        if open_menu:   # the compact composer grows (gliding) while a drop-down needs the room, then returns
            owner = str(open_menu.get("owner", ""))
            key = owner[len("generate:"):] if owner.startswith("generate:") else ""
            row_of = {pill: index for index, row in enumerate(generate_rows) for pill, _text, _width in row}
            if owner == "role" or key:
                anchor = int(design.accent_top) + (44 + _GENERATE_ROW_H * row_of[key] if key in row_of else 12)
                card_h = max(card_h, anchor + 3 + min(6, len(open_menu.get("choices", ()))) * 20 + 6 + 7)
        W = (card_w + 2 * int(design.shadow_pad)) * ss
        H = (card_h + 2 * int(design.shadow_pad)) * ss
        pad = int(design.shadow_pad) * ss
        rad = panel_radius * ss
        box = [pad, pad, W - pad, H - pad]

        # Base card (soft shadow + rounded fill + border) via the shared primitive, so the
        # panel matches the cube's floating hint. All four corners stay rounded; positioning
        # keeps the panel cube-adjacent (a separate dock bridge read as a square artifact).
        provider = self._composer_search_provider() if is_input else ""
        from interface.desktop_brand import SEARCH_SERVICE_COLORS
        colors = SEARCH_SERVICE_COLORS.get(provider, ((*self._edge, 255), (*self._cyan, 110), (*self._edge, 255)))
        # The shadow, fill and edge depend only on geometry and palette, so typing and
        # scrolling inside one card size reuse them; the cached base is never drawn on.
        blur = self._blur_enabled()     # row 32: Windows blurs the screen under a see-through card
        base_key = (W, H, tuple(box), rad, tuple(self._card), tuple(self._edge), ss,
                    int(design.shadow_alpha), int(design.shadow_blur), colors, blur)
        base = getattr(self, "_card_base", None)
        if base is None or base[0] != base_key:
            canvas = card.draw_card(
                card.new_canvas(W, H), tuple(box), radius=rad, fill=(*self._card, BLUR_CARD_ALPHA if blur else 255),
                edge=(*self._edge, 255), edge_width=max(1, ss),
                shadow_alpha=0 if blur else int(design.shadow_alpha),
                shadow_blur=int(design.shadow_blur) * ss, shadow_dy=6 * ss)
            card.gradient_edge(canvas, tuple(box), radius=rad, width=max(1, ss), colors=colors)
            base = self._card_base = (base_key, canvas)
        img = base[1].copy()
        d = ImageDraw.Draw(img)
        ax, ay = pad + panel_padding * ss, pad + int(design.accent_top) * ss
        if is_input:
            # The composer's single cube is also its search switch: a click turns it into Google,
            # YouTube or Translate (their brand marks) and back to MO's cube.
            provider = self._composer_search_provider() if callable(getattr(self, "_on_web_search", None)) else ""
            if provider:
                self._draw_search_brand(img, d, provider, ax, ay, ss)
            else:
                edge = 10*ss
                radius = round(edge*float(getattr(self._cube, "_corner", .15)))
                d.rounded_rectangle((ax, ay-3*ss, ax+edge, ay+7*ss), radius=radius,
                                    fill=(*getattr(self._cube, "_color_rgb", self._cyan), 255))
        elif not image_card:
            d.rounded_rectangle([ax, ay, ax + 26 * ss, ay + 3 * ss], radius=ss, fill=(*self._cyan, 255))
        self._hit = {}

        def _set_hit(key: str, rect: tuple[int, int, int, int], *,
                     min_width: int = 0, min_height: int = 0) -> None:
            """Register a forgiving 1x hit target without changing the drawn control."""
            x0, y0, x1, y1 = rect
            if x1 - x0 < min_width:
                grow = min_width - (x1 - x0)
                x0 -= grow // 2
                x1 += grow - grow // 2
            if y1 - y0 < min_height:
                grow = min_height - (y1 - y0)
                y0 -= grow // 2
                y1 += grow - grow // 2
            self._hit[key] = (
                max(0, x0), max(0, y0), min(W // ss, x1), min(H // ss, y1),
            )

        if is_input:
            _set_hit("search_cycle" if callable(getattr(self, "_on_web_search", None)) else "collapse",
                     (int(ax/ss)-6, int(ay/ss)-7, int(ax/ss)+18, int(ay/ss)+12))
            role_right = ax + 16 * ss
            if callable(getattr(self, "_on_role_select", None)):
                label = card.fit_text(d, getattr(self, "_role_label", "") or "Default role",
                                      (content_width-34)*ss, self._sfont)
                role_right = min(box[2]-panel_padding*ss, ax+40*ss+d.textlength(label, font=self._sfont))
                d.rounded_rectangle((ax+21*ss, ay-8*ss, role_right, ay+12*ss),
                    radius=button_radius, fill=(*self._entry, 255))
                d.text((ax+28*ss, ay-5*ss), label, font=self._sfont,
                       fill=(*(self._cyan if self._hovering("role") else self._muted), 255))
                _set_hit("role", (int(ax/ss)+21, int(ay/ss)-8, int(role_right/ss), int(ay/ss)+12))

            if generate_mode:
                menu_owner = str((open_menu or {}).get("owner", ""))
                # Beside the role: the kind, the provider and the credits (privacy's '!' sits with the icons by Send).
                left, limit = role_right + 5 * ss, box[2] - panel_padding * ss
                for index, (key, text) in enumerate(generate_top):
                    reserve = (34 + 5) * ss * (len(generate_top) - index - 1)
                    width = max(34 * ss, min(self._pill_width(d, key, text), limit - left - reserve))
                    _set_hit("generate:" + key, self._draw_generate_pill(d, key, text, left, ay - 8 * ss, width, 20 * ss,
                                                                          menu_owner))
                    left += width + 5 * ss
                cy = ay + 22 * ss
                for row in generate_rows:                          # a picked kind's choices, in order
                    left = ax
                    for key, text, width in row:
                        _set_hit("generate:" + key, self._draw_generate_pill(d, key, text, left, cy, width, 22 * ss,
                                                                              menu_owner))
                        left += width + 5 * ss
                    cy += _GENERATE_ROW_H * ss
                note = getattr(self, "_generate_progress", "")
                if note:                                           # progress only; no standing sentence
                    d.text((ax, cy + 2 * ss), card.fit_text(d, note, content_width * ss, self._sfont), font=self._sfont, fill=(*self._muted, 255))

        if not is_input and not image_card and callable(getattr(self, "_on_session_history", None)):
            hcol = self._cyan if self._hovering("sessions") else self._muted
            hps = max(10, int(getattr(getattr(self, "_panel_design", DEFAULT_DESKTOP_PANEL_DESIGN), "pin_icon_size", 14) or 14)) * ss
            hx = box[2] - panel_padding * ss - hps - 22 * ss
            hy = box[1] + int(design.accent_top) * ss - 4 * ss
            d.ellipse([hx + 2 * ss, hy + 2 * ss, hx + 13 * ss, hy + 13 * ss], outline=(*hcol, 255), width=max(1, ss))
            d.line([(hx + 7.5 * ss, hy + 4 * ss), (hx + 7.5 * ss, hy + 8 * ss), (hx + 10.5 * ss, hy + 10 * ss)],
                   fill=(*hcol, 255), width=max(1, ss))
            _set_hit("sessions", (int(hx / ss) - 2, int(hy / ss) - 2, int(hx / ss) + 17, int(hy / ss) + 17),
                     min_width=26, min_height=26)
        if not is_input and not image_card:
            # Copy MO's message. Two offset rounded squares — the universal copy mark. The old
            # control here was a pin drawn from four strokes, which read as the letter "t".
            copy_col = (self._cyan if bool(getattr(self, "_copied", False)) or self._hovering("copy")
                        else self._muted)
            ps = max(10, int(getattr(getattr(self, "_panel_design", DEFAULT_DESKTOP_PANEL_DESIGN),
                                     "pin_icon_size", 14) or 14)) * ss
            pr = box[2] - panel_padding * ss
            px = pr - ps
            py = box[1] + int(design.accent_top) * ss - 3 * ss
            r = max(1, int(ps * 0.16))
            w = max(1, ss)
            back = (px + ps * 0.30, py + ps * 0.06, px + ps * 0.96, py + ps * 0.72)
            front = (px + ps * 0.04, py + ps * 0.30, px + ps * 0.70, py + ps * 0.96)
            d.rounded_rectangle(back, radius=r, outline=(*copy_col, 150), width=w)
            d.rounded_rectangle(front, radius=r, outline=(*copy_col, 255), width=w,
                                fill=(*self._card, 255))
            _set_hit(
                "copy",
                (int((px - 3 * ss) / ss), int((py - 3 * ss) / ss),
                 int((pr + 3 * ss) / ss), int((py + ps + 3 * ss) / ss)),
                min_width=32,
                min_height=32,
            )
        # body / input text
        ty = pad + body_top * ss
        tx = ax
        fill = self._muted if (placeholder and not browse_head) or attachment_caption else self._text
        body_font = self._sfont if attachment_caption else self._font
        content_r = pad + panel_padding * ss + int(content_width) * ss
        if preview is not None:
            slot_w = preview.size[0]
            px = pad + (card_w*ss-slot_w)//2
            py = ty
            if tools_open:
                tw = int(content_width) * ss          # full-width tools slot (left-aligned)
                slot_h = self._panel_tools_layout(tw, ss, preview_h=preview.size[1])["total_h"]
                self._draw_panel_tools(d, img, ax, py, tw, ss, preview)
                ty += slot_h + preview_gap * ss
            else:
                mask = Image.new("L", preview.size, 0)
                md = ImageDraw.Draw(mask)
                md.rounded_rectangle(
                    [0, 0, preview.size[0] - 1, preview.size[1] - 1],
                    radius=button_radius,
                    fill=255,
                )
                pr, pg, pb, pa = preview.split()
                pa = ImageChops.multiply(pa, mask)
                rounded = Image.merge("RGBA", (pr, pg, pb, pa))
                img.alpha_composite(rounded, (px, py))
                d.rounded_rectangle(
                    [px, py, px + preview.size[0], py + preview.size[1]],
                    radius=button_radius,
                    outline=(*self._edge, 200),
                    width=max(1, ss),
                )
                self._draw_panel_preview_actions(
                    d, img, ax, py, int(content_width) * ss, preview.size[1], ss,
                )
                if self._panel_state == PanelState.MEDIA:          # its play mark plays the song or clip
                    _set_hit("preview_play", (int(px / ss), int(py / ss), int((px + preview.size[0]) / ss),
                                              int((py + preview.size[1]) / ss)))
                ty += preview.size[1] + (actions_h + preview_gap) * ss
        text_y = ty
        if is_input:
            self._input_text_top = int(text_y // ss)    # where a height glide splits the finished card
        if results:
            self._draw_results(d, img, ax, text_y, content_width * ss)
        hinted = placeholder and not browse_head and not results and hint != self._DEFAULT_HINT   # the default stays as it was
        if hinted:
            self._draw_composer_hint(d, tx, ty, hint, content_width * ss)
        for idx, ln in enumerate([] if hinted else lines):
            mail_heading = mail_reply and self._scroll_line + idx == 0
            if mail_heading:
                heading_fill = tuple(int(base * 0.88 + accent * 0.12)
                                     for base, accent in zip(self._card, self._cyan))
                d.rounded_rectangle(
                    [tx - 5 * ss, ty - 2 * ss, content_r + 5 * ss,
                     ty + int(design.line_height) * ss - 2 * ss],
                    radius=button_radius * ss, fill=(*heading_fill, 255),
                )
            plain = plain_spans(ln)
            disp, rtl = shape_line(plain)  # Arabic/RTL: reshape + reorder so it isn't reversed
            if rtl:
                lw = int(d.textlength(disp, font=body_font))
                lx = content_r - lw
                if is_input and bool(getattr(self, "_select_all", False)) and not placeholder:
                    d.rectangle([lx, ty, lx + lw, ty + int(design.line_height) * ss], fill=(*self._cyan, 70))
                d.text((lx, ty), disp, font=body_font,
                       fill=(*(self._cyan if mail_heading else fill), 255))
            else:
                lw = int(self._line_width(d, ln))
                lx = tx
                if is_input and bool(getattr(self, "_select_all", False)) and not placeholder:
                    d.rectangle([lx, ty, lx + lw, ty + int(design.line_height) * ss], fill=(*self._cyan, 70))
                for piece, bold in ln:
                    font = (
                        self._sfont
                        if attachment_caption
                        else getattr(self, "_bfont", self._font) if bold else self._font
                    )
                    for part, name in self._chip_parts(piece):   # a plain piece unless Generate chips exist
                        if name and not placeholder:
                            lx += self._draw_reference_chip(d, img, lx, ty, name)
                        elif part:
                            d.text((lx, ty), part, font=font,
                                   fill=(*(self._cyan if mail_heading else fill), 255))
                            lx += float(d.textlength(part, font=font))
            if idx < len(lines) - 1:
                ty += int(design.line_height) * ss
        if is_input and not browsing and not privacy and not results and not bool(getattr(self, "_select_all", False)):
            caret_spans = caret_lines[-1] if caret_lines else [("", False)]
            caret_plain = plain_spans(caret_spans)
            caret_display, caret_rtl = shape_line(caret_plain)
            caret_w = int(d.textlength(caret_display, font=self._font)) if caret_rtl else int(self._line_width(d, caret_spans))
            cx = (content_r - caret_w) if caret_rtl else (ax + caret_w)
            cy = text_y + max(0, caret_line - self._scroll_line) * int(design.line_height) * ss
            caret_rect = (
                int((cx + 2 * ss) / ss),
                int(cy / ss),
                int((cx + 3 * ss) / ss),
                int((cy + 15 * ss) / ss),
            )

        if self._max_scroll_line:
            track_x = content_r + max(2, panel_padding // 2) * ss
            track_h = text_h * ss
            thumb_h = max(12 * ss, int(track_h * visible_count / len(all_lines)))
            thumb_y = text_y + int((track_h - thumb_h) * self._scroll_line / self._max_scroll_line)
            d.rounded_rectangle(
                [track_x, text_y, track_x + 2 * ss, text_y + track_h],
                radius=ss, fill=(*self._muted, 45),
            )
            d.rounded_rectangle(
                [track_x, thumb_y, track_x + 2 * ss, thumb_y + thumb_h],
                radius=ss, fill=(*self._muted, 170),
            )

        if option_rows:
            options_top = text_y + text_h * ss + 8 * ss
            viewport_h = (options_h - 8) * ss
            row_w = int(content_width) * ss
            options_image = Image.new("RGBA", (row_w, viewport_h))
            od = ImageDraw.Draw(options_image)
            oy = -self._options_scroll * ss
            self._options_region = (
                int(ax / ss), int(options_top / ss),
                int((ax + row_w) / ss), int((options_top + viewport_h) / ss),
            )
            selected = set(getattr(self, "_selected_options", set()) or set())
            single = str(getattr(self, "_option_mode", "multi")) == "single"
            for idx, option in enumerate(option_rows):
                label_lines, detail_lines, height = option_layout[idx]
                row_h = height * ss
                active = idx in selected
                highlighted = active or self._hovering(f"option:{idx}")
                edge = self._cyan if highlighted else self._muted
                od.rounded_rectangle(
                    [0, oy, row_w, oy + row_h], radius=button_radius,
                    fill=(*getattr(self, "_entry", self._edge), 255),
                    outline=(*(self._cyan if highlighted else self._edge), 255), width=max(1, ss),
                )
                ix, iy = 9 * ss, oy + 11 * ss
                mark = [ix, iy, ix + 13 * ss, iy + 13 * ss]
                if single:
                    od.ellipse(mark, outline=(*edge, 255), width=max(1, ss))
                    if active:
                        od.ellipse([ix + 4 * ss, iy + 4 * ss, ix + 9 * ss, iy + 9 * ss], fill=(*self._cyan, 255))
                else:
                    od.rounded_rectangle(mark, radius=3 * ss, outline=(*edge, 255), width=max(1, ss))
                    if active:
                        od.line([(ix + 3 * ss, iy + 7 * ss), (ix + 6 * ss, iy + 10 * ss),
                                (ix + 11 * ss, iy + 3 * ss)], fill=(*self._cyan, 255), width=max(1, 2 * ss))
                row_y = oy + 5 * ss
                for line in label_lines:
                    od.text((31 * ss, row_y), "".join(text for text, _bold in line),
                           font=self._font, fill=(*self._text, 255))
                    row_y += 16 * ss
                for line in detail_lines:
                    od.text((31 * ss, row_y), "".join(text for text, _bold in line),
                           font=self._sfont, fill=(*self._muted, 255))
                    row_y += 14 * ss
                if oy < viewport_h and oy + row_h > 0:
                    self._hit[f"option:{idx}"] = (
                        int(ax / ss), int((options_top + max(0, oy)) / ss),
                        int((ax + row_w) / ss), int((options_top + min(viewport_h, oy + row_h)) / ss),
                    )
                oy += (height + 6) * ss
            img.alpha_composite(options_image, (ax, options_top))
            if self._max_options_scroll:
                track_x = content_r + max(2, panel_padding // 2) * ss
                thumb_h = max(12 * ss, int(viewport_h * (options_h - 8) / options_content_h))
                thumb_y = options_top + int((viewport_h - thumb_h) * self._options_scroll / self._max_options_scroll)
                d.rounded_rectangle(
                    [track_x, options_top, track_x + 2 * ss, options_top + viewport_h],
                    radius=ss, fill=(*self._muted, 45),
                )
                d.rounded_rectangle(
                    [track_x, thumb_y, track_x + 2 * ss, thumb_y + thumb_h],
                    radius=ss, fill=(*self._muted, 170),
                )

        # footer: reply recall, history, composer searches, primary action, and context action.
        if footer:
            fy = box[3] - int(design.footer_height) * ss
            cyy = fy + 9 * ss
            right_edge = box[2] - panel_padding * ss
            if has_footer_action:
                # Icon only; the label remains the control's accessible name.
                label = str(getattr(self, "_footer_action_label", "") or "Report")[:18]
                px = button_pad
                ic = 13 * ss
                rx1 = right_edge
                rx0 = rx1 - ic - 2 * px
                d.rounded_rectangle([rx0, cyy - 4 * ss, rx1, cyy + 15 * ss], radius=button_radius,
                                    fill=(*self._edge, 255),
                                    outline=(*self._cyan, 255 if self._hovering("action") else 220),
                                    width=max(1, ss) * (2 if self._hovering("action") else 1))
                ix, iy = rx0 + px, cyy - 1 * ss
                if "send" in label.casefold():
                    from interface.desktop_brand import make_glyph_icon
                    glyph = make_glyph_icon("share", ic, color="#%02x%02x%02x" % tuple(self._cyan))
                    img.alpha_composite(glyph, (int(ix), int(iy)))
                else:   # a circled "!" for reporting
                    d.ellipse([ix, iy, ix + 10 * ss, iy + 10 * ss], outline=(*self._cyan, 255), width=ss)
                    d.line([(ix + 5 * ss, iy + 2 * ss), (ix + 5 * ss, iy + 6 * ss)],
                           fill=(*self._cyan, 255), width=ss)
                    d.point((ix + 5 * ss, iy + 8 * ss), fill=(*self._cyan, 255))
                _set_hit(
                    "action",
                    (int(rx0 / ss), int((cyy - 5 * ss) / ss),
                     int(rx1 / ss), int((cyy + 16 * ss) / ss)),
                    min_width=48,
                    min_height=28,
                )
                right_edge = rx0 - 6 * ss

            if option_rows:
                count = len(getattr(self, "_selected_options", set()) or set())
                submitted = bool(getattr(self, "_options_submitted", False))
                label = "Working…" if submitted else f"Submit > {count}"
                lw = int(d.textlength(label, font=self._sfont)); px = button_pad
                rx1 = right_edge
                rx0 = rx1 - lw - 2 * px
                active = count > 0 and not submitted
                d.rounded_rectangle([rx0, cyy - 4 * ss, rx1, cyy + 15 * ss], radius=button_radius,
                                    fill=(*(self._cyan if active else self._edge), 255),
                                    outline=(*self._cyan, 255), width=max(1, ss))
                d.text((rx0 + px, cyy - 3 * ss), label, font=self._sfont,
                       fill=(*(self._text if active else self._muted), 255))
                if not submitted:
                    _set_hit(
                        "options_submit",
                        (int(rx0 / ss), int((cyy - 5 * ss) / ss),
                         int(rx1 / ss), int((cyy + 16 * ss) / ss)),
                        min_width=48,
                        min_height=28,
                    )
                right_edge = rx0 - 6 * ss
            if is_input:
                label = "Search" if self._composer_search_provider() else "Send"
                lw = int(d.textlength(label, font=self._sfont)); px = button_pad
                rx1 = right_edge
                rx0 = rx1 - lw - 2 * px
                d.rounded_rectangle([rx0, cyy - 4 * ss, rx1, cyy + 15 * ss], radius=button_radius,
                                    fill=(*self._cyan, 255),
                                    outline=(*self._text, 255) if self._hovering("send") else None,
                                    width=max(1, ss))
                d.text((rx0 + px, cyy - 3 * ss), label, font=self._sfont,
                       fill=(*getattr(self, "_accent_ink", self._card), 255))
                _set_hit(
                    "send",
                    (int(rx0 / ss), int((cyy - 5 * ss) / ss),
                     int(rx1 / ss), int((cyy + 16 * ss) / ss)),
                    min_width=48,
                    min_height=28,
                )
                if callable(getattr(self, "_on_attach", None)):
                    from interface.desktop_brand import make_glyph_icon
                    color = self._cyan if self._hovering("attach") else self._muted
                    clip_x = rx0 - 8 * ss - 15 * ss
                    img.alpha_composite(make_glyph_icon("clip", 15 * ss, color="#%02x%02x%02x" % tuple(color)),
                                        (int(clip_x), int(cyy - 2 * ss)))
                    _set_hit("attach", (int(clip_x / ss) - 4, int((cyy - 5 * ss) / ss), int(clip_x / ss) + 19,
                                        int((cyy + 16 * ss) / ss)), min_width=26, min_height=28)
                if generate_mode:          # Refine: MO's prompt enhancer for this request (Generate only)
                    busy = getattr(self, "_generate_refining", False)
                    color = self._cyan if (busy or self._hovering("generate:enhance")) else self._muted
                    spark_x = (clip_x if callable(getattr(self, "_on_attach", None)) else rx0) - 8 * ss - 14 * ss
                    mx, my, outer, inner = spark_x + 7 * ss, cyy + 5.5 * ss, 6 * ss, 1.7 * ss
                    d.polygon([(mx, my - outer), (mx + inner, my - inner), (mx + outer, my), (mx + inner, my + inner),
                               (mx, my + outer), (mx - inner, my + inner), (mx - outer, my), (mx - inner, my - inner)],
                              fill=(*color, 255))
                    d.ellipse((mx + 5.6 * ss - 1.3 * ss, my - 5.6 * ss - 1.3 * ss, mx + 5.6 * ss + 1.3 * ss, my - 5.6 * ss + 1.3 * ss),
                              fill=(*color, 255))
                    _set_hit("generate:enhance", (int(spark_x / ss) - 4, int((cyy - 5 * ss) / ss), int(spark_x / ss) + 18,
                                                  int((cyy + 16 * ss) / ss)), min_width=26, min_height=28)
                    from interface.desktop_brand import make_glyph_icon
                    results_x = spark_x - 8 * ss - 15 * ss        # Saved results: a button, not a menu item
                    color = self._cyan if (self._hovering("generate:jobs") or getattr(self, "_results_open", False)) else self._muted
                    img.alpha_composite(make_glyph_icon("folder", 15 * ss, color="#%02x%02x%02x" % tuple(color)),
                                        (int(results_x), int(cyy - 2 * ss)))
                    _set_hit("generate:jobs", (int(results_x / ss) - 4, int((cyy - 5 * ss) / ss), int(results_x / ss) + 19,
                                               int((cyy + 16 * ss) / ss)), min_width=26, min_height=28)
                    privacy_x = results_x - 8 * ss - 12 * ss      # privacy: a small '!' with the other icons
                    self._draw_privacy_mark(d, privacy_x, cyy - 0.5 * ss, 12 * ss)
                    _set_hit("generate:privacy", (int(privacy_x / ss) - 4, int((cyy - 5 * ss) / ss), int(privacy_x / ss) + 16,
                                                  int((cyy + 16 * ss) / ss)), min_width=24, min_height=28)
                # MO's own hover label for the icon-only controls, drawn in the card itself the way
                # Focus reveals a pin's name: no Windows tooltip window, no delay timer.
                tip, tip_right = "", 0
                if callable(getattr(self, "_on_attach", None)) and self._hovering("attach"):
                    tip = "Add references · several at once" if generate_mode else "Attach files · several at once"
                    tip_right = (privacy_x if generate_mode else clip_x) - 6 * ss   # never over the other icons
                if generate_mode and self._hovering("generate:enhance"):
                    tip = "Refining the prompt…" if busy else "Refine the prompt"
                    tip_right = privacy_x - 6 * ss
                if generate_mode and self._hovering("generate:jobs"):
                    tip, tip_right = "Saved results", privacy_x - 6 * ss
                if generate_mode and self._hovering("generate:privacy"):
                    tip, tip_right = "Privacy", privacy_x - 6 * ss
                if tip:
                    tip_left = max(box[0] + panel_padding * ss, tip_right - d.textlength(tip, font=self._sfont) - 16 * ss)
                    d.rounded_rectangle((tip_left, cyy - 4 * ss, tip_right, cyy + 15 * ss), radius=button_radius,
                                        fill=(*self._entry, 255), outline=(*self._edge, 120), width=max(1, ss))
                    d.text((tip_left + 8 * ss, cyy - 3 * ss), card.fit_text(d, tip, tip_right - tip_left - 16 * ss, self._sfont),
                           font=self._sfont, fill=(*self._muted, 255))
                if dots and not results and not privacy:
                    # MO's earlier replies: three dots at the right edge, centred, an arrow above and
                    # below. The bottom dot is your draft, the middle one MO's last reply, the top one
                    # anything older; the upper half moves back, the lower half forward.
                    lit = 2 if not browsing else (1 if browse_idx == len(reply_history) - 1 else 0)
                    dot_x = box[2] - 10 * ss
                    low, high = box[1] + 40 * ss, cyy - 30 * ss
                    mid = (box[1] + box[3]) // 2
                    mid = min(max(mid, low), high) if low <= high else (box[1] + cyy) // 2
                    for index in range(3):                         # quiet: dim dots, the lit one softly cyan
                        dot_y = mid + (index - 1) * 6 * ss
                        hot = (index < 2 and self._hovering("dots_up")) or (index == 2 and self._hovering("dots_down"))
                        alpha = 150 if index == lit or hot else 60
                        color = self._cyan if index == lit or hot else self._muted
                        d.ellipse([dot_x - 1.6 * ss, dot_y - 1.6 * ss, dot_x + 1.6 * ss, dot_y + 1.6 * ss], fill=(*color, alpha))
                    for direction, key in ((-1, "dots_up"), (1, "dots_down")):     # small, dimmed arrows
                        tip_y = mid + direction * 18 * ss
                        hot = self._hovering(key)
                        color, alpha = (self._cyan, 220) if hot else (self._muted, 150)
                        d.line([(dot_x - 3 * ss, tip_y - direction * 1.8 * ss), (dot_x, tip_y + direction * 1.2 * ss),
                                (dot_x + 3 * ss, tip_y - direction * 1.8 * ss)], fill=(*color, alpha),
                               width=max(1, int(1.2 * ss)), joint="curve")
                    mid_y = int(mid / ss)
                    _set_hit("dots_up", (int(dot_x / ss) - 9, mid_y - 27, int(dot_x / ss) + 9, mid_y))
                    _set_hit("dots_down", (int(dot_x / ss) - 9, mid_y, int(dot_x / ss) + 9,
                                           min(mid_y + 27, self._hit["send"][1] - 1)))   # never over Send
            elif bool(getattr(self, "_controls_enabled", True)):
                # Reply: icon only (a drawn return arrow on the accent fill).
                px = button_pad; ic = 13 * ss
                rx1 = right_edge
                rx0 = rx1 - 2 * px - ic
                d.rounded_rectangle([rx0, cyy - 4 * ss, rx1, cyy + 15 * ss], radius=button_radius,
                                    fill=(*self._cyan, 255),
                                    outline=(*self._text, 255) if self._hovering("reply") else None,
                                    width=max(1, ss))
                ink = getattr(self, "_accent_ink", self._card)
                ix, iy = rx0 + px, cyy + 6 * ss
                d.line([(ix + 9 * ss, iy - 4 * ss), (ix + 9 * ss, iy), (ix, iy)], fill=ink, width=ss)
                d.line([(ix, iy), (ix + 4 * ss, iy - 3 * ss)], fill=ink, width=ss)
                d.line([(ix, iy), (ix + 4 * ss, iy + 3 * ss)], fill=ink, width=ss)
                _set_hit(
                    "reply",
                    (int(rx0 / ss), int((cyy - 5 * ss) / ss),
                     int(rx1 / ss), int((cyy + 16 * ss) / ss)),
                    min_width=48,
                    min_height=28,
                )
                if nav_controls and len(getattr(self, "_reply_history", None) or []) > 1:
                    # The composer's three dots, just left of Reply: the lit one is where you are
                    # (top = older replies, middle = the newest). Click above or below to move.
                    history_count = len(self._reply_history)
                    at_newest = int(getattr(self, "_reply_idx", 0) or 0) >= history_count - 1
                    dot_x = rx0 - 10 * ss
                    mid_y = cyy + 5 * ss
                    for index, dot_y in enumerate((mid_y - 6 * ss, mid_y, mid_y + 6 * ss)):
                        lit = index == (1 if at_newest else 0)
                        hot = (index == 0 and self._hovering("up")) or (index == 2 and self._hovering("down"))
                        color = self._cyan if lit or hot else self._muted
                        d.ellipse([dot_x - 2 * ss, dot_y - 2 * ss, dot_x + 2 * ss, dot_y + 2 * ss],
                                  fill=(*color, 210 if lit or hot else 95))
                    _set_hit("up", (int(dot_x / ss) - 10, int((mid_y - 14 * ss) / ss), int(dot_x / ss) + 8, int(mid_y / ss)),
                             min_width=28)
                    _set_hit("down", (int(dot_x / ss) - 10, int(mid_y / ss), int(dot_x / ss) + 8, int((mid_y + 14 * ss) / ss)),
                             min_width=28)

        hovered = str(getattr(self, "_hover", "") or getattr(self, "_keyboard_hit", "") or "")
        prefix, _sep, number = hovered.rpartition(":")
        if (is_input and not open_menu and prefix in {"chip", "glance", "glance_remove"} and number.isdigit()
                and f"chip:{number}" in self._hit):   # the preview stays while the pointer is on it or its X
            self._draw_chip_glance(d, img, box, self._hit[f"chip:{number}"], int(number))
        if open_menu and open_menu.get("owner") in self._hit:
            # One opaque drop-down under its control (the role selector or a Generate pill);
            # it never resizes or replaces the composer and scrolls when it cannot fit.
            choices = open_menu["choices"]
            anchor = self._hit[open_menu["owner"]]
            menu_left, menu_top = anchor[0]*ss, anchor[3]*ss+3*ss
            available = max(1, int((box[3]-7*ss-menu_top)/(20*ss)))
            self._menu_capacity = min(6, available)
            scroll = int(open_menu.get("scroll", 0))
            visible = choices[scroll:scroll+self._menu_capacity]
            label_width = max(d.textlength(label, font=self._sfont) for _value, label in choices)
            menu_right = min(box[2]-8*ss, max(anchor[2]*ss, menu_left+label_width+24*ss))
            menu_left = max(box[0]+8*ss, min(menu_left, menu_right-label_width-24*ss))
            menu_bottom = menu_top+(len(visible)*20+6)*ss
            d.rounded_rectangle((menu_left, menu_top, menu_right, menu_bottom), radius=button_radius,
                                 fill=(*self._card, 255), outline=(*self._edge, 255), width=ss)
            # Underlying controls cannot receive a click through this dropdown.
            self._hit = {key: bounds for key, bounds in self._hit.items() if
                         not (bounds[0]*ss < menu_right and bounds[2]*ss > menu_left and
                              bounds[1]*ss < menu_bottom and bounds[3]*ss > menu_top)}
            for offset, (value, label) in enumerate(visible):
                key = f"menu:{offset+scroll}"
                top = menu_top+(3+offset*20)*ss
                bounds = (menu_left+3*ss, top, menu_right-3*ss, top+20*ss)
                if self._hovering(key) or value == open_menu.get("selected"):
                    d.rounded_rectangle(bounds, radius=button_radius, fill=(*self._entry, 255))
                text = card.fit_text(d, label, menu_right-menu_left-16*ss, self._sfont)
                d.text((menu_left+8*ss, top+3*ss), text, font=self._sfont,
                       fill=(*(self._cyan if value == open_menu.get("selected") else self._text), 255))
                _set_hit(key, tuple(round(v/ss) for v in bounds))
            if len(choices) > self._menu_capacity:
                track = max(8*ss, menu_bottom-menu_top-8*ss)
                thumb = max(8*ss, track*self._menu_capacity/len(choices))
                ty = menu_top+4*ss+(track-thumb)*scroll/max(1, len(choices)-self._menu_capacity)
                d.rounded_rectangle((menu_right-3*ss, ty, menu_right-2*ss, ty+thumb), radius=ss, fill=(*self._muted, 180))
            caret_rect = None
        if browse_head:
            self._dim_around_browse_line(img, box, panel_radius * ss, pad + body_top * ss, line_h * ss, ss)
            if callable(getattr(self, "_on_session_history", None)):
                hx = box[2] - panel_padding * ss - 18 * ss
                hy = ay - 6 * ss
                col = self._cyan if self._hovering("sessions") else self._text
                d2 = ImageDraw.Draw(img)
                d2.ellipse([hx + 3 * ss, hy + 2 * ss, hx + 14 * ss, hy + 13 * ss], outline=(*col, 255), width=max(1, ss))
                d2.line([(hx + 8.5 * ss, hy + 4 * ss), (hx + 8.5 * ss, hy + 8 * ss), (hx + 11.5 * ss, hy + 10 * ss)],
                        fill=(*col, 255), width=max(1, ss))
                _set_hit("sessions", (int(hx / ss), int(hy / ss) - 2, int(hx / ss) + 18, int(hy / ss) + 16),
                         min_width=26, min_height=26)
        if privacy:   # browsing's dimming at this size: a lit band in the middle carries the lines
            self._draw_privacy_band(img, box, panel_radius, panel_padding, ss)
        finished = card.finish(img, ss)   # premultiply-then-downscale (shared primitive)
        fade_from = getattr(self, "_browse_from", None) if is_input else None
        if fade_from is not None:
            amount = (time.perf_counter() - float(getattr(self, "_browse_started", 0.0))) / _BROWSE_FADE_SECONDS
            if amount < 1 and fade_from.size == finished.size and fade_from.mode == finished.mode:
                from PIL import Image as _Image
                finished = _Image.blend(fade_from, finished, max(0.0, amount))
                try:
                    self._gui.schedule(16, self._repaint)
                except Exception:
                    self._browse_from = None
            else:
                self._browse_from = None
        if is_input:
            # The resting card is expensive to build (shadow, fonts, RTL, wrapping) but
            # the blinking caret is only a tiny opaque rectangle. Cache the caret-free
            # finished frame so blink ticks can update that rectangle without rendering
            # the whole supersampled panel twice per second.
            self._input_base_image = finished
            self._input_caret_rect = caret_rect
            return self._input_frame_with_caret()
        self._input_base_image = None
        self._input_caret_rect = None
        return finished

    # ---- reusable chevron + panel-tools morph --------------------------------
    def _chevron(self, d: Any, cx: float, cy: float, size: float, direction: str,
                 color: tuple[int, int, int], ss: int, width: int | None = None) -> None:
        """Draw a chevron centered at (cx, cy) — the reusable more/back mark, same
        3-point ``d.line`` language as the footer history chevrons. ``direction`` is
        'right' (>, more), 'left' (<, back), 'up', or 'down'; ``size`` is the half-extent.
        Coordinates are supersampled px, so callers pass ``* ss`` sizes."""
        h = float(size)
        w = max(1, int(width if width is not None else ss))
        pts = {
            "right": [(cx - h, cy - h), (cx + h, cy), (cx - h, cy + h)],
            "left": [(cx + h, cy - h), (cx - h, cy), (cx + h, cy + h)],
            "up": [(cx - h, cy + h), (cx, cy - h), (cx + h, cy + h)],
            "down": [(cx - h, cy - h), (cx, cy + h), (cx + h, cy - h)],
        }.get(direction, [(cx - h, cy - h), (cx + h, cy), (cx - h, cy + h)])
        d.line(pts, fill=(*color, 255), width=w)









    def _input_frame_with_caret(self) -> Any:
        base = getattr(self, "_input_base_image", None)
        rect = getattr(self, "_input_caret_rect", None)
        if base is None or not bool(getattr(self, "_caret", False)) or rect is None:
            return base
        try:
            from PIL import ImageDraw

            frame = base.copy()
            ImageDraw.Draw(frame).rectangle(rect, fill=(*self._cyan, 255))
            return frame
        except Exception:
            return base



    def _render_supersample(self, *, morphing: bool, now: float) -> tuple[int, bool]:
        """Select render quality: full, except the cheaper 2x pass for reply text still arriving."""
        if morphing:
            return _SS, False
        recent_render = (
            now - float(getattr(self, "_last_render_at", 0.0) or 0.0)
        ) < 0.25
        # Only a reply's text that is still arriving earns the cheaper 2x pass. A hover, an
        # option toggle or a tab switch over the same text at 2x re-rasterizes every glyph, and
        # the 3x settle snaps them back: the card shimmered under a moving pointer (his msg 167).
        # Typing too: a 1x or 2x keystroke frame drew the composer's text wider and its edges
        # jagged until the settle (his report 2026-10-07); the composer's full render is ~15 ms.
        streaming = getattr(self, "_body", None) != getattr(self, "_last_render_body", None)
        fast = recent_render and streaming and self._mode != "input"
        return (2 if fast else _SS), fast

    def _repaint(self) -> bool:
        if getattr(self._cube, "_launcher_active", False):
            return self._visible
        self._sync_accessible_title()
        if not self._layered.available():
            self._last_error = self.failure_detail()
            return False
        if self._cube is None:
            self._last_error = "desktop cube unavailable"
            return False
        design = getattr(self, "_design", DEFAULT_BUBBLE_DESIGN)
        composer = self._mode == "input" and callable(getattr(self._cube, "launch_piece", None))
        dashboard_face = (getattr(self, "_face", "panel") == "dashboard"
                          and getattr(self, "_panel_state", None) == PanelState.DASHBOARD)
        if ((composer and getattr(self._cube, "_composer_controller", None) is not self)
                or (dashboard_face and getattr(self._cube, "_dashboard_controller", None) is not self)):
            fit = getattr(self._cube, "_fit_working_to_desktop", None)
            if callable(fit):
                fit(busy=True)      # MO Terminal's enlarged working cubes: normal size before docking
        try:
            cx, cy = self._cube.center()
            half = int(getattr(self._cube, "_size", 84) / 2)
            sw = int(screen_size()[0])
            sh = int(screen_size()[1])
        except Exception as exc:
            self._last_error = f"panel geometry failed ({type(exc).__name__})"
            return False
        # Preparing the crisp base card can take a meaningful part of the short reveal
        # budget, especially for the compact Dashboard.  A pending reveal
        # therefore renders first and starts its clock only after the base is ready; the
        # old order spent the animation while the GUI thread was still drawing off-screen.
        starting_transition = bool(getattr(self, "_transition_pending_start", False))
        if composer and getattr(self._cube, "_composer_controller", None) is not self:
            self._capture_cube_source(1)
            self._cube._composer_controller = self
        elif not composer and getattr(self._cube, "_composer_controller", None) is self:
            self._cube._composer_controller = None
        if dashboard_face and getattr(self._cube, "_dashboard_controller", None) is not self:
            self._capture_cube_source(0)
            self._cube._dashboard_controller = self
        elif not dashboard_face and getattr(self._cube, "_dashboard_controller", None) is self:
            self._cube._dashboard_controller = None
        progress = 0.0 if starting_transition else self._transition_progress()
        morphing = starting_transition or progress < 1.0
        now = time.perf_counter()
        # Direct input edits use 1x so one key never triggers a full 3x card rebuild.
        # Streaming uses 2x, while morphs and the scheduled settle remain crisp at 3x.
        self._ss, fast = self._render_supersample(morphing=morphing, now=now)
        self._font, self._bfont, self._sfont, self._ifont = self._fonts(self._ss)
        self._last_render_at = now
        self._last_render_body = getattr(self, "_body", None)

        card_w = self._card_width()
        side = "right" if cx + half + card_w + 2 * int(design.shadow_pad) <= sw else "left"
        self._dock_side = side
        if morphing:
            key = (self._mode, self._panel_state, self._body, card_w, getattr(self, "_role_label", ""))   # a role switch mid-reveal redraws
            if getattr(self, "_transition_base", None) is None or getattr(self, "_transition_base_key", None) != key:
                self._transition_base = self._render()   # rendered ONCE, crisp
                self._transition_base_key = key
            img = self._transition_base
        else:
            finish_base = bool(getattr(self, "_transition_finish_base", False))
            cached_base = getattr(self, "_transition_base", None)
            self._transition_finish_base = False
            self._transition_base = None
            self._transition_base_key = None
            if finish_base and cached_base is not None:
                # The expanding image is already the crisp 1x final card.  Reuse it for
                # the completion frame instead of rendering the same panel a second time.
                img = cached_base
            else:
                img = self._render()
                if fast:
                    self._schedule_settle()
        W, H = img.size
        overlap = int(design.dock_overlap)
        card_left = (cx + half - overlap) if side == "right" else (cx - half - card_w + overlap)
        card_left = int(max(0, min(card_left, sw - card_w)))
        img_x = card_left - int(design.shadow_pad)
        # Centre the panel on the cube. This used to anchor the cube to the panel's header so a
        # tall dashboard grew downward, but `min(H // 2, header_off)` centres only a card under
        # ~48px tall — every real reply pinned the cube 24px below the card top and read as
        # offset. Off-screen panels are still handled by the clamp below.
        img_y = int(max(-int(design.shadow_pad), min(cy - H // 2, sh - H + int(design.shadow_pad))))
        focus = getattr(self._cube, "_focus_controller", None)
        if dashboard_face:
            from interface.desktop_widgets import _monitor_work_area
            from mo_desktop.focus import cube_geometry
            self._cube_face_size = (card_w, H-2*int(design.shadow_pad))
            dx, dy, cw, ch = self.cube_extent()
            area = focus._area if focus is not None else _monitor_work_area(self._cube._win)
            _, center = cube_geometry((cx, cy), area or (0, 0, sw, sh), self._cube._size, (dx, dy), (cw, ch),
                                      self._cube.docked_faces(exclude="dashboard"))
            self._follow_group_center(center, focus)
            card_left, card_top = round(center[0]+dx), round(center[1]+dy)
            img_x, img_y = card_left-int(design.shadow_pad), card_top-int(design.shadow_pad)
        elif composer:
            from interface.desktop_widgets import _monitor_work_area
            from mo_desktop.focus import cube_geometry
            self._cube_face_size = (card_w, H-2*int(design.shadow_pad))
            if getattr(self, "_rest_face_height", None) is None:
                self._rest_face_height = self._cube_face_size[1]
            dx, dy, cw, ch = self.cube_extent()
            area = focus._area if focus is not None else _monitor_work_area(self._cube._win)
            _, center = cube_geometry((cx, cy), area or (0, 0, sw, sh), self._cube._size,
                                      (dx, dy), (cw, ch), self._cube.docked_faces(exclude="composer"))
            self._follow_group_center(center, focus)
            card_left, card_top = round(center[0]+dx), round(center[1]+dy)
            img_x, img_y = card_left-int(design.shadow_pad), card_top-int(design.shadow_pad)
        elif focus is not None:
            card_left, card_top = focus.panel_position(card_w, H-2*int(design.shadow_pad))
            img_x = card_left-int(design.shadow_pad)
            img_y = card_top-int(design.shadow_pad)
        self._bounds = (card_left, img_y + int(design.shadow_pad),
                        card_left + card_w, img_y + H - int(design.shadow_pad))
        if morphing and starting_transition and not composer and not dashboard_face:
            # A reply, status or file panel grows out of the cube on its docking side.
            self._capture_cube_source(1 if side == "right" else 0)
        if morphing and getattr(self, "_cube_source_image", None) is not None:
            source = self._cube_source_image
            source_x = self._cube_source_center[0]-source.width/2
            source_y = self._cube_source_center[1]-source.height/2
            size = (max(1, round(source.width+(W-source.width)*progress)),
                    max(1, round(source.height+(H-source.height)*progress)))
            img = card.grow_frame(source, img, size, progress)
            img_x = round(source_x+(img_x-source_x)*progress)
            img_y = round(source_y+(img_y-source_y)*progress)
            W, H = size
        if composer and not morphing:
            img, img_y, H = self._composer_height_glide(img, img_x, img_y, H)
            self._bounds = (card_left, img_y + int(design.shadow_pad),
                            card_left + card_w, img_y + H - int(design.shadow_pad))
        else:
            self._cancel_composer_glide()
            self._composer_shown = None          # an opening or another panel starts from its own size
        if composer or dashboard_face:   # the source pixels its cubes are launched from
            self._cube_published = (img, img_x, img_y)
            extent = tuple(self.cube_extent()) if not morphing else getattr(self, "_last_face_extent", None)
            if extent is not None and extent != getattr(self, "_last_face_extent", None):
                self._last_face_extent = extent
                relayout = getattr(self._cube, "relayout_docked_faces", None)
                if callable(relayout):
                    relayout(self)
        # Paint at the correct position WHILE still hidden, THEN show. Deiconifying first
        # flashed the window at its previous (stale) geometry for a frame before blit
        # repositioned it — the "loads at a stale spot then snaps into place" glitch.
        geometry = (img_x, img_y, W, H)
        reposition = geometry != getattr(self, "_last_layered_geometry", None)
        ok = self._layered.blit(
            img,
            img_x,
            img_y,
            premultiplied=True,
            position=reposition,
        )
        if not ok:
            getter = getattr(self._layered, "failure_detail", None)
            self._last_error = str(getter() if callable(getter) else "layered blit failed")[:160]
        else:
            self._last_layered_geometry = geometry
            self._layered.set_input_bounds(self._bounds)
            self._last_error = ""
        try:
            self._win.show()
        except Exception:
            pass
        self._publish_visibility(ok)
        self._place_blur_backdrop(bool(ok and not morphing and getattr(self, "_composer_glide", None) is None))
        if starting_transition:
            self._transition_pending_start = False
            if ok:
                self._transition_started_at = time.perf_counter()
                self._schedule_panel_transition()
            else:
                self._transition_started_at = 0.0
        self._last_input_blit_position = (img_x, img_y) if ok and self._mode == "input" else None
        return ok

    def _composer_height_glide(self, img: Any, img_x: int, img_y: int, height: int) -> tuple[Any, int, int]:
        """The composer grows with its text; a new or removed line glides over the panel transition
        time instead of jumping (his order 2026-10-08). Frames are cut from the finished card
        (card.height_frame), so nothing is scaled or rendered again."""
        shown = getattr(self, "_composer_shown", None)
        glide = getattr(self, "_composer_glide", None)
        split = int(getattr(self, "_input_text_top", 0) or 0)
        if glide is not None and (glide["x"], glide["y1"], glide["h1"]) == (img_x, img_y, height):
            glide["image"], glide["split"] = img, split          # typing on the same line mid-glide
            return self._composer_glide_frame()
        if shown is None or shown[0] != img_x or split <= 0 or (shown[1], shown[2]) == (img_y, height):
            self._cancel_composer_glide()
            self._composer_shown = (img_x, img_y, height)
            return img, img_y, height
        self._cancel_composer_glide()
        self._composer_glide = {"started": time.perf_counter(), "x": img_x, "y0": shown[1], "h0": shown[2],
                                "y1": img_y, "h1": height, "image": img, "split": split}
        try:
            self._composer_glide_after = self._gui.schedule(16, self._composer_glide_tick, frame=True)
        except Exception:
            self._composer_glide = None
            self._composer_shown = (img_x, img_y, height)
            return img, img_y, height
        return self._composer_glide_frame()

    def _composer_glide_frame(self) -> tuple[Any, int, int]:
        glide = self._composer_glide
        raw = min(1.0, (time.perf_counter() - glide["started"]) / max(0.001, self._transition_seconds()))
        amount = raw * raw * (3.0 - 2.0 * raw)          # the panel transition's own easing
        if raw >= 1.0:
            self._composer_glide = None
            image, y, height = glide["image"], glide["y1"], glide["h1"]
        else:
            y = round(glide["y0"] + (glide["y1"] - glide["y0"]) * amount)
            height = round(glide["h0"] + (glide["h1"] - glide["h0"]) * amount)
            image = card.height_frame(glide["image"], height, glide["split"])
        self._composer_shown = (glide["x"], y, height)
        return image, y, height

    def _composer_glide_tick(self) -> None:
        self._composer_glide_after = None
        glide = getattr(self, "_composer_glide", None)
        if glide is None or self._mode != "input" or not bool(getattr(self, "_visible", False)):
            self._cancel_composer_glide()
            return
        image, y, height = self._composer_glide_frame()
        x = glide["x"]
        pad = int(getattr(self, "_design", DEFAULT_BUBBLE_DESIGN).shadow_pad)
        if self._layered.blit(image, x, y, premultiplied=True, position=True):
            self._last_layered_geometry = (x, y, image.width, height)
            self._last_input_blit_position = (x, y)
            self._bounds = (x + pad, y + pad, x + image.width - pad, y + height - pad)
            self._layered.set_input_bounds(self._bounds)
            if getattr(self._cube, "_composer_controller", None) is self:
                self._cube_published = (image, x, y)
        if getattr(self, "_composer_glide", None) is not None:
            try:
                self._composer_glide_after = self._gui.schedule(16, self._composer_glide_tick, frame=True)
            except Exception:
                self._composer_glide = None
        else:
            self._place_blur_backdrop(True)

    def _cancel_composer_glide(self) -> None:
        after = getattr(self, "_composer_glide_after", None)
        self._composer_glide_after = None
        self._composer_glide = None
        if after is not None:
            try:
                self._gui.cancel(after)
            except Exception:
                pass

    def _blur_enabled(self) -> bool:
        """The approved blur-behind hybrid applies with the default 'hybrid' window effect."""
        import os

        effects = getattr(getattr(self, "_visuals", None), "effects", None)
        return os.name == "nt" and str(getattr(effects, "style", "")) == "hybrid" and int(getattr(effects, "intensity", 0) or 0) > 0

    def _place_blur_backdrop(self, settled: bool) -> None:
        """Keep Windows' blur under the settled card; hide it while the card moves or animates."""
        backdrop = getattr(self, "_blur_backdrop", None)
        if not self._blur_enabled() or not settled:
            if backdrop is not None:
                backdrop.hide()
            return
        if backdrop is None:
            from interface.desktop_widgets import PanelBlurBackdrop

            backdrop = self._blur_backdrop = PanelBlurBackdrop()
        hwnd = int(getattr(self._layered, "_native_hwnd", 0) or 0)
        backdrop.place(hwnd, self._bounds, int(self._visuals.metrics.panel_corner_radius))

    def _publish_visibility(self, visible: bool) -> None:
        """Set native panel visibility and notify its coordinator on real changes."""
        value = bool(visible)
        if not value and getattr(self, "_blur_backdrop", None) is not None:
            self._blur_backdrop.hide()
        changed = value != bool(getattr(self, "_visible", False))
        self._visible = value
        cube = getattr(self, "_cube", None)
        if not value and getattr(cube, "_composer_controller", None) is self:
            cube._composer_controller = None
        if not value and getattr(cube, "_dashboard_controller", None) is self:
            cube._dashboard_controller = None   # the two left cubes come back
        if changed and getattr(self, "_last_face_extent", None) is not None:
            self._last_face_extent = None
            relayout = getattr(cube, "relayout_docked_faces", None)
            if callable(relayout):
                relayout(self)
        callback = getattr(self, "_on_visibility_changed", None)
        if changed and callable(callback):
            try:
                callback(value)
            except Exception:
                pass

    def _composer_search_provider(self) -> str:
        """Return the one valid composer search mode; invalid transient state is chat."""
        provider = str(getattr(self, "_search_provider", "") or "").strip().lower()
        return provider if provider in {"google", "youtube", "translate"} else ""

    def _composer_search_label(self) -> str:
        return {"google": "Google", "youtube": "YouTube", "translate": "Google Translate"}.get(
            self._composer_search_provider(), ""
        )

    def _sync_accessible_title(self) -> None:
        """Give the custom-drawn surface a useful Automation window name."""
        state = getattr(self, "_panel_state", PanelState.REPLY)
        mode = str(getattr(self, "_mode", "reply") or "reply")
        if state == PanelState.STATUS:
            title = f"MO Desktop — {getattr(self, '_status_text', '') or 'Working'}"
        elif state == PanelState.DASHBOARD:
            title = "MO Desktop — Dashboard"
        elif state == PanelState.HISTORY:
            title = ("MO Desktop — Clipboard" if getattr(self, "_list_view", "") == "clipboard"
                     else "MO Desktop — Conversation history")
        elif mode == "input":
            provider_label = self._composer_search_label()
            title = (
                f"MO Desktop — Composer — Search {provider_label}"
                if provider_label else "MO Desktop — Composer — Send message"
            )
        elif getattr(self, "_options", None):
            title = "MO Desktop — Options — Select and submit or type a follow-up"
        elif state in {PanelState.FILE, PanelState.IMAGE, PanelState.MEDIA}:
            title = f"MO Desktop — {state.value.title()} preview"
        elif state == PanelState.FOOTERLESS:
            title = "MO Desktop — Guidance"
        else:
            title = "MO Desktop — Reply — Copy, history, or reply"
        focused = self._accessible_hit_label(getattr(self, "_keyboard_hit", ""))
        if focused:
            title += f" — Focus: {focused}"
        try:
            self._win.set_title(title)
        except Exception:
            pass

    def _repaint_cached_input_caret(self) -> bool:
        """Blit the cached input card with the current caret state only."""
        if getattr(self, "_composer_glide", None) is not None:
            return True             # a height glide owns these frames; the caret blinks again after it
        if self._mode != "input" or not self._visible or self._transition_progress() < 1.0:
            return False
        position = getattr(self, "_last_input_blit_position", None)
        frame = self._input_frame_with_caret()
        if position is None or frame is None:
            return False
        try:
            published = bool(
                self._layered.blit(
                    frame,
                    position[0],
                    position[1],
                    premultiplied=True,
                    position=False,
                )
            )
            if published and self in (getattr(self._cube, "_composer_controller", None),
                                      getattr(self._cube, "_dashboard_controller", None)):
                self._cube_published = (frame, position[0], position[1])
            return published
        except Exception:
            return False

    # ------------------------------------------------------------------ public
    def _hold(self, on: bool) -> None:
        # keep the cubes still while a bubble is docked to them, so they stay cohesive
        fn = getattr(self._cube, "set_hold", None)
        if callable(fn):
            try:
                fn(on)
            except Exception:
                pass

    def _card_width(self) -> int:
        design = getattr(self, "_design", DEFAULT_BUBBLE_DESIGN)
        if getattr(self, "_panel_state", None) == PanelState.DASHBOARD:
            from mo_desktop.dashboard_card import CONTENT_W as _DCW
            return _DCW + 2 * int(self._visuals.metrics.panel_padding)
        if getattr(self, "_panel_state", None) in (PanelState.HISTORY, PanelState.STATUS):
            content = max(
                int(design.content_width),
                int(getattr(design, "reply_content_width", 0) or 0),
            )
            return content + 2 * int(self._visuals.metrics.panel_padding)
        return self._content_width(None, getattr(self, "_body", ""), rich=self._mode != "input") \
            + 2 * int(self._visuals.metrics.panel_padding)

    def _prepare_panel_show(self, mode: str, state: PanelState, *, controls: bool = False) -> bool:
        """Set the shared panel state and report whether it needs a new reveal."""
        if mode != "input":
            self._end_browse(repaint=False)   # a reply, status or list replaces the composer browse
        transition = (
            not bool(getattr(self, "_visible", False))
            or getattr(self, "_mode", "reply") != mode
            or getattr(self, "_panel_state", PanelState.REPLY) != state
            or bool(getattr(self, "_cube_closing", False))
        )
        if mode == "input" and transition:
            self._rest_face_height = None      # a fresh composer measures its rest height again
        self._cube_closing = False
        self._mode = mode
        self._panel_state = state
        self._controls_enabled = bool(controls)
        self._footer_action_label = ""
        self._on_footer_action = None
        self._clear_options()
        return transition

    def _transition_seconds(self) -> float:
        panel = getattr(self, "_panel_design", DEFAULT_DESKTOP_PANEL_DESIGN)
        try:
            return max(0.0, float(getattr(panel, "transition_ms", 0) or 0) / 1000.0)
        except Exception:
            return 0.0

    def _transition_progress(self) -> float:
        if bool(getattr(self, "_transition_pending_start", False)):
            return 0.0
        started = float(getattr(self, "_transition_started_at", 0.0) or 0.0)
        seconds = self._transition_seconds()
        if started <= 0.0 or seconds <= 0.0:
            return 1.0
        raw = max(0.0, min(1.0, (time.perf_counter() - started) / seconds))
        amount = raw * raw * (3.0 - 2.0 * raw)
        return 1-amount if getattr(self, "_cube_closing", False) else amount

    def _panel_transition_active(self) -> bool:
        return bool(getattr(self, "_transition_pending_start", False)) or self._transition_progress() < 1.0

    def _begin_panel_transition(self) -> None:
        self._cancel_panel_transition()
        self._transition_callbacks = []
        self._transition_base = None            # content changed -> rebuild the morph base once
        self._transition_base_key = None
        self._transition_repaint_deferred = False
        self._transition_finish_base = False
        self._transition_started_at = 0.0
        if self._transition_seconds() <= 0.0:
            self._transition_pending_start = False
            return
        self._transition_pending_start = True

    def _repaint_for_panel_show(self, transition: bool, *, visual_changed: bool = True) -> bool:
        """Paint and activate one public panel state without interrupting its reveal.

        Same-state streaming/dashboard updates may arrive while the crisp
        base is expanding.  Their state is already current on ``self``; deferring the
        extra paint lets the normal final frame render the newest state once instead of
        blocking mid-animation or popping a stale cached base into view.
        """
        is_input = self._mode == "input"
        if not is_input:
            self._stop_blink()
        if transition:
            self._begin_panel_transition()
            painted = self._repaint()
        elif self._panel_transition_active():
            if visual_changed:
                self._transition_repaint_deferred = True
            painted = bool(getattr(self, "_visible", False))
        elif float(getattr(self, "_transition_started_at", 0.0) or 0.0) > 0.0:
            # The nominal duration elapsed just before its scheduled completion tick.
            # Complete it here once and cancel that now-redundant callback.
            self._cancel_panel_transition()
            self._transition_started_at = 0.0
            needs_render = visual_changed or bool(getattr(self, "_transition_repaint_deferred", False))
            self._transition_repaint_deferred = False
            if needs_render:
                self._transition_base = None
                self._transition_base_key = None
                self._last_render_at = 0.0
            else:
                self._transition_finish_base = True
            painted = self._repaint()
            if painted:
                self._flush_panel_transition_callbacks()
            else:
                self._transition_callbacks = []
        elif not visual_changed and bool(getattr(self, "_visible", False)):
            painted = True
        else:
            painted = self._repaint()
        if painted:
            if is_input:
                try:
                    self._win.activate()
                except Exception:
                    pass
            self._hold(True)
            self._arm_click_away()
            if is_input:
                self._start_blink()
        return painted

    def after_panel_transition(self, callback: Callable[[], None]) -> bool:
        """Run ``callback`` only after the current reveal has painted its final frame."""
        if not callable(callback):
            return False
        if (
            bool(getattr(self, "_transition_pending_start", False))
            or float(getattr(self, "_transition_started_at", 0.0) or 0.0) > 0.0
        ):
            callbacks = getattr(self, "_transition_callbacks", None)
            if not isinstance(callbacks, list):
                callbacks = []
                self._transition_callbacks = callbacks
            if callback not in callbacks:
                callbacks.append(callback)
            return True
        try:
            callback()
            return True
        except Exception:
            return False

    def _flush_panel_transition_callbacks(self) -> None:
        callbacks = list(getattr(self, "_transition_callbacks", None) or ())
        self._transition_callbacks = []
        for callback in callbacks:
            try:
                callback()
            except Exception:
                pass

    def _cancel_panel_transition(self) -> None:
        if getattr(self, "_transition_after", None) is not None:
            try:
                self._gui.cancel(self._transition_after)
            except Exception:
                pass
            self._transition_after = None

    def _schedule_panel_transition(self) -> None:
        if bool(getattr(self, "_transition_pending_start", False)):
            return
        if not bool(getattr(self, "_visible", False)) and self._transition_started_at <= 0.0:
            return
        progress = self._transition_progress()
        if progress >= 1.0:
            delay = 0
        else:
            # Keep frame starts on the reveal clock instead of sleeping a full
            # frame *after* each resize/blit.  The old drift produced only a few
            # unevenly spaced frames on a short transition.
            elapsed_ms = max(
                0.0,
                (time.perf_counter() - float(self._transition_started_at)) * 1000.0,
            )
            frame_ms = 16.0
            delay = max(1, int(round(frame_ms - (elapsed_ms % frame_ms))))
        self._cancel_panel_transition()
        try:
            # Never clear an elapsed transition here: the completion tick owns the
            # final cached-base/latest-content paint.  Skipping it can strand a panel
            # on its last almost-full frame when one render crosses the deadline.
            self._transition_after = self._gui.schedule(delay, self._panel_transition_tick, frame=delay > 0)
        except Exception:
            self._transition_after = None

    def _panel_transition_tick(self) -> None:
        self._transition_after = None
        if getattr(self, "_cube_closing", False):
            if time.perf_counter()-self._transition_started_at >= self._transition_seconds():
                self.hide()
            else:
                self._repaint()
                self._schedule_panel_transition()
            return
        if not bool(getattr(self, "_visible", False)):
            self._transition_started_at = 0.0
            self._transition_callbacks = []
            return
        if self._transition_progress() >= 1.0:
            self._transition_started_at = 0.0
            deferred = bool(getattr(self, "_transition_repaint_deferred", False))
            self._transition_repaint_deferred = False
            if deferred:
                self._transition_base = None
                self._transition_base_key = None
                self._last_render_at = 0.0
            else:
                self._transition_finish_base = True
            painted = self._repaint()
            if painted:
                self._flush_panel_transition_callbacks()
            else:
                self._transition_callbacks = []
            return
        self._repaint()
        self._schedule_panel_transition()

    def _capture_cube_source(self, index: int) -> None:
        """The real pixels of cube ``index``: every panel reveal grows out of them (one effect
        for composer, replies, status, history, files and the Dashboard)."""
        launch = getattr(self._cube, "launch_piece", None)
        if not callable(launch):
            self._cube_source_image = None
            return
        try:
            from PIL import Image
            self._cube_source_center, source = launch(index, time.perf_counter())
            self._cube_source_image = Image.frombytes("RGBA", source.size, source.convert("RGBa").tobytes())
        except Exception:
            self._cube_source_image = None

    def _schedule_settle(self) -> None:
        """Motion renders at a lower supersample; once it stops, repaint once at full
        quality so the resting card is crisp."""
        if getattr(self, "_settle_after", None) is not None:
            try:
                self._gui.cancel(self._settle_after)
            except Exception:
                pass
        try:
            self._settle_after = self._gui.schedule(240, self._settle_repaint)
        except Exception:
            self._settle_after = None

    def _settle_repaint(self) -> None:
        self._settle_after = None
        if not self._visible:
            return
        self._last_render_at = 0.0   # force the full-quality path, and never re-arm the settle
        self._repaint()

    def show(
        self,
        text: str,
        history: list[dict[str, Any]] | None = None,
        *,
        controls: bool = True,
        action_label: str = "",
        on_action: Callable[[], None] | None = None,
        on_session_history: Callable[[], None] | None = None,
        options: Any = None,
        on_options_submit: Callable[[list[str]], bool | None] | None = None,
        presentation: dict[str, Any] | None = None,
        fit: bool = False,
    ) -> bool:
        """Show MO's reply. ``history`` = MO's replies (oldest->newest, current last) so the
        card's ↑/↓ can browse back through what MO said. ``controls=False`` yields a FOOTERLESS
        card — a genuine walkthrough, or transient tool-streaming prose — not a walkthrough by
        itself. ``fit`` sizes the card to its text: a notice outside any turn ("No speech
        detected.") never sits alone in the turn's fixed-size card."""
        self._keyboard_hit = ""
        self._fit_reply = bool(fit)
        self._stash_input_draft()   # a reply arriving mid-compose must not drop the draft
        state = PanelState.FOOTERLESS if not controls else PanelState.REPLY
        transition = self._prepare_panel_show("reply", state, controls=controls)
        self._footer_action_label = str(action_label or "").strip()[:24]
        self._on_footer_action = on_action if self._footer_action_label and callable(on_action) else None
        self._on_session_history = on_session_history if callable(on_session_history) else None
        self._options = list(getattr(options, "options", []) or [])
        self._option_mode = str(getattr(options, "mode", "multi") or "multi")
        self._on_options_submit = on_options_submit if self._options and callable(on_options_submit) else None
        body = str(text or "")
        if body != self._body:
            self._scroll_line = 0
        self._body = body
        self._apply_reply_presentation(presentation or {})
        if self._controls_enabled and history is not None:
            self._reply_history = [dict(h) for h in history if h.get("content")][-100:]
        self._reply_idx = len(self._reply_history) - 1
        return self._repaint_for_panel_show(transition)

    def show_status(self, text: str) -> bool:
        """Show what MO heard or is doing as the compact line the reply grows from.

        Repeated steps update the same line in place; the next reply or composer
        is a state change, so it grows out of the cube through the usual reveal.
        """
        if getattr(self, "_mode", "reply") == "input" and bool(getattr(self, "_visible", False)):
            self._stash_input_draft()
        self._keyboard_hit = ""
        was_status = getattr(self, "_panel_state", None) == PanelState.STATUS
        previous = str(getattr(self, "_status_text", "") or "")
        transition = self._prepare_panel_show("status", PanelState.STATUS)
        self._status_text = " ".join(str(text or "").split())[:120]
        # This turn's earlier steps, shown dim under the current one; a new turn starts empty.
        trail = list(getattr(self, "_status_trail", []) or []) if was_status else []
        if was_status and previous and previous != self._status_text and (not trail or trail[-1] != previous):
            trail.append(previous)
        self._status_trail = trail[-3:]
        return self._repaint_for_panel_show(transition)

    def show_session_history(
        self,
        items: list[dict[str, Any]],
        *,
        on_select: Callable[[str], None],
        on_new: Callable[[], None],
        on_back: Callable[[], None],
    ) -> bool:
        """Show the dynamic Desktop conversation catalog without opening another window."""
        return self._show_list("conversations", items[:24], on_select=on_select, on_new=on_new, on_back=on_back)

    def show_clipboard(
        self,
        rows: list[dict[str, Any]],
        *,
        on_restore: Callable[[str], None],
        on_action: Callable[[str, str], None],
        on_clear: Callable[[], None],
        on_back: Callable[[], None],
    ) -> bool:
        """The clipboard history as the same list view: click restores, a row's buttons ask MO
        about it or remove it, the main button clears all."""
        return self._show_list("clipboard", rows, on_select=on_restore, on_new=on_clear, on_back=on_back,
                               on_action=on_action)

    def _show_list(self, view: str, items: list[dict[str, Any]], *, on_select: Any, on_new: Any,
                   on_back: Any, on_action: Any = None) -> bool:
        self._keyboard_hit = ""
        self._stash_input_draft()
        same_view = (getattr(self, "_panel_state", None) == PanelState.HISTORY
                     and getattr(self, "_list_view", "") == view)
        transition = self._prepare_panel_show("history", PanelState.HISTORY)
        self._body = ""
        self._list_view = view
        self._session_history_items = [dict(item) for item in list(items or [])]
        if not same_view:
            self._session_history_page = 0   # a refresh of the open list keeps its page
        self._on_session_select = on_select if callable(on_select) else None
        self._on_session_new = on_new if callable(on_new) else None
        self._on_session_back = on_back if callable(on_back) else None
        self._on_session_row_action = on_action if callable(on_action) else None
        self._attachment_preview_paths = []
        return self._repaint_for_panel_show(transition)

    def show_input(
        self,
        on_submit: Callable[[str], None],
        *,
        on_session_history: Callable[[], None] | None = None,
        on_web_search: Callable[[str, str], bool] | None = None,
        role_options: Callable[[], tuple[str, ...]] | None = None,
        on_role_select: Callable[[str], None] | None = None,
        role_label: str | None = None,
        history: list[dict[str, Any]] | None = None,
        on_attach: Callable[[], None] | None = None,
        generate_selection: dict | None = None,
        on_generate_action: Callable[[str], None] | None = None,
        role_hints: dict[str, str] | None = None,
    ) -> bool:
        already_composing = bool(getattr(self, "_visible", False)) and self._mode == "input"
        if role_hints is not None:
            self._role_hints = dict(role_hints)     # each role's own empty-composer hint (its SKILL.md role_hint)
        if history is not None:
            # The composer's Up/Down browse MO's replies from the saved conversation,
            # not only the replies this process has shown since it started.
            self._reply_history = [dict(h) for h in history if h.get("content")][-100:]
            self._reply_idx = len(self._reply_history) - 1
        self._keyboard_hit = ""
        transition = self._prepare_panel_show("input", PanelState.INPUT, controls=True)
        self._on_session_history = on_session_history if callable(on_session_history) else None
        self._on_web_search = on_web_search if callable(on_web_search) else None
        if on_attach is not None:
            self._on_attach = on_attach if callable(on_attach) else None
        if role_options is not None:
            self._role_options = role_options
        if on_role_select is not None:
            self._on_role_select = on_role_select
        if role_label is not None:
            self._role_label = role_label
        if generate_selection is not None and not getattr(self, "_generate_selection", None):
            self._generate_selection = generate_selection
        if on_generate_action is not None:
            self._on_generate_action = on_generate_action
        if self._on_web_search is None:
            self._search_provider = ""
        self._end_browse(repaint=False)
        # A repeated summon keeps the live edit. Only reopening a closed composer
        # or returning from another panel consumes its stashed draft.
        if not already_composing:
            self._menu = None
            self._privacy_open = self._results_open = False
            self._body = str(getattr(self, "_input_draft", "") or "")
            self._cursor = len(self._body)
            self._select_all = False
            self._scroll_line = 0
        self._input_draft = ""
        self._attachment_preview_paths = list(getattr(self, "_input_attachments", []))
        self._on_submit = on_submit
        self._caret = True
        return self._repaint_for_panel_show(transition)

    def clear_input_draft(self) -> None:
        """Drop a draft and composer routing only for a conversation switch/new action."""
        self._input_draft = ""
        self._input_attachments = []
        self._attachment_preview_paths = []
        self._generate_reference_roles = {}
        self._generate_selection = None
        self._generate_progress = ""
        self._search_provider = ""
        if self._mode == "input":
            self._body = ""
            self._cursor = 0
            self._select_all = False

    def show_dashboard(self, data: dict | None = None, actions: dict | None = None) -> bool:
        """Show the compact dashboard. ``actions`` maps a compact hit suffix
        (``terminal`` / ``action:<i>`` / ``comm:<i>``)
        to a callback.

        The selected compact/deep view is resident presentation state. It is
        preserved across background snapshot refreshes and never becomes a
        dashboard truth or polling owner.
        """
        keyboard_changed = bool(getattr(self, "_keyboard_hit", ""))
        previous_data = dict(getattr(self, "_dashboard_data", None) or {})
        self._keyboard_hit = ""
        transition = self._prepare_panel_show("dashboard", PanelState.DASHBOARD)
        self._body = ""
        payload = dict(data or {})
        requested_view = str(payload.get("view") or getattr(self, "_dashboard_view", "overview"))
        self._dashboard_view = requested_view if requested_view in {"overview", "work", "personal", "systems"} else "overview"
        payload["view"] = self._dashboard_view
        self._dashboard_data = payload
        self._dashboard_compact_actions = dict(actions or {})
        self._attachment_preview_paths = []
        return self._repaint_for_panel_show(
            transition,
            visual_changed=keyboard_changed or payload != previous_data,
        )

    def show_attachment(self, text: str, state: PanelState | str = PanelState.FILE,
                        preview_path: str | None = None,
                        preview_paths: list[str] | tuple[str, ...] | None = None,
                        allow_tools: bool = True) -> bool:
        """Show a pinned-capable attachment summary in the cube-attached panel."""
        self._keyboard_hit = ""
        panel_state = coerce_panel_state(state)
        if panel_state not in {PanelState.FILE, PanelState.IMAGE, PanelState.MEDIA}:
            panel_state = PanelState.FILE
        transition = self._prepare_panel_show("reply", panel_state)
        body = str(text or "").strip() or "Attached file."
        if body != self._body:
            self._scroll_line = 0
        self._body = body
        paths = [str(path) for path in (preview_paths or []) if str(path or "").strip()]
        if preview_path:
            paths.insert(0, str(preview_path))
        self._apply_reply_presentation({"attachments": paths[:8], "attachment_state": panel_state.value, "allow_tools": allow_tools})
        self._crop_clear()
        return self._repaint_for_panel_show(transition)

    def hide(self) -> None:
        self._end_browse(repaint=False)
        self._menu = None
        self._cube_closing = False
        self._cancel_panel_transition()
        if getattr(self, "_settle_after", None) is not None:
            try:
                self._gui.cancel(self._settle_after)
            except Exception:
                pass
            self._settle_after = None
        self._transition_base = None
        self._transition_base_key = None
        self._transition_pending_start = False
        self._transition_repaint_deferred = False
        self._transition_finish_base = False
        self._transition_callbacks = []
        self._transition_started_at = 0.0
        self._input_base_image = None
        self._input_caret_rect = None
        self._last_input_blit_position = None
        self._publish_visibility(False)
        self._copied = False
        self._keyboard_hit = ""
        self._stop_blink()
        self._mode = "reply"
        self._panel_state = PanelState.REPLY
        self._footer_action_label = ""
        self._on_footer_action = None
        self._clear_options()
        self._attachment_preview_paths = []
        self._attachment_tools_allowed = True
        self._end_browse(repaint=False)
        self._hold(False)
        try:
            self._win.hide()
        except Exception:
            pass

    def visible(self) -> bool:
        return self._visible

    def dock_side(self) -> str | None:
        """Actual side selected from the current rendered card width."""
        side = str(getattr(self, "_dock_side", "") or "")
        return side if self.visible() and side in {"left", "right"} else None

    # ------------------------------------------------------------------ input keys
    def _on_key(self, event: Any) -> str | None:
        if not self._visible:
            return
        ks = getattr(event, "keysym", "")
        key = str(ks).lower()
        if self._mode == "input" and getattr(self, "_privacy_open", False):
            self._privacy_open = False                    # any key returns from the privacy note
            self._repaint()
            return "break"
        if self._mode == "input" and getattr(self, "_results_open", False):
            self._results_open = False                    # the list closes; typing goes on into the draft
            if ks == "Escape":
                self._repaint()
                return "break"
        if self._mode == "input" and getattr(self, "_menu", None) is not None:
            if ks == "Escape":
                self._menu = None
                self._repaint()
            elif ks in {"Tab", "Down", "Up"}:
                self._move_keyboard_hit(-1 if ks == "Up" else 1)
            elif ks in {"Return", "KP_Enter", "space"}:
                self._activate_hit(getattr(self, "_keyboard_hit", "") or self._menu["owner"])
            return "break"
        if self._mode != "input":
            shift = bool(int(getattr(event, "state", 0) or 0) & 0x1)
            if ks == "Escape":
                self.hide()
            elif ks in {"Tab", "ISO_Left_Tab"}:
                self._move_keyboard_hit(-1 if shift or ks == "ISO_Left_Tab" else 1)
            elif ks == "Right":
                self._move_keyboard_hit(1)
            elif ks == "Left":
                self._move_keyboard_hit(-1)
            elif ks in {"Return", "KP_Enter", "space"}:
                self._activate_hit(getattr(self, "_keyboard_hit", ""))
            else:
                return None
            return "break"
        control = bool(int(getattr(event, "state", 0) or 0) & 0x4)
        shift = bool(int(getattr(event, "state", 0) or 0) & 0x1)
        if getattr(self, "_browse_idx", None) is not None and ks not in {"Up", "Down", "Tab", "ISO_Left_Tab"}:
            self._end_browse()           # any other key returns to the draft first
            if ks in {"Return", "KP_Enter", "Escape"}:
                return "break"
        if ks in {"Tab", "ISO_Left_Tab"}:
            self._move_keyboard_hit(-1 if shift or ks == "ISO_Left_Tab" else 1)
            return "break"
        if ks == "space" and getattr(self, "_keyboard_hit", ""):
            self._activate_hit(self._keyboard_hit)
            return "break"
        if ks in {"Return", "KP_Enter"}:
            self._submit_input()
            return "break"
        if ks == "Escape":
            self.hide()
            return "break"
        if ks in ("Up", "Down"):
            return "break"           # arrows are handled by the explicit <Up>/<Down> bindings
        if control:
            if key == "a":
                self._cursor = len(self._body)
                self._select_all = True
                self._finish_input_edit()
            elif key in {"c", "x"} and self._select_all:
                set_clipboard_text(self._body)
                if key == "x":
                    self._body = ""
                    self._cursor = 0
                    self._select_all = False
                    self._finish_input_edit()
            elif key == "v":
                try:
                    pasted = str(clipboard_text())
                except Exception:
                    return "break"
                pasted = " ".join(pasted.replace("\r", "\n").replace("\t", " ").splitlines())
                self._replace_input_selection(pasted)
                self._finish_input_edit()
            return "break"
        if ks in {"Left", "Right", "Home", "End"}:
            if self._select_all:
                self._cursor = 0 if ks in {"Left", "Home"} else len(self._body)
            elif ks == "Left":
                chip = self._reference_edge(self._cursor, -1)      # a chip is one step
                self._cursor = chip[0] if chip else max(0, self._cursor - 1)
            elif ks == "Right":
                chip = self._reference_edge(self._cursor, 1)
                self._cursor = chip[1] if chip else min(len(self._body), self._cursor + 1)
            elif ks == "Home":
                self._cursor = 0
            else:
                self._cursor = len(self._body)
            self._select_all = False
            self._finish_input_edit()
            return "break"
        if ks in {"BackSpace", "Delete"}:
            if self._select_all:
                self._body = ""
                self._cursor = 0
                self._select_all = False
            else:
                chip = self._reference_edge(self._cursor, -1 if ks == "BackSpace" else 1)
                if chip:                  # a chip leaves the sentence whole and takes its file with it
                    self._remove_reference(self._chips[self._body[chip[0]:chip[1]]]["index"])
                elif ks == "BackSpace" and self._cursor > 0:
                    self._body = self._body[:self._cursor - 1] + self._body[self._cursor:]
                    self._cursor -= 1
                elif ks == "Delete" and self._cursor < len(self._body):
                    self._body = self._body[:self._cursor] + self._body[self._cursor + 1:]
        else:
            ch = getattr(event, "char", "")
            if ch and ch.isprintable() and ch not in ("\r", "\n", "\t"):
                self._replace_input_selection(ch)
            else:
                return None
        self._finish_input_edit()
        return "break"

    def _replace_input_selection(self, text: str) -> None:
        if self._select_all:
            self._body = str(text)
            self._cursor = len(self._body)
            self._select_all = False
            return
        cursor = max(0, min(len(self._body), int(self._cursor)))
        self._body = self._body[:cursor] + str(text) + self._body[cursor:]
        self._cursor = cursor + len(str(text))

    def _finish_input_edit(self) -> None:
        self._forget_references_left_out()
        self._keyboard_hit = ""
        self._caret = True
        self._repaint()

    # ------------------------------------------------------------------ footer clicks
    def _on_click(self, event: Any) -> None:
        """Route a click on the footer controls. Window coords == final-image pixels."""
        if not self._visible:
            return
        x, y = getattr(event, "x", -1), getattr(event, "y", -1)
        if getattr(self, "_crop", None) is not None and self._crop.dragging:
            self._crop.end()            # a crop drag ends here; keep the selection, not a click
            self._repaint()
            return
        key = self._hit_key_at(x, y)
        if getattr(self, "_browse_idx", None) is not None and key != "sessions":
            # While browsing, the darkened panel is not clickable: a click there only brings the
            # composer back to normal. The lit message line opens that message in full; the
            # history button at the top stays live.
            band = getattr(self, "_browse_band", None)
            if band is not None and band[0] <= x <= band[2] and band[1] <= y <= band[3]:
                self._open_browsed_reply()
            else:
                self._end_browse()
            return
        self._activate_hit(key)

    def collapse_to_cube(self) -> None:
        """Close a docked face back into its cube: the composer into the upper-right cube
        (keeping the half-typed draft), the Dashboard into the upper-left one. Same reverse
        morph for both, so they open and close alike."""
        if getattr(self, "_cube_closing", False):
            return
        self._stash_input_draft()
        self._cancel_panel_transition()
        self._stop_blink()
        index = (1 if getattr(self._cube, "_composer_controller", None) is self
                 else 0 if getattr(self._cube, "_dashboard_controller", None) is self else None)
        if self._transition_seconds() <= 0 or index is None:
            self.hide()
            return
        now = time.perf_counter()
        duration = self._transition_seconds()
        started = self._transition_started_at
        opened_for = (0.0 if self._transition_pending_start else
                      min(duration, max(0.0, now-started)) if started > 0 else duration)
        if opened_for >= duration:
            bx, by = self._cube._bases[index]
            self._cube_source_center = (self._cube._x+bx-self._cube._size/2,
                                        self._cube._y+by-self._cube._size/2)
        self._cube_closing = True
        self._transition_pending_start = False
        # Smoothstep is symmetric: reverse the current phase and cached
        # pixels instead of jumping to a fresh, fully expanded close pose.
        self._transition_started_at = now - (duration-opened_for)
        self._repaint()
        self._schedule_panel_transition()

    def _activate_hit(self, key: str) -> None:
        """Activate one custom-drawn control from either mouse or keyboard."""
        key = str(key or "")
        if not key or key not in (getattr(self, "_hit", None) or {}):
            return
        self._keyboard_hit = key
        if key.startswith("menu:"):
            self._pick_menu(int(key.split(":", 1)[1]))
        elif key in {"generate:enhance", "generate:credits", "generate:jobs"}:   # act at once; no drop-down
            self._menu = None
            callback = getattr(self, "_on_generate_action", None)
            if callable(callback):
                callback(key[len("generate:"):], "")
        elif key in {"generate:privacy", "privacy_close"}:
            self._menu = None
            self._privacy_open = key == "generate:privacy" and not getattr(self, "_privacy_open", False)
            self._repaint()
        elif key == "preview_play":
            self._open_attachment_file()
        elif key.startswith(("result_play:", "result_save:", "result_continue:", "result_open:")):
            callback = getattr(self, "_on_generate_action", None)
            if callable(callback):
                verb, _sep, number = key.partition(":")
                callback("result", f"{verb[len('result_'):]}:{number}")
        elif key.startswith("generate:"):
            self._toggle_generate_menu(key)
        elif key.startswith("glance_remove:"):              # the preview's X removes that file with its chip
            self._remove_reference(int(key.rpartition(":")[2]))
            self._hover = self._keyboard_hit = ""
            self._repaint()
        elif key.startswith("chip:"):   # a chip opens Generate's References menu (role or remove), elsewhere Remove
            if str(getattr(self, "_role_label", "")).casefold() == "generate" and getattr(self, "_generate_selection", None):
                self._toggle_generate_menu("generate:refs")
            else:
                self._toggle_menu(key, (("remove", "Remove"),), "")
        elif key == "collapse":
            self.collapse_to_cube()
        elif key == "role":
            choices = tuple((role, role or "Default role") for role in ("", *self._role_options()))
            self._toggle_menu("role", choices, getattr(self, "_role_label", ""))
        elif key == "sessions":
            callback = getattr(self, "_on_session_history", None)
            if callable(callback):
                try:
                    callback()
                except Exception:
                    pass
        elif key == "search_cycle":
            order = ("", "google", "youtube", "translate")
            current = self._composer_search_provider()
            self._search_provider = order[(order.index(current) + 1) % len(order)]
            self._repaint()
        elif key == "dots_up":
            self._browse(-1)
        elif key == "dots_down":
            self._browse(+1)
        elif key == "attach":
            callback = getattr(self, "_on_attach", None)
            if callable(callback):
                try:
                    callback()
                except Exception:
                    pass
        elif key == "session:back":
            callback = getattr(self, "_on_session_back", None)
            if callable(callback):
                try:
                    callback()
                except Exception:
                    pass
        elif key == "session:new":
            callback = getattr(self, "_on_session_new", None)
            if callable(callback):
                try:
                    callback()
                except Exception:
                    pass
        elif key == "session:prev":
            self._session_history_page = max(
                0, int(getattr(self, "_session_history_page", 0) or 0) - 1,
            )
            self._repaint()
        elif key == "session:next":
            count = len(getattr(self, "_session_history_items", []) or [])
            last_page = max(0, (count - 1) // 4)
            self._session_history_page = min(
                last_page,
                int(getattr(self, "_session_history_page", 0) or 0) + 1,
            )
            self._repaint()
        elif key.startswith(("session:ask:", "session:remove:")):
            action, _, raw = key[len("session:"):].partition(":")
            try:
                name = str((getattr(self, "_session_history_items", None) or [])[int(raw)].get("name") or "")
            except (IndexError, TypeError, ValueError):
                name = ""
            callback = getattr(self, "_on_session_row_action", None)
            if name and callable(callback):
                try:
                    callback(action, name)
                except Exception:
                    pass
        elif key.startswith("session:"):
            try:
                item = (getattr(self, "_session_history_items", None) or [])[
                    int(key.partition(":")[2])
                ]
                name = str(item.get("name") or "").strip()
            except (IndexError, TypeError, ValueError):
                name = ""
            callback = getattr(self, "_on_session_select", None)
            if name and callable(callback):
                try:
                    callback(name)
                except Exception:
                    pass
        elif key.startswith("dash:view:"):
            selected = key[len("dash:view:"):]
            if selected in {"overview", "personal", "systems"}:
                self._dashboard_view = selected
                self._dashboard_data = dict(getattr(self, "_dashboard_data", None) or {})
                self._dashboard_data["view"] = selected
                self._repaint()
        elif key.startswith("dash:"):
            cb = getattr(self, "_dashboard_compact_actions", {}).get(key[len("dash:"):])
            if callable(cb):
                try:
                    cb()
                except Exception:
                    pass
        elif key == "panel_tools":
            self._panel_tools_open = True
            self._repaint()
        elif key == "panel_share":
            callback = getattr(self, "_on_panel_share", None)
            paths = list(getattr(self, "_attachment_preview_paths", []) or [])
            if callable(callback) and paths:
                callback(paths[0])
        elif key == "panel_send":
            callback = getattr(self, "_on_panel_send", None)
            paths = list(getattr(self, "_attachment_preview_paths", []) or [])
            if callable(callback) and paths:
                callback(paths)
        elif key == "panel_open_folder":
            self._open_attachment_folder()
        elif key == "panel_copy_path":
            path = self._first_attachment_file()
            if path is not None:
                self._copy_to_clipboard(str(path))
        elif key == "panel_tools_back":
            if getattr(self, "_panel_tool_active", ""):
                self._panel_tool_active = ""       # chips/crop -> grid
                self._crop_clear()
            else:
                self._panel_tools_open = False      # grid -> preview
            self._repaint()
        elif key == "panel_crop_apply":
            self._apply_crop()
        elif key.startswith("panel_chip:"):
            _, tool, arg = key.split(":", 2)
            self._fire_panel_tool(tool, arg)
        elif key.startswith("panel_tool:"):
            name = key[len("panel_tool:"):]
            if name == "crop" or name in _PANEL_TOOL_CHIPS:
                self._panel_tool_active = name      # tile -> its sub-mode (chips / crop)
                self._repaint()
            else:
                self._fire_panel_tool(name, None)
        elif key.startswith("option:"):
            self._toggle_option(int(key.partition(":")[2]))
        elif key == "options_submit":
            self._fire_options_submit()
        elif key == "copy":
            self._copy_body()
        elif key == "action":
            self._fire_footer_action()
        elif key == "send":
            self._submit_input()
        elif key == "reply":
            self._fire_reply(0)
        elif key == "up":
            self._nav(-1)
        elif key == "down":
            self._nav(+1)

    def _move_keyboard_hit(self, step: int) -> None:
        keys = list((getattr(self, "_hit", None) or {}).keys())
        if not keys:
            self._keyboard_hit = ""
            return
        current = str(getattr(self, "_keyboard_hit", "") or "")
        if current not in keys:
            index = 0 if step >= 0 else len(keys) - 1
        else:
            index = (keys.index(current) + (1 if step >= 0 else -1)) % len(keys)
        self._keyboard_hit = keys[index]
        self._repaint()

    def _nav(self, step: int) -> None:
        if self._mode == "input" and getattr(self, "_menu", None) is not None:
            open_menu = self._menu
            current = str(getattr(self, "_keyboard_hit", ""))
            index = int(current.split(":", 1)[1]) if current.startswith("menu:") else -1
            index = max(0, min(len(open_menu["choices"])-1, index+step))
            capacity = getattr(self, "_menu_capacity", 3)
            open_menu["scroll"] = max(0, min(index, max(int(open_menu.get("scroll", 0)), index-capacity+1)))
            self._keyboard_hit = f"menu:{index}"
            self._repaint()
        elif self._mode == "reply" and bool(getattr(self, "_controls_enabled", True)):
            self._recall_reply(step)
        elif self._mode == "input":
            self._browse(step)          # MO's earlier replies, in place in the composer

    def _stash_input_draft(self) -> None:
        """Preserve an in-progress composer draft so closing or replacing the input panel
        (click-away, or MO pushing a reply mid-compose) never discards what the operator
        was typing. Restored by ``show_input``; cleared only on an MO submission."""
        if self._mode == "input":
            self._input_draft = self._body if str(getattr(self, "_body", "") or "").strip() else ""
            self._input_attachments = list(getattr(self, "_attachment_preview_paths", []))

    def _submit_input(self) -> None:
        text = self._body.strip()
        if not text:
            return
        provider = self._composer_search_provider()
        if provider:
            callback = getattr(self, "_on_web_search", None)
            opened = False
            if callable(callback):
                try:
                    opened = bool(callback(provider, text))
                except Exception:
                    pass
            if opened:
                self._input_draft = ""
                self.hide()
            return
        cb = self._on_submit
        if not callable(cb):
            return
        generate = str(getattr(self, "_role_label", "")).casefold() == "generate"
        if generate:
            if cb(text) is False:
                return
            self._body = ""
            self._cursor = 0
            self._select_all = False
            self._attachment_preview_paths = []
            self._generate_reference_roles = {}
            self._repaint()
        else:
            attached = list(getattr(self, "_attachment_preview_paths", []) or [])
            self.hide()
            self._attachment_preview_paths = attached    # hide() clears them; the files in the sentence go with it
            cb(text)
            self._attachment_preview_paths = []
        self._input_draft = ""      # submitted to MO — the draft must not reappear
        self._input_attachments = []

    def _toggle_menu(self, owner: str, choices: tuple, selected: str) -> None:
        """Open the composer's one drop-down under ``owner``'s control, or close it."""
        current = getattr(self, "_menu", None)
        self._menu = None if (current and current.get("owner") == owner) or not choices else {
            "owner": owner, "choices": tuple(choices), "selected": str(selected), "scroll": 0}
        self._repaint()

    def _toggle_generate_menu(self, hit: str) -> None:
        from pathlib import Path
        from mo_desktop.generate_controls import menu, reference_tokens

        roles = getattr(self, "_generate_reference_roles", {}) or {}
        paths = [str(p) for p in getattr(self, "_attachment_preview_paths", [])]
        named = {path: name for name, path in reference_tokens(paths).items()}
        references = [(roles.get(path, "reference"), f"{named.get(path, '')} {Path(path).name}".strip()) for path in paths]
        choices, selected = menu(self._generate_selection, hit[len("generate:"):], references=references)
        self._toggle_menu(hit, tuple(choices), selected)

    def _pick_menu(self, index: int) -> None:
        """Apply one drop-down pick to the control that opened it, then close the menu."""
        open_menu = getattr(self, "_menu", None) or {}
        choices = open_menu.get("choices", ())
        if not 0 <= index < len(choices):
            return
        owner, value = str(open_menu.get("owner")), choices[index][0]
        self._menu = None
        if owner == "role":
            self._on_role_select(value)
            self._role_label = value
        elif owner.startswith("generate:"):
            callback = getattr(self, "_on_generate_action", None)
            if callable(callback):
                callback(owner[len("generate:"):], value)
        elif owner.startswith("chip:") and value == "remove":
            self._remove_reference(int(owner[len("chip:"):]))
        self._repaint()

    def _on_wheel(self, event: Any) -> str:
        open_menu = getattr(self, "_menu", None)
        if open_menu is not None:
            direction = -1 if getattr(event, "delta", 0) > 0 else 1
            limit = max(0, len(open_menu["choices"])-getattr(self, "_menu_capacity", 3))
            open_menu["scroll"] = max(0, min(limit, int(open_menu.get("scroll", 0))+direction))
            self._repaint()
            return "break"
        if getattr(self, "_results_open", False):         # the Saved results list scrolls a row at a time
            limit = max(0, len(getattr(self, "_results", None) or []) - _RESULT_ROWS)
            self._results_scroll = max(0, min(limit, int(getattr(self, "_results_scroll", 0))
                                              + (-1 if getattr(event, "delta", 0) > 0 else 1)))
            self._repaint()
            return "break"
        delta = int(getattr(event, "delta", 0) or 0)
        step = -3 if delta > 0 else 3
        region = getattr(self, "_options_region", None)
        x, y = int(getattr(event, "x", -1)), int(getattr(event, "y", -1))
        if region and region[0] <= x <= region[2] and region[1] <= y <= region[3]:
            current = int(getattr(self, "_options_scroll", 0))
            maximum = int(getattr(self, "_max_options_scroll", 0))
            self._options_scroll = max(0, min(maximum, current + step * int(self._design.line_height)))
            if self._options_scroll != current:
                self._repaint()
            return "break"
        self._scroll_body(step)
        return "break"

    def _scroll_body(self, step: int) -> str:
        if not self._visible:
            return "break"
        current = int(getattr(self, "_scroll_line", 0) or 0)
        maximum = int(getattr(self, "_max_scroll_line", 0) or 0)
        if maximum <= 0:
            return "break"
        nxt = max(0, min(maximum, current + int(step or 0)))
        if nxt != current:
            self._scroll_line = nxt
            self._repaint()
        return "break"

    def _recall_reply(self, step: int) -> None:
        """↑ (step -1) / ↓ (step +1) through MO's previous replies, shown in the card. When
        the browse was opened from the composer, ↓ past the newest returns to the draft."""
        if len(self._reply_history) < 2:
            return
        self._reply_idx = max(0, min(len(self._reply_history) - 1, self._reply_idx + step))
        self._restore_history_reply()
        self._repaint()

    def _browse(self, step: int) -> None:
        """Up (step -1) / Down (+1) in the composer: MO's earlier replies shown IN the composer,
        everything around it dimmed; Down past the newest returns to the draft."""
        history = list(getattr(self, "_reply_history", None) or [])
        if self._mode != "input" or not history:
            return
        current = getattr(self, "_browse_idx", None)
        if current is None:
            if step > 0:
                return
            index = len(history) - 1
        else:
            index = current + step
            if index >= len(history):
                self._end_browse()
                return
            index = max(0, index)
            if index == current:
                return
        self._browse_idx = index
        self._wrapped_body = None
        self._browse_fade(time.perf_counter())

    def _end_browse(self, *, repaint: bool = True) -> None:
        """Back to the draft (typing, Enter, Esc, Down past the newest, the panel closing)."""
        if getattr(self, "_browse_idx", None) is None:
            return
        self._browse_idx = None
        self._wrapped_body = None
        if repaint:
            self._browse_fade(time.perf_counter())

    def _browse_fade(self, started: float) -> None:
        """Cross-fade the composer from the message it showed to the next (about 150 ms)."""
        previous = getattr(self, "_input_base_image", None)
        self._browse_from = previous
        self._browse_started = started
        self._repaint()

    def _dim_around_browse_line(self, img: Any, box: Any, radius: int, line_top: int, line_h: int, ss: int) -> None:
        """Dim this panel, not the screen: everything but a band around the browsed message's line."""
        from PIL import Image as _Image, ImageDraw as _ImageDraw

        veil = _Image.new("RGBA", img.size, (0, 0, 0, 0))
        vd = _ImageDraw.Draw(veil)
        vd.rounded_rectangle(tuple(box), radius=radius, fill=(0, 0, 0, _BROWSE_DIM_ALPHA))
        band = (box[0] + ss, line_top - 4 * ss, box[2] - ss, line_top + line_h + 4 * ss)
        vd.rectangle(band, fill=(0, 0, 0, 0))
        img.alpha_composite(veil)
        self._browse_band = tuple(int(v // ss) for v in band)      # 1x: a click there opens it

    def _open_browsed_reply(self) -> None:
        """Open the browsed message in full: the card becomes the reply view at that message (it
        sizes like any reply) with its usual controls; the draft waits for the composer."""
        index = getattr(self, "_browse_idx", None)
        history = list(getattr(self, "_reply_history", None) or [])
        if index is None or not 0 <= index < len(history):
            self._end_browse()
            return
        self._stash_input_draft()
        self._end_browse(repaint=False)
        transition = self._prepare_panel_show("reply", PanelState.REPLY, controls=True)
        self._reply_idx = index
        self._restore_history_reply()
        self._repaint_for_panel_show(transition)

    def _draw_search_brand(self, img: Any, d: Any, provider: str, ax: int, ay: int, ss: int) -> None:
        """The composer cube's search modes, in each service's own colours."""
        if provider == "google":
            glyph_w = int(d.textlength("G", font=self._bfont))
            d.text((ax + (10 * ss - glyph_w) // 2, ay - 8 * ss), "G", font=self._bfont, fill=(66, 133, 244, 255))
        elif provider == "youtube":
            d.rounded_rectangle((ax - 2 * ss, ay - 2 * ss, ax + 12 * ss, ay + 7 * ss), radius=round(2.5 * ss),
                                fill=(255, 0, 0, 255))
            d.polygon([(ax + 3.5 * ss, ay - 0.2 * ss), (ax + 3.5 * ss, ay + 5.2 * ss), (ax + 8 * ss, ay + 2.5 * ss)],
                      fill=(255, 255, 255, 255))
        else:
            from interface.desktop_brand import make_glyph_icon

            d.rounded_rectangle((ax - 1 * ss, ay - 4 * ss, ax + 11 * ss, ay + 8 * ss), radius=round(2.5 * ss),
                                fill=(66, 133, 244, 255))
            img.alpha_composite(make_glyph_icon("translate", 10 * ss, color="#ffffff"), (int(ax), int(ay - 3 * ss)))

    def _apply_reply_presentation(self, presentation: dict[str, Any]) -> None:
        self._attachment_preview_paths = list(presentation.get("attachments") or [])
        self._attachment_tools_allowed = bool(presentation.get("allow_tools", True))
        self._panel_tools_open = False
        self._panel_tool_active = ""
        if self._attachment_preview_paths:
            state = coerce_panel_state(presentation.get("attachment_state", "file"))
            if state in {PanelState.FILE, PanelState.IMAGE, PanelState.MEDIA}:
                self._panel_state = state
            if (state in {PanelState.FILE, PanelState.MEDIA} and self._attachment_tools_allowed
                    and not self._on_footer_action and callable(getattr(self, "_on_panel_tool", None))):
                self._footer_action_label = "Send file"
                paths = tuple(self._attachment_preview_paths)
                self._on_footer_action = lambda: self._on_panel_tool("send", "", paths)

    def _restore_history_reply(self) -> None:
        """Restore controls from the displayed message, never the newest reply."""
        message = self._reply_history[self._reply_idx]
        self._body, options = parse_options(str(message.get("content") or ""))
        self._clear_options()
        self._options = list(getattr(options, "options", []) or [])
        self._option_mode = str(getattr(options, "mode", "multi") or "multi")
        self._on_options_submit = getattr(self, "_on_history_options_submit", None) if options else None
        from mo_desktop.issue_report import ISSUE_REPORT_BUTTON_LABEL, looks_like_issue_admission

        report = looks_like_issue_admission(self._body)
        self._footer_action_label = ISSUE_REPORT_BUTTON_LABEL if report else ""
        self._on_footer_action = getattr(self, "_on_report", None) if report else None
        self._panel_state = PanelState.REPLY
        self._apply_reply_presentation(message.get("_mo_presentation", {}))
        self._scroll_line = 0

    def _fire_reply(self, step: int) -> None:
        cb = self._on_reply
        if callable(cb):
            try:
                cb(step)
            except Exception:
                pass

    def _fire_footer_action(self) -> None:
        cb = getattr(self, "_on_footer_action", None)
        if callable(cb):
            try:
                cb()
            except Exception:
                pass

    def _fire_panel_tool(self, tool: str, arg: str | None = None) -> None:
        """A quick-tool tile or chip was tapped. The owner (companion) performs the edit
        via core/imageedit against ``source`` (the current preview image) and refreshes
        the panel. Unset ``_on_panel_tool`` -> inert (crop/flip until their phases)."""
        cb = getattr(self, "_on_panel_tool", None)
        if not callable(cb):
            return
        paths = getattr(self, "_attachment_preview_paths", None) or []
        source = str(paths[0]) if paths else None
        try:
            cb(tool, arg, source)
        except Exception:
            pass

    def _crop_clear(self) -> None:
        """Reset the crop selection if one exists (guarded so partial instances are safe)."""
        c = getattr(self, "_crop", None)
        if c is not None:
            c.clear()

    def _apply_crop(self) -> None:
        """Map the crop selection to source pixels and commit through the seam (companion ->
        core/imageedit.crop). No selection / bad geometry -> no-op."""
        db = getattr(self, "_crop_disp_box", None)
        paths = getattr(self, "_attachment_preview_paths", None) or []
        if db is None or not paths:
            return
        try:
            from PIL import Image
            with Image.open(str(paths[0])) as im:
                src_size = im.size
        except Exception:
            return
        box = self._crop.box_source(db, src_size)
        if box is None:
            return
        x, y, w, h = box
        self._fire_panel_tool("crop", f"{x},{y},{w},{h}")

    def _clear_options(self) -> None:
        self._options = []
        self._options_scroll = 0
        self._option_mode = "multi"
        self._selected_options = set()
        self._options_submitted = False
        self._on_options_submit = None

    def _toggle_option(self, index: int) -> None:
        if bool(getattr(self, "_options_submitted", False)):
            return
        if not 0 <= index < len(getattr(self, "_options", []) or []):
            return
        if str(getattr(self, "_option_mode", "multi")) == "single":
            self._selected_options = {index}
        elif index in self._selected_options:
            self._selected_options.remove(index)
        else:
            self._selected_options.add(index)
        self._repaint()

    def _fire_options_submit(self) -> None:
        cb = getattr(self, "_on_options_submit", None)
        options = list(getattr(self, "_options", []) or [])
        selected = [str(getattr(options[i], "label", "") or "") for i in sorted(self._selected_options)]
        if not selected or not callable(cb):
            return
        self._options_submitted = True
        self._on_options_submit = None
        self._repaint()
        accepted = cb(selected)
        if accepted is False:
            self._options_submitted = False
            self._on_options_submit = cb
            self._repaint()

    def _start_blink(self) -> None:
        self._stop_blink()
        try:
            self._blink_after = self._gui.schedule(530, self._blink)
        except Exception:
            self._blink_after = None

    def _stop_blink(self) -> None:
        if self._blink_after is not None:
            try:
                self._gui.cancel(self._blink_after)
            except Exception:
                pass
            self._blink_after = None

    def _blink(self) -> None:
        self._blink_after = None
        if self._mode != "input" or not self._visible:
            return
        self._caret = not self._caret
        if not self._repaint_cached_input_caret():
            self._repaint()
        self._start_blink()

    # ------------------------------------------------------------------ input yield
    def set_input_yield(self, on: bool) -> bool:
        """Stay visible while MO acts: let its clicks pass through and keep out of captures.

        Click-away is paused meanwhile, so MO's own clicks never dismiss the panel.
        Returns False when Windows cannot apply both; the caller then hides instead.
        """
        win = getattr(self, "_win", None)
        if on:
            if not bool(getattr(self, "_input_yielded", False)):
                self._dismissible_before_yield = bool(getattr(self, "_dismissible", True))
            try:
                applied = bool(win is not None and win.set_click_through(True) and win.exclude_from_capture(True))
            except Exception:
                applied = False
            if not applied:
                self._release_input_yield(win)
                return False
            self._input_yielded = True
            self._dismissible = False
            if self._watch_after is not None:
                try:
                    self._gui.cancel(self._watch_after)
                except Exception:
                    pass
                self._watch_after = None
            return True
        if bool(getattr(self, "_input_yielded", False)):
            self._release_input_yield(win)
            self._input_yielded = False
            self._dismissible = bool(getattr(self, "_dismissible_before_yield", True))
            if bool(getattr(self, "_visible", False)):
                self._arm_click_away()
        return True

    @staticmethod
    def _release_input_yield(win: Any) -> None:
        try:
            if win is not None:
                win.set_click_through(False)
                win.exclude_from_capture(False)
        except Exception:
            pass

    # ------------------------------------------------------------------ click-away
    def _arm_click_away(self) -> None:
        import sys
        if sys.platform != "win32":
            return
        if self._watch_after is not None:
            try:
                self._gui.cancel(self._watch_after)
            except Exception:
                pass
        if not bool(getattr(self, "_dismissible", True)):
            self._watch_after = None
            return
        try:
            self._watch_after = self._gui.schedule(220, self._watch_click_away)
        except Exception:
            self._watch_after = None

    def _hovering(self, key: str) -> bool:
        return key in {
            getattr(self, "_hover", ""),
            getattr(self, "_keyboard_hit", ""),
        }

    def _draw_keyboard_focus(self, image: Any, hits: dict[str, Any]) -> Any:
        """Outline focus on pure-renderer cards without changing their palette."""
        rect = hits.get(getattr(self, "_keyboard_hit", "")) if hits else None
        if not rect:
            return image
        try:
            from PIL import ImageDraw

            x0, y0, x1, y1 = (int(value) for value in rect)
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle(
                [max(0, x0 - 2), max(0, y0 - 2), min(image.width - 1, x1 + 2), min(image.height - 1, y1 + 2)],
                radius=int(self._visuals.metrics.button_corner_radius),
                outline=(*self._cyan, 255),
                width=2,
            )
        except Exception:
            return image
        return image


    def _hit_key_at(self, x: int, y: int) -> str:
        for name, r in (getattr(self, "_hit", None) or {}).items():
            if r and r[0] <= x <= r[2] and r[1] <= y <= r[3]:
                return name
        return ""

    def _on_motion(self, event: Any) -> None:
        """Point at a control and it lights up under a hand cursor. Repaint only on a change."""
        if not getattr(self, "_visible", False):
            return
        key = self._hit_key_at(getattr(event, "x", -1), getattr(event, "y", -1))
        if key == getattr(self, "_hover", ""):
            return
        self._hover = key
        try:
            self._win.set_cursor("hand" if key and not key.startswith("glance:") else "arrow")   # a preview only shows
        except Exception:
            pass
        self._repaint()

    def _on_leave(self, _event: Any = None) -> None:
        if not getattr(self, "_hover", ""):
            return
        self._hover = ""
        try:
            self._win.set_cursor("arrow")
        except Exception:
            pass
        if getattr(self, "_visible", False):
            self._repaint()

    def _on_press(self, event: Any) -> None:
        """Begin a crop selection when pressing inside the shown image (crop mode only)."""
        if not getattr(self, "_visible", False) or getattr(self, "_panel_tool_active", "") != "crop":
            return
        db = getattr(self, "_crop_disp_box", None)
        if db is None:
            return
        ex, ey = getattr(event, "x", -1), getattr(event, "y", -1)
        if db[0] <= ex <= db[2] and db[1] <= ey <= db[3]:
            self._crop.begin(ex, ey)
            self._repaint()

    def _on_drag(self, event: Any) -> None:
        """Extend the crop rectangle while dragging (crop mode only)."""
        if getattr(self, "_panel_tool_active", "") != "crop" or not self._crop.dragging:
            return
        self._crop.drag(getattr(event, "x", -1), getattr(event, "y", -1))
        self._repaint()

    def _first_attachment_file(self) -> Any | None:
        from pathlib import Path

        paths = getattr(self, "_attachment_preview_paths", None) or []
        if not paths:
            return None
        try:
            path = Path(paths[0]).resolve(strict=True)
            return path if path.is_file() else None
        except (OSError, RuntimeError, TypeError, ValueError):
            return None

    def _open_attachment_folder(self) -> None:
        import os

        path = self._first_attachment_file()
        if path is not None:
            try:
                os.startfile(str(path.parent))  # type: ignore[attr-defined]
            except (OSError, AttributeError):
                pass

    def cube_extent(self) -> tuple[float, float, int, int]:
        """The composer replaces the upper-right cube and grows upward; the Dashboard face
        replaces the two left cubes: its right edge on theirs, its top on the right column's
        top (the docked composer, else the upper-right cube)."""
        width, height = getattr(self, "_cube_face_size", (0, 0))
        half, edge = self._cube._size/2, self._cube._cube_edge
        if getattr(self, "_face", "panel") == "dashboard":
            bx0, _by0 = self._cube._bases[0]
            _bx1, by1 = self._cube._bases[1]
            top = by1-half-edge/2
            composer = getattr(self._cube, "_composer_controller", None)
            if composer is not None:
                top = min(top, composer.rest_top())
            return bx0-half+edge/2-width, top, width, height
        bx, by = self._cube._bases[1]
        return bx-half-edge/2, by-half+edge/2-height, width, height

    def rest_top(self) -> float:
        """The composer's top as it opened. A Dashboard beside it lines up here, so the composer
        growing as he types never moves or stretches it (only the composer changes size, msg 165)."""
        _width, height = getattr(self, "_cube_face_size", (0, 0))
        rest = getattr(self, "_rest_face_height", None) or height
        _bx, by = self._cube._bases[1]
        return by-self._cube._size/2+self._cube._cube_edge/2-rest

    def _follow_group_center(self, center: tuple[int, int], focus: Any) -> None:
        """Move the cubes to the clamped group centre and re-place the other docked faces."""
        moved = (self._cube._x, self._cube._y) != tuple(center)
        self._cube._x, self._cube._y = center
        self._cube._from = self._cube._to = center
        if focus is not None and focus._cube_center != center:
            focus._cube_center = center
            focus._fit_visible_rows()
            focus._place_face()
        if moved:
            for name in ("_composer_controller", "_dashboard_controller"):
                other = getattr(self._cube, name, None)
                if other is not None and other is not self and getattr(other, "_visible", False):
                    other._repaint()

    def launch_piece(self, *, upper: bool = True) -> tuple[tuple[float, float], Any]:
        """The published card as source pixels; the Dashboard face gives its upper or lower
        half, one for each of the two cubes it replaced."""
        from PIL import Image
        image, x, y = self._cube_published
        straight = Image.frombytes("RGBa", image.size, image.tobytes()).convert("RGBA")
        if getattr(self, "_face", "panel") == "dashboard":
            half = straight.height // 2
            box = (0, 0, straight.width, half) if upper else (0, half, straight.width, straight.height)
            piece = straight.crop(box)
            return (x+piece.width/2, y+box[1]+piece.height/2), piece
        return (x+image.width/2, y+image.height/2), straight

    def set_launcher_active(self, active: bool) -> None:
        """Yield the current card while the same four-piece launcher is open."""
        if active:
            self._cancel_panel_transition()
            self._transition_pending_start = False
            self._transition_started_at = 0.0
            self._cube_closing = False
            self._stop_blink()
            self._win.hide()
        else:
            self._repaint()
            self._start_blink()

    def _copy_to_clipboard(self, text: str) -> bool:
        if not text:
            return False
        try:
            set_clipboard_text(text)
        except Exception:
            return False
        return True

    def _copy_body(self) -> None:
        """Copy MO's message to the clipboard, and flash the control so the click is felt."""
        text = str(getattr(self, "_body", "") or "").strip()
        if not self._copy_to_clipboard(text):
            return
        self._copied = True
        self._repaint()
        try:
            self._gui.schedule(900, self._clear_copied)
        except Exception:
            pass

    def _clear_copied(self) -> None:
        if getattr(self, "_copied", False):
            self._copied = False
            if self._visible:
                self._repaint()

    def set_dismissible(self, on: bool) -> None:
        """A walkthrough is a sequence. Clicking the thing MO just pointed at must not tear the
        panel down mid-explanation — that reads as the walkthrough being interrupted."""
        self._dismissible = bool(on)
        if self._dismissible and self._visible:
            self._arm_click_away()
        elif getattr(self, "_watch_after", None) is not None:
            try:
                self._gui.cancel(self._watch_after)
            except Exception:
                pass
            self._watch_after = None

    def _watch_click_away(self) -> None:
        self._watch_after = None
        if not self._visible:
            return
        if not getattr(self, "_dismissible", True):
            return
        if getattr(self._cube, "_launcher_active", False):
            self._arm_click_away()
            return
        try:
            if self._click_is_away():
                self._stash_input_draft()   # click-away must not delete a half-typed message
                self.hide()
                return
        except Exception:
            pass
        try:
            # Every 30 ms: a normal click (60-120 ms, press to release) can no longer fall between two
            # reads; at 90 ms a quick click often did and the panel needed a second one (his report).
            self._watch_after = self._gui.schedule(_CLICK_AWAY_POLL_MS, self._watch_click_away)
        except Exception:
            self._watch_after = None

    def _click_is_away(self) -> bool:
        """The left button is down outside this panel, the faces docked with it and the cubes."""
        from core.desktop.win32 import VK_LBUTTON, mouse_button_held
        if not mouse_button_held((VK_LBUTTON,)):
            return False
        px, py = pointer_position()
        x0, y0, x1, y1 = self._bounds
        if (x0 <= px <= x1 and y0 <= py <= y1) or self._inside_docked_group(px, py):
            return False
        if self._cube is not None:
            try:
                cx, cy = self._cube.center()
                r = getattr(self._cube, "_size", 84)
                if abs(px - cx) <= r and abs(py - cy) <= r:
                    return False
            except Exception:
                pass
        return True

    def _on_blur(self) -> None:
        """Focus went to another window. When a click elsewhere took it (the button is still down
        as the message arrives), close at once rather than waiting for the next read; a keyboard
        switch (Alt+Tab) holds no button and leaves the panel open, as before."""
        if (not self._visible or not getattr(self, "_dismissible", True) or self._watch_after is None
                or getattr(self._cube, "_launcher_active", False)):
            return
        try:
            if self._click_is_away():
                self._stash_input_draft()
                self.hide()
        except Exception:
            pass

    def _inside_docked_group(self, px: int, py: int) -> bool:
        """A click on another docked face (composer, Dashboard, Focus) is not a click away:
        the faces docked into the cubes are one block and stay open together."""
        cube = getattr(self, "_cube", None)
        for name in ("_composer_controller", "_dashboard_controller"):
            other = getattr(cube, name, None)
            if other is not None and other is not self and getattr(other, "_visible", False):
                bx0, by0, bx1, by1 = getattr(other, "_bounds", (0, 0, -1, -1))
                if bx0 <= px <= bx1 and by0 <= py <= by1:
                    return True
        focus = getattr(cube, "_focus_controller", None)
        if focus is not None:
            try:
                gx, gy, gw, gh = focus.group_bounds()
            except Exception:
                return False
            return gx <= px <= gx + gw and gy <= py <= gy + gh
        return False

    def destroy(self) -> None:
        if getattr(self, "_blur_backdrop", None) is not None:
            self._blur_backdrop.destroy()
            self._blur_backdrop = None
        self._publish_visibility(False)
        self._cancel_panel_transition()
        self._transition_callbacks = []
        self._stop_blink()
        for name in ("_watch_after", "_settle_after"):
            timer = getattr(self, name, None)
            if timer is not None:
                self._gui.cancel(timer)
                setattr(self, name, None)
        try:
            self._layered.destroy()
        except Exception:
            pass
