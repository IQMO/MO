"""Standalone secure renderer for portable MO Design ``.modesign`` documents."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from core.runtime.subprocess_flags import gui_python_executable
from mo_desktop.gui_loop import display_period, display_tick


def renderer_available() -> bool:
    return importlib.util.find_spec("webview") is not None


def launch_dashboard(url: str, *, config_path: str = "", on_source: Any = None,
                     on_started: Any = None, on_ready: Any = None) -> subprocess.Popen[Any]:
    """Host the instance-owned Dashboard with the existing optional WebView runtime."""
    if not renderer_available():
        raise RuntimeError("Frameless Dashboard needs MO's optional Design renderer")
    command = [gui_python_executable(), "-m", "mo_desktop.mo_renderer", "--dashboard-stdin"]
    if config_path:
        command.extend(["--config", config_path])
    process = subprocess.Popen(command, cwd=str(Path(__file__).resolve().parents[1]),
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE if on_source or on_ready else None, text=True)
    try:
        process.stdin.write(url + "\n")
        process.stdin.write(json.dumps({"deferred": True} if on_source else None) + "\n")
        process.stdin.flush()
    finally:
        if not on_source:
            process.stdin.close()
    if on_source or on_ready:
        _receive_launch_ready(process, on_ready, on_source=on_source, on_started=on_started)
    return process


def _receive_launch_ready(process: Any, callback: Any, *, on_source: Any = None,
                          on_started: Any = None) -> None:
    """Consume one native launch receipt, including an unsuccessful process exit."""
    def receive() -> None:
        confirmed = False
        try:
            for line in process.stdout:
                try:
                    status = json.loads(line)
                except ValueError:
                    continue
                if isinstance(status, dict) and status.get("kind") == "launch_source" and on_source:
                    def send(source: dict | None) -> None:
                        if process.poll() is None:
                            try:
                                process.stdin.write(json.dumps(source) + "\n")
                                process.stdin.flush()
                            except (OSError, ValueError):
                                pass  # EOF or the child's failure receipt owns the outcome.
                            finally:
                                try:
                                    process.stdin.close()
                                except (OSError, ValueError):
                                    pass
                    on_source(send)
                elif isinstance(status, dict) and status.get("kind") == "launch_started" and on_started:
                    on_started()
                elif isinstance(status, dict) and status.get("kind") == "launch_ready":
                    confirmed = True
                    if callback:
                        callback(status.get("confirmed") is True)
                    break
        finally:
            process.stdout.close()
            if not confirmed and callback:
                callback(False)
    threading.Thread(target=receive, name="mo-app-launch", daemon=True).start()


def _app_entrance_art(source: dict, target: dict) -> dict:
    """Use the actual source cubes and the selected app's own final pixels."""
    import base64
    from PIL import Image

    if source.get("app", "shell") != "shell":
        from PIL import ImageColor
        from mo_desktop.tray import APP_COLOR_ROLES

        visuals = target["visuals"]
        sprites = [(tuple(piece["center"]), Image.open(io.BytesIO(base64.b64decode(piece["png"]))).convert("RGBA"))
                   for piece in source["pieces"]]
        if len(sprites) != 4:
            raise ValueError("Window entrance requires the four real source cubes")
        return {"app": source["app"], "source": sprites, "position": tuple(target["position"]),
                "window_size": tuple(target["size"]), "edge": source["edge"],
                "accent": ImageColor.getrgb(getattr(visuals.palette, APP_COLOR_ROLES[source["app"]])),
                "card": ImageColor.getrgb(visuals.palette.card),
                  "radius": visuals.metrics.panel_corner_radius,
                  "duration": {"dashboard": .78, "files": .82, "phone": .86, "design": .84,
                             "systemcare": .80, "settings": .76}[source["app"]]}
    final = Image.open(io.BytesIO(base64.b64decode(target["png"]))).convert("RGBA")
    pieces = []
    bounds = []
    for original, rectangle in zip(source["pieces"], target["cubes"]):
        sprite = Image.open(io.BytesIO(base64.b64decode(original["png"]))).convert("RGBA")
        x, y, width, height = rectangle
        destination = (target["position"][0] + x + width / 2, target["position"][1] + y + height / 2)
        pieces.append((tuple(original["center"]), destination, sprite, final.crop((x, y, x + width, y + height))))
        for center in (original["center"], destination):
            pad = max(sprite.size) + 24
            bounds.append((center[0] - pad, center[1] - pad, center[0] + pad, center[1] + pad))
    if len(pieces) < 2 or len(pieces) != len(source["pieces"]) or len(pieces) != len(target["cubes"]):
        raise ValueError("App entrance requires matching source and destination pieces")
    left, top = math.floor(min(box[0] for box in bounds)), math.floor(min(box[1] for box in bounds))
    right, bottom = math.ceil(max(box[2] for box in bounds)), math.ceil(max(box[3] for box in bounds))
    return {"pieces": pieces, "position": (left, top), "size": (right - left, bottom - top),
            "edge": source["edge"], "color": tuple(source["color"]),
            "final": final, "final_position": tuple(target["position"]), "app": source.get("app", "shell"),
            "duration": .70, "travel": .48}


def _app_entrance_frame(art: dict, elapsed: float) -> Any:
    if art["app"] != "shell":
        return _purpose_entrance_frame(art, elapsed)
    from PIL import Image
    from mo_desktop.cube_motion import paint_cube_trace, _TRACE_SAMPLE_SECONDS, _TRACE_MAX_POINTS

    image = Image.new("RGBA", art["size"])
    left, top = art["position"]

    def point(start: Any, end: Any, index: int, at: float) -> tuple[float, float, float]:
        phase = max(0.0, min(1.0, (at - index * .025) / art["travel"]))
        ease = phase * phase * (3 - 2 * phase)
        arc = math.sin(math.pi * phase)
        spread = (index * 2 - 1) * 16
        return (start[0] + (end[0] - start[0]) * ease + spread * arc,
                start[1] + (end[1] - start[1]) * ease - 12 * arc, ease)

    for index, (start, end, _sprite, _final) in enumerate(art["pieces"]):
        points = []
        for sample in range(1, _TRACE_MAX_POINTS + 1):
            at = elapsed - sample * _TRACE_SAMPLE_SECONDS
            if 0 < at < art["travel"] + index * .025:
                x, y, _ = point(start, end, index, at)
                points.append((x, y, at))
        tail = min(1.0, max(0.0, (art["duration"] - elapsed) / .20))
        paint_cube_trace(image, points, now=elapsed, origin=(left, top), offsets=((0, 0),),
                         edge=art["edge"], color=art["color"], alpha=round(110 * tail))
    if elapsed >= art["travel"] + (len(art["pieces"]) - 1) * .025:
        image.alpha_composite(art["final"], (art["final_position"][0] - left, art["final_position"][1] - top))
    else:
        for index, (start, end, sprite, final) in enumerate(art["pieces"]):
            x, y, ease = point(start, end, index, elapsed)
            size = (max(1, round(sprite.width + (final.width - sprite.width) * ease)),
                    max(1, round(sprite.height + (final.height - sprite.height) * ease)))
            amount = max(0.0, min(1.0, (ease - .65) / .35))
            painted = Image.blend(sprite.resize(size, Image.Resampling.LANCZOS),
                                  final.resize(size, Image.Resampling.LANCZOS), amount)
            image.alpha_composite(painted, (round(x - left - size[0] / 2), round(y - top - size[1] / 2)))
    return image


