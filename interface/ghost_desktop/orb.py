"""The desktop Ghost moon orb — MO's own on-screen pointer.

A small, borderless, always-on-top, click-through window holding an animated moon.
It is *MO's* cursor: when MO points (``point_on_screen``) or acts in Do mode, the
moon glides to the target and pulses — the clicky-style flying pointer — instead
of MO silently jerking the bare Windows cursor. While voice is listening it shows
a soft breathing ring so the operator can see Ghost is hearing them.

Windows note: a full-screen colour-keyed overlay renders black against the DWM
compositor (see ``interface/screen_overlay.py``). So this is deliberately a tiny
follower window using Tk ``-transparentcolor`` — only the moon's pixels paint and
everything else is transparent *and* click-through. Lives on the companion's Tk
root/thread; all public methods are GUI-thread only (the companion marshals calls
onto its event queue), except ``tick`` which the companion calls each frame.
"""
from __future__ import annotations

import time
from typing import Any

# A colour that will not appear in the moon art; keyed fully transparent + click-through.
_CHROMA = "#ff00ff"
_MOON = "#00cccc"        # cyan half-moon = MO mark
_MOON_DARK = "#04141a"   # the shadowed limb of the crescent
_GLOW = "#3fe0e0"
_LISTEN = "#bb86fc"      # purple pulse while hearing voice

_SIZE = 72               # window + canvas edge (px)
_GLIDE_SECONDS = 0.45    # travel time to a new point


def _ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


class GhostOrb:
    """An animated moon pointer hosted on the companion's Tk root."""

    def __init__(self, root: Any, *, size: int = _SIZE) -> None:
        import tkinter as tk

        self._size = int(size)
        self._win = tk.Toplevel(root)
        self._win.withdraw()
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        try:
            # Key colour → transparent + click-through (Windows DWM).
            self._win.configure(bg=_CHROMA)
            self._win.attributes("-transparentcolor", _CHROMA)
        except Exception:
            pass
        self._canvas = tk.Canvas(
            self._win, width=self._size, height=self._size,
            bg=_CHROMA, highlightthickness=0, bd=0,
        )
        self._canvas.pack()

        # Position state (screen coords of the moon CENTRE).
        self._x = float(root.winfo_screenwidth()) / 2.0
        self._y = float(root.winfo_screenheight()) / 2.0
        self._from = (self._x, self._y)
        self._to = (self._x, self._y)
        self._glide_start = 0.0
        self._glide_dur = 0.0

        self._visible = False
        self._listening = False
        self._hide_at = 0.0          # auto-park time after a point
        self._phase = 0.0            # breathing/pulse clock

    # ------------------------------------------------------------------
    # Public control (GUI-thread only)
    # ------------------------------------------------------------------

    def point_to(self, x: int, y: int, label: str = "here", seconds: float = 4.0) -> bool:
        """Glide the moon to a screen point and hold it there for ``seconds``."""
        now = time.time()
        self._from = (self._x, self._y)
        self._to = (float(x), float(y))
        self._glide_start = now
        self._glide_dur = _GLIDE_SECONDS
        self._hide_at = now + _GLIDE_SECONDS + max(0.5, float(seconds or 4.0))
        self._show()
        return True

    def set_listening(self, on: bool) -> None:
        """Show/clear the breathing ring while voice capture is active."""
        self._listening = bool(on)
        if on:
            self._hide_at = 0.0      # stay up while hearing
            self._show()
        elif not self._is_gliding():
            self._hide_at = time.time() + 0.6

    def park(self) -> None:
        """Hide the moon."""
        self._listening = False
        self._hide()

    def destroy(self) -> None:
        try:
            self._win.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Per-frame animation (called by the companion loop on the GUI thread)
    # ------------------------------------------------------------------

    def tick(self, now: float | None = None) -> None:
        if not self._visible:
            return
        current = time.time() if now is None else float(now)
        self._phase = current
        self._advance_glide(current)
        if self._hide_at and not self._listening and not self._is_gliding() and current >= self._hide_at:
            self._hide()
            return
        self._reposition()
        self._draw(current)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _is_gliding(self) -> bool:
        return self._glide_dur > 0.0 and (time.time() - self._glide_start) < self._glide_dur

    def _advance_glide(self, now: float) -> None:
        if self._glide_dur <= 0.0:
            return
        t = (now - self._glide_start) / self._glide_dur
        if t >= 1.0:
            self._x, self._y = self._to
            self._glide_dur = 0.0
            return
        e = _ease_out_cubic(t)
        fx, fy = self._from
        tx, ty = self._to
        self._x = fx + (tx - fx) * e
        self._y = fy + (ty - fy) * e

    def _reposition(self) -> None:
        left = int(round(self._x - self._size / 2.0))
        top = int(round(self._y - self._size / 2.0))
        try:
            self._win.geometry(f"{self._size}x{self._size}+{left}+{top}")
        except Exception:
            pass

    def _draw(self, now: float) -> None:
        c = self._canvas
        try:
            c.delete("all")
        except Exception:
            return
        cx = cy = self._size / 2.0
        breathe = 0.5 + 0.5 * _sin(now * 2.2)        # 0..1 gentle
        base_r = self._size * 0.26

        # Listening: an expanding soft ring that fades as it grows.
        if self._listening:
            pulse = (now * 1.4) % 1.0
            ring_r = base_r + pulse * self._size * 0.22
            self._oval(cx, cy, ring_r, outline=_LISTEN, width=2)

        # Outer glow (a couple of faint rings that breathe).
        self._oval(cx, cy, base_r + 6 + breathe * 3, outline=_GLOW, width=1)
        self._oval(cx, cy, base_r + 3, outline=_GLOW, width=1)

        # The moon body, then a shadow disc offset to carve the ◐ crescent.
        self._oval(cx, cy, base_r, fill=_MOON, outline="")
        shadow_dx = base_r * 0.55
        self._oval(cx + shadow_dx, cy, base_r, fill=_MOON_DARK, outline="")

    def _oval(self, cx: float, cy: float, r: float, **kw: Any) -> None:
        try:
            self._canvas.create_oval(cx - r, cy - r, cx + r, cy + r, **kw)
        except Exception:
            pass

    def _show(self) -> None:
        if not self._visible:
            self._visible = True
            try:
                self._win.deiconify()
                self._win.attributes("-topmost", True)
            except Exception:
                pass

    def _hide(self) -> None:
        self._visible = False
        self._hide_at = 0.0
        try:
            self._win.withdraw()
        except Exception:
            pass


def _sin(x: float) -> float:
    import math
    return math.sin(x)
