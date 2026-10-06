"""Frame scheduling, movement, and trace behavior for the Desktop cube."""

from __future__ import annotations

from mo_desktop.gui_loop import screen_size

import math
import os
import time
from typing import Any

_HOME_ARRIVED_PX = 3.0
_COMPUTER_ACTIVITY_POLL_SECONDS = 0.75
_COMPUTER_ACTIVITY_ACTIVE_TTL_SECONDS = 15.0
_COMPUTER_ACTIVITY_FINISHED_TTL_SECONDS = 2.0
_CHASE_IDLE_DIM_AFTER = 15.0
_CHASE_IDLE_STEADY_PX = 2.0
_TRACE_SAMPLE_SECONDS = 0.09
_TRACE_MIN_DISTANCE = 26.0
_TRACE_FADE_SECONDS = 0.82
_TRACE_MAX_POINTS = 8
_NOMINAL_ACTIVE_FPS = 30.0
_ACTIVE_POINTER_GRACE_SECONDS = 0.35
_CURSOR_REACTION_RADIUS_RATIO = 0.90
_CURSOR_REACTION_SHIFT_RATIO = 0.18
_CURSOR_REACTION_BRIGHTNESS = 0.12
# Chase-mode sidestep (row 33): a short glide one cube-size across the pointer's path,
# at most once per approach; a pointer that follows the cubes there is aiming at them.
_DODGE_SECONDS = 0.2
_DODGE_REACH = 0.9
_DODGE_QUIET_SECONDS = 1.5
_DODGE_MIN_MOVE_PX = 3.0
_CURSOR_REACTION_EASE = 0.18


def paint_cube_trace(draw: Any, points: Any, *, now: float, origin: tuple[float, float],
                     offsets: Any, edge: float, color: Any, alpha: int = 110,
                     sizes: Any = None, corner: float = .22) -> None:
    """Paint the shared fading footsteps for a bounded set of cube positions."""
    for px, py, stamped_at in points:
        life = max(0.0, min(1.0, 1.0 - (now - stamped_at) / _TRACE_FADE_SECONDS))
        opacity = int(alpha * life ** 1.35)
        if opacity <= 0:
            continue
        for index, (ox, oy) in enumerate(offsets):
            size = max(5.0, (sizes[index] if sizes is not None else edge) * .62) * (.72 + .22 * life)
            cx, cy = px + ox - origin[0], py + oy - origin[1]
            draw.rounded_rectangle((cx - size / 2, cy - size / 2, cx + size / 2, cy + size / 2),
                                   radius=max(0, size * corner), fill=(*color, opacity))


def _time_scaled_ease(per_frame: float, elapsed: float) -> float:
    """Preserve a 30-FPS spring's wall-clock motion at any render cadence."""
    base = max(0.0, min(1.0, float(per_frame)))
    dt = max(0.0, min(0.25, float(elapsed)))
    if base <= 0.0 or dt <= 0.0:
        return 0.0
    if base >= 1.0:
        return 1.0
    return 1.0 - ((1.0 - base) ** (dt * _NOMINAL_ACTIVE_FPS))


def _ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * t * (t * (t * 6 - 15) + 10)


def _bound_terminal_computer_activity(
    snapshots: list[dict[str, Any]],
    instance_id: str,
    *,
    now: float | None = None,
) -> tuple[str, bool]:
    """Return the live cue for this cube's exact parent terminal instance."""
    current = time.time() if now is None else float(now)
    for snapshot in snapshots:
        if str(snapshot.get("instance_id") or "") != str(instance_id or ""):
            continue
        if str(snapshot.get("surface") or "") != "terminal":
            continue
        activity = snapshot.get("computer_activity")
        if not isinstance(activity, dict) or not activity:
            return "", False
        updated = float(activity.get("updated_at") or snapshot.get("created_at") or 0.0)
        active = bool(activity.get("active"))
        ttl = (
            _COMPUTER_ACTIVITY_ACTIVE_TTL_SECONDS
            if active
            else _COMPUTER_ACTIVITY_FINISHED_TTL_SECONDS
        )
        if updated <= 0.0 or current - updated > ttl:
            return "", False
        tool = str(activity.get("tool") or "")
        if tool == "computer_act":
            return "MO is using this computer…", active
        if tool == "point_on_screen":
            return "MO is pointing on your screen…", False
        if tool in {"computer_observe", "computer_targets"}:
            return "MO is looking at your screen…", False
        return "MO is using computer tools…", False
    return "", False