def _purpose_entrance_frame(art: dict, elapsed: float) -> Any:
    """Distinct purpose motion, painted through the same finite native adapters."""
    from PIL import Image, ImageDraw
    from mo_desktop.cube_motion import _ease_out

    app = art["app"]
    phase = max(0., min(1., elapsed / art["duration"]))
    unfold = _ease_out(max(0., min(1., (phase - .38) / .62)))
    wx, wy = art["position"]
    width, height = art["window_size"]
    cx, cy = wx + width / 2, wy + height / 2
    extent = min(150, width * .25, height * .30)
    seed_width = max(p[0][0] + p[1].width / 2 for p in art["source"]) - min(p[0][0] - p[1].width / 2 for p in art["source"])
    seed_height = max(p[0][1] + p[1].height / 2 for p in art["source"]) - min(p[0][1] - p[1].height / 2 for p in art["source"])
    sculpt = min(1., phase / .38)
    sculpt = sculpt * sculpt * (3 - 2 * sculpt)
    purpose_width, purpose_height = extent, extent
    if app == "phone":
        purpose_width, purpose_height = extent * .58, extent * 1.4
    cw = seed_width + (purpose_width - seed_width) * sculpt
    ch = seed_height + (purpose_height - seed_height) * sculpt
    cw, ch = cw + (width - cw) * unfold, ch + (height - ch) * unfold
    destinations = []
    for index in range(4):
        col, row = index % 2, index // 2
        if app == "files": destination = (cx + (col - .5) * extent * .70, cy + (row - .5) * extent * .45)
        elif app == "phone": destination = (cx + (col - .5) * extent * .45, cy + (row - .5) * extent)
        elif app == "design": destination = (cx + (index - 1.5) * extent * .30, cy - (index - 1.5) * extent * .30)
        elif app == "settings": destination = (cx + math.sin(phase * math.pi + index) * extent * .25, cy + (index - 1.5) * extent * .25)
        elif app == "systemcare":
            angle = index * math.pi / 2 + (1 - sculpt) * math.pi
            destination = (cx + math.cos(angle) * extent * .40, cy + math.sin(angle) * extent * .40)
        else: destination = (cx + (col - .5) * extent * .65, cy + (row - .5) * extent * .65)
        destinations.append(destination)
    pieces = []
    for index, ((start, sprite), destination) in enumerate(zip(art["source"], destinations)):
        travel = max(0., min(1., (phase - index * .018) / .55))
        travel = travel * travel * (3 - 2 * travel)
        x, y = (start[axis] + (destination[axis] - start[axis]) * travel for axis in (0, 1))
        if app == "files": y -= math.sin(math.pi * travel) * extent * .20 * (1 if index < 2 else -.5)
        elif app == "dashboard": x += (1 if index % 2 else -1) * math.sin(math.pi * travel) * extent * .22
        elif app == "phone": x += math.sin(math.pi * travel) * extent * .10 * (1 if index % 2 else -1)
        pieces.append((x, y, sprite))
    # The purpose form travels with the real cubes before unfolding into the
    # normal window. It never appears separately at a fixed screen centre.
    cx = sum(piece[0] for piece in pieces) / len(pieces)
    cy = sum(piece[1] for piece in pieces) / len(pieces)
    box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
    visible = min(1., phase / .30)
    bounds = [(x - sprite.width / 2 - 3, y - sprite.height / 2 - 3,
               x + sprite.width / 2 + 3, y + sprite.height / 2 + 3) for x, y, sprite in pieces]
    if phase > 0: bounds.append((box[0] - 12, box[1] - 12, box[2] + 12, box[3] + 12))
    left, top = math.floor(min(b[0] for b in bounds)), math.floor(min(b[1] for b in bounds))
    right, bottom = math.ceil(max(b[2] for b in bounds)), math.ceil(max(b[3] for b in bounds))
    frame = Image.new("RGBA", (max(1, right - left), max(1, bottom - top)))
    frame.info["screen_position"] = (left, top)
    if phase > 0:
        # Antialias thin strokes and corners, rather than resampling a full window.
        draw = ImageDraw.Draw(frame)
        def rgba(color, alpha, premultiplied=False):
            amount = round(alpha * visible)
            rgb = tuple(round(channel * amount / 255) for channel in color) if premultiplied else color
            return (*rgb, amount)
        def stroke(points, alpha=180, line_width=2):
            if round(alpha * visible) == 0:
                return
            for a, b in zip(points, points[1:]):
                pad = line_width + 1
                sx, sy = math.floor(min(a[0], b[0]) - pad), math.floor(min(a[1], b[1]) - pad)
                size = (math.ceil(max(a[0], b[0]) + pad) - sx,
                        math.ceil(max(a[1], b[1]) + pad) - sy)
                if size[0] * size[1] <= 32768:
                    ink = Image.new("RGBa", (size[0] * 2, size[1] * 2))
                    ImageDraw.Draw(ink).line([(round((x-sx)*2), round((y-sy)*2)) for x, y in (a, b)],
                                             fill=rgba(art["accent"], alpha, True), width=line_width * 2)
                    frame.alpha_composite(ink.resize(size, Image.Resampling.BOX).convert("RGBA"), (sx-left, sy-top))
                    continue
                # Long diagonals occupy a thin band. Rasterize disjoint strips
                # instead of allocating/resizing their mostly empty rectangle.
                axis = 0 if abs(b[0]-a[0]) >= abs(b[1]-a[1]) else 1
                origin = (sx, sy)
                delta = b[axis]-a[axis]
                points_2x = [(round((x-sx)*2), round((y-sy)*2)) for x, y in (a, b)]
                mask = Image.new("1", (size[0] * 2, size[1] * 2))
                ImageDraw.Draw(mask).line(points_2x, fill=1, width=line_width * 2)
                for offset in range(0, size[axis], 128):
                    lo, hi = origin[axis]+offset, origin[axis]+min(size[axis], offset+128)
                    other = [a[1-axis]+(value-a[axis])*(b[1-axis]-a[1-axis])/delta
                             for value in (lo, hi)] if delta else [a[1-axis]]
                    low = max(origin[1-axis], math.floor(min(other)-pad))
                    high = min(origin[1-axis]+size[1-axis], math.ceil(max(other)+pad))
                    tx, ty, tw, th = ((lo, low, hi-lo, high-low) if axis == 0
                                      else (low, lo, high-low, hi-lo))
                    ink = Image.new("RGBa", (tw * 2, th * 2))
                    ink.paste(rgba(art["accent"], alpha, True), (0, 0, tw*2, th*2),
                              mask.crop(((tx-sx)*2, (ty-sy)*2, (tx-sx+tw)*2, (ty-sy+th)*2)))
                    frame.alpha_composite(ink.resize((tw, th), Image.Resampling.BOX).convert("RGBA"), (tx-left, ty-top))
        def panel(rect, alpha=150, fill=16):
            x0, y0, x1, y1 = (round(value) for value in rect)
            radius = max(0, round(min(art["radius"], (x1-x0)/4, (y1-y0)/4)))
            bg, edge = rgba(art["card"], fill), rgba(art["accent"], alpha)
            if radius == 0:
                draw.rectangle((x0-left, y0-top, x1-left, y1-top), fill=bg, outline=edge)
                return
            draw.rectangle((x0+radius-left, y0-top, x1-radius-left, y1-top), fill=bg)
            draw.rectangle((x0-left, y0+radius-top, x1-left, y1-radius-top), fill=bg)
            draw.line((x0+radius-left, y0-top, x1-radius-left, y0-top), fill=edge)
            draw.line((x0+radius-left, y1-top, x1-radius-left, y1-top), fill=edge)
            draw.line((x0-left, y0+radius-top, x0-left, y1-radius-top), fill=edge)
            draw.line((x1-left, y0+radius-top, x1-left, y1-radius-top), fill=edge)
            size = radius * 2 + 3
            corners = Image.new("RGBa", (size * 2, size * 2))
            ImageDraw.Draw(corners).rounded_rectangle((2, 2, (size-2)*2, (size-2)*2), radius=radius*2,
                fill=rgba(art["card"], fill, True), outline=rgba(art["accent"], alpha, True), width=2)
            corners = corners.resize((size, size), Image.Resampling.BOX).convert("RGBA")
            split = radius + 1
            for crop, position in (
                ((0, 0, split, split), (x0-1, y0-1)),
                ((split+1, 0, size, split), (x1-radius+1, y0-1)),
                ((0, split+1, split, size), (x0-1, y1-radius+1)),
                ((split+1, split+1, size, size), (x1-radius+1, y1-radius+1)),
            ):
                frame.alpha_composite(corners.crop(crop), (position[0]-left, position[1]-top))
        x0, y0, x1, y1 = box
        if app == "dashboard":
            gap = min(8 * (1 - unfold) + 4, cw / 8, ch / 8)
            for row in range(2):
                for col in range(2):
                    px, py = x0 + col * cw/2, y0 + row * ch/2
                    panel((px + gap, py + gap, px + cw/2 - gap, py + ch/2 - gap), 175, 36)
        elif app == "files":
            panel((x0+6, y0+10, x1-6, y1), 80, 28)
            flap = ch * .18 * (1 - unfold)
            stroke([(x0, y1), (x0, y0+flap), (x0+cw*.36, y0+flap),
                    (x0+cw*.43, y0), (x1, y0), (x1, y1), (x0, y1)], 190)
            lid = y0 + ch * .22 * (1 - unfold)
            stroke([(x0+4, lid), (x1-4, lid)], 100)
        elif app == "phone":
            panel(box, 190, 24)
            scan = y0 + ch * (phase * 1.8 % 1)
            stroke([(x0+cw*.12, scan), (x1-cw*.12, scan)], 65)
            notch = cw * .25 * (1 - unfold)
            stroke([(cx-notch/2, y0+8), (cx+notch/2, y0+8)], 180, 3)
        elif app == "design":
            for step in range(1, 5):
                stroke([(x0+cw*step/5, y0), (x0+cw*step/5, y1)], 18, 1)
                stroke([(x0, y0+ch*step/5), (x1, y0+ch*step/5)], 18, 1)
            corners = [(x0,y0), (x1,y0), (x1,y1), (x0,y1), (x0,y0)]
            drawn = min(4., max(0., (phase-.1)/.8*4))
            for index in range(min(4, math.ceil(drawn))):
                a, b = corners[index:index+2]
                amount = min(1., drawn-index)
                stroke([a, (a[0]+(b[0]-a[0])*amount, a[1]+(b[1]-a[1])*amount)], 190)
        elif app == "systemcare":
            panel(box, 85, 14)
            shield = [(cx-cw*.23, cy-ch*.24), (cx+cw*.23, cy-ch*.24),
                      (cx+cw*.20, cy+ch*.12), (cx,cy+ch*.30), (cx-cw*.20,cy+ch*.12), (cx-cw*.23,cy-ch*.24)]
            stroke(shield, round(190*(1-unfold)))
            stroke([(cx-cw*.10,cy), (cx-cw*.02,cy+ch*.09), (cx+cw*.12,cy-ch*.10)], round(210*(1-unfold)), 3)
        elif app == "settings":
            for index in range(4):
                sy=y0+ch*(index+1)/5
                stroke([(x0+cw*.14,sy),(x1-cw*.14,sy)], 75, 2)
                knob=x0+cw*(.25+.5*(.5+.5*math.sin(phase*math.pi+index)))
                panel((knob-4,sy-7,knob+4,sy+7), 190, 90)
    alpha = max(0., 1 - max(0., phase-.48)/.30)
    for x, y, sprite in pieces:
        if alpha < 1:
            sprite = sprite.copy()
            sprite.putalpha(sprite.getchannel("A").point(lambda value: round(value * alpha)))
        frame.alpha_composite(sprite, (round(x-left-sprite.width/2), round(y-top-sprite.height/2)))
    return frame


