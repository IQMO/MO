"""Pointer, click, drag, and scroll behavior for the Desktop cube."""

from __future__ import annotations

from mo_desktop.gui_loop import pointer_position

import math
import time
from typing import Any

from core.desktop.win32 import mouse_button_held as _mouse_button_held

_DRAG_LEAVE_GRACE_SECONDS = 0.20
_DRAG_STALE_SECONDS = 5.0
_ARM_RELEASE_FACTOR = 1.35
_CUBE_HOLD_MS = 2000

class CubeInteractionMixin:
    """Input behavior mixed into :class:`mo_desktop.cube.DesktopCube`."""

    def set_hover_handler(self, callback: Any) -> None:
        self._hover_handler = callback

    def _update_hover_presence(self, hovered: bool, *, now: float | None = None) -> None:
        active = bool(hovered)
        current = time.perf_counter() if now is None else now
        same = active == bool(getattr(self, "_hovering_cube", False))
        if same and (not active or current < getattr(self, "_hover_refresh_at", 0.0)):
            return
        self._hovering_cube = active
        self._hover_refresh_at = current + 0.75 if active else 0.0
        callback = getattr(self, "_hover_handler", None)
        if callable(callback):
            callback(active)

    def _on_native_event(self, kind: str, event: Any) -> None:
        if self._launcher_active and self._launcher_input is not None:
            self._launcher_input(kind, event)
            return
        if kind == "press":
            self._press_cube(event)
        elif kind == "release":
            self._release_click("right" if event.num == 3 else "left", event)
        elif kind == "double":
            self._handle_double()
        elif kind == "wheel" and self._volume_controls:
            self._on_scroll(event.delta, shift=bool(int(getattr(event, "state", 0) or 0) & 1),
                            point=(getattr(event, "x_root", 0), getattr(event, "y_root", 0)))
        elif kind == "key" and event.keysym == "Escape" and self._escape is not None:
            self._escape()

    def _event_screen_point(self, event: Any | None = None) -> tuple[float, float] | None:
        try:
            if event is not None and hasattr(event, "x_root") and hasattr(event, "y_root"):
                return float(event.x_root), float(event.y_root)
            px, py = pointer_position()
            return float(px), float(py)
        except Exception:
            return None

    def _sample_frame_pointer(self, now: float) -> tuple[float, float] | None:
        """Read the OS pointer once for all consumers in one rendered frame."""
        try:
            px, py = pointer_position()
            point = (float(px), float(py))
        except Exception:
            point = None
        self._pointer_sample = point
        self._pointer_sample_at = float(now)
        self._pointer_sample_ready = True
        return point

    def _frame_pointer(self, now: float | None = None) -> tuple[float, float] | None:
        """Reuse this frame's pointer sample, falling back for direct helper calls."""
        current = float(getattr(self, "_last_tick_at", -2.0) if now is None else now)
        sampled_at = float(getattr(self, "_pointer_sample_at", -1.0))
        if bool(getattr(self, "_pointer_sample_ready", False)) and abs(sampled_at - current) <= 1e-9:
            return getattr(self, "_pointer_sample", None)
        try:
            px, py = pointer_position()
            return float(px), float(py)
        except Exception:
            return None

    def _press_cube(self, event: Any | None = None) -> None:
        self._cancel_cube_hold()
        if bool(getattr(self, "_launcher_active", False)):
            return
        self._press_on_cube = True
        point = self._event_screen_point(event)
        self._pressed_cube_index = self.cube_at_screen(*point) if point is not None else None
        self._press_at = point
        if (getattr(event, "num", 1) == 1 and self._pressed_cube_index is not None
                and self._pressed_cube_index in (self._bottom_left_cube_index(), 3)):
            self._cube_hold_started_at = time.perf_counter()
            self._cube_hold_after = self._gui.schedule(_CUBE_HOLD_MS, self._fire_cube_hold)

    def _bottom_left_cube_index(self) -> int | None:
        bases = getattr(self, "_bases", ())
        return min(range(len(bases)), key=lambda index: (-bases[index][1], bases[index][0])) if bases else None

    def _cancel_cube_hold(self) -> None:
        timer = getattr(self, "_cube_hold_after", None)
        self._cube_hold_after = None
        self._cube_hold_started_at = 0.0
        if timer is not None:
            self._gui.cancel(timer)

    def _cube_hold_progress(self, index: int, now: float) -> float:
        """Skin-accent ramp for the held cube; no separate animation clock."""
        if (getattr(self, "_cube_hold_after", None) is None
                or index != getattr(self, "_pressed_cube_index", None)
                or not _mouse_button_held()):
            return 0.0
        point = self._frame_pointer(now)
        start = getattr(self, "_press_at", None)
        if point is None or start is None or math.hypot(point[0] - start[0], point[1] - start[1]) > 12:
            return 0.0
        elapsed = max(0.0, now - float(getattr(self, "_cube_hold_started_at", now)))
        return min(1.0, elapsed / 0.25) * (0.35 + 0.65 * min(1.0, elapsed / 2.0))

    def _fire_cube_hold(self) -> None:
        self._cube_hold_after = None
        self._cube_hold_started_at = 0.0
        if (not getattr(self, "_press_on_cube", False)
                or bool(getattr(self, "_launcher_active", False))
                or bool(getattr(self, "_actuation_yield", False))
                or not _mouse_button_held()):
            return
        point = self._event_screen_point()
        start = getattr(self, "_press_at", None)
        if point is None or start is None or math.hypot(point[0] - start[0], point[1] - start[1]) > 12:
            return
        if self.cube_at_screen(*point) != getattr(self, "_pressed_cube_index", None):
            return
        callback = getattr(self, "_focus_hold" if self._pressed_cube_index == 3 else "_capture_hold", None)
        if callable(callback):
            self._press_on_cube = False
            self._pressed_cube_index = None
            callback()

    def _release_click(self, button: str, event: Any | None = None) -> None:
        self._cancel_cube_hold()
        if bool(getattr(self, "_launcher_active", False)):
            return
        if not getattr(self, "_press_on_cube", False):
            return
        self._press_on_cube = False
        pressed = getattr(self, "_pressed_cube_index", None)
        self._pressed_cube_index = None
        point = self._event_screen_point(event)
        released = self.cube_at_screen(*point) if point is not None else pressed
        if pressed is not None and released == pressed:
            callback = getattr(self, "_cube_click", None)
            if callable(callback):
                try:
                    if callback(int(pressed), str(button)):
                        return
                except Exception:
                    pass
        self._handle_click(button)

    def _reach_px(self) -> float:
        """Drag reaction radius: the cube's visible footprint, never its chase distance."""
        return max(1.0, float(getattr(self, "_size", 1) or 1) / 2.0)

    def accepts_drop(self) -> bool:
        """Accept only a release inside the visible cube footprint."""
        return float(getattr(self, "_drag_dist", 1e9)) <= self._size / 2.0

    def _hit_size(self) -> int:
        """The drop target never changes the cube's real window footprint."""
        return self._size

    def _poll_drag_arm(self) -> None:
        """Fill transparent cube gaps only while a held file is on the cube itself."""
        held = _mouse_button_held()
        if getattr(self, "_drag", False):
            if not held:
                self.drag_end()
            else:
                self._drag_armed = True
            return
        if not held:
            self._drag_armed = False
            return
        point = self._frame_pointer()
        if point is None:
            return
        px, py = point
        dist = math.hypot(float(px) - self._x, float(py) - self._y)
        reach = self._reach_px()
        limit = reach * _ARM_RELEASE_FACTOR if self._drag_armed else reach
        self._drag_armed = dist <= limit

    def set_drag(self, active: bool, nearness: float = 0.0) -> None:
        """A file is held over the cube. It wiggles while the drag is live, and opens its
        mouth — the cubes gape apart around the incoming file — by ``nearness`` (0 at the
        edge of the cube's window, 1 dead centre). ``set_drag(False)`` closes it again."""
        self._drag = bool(active)
        try:
            near = max(0.0, min(1.0, float(nearness)))
        except Exception:
            near = 0.0
        self._mouth_target = near if self._drag else 0.0
        if self._drag:
            self._hide_at = 0.0  # a drag only reaches a mapped window: just don't auto-hide mid-drag

    def drag_over(self, x_root: int, y_root: int) -> None:
        """Screen-coordinate drag position from the OS: open the mouth by how near the
        file is to the cube's centre. Windows only reports this while the drag is over
        the cube's window, so the ramp is the cube's own reach — not the whole screen."""
        reach = self._reach_px()
        dist = math.hypot(float(x_root) - self._x, float(y_root) - self._y)
        self._drag_dist = dist
        self._drag_leave_at = 0.0  # still here: cancel any leave awaiting confirmation
        self._drag_seen_at = time.perf_counter()
        self.set_drag(True, 1.0 - min(1.0, dist / reach))

    def drag_leave(self) -> None:
        """A DropLeave arrived. Confirm it after a grace rather than acting at once: the
        first one can fire on the frame between DropEnter and the drag-catch alpha being
        painted, and treating that as a real leave is what made the cube stutter."""
        if getattr(self, "_drag", False):
            self._drag_leave_at = time.perf_counter()
        else:
            self.drag_end()

    def drag_end(self) -> None:
        """The drag is definitively over (dropped, confirmed leave, or gone stale): close
        the mouth and drop the drag-catch alpha so the empty space is click-through again."""
        self._drag_leave_at = 0.0
        self._drag_seen_at = 0.0
        self._drag_armed = False
        self.set_drag(False)

    def _settle_drag(self, now: float) -> None:
        """Close a drag whose leave has outlived the grace, or whose events stopped arriving."""
        if not getattr(self, "_drag", False):
            return
        leave_at = float(getattr(self, "_drag_leave_at", 0.0) or 0.0)
        seen_at = float(getattr(self, "_drag_seen_at", 0.0) or 0.0)
        left = leave_at and (now - leave_at) >= _DRAG_LEAVE_GRACE_SECONDS
        stale = seen_at and (now - seen_at) >= _DRAG_STALE_SECONDS
        if left or stale:
            self.drag_end()

    def _handle_click(self, button: str) -> None:
        now = time.perf_counter()
        if button == "left" and (now - self._double_at) < 0.35:
            return  # tail release of a double-click — suppress the spurious single
        last_btn, last_at = self._last_click
        if button == last_btn and (now - last_at) < 0.12:
            return
        self._last_click = (button, now)
        cb = self._left_click if button == "left" else self._right_click
        if callable(cb):
            cb()

    def _handle_double(self) -> None:
        if bool(getattr(self, "_launcher_active", False)):
            return
        self._double_at = time.perf_counter()
        cb = self._left_double
        if callable(cb):
            cb()

    # --- volume (scroll) and brightness (Shift + scroll) ---
    def _on_scroll(self, delta: int, *, shift: bool = False, point: tuple[int, int] | None = None) -> None:
        """Scroll over the cube -> nudge Windows volume; Shift + scroll -> the brightness of
        the display under the cubes. The level shows as a small text label, only on scroll."""
        if bool(getattr(self, "_launcher_active", False)):
            return
        try:
            if shift:
                from mo_desktop import brightness
                from mo_desktop.focus_native import native_handle

                x, y = point if point and any(point) else self.center()
                text = brightness.nudge(x, y, 1 if delta > 0 else -1, anchor_hwnd=native_handle(self._win))
                if text:
                    self.show_bubble(text, seconds=1.4)
                return
            from mo_desktop import volume

            v = volume.nudge_volume(0.04 if delta > 0 else -0.04)
            if v is not None:
                self.show_bubble(_volume_text(v), seconds=1.4)
        except Exception:
            pass


def _volume_text(v: float) -> str:
    """Compact volume readout for the cube label (plain text — no special glyphs)."""
    return "volume  %d%%" % round(max(0.0, min(1.0, v)) * 100)
