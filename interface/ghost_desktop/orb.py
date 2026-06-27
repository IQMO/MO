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

# Near-black transparency key: the moon is rendered anti-aliased (PIL) and flattened
# onto this colour, so the feathered rim fades to a soft dark halo and only the exact
# key pixels are keyed out (transparent + click-through). No art pixel equals it.
_CHROMA = "#010101"
_MOON = "#00cccc"        # cyan half-moon = MO mark
_MOON_DARK = "#04141a"   # the shadowed limb of the crescent
_GLOW = "#3fe0e0"
_LISTEN = "#bb86fc"      # purple pulse while hearing voice

_SIZE = 80               # window + canvas edge (px)
_GLIDE_SECONDS = 0.45    # travel time to a new point
_ACTIVE_REDRAW_SECONDS = 0.05
_IDLE_REDRAW_SECONDS = 0.16


_MOON_RGB = (0, 204, 204)
_LISTEN_RGB = (187, 134, 252)
_DARK_RGB = (11, 20, 24)


def _ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def _lerp_hex(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> str:
    t = max(0.0, min(1.0, t))
    r = int(round(c1[0] + (c2[0] - c1[0]) * t))
    g = int(round(c1[1] + (c2[1] - c1[1]) * t))
    b = int(round(c1[2] + (c2[2] - c1[2]) * t))
    return f"#{r:02x}{g:02x}{b:02x}"


def _glow_shade(i: int) -> str:
    """Outer glow rings fade from near-cyan toward the dark background as i grows."""
    return _lerp_hex(_MOON_RGB, _DARK_RGB, min(1.0, 0.35 + i * 0.2))


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
        self._level = 0.0            # live mic level (0..~0.3) → audio-reactive rings
        self._photo: Any = None      # current ImageTk frame (kept to avoid GC)
        self._pil_ok = True          # falls back to canvas ovals if PIL is unusable
        self._last_draw_at = 0.0
        self._last_draw_key: tuple[Any, ...] | None = None

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

    def wake(self, x: int | None = None, y: int | None = None, seconds: float = 2.4) -> bool:
        """Briefly show the breathing moon (at a point, or where it last was) — a
        visible 'Ghost is here' with no target to fly to. Used on summon so the
        moon is always the face of Win+Alt+M, never a bare window."""
        now = time.time()
        if x is not None and y is not None:
            self._x, self._y = float(x), float(y)
            self._from = self._to = (self._x, self._y)
        self._glide_dur = 0.0
        self._hide_at = now + max(0.5, float(seconds or 2.4))
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

    def set_level(self, level: float) -> None:
        """Feed the live mic level so the listening rings react to the voice."""
        try:
            self._level = max(0.0, float(level or 0.0))
        except Exception:
            self._level = 0.0

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
        if self._should_redraw(current):
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
        self._last_draw_at = now
        self._last_draw_key = self._draw_key(now)
        if self._pil_ok and self._render_pil(now):
            return
        self._draw_canvas(now)

    def _draw_key(self, now: float) -> tuple[Any, ...]:
        level_bucket = int(max(0.0, min(1.0, self._level * 9.0)) * 12)
        phase_bucket = int(now * (18 if self._listening else 6))
        return (self._listening, level_bucket, phase_bucket)

    def _should_redraw(self, now: float) -> bool:
        key = self._draw_key(now)
        interval = _ACTIVE_REDRAW_SECONDS if self._listening else _IDLE_REDRAW_SECONDS
        return self._last_draw_key != key and (now - self._last_draw_at) >= interval

    def _render_pil(self, now: float) -> bool:
        """Anti-aliased moon via PIL: draw at 4x and downscale so edges are smooth,
        then flatten onto the near-black key (feather → soft halo). Returns False to
        fall back to the canvas ovals if PIL isn't usable."""
        try:
            from PIL import Image, ImageDraw, ImageTk
        except Exception:
            self._pil_ok = False
            return False
        try:
            ss = 4
            s = self._size
            big = s * ss
            cx = cy = big / 2.0
            base_r = big * 0.22
            breathe = 0.5 + 0.5 * _sin(now * 2.2)
            lvl = max(0.0, min(1.0, self._level * 9.0))
            img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)

            def ring(r: float, rgba: tuple, w: int) -> None:
                d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=rgba, width=max(1, int(w)))

            def disc(r: float, cxx: float, rgba: tuple) -> None:
                d.ellipse([cxx - r, cy - r, cxx + r, cy + r], fill=rgba)

            # Listening: reactive purple rings whose spread/brightness grow with voice.
            if self._listening:
                for i in range(3):
                    spread = (i + 1) / 3.0
                    rr = base_r + big * 0.04 + lvl * big * 0.32 * spread
                    alpha = int(170 * (1.0 - spread) * (0.4 + 0.6 * lvl))
                    ring(rr, (187, 134, 252, max(0, alpha)), ss * 2)
                ring(base_r + big * 0.05 + breathe * big * 0.02, (187, 134, 252, 90), ss)

            # Soft cyan glow halo, fading outward → smooth feathered edge.
            for i, gr in enumerate((0.16, 0.11, 0.07, 0.04)):
                ring(base_r + big * gr, (0, 204, 204, int(75 * (1.0 - i / 4.0))), ss * 2)

            # Moon body + offset shadow disc → the ◐ crescent, gentle size pulse on level.
            body_r = base_r * (1.0 + 0.06 * lvl)
            disc(body_r, cx, (0, 204, 204, 255))
            disc(body_r, cx + body_r * 0.55, (4, 20, 26, 255))

            img = img.resize((s, s), Image.LANCZOS)
            flat = Image.new("RGB", (s, s), (1, 1, 1))  # == _CHROMA #010101
            flat.paste(img, (0, 0), img)
            self._photo = ImageTk.PhotoImage(flat)
            self._canvas.delete("all")
            self._canvas.create_image(s // 2, s // 2, image=self._photo)
            return True
        except Exception:
            self._pil_ok = False
            return False

    def _draw_canvas(self, now: float) -> None:
        c = self._canvas
        try:
            c.delete("all")
        except Exception:
            return
        cx = cy = self._size / 2.0
        breathe = 0.5 + 0.5 * _sin(now * 2.2)        # 0..1 gentle
        base_r = self._size * 0.24
        lvl = max(0.0, min(1.0, self._level * 9.0))  # normalise mic level to 0..1

        # Listening: audio-reactive concentric rings — an equalizer-like bloom that
        # grows with how loud you speak, over a steady breathing ring.
        if self._listening:
            self._oval(cx, cy, base_r + 6 + breathe * 3, outline=_LISTEN, width=1)
            for i in range(3):
                spread = (i + 1) / 3.0
                react_r = base_r + 4 + lvl * self._size * 0.30 * spread
                self._oval(cx, cy, react_r,
                           outline=_lerp_hex(_LISTEN_RGB, _DARK_RGB, spread * (1.0 - 0.5 * lvl)),
                           width=2 if i == 0 else 1)

        # Soft glow halo: graduated rings fading outward so the rim reads soft, not hard.
        for i, gr in enumerate((11, 8, 5, 3)):
            self._oval(cx, cy, base_r + gr + breathe * 2, outline=_glow_shade(i), width=1)

        # Moon body + offset shadow disc → the ◐ crescent, with a gentle size pulse.
        body_r = base_r * (1.0 + 0.06 * lvl)
        self._oval(cx, cy, body_r, fill=_MOON, outline="")
        self._oval(cx + body_r * 0.55, cy, body_r, fill=_MOON_DARK, outline="")

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