def run_dashboard(url: str, *, config_path: str = "", close_after: float = 0,
                  launch_snapshot: dict | None = None) -> int:
    """Native chrome only. All data/actions stay in the authenticated loopback owner."""
    from urllib.parse import urlsplit
    address = urlsplit(url)
    if (address.scheme != "http" or address.hostname != "127.0.0.1"
            or not address.port or address.username or address.password
            or address.path != "/" or address.query or len(address.fragment) < 32):
        raise ValueError("Invalid local Dashboard connection")
    import webview
    from mo_desktop.visuals import load_desktop_config, load_desktop_visual_state
    config = load_desktop_config(config_path=config_path)
    visuals = load_desktop_visual_state(config, refresh_skin=True)
    _set_process_identity()
    window = webview.create_window("MO Dashboard", url=url, width=1200, height=850,
                                   min_size=(720, 540), frameless=True, easy_drag=False,
                                   shadow=False, text_select=True)

    def apply_native_visuals(_theme):
        _apply_design_window_visuals(window, visuals.metrics.panel_corner_radius,
                                    visuals.effects, visuals.palette.border,
                                    str(visuals.token("_GLOW")))
        if native_visuals.entering:
            effect = getattr(window, "_mo_window_effect_layer", None)
            if effect is not None:
                effect.hide()

    native_visuals = _DesignNativeVisualController(window, apply_native_visuals, {})

    def apply_loaded_frame(*_args):
        # WebView owns its pixels: keep its curved edge in the DOM, just like
        # Design's shell. A one-shot GDI frame is erased by subsequent paints.
        frame = {"--panel-radius": f"{visuals.metrics.panel_corner_radius}px",
                 "--button-radius": f"{visuals.metrics.button_corner_radius}px",
                 "--window-border": visuals.palette.border}
        window.evaluate_js(
            "Object.entries(" + json.dumps(frame) + ").forEach(([key,value])=>"
            "document.documentElement.style.setProperty(key,value))"
        )

    window.events.loaded += apply_loaded_frame

    native_visuals.configure_entrance(launch_snapshot, visuals,
        lambda status: print(json.dumps(status), flush=True))
    window.events.shown += native_visuals.start

    def window_control(action, width=0, height=0):
        nonlocal visuals
        if action == "close":
            window.destroy()
        elif action == "minimize":
            window.minimize()
        elif action == "maximize":
            window.restore() if str(window.native.WindowState).endswith("Maximized") else window.maximize()
        elif action == "visuals":
            current = load_desktop_visual_state(config_path=config_path, refresh_skin=True)
            if current != visuals:
                visuals = current
                apply_loaded_frame()
                native_visuals.refresh()
            from core.dashboard.render import _dashboard_skin_css
            return _dashboard_skin_css(current, config)
        elif action in {"reveal", "ready"}:
            native_visuals.mark_content_ready()
        elif action == "resize":
            window.resize(max(720, min(3840, int(width))), max(540, min(2160, int(height))))
        else:
            raise ValueError("Unknown window control")

    window.expose(window_control)
    if close_after > 0:
        def close_later():
            threading.Event().wait(close_after)
            window.destroy()
        window.events.shown += lambda: threading.Thread(target=close_later, daemon=True).start()
    try:
        webview.start(debug=False, icon=str(_renderer_icon_path(visuals, config)))
    finally:
        native_visuals.close()
    return 0


