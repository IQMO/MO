"""The lower-right resident cube expands into a freely movable window list."""
from __future__ import annotations

from datetime import datetime
import logging
import time
from typing import Any

from interface.desktop_ui import DesktopVisualState
from mo_desktop.cube_motion import _ease_out, _time_scaled_ease
from mo_desktop.design import DEFAULT_DESKTOP_PANEL_DESIGN
from mo_desktop.focus_native import (
    TaskbarVisibility, WindowPreview, fit_preview, is_minimized, open_system_tray, window_icon,
)
from mo_desktop.phone.trackpad import activate_switchable_window, switchable_windows

_LOG = logging.getLogger(__name__)
WINDOW_LIMIT = 256
IDLE_OPACITY = .42
FADE_EASE = .42
SCAN_SECONDS = 1.0
FACE_SIZE = 208
SCREEN_GAP = 8


def cube_geometry(center: tuple[float, float], area: tuple[int, int, int, int],
                  cube_size: int, offset: tuple[float, float], size: tuple[int, int],
                  other_faces: Any = ()
                  ) -> tuple[tuple[int, int, int, int], tuple[int, int]]:
    """Clamp the whole cube group, this face and every other docked face, without snapping to an
    edge. ``other_faces`` are ``(dx, dy, w, h)`` from ``DesktopCube.docked_faces``."""
    left, top, right, bottom = area
    width, height = size
    dx, dy = offset
    extents = [(-cube_size/2, -cube_size/2, cube_size, cube_size), (dx, dy, width, height)]
    extents.extend(other_faces or ())
    cx = max(left+SCREEN_GAP-min(x for x, y, w, h in extents),
             min(center[0], right-SCREEN_GAP-max(x+w for x, y, w, h in extents)))
    cy = max(top+SCREEN_GAP-min(y for x, y, w, h in extents),
             min(center[1], bottom-SCREEN_GAP-max(y+h for x, y, w, h in extents)))
    return (round(cx+dx), round(cy+dy), width, height), (round(cx), round(cy))


def popup_geometry(anchor: tuple[int, int, int, int], size: tuple[int, int],
                   area: tuple[int, int, int, int], avoid: tuple = (), *, below: bool = False,
                   side: bool = False) -> tuple[int, int]:
    """Choose available space around the actual cube, preserving monitor coordinates."""
    x, y, width, height = anchor
    pw, ph = size
    left, top, right, bottom = area
    cx, cy = x+(width-pw)//2, y+(height-ph)//2
    above_y, below_y = y-ph-SCREEN_GAP, y+height+SCREEN_GAP
    right_x, left_x = x+width+SCREEN_GAP, x-pw-SCREEN_GAP
    for ax, ay, aw, ah in avoid:
        if ax < cx+pw and ax+aw > cx:
            above_y = min(above_y, ay-ph-SCREEN_GAP)
            below_y = max(below_y, ay+ah+SCREEN_GAP)
        if ay < cy+ph and ay+ah > cy:
            right_x = max(right_x, ax+aw+SCREEN_GAP)
            left_x = min(left_x, ax-pw-SCREEN_GAP)
    candidates = ((cx, above_y), (cx, below_y), (right_x, cy), (left_x, cy))
    if below:
        candidates = (candidates[1], candidates[0], *candidates[2:])
    elif side:
        candidates = (*candidates[2:], *candidates[:2])
    def score(point: tuple[int, int]) -> int:
        px, py = point
        return sum(max(0, min(px+pw, ax+aw)-max(px, ax))*
                   max(0, min(py+ph, ay+ah)-max(py, ay)) for ax, ay, aw, ah in (anchor, *avoid))
    positions = [(max(left+SCREEN_GAP, min(px, right-pw-SCREEN_GAP)),
                  max(top+SCREEN_GAP, min(py, bottom-ph-SCREEN_GAP))) for px, py in candidates]
    return min(positions, key=score)


def stable_rows(previous: list[int], rows: list[tuple[int, str, bool]]) -> list[tuple[int, str, bool]]:
    """Focus must not move a card under the pointer just because foreground changed."""
    current = {row[0]: row for row in rows}
    order = [handle for handle in previous if handle in current]
    order.extend(handle for handle in current if handle not in order)
    return [current[handle] for handle in order]


