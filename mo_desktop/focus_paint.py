"""Focus's pixels, using the same antialiased card and font owners as the cube UI."""
from __future__ import annotations

import calendar
from datetime import datetime
from typing import Any

from interface.desktop_brand import make_glyph_icon
from mo_desktop import card


SS = card.SS
# Hover repaints the whole face (row 20: 32 ms each, on the cubes' GUI thread). The window and
# pin icons and the card surface are the same on every paint, so they are scaled and built once;
# entries hold the source object, so a reused id can never return another icon's pixels.
_SCALED: dict[tuple, tuple[Any, Any]] = {}
_SURFACES: dict[tuple, tuple[Any, Any]] = {}
_CACHE_LIMIT = 256


def _scaled(icon: Any, edge: int, *, dim: bool = False) -> Any:
    """``icon`` at ``edge`` px (LANCZOS), optionally at the idle 72% alpha; never mutate the result."""
    from PIL import Image

    key = (id(icon), edge, dim)
    hit = _SCALED.get(key)
    if hit is not None and hit[0] is icon:
        return hit[1]
    sprite = icon.resize((edge, edge), Image.Resampling.LANCZOS)
    if dim:
        sprite.putalpha(sprite.getchannel("A").point(lambda alpha: round(alpha*.72)))
    if len(_SCALED) >= _CACHE_LIMIT:
        _SCALED.clear()
    _SCALED[key] = (icon, sprite)
    return sprite


def _surface(size: tuple[int, int], visuals: Any) -> Any:
    """A fresh copy of the face's card surface (built once per size and visual state)."""
    key = (tuple(size), id(visuals))
    hit = _SURFACES.get(key)
    if hit is None or hit[0] is not visuals:
        if len(_SURFACES) >= 16:
            _SURFACES.clear()
        hit = (visuals, card.surface_canvas(size, visuals))
        _SURFACES[key] = hit
    return hit[1].copy()