def _other_terminal_computer_use(snapshots: list[Any], instance_id: str, *,
                                 now: float | None = None) -> dict[str, Any] | None:
    """Any other local MO Terminal using this computer right now (one machine, one pointer)."""
    current = time.time() if now is None else float(now)
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or str(snapshot.get("instance_id") or "") == str(instance_id or ""):
            continue
        if str(snapshot.get("surface") or "") != "terminal":
            continue
        activity = snapshot.get("computer_activity")
        if (isinstance(activity, dict) and activity.get("active") and str(activity.get("tool") or "") == "computer_act"
                and current - float(activity.get("updated_at") or 0.0) <= _COMPUTER_ACTIVITY_ACTIVE_TTL_SECONDS):
            return snapshot
    return None


TERMINAL_WORKING_LABEL = "MO Terminal is using your computer · Esc stops it"


class CubeMotionMixin:
    """Frame and movement behavior mixed into the concrete Desktop cube."""

    def _expire_app_pulse(self, current: float) -> None:
        deadline = float(getattr(self, "_app_pulse_until", 0.0) or 0.0)
        if not deadline or current < deadline:
            return
        self._app_pulse_until = 0.0
        self._color_rgb = self._app_pulse_restore_rgb or self._color_rgb
        self._app_pulse_restore_rgb = None
        self._build_sprites()

    # ------------------------------------------------------------------ per-frame
    def _poll_bound_terminal_computer_activity(self, current: float) -> None:
        last = float(getattr(self, "_terminal_computer_poll_at", 0.0) or 0.0)
        if current - last < _COMPUTER_ACTIVITY_POLL_SECONDS:
            return
        self._terminal_computer_poll_at = current
        label = ""
        should_yield = False
        try:
            from core.runtime.instance import ENV_MO_INSTANCE_ID, recent_instance_snapshots

            instance_id = str(os.environ.get(ENV_MO_INSTANCE_ID) or "").strip()
            snapshots = recent_instance_snapshots(
                current_pid=os.getpid(),
                max_age_seconds=_COMPUTER_ACTIVITY_ACTIVE_TTL_SECONDS,
                limit=32,
            )
            acting = None
            if instance_id:
                label, should_yield = _bound_terminal_computer_activity(snapshots, instance_id)
                if should_yield:
                    acting = next((row for row in snapshots if isinstance(row, dict)
                                   and str(row.get("instance_id") or "") == instance_id), {})
            if not should_yield:
                acting = _other_terminal_computer_use(snapshots, instance_id)
                should_yield = acting is not None
            if should_yield:   # who to stop when Esc is pressed (row 45)
                self._working_terminal = {"instance_id": str(acting.get("instance_id") or instance_id),
                                          "pid": int(acting.get("pid") or 0), "cwd": str(acting.get("cwd") or "")}
        except Exception:
            label = ""
            should_yield = False

        yielded = self.actuation_yield_held_by("terminal")
        if should_yield and not yielded:
            self.set_terminal_working(True)
            self.hold_actuation_yield("terminal", True)
        elif not should_yield and yielded:
            self.set_terminal_working(False)
            self.hold_actuation_yield("terminal", False)
        elif should_yield:
            self.set_terminal_working(True)       # takes the corner once Desktop is idle
        if should_yield:
            self._show_terminal_working_label(current)
        else:
            if getattr(self, "_label_value", "") == TERMINAL_WORKING_LABEL:
                self._hide_label()
            if label:
                self.show_bubble(label, seconds=1.25)

    def _show_terminal_working_label(self, current: float) -> None:
        """Say what the working cubes mean and how to stop them, while Desktop itself is idle
        (Desktop's own labels and panels own the space otherwise)."""
        shown = getattr(self, "_heartbeat_shown", None)
        if not callable(shown) or not shown():
            return
        if getattr(self, "_label_value", "") == TERMINAL_WORKING_LABEL and getattr(self, "_label_kind", "") == "bubble":
            self._label_until = current + 2.0     # already up: keep it, no re-render
            return
        self.show_bubble(TERMINAL_WORKING_LABEL, seconds=2.0)

    def tick(self, now: float | None = None) -> None:
        current = time.perf_counter() if now is None else float(now)
        focus = getattr(self, "_focus_controller", None)
        if focus is not None:
            focus.tick(current)
        if bool(getattr(self, "_launcher_active", False)):
            self._expire_app_pulse(current)
            self._poll_notice(current)
            painter = getattr(self, "_launcher_painter", None)
            if callable(painter):
                self._sample_frame_pointer(current)
                self._launcher_interacting = bool(painter(current))
            return
        self._poll_bound_terminal_computer_activity(current)
        previous = float(getattr(self, "_last_tick_at", 0.0) or 0.0)
        self._frame_dt = (
            max(0.0, min(0.25, current - previous))
            if previous > 0.0 and current >= previous
            else 1.0 / _NOMINAL_ACTIVE_FPS
        )
        self._last_tick_at = current
        self._expire_app_pulse(current)
        terminal_only = (getattr(self, "_terminal_working", None) is not None
                         and not set(getattr(self, "_actuation_yield_holders", ()) or ()) - {"terminal"})
        if bool(getattr(self, "_actuation_yield", False)) and not (
                terminal_only and getattr(self, "_activity_cube_visible", False)):
            # Desktop's own acting holds the cubes still; for MO Terminal (another process) the
            # click-through, capture-excluded cubes keep moving: the glide to the working corner.
            if getattr(self, "_activity_cube_visible", False):
                self._show()
                self._render(current)
            elif self._visible:
                self._hide()
            return
        if getattr(self, "_app_launch_origin", None) is not None:
            self._poll_notice(current)
            return
        if (self._follow_enabled or self._resident) and not self._visible:
            self._show()
        if not self._visible:
            return
        self._sample_frame_pointer(current)
        self._poll_drag_arm()
        game_session_active = bool(getattr(self, "game_session_active", lambda: False)())
        if game_session_active:
            dock = getattr(self, "_game_session_dock", None)
            if dock is not None:
                self._x, self._y = dock
                self._from = self._to = dock
            self._glide_dur = 0.0
        else:
            self._advance_glide(current)
        if (
            game_session_active
            or self._held
            or bool(getattr(self, "_focus_mode", False))
            or self._listening
            or getattr(self, "_speaking", False)
            or self._thinking
            or self._drag
            or self._drag_armed
            or getattr(self, "_cube_hold_after", None) is not None
        ):
            pass  # Frozen while a bubble is docked, while LISTENING (mic hot), or
            #       while MO is working or receiving a drag. Explicit point_to glides still
            #       run above; ambient follow/wander does not move a live drop target.
        elif self._follow_enabled and not self._is_gliding() and current >= self._follow_pause_until:
            self._advance_follow(current)
        elif (
            self._resident
            and not self._follow_enabled
            and not self._is_gliding()
            and self._formation == "cluster"
            and current >= self._follow_pause_until
        ):
            self._advance_wander(current)
        if getattr(self, "_cube_hold_after", None) is None:
            self._update_cursor_reactions(current)
        if (
            self._hide_at
            and not self._listening
            and not getattr(self, "_speaking", False)
            and not self._is_gliding()
            and not self._resident
            and current >= self._hide_at
        ):
            if self._follow_enabled:
                self._hide_at = 0.0
                self._hide_label()
            else:
                self._hide()
                return
        self._poll_notice(current)
        if self._label_until and current >= self._label_until:
            if not (getattr(self, "_label_variant", "glance") == "guidance" and self._replay_pending_glance()):
                self._hide_label()
        if self._emote is not None and (current - self._emote[1]) >= self._emote[2]:
            fn, t0, dur = self._emote
            # A looping emote (recharging) rebases instead of clearing: clearing it left ONE bare
            # frame before the next cycle was played, and the idle bob snapped back for that frame
            # — the flick at the end of the colour sweep. Rebasing by exactly `dur` also keeps the
            # heartbeat and the LED's ring position continuous across the seam. The loop stays
            # alive while either charging driver holds: the dock, or a verified phone presence
            # that has not aged out — expiry ends it at a cycle boundary, seam-free.
            keep_loop = self._emote_loops(fn) and (
                self._charging
                or current < getattr(self, "_phone_charging_until", 0.0)
            )
            self._emote = (fn, t0 + dur, dur) if keep_loop else None
        if (
            self._emote is None
            and not self._thinking
            and current < getattr(self, "_phone_charging_until", 0.0)
        ):
            # A verified phone charge is current but another emote/turn displaced the
            # loop: resume it, mirroring the dock's own replay in _advance_home.
            self.play_emote("charging")
        focus = getattr(self, "_focus_controller", None)
        if focus is not None:
            focus.follow_cube_position(current)
        self._update_trace(current)
        self._reposition()
        self._render(current)

    def needs_active_frames(self, now: float | None = None) -> bool:
        """Return whether the next cube frame needs the full interaction cadence.

        Passive breathing, recharge, and free-mode drift deliberately use the
        low-cost cadence. Direct interaction and short transitions use the
        ~60 Hz active path. Movement springs are wall-clock scaled, so moving
        between those cadences does not change chase or wander speed.
        """
        current = time.perf_counter() if now is None else float(now)
        focus = getattr(self, "_focus_controller", None)
        if focus is not None and focus.needs_active_frames(current):
            return True
        if bool(getattr(self, "_launcher_active", False)):
            return bool(getattr(self, "_launcher_interacting", False))
        if bool(getattr(self, "_visible", False)) and self._heartbeat_shown():
            return True       # MO Terminal acts in another process; the heartbeat stays smooth
        if not bool(getattr(self, "_visible", False)) or bool(getattr(self, "_actuation_yield", False)):
            return False
        if any(
            bool(getattr(self, name, False))
            for name in (
                "_listening",
                "_speaking",
                "_thinking",
                "_drag",
                "_drag_armed",
            )
        ):
            return True
        if getattr(self, "_cube_hold_after", None) is not None:
            return True
        glide_dur = float(getattr(self, "_glide_dur", 0.0) or 0.0)
        glide_start = float(getattr(self, "_glide_start", 0.0) or 0.0)
        if glide_dur > 0.0 and (current - glide_start) < glide_dur:
            return True
        mouth = float(getattr(self, "_mouth", 0.0) or 0.0)
        mouth_target = float(getattr(self, "_mouth_target", 0.0) or 0.0)
        fade = float(getattr(self, "_fade", 0.0) or 0.0)
        fade_target = float(getattr(self, "_fade_target", 0.0) or 0.0)
        if abs(mouth - mouth_target) >= 0.002 or abs(fade - fade_target) >= 0.002:
            return True
        emote = getattr(self, "_emote", None)
        if emote is not None and not self._emote_loops(emote[0]):
            return True
        if (bool(getattr(self, "_follow_enabled", False)) and not bool(getattr(self, "_held", False))
                and not bool(getattr(self, "_focus_mode", False))):
            since = getattr(self, "_cursor_idle_since", None)
            if since is None or (current - float(since)) < _ACTIVE_POINTER_GRACE_SECONDS:
                return True
            try:
                point = self._frame_pointer(current)
                if point is None:
                    raise ValueError("pointer unavailable")
                px, py = point
                distance = math.hypot(float(px) - float(self._x), float(py) - float(self._y))
                if distance > float(getattr(self, "_follow_distance", 64.0) or 64.0) + 1.0:
                    return True
            except Exception:
                pass
        return any(
            abs(dx) > 0.05 or abs(dy) > 0.05 or level > 0.01
            for dx, dy, level in (getattr(self, "_cursor_reactions", []) or [])
        )

    # ------------------------------------------------------------------ internals
    def _is_gliding(self) -> bool:
        return self._glide_dur > 0.0 and (time.perf_counter() - self._glide_start) < self._glide_dur

    def _advance_wander(self, now: float) -> None:
        """Gentle free-mode drift: ease toward a slowly-changing nearby point so the
        companion feels alive in FREE mode without darting around."""
        if now >= self._wander_until:
            import random

            try:
                sw = int(screen_size()[0])
                sh = int(screen_size()[1])
            except Exception:
                return
            margin = self._size
            self._wander_to = (
                min(max(margin, self._x + random.uniform(-90, 90)), sw - margin),
                min(max(margin, self._y + random.uniform(-70, 70)), sh - margin),
            )
            self._wander_until = now + random.uniform(4.0, 8.0)
        tx, ty = self._wander_to
        ease = _time_scaled_ease(0.012, getattr(self, "_frame_dt", 1.0 / _NOMINAL_ACTIVE_FPS))
        self._x += (tx - self._x) * ease
        self._y += (ty - self._y) * ease
        self._from = self._to = (self._x, self._y)

    def _advance_glide(self, now: float) -> None:
        if self._glide_dur <= 0.0:
            return
        t = (now - self._glide_start) / self._glide_dur
        if t >= 1.0:
            self._x, self._y = self._to
            self._glide_dur = 0.0
            return
        e = _ease_out(t)
        fx, fy = self._from
        tx, ty = self._to
        self._x = fx + (tx - fx) * e
        self._y = fy + (ty - fy) * e

    def _start_trace(self, now: float) -> None:
        self._trace_points = [(float(self._x), float(self._y), float(now))]
        self._trace_last_at = float(now)

    def _clear_trace(self) -> None:
        self._trace_points = []
        self._trace_last_at = 0.0
        self._summon_trace_until = 0.0
        try:
            self._trace_win.hide()
        except Exception:
            pass

    def _maybe_sample_trace(self, now: float) -> None:
        gliding = float(getattr(self, "_glide_dur", 0.0) or 0.0) > 0.0
        summoning = float(now) < float(getattr(self, "_summon_trace_until", 0.0) or 0.0)
        if not (gliding or summoning):
            return  # normal follow/wander leaves no footprints — only glides + Ctrl-Ctrl summons trace
        points = list(getattr(self, "_trace_points", []) or [])
        if not points:
            self._start_trace(now)
            return
        last_x, last_y, _last_t = points[-1]
        moved = math.hypot(float(self._x) - last_x, float(self._y) - last_y)
        if moved < 2.0:
            return  # never stack footprints while parked/caught (the summon window can outlast the chase)
        if (
            float(now) - float(getattr(self, "_trace_last_at", 0.0) or 0.0)
        ) < _TRACE_SAMPLE_SECONDS and moved < _TRACE_MIN_DISTANCE:
            return
        points.append((float(self._x), float(self._y), float(now)))
        del points[:-_TRACE_MAX_POINTS]
        self._trace_points = points
        self._trace_last_at = float(now)

    def _active_trace_points(self, now: float) -> list[tuple[float, float, float]]:
        fade = max(0.1, _TRACE_FADE_SECONDS)
        active = [
            (float(x), float(y), float(t))
            for x, y, t in (getattr(self, "_trace_points", []) or [])
            if (float(now) - float(t)) <= fade
        ]
        self._trace_points = active
        return active

    def _update_trace(self, now: float) -> None:
        points = getattr(self, "_trace_points", None)
        summoning = float(now) < float(getattr(self, "_summon_trace_until", 0.0) or 0.0)
        if not points and not self._is_gliding() and not summoning:
            return
        self._maybe_sample_trace(now)
        self._paint_trace(now)

    def _advance_follow(self, now: float) -> None:
        """Smoothly TRAIL the cursor: ease toward a point ``follow_distance`` behind the
        cursor along the cube→cursor line, so the cube lags naturally on whatever side
        it is already on — no corner parking, no freeze. ``follow_ease`` is the spring
        (lower = more delay); both are settings.

        When the cursor is inside MO's terminal the cube goes home instead — it belongs there."""
        home = getattr(self, "_home", None)
        if home is not None:
            self._advance_home(home)
            return
        try:
            point = self._frame_pointer(now)
            if point is None:
                return
            px, py = point
            sw = int(screen_size()[0])
            sh = int(screen_size()[1])
        except Exception:
            return
        px, py = float(px), float(py)
        dx, dy = self._x - px, self._y - py  # cursor -> cube
        dist = (dx * dx + dy * dy) ** 0.5
        self._mark_pointer_activity(px, py, now)
        # Catchable: STOP once the cursor is within the catch zone, so you can move the
        # pointer over the cubes and CLICK them — they don't flee. They ease to a stop as
        # the cursor closes in, and only resume trailing when it moves back out past the
        # zone. This is what makes lock-mode clickable.
        catch = self._follow_distance
        previous = getattr(self, "_dodge_last_ptr", None)
        self._dodge_last_ptr = (px, py)
        if dist <= catch:
            self._maybe_step_aside(px, py, previous, now, sw, sh)
            return
        ux, uy = dx / dist, dy / dist
        margin = self._size * 0.5 + 8.0
        tx = min(max(margin, px + ux * self._follow_distance), sw - margin)
        ty = min(max(margin, py + uy * self._follow_distance), sh - margin)
        ease = self._follow_ease if not self._listening else min(0.5, self._follow_ease + 0.06)
        # ease to a stop near the catch zone (slow down, then stop) — not a hard halt
        ease *= 0.15 + 0.85 * min(1.0, (dist - catch) / max(1.0, catch))
        ease = _time_scaled_ease(ease, getattr(self, "_frame_dt", 1.0 / _NOMINAL_ACTIVE_FPS))
        self._x += (tx - self._x) * ease
        self._y += (ty - self._y) * ease
        self._from = self._to = (self._x, self._y)

    def _maybe_step_aside(self, px: float, py: float, previous: tuple[float, float] | None,
                          now: float, sw: int, sh: int) -> bool:
        """Chase only: step aside once when the pointer comes at the cubes over TEXT (the
        system cursor is the text I-beam), with no button held: it is heading for the text
        under them, not for MO. An arrow-cursor approach still catches them (aiming at MO); a
        held button (a file dragged to MO, a selection under way) never moves them; and the
        cubes catching up to a resting pointer never count. Following them there within the
        quiet time keeps them put."""
        if not getattr(self, "_follow_enabled", False) or previous is None:
            return False
        if now < float(getattr(self, "_dodge_quiet_until", 0.0) or 0.0):
            return False
        if getattr(self, "_drag", False) or getattr(self, "_drag_armed", False):
            return False
        mx, my = px - previous[0], py - previous[1]
        moved = math.hypot(mx, my)
        if moved < _DODGE_MIN_MOVE_PX or mx * (self._x - px) + my * (self._y - py) <= 0:
            return False   # resting, or moving away: only a pointer coming at the cubes counts
        if not self._pointer_over_text():
            return False
        try:
            from core.desktop.win32 import mouse_button_held
            if mouse_button_held():
                return False
        except Exception:
            return False
        nx, ny = -my / moved, mx / moved                 # across the pointer's path
        if (self._x - px) * nx + (self._y - py) * ny < 0:
            nx, ny = -nx, -ny                            # the side away from the pointer
        reach = self._size * _DODGE_REACH
        margin = self._size * 0.5 + 8.0
        target = (min(max(margin, self._x + nx * reach), sw - margin),
                  min(max(margin, self._y + ny * reach), sh - margin))
        self._from = (self._x, self._y)
        self._to = target
        self._glide_start = now
        self._glide_dur = _DODGE_SECONDS
        self._follow_pause_until = now + _DODGE_SECONDS
        self._dodge_quiet_until = now + _DODGE_QUIET_SECONDS
        return True

    @staticmethod
    def _pointer_over_text() -> bool:
        """Whether the system cursor is the text I-beam (the pointer is over selectable text)."""
        try:
            import ctypes
            from ctypes import wintypes

            class CURSORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                            ("hCursor", wintypes.HANDLE), ("ptScreenPos", wintypes.POINT)]

            user32 = ctypes.windll.user32
            user32.LoadCursorW.restype = wintypes.HANDLE
            info = CURSORINFO()
            info.cbSize = ctypes.sizeof(info)
            if not user32.GetCursorInfo(ctypes.byref(info)) or not info.hCursor:
                return False
            return int(info.hCursor) == int(user32.LoadCursorW(None, 32513) or 0)   # IDC_IBEAM
        except Exception:
            return False

    def _advance_home(self, home: tuple[float, float]) -> None:
        """Ease to the dock above the terminal's gauge and settle there, recharging.

        Recharging is the ``charging`` emote replayed for as long as the cube stays docked —
        the animation lives in the emote library with every other one, not in the state chain."""
        tx, ty = home
        dx, dy = tx - self._x, ty - self._y
        dist = math.hypot(dx, dy)
        self._charging = dist <= _HOME_ARRIVED_PX
        # The thinking spinner brightens one cube in turn; the recharge loop dims the others and
        # paints one amber. Layered, they fight on the same four cubes. Working wins.
        if self._charging and self._emote is None and not self._thinking:
            self.play_emote("charging")
        if dist > 0.5:
            ease = _time_scaled_ease(
                self._follow_ease,
                getattr(self, "_frame_dt", 1.0 / _NOMINAL_ACTIVE_FPS),
            )
            self._x += dx * ease
            self._y += dy * ease
            self._from = self._to = (self._x, self._y)

    def _mark_pointer_activity(self, px: float, py: float, now: float) -> None:
        last = getattr(self, "_last_ptr", None)
        if last is None:
            self._cursor_idle_since = now
        else:
            moved = math.hypot(float(px) - float(last[0]), float(py) - float(last[1]))
            if moved > _CHASE_IDLE_STEADY_PX:
                self._cursor_idle_since = now
        self._last_ptr = (float(px), float(py))

    def _update_cursor_reactions(self, now: float) -> None:
        """Ease every visual cube independently toward a nearby cursor.

        This is an internal formation reaction, not another chase mode: the
        character window keeps its established position while each constituent
        cube leans and brightens according to its own distance.
        """
        bases = list(getattr(self, "_bases", []) or [])
        if not bases:
            self._cursor_reactions = []
            self._active_cube_index = None
            return
        count = len(bases)
        reactions = list(getattr(self, "_cursor_reactions", []) or [])
        reactions = (reactions + [(0.0, 0.0, 0.0)] * count)[:count]
        pointer = self._frame_pointer(now)
        radius = max(
            float(getattr(self, "_size", 84) or 84) * _CURSOR_REACTION_RADIUS_RATIO,
            float(getattr(self, "_cube_edge", 18) or 18) * 2.5,
        )
        shift = float(getattr(self, "_cube_edge", 18) or 18) * _CURSOR_REACTION_SHIFT_RATIO
        ease = _time_scaled_ease(
            _CURSOR_REACTION_EASE,
            getattr(self, "_frame_dt", 1.0 / _NOMINAL_ACTIVE_FPS),
        )
        # A docked panel holds the cubes still; their lean eases back to rest.
        suppress = (bool(getattr(self, "_drag", False)) or self._emote_holds_still()
                    or bool(getattr(self, "_held", False)))
        next_values: list[tuple[float, float, float]] = []
        nearest: tuple[float, int] | None = None
        offset = float(getattr(self, "_size", 84) or 84) / 2.0
        for index, (_bx, _by) in enumerate(bases):
            target_x = target_y = target_level = 0.0
            if pointer is not None and not suppress:
                # Measure from the current pre-reaction render position, not the
                # static formation slot. Bobbing and emotes can move a cube far
                # enough that static proximity disagrees with what the operator
                # is actually pointing at.
                cx, cy, _bright, _alpha = self._cube_state(index, now, include_cursor=False)
                screen_x = float(self._x) + float(cx) - offset
                screen_y = float(self._y) + float(cy) - offset
                dx, dy = pointer[0] - screen_x, pointer[1] - screen_y
                distance = math.hypot(dx, dy)
                if nearest is None or distance < nearest[0]:
                    nearest = (distance, index)
                raw = max(0.0, min(1.0, 1.0 - distance / radius))
                target_level = raw * raw * (3.0 - 2.0 * raw)
                if distance > 0.001:
                    target_x = (dx / distance) * shift * target_level
                    target_y = (dy / distance) * shift * target_level
            old_x, old_y, old_level = reactions[index]
            new_x = target_x if abs(target_x - old_x) < 0.01 else old_x + (target_x - old_x) * ease
            new_y = target_y if abs(target_y - old_y) < 0.01 else old_y + (target_y - old_y) * ease
            new_level = (
                target_level if abs(target_level - old_level) < 0.002 else old_level + (target_level - old_level) * ease
            )
            next_values.append((new_x, new_y, new_level))
        self._cursor_reactions = next_values
        hit_radius = float(getattr(self, "_cube_edge", 18) or 18) * 0.75
        self._active_cube_index = nearest[1] if nearest is not None and nearest[0] <= hit_radius else None

    def _chase_idle_dimmed(self, now: float) -> bool:
        if not getattr(self, "_follow_enabled", False):
            return False
        if (
            getattr(self, "_held", False)
            or getattr(self, "_listening", False)
            or getattr(self, "_speaking", False)
            or getattr(self, "_thinking", False)
        ):
            return False
        if float(getattr(self, "_glide_dur", 0.0) or 0.0) > 0.0 and (
            time.perf_counter() - float(getattr(self, "_glide_start", 0.0) or 0.0)
        ) < float(getattr(self, "_glide_dur", 0.0) or 0.0):
            return False
        since = getattr(self, "_cursor_idle_since", None)
        if since is None:
            return False
        after = max(0.0, float(getattr(self, "_idle_dim_after", _CHASE_IDLE_DIM_AFTER) or 0.0))
        return (now - float(since)) >= after

    def _reposition(self) -> None:
        if bool(getattr(self, "_launcher_active", False)):
            return
        # Use the HIT size, not the drawn size: the layered blit sizes the window to the frame it
        # paints, and forcing it back to the cube's size here would collapse the armed reach every
        # frame — the hit area would exist for exactly one tick.
        w = self._hit_size()
        left = int(round(self._x - w / 2.0))
        top = int(round(self._y - w / 2.0))
        geo = (left, top, w, w)
        if geo != self._last_geometry:
            self._last_geometry = geo
            try:
                self._win.position(*geo)
            except Exception:
                pass
        self._reposition_label()