def _studio_shell(theme: dict[str, Any]) -> str:
    """Compose trusted local chrome without external runtime assets."""
    from interface.desktop_brand import cube_mark_css, cube_mark_html, window_chrome_css, window_controls_html

    studio_dir = Path(__file__).resolve().parent / "design_studio"
    shell = (studio_dir / "studio.html").read_text(encoding="utf-8")
    replacements = {
        "/*__MO_THEME__*/": _initial_studio_theme_css(theme),
        "/*__MO_STUDIO_VISUAL__*/": cube_mark_css() + "\n" + (studio_dir / "studio_visual.css").read_text(encoding="utf-8") + window_chrome_css(),
        "<!--__MO_WINDOW_CONTROLS__-->": window_controls_html(("toggle_pin", "minimize", "toggle_maximize", "close"), attribute="data-window-control"),
        "/*__MO_PREVIEW__*/": (studio_dir / "preview_editor.js").read_text(encoding="utf-8"),
        "<!--__MO_BRAND_MARK__-->": cube_mark_html(element_id="brand-mark"),
        "<!--__MO_ACTIVITY_MARK__-->": cube_mark_html(class_name="mo-mark activity-mark"),
        "<!--__MO_BOOT_MARK__-->": cube_mark_html(class_name="mo-mark boot-mark", element_id="boot-mark"),
    }
    if any(shell.count(marker) != 1 for marker in replacements):
        raise RuntimeError("MO Design Studio shell markers are invalid")
    for marker, value in replacements.items():
        shell = shell.replace(marker, value, 1)
    return shell


def focus_renderer(process: subprocess.Popen[Any]) -> bool:
    """Best-effort focus for a renderer already owned by this MO process."""
    if process.poll() is not None:
        return False
    return focus_renderer_pid(int(process.pid))