def cube_face(size: tuple[int, int], visuals: Any, rows: list, icons: dict,
              *, collapsed: bool, hover: Any, failed: Any, tray_failed: bool,
              now: datetime, total: int, cube_color: tuple, cube_corner: float,
              cube_sprite: Any = None, pins: tuple = (), query: str = "", searching: bool = False,
              search_note: str = "", selection: tuple | None = None,
              row_kind: str = "window", settings: bool = False, idle_opacity: float = .42,
              power: tuple = ("", False, 15, 0, False, "")) -> tuple[Any, dict]:
    from PIL import ImageDraw, Image
    from interface.theming import contrast_text, _mix
    width, height = size
    p = visuals.palette
    power_action, power_delay, power_minutes, power_remaining, power_busy, power_error = power
    if collapsed:
        if cube_sprite is None:
            raise ValueError("Collapsed Focus requires the resident cube sprite")
        image = cube_sprite.resize((width*SS, height*SS), Image.Resampling.LANCZOS)
        draw = ImageDraw.Draw(image)
        color = "#%02x%02x%02x" % cube_color
        if power_remaining:
            color = _mix(color, p.action, .48)
            tint = Image.new("RGBA", image.size, color)
            tint.putalpha(image.getchannel("A"))
            image = Image.blend(image, tint, .48)
            draw = ImageDraw.Draw(image)
        ink = contrast_text(color, dark=p.card, light=p.text)
        draw.text((width*SS/2, 13*SS), now.strftime("%H:%M"), font=card.role_font("small"), fill=ink, anchor="mm")
        if power_remaining:
            label = f"{(power_remaining+59)//60}m" if power_remaining >= 60 else f"{power_remaining}s"
            draw.text((4*SS, 30*SS), label, font=card.role_font("tiny"), fill=ink, anchor="lm")
            image.alpha_composite(make_glyph_icon("close", 9*SS, color=ink), ((width-12)*SS, 26*SS))
            return card.finish(image), {"power:cancel": (width-17, 22, 17, height-22), "toggle": (0, 0, width, height)}
        icon = make_glyph_icon("split", 9*SS, color=ink)
        label = str(total)
        font = card.role_font("tiny")
        span = 13*SS+draw.textlength(label, font=font)
        left = round((width*SS-span)/2)
        image.alpha_composite(icon, (left, 26*SS))
        draw.text((left+13*SS, 30*SS), label, font=font, fill=ink, anchor="lm")
        return card.finish(image), {"toggle": (0, 0, width, height)}

    image = _surface(size, visuals)
    draw = ImageDraw.Draw(image)
    hits = {"toggle": (7, 4, 24, 28), "search": (105, 7, width-112, 23)}
    radius = visuals.metrics.button_corner_radius*SS
    # One cube is the folding handle of this same fourth face.
    cube_edge = 10*SS
    draw.rounded_rectangle((14*SS, 12*SS, 24*SS, 22*SS), radius=cube_edge*cube_corner,
                            fill=cube_color)
    draw.text((32*SS, 18*SS), f"Windows {total}", font=card.role_font("tiny"), fill=p.muted, anchor="lm")
    x, y, w, h = hits["search"]
    draw.rounded_rectangle((x*SS, y*SS, (x+w)*SS, (y+h)*SS), radius=radius, fill=p.entry)
    if query:
        hits["search:clear"] = (x+w-23, y, 23, h)
        image.alpha_composite(make_glyph_icon("close", 10*SS, color=p.text if hover == "search:clear" else p.muted),
                              ((x+w-17)*SS, (y+6)*SS))
        w -= 23
        hits["search"] = (x, y, w, h)
    font = card.role_font("tiny")
    label = card.fit_text(draw, query or "Search PC", (w-12)*SS, font)
    draw.text(((x+6)*SS, (y+h/2)*SS), label, font=font, fill=p.text if query else p.muted, anchor="lm")
    if selection is not None:
        start, end = selection
        cx = min((x+w-5)*SS, (x+6)*SS+draw.textlength(query[:end], font=font))
        if start != end:
            sx = min(cx, (x+6)*SS+draw.textlength(query[:start], font=font))
            draw.line((sx, (y+h-4)*SS, cx, (y+h-4)*SS), fill=p.accent, width=SS)
        else:
            draw.line((cx, (y+5)*SS, cx, (y+h-5)*SS), fill=p.accent, width=SS)
    if pins:
        draw.rounded_rectangle((7*SS, (height-82)*SS, (width-7)*SS, (height-47)*SS),
                               radius=radius, fill=_mix(p.card, p.entry, .55))
    for index, (path, _name, icon) in enumerate(pins):
        key = ("pin", path)
        x, y = (width-len(pins)*37)//2+index*37+2, height-80
        hits[key] = (x, y, 32, 32)
        if hover == key:
            draw.rounded_rectangle((x*SS, y*SS, (x+32)*SS, (y+32)*SS), radius=radius, fill=p.entry)
        if icon is not None:
            image.alpha_composite(_scaled(icon, 22*SS, dim=hover != key), ((x+5)*SS, (y+5)*SS))
    power_names = {"sleep": "Sleep", "restart": "Restart", "shutdown": "Shut down"}
    def control(key: str, label: str, box: tuple, selected: bool = False) -> None:
        x, y, w, h = box
        if not power_busy:
            hits[key] = box
        draw.rounded_rectangle((x*SS, y*SS, (x+w)*SS, (y+h)*SS), radius=radius,
                               fill=p.entry, outline=p.accent if selected or hover == key else p.border, width=SS)
        draw.text(((x+w/2)*SS, (y+h/2)*SS), label, font=card.role_font("tiny"),
                  fill=p.muted if power_busy else p.text, anchor="mm")
    for index, (handle, title, active) in enumerate([] if settings else rows):
        _draw_row(image, draw, visuals, width, index, handle, title, active, row_kind=row_kind,
                  hover=hover, failed=failed, icons=icons, hits=hits)
    if settings:
        draw.text((15*SS, 49*SS), "Focus settings", font=card.role_font("small"), fill=p.text, anchor="lm")
        draw.text((15*SS, 68*SS), f"Dimming   {round((1-idle_opacity)*100)}%", font=card.role_font("tiny"), fill=p.muted, anchor="lm")
        hits["dimming"] = (20, 76, width-40, 20)
        start, end, cy = 23*SS, (width-23)*SS, 86*SS
        cx = start+(end-start)*(1-idle_opacity)
        draw.rounded_rectangle((start, cy-2*SS, end, cy+2*SS), radius=2*SS, fill=p.entry)
        draw.rounded_rectangle((start, cy-2*SS, max(start+SS, cx), cy+2*SS), radius=2*SS, fill=p.accent)
        draw.ellipse((cx-5*SS, cy-5*SS, cx+5*SS, cy+5*SS), fill=p.text)
        for index, (action, glyph) in enumerate((("sleep", "sleep"), ("restart", "refresh"), ("shutdown", "power"), ("delay", "timer"))):
            x, y = (width-167)//2+index*45, 105
            selected = power_delay if action == "delay" else power_action == action
            key = "power:"+action
            if not power_remaining and not power_busy:
                hits[key] = (x, y, 32, 27)
            if selected or hover == key:
                draw.rounded_rectangle((x*SS, y*SS, (x+32)*SS, (y+27)*SS), radius=radius, fill=p.entry)
            color = p.accent if selected else p.muted if action == "delay" or power_busy else p.text
            image.alpha_composite(make_glyph_icon(glyph, 17*SS, color=color), ((x+7)*SS, (y+5)*SS))
        if power_remaining or power_busy:
            label = f"{power_names[power_action]} {power_remaining//60}:{power_remaining%60:02}" if power_remaining else "Requesting Windows…"
            draw.text((14*SS, 148*SS), label, font=card.role_font("tiny"), fill=p.text, anchor="lm")
            control("power:cancel", "Cancel", (width-61, 136, 48, 23))
        elif power_action:
            label = f"{power_names[power_action]} in {power_minutes}m" if power_delay else power_names[power_action]+" now"
            control("power:confirm", label, (13, 136, width-56, 23), True)
            control("power:cancel", "×", (width-36, 136, 23, 23))
        elif power_error:
            draw.text((width*SS/2, 149*SS), power_error, font=card.role_font("tiny"), fill=p.error, anchor="mm")
        elif power_delay:
            control("power:less", "−", (47, 136, 24, 23))
            draw.text((width*SS/2, 148*SS), f"{power_minutes} min", font=card.role_font("tiny"), fill=p.text, anchor="mm")
            control("power:more", "+", (width-71, 136, 24, 23))
        else:
            label = {"power:sleep": "Sleep", "power:restart": "Restart", "power:shutdown": "Shut down", "power:delay": "Timer off"}.get(hover, "")
            draw.text((width*SS/2, 149*SS), label, font=card.role_font("tiny"), fill=p.muted, anchor="mm")
    elif not rows:
        empty = "Searching…" if searching else "No matches" if query else "No open windows"
        draw.text((width*SS/2, 82*SS), empty, font=card.role_font("small"), fill=p.muted, anchor="mm")
    footer = height-45
    if not settings:
        for index, (path, name, _icon) in enumerate(pins):
            if hover != ("pin", path):
                continue
            # Reveal the label at the bottom of the existing list.
            # No tooltip window, resize, delayed timer, or extra render owner.
            top = max(35, footer-71)
            draw.rounded_rectangle((7*SS, top*SS, (width-5)*SS, (top+29)*SS),
                                   radius=radius, fill=p.entry)
            draw.text((15*SS, (top+14)*SS),
                      card.fit_text(draw, name, (width-30)*SS, card.role_font("small")),
                      font=card.role_font("small"), fill=p.muted, anchor="lm")
    draw.line((13*SS, footer*SS, (width-13)*SS, footer*SS), fill=p.border, width=SS)
    hits["clock"] = (8, footer+2, width-86, 20 if power_remaining else 40)
    hits["tray"] = (width-41, footer+7, 32, 32)
    hits["settings"] = (width-72, footer+7, 30, 32)
    if hover == "settings" or settings:
        x, y, w, h = hits["settings"]
        draw.rounded_rectangle((x*SS, y*SS, (x+w)*SS, (y+h)*SS), radius=radius, fill=p.entry)
    image.alpha_composite(make_glyph_icon("settings", 16*SS, color=p.accent if settings else p.muted),
                           ((width-65)*SS, (footer+15)*SS))
    if hover == "tray":
        x, y, w, h = hits["tray"]
        draw.rounded_rectangle((x*SS, y*SS, (x+w)*SS, (y+h)*SS), radius=radius, fill=p.entry)
    color = p.error if tray_failed else p.text if hover == "tray" else p.muted
    image.alpha_composite(make_glyph_icon("tray", 18*SS, color=color), ((width-34)*SS, (footer+14)*SS))
    draw.text((15*SS, (footer+16)*SS), now.strftime("%H:%M"), font=card.role_font("body"),
              fill=p.text, anchor="lm")
    countdown = f"{power_names[power_action]} {power_remaining//60}:{power_remaining%60:02}" if power_remaining else ""
    if power_remaining and not settings:
        hits["power:cancel"] = (width-110, footer+23, 18, 18)
        image.alpha_composite(make_glyph_icon("close", 10*SS, color=p.action), ((width-106)*SS, (footer+27)*SS))
    footer_text = card.fit_text(draw, countdown or search_note or now.strftime("%a %d %b"),
                               (width-(127 if power_remaining else 91))*SS, card.role_font("tiny"))
    draw.text((15*SS, (footer+32)*SS), footer_text, font=card.role_font("tiny"),
              fill=p.action if power_remaining else p.text if hover == "clock" else p.muted, anchor="lm")
    return card.finish(image), hits


