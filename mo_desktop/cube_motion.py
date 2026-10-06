"""Frame scheduling, movement, and trace behavior for the Desktop cube."""

from __future__ import annotations

from mo_desktop.gui_loop import screen_size

import math
import os
from functools import lru_cache
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


def paint_cube_trace(frame: Any, points: Any, *, now: float, origin: tuple[float, float],
                     offsets: Any, edge: float, color: Any, alpha: int = 110,
                     sizes: Any = None, corner: float = .22) -> None:
    """Paint the shared fading footsteps for a bounded set of cube positions onto ``frame``.

    Every footstep is an antialiased shape; overlapping ones keep the stronger of the two (a
    plain 1x rounded rectangle left stair-stepped, hard edges), and the colour goes on once."""
    from PIL import Image, ImageChops

    steps = []
    for px, py, stamped_at in points:
        life = max(0.0, min(1.0, 1.0 - (now - stamped_at) / _TRACE_FADE_SECONDS))
        opacity = int(alpha * life ** 1.35)
        if opacity <= 0:
            continue
        for index, (ox, oy) in enumerate(offsets):
            size = max(5.0, (sizes[index] if sizes is not None else edge) * .62) * (.72 + .22 * life)
            mask = _footstep(max(1, round(size)), round(float(corner) * 100), opacity // 6 * 6)
            cx, cy = px + ox - origin[0], py + oy - origin[1]
            steps.append((mask, round(cx - mask.width / 2), round(cy - mask.height / 2)))
    if not steps:
        return
    margin = max(mask.width for mask, _x, _y in steps)
    width, height = frame.size
    combined = Image.new("L", (width + 2 * margin, height + 2 * margin), 0)
    for mask, x, y in steps:
        box = (x + margin, y + margin, x + margin + mask.width, y + margin + mask.height)
        if box[2] <= 0 or box[3] <= 0 or box[0] >= combined.width or box[1] >= combined.height:
            continue
        combined.paste(ImageChops.lighter(combined.crop(box), mask), box[:2])
    layer = Image.new("RGBA", (width, height), (*tuple(int(v) for v in tuple(color)[:3]), 0))
    layer.putalpha(combined.crop((margin, margin, margin + width, margin + height)))
    frame.alpha_composite(layer)


@lru_cache(maxsize=384)
def _footstep(size: int, corner_percent: int, opacity: int) -> Any:
    """One footstep's coverage: the shape drawn as a 4x mask and box-filtered down (smooth edges,
    no colour fringe), at ``opacity``."""
    from PIL import Image, ImageDraw

    ss, side = 4, size + 2
    corner = corner_percent / 100
    mask = Image.new("L", (side * ss, side * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle((ss, ss, (side - 1) * ss - 1, (side - 1) * ss - 1),
                                           radius=max(0, size * corner) * ss, fill=opacity)
    return mask.resize((side, side), Image.Resampling.BOX)


def paint_ink_line(frame: Any, points: Any, *, now: float, origin: tuple[float, float], color: Any,
                   fade: float, width: float = 2.2) -> None:
    """Antialiased ink along the pen's path onto ``frame``: a 4x coverage mask, box-filtered down,
    each segment fading with its age (row 28, the writer character)."""
    from PIL import Image, ImageDraw

    ss = 4
    mask = Image.new("L", (frame.width * ss, frame.height * ss), 0)
    draw = ImageDraw.Draw(mask)
    ox, oy = origin
    for (xa, ya, _ta), (xb, yb, tb) in zip(points, points[1:]):
        life = max(0.0, 1.0 - (now - tb) / max(0.1, fade))
        if life > 0:
            draw.line(((xa - ox) * ss, (ya - oy) * ss, (xb - ox) * ss, (yb - oy) * ss),
                      fill=int(235 * life ** 1.4), width=max(1, round(width * ss)))
    layer = Image.new("RGBA", frame.size, (*tuple(int(v) for v in tuple(color)[:3]), 0))
    layer.putalpha(mask.resize(frame.size, Image.Resampling.BOX))
    frame.alpha_composite(layer)


def _filament(start: tuple[float, float], end: tuple[float, float], energy: float, seconds: float,
              scale: float) -> list[tuple[float, float]]:
    """MO Shell's electric filament (ShellGroupSurface.ElectricFilament): a wire that ripples
    more as its energy rises."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = max(.01, math.hypot(dx, dy))
    amplitude = min(length * .08, 2 * scale)
    points = []
    for i in range(25):
        t = i / 24
        wave = math.sin(t * math.pi * 2) * .5 + energy * math.sin(t * math.pi) * (
            math.sin(t * math.pi * 6 - (seconds * 18 % (math.pi * 2)))
            + .3 * math.sin(t * math.pi * 14 + (seconds * 29 % (math.pi * 2))))
        bend = wave * amplitude
        points.append((start[0] + dx * t - dy / length * bend, start[1] + dy * t + dx / length * bend))
    points[0], points[-1] = start, end
    return points


def _touch_point(center: tuple[float, float], rect: tuple[float, float, float, float]) -> tuple[float, float]:
    """Where the line from ``center`` toward the panel's middle meets the panel's border: the wire
    ends exactly on the panel's stroke."""
    cx, cy = center
    x0, y0, x1, y1 = rect
    dx, dy = (x0 + x1) / 2 - cx, (y0 + y1) / 2 - cy
    hits = []
    for edge_x in (x0, x1):
        if dx:
            t = (edge_x - cx) / dx
            if 0 <= t <= 1 and y0 - 0.5 <= cy + t * dy <= y1 + 0.5:
                hits.append(t)
    for edge_y in (y0, y1):
        if dy:
            t = (edge_y - cy) / dy
            if 0 <= t <= 1 and x0 - 0.5 <= cx + t * dx <= x1 + 0.5:
                hits.append(t)
    t = min(hits) if hits else 1.0
    return cx + t * dx, cy + t * dy


def _perimeter_point(rect: tuple[float, float, float, float], s: float) -> tuple[float, float]:
    """The point ``s`` px along the rectangle's border, clockwise from its top-left corner."""
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    s %= 2 * (w + h)
    if s < w:
        return x0 + s, y0
    s -= w
    if s < h:
        return x1, y0 + s
    s -= h
    if s < w:
        return x1 - s, y1
    return x0, y1 - (s - w)


def _perimeter_offset(rect: tuple[float, float, float, float], point: tuple[float, float]) -> float:
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    px, py = point
    sides = [(abs(py - y0), px - x0), (abs(px - x1), w + (py - y0)), (abs(py - y1), w + h + (x1 - px)),
             (abs(px - x0), 2 * w + h + (y1 - py))]
    return min(sides)[1]


@lru_cache(maxsize=16)
def _frame_glow(width: int, height: int, radius: int, color: tuple[int, int, int]) -> Any:
    """The lit frame of the panel in use: a soft ring just outside its edge (cached per size)."""
    from PIL import Image, ImageDraw, ImageFilter

    from PIL import ImageChops

    pad = 16
    glow = Image.new("RGBA", (width + 2 * pad, height + 2 * pad), (0, 0, 0, 0))
    ImageDraw.Draw(glow).rounded_rectangle((pad - 3, pad - 3, pad + width + 2, pad + height + 2), radius=radius + 3,
                                           outline=(*color, 255), width=6)
    glow = glow.filter(ImageFilter.GaussianBlur(4))
    ImageDraw.Draw(glow).rounded_rectangle((pad - 1, pad - 1, pad + width, pad + height), radius=radius + 1,
                                           outline=(*color, 235), width=1)  # a crisp lit edge
    inner = Image.new("L", glow.size, 255)
    ImageDraw.Draw(inner).rounded_rectangle((pad + 1, pad + 1, pad + width - 2, pad + height - 2), radius=radius, fill=0)
    glow.putalpha(ImageChops.multiply(glow.getchannel("A"), inner))  # never over the panel itself
    return glow


@lru_cache(maxsize=8)
def _wiring_base(cx: int, cy: int, rects: tuple, focused: str, rgb: tuple[int, int, int], core: bool) -> tuple[Any, int, int]:
    """The still part of a wiring frame: its bounds, the lit frame of the face in use, the core."""
    from PIL import Image

    focus_rect = next((rect for name, rect in rects if name == focused), None)
    xs = [cx - 30, cx + 30] + ([focus_rect[0] - 18, focus_rect[2] + 18] if focus_rect else [])
    ys = [cy - 30, cy + 30] + ([focus_rect[1] - 18, focus_rect[3] + 18] if focus_rect else [])
    for _name, (x0, y0, x1, y1) in rects:
        xs.append(min(max(cx, x0), x1))
        ys.append(min(max(cy, y0), y1))
    left, top = math.floor(min(xs)), math.floor(min(ys))
    width, height = max(1, math.ceil(max(xs)) - left), max(1, math.ceil(max(ys)) - top)
    base = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    if focus_rect is not None:
        fw, fh = focus_rect[2] - focus_rect[0], focus_rect[3] - focus_rect[1]
        base.alpha_composite(_frame_glow(fw, fh, 12, rgb), (focus_rect[0] - 16 - left, focus_rect[1] - 16 - top))
    if core:
        base.alpha_composite(_core_halo(rgb), (cx - 32 - left, cy - 32 - top))
    return base, left, top


@lru_cache(maxsize=48)
def _frame_glow_scaled(width: int, height: int, color: tuple[int, int, int], tenths: int) -> Any:
    """The lit frame at ``tenths``/10 strength, for the flare when the charge arrives."""
    glow = _frame_glow(width, height, 12, color)
    if tenths >= 10:
        return glow
    scaled = glow.copy()
    scaled.putalpha(glow.getchannel("A").point(lambda v, k=tenths / 10: int(v * k)))
    return scaled


@lru_cache(maxsize=8)
def _core_halo(color: tuple[int, int, int]) -> Any:
    from PIL import Image, ImageDraw, ImageFilter

    halo = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse((16, 16, 48, 48), fill=(*color, 120))
    return halo.filter(ImageFilter.GaussianBlur(8))


def paint_dock_wiring(center: tuple[float, float], faces: Any, focused: str, charge: float | None, *, now: float,
                      color: Any, core: bool, sprite: Any = None, sweep: float | None = None) -> tuple[Any, int, int]:
    """One frame of the connected companion (row 42) in screen space: returns (image, left, top).

    Wires run from ``center`` to the corner of each face nearest it (three strokes like MO Shell's
    DrawElectricConnection); the focused face's wire carries ``charge`` (0..1 along it) and its
    frame glows; when ``core`` is set, MO's mark sits at the centre. Each wire ends on its panel's
    stroke, and once the charge arrives (``sweep`` 0..1) it runs on around that panel's frame from
    the contact point both ways while the frame flares, so wire and panel read as one current.
    The glow and core are cached per layout; each pulse frame redraws only what moves."""
    from PIL import Image, ImageDraw

    rgb = tuple(int(v) for v in tuple(color)[:3])
    cx, cy = center
    rects = tuple((name, (cx + dx, cy + dy, cx + dx + w, cy + dy + h)) for name, (dx, dy, w, h) in faces)
    base, left, top = _wiring_base(round(cx), round(cy), tuple((n, tuple(round(v) for v in r)) for n, r in rects),
                                   focused, rgb, core)
    frame = base.copy()
    ends = [(name, _touch_point((cx, cy), rect)) for name, rect in rects]
    reach = max([abs(ex - cx) for _n, (ex, _ey) in ends] + [abs(ey - cy) for _n, (_ex, ey) in ends] + [12.0]) + 8
    wl, wt = math.floor(cx - reach), math.floor(cy - reach)
    ws = max(1, math.ceil(2 * reach))
    ss = 3
    wires = Image.new("RGBA", (ws * ss, ws * ss), (0, 0, 0, 0))   # only the hub's neighbourhood
    draw = ImageDraw.Draw(wires)
    for name, end in ends:                                          # each wire ends on its panel's stroke
        energy = 1.0 if name == focused else 0.25
        points = [((x - wl) * ss, (y - wt) * ss) for x, y in _filament((cx, cy), end, energy, now, 5.0)]
        for stroke, alpha in ((6, 30 + 50 * energy), (3.5, 55 + 90 * energy), (1.6, 190 + 65 * energy)):
            draw.line(points, fill=(*rgb, int(alpha)), width=max(1, round(stroke * ss)),
                      joint="curve" if stroke < 2 else None)   # rounded joints only on the crisp core
        if name == focused and charge is not None:
            light = tuple(int(c * .3 + 255 * .7) for c in rgb)
            segments = len(points) - 1
            for k in range(segments):
                strength = max(0.0, 1 - abs(k / segments - charge) / .16)
                if strength > 0:
                    draw.line((points[k], points[k + 1]), fill=(*light, int(240 * strength)), width=round(2.6 * ss))
    frame.alpha_composite(wires.resize((ws, ws), Image.Resampling.LANCZOS), (wl - left, wt - top))
    focus_rect = next((rect for name, rect in rects if name == focused), None)
    if sweep is not None and focus_rect is not None:
        # The charge continues into the panel: a light runs from the contact point around the
        # frame both ways, fading, while the whole lit frame flares and settles.
        touch = next(end for name, end in ends if name == focused)
        x0, y0, x1, y1 = focus_rect
        ring = (x0 - 1, y0 - 1, x1 + 1, y1 + 1)
        start = _perimeter_offset(ring, touch)
        half_way = (ring[2] - ring[0]) + (ring[3] - ring[1])
        flare = (1.0 - sweep) ** 1.5
        frame.alpha_composite(_frame_glow_scaled(round(x1 - x0), round(y1 - y0), rgb, round(flare * 10)),
                              (round(x0) - 16 - left, round(y0) - 16 - top))
        light = tuple(int(c * .3 + 255 * .7) for c in rgb)
        lead = sweep * half_way
        sweep_draw = ImageDraw.Draw(frame)
        for direction in (1, -1):
            for k in range(14):
                s0 = start + direction * (lead - k * 4)
                s1 = start + direction * (lead - (k + 1) * 4)
                if direction * (s1 - start) < 0:
                    break
                a, b = _perimeter_point(ring, s0), _perimeter_point(ring, s1)
                sweep_draw.line(((a[0] - left, a[1] - top), (b[0] - left, b[1] - top)),
                                fill=(*light, int(235 * (1 - k / 14) * (1 - sweep * 0.6))), width=2)
    if core and sprite is not None:
        mark = sprite.resize((9, 9), Image.Resampling.LANCZOS)
        for ox, oy in ((-10, -10), (1, -10), (-10, 1), (1, 1)):
            frame.alpha_composite(mark, (round(cx) + ox - left, round(cy) + oy - top))
    return frame, left, top


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
        if getattr(self, "_role_motion", "") == "writer" and (self._thinking or getattr(self, "_ink_points", None)):
            self._tick_ink(now)             # the writer's ink owns the trace layer while it writes
            return
        points = getattr(self, "_trace_points", None)
        summoning = float(now) < float(getattr(self, "_summon_trace_until", 0.0) or 0.0)
        if not points and not self._is_gliding() and not summoning:
            self._tick_wiring(now)          # docked panels: the connected companion's wiring
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