def focus_renderer_pid(pid: int) -> bool:
    """Best-effort focus for an exact live renderer process."""
    pid = int(pid)
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            found: list[int] = []

            @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
            def visit(handle: int, _extra: int) -> bool:
                owner = wintypes.DWORD()
                user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
                if owner.value == pid and user32.IsWindowVisible(handle):
                    found.append(handle)
                    return False
                return True

            user32.EnumWindows(visit, 0)
            if not found:
                return False
            user32.ShowWindow(found[0], 9)
            return bool(user32.SetForegroundWindow(found[0]))
        except Exception:
            return False
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                [
                    "/usr/bin/osascript", "-e",
                    f'tell application "System Events" to set frontmost of first process whose unix id is {pid} to true',
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False
    wmctrl = shutil.which("wmctrl")
    if wmctrl:
        try:
            rows = subprocess.run(
                [wmctrl, "-lp"], check=False, capture_output=True, text=True, timeout=3,
            ).stdout.splitlines()
            for row in rows:
                columns = row.split(None, 4)
                if len(columns) >= 3 and columns[2] == str(pid):
                    return subprocess.run(
                        [wmctrl, "-ia", columns[0]], check=False, timeout=3,
                    ).returncode == 0
        except (OSError, subprocess.SubprocessError):
            pass
    return False


def launch_renderer(
    path: str | Path | None,
    *,
    config_path: str = "",
    ready_file: str = "",
    close_after: float = 0.0,
    command_secret: str = "",
    standalone_board: bool = False,
    terminal_synced: bool = False,
    board_link_instance: str = "",
    board_link_token: str = "",
    on_source: Any = None,
    on_started: Any = None,
    on_ready: Any = None,
) -> subprocess.Popen[Any]:
    target = Path(path).expanduser().resolve(strict=False) if path is not None else None
    from core.design.service import load_design

    if target is not None:
        load_design(target)
    if not renderer_available():
        raise RuntimeError("MO Design needs its optional renderer: pip install -r requirements-design.txt")
    command = [gui_python_executable(), "-m", "mo_desktop.mo_renderer", str(target) if target is not None else "--home"]
    if config_path:
        command.extend(["--config", str(config_path)])
    if ready_file:
        command.extend(["--ready-file", str(ready_file)])
    if close_after > 0:
        command.extend(["--close-after", str(float(close_after))])
    clean_instance = str(board_link_instance or "").strip()
    clean_token = str(board_link_token or "").strip()
    linked_board = bool(clean_instance or clean_token)
    if linked_board and (not clean_instance or len(clean_token) < 32):
        raise ValueError("The standalone MO Board binding is invalid")
    if standalone_board:
        command.append("--standalone-board")
        if linked_board:
            command.append("--board-link-stdin")
    elif linked_board:
        raise ValueError("The standalone MO Board binding is invalid")
    if terminal_synced:
        command.append("--terminal-synced")
    clean_secret = str(command_secret or "").strip()
    if clean_secret and len(clean_secret) < 32:
        raise ValueError("MO Design command secret is invalid")
    if clean_secret:
        command.append("--command-secret-stdin")
    if on_source:
        command.append("--launch-snapshot-stdin")
    env = dict(os.environ)
    options: dict[str, Any] = {"cwd": str(Path(__file__).resolve().parents[1]), "env": env}
    if clean_secret or linked_board or on_source:
        options.update({"stdin": subprocess.PIPE, "text": True})
    if on_source or on_ready:
        options.update({"stdout": subprocess.PIPE, "text": True})
    process = subprocess.Popen(command, **options)
    if clean_secret or linked_board or on_source:
        stream = process.stdin
        if stream is None:
            raise RuntimeError("MO Design command channel is unavailable")
        try:
            if clean_secret:
                stream.write(clean_secret + "\n")
            if linked_board:
                stream.write(clean_instance + "\n")
                stream.write(clean_token + "\n")
            if on_source:
                stream.write(json.dumps({"deferred": True}) + "\n")
            stream.flush()
        finally:
            if not on_source:
                stream.close()
    if on_source or on_ready:
        _receive_launch_ready(process, on_ready, on_source=on_source, on_started=on_started)
    return process


def run_renderer(
    path: str | Path | None,
    *,
    config_path: str = "",
    ready_file: str = "",
    close_after: float = 0.0,
    command_secret: str = "",
    standalone_board: bool = False,
    terminal_synced: bool = False,
    board_link_instance: str = "",
    board_link_token: str = "",
    launch_snapshot: dict | None = None,
) -> int:
    resource_started_at = time.monotonic()

    def emit_resource(transition: str, reason: str = "") -> None:
        from core.runtime.resource_events import emit_component_resource_event

        emit_component_resource_event(
            "desktop_design",
            transition,
            pids=(os.getpid(),),
            elapsed_seconds=time.monotonic() - resource_started_at,
            reason=reason,
        )

    emit_resource("start")
    target = Path(path).expanduser().resolve(strict=False) if path is not None else None
    if not renderer_available():
        raise RuntimeError("MO Design needs its optional renderer: pip install -r requirements-design.txt")
    import webview

    from mo_desktop.design_studio.bridge import StudioBridge

    bridge = StudioBridge(
        target,
        config_path=config_path,
        ready_file=ready_file,
        command_secret=command_secret,
        standalone_board=standalone_board,
        terminal_synced=terminal_synced,
        board_link_instance=board_link_instance,
        board_link_token=board_link_token,
    )
    shell = _studio_shell(bridge.theme)
    theme = bridge.theme["tokens"]
    icon_path = _renderer_icon_path(bridge.visuals, bridge.config)
    _set_process_identity()
    window = webview.create_window(
        f"{'MO Board' if standalone_board else 'MO Design'} — {bridge._window_title}",
        html=shell,
        js_api=bridge,
        width=bridge.window_options.width,
        height=bridge.window_options.height,
        min_size=(bridge.window_options.min_width, bridge.window_options.min_height),
        resizable=bridge.window_options.resizable,
        frameless=True,
        easy_drag=False,
        shadow=False,
        background_color=str(theme["background"]),
    )
    bridge.attach_window(window)

    def apply_native_visuals(theme_payload: dict[str, Any] | None = None) -> None:
        payload = theme_payload if isinstance(theme_payload, dict) else bridge.theme
        panel = payload.get("panel") if isinstance(payload.get("panel"), dict) else {}
        tokens = payload.get("tokens") if isinstance(payload.get("tokens"), dict) else {}
        required_panel = {
            "corner_radius",
            "window_effect",
            "window_effect_intensity",
        }
        missing = sorted(required_panel.difference(panel))
        required_tokens = {"border", "glow"}
        missing_tokens = sorted(required_tokens.difference(tokens))
        if missing or missing_tokens:
            names = missing + [f"tokens.{name}" for name in missing_tokens]
            raise ValueError("MO Design native visuals are missing: " + ", ".join(names))
        from interface.desktop_ui import (
            DESKTOP_WINDOW_EFFECT_TYPES,
            DesktopWindowEffects,
        )

        style = str(panel["window_effect"]).strip().lower()
        raw_intensity = panel["window_effect_intensity"]
        if style not in DESKTOP_WINDOW_EFFECT_TYPES:
            raise ValueError(f"MO Design window effect is invalid: {style}")
        if isinstance(raw_intensity, bool) or not isinstance(raw_intensity, (int, float)):
            raise ValueError("MO Design window-effect intensity must be numeric")
        intensity = int(raw_intensity)
        if intensity < 0 or intensity > 100 or float(raw_intensity) != intensity:
            raise ValueError(
                "MO Design window-effect intensity must be an integer from 0 to 100"
            )
        _apply_design_window_visuals(
            window,
            int(panel["corner_radius"]),
            DesktopWindowEffects(style=style, intensity=intensity),
            str(tokens["border"]),
            str(tokens["glow"]),
        )
        if native_visuals.entering:
            effect = getattr(window, "_mo_window_effect_layer", None)
            if effect is not None:
                effect.hide()

    native_visuals = _DesignNativeVisualController(
        window,
        apply_native_visuals,
        bridge.theme,
    )
    if launch_snapshot:
        native_visuals.configure_entrance(launch_snapshot, bridge.visuals,
            lambda status: print(json.dumps(status), flush=True))
    window.events.loaded += lambda *_args: native_visuals.mark_content_ready()
    window.events.shown += native_visuals.start
    resource_ready = False

    def mark_resource_ready(*_args: Any) -> None:
        nonlocal resource_ready
        if resource_ready:
            return
        resource_ready = True
        emit_resource("ready")
        emit_resource("first_use")

    window.events.shown += mark_resource_ready
    bridge._visual_state_changed = native_visuals.refresh
    if close_after > 0:
        def close_later() -> None:
            threading.Event().wait(close_after)
            try:
                window.destroy()
            except Exception:
                pass
        threading.Thread(target=close_later, name="mo-design-e2e-close", daemon=True).start()
    try:
        webview.start(debug=False, icon=str(icon_path))
    finally:
        try:
            native_visuals.close()
        finally:
            emit_resource("stop", "window_closed")
            bridge.close()
    return 0


class _DesignNativeVisualController:
    """Keep native app visuals on WinForms' persistent UI thread."""

    def __init__(
        self,
        window: Any,
        apply_visuals: Any,
        initial_theme: dict[str, Any],
    ) -> None:
        self._window = window
        self._apply_visuals = apply_visuals
        self._theme = initial_theme
        self._lock = threading.Lock()
        self._scheduled = False
        self._closed = False
        self._hooks_installed = False
        self._native_handlers: tuple[Any, ...] = ()
        self._scheduled_delegate: Any = None
        self._resize_timer: Any = None
        self._resize_tick_handler: Any = None
        self._resize_pending = False
        self._entrance: dict | None = None
        self._content_ready = True
        self._chrome_state: tuple[bool, bool, bool] | None = None

    @property
    def entering(self) -> bool:
        return self._entrance is not None or not self._content_ready

    def configure_entrance(self, source: dict | None, visuals: Any, notify: Any = None,
                           *, source_reader: bool = True) -> None:
        """One finite WinForms entrance for Dashboard, Files and Design."""
        self._content_ready = False
        if os.name != "nt":
            return
        self._entrance = {"art": None, "notify": notify, "timer": None, "surface": None,
                          "visuals": visuals, "source_reader": source_reader, "requested": False} if source else None

        def prepare() -> None:
            native = self._window.native
            native.Opacity = 0
            if source:
                if not source.get("deferred"):
                    self._entrance["art"] = _app_entrance_art(source,
                        {"position": (native.Left, native.Top), "size": (native.Width, native.Height), "visuals": visuals})
                native.Shown += self._start_app_entrance
            elif self._content_ready:
                native.Opacity = 1
        self._window.events.before_show += prepare

    def mark_content_ready(self) -> None:
        self._content_ready = True
        self._chrome_state = None
        if os.name != "nt":
            self._window.show()
            return
        from System import Action

        def ready() -> None:
            entrance = self._entrance
            if entrance and entrance["timer"] is not None:
                entrance["timer"].Start()
            elif not entrance:
                self._window.native.Opacity = 1
                self.refresh()
        self._window.native.Invoke(Action(ready))

    def _start_app_entrance(self, *_args: Any) -> None:
        from System.Windows.Forms import Timer
        from mo_desktop.layered import NativeLayeredWindow

        entrance = self._entrance
        if entrance is None or entrance["timer"] is not None:
            return
        if entrance["art"] is None:
            if not entrance["requested"]:
                entrance["requested"] = True
                entrance["notify"]({"kind": "launch_source"})
                if entrance["source_reader"]:
                    def receive() -> None:
                        try:
                            source = json.loads(sys.stdin.readline(300_000))
                        except (OSError, ValueError):
                            source = None
                        self.supply_launch_source(source)
                    threading.Thread(target=receive, name="mo-app-source", daemon=True).start()
            return
        try:
            entrance.update(started=time.perf_counter(), fade_started=None,
                            surface=NativeLayeredWindow(int(self._window.native.Handle.ToInt64())))
        except (OSError, RuntimeError):
            self._finish_app_entrance(False)
            return
        timer = Timer()
        # Stay below the default 15.625 ms Windows timer quantum: requesting
        # 16 ms can produce 31 ms frames. This clock exists only during reveal.
        timer.Interval = 15
        timer.Tick += self._tick_app_entrance
        entrance["timer"] = timer
        self._tick_app_entrance(first=True)
        if self._entrance is not None:
            timer.Start()

    def _tick_app_entrance(self, *_args: Any, first: bool = False) -> None:
        entrance = self._entrance
        if entrance is None:
            return
        now = time.perf_counter()
        if not first:
            # The WinForms clock fires at an arbitrary phase of the refresh. Sleep to
            # the display tick and pose this frame for when it is actually on screen.
            shown = display_tick()
            if shown is not None:
                now = shown + display_period()
        elapsed = 0 if first else now - entrance["started"]
        duration = entrance["art"].get("duration", .88)
        if elapsed >= duration and self._content_ready and entrance["fade_started"] is None:
            entrance["fade_started"] = now
        fade = min(1, (now - entrance["fade_started"]) / .20) if entrance["fade_started"] is not None else 0
        if fade >= 1:
            self._finish_app_entrance(True)
            return
        self._window.native.Opacity = fade
        try:
            frame = _app_entrance_frame(entrance["art"], elapsed)
            published = entrance["surface"].blit(frame, *frame.info.get("screen_position", entrance["art"]["position"]), opacity=round(255 * (1 - fade)))
        except (OSError, RuntimeError, TypeError, ValueError):
            published = False
        if not published:
            self._finish_app_entrance(False)
        elif elapsed >= duration and not self._content_ready:
            entrance["timer"].Stop()
        if first and published and entrance["notify"]:
            entrance["notify"]({"kind": "launch_started"})

    def supply_launch_source(self, source: dict | None) -> None:
        from System import Action

        if self._closed:
            return
        def apply() -> None:
            entrance = self._entrance
            if entrance is None or self._closed:
                return
            if source is None:
                self._finish_app_entrance(False)
                return
            native = self._window.native
            try:
                from interface.desktop_widgets import centre_desktop_window
                centre_desktop_window(native, work_area=source.get("work_area"))
                entrance["art"] = _app_entrance_art(source, {"position": (native.Left, native.Top),
                    "size": (native.Width, native.Height), "visuals": entrance["visuals"]})
            except (KeyError, OSError, TypeError, ValueError):
                self._finish_app_entrance(False)
                return
            self._start_app_entrance()
        self._window.native.Invoke(Action(apply))

    def _finish_app_entrance(self, confirmed: bool) -> None:
        entrance, self._entrance = self._entrance, None
        if entrance is None:
            return
        if entrance["timer"] is not None:
            entrance["timer"].Stop()
            entrance["timer"].Dispose()
        if entrance["surface"] is not None:
            entrance["surface"].destroy()
        native = self._window.native
        if not bool(getattr(native, "IsDisposed", False)):
            native.Opacity = 1 if self._content_ready else 0
            self.refresh()
        if entrance["notify"]:
            entrance["notify"]({"kind": "launch_ready", "confirmed": confirmed})

    def start(self, *_args: Any) -> None:
        self.refresh()

    def close(self) -> None:
        """Release the passive effect even when native close notification is missed."""
        if os.name != "nt":
            return
        native = getattr(self._window, "native", None)
        if native is not None and not bool(getattr(native, "IsDisposed", False)):
            try:
                if bool(getattr(native, "InvokeRequired", False)):
                    from System import Action

                    native.Invoke(Action(self._on_native_closed))
                    return
            except Exception:
                pass
        self._on_native_closed()

    def refresh(
        self,
        theme_payload: dict[str, Any] | None = None,
        *_args: Any,
    ) -> None:
        if os.name != "nt":
            return
        with self._lock:
            if isinstance(theme_payload, dict):
                self._theme = theme_payload
            if self._closed or self._scheduled:
                return
            self._scheduled = True
        try:
            native = getattr(self._window, "native", None)
            if native is None or bool(getattr(native, "IsDisposed", False)):
                with self._lock:
                    self._scheduled = False
                return
            from System import Action

            delegate = Action(self._run_scheduled_refresh)
            self._scheduled_delegate = delegate
            native.BeginInvoke(delegate)
        except Exception:
            with self._lock:
                self._scheduled = False
            self._scheduled_delegate = None
            raise

    def _run_scheduled_refresh(self) -> None:
        with self._lock:
            self._scheduled = False
            self._scheduled_delegate = None
            if self._closed:
                return
            theme = self._theme
        self._install_native_hooks()
        self._apply_visuals(theme)
        self._publish_window_chrome()

    def _install_native_hooks(self) -> None:
        if self._hooks_installed:
            return
        native = self._window.native
        from System.Windows.Forms import Timer

        # Frameless MO chrome follows MO's skin, not pywebview's system Mica.
        # Remove its competing writer so an OS theme change cannot restore it.
        system_theme_handler = getattr(native, "on_system_theme_changed", None)
        if system_theme_handler is not None:
            from Microsoft.Win32 import SystemEvents

            SystemEvents.UserPreferenceChanged -= system_theme_handler

        resize_timer = Timer()
        resize_timer.Interval = 120
        resize_tick = self._on_resize_settled
        resize_timer.Tick += resize_tick
        moved = self._on_native_move
        resized = self._on_native_resize
        closing = self._on_native_closing
        closed = self._on_native_closed
        visibility_changed = self._on_native_visibility_changed
        native.Move += moved
        native.Resize += resized
        native.FormClosing += closing
        native.FormClosed += closed
        native.VisibleChanged += visibility_changed
        native.Activated += self._publish_window_chrome
        native.Deactivate += self._publish_window_chrome
        self._resize_timer = resize_timer
        self._resize_tick_handler = resize_tick
        self._native_handlers = (moved, resized, closing, closed, resize_tick, visibility_changed, self._publish_window_chrome)
        self._hooks_installed = True

    def _publish_window_chrome(self, *_args: Any) -> None:
        """Project actual native state once per change; no poll or UI-thread wait."""
        if self._closed:
            return
        native = self._window.native
        browser = getattr(getattr(native, "webview", None), "CoreWebView2", None)
        if browser is None:
            return  # The existing host has not initialized its document yet.
        state = (bool(native.ContainsFocus), str(native.WindowState).endswith("Maximized"), bool(native.TopMost))
        if state == self._chrome_state:
            return
        self._chrome_state = state
        active, maximized, pinned = (str(value).lower() for value in state)
        browser.ExecuteScriptAsync(
            "(()=>{const root=document.documentElement;"
            f"root.dataset.moActive='{active}';root.dataset.moMaximized='{maximized}';"
            f"document.querySelectorAll('.mo-window-pin').forEach(b=>b.setAttribute('aria-pressed','{pinned}'));"
            f"const label={'true' if state[1] else 'false'}?'Restore':'Maximize';"
            "document.querySelectorAll('.mo-window-maximize').forEach(b=>{b.setAttribute('aria-label',label);b.title=label});})()"
        )

    def _on_native_visibility_changed(self, *_args: Any) -> None:
        if self._window.native.Visible:
            self.refresh()
        else:
            # Hide-to-close apps retain their host; their passive effect must hide too.
            self._on_native_closing()

    def _on_native_move(self, *_args: Any) -> None:
        with self._lock:
            if self._closed or self._resize_pending:
                return
            theme = self._theme
        self._apply_visuals(theme)

    def _on_native_resize(self, *_args: Any) -> None:
        self._publish_window_chrome()
        with self._lock:
            if self._closed:
                return
            self._resize_pending = True
            timer = self._resize_timer
        if timer is None:
            return
        timer.Stop()
        timer.Start()
        effect = getattr(self._window, "_mo_window_effect_layer", None)
        if effect is not None:
            effect.hide()

    def _on_resize_settled(self, *_args: Any) -> None:
        with self._lock:
            if self._closed:
                return
            timer = self._resize_timer
            theme = self._theme
            self._resize_pending = False
        if timer is not None:
            timer.Stop()
        self._apply_visuals(theme)

    def _on_native_closing(self, *_args: Any) -> None:
        if any(getattr(arg, "Cancel", False) for arg in _args):
            return
        with self._lock:
            if self._closed:
                return
            self._resize_pending = False
            timer = self._resize_timer
        if timer is not None:
            timer.Stop()
        effect = getattr(self._window, "_mo_window_effect_layer", None)
        if effect is not None:
            effect.hide()

    def _on_native_closed(self, *_args: Any) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._scheduled = False
            self._scheduled_delegate = None
            self._resize_pending = False
            timer = self._resize_timer
            self._resize_timer = None
            self._resize_tick_handler = None
        if timer is not None:
            try:
                timer.Stop()
                timer.Dispose()
            except Exception:
                pass
        self._finish_app_entrance(False)
        effect = getattr(self._window, "_mo_window_effect_layer", None)
        if effect is not None:
            effect.destroy()
            setattr(self._window, "_mo_window_effect_layer", None)


def _apply_design_window_visuals(
    window: Any,
    radius: int,
    effects: Any,
    border_color: str,
    glow_color: str,
) -> str:
    """Apply the shared Desktop region, frame, and effect to pywebview's host."""
    if os.name != "nt":
        return "unsupported"
    from interface.desktop_ui import DesktopWindowEffects

    if not isinstance(effects, DesktopWindowEffects):
        raise TypeError("MO Design native visuals require DesktopWindowEffects")
    native = getattr(window, "native", None)
    handle_value = getattr(native, "Handle", None)
    if native is None or handle_value is None:
        raise RuntimeError("MO Design native window is unavailable")
    converter = getattr(handle_value, "ToInt64", None)
    hwnd = int(converter() if callable(converter) else handle_value)
    # pywebview enables the Windows Mica backdrop for dark system themes.
    # MO owns the background; Mica otherwise fills the clipped-off corners.
    import ctypes
    from ctypes import wintypes
    from interface.desktop_ui import DesktopVisualAdapterError

    set_attribute = ctypes.windll.dwmapi.DwmSetWindowAttribute
    set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    set_attribute.restype = ctypes.c_long
    backdrop = ctypes.c_int(1)  # DWMSBT_NONE; attribute 38 = DWMWA_SYSTEMBACKDROP_TYPE.
    result = set_attribute(hwnd, 38, ctypes.byref(backdrop), ctypes.sizeof(backdrop))
    # Older Windows versions have no system backdrop and reject this attribute.
    if result not in (0, -2147024809):  # S_OK, E_INVALIDARG
        raise DesktopVisualAdapterError("could not disable the WebView system backdrop")
    width = int(getattr(native, "Width"))
    height = int(getattr(native, "Height"))
    key = (hwnd, width, height, max(0, int(radius)))
    region_result = "unchanged"
    from interface.desktop_widgets import (
        DesktopWindowEffectLayer,
        apply_windows_rounded_frame,
        apply_windows_rounded_region,
    )

    if getattr(window, "_mo_rounded_region_key", None) != key:
        region_result = apply_windows_rounded_region(hwnd, width, height, int(radius))
        if region_result in {"applied", "unsupported"}:
            setattr(window, "_mo_rounded_region_key", key)
    # WebView paints the complete rectangular client surface. Restore the same
    # skin-owned curved frame after every native paint/geometry callback so its
    # clipped corner arcs cannot look square or unfinished.
    apply_windows_rounded_frame(
        hwnd,
        width,
        height,
        int(radius),
        str(border_color),
    )
    effect = getattr(window, "_mo_window_effect_layer", None)
    if not isinstance(effect, DesktopWindowEffectLayer):
        effect = DesktopWindowEffectLayer()
        setattr(window, "_mo_window_effect_layer", effect)
    effect_result = effect.refresh_hwnd(
        hwnd,
        width,
        height,
        int(radius),
        effects,
        glow_color,
    )
    return region_result if region_result != "unchanged" else effect_result


def _create_design_native_visual_controller(
    window: Any,
    theme: dict[str, Any],
) -> _DesignNativeVisualController:
    """Bind the common WebView theme payload to one native window owner."""
    controller: _DesignNativeVisualController | None = None

    def apply(payload: dict[str, Any]) -> None:
        from interface.desktop_ui import DesktopWindowEffects

        panel = payload["panel"]
        tokens = payload["tokens"]
        _apply_design_window_visuals(
            window,
            int(panel["corner_radius"]),
            DesktopWindowEffects(
                style=str(panel["window_effect"]),
                intensity=int(panel["window_effect_intensity"]),
            ),
            str(tokens["border"]),
            str(tokens["glow"]),
        )
        if controller is not None and controller.entering:
            effect = getattr(window, "_mo_window_effect_layer", None)
            if effect is not None:
                effect.hide()

    controller = _DesignNativeVisualController(window, apply, theme)
    return controller


def _renderer_icon_path(
    visuals: Any,
    config: dict[str, Any] | None = None,
) -> Path:
    """Materialize the shared skin-aware MO mark for the native window."""
    from core.state.paths import MO_DESIGN_RUNTIME_DIR, resolve_state_path
    from core.utils.atomic_write import atomic_create_bytes
    from interface.desktop_brand import make_four_cube_icon
    from interface.desktop_ui import DesktopVisualState

    if not isinstance(visuals, DesktopVisualState):
        raise TypeError("MO Design icon requires DesktopVisualState")
    palette = visuals.palette
    icon_tokens = {
        "CARD": palette.card,
        "CYAN": palette.accent,
        "_ENTRY_BG": palette.entry,
    }
    fingerprint = hashlib.sha256(
        json.dumps(icon_tokens, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:12]
    suffix = ".ico" if os.name == "nt" else ".png"
    target = Path(resolve_state_path(MO_DESIGN_RUNTIME_DIR, config or {})) / f"mo-design-{fingerprint}{suffix}"
    if target.is_file() and target.stat().st_size > 0:
        return target
    image = make_four_cube_icon(64, palette=palette)
    data = io.BytesIO()
    if suffix == ".ico":
        image.save(data, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)])
    else:
        image.save(data, format="PNG")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        atomic_create_bytes(target, data.getvalue())
    except FileExistsError:
        pass
    return target