def tray_image(items: list, visuals: Any, hovered: int | None = None) -> tuple[Any, dict]:
    from PIL import ImageDraw, Image
    columns = min(5, max(1, len(items)))
    rows = (len(items)+columns-1)//columns
    size = (columns*38+16, rows*38+38)
    image = card.surface_canvas(size, visuals)
    draw = ImageDraw.Draw(image)
    hits = {}
    for index, item in enumerate(items):
        x, y = 8+index%columns*38, 8+index//columns*38
        hits[index] = (x, y, 36, 36)
        if index == hovered:
            draw.rounded_rectangle((x*SS, y*SS, (x+36)*SS, (y+36)*SS),
                radius=visuals.metrics.button_corner_radius*SS, fill=visuals.palette.entry)
        image.alpha_composite(item.image.resize((22*SS, 22*SS), Image.Resampling.LANCZOS), ((x+7)*SS, (y+7)*SS))
    label = items[hovered].name.splitlines()[0] if hovered is not None else "System tray"
    draw.text((size[0]*SS/2, (size[1]-16)*SS),
        card.fit_text(draw, label, (size[0]-20)*SS, card.role_font("tiny")),
        font=card.role_font("tiny"), fill=visuals.palette.muted, anchor="mm")
    return card.finish(image), hits