class FocusBar:
    """One expanded cube face; the resident cube owns movement and scheduling."""

    COLLAPSED_EDGE = 32

    def __init__(self, owner: Any) -> None:
        from mo_desktop.layered import NativeLayeredWindow
        from interface.desktop_widgets import DesktopWindowEffectLayer

        self.owner = owner
        self.cube = owner._companion._cube
        self._visuals = owner._visuals
        self._closed = False
        self._taskbars = None
        self._surface = self._popup = None
        self._effects = DesktopWindowEffectLayer()
        self._popup_effects = DesktopWindowEffectLayer()
        self._rows, self._hits, self._icons = [], {}, {}
        self._pins, self._pin_offset, self._pins_at = [], 0, 0.0
        self._pins_pending = False
        self._query, self._search_note = "", ""
        self._results, self._result_icons = [], {}
        self._search_pending, self._search_at = False, 0.0
        self._search_selection = None
        self._result_selected = 0
        from core.desktop.search import DesktopSearch
        self._search = DesktopSearch()
        self._offset, self._capacity = 0, 4
        self._collapsed, self._collapse_at = False, 0.0
        self._settings_open, self._settings_drag = False, False
        self._idle_opacity, self._settings_preview_until = IDLE_OPACITY, 0.0
        self._settings_mouse_down = False
        self._power_delay = self._power_busy = False
        self._power_action, self._power_error = "", ""
        self._power_minutes, self._power_deadline = 15, 0.0
        self._layout_motion = None
        self._paint_key = None
        self._painted_hover = None
        self._sprite_key = None
        self._cube_sprites = []
        self._image = None
        self._opacity = 1.0
        self._tick_at = time.perf_counter()
        self._scan_at = self._hold_until = self._feedback_until = 0.0
        self._hover = self._pressed = self._drag = None
        self._hover_at = self._last_toggle_press = 0.0
        self._drag_moved = False
        self._failed_handle = None
        self._tray_pending = self._tray_failed = False
        self._tray_until = 0.0
        self._popup_kind, self._popup_handle = "", None
        self._popup_amount = self._popup_until = 0.0
        self._popup_geometry = (0, 0, 0, 0)
        self._popup_hits = {}
        self._popup_preview = None
        self._opening = True
        self._opening_at = time.perf_counter()
        self._expansion = 0.0
        self._source_center, source = self.cube.launch_piece(3, self._opening_at)
        from PIL import Image
        premultiplied = source.convert("RGBa")
        self._source_image = Image.frombytes("RGBA", source.size, premultiplied.tobytes())
        self._saved = {key: getattr(self.cube, key, None) for key in
                       ("_glide_dur", "_home", "_charging", "_fade", "_fade_target")}
        # This is the native input/pixel projection of the fourth cube. The
        # resident cube suppresses only that sprite while this face owns it.
        self._surface = NativeLayeredWindow(on_message=self._message, post=owner._post_gui,
                                           title="MO Desktop — Focus", activate=False)
        self._hwnd = self._surface._native_hwnd
        self._taskbars = TaskbarVisibility()
        self._area = self._work_area()
        self._cube_center = (self.cube._x, self.cube._y)
        bx, by = self.cube._bases[3]
        edge = self.cube._cube_edge
        self._face_offset = (bx-self.cube._size/2-edge/2, by-self.cube._size/2-edge/2)
        self._rows = switchable_windows(limit=WINDOW_LIMIT)
        self._geometry = None
        self._fit_visible_rows()
        self.cube._focus_mode = True
        self.cube._focus_opacity = 1.0
        self.cube._focus_controller = self
        self.cube._glide_dur = 0.0
        self.cube._fade = self.cube._fade_target = 0.0
        self.cube.set_home(None)
        self.cube.hide_running_apps()
        self.cube._show()
        self._position_cube()
        self._paint_page()
        relayout = getattr(self.cube, "relayout_docked_faces", None)
        if callable(relayout):
            relayout(self)   # a docked Dashboard now spans down to Focus


    def _work_area(self) -> tuple[int, int, int, int]:
        return self._taskbars.work_area(self.cube._win)


    def _fit_visible_rows(self, *, animate: bool = False) -> None:
        from mo_desktop.design import DEFAULT_BUBBLE_DESIGN
        width = DEFAULT_BUBBLE_DESIGN.content_width+2*self._visuals.metrics.panel_padding
        composer = getattr(self.cube, "_composer_controller", None)
        if composer is not None:
            width = composer.cube_extent()[2]
        self._pin_capacity = max(1, (width-16)//37)
        size = (40, 40) if self._collapsed else (width, FACE_SIZE+(40 if self._pins else 0))
        geometry, center = cube_geometry(self._cube_center, self._area, self.cube._size, self._face_offset, size,
                                        self.cube.docked_faces(exclude="focus"))
        if self._layout_motion is not None and not animate and geometry == self._layout_motion[2]:
            return
        if geometry == self._geometry and size == getattr(self, "_target_size", None):
            return
        if animate and self._geometry is not None:
            self._layout_motion = (time.perf_counter(), self._geometry, geometry)
            self._layout_source = self._published_image
            self._layout_before_collapsed = self._published_image.width <= 40
            self._layout_amount = 0.0
        else:
            self._geometry = geometry
        self._cube_center = center
        self._target_size = size
        self._paint_key = None

    def _tick_layout(self, now: float) -> None:
        if self._opening:
            self._expansion = _ease_out(min(1.0, (now-self._opening_at)/.20))
            if self._expansion == 1:
                self._opening = False
                self._hold_until = now+.4
            self._place_face()
        if self._layout_motion is None:
            return
        started, before, after = self._layout_motion
        progress = min(1.0, (now-started)/(DEFAULT_DESKTOP_PANEL_DESIGN.transition_ms/1000))
        amount = _ease_out(progress)
        self._layout_amount = amount
        self._geometry = tuple(round(a+(b-a)*amount) for a, b in zip(before, after))
        if progress == 1:
            self._layout_motion = None
            self._layout_source = None
        self._place_face()


    def _position_cube(self, *, dragging: bool = False) -> None:
        cube = self.cube
        if (self._collapsed and not dragging) or cube._launcher_active or getattr(cube, "_app_launch_origin", None) is not None:
            return
        x, y = self._cube_center
        moved = (cube._x, cube._y) != (x, y)
        cube._x, cube._y = x, y
        cube._from = cube._to = (x, y)
        if moved:
            cube._reposition()
        if moved:
            for bubble in (getattr(self.owner._companion, "_bubble", None),
                           getattr(self.owner._companion, "_dashboard_bubble", None)):
                if bubble and getattr(bubble, "_visible", False):
                    bubble._repaint()

    def _move_to(self, center: tuple[float, float]) -> None:
        self._close_popup()
        self._layout_motion = None
        self._geometry, self._cube_center = cube_geometry(center, self._area, self.cube._size,
                                                         self._face_offset, self._target_size,
                                                         self.cube.docked_faces(exclude="focus"))
        self._position_cube(dragging=True)
        self._place_face()


    def apply_visual_state(self, visuals: DesktopVisualState) -> None:
        if not isinstance(visuals, DesktopVisualState):
            raise TypeError("Focus requires DesktopVisualState")
        self._visuals = visuals
        self._close_popup()
        self._paint_key = None
        self._paint_page()

    def set_actuation_yield(self, active: bool) -> None:
        """Yield input before the tool starts, while the existing clock fades pixels."""
        self._surface.set_click_through(active)
        if active:
            self._surface.blur_text_input()
            self._close_popup()
            self._hover = self._pressed = self._drag = None
            import win32gui
            if win32gui.GetCapture() == self._hwnd:
                win32gui.ReleaseCapture()

    def set_launcher_active(self, active: bool) -> None:
        self._close_popup()
        self._surface.set_click_through(active or bool(getattr(self.cube, "_actuation_yield", False)))
        if active:
            self._opacity = 0.0
            self._surface.hide()
            self._effects.hide()
        else:
            self._place_face()

    def _refresh(self) -> None:
        self._taskbars.maintain()
        self._area = self._work_area()
        self._rows = stable_rows([row[0] for row in self._rows], switchable_windows(limit=WINDOW_LIMIT))
        count = len(self._results) if self._query else len(self._rows)
        self._offset = min(self._offset, max(0, count-self._capacity))
        self._fit_visible_rows()
        self._paint_page()

    def _paint_page(self) -> None:
        from interface.desktop_brand import make_glyph_icon
        source_rows = ([(row.path, row.name, index == self._result_selected) for index, row in enumerate(self._results)]
                       if self._query else self._rows)
        rows = [] if self._collapsed else source_rows[self._offset:self._offset+self._capacity]
        self._icons = {h: icon for h, icon in self._icons.items() if h in {r[0] for r in self._rows}}
        for handle, _title, _active in ([] if self._query else rows):
            if handle not in self._icons:
                self._icons[handle] = window_icon(handle) or make_glyph_icon("open", 24, color=self._visuals.palette.muted)
        stamp = datetime.now()
        sprite = None
        if self._collapsed:
            sprite_key = (self.cube._color_rgb, self.cube._corner, self.cube._glow, self.cube._form)
            if sprite_key != self._sprite_key:
                self._sprite_key = sprite_key
                self._cube_sprites = self.cube._render_sprite_set(self.cube._color_rgb, edge=self.COLLAPSED_EDGE)
            brightness = self.cube._cube_state(3, time.perf_counter(), projected=True)[2]
            level = max(0, min(len(self._cube_sprites)-1, round(brightness*(len(self._cube_sprites)-1))))
            sprite = self._cube_sprites[level]
        key = (self._target_size, self._collapsed, tuple(rows), self._hover, self._failed_handle,
               self._tray_failed, stamp.strftime("%Y-%m-%d %H:%M"), self._visuals, len(self._rows), self.cube._color_rgb, self.cube._corner,
               self._query, self._search_pending, self._search_note, self._search_selection, self._pin_offset, tuple(p[0] for p in self._pins),
               self._settings_open, self._idle_opacity, self._power_action,
               self._power_delay, self._power_minutes, self._power_remaining(), self._power_busy, self._power_error, id(sprite))
        if key == self._paint_key:
            return
        previous_key, self._paint_key = self._paint_key, key
        pins = tuple(self._pins[self._pin_offset:self._pin_offset+self._pin_capacity]) if not self._collapsed else ()
        row_kind = "result" if self._query else "window"
        if not self._patch_hover_rows(previous_key, key, rows, row_kind):
            self._paint_full_face(rows, row_kind, pins, stamp, sprite)
        self._painted_hover = self._hover
        self._publish_face(rows, row_kind, pins)

    def _patch_hover_rows(self, previous_key: Any, key: tuple, rows: list, row_kind: str) -> bool:
        """When only the hover moved between window rows, redraw just those rows (row 20: the
        whole-face repaint cost 30 ms on the cubes' GUI thread for each hover change)."""
        from mo_desktop.focus_paint import row_hover_patch
        if (previous_key is None or self._image is None or self._collapsed or self._settings_open
                or len(previous_key) != len(key) or previous_key[:3] + previous_key[4:] != key[:3] + key[4:]):
            return False
        handles = {handle: index for index, (handle, _title, _active) in enumerate(rows)}

        def row_of(hover: Any) -> int | None:
            if hover is None:
                return None
            if isinstance(hover, tuple) and len(hover) == 2 and hover[0] in {row_kind, "close"} and hover[1] in handles:
                return handles[hover[1]]
            raise LookupError

        try:
            indexes = tuple(index for index in (row_of(getattr(self, "_painted_hover", None)), row_of(self._hover))
                            if index is not None)
        except LookupError:
            return False
        if not indexes:
            return False
        self._image, patch_hits = row_hover_patch(
            self._image, self._target_size, self._visuals, rows, self._result_icons if self._query else self._icons,
            hover=self._hover, failed=self._failed_handle, row_kind=row_kind, indexes=indexes)
        affected = {rows[index][0] for index in indexes}
        hits = {name: box for name, box in self._hits.items()
                if not (isinstance(name, tuple) and len(name) == 2 and name[0] in {row_kind, "close"} and name[1] in affected)}
        hits.update(patch_hits)
        self._hits = hits
        return True

    def _paint_full_face(self, rows: list, row_kind: str, pins: tuple, stamp: Any, sprite: Any) -> None:
        from mo_desktop.focus_paint import cube_face
        self._image, self._hits = cube_face(self._target_size, self._visuals, rows, self._result_icons if self._query else self._icons,
            collapsed=self._collapsed, hover=self._hover, failed=self._failed_handle,
            tray_failed=self._tray_failed, now=stamp, total=len(self._rows),
            cube_color=self.cube._color_rgb, cube_corner=self.cube._corner, cube_sprite=sprite, pins=pins,
            query=self._query, searching=self._search_pending or bool(self._search_at), search_note=self._search_note,
            selection=self._search_selection, row_kind=row_kind, settings=self._settings_open, idle_opacity=self._idle_opacity,
            power=(self._power_action, self._power_delay, self._power_minutes,
                   self._power_remaining(), self._power_busy, self._power_error))

    def _publish_face(self, rows: list, row_kind: str, pins: tuple) -> None:
        labels = {"toggle": "Expand windows" if self._collapsed else "Collapse windows",
                  "tray": "System tray", "clock": "Calendar", "search": "Search this PC", "search:clear": "Clear search", "settings": "Focus settings",
                  "dimming": f"Dimming {round((1-self._idle_opacity)*100)} percent"}
        labels.update({(row_kind, handle): title for handle, title, _active in rows})
        labels.update({("close", handle): "Close "+title for handle, title, _active in rows})
        labels.update({("pin", path): name for path, name, _icon in pins})
        labels.update({"power:sleep": "Select Sleep", "power:restart": "Select Restart", "power:shutdown": "Select Shut down",
                       "power:delay": "Delay, " + ("On" if self._power_delay else "Off"),
                       "power:less": "Decrease delay", "power:more": "Increase delay",
                       "power:cancel": "Cancel power timer", "power:confirm": self._power_confirm_label()})
        self._surface.set_controls({key: (labels[key], bounds) for key, bounds in self._hits.items()}, self._invoke)
        self._surface.text_input(self._hits.get("search"), self._search_changed)
        self._place_face()

    def _place_face(self) -> None:
        from PIL import Image
        if self._image is None or self.cube._launcher_active:
            return
        x, y, width, height = self._geometry
        amount = self._expansion
        if amount < 1:
            source = self._source_image
            sx, sy = self._source_center[0]-source.width/2, self._source_center[1]-source.height/2
            x, y = round(sx+(x-sx)*amount), round(sy+(y-sy)*amount)
            size = (max(1, round(source.width+(width-source.width)*amount)),
                    max(1, round(source.height+(height-source.height)*amount)))
            image = Image.blend(source.resize(size, Image.Resampling.LANCZOS),
                                self._image.resize(size, Image.Resampling.LANCZOS), amount)
        else:
            image = self._image if self._image.size == (width, height) else self._image.resize((width, height), Image.Resampling.LANCZOS)
            if self._layout_motion is not None:
                from PIL import ImageColor
                from mo_desktop.card import fold_frame
                panel = ImageColor.getrgb(self._visuals.palette.card)
                small_radius = 40*self.cube._corner
                panel_radius = self._visuals.metrics.panel_corner_radius
                image = fold_frame(self._layout_source, self._image, (width, height), self._layout_amount,
                    start_color=self.cube._color_rgb if self._layout_before_collapsed else panel,
                    end_color=self.cube._color_rgb if self._collapsed else panel,
                    start_radius=small_radius if self._layout_before_collapsed else panel_radius,
                    end_radius=small_radius if self._collapsed else panel_radius)
        if not self._surface.blit(image, x, y, premultiplied=True, opacity=round(self._opacity*255)):
            raise RuntimeError(self._surface.failure_detail())
        self._published_image, self._published_position = image, (x, y)
        self._refresh_effect()

    def launch_piece(self) -> tuple[tuple[float, float], Any]:
        """Let app/launcher capture use the real expanded fourth face, not an invisible sprite."""
        from PIL import Image
        x, y = self._published_position
        source = self._published_image
        # The published face is premultiplied; reverse that representation once.
        image = Image.frombytes("RGBa", source.size, source.tobytes()).convert("RGBA")
        image.putalpha(image.getchannel("A").point(lambda alpha: round(alpha*self._opacity)))
        return (x+image.width/2, y+image.height/2), image


    def _refresh_effect(self) -> None:
        _x, _y, width, height = self._geometry
        radius = min(width, height)*self.cube._corner if self._collapsed else self._visuals.metrics.panel_corner_radius
        self._effects.refresh_hwnd(self._hwnd, width, height, radius,
            self._visuals.effects, self._visuals.token("_GLOW"),
            show=self._opacity >= .82 and not self._collapsed and not self._opening and self._layout_motion is None)


    @staticmethod
    def _hit(point: tuple[int, int], hits: dict) -> Any:
        px, py = point
        return next((key for key, (x, y, w, h) in hits.items() if x <= px < x+w and y <= py < y+h), None)

    def _message(self, hwnd: int, message: int, wparam: int, lparam: int) -> int | None:
        import win32gui
        now = time.perf_counter()
        point = (lparam & 0xffff, (lparam >> 16) & 0xffff)
        if message == 0x0200:  # WM_MOUSEMOVE
            if self._settings_drag and wparam & 1:
                self._set_dimming(point[0])
                return 0
            key = self._hit(point, self._hits)
            if key != self._hover:
                self._hover, self._hover_at = key, now+.14
                self._paint_page()
            if self._drag is not None and wparam & 1:
                x, y = win32gui.GetCursorPos()
                if max(abs(x-self._drag[0]), abs(y-self._drag[1])) >= 6:
                    self._drag_moved = True
                    self._last_toggle_press = 0.0
                    self._move_to((self._drag_origin[0]+x-self._drag[0], self._drag_origin[1]+y-self._drag[1]))
            return 0
        if message in (0x0201, 0x0203):
            self._pressed = self._hit(point, self._hits)
            self._drag_moved = False
            if self._pressed == "dimming":
                self._settings_drag = True
                self._set_dimming(point[0])
                win32gui.SetCapture(hwnd)
                return 0
            if self._pressed == "toggle":
                from mo_desktop.focus_native import _api
                if message == 0x0203 or now-self._last_toggle_press <= _api().user.GetDoubleClickTime()/1000:
                    self._exit_focus()
                    return 0
                self._last_toggle_press = now
                self._collapse_at = 0.0
                # Native input is queued onto the resident GUI lane. The pointer may already
                # have moved when this press is consumed; anchor the press itself.
                self._drag, self._drag_moved = win32gui.ClientToScreen(hwnd, point), False
                self._drag_origin = self._cube_center
                if self._collapsed:
                    self.cube._focus_mode = True
                    self.cube._glide_dur = 0.0
                win32gui.SetCapture(hwnd)
            return 0
        if message == 0x0202:
            if win32gui.GetCapture() == hwnd:
                win32gui.ReleaseCapture()
            if self._settings_drag:
                self._settings_drag = False
                self._pressed = None
                return 0
            key = self._hit(point, self._hits)
            self._drag = None
            if key == self._pressed and not self._drag_moved:
                self._invoke(key)
            self._pressed = None
            return 0
        if message == 0x020A:
            if isinstance(self._hover, tuple) and self._hover[0] == "pin":
                step = -1 if (wparam >> 16) & 0x8000 == 0 else 1
                self._pin_offset = max(0, min(len(self._pins)-self._pin_capacity, self._pin_offset+step))
                self._paint_page()
                return 0
            self.scroll(-1 if (wparam >> 16) & 0x8000 == 0 else 1)
            return 0
        if message == 0x0100:
            if self._surface.text_selection() is not None:
                if wparam == 27:
                    self._surface.clear_text_input()
                elif wparam in (38, 40) and self._results:
                    self._result_selected = max(0, min(len(self._results)-1, self._result_selected+(-1 if wparam == 38 else 1)))
                    self._offset = max(0, min(self._result_selected, max(self._offset, self._result_selected-self._capacity+1)))
                    self._paint_page()
                elif wparam == 13 and self._results:
                    self._launch_path(self._results[self._result_selected].path)
                return 0
            directions = {37: (-16, 0), 38: (0, -16), 39: (16, 0), 40: (0, 16)}
            if wparam == 27:
                self._close_popup()
            elif wparam in directions:
                dx, dy = directions[wparam]
                self._move_to((self._cube_center[0]+dx, self._cube_center[1]+dy))
            elif wparam in (13, 32):
                self._invoke(self._hover or "toggle")
            elif wparam == 9:
                keys = list(self._hits)
                index = keys.index(self._hover) if self._hover in keys else -1
                self._hover = keys[(index+1) % len(keys)]
                self._paint_page()
            return 0
        return None

    def _invoke(self, key: Any) -> None:
        if isinstance(key, tuple):
            if key[0] == "window":
                self._switch(key[1])
            elif key[0] == "close":
                self._switch(key[1], close=True)
            elif key[0] in {"pin", "result"}:
                self._launch_path(key[1])
        elif key == "toggle":
            from mo_desktop.focus_native import _api
            self._collapse_at = time.perf_counter()+_api().user.GetDoubleClickTime()/1000
        elif key == "tray": self._open_tray()
        elif key == "clock": self._show_calendar()
        elif key == "search":
            self._settings_open = False
            self._surface.focus_text_input()
            self._paint_page()
        elif key == "search:clear":
            self._surface.clear_text_input()
            self._surface.focus_text_input()
        elif key == "settings":
            self._surface.blur_text_input()
            self._settings_open = not self._settings_open
            self._settings_mouse_down = False
            if not self._settings_open and not self._power_deadline:
                self._power_action = ""
            self._close_popup()
            self._paint_page()
        elif isinstance(key, str) and key.startswith("power:"):
            self._power_control(key)
        elif key == "dimming":
            self._idle_opacity = .9 if self._idle_opacity <= .15 else max(.1, self._idle_opacity-.1)
            self._settings_preview_until = time.perf_counter()+1.5
            self._paint_page()

    def _set_dimming(self, x: int) -> None:
        left, _y, width, _h = self._hits["dimming"]
        self._idle_opacity = max(.1, min(1., 1-(x-left)/width))
        self._settings_preview_until = time.perf_counter()+1.5
        self._paint_page()

    def _power_remaining(self) -> int:
        from math import ceil
        return max(1, ceil(self._power_deadline-time.perf_counter())) if self._power_deadline else 0

    def _power_confirm_label(self) -> str:
        action = {"sleep": "Sleep", "restart": "Restart", "shutdown": "Shut down"}.get(self._power_action, "Power")
        return f"{action} in {self._power_minutes} min" if self._power_delay else f"{action} now"

    def _power_control(self, key: str) -> None:
        if self._power_busy:
            return
        self._power_error = ""
        if key == "power:cancel":
            self._power_deadline = 0.0
            self._power_action = ""
        elif not self._power_deadline:
            action = key.partition(":")[2]
            if action in {"sleep", "restart", "shutdown"}:
                self._power_action = action
            elif action == "delay":
                self._power_delay = not self._power_delay
                self._power_action = ""
            elif action in {"less", "more"}:
                self._power_minutes = max(1, min(240, self._power_minutes + (-5 if action == "less" else 5)))
            elif action == "confirm" and self._power_action:
                if self._power_delay:
                    self._power_deadline = time.perf_counter()+self._power_minutes*60
                else:
                    self._execute_power()
        self._paint_page()

    def _tick_settings(self) -> None:
        from core.desktop.win32 import mouse_button_held
        down = mouse_button_held()
        if down and not self._settings_mouse_down:
            point = self.cube._frame_pointer()
            x, y, w, h = self._geometry
            if point is not None and not (x <= point[0] < x+w and y <= point[1] < y+h):
                self._settings_open = False
                if not self._power_deadline:
                    self._power_action = ""
                self._paint_page()
        self._settings_mouse_down = down

    def _execute_power(self) -> None:
        import threading
        from core.desktop.win32 import request_power_action
        self._power_deadline, self._power_busy = 0.0, True
        action = self._power_action
        def completed(error: str) -> None:
            if self._closed:
                return
            self._power_busy, self._power_error = False, error
            self._power_action = ""
            self._paint_page()
        def invoke() -> None:
            error = ""
            try:
                request_power_action(action)
            except Exception:
                _LOG.exception("Windows rejected the explicit power action")
                error = "Windows could not complete this"
            self.owner._post_gui(lambda: completed(error))
        threading.Thread(target=invoke, name="mo-focus-power", daemon=True).start()

    def _launch_path(self, path: str) -> None:
        import os
        self._close_popup()
        try:
            os.startfile(path)
        except OSError:
            self._failed_handle = path
            self._feedback_until = time.perf_counter()+2
            self._paint_page()

    def _search_changed(self, value: str) -> None:
        if self._closed or value == self._query:
            return
        self._query = value
        self._results, self._result_icons, self._search_note = [], {}, ""
        self._offset = self._result_selected = 0
        self._search_at = time.perf_counter()+.25 if value.strip() else 0.0
        self._close_popup()
        self._paint_page()

    def _tick_search(self, now: float) -> None:
        selection = self._surface.text_selection()
        if selection != self._search_selection:
            self._search_selection = selection
            self._paint_page()
        if not self._search_at or now < self._search_at or self._search_pending:
            return
        import threading
        from mo_desktop.layered import path_icon
        query = self._query
        self._search_pending, self._search_at = True, 0.0
        def completed(rows: list, icons: dict, note: str) -> None:
            self._search_pending = False
            if self._closed:
                return
            if self._query == query:
                self._results, self._result_icons, self._search_note = rows, icons, note
                self._paint_page()
        def search() -> None:
            try:
                rows, note = self._search.query(query)
                icons = {row.path: path_icon(row.path) for row in rows}
            except Exception:
                _LOG.exception("Focus local search failed")
                rows, icons, note = [], {}, "Search unavailable"
            self.owner._post_gui(lambda: completed(rows, icons, note))
        threading.Thread(target=search, name="mo-focus-search", daemon=True).start()

    def _refresh_pins(self, now: float) -> None:
        if self._pins_pending or now-self._pins_at < 15:
            return
        import threading
        self._pins_pending, self._pins_at = True, now
        def completed(pins: list) -> None:
            self._pins_pending = False
            if self._closed:
                return
            if [p[0] for p in pins] != [p[0] for p in self._pins]:
                self._pins = pins
                self._pin_offset = min(self._pin_offset, max(0, len(pins)-self._pin_capacity))
                self._fit_visible_rows(animate=not self._opening)
                self._paint_page()
        def load() -> None:
            from core.desktop.apps import pinned_taskbar_apps
            from mo_desktop.layered import path_icon
            try:
                pins = [(app.path, app.name, path_icon(app.path)) for app in pinned_taskbar_apps()]
            except Exception:
                _LOG.exception("Could not read pinned taskbar apps")
                pins = []
            self.owner._post_gui(lambda: completed(pins))
        threading.Thread(target=load, name="mo-focus-pins", daemon=True).start()

    def _exit_focus(self, _event: Any = None) -> str:
        self._collapse_at = 0.0
        self.owner._on_toggle_focus()
        return "break"

    def _toggle_collapsed(self) -> None:
        self._surface.blur_text_input()
        self.cube._clear_trace()
        self._collapsed = not self._collapsed
        if not self._collapsed:
            self._saved = {key: getattr(self.cube, key, None) for key in
                           ("_glide_dur", "_home", "_charging", "_fade", "_fade_target")}
            self._cube_center = (self.cube._x, self.cube._y)
            self.cube._focus_mode = True
            self.cube._glide_dur = 0.0
            self.cube.set_home(None)
            self.cube._fade = self.cube._fade_target = 0.0
            bx, by = self.cube._bases[3]
            self._face_offset = (bx-self.cube._size/2-self.cube._cube_edge/2,
                                 by-self.cube._size/2-self.cube._cube_edge/2)
        self._close_popup()
        self._hover = None
        self._hold_until = time.perf_counter()+.6
        self._fit_visible_rows(animate=True)
        self._paint_page()

    def follow_cube_position(self, now: float | None = None) -> None:
        """The folded face follows the normal movement owner after its frame advances."""
        if not self._collapsed or self._layout_motion is not None or self.cube._launcher_active:
            return
        if self.cube._focus_mode and self._drag is None:
            self.cube._focus_mode = False
            for key, value in self._saved.items():
                setattr(self.cube, key, value)
            self._saved = {}
            self.cube._focus_opacity = 1.0
        center = (self.cube._x, self.cube._y)
        cx, cy, _brightness, _alpha = self.cube._cube_state(3, time.perf_counter() if now is None else now, projected=True)
        margin = (self._target_size[0]-self.COLLAPSED_EDGE)/2
        offset = (cx-self.cube._size/2-self.cube._cube_edge/2-margin,
                  cy-self.cube._size/2-self.cube._cube_edge/2-margin)
        if center != self._cube_center or offset != self._face_offset:
            self._face_offset = offset
            self._cube_center = center
            self._fit_visible_rows()
            if self._cube_center != center:
                self.cube._x, self.cube._y = self._cube_center
                self.cube._from = self.cube._to = self._cube_center
                self.cube._reposition()
            self._place_face()
        self._paint_page()


    def group_bounds(self, *, include_cubes: bool = True) -> tuple[int, int, int, int]:
        x, y, width, height = self._geometry
        cx, cy = self._cube_center
        bounds = [(x, y, width, height)]
        for dx, dy, cw, ch in self.cube.docked_faces(exclude="focus"):
            bounds.append((round(cx+dx), round(cy+dy), cw, ch))
        if include_cubes:
            half = self.cube._size/2
            bounds.append((round(cx-half), round(cy-half), self.cube._size, self.cube._size))
        left, top = min(b[0] for b in bounds), min(b[1] for b in bounds)
        return left, top, max(b[0]+b[2] for b in bounds)-left, max(b[1]+b[3] for b in bounds)-top

    def _popup_anchor(self, key: Any) -> tuple[int, int, int, int]:
        x, _y, _width, _height = self._geometry
        _gx, gy, _gw, gh = self.group_bounds()
        bx, by, bw, bh = self._hits[key]
        if key == "tray":
            return x+bx, self._geometry[1]+by, bw, bh
        if isinstance(key, tuple) and key[0] == "window":
            return self.group_bounds(include_cubes=False)
        return x+bx, gy, bw, gh

    def panel_position(self, width: int, height: int) -> tuple[int, int]:
        return popup_geometry(self.group_bounds(include_cubes=False), (width, height), self._area,
                              (self.group_bounds(),))

    def _popup_position(self, anchor: tuple[int, int, int, int], size: tuple[int, int], *, below: bool = False, side: bool = False) -> tuple[int, int]:
        avoid = [self.group_bounds()]
        bubble = getattr(self.owner._companion, "_bubble", None)
        if bubble and getattr(bubble, "_visible", False):
            bx, by, br, bb = bubble._bounds
            avoid.append((bx, by, br-bx, bb-by))
        return popup_geometry(anchor, size, self._area, tuple(avoid), below=below, side=side)


    def _open_popup(self, kind: str, image: Any, anchor: Any) -> None:
        from mo_desktop.layered import NativeLayeredWindow
        self._close_popup()
        self._popup = NativeLayeredWindow(on_message=self._popup_message, post=self.owner._post_gui,
                                         title="MO Desktop — "+kind.title(), activate=kind == "calendar")
        self._popup_kind = kind
        self._popup_geometry = (*self._popup_position(self._popup_anchor(anchor), image.size, below=kind == "tray", side=kind == "preview"), *image.size)
        self._popup_image = image
        self._popup_amount = 0.0
        self._popup_until = time.perf_counter()+DEFAULT_DESKTOP_PANEL_DESIGN.transition_ms/1000
        import win32api
        self._popup_mouse_down = bool(win32api.GetAsyncKeyState(1) & 0x8000)
        x, y, _w, _h = self._popup_geometry
        self._popup.blit(image, x, y, premultiplied=True, opacity=0)
        if kind == "calendar":
            import win32gui
            win32gui.SetForegroundWindow(self._popup._native_hwnd)

    def _show_preview(self, handle: int) -> None:
        if self._popup_kind == "calendar" or self._popup_handle == handle:
            return
        import win32gui
        from mo_desktop.focus_paint import preview
        minimized = is_minimized(handle)
        try:
            left, top, right, bottom = (win32gui.GetWindowPlacement(handle)[4] if minimized
                                        else win32gui.GetWindowRect(handle))
        except win32gui.error:
            return  # The source can close between the bounded scan and hover.
        reuse = self._popup_kind == "preview" and self._popup is not None
        if reuse:
            width, height = self._popup_geometry[2:]
            if self._popup_preview is not None:
                self._popup_preview.close()
        else:
            height = self.group_bounds(include_cubes=False)[3]
            pl, pt, pr, pb = fit_preview((0, 0, 360, height-76), (right-left, bottom-top))
            width = max(256, pr-pl)+24
        title = next((row[1] for row in self._rows if row[0] == handle), "Window")
        if handle not in self._icons:
            self._icons[handle] = window_icon(handle)
        image = preview((width, height), self._visuals, title, self._icons.get(handle), minimized)
        if not reuse:
            self._open_popup("preview", image, ("window", handle))
        self._popup_image = image
        self._popup_handle = handle
        self._popup_preview = WindowPreview(self._popup._native_hwnd, handle)
        if self._popup_preview.source_size() is None:
            self._popup_image = preview((width, height), self._visuals, title, self._icons.get(handle),
                                        minimized, available=False)
        x, y, _w, _h = self._popup_geometry
        self._popup.blit(self._popup_image, x, y, premultiplied=True, opacity=round(self._popup_amount*255))
        self._popup_preview.update((12, 64, width-24, height-76), self._popup_amount)
        self._popup.set_controls({"preview": ("Switch to "+title, (0, 0, width, height))}, lambda _key: self._switch(handle))


    def _show_calendar(self) -> None:
        if self._popup_kind == "calendar":
            self._close_popup()
            return
        from mo_desktop.focus_paint import calendar_image
        today = datetime.now()
        self._month = (today.year, today.month)
        image, hits = calendar_image(self._visuals, *self._month, today)
        self._open_popup("calendar", image, "clock")
        self._popup_hits = hits
        self._popup.set_controls({key: ("Previous month" if key == "month_previous" else "Next month", bounds)
                                  for key, bounds in hits.items()}, self._calendar_month)

    def _calendar_month(self, key: str) -> None:
        from mo_desktop.focus_paint import calendar_image
        year, month = self._month
        offset = year*12+month-1+(-1 if key == "month_previous" else 1)
        self._month = (offset//12, offset%12+1)
        self._popup_image, self._popup_hits = calendar_image(self._visuals, *self._month, datetime.now())
        x, y, _w, _h = self._popup_geometry
        self._popup.blit(self._popup_image, x, y, premultiplied=True)

    def _popup_message(self, hwnd: int, message: int, wparam: int, lparam: int) -> int | None:
        if message == 0x020A and self._popup_kind == "preview":
            import win32gui
            handles = [row[0] for row in self._rows if win32gui.IsWindow(row[0])]
            if handles:
                index = handles.index(self._popup_handle) if self._popup_handle in handles else 0
                step = 1 if wparam >> 16 & 0x8000 else -1
                self._show_preview(handles[(index+step) % len(handles)])
            return 0
        if self._popup_kind == "tray":
            key = self._hit((lparam & 0xffff, lparam >> 16 & 0xffff), self._popup_hits)
            if message == 0x0200 and key != self._tray_hover:
                from mo_desktop.focus_paint import tray_image
                self._tray_hover = key
                self._popup_image, self._popup_hits = tray_image(self._tray_items, self._visuals, key)
                self._popup.blit(self._popup_image, *self._popup_geometry[:2], premultiplied=True)
                return 0
            if message in (0x0202, 0x0205) and key is not None:
                self._invoke_tray(key, context=message == 0x0205)
                return 0
        if message == 0x0202:
            if self._popup_kind == "preview":
                self._switch(self._popup_handle)
            else:
                key = self._hit((lparam & 0xffff, (lparam >> 16) & 0xffff), self._popup_hits)
                if key in {"month_previous", "month_next"}:
                    self._calendar_month(key)
            return 0
        if message == 0x0100 and wparam == 27:
            self._close_popup()
            return 0
        if message == 0x0006 and not wparam and lparam != self._hwnd:
            self._close_popup()
            return 0
        return None

    def _close_popup(self) -> None:
        if self._popup_preview is not None:
            self._popup_preview.close()
            self._popup_preview = None
        if self._popup is not None:
            self._popup.destroy()
            self._popup = None
        self._popup_effects.hide()
        self._popup_kind, self._popup_handle = "", None
        self._popup_amount = 0.0

    def _popup_target(self, now: float) -> float:
        if getattr(self.cube, "_fade_target", 0.0):
            return 0.0
        import win32gui
        px, py = win32gui.GetCursorPos()
        x, y, w, h = self._popup_geometry
        inside = x-8 <= px <= x+w+8 and y-8 <= py <= y+h+8
        return float(not self.cube._launcher_active and (self._popup_kind in {"calendar", "tray"} or inside
                     or self._popup_kind == "tray" and self._hover == "tray"
                     or self._hover == ("window", self._popup_handle) or now < self._popup_until))

    def _tick_popup(self, now: float, ease: float) -> None:
        if getattr(self.cube, "_fade_target", 0.0):
            self._close_popup()
            return
        point = self.cube._frame_pointer(now)
        x, y, w, h = self._geometry
        over_list = point is not None and x <= point[0] < x+w and y <= point[1] < y+h
        if (isinstance(self._hover, tuple) and self._hover[0] in {"window", "close"} and now >= self._hover_at and not self.cube._launcher_active
                and over_list and not self._collapsed and self._layout_motion is None):
            self._show_preview(self._hover[1])
        if self._popup is None:
            return
        if self._popup_kind == "tray":
            import win32api, win32gui
            down = bool(win32api.GetAsyncKeyState(1) & 0x8000)
            if down and not self._popup_mouse_down:
                px, py = win32gui.GetCursorPos()
                if not any(x <= px < x+w and y <= py < y+h for x, y, w, h in (self._popup_geometry, self._geometry)):
                    self._close_popup()
                    return
            self._popup_mouse_down = down
        target = self._popup_target(now)
        amount = self._popup_amount+(target-self._popup_amount)*ease
        if abs(amount-target) < .002:
            amount = target
        if amount == self._popup_amount:
            return
        self._popup_amount = amount
        if amount == 0:
            self._close_popup()
            return
        self._popup.set_opacity(round(amount*255))
        x, y, w, h = self._popup_geometry
        if self._popup_preview is not None:
            self._popup_preview.update((12, 64, w-24, h-76), amount)
        self._popup_effects.refresh_hwnd(self._popup._native_hwnd, w, h, self._visuals.metrics.panel_corner_radius,
            self._visuals.effects, self._visuals.token("_GLOW"), show=amount >= .82)

    def _switch(self, handle: int, *, close: bool = False) -> None:
        self._close_popup()
        self._hover = None
        if not activate_switchable_window(handle, limit=WINDOW_LIMIT, minimize_active=not close):
            self._failed_handle = handle
            self._feedback_until = self._hold_until = time.perf_counter()+2.0
            self._paint_page()
            return
        self._failed_handle = None
        if close:
            import win32gui
            # Restore/activate first: apps such as Paint display their save prompt
            # inside the main window, where it stays hidden while minimized.
            win32gui.PostMessage(handle, 0x0010, 0, 0)
        self._refresh()

    def scroll(self, direction: int) -> None:
        self._close_popup()
        count = len(self._results) if self._query else len(self._rows)
        self._offset = max(0, min(max(0, count-self._capacity), self._offset+direction))
        self._paint_page()

    def _engaged(self, now: float) -> bool:
        import win32gui
        px, py = win32gui.GetCursorPos()
        x, y, w, h = self._geometry
        cx, cy = self._cube_center
        inside = x <= px < x+w and y <= py < y+h
        if not inside and self._drag is None and self._hover is not None:
            self._hover = None
            self._popup_until = max(self._popup_until, now+.16)
            self._paint_page()
        return bool(inside or abs(px-cx) <= self.cube._size/2 and abs(py-cy) <= self.cube._size/2
                    or self._drag is not None or self._popup_kind == "calendar" or now < self._hold_until
                    or win32gui.GetForegroundWindow() == self._hwnd)

    def _opacity_target(self, now: float) -> float:
        if (self.cube._launcher_active or getattr(self.cube, "_actuation_yield", False)
                or getattr(self.cube, "_fade_target", 0.0)
                or not getattr(self.cube, "_visible", True)):
            return 0.0
        if self._collapsed:
            return self.cube._cube_state(3, now, projected=True)[3] if self.cube._visible else 0.0
        if self._settings_open and now < self._settings_preview_until:
            return self._idle_opacity
        return 1.0 if self._engaged(now) else self._idle_opacity

    def needs_active_frames(self, now: float) -> bool:
        return bool(self._opening or self._layout_motion is not None
                    or abs(self._opacity-self._opacity_target(now)) > .002
                    or self._popup is not None and abs(self._popup_amount-self._popup_target(now)) > .002)


    def tick(self, now: float) -> None:
        if self._closed:
            return
        if self._settings_open:
            self._tick_settings()
        if self._power_deadline:
            if now >= self._power_deadline:
                self._execute_power()
            self._paint_page()
        if getattr(self.cube, "_actuation_yield", False):
            self._close_popup()
        if self._collapse_at and now >= self._collapse_at:
            self._collapse_at = 0.0
            self._toggle_collapsed()
        self._tick_layout(now)
        self._position_cube()
        target = self._opacity_target(now)
        ease = _time_scaled_ease(FADE_EASE, max(0, min(.25, now-self._tick_at)))
        self._tick_at = now
        opacity = self._opacity+(target-self._opacity)*ease
        if abs(opacity-target) < .002:
            opacity = target
        if abs(opacity-self._opacity) > .001:
            self._opacity = opacity
            self._surface.set_opacity(round(opacity*255))
            self.cube._focus_opacity = 1.0 if self._collapsed or self.cube._launcher_active else opacity
            self._refresh_effect()
        self._tick_popup(now, ease)
        self._tick_tray(now)
        if not self._collapsed:
            self._tick_search(now)
            self._refresh_pins(now)
        if self._feedback_until and now >= self._feedback_until:
            self._feedback_until = 0.0
            self._failed_handle = None
            self._tray_failed = False
            self._paint_page()
        if now-self._scan_at >= SCAN_SECONDS:
            self._scan_at = now
            self._refresh()

    def _open_tray(self) -> None:
        if self._popup_kind == "tray":
            self._close_popup()
            return
        if self._tray_pending or self._tray_until:
            return
        import threading
        self._close_popup()
        self._hold_until = time.perf_counter()+2.0
        self._tray_pending = True
        def completed(opened: bool, items: Any = None) -> None:
            if self._closed:
                return
            self._tray_pending = False
            if items:
                from mo_desktop.focus_paint import tray_image
                self._tray_items, self._tray_hover = items, None
                image, hits = tray_image(items, self._visuals)
                self._open_popup("tray", image, "tray")
                self._popup_until = time.perf_counter()+.6
                self._popup_hits = hits
                self._popup.set_controls({key: (items[key].name, box) for key, box in hits.items()}, self._invoke_tray)
                self._tray_failed = False
                self._paint_page()
                return
            self._tray_failed = not opened
            self._tray_until = time.perf_counter()+1 if opened else 0.0
            self._feedback_until = time.perf_counter()+2 if not opened else 0.0
            self._paint_page()
        def invoke() -> None:
            try:
                import uiautomation as auto
                with auto.UIAutomationInitializerInThread():
                    from mo_desktop.focus_native import tray_items
                    import win32gui
                    items = tray_items()
                    if items is not None:
                        overflow = win32gui.FindWindow("NotifyIconOverflowWindow", None)
                        if not overflow and open_system_tray():
                            # Explorer creates this native area lazily. Initialize
                            # it once, then reuse its own buttons while hidden.
                            for _ in range(10):
                                time.sleep(.03)
                                overflow = win32gui.FindWindow("NotifyIconOverflowWindow", None)
                                if overflow:
                                    break
                        if overflow:
                            win32gui.ShowWindow(overflow, 0)
                        items = tray_items()
                        opened = bool(items)
                    else:
                        opened = open_system_tray()  # XAML shell: its native flyout owns presentation.
            except Exception:
                _LOG.exception("Could not open Explorer's hidden-icons flyout")
                opened = False
                items = None
            self.owner._post_gui(lambda: completed(opened, items))
        threading.Thread(target=invoke, name="mo-focus-tray", daemon=True).start()

    def _invoke_tray(self, index: int, *, context: bool = False) -> None:
        import threading
        item = self._tray_items[index]
        self._close_popup()
        def invoke() -> None:
            import uiautomation as auto
            from mo_desktop.focus_native import invoke_tray_item
            try:
                with auto.UIAutomationInitializerInThread():
                    success = invoke_tray_item(item, context=context)
            except Exception:
                _LOG.exception("Native tray action failed")
                success = False
            if not success:
                def failed() -> None:
                    if not self._closed:
                        self._tray_failed = True
                        self._feedback_until = time.perf_counter()+2
                        self._paint_page()
                self.owner._post_gui(failed)
        threading.Thread(target=invoke, name="mo-focus-tray-action", daemon=True).start()

    def _tick_tray(self, now: float) -> None:
        if not self._tray_until:
            return
        from mo_desktop.focus_native import system_tray_popup, position_system_tray
        popup = system_tray_popup()
        if popup is not None:
            handle, (_x, _y, w, h) = popup
            self._tray_failed = not position_system_tray(handle, *self._popup_position(self._popup_anchor("tray"), (w, h), below=True))
            self._tray_until = 0.0
            self._paint_page()
        elif now >= self._tray_until:
            self._tray_until = 0.0
            self._tray_failed = True
            self._feedback_until = now+2.0
            self._paint_page()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._power_deadline = 0.0
        if self._taskbars is not None:
            self._taskbars.restore()
        self._close_popup()
        if self._surface is not None:
            self._surface.destroy()
        self._effects.destroy()
        self._popup_effects.destroy()
        self.cube._focus_mode = False
        self.cube._focus_opacity = 1.0
        self.cube._focus_controller = None
        relayout = getattr(self.cube, "relayout_docked_faces", None)
        if callable(relayout):
            relayout(self)
        for key, value in self._saved.items():
            setattr(self.cube, key, value)
        self.cube._last_geometry = ""
        self.cube._reposition()