def _initial_studio_theme_css(theme: dict[str, Any]) -> str:
    """Project the already-resolved skin/settings into the very first webview frame."""
    tokens = theme.get("tokens") if isinstance(theme.get("tokens"), dict) else {}
    panel = theme.get("panel") if isinstance(theme.get("panel"), dict) else {}
    character = theme.get("character") if isinstance(theme.get("character"), dict) else {}
    typography = theme.get("typography") if isinstance(theme.get("typography"), dict) else {}

    def color(name: str) -> str:
        value = str(tokens.get(name) or "").strip()
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError(f"MO Design theme token is invalid: {name}")
        return value

    def required_number(values: dict[str, Any], name: str) -> float:
        from math import isfinite

        if name not in values:
            raise ValueError(f"MO Design theme metric is missing: {name}")
        try:
            parsed = float(values[name])
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"MO Design theme metric is invalid: {name}") from None
        if not isfinite(parsed):
            raise ValueError(f"MO Design theme metric is invalid: {name}")
        return parsed

    brand = color("brand")
    configured_cube = character.get("color_mode")
    if configured_cube == "skin":
        cube = brand
    elif isinstance(configured_cube, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", configured_cube):
        cube = configured_cube
    else:
        raise ValueError("MO Design character color_mode is invalid")
    values = {
        "--mo-bg": color("background"),
        "--mo-surface": color("surface"),
        "--mo-input": color("input"),
        "--mo-brand": brand,
        "--mo-cube": cube,
        "--mo-glow": color("glow"),
        "--mo-text": color("text"),
        "--mo-muted": color("muted"),
        "--mo-border": color("border"),
        "--mo-ok": color("ok"),
        "--mo-warn": color("warn"),
        "--mo-error": color("error"),
        "--mo-action": color("action"),
        "--panel-pad": f"{int(required_number(panel, 'padding'))}px",
        "--panel-radius": f"{int(required_number(panel, 'corner_radius'))}px",
        "--button-pad": f"{int(required_number(panel, 'button_padding'))}px",
        "--button-radius": f"{int(required_number(panel, 'button_corner_radius'))}px",
        "--cube-radius": f"{max(0.0, min(0.5, required_number(character, 'corner_radius'))) * 100:.2f}%",
        "--cube-glow": f"{max(0.0, min(1.0, required_number(character, 'glow'))):.3f}",
    }
    for role in ("small", "body", "title", "mono"):
        font = typography.get(role)
        if not isinstance(font, dict) or not re.fullmatch(r"[A-Za-z0-9 _.-]{1,80}", str(font.get("family", ""))):
            raise ValueError(f"MO Desktop typography is invalid: {role}")
        size = required_number(font, "size")
        if size <= 0:
            raise ValueError(f"MO Desktop typography size is invalid: {role}")
        values[f"--mo-font-{role}-family"] = '"' + font["family"] + '"'
        # Tk's canonical role sizes are points; CSS pixels are 1/96 inch.
        values[f"--mo-font-{role}-size"] = f"{size * 4 / 3:.4f}px"
    return ":root{" + ";".join(f"{name}:{value}" for name, value in values.items()) + "}"


_WORKSPACE_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
    "img-src data: blob:; connect-src 'none'; font-src 'none'; "
    "object-src 'none'; base-uri 'none'; form-action 'none'"
)