def preview(size: tuple[int, int], visuals: Any, title: str, icon: Any, minimized: bool, *, available: bool = True) -> Any:
    from PIL import ImageDraw, Image
    image = card.surface_canvas(size, visuals)
    draw = ImageDraw.Draw(image)
    w, h = size
    if icon is not None:
        image.alpha_composite(icon.resize((24*SS, 24*SS), Image.Resampling.LANCZOS), (14*SS, 17*SS))
    draw.text((49*SS, 24*SS), card.fit_text(draw, title, (w-64)*SS, card.role_font("section")),
              font=card.role_font("section"), fill=visuals.palette.text, anchor="lm")
    draw.text((49*SS, 43*SS), "Minimized · scroll to browse" if minimized else "Scroll to browse · click to switch",
              font=card.role_font("tiny"), fill=visuals.palette.muted, anchor="lm")
    if not available:
        draw.text((w*SS/2, (h+62)*SS/2), "Preview unavailable", font=card.role_font(), fill=visuals.palette.muted, anchor="mm")
    return card.finish(image)


def calendar_image(visuals: Any, year: int, month: int, today: datetime) -> tuple[Any, dict]:
    from PIL import ImageDraw
    size = (266, 286)
    image = card.surface_canvas(size, visuals)
    draw = ImageDraw.Draw(image)
    p = visuals.palette
    draw.text((18*SS, 22*SS), f"{calendar.month_name[month]} {year}", font=card.role_font("section"), fill=p.text, anchor="lm")
    hits = {"month_previous": (204, 10, 22, 24), "month_next": (232, 10, 22, 24)}
    for key, glyph in (("month_previous", "chevron_left"), ("month_next", "chevron_right")):
        x, y, w, h = hits[key]
        image.alpha_composite(make_glyph_icon(glyph, 14*SS, color=p.muted), ((x+4)*SS, (y+5)*SS))
    for col, name in enumerate(("M", "T", "W", "T", "F", "S", "S")):
        draw.text(((28+35*col)*SS, 57*SS), name, font=card.role_font("tiny"), fill=p.muted, anchor="mm")
    radius = visuals.metrics.button_corner_radius*SS
    for row, week in enumerate(calendar.monthcalendar(year, month)):
        for col, day in enumerate(week):
            if not day:
                continue
            x, y = 28+35*col, 88+31*row
            selected = (year, month, day) == (today.year, today.month, today.day)
            if selected:
                draw.rounded_rectangle(((x-14)*SS, (y-13)*SS, (x+14)*SS, (y+13)*SS), radius=radius, fill=p.accent)
            draw.text((x*SS, y*SS), str(day), font=card.role_font(), fill=p.card if selected else p.text, anchor="mm")
    draw.text((18*SS, 264*SS), today.strftime("%A, %d %B"), font=card.role_font("tiny"), fill=p.muted, anchor="lm")
    return card.finish(image), hits