def workspace_document(theme: dict[str, Any], *, theme_id: str, css: str, body: str,
                       scripts: tuple[str, ...], title: str = "") -> str:
    """Compose one offline native-host page; the strict CSP lives only here."""
    from html import escape

    heading = f"<title>{escape(title)}</title>" if title else ""
    return (
        '<!doctype html><html><head><meta charset="utf-8">' + heading
        + '<meta name="viewport" content="width=device-width,initial-scale=1">'
        + f'<meta http-equiv="Content-Security-Policy" content="{_WORKSPACE_CSP}">'
        + f'<style id="{theme_id}">{_initial_studio_theme_css(theme)}</style><style>{css}</style>'
        + f'</head><body>{body}' + "".join(f"<script>{script}</script>" for script in scripts)
        + "</body></html>"
    )


def read_host_init(name: str, *, required: bool = True) -> dict[str, Any] | None:
    """Read a native app host's Desktop-owned JSON init line.

    NativeAppWindow writes its pipe as UTF-8 on every Windows locale, so the host
    must read and report in UTF-8 too or non-ASCII names and paths are corrupted.
    """
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    line = sys.stdin.readline()
    first = json.loads(line) if line.strip() else None
    if isinstance(first, dict) and first.get("cmd") == "init" and isinstance(first.get("config"), dict):
        return first
    if required:
        raise ValueError(f"{name} requires its Desktop initialization payload")
    return None


def _set_process_identity() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MOAgent.Design")
    except Exception:
        pass


def self_check(path: str | Path = "") -> int:
    from mo_desktop.design_studio.theme import studio_theme

    shell = _studio_shell(studio_theme())
    required = ("MO Design", "preview-frame", "pywebview.api", "sandbox=\"allow-scripts\"")
    missing = [item for item in required if item not in shell]
    if path:
        from core.design.service import load_design
        load_design(path)
    if missing:
        print("MO Design self-check failed: " + ", ".join(missing))
        return 1
    print(f"MO Design self-check passed; renderer={'available' if renderer_available() else 'not installed'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render one portable MO Design .modesign document.")
    parser.add_argument("path", nargs="?", default="")
    parser.add_argument("--config", default="")
    parser.add_argument("--ready-file", default="")
    parser.add_argument("--close-after", type=float, default=0.0)
    parser.add_argument("--standalone-board", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--home", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--terminal-synced", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--board-link-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--command-secret-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--launch-snapshot-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--dashboard-stdin", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.self_check:
        return self_check(args.path)
    if not args.path and not args.dashboard_stdin and not args.home:
        parser.error("path is required unless --self-check is used")
    try:
        if args.dashboard_stdin:
            url = sys.stdin.readline(1024).strip()
            snapshot_line = sys.stdin.readline(300_000).strip()
            return run_dashboard(url, config_path=args.config, close_after=max(0.0, args.close_after),
                                 launch_snapshot=json.loads(snapshot_line) if snapshot_line else None)
        command_secret = ""
        board_link_instance = ""
        board_link_token = ""
        if args.command_secret_stdin:
            stream = getattr(sys, "stdin", None)
            command_secret = str(stream.readline(160) if stream is not None else "").strip()
            if len(command_secret) < 32:
                raise RuntimeError("MO Design command channel is unavailable")
        if args.board_link_stdin:
            stream = getattr(sys, "stdin", None)
            board_link_instance = str(stream.readline(80) if stream is not None else "").strip()
            board_link_token = str(stream.readline(160) if stream is not None else "").strip()
            if not board_link_instance or len(board_link_token) < 32:
                raise RuntimeError("The standalone MO Board command channel is unavailable")
        launch_snapshot = json.loads(sys.stdin.readline(300_000)) if args.launch_snapshot_stdin else None
        return run_renderer(
            None if args.home else args.path, config_path=args.config, ready_file=args.ready_file,
            close_after=max(0.0, float(args.close_after or 0.0)),
            command_secret=command_secret,
            standalone_board=bool(args.standalone_board),
            terminal_synced=bool(args.terminal_synced),
            board_link_instance=board_link_instance,
            board_link_token=board_link_token,
            launch_snapshot=launch_snapshot,
        )
    except Exception as exc:
        print(f"MO Design could not open: {str(exc) or type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