def _draw_row(image: Any, draw: Any, visuals: Any, width: int, index: int, handle: Any, title: str,
              active: bool, *, row_kind: str, hover: Any, failed: Any, icons: dict, hits: dict,
              top: int = 0) -> None:
    """One window (or search result) row at its slot; ``top`` is the 1x row a band canvas starts at."""
    p = visuals.palette
    radius = visuals.metrics.button_corner_radius*SS
    x, y, w, h = 7, 35+index*30, width-14, 29
    key = (row_kind, handle)
    close = ("close", handle)
    row_hover = hover in (key, close)
    if row_kind == "window" and row_hover:
        hits[close] = (width-34, y+3, 23, 23)
    hits[key] = (x, y, w, h)
    y -= top
    if active or row_hover or handle == failed:
        draw.rounded_rectangle((x*SS, y*SS, (x+w)*SS, (y+h)*SS), radius=radius,
            fill=p.entry, outline=p.error if handle == failed else None, width=SS)
    icon = icons.get(handle)
    if icon is not None:
        image.alpha_composite(_scaled(icon, 18*SS), (14*SS, (y+6)*SS))
    if active:
        draw.ellipse((8*SS, (y+13)*SS, 10*SS, (y+15)*SS), fill=p.accent)
    label = card.fit_text(draw, title, (width-(78 if row_hover and row_kind == "window" else 56))*SS, card.role_font("small"))
    draw.text((40*SS, (y+15)*SS), label, font=card.role_font("small"), fill=p.text, anchor="lm")
    if close in hits:
        cx, cy, cw, ch = hits[close]
        cy -= top
        if hover == close:
            draw.rounded_rectangle((cx*SS, cy*SS, (cx+cw)*SS, (cy+ch)*SS), radius=radius, fill=p.card)
        image.alpha_composite(make_glyph_icon("close", 10*SS, color=p.error if hover == close else p.muted),
                              ((cx+6)*SS, (cy+6)*SS))


# A row band is finished with this many 1x px of real neighbour pixels on each side and pasted
# without them: the downscale (LANCZOS at 1/SS) reaches 3 px, so the pasted pixels match a full render.
_BAND_MARGIN = 4


def row_hover_patch(base: Any, size: tuple[int, int], visuals: Any, rows: list, icons: dict, *,
                    hover: Any, failed: Any, row_kind: str, indexes: tuple[int, ...]) -> tuple[Any, dict]:
    """The finished face ``base`` with only the rows at ``indexes`` redrawn for ``hover`` (row 20:
    hovering windows repainted the whole face, 30 ms each). Returns the image and those rows' hits."""
    from PIL import ImageDraw

    width, height = size
    key = (tuple(size), id(visuals))
    surface = _SURFACES[key][1] if key in _SURFACES and _SURFACES[key][0] is visuals else card.surface_canvas(size, visuals)
    out, hits = base.copy(), {}
    spans: list[list[int]] = []
    for index in sorted(set(indexes)):                                 # adjacent rows share one band
        if spans and index == spans[-1][1] + 1:
            spans[-1][1] = index
        else:
            spans.append([index, index])
    for first, last in spans:
        first_top, last_top = 35 + first*30, 35 + last*30
        top, bottom = max(0, first_top - _BAND_MARGIN), min(height, last_top + 29 + _BAND_MARGIN)
        band = surface.crop((0, top*SS, width*SS, bottom*SS))
        draw = ImageDraw.Draw(band)
        for other, (handle, title, active) in enumerate(rows):
            other_top = 35 + other*30
            if other_top + 29 >= top and other_top <= bottom:      # neighbours that reach the band
                _draw_row(band, draw, visuals, width, other, handle, title, active, row_kind=row_kind,
                          hover=hover, failed=failed, icons=icons,
                          hits=hits if first <= other <= last else {}, top=top)
        finished = card.finish(band)
        inner_top, inner_bottom = first_top - 1, last_top + 30       # the rows and their 1 px gaps
        out.paste(finished.crop((0, inner_top - top, width, inner_bottom - top)), (0, inner_top))
    return out, hits
