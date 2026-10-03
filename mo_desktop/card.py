"""MO Desktop — the one card primitive.

The cube's floating hint and the reply/dashboard panel draw the same thing: a
rounded, soft-shadowed, bordered card blitted with premultiplied alpha. This is
that single primitive, so every desktop surface reads as one system — one radius
language, one shadow language, one anti-alias factor — instead of two hand-rolled
copies drifting apart.

PIL-only and self-contained: it must not import tkinter, ``layered``, or any
provider SDK, so it stays a pure, headless-testable drawing helper (a caller can
render a card to a plain image without a live window).
"""
from __future__ import annotations

from typing import Any

SS = 3   # single supersample factor for every desktop card (anti-aliased edges + text)

Box = tuple[int, int, int, int]
RGBA = tuple[int, int, int, int]


def new_canvas(width: int, height: int) -> Any:
    """A transparent supersampled RGBA canvas the caller draws the card onto."""
    from PIL import Image
    return Image.new("RGBA", (max(1, int(width)), max(1, int(height))), (0, 0, 0, 0))


def fit_text(draw: Any, text: str, width: int, font: Any, marker: str = "…") -> str:
    """Single-line ellipsis fit for card text: collapse whitespace, then trim
    until the text plus marker fits ``width``. One owner so the dashboard,
    session history, and the reply surface truncate identically."""
    value = " ".join(str(text or "").split())
    if draw.textlength(value, font=font) <= width:
        return value
    while value and draw.textlength(value + marker, font=font) > width:
        value = value[:-1]
    return value.rstrip() + marker


_SHADOW_CACHE: dict[tuple, Any] = {}
_SHADOW_CACHE_MAX = 24
_GRADIENT_CACHE: dict[tuple, Any] = {}
_GRADIENT_CACHE_BYTES = 8 * 1024 * 1024
_GRADIENT_CACHE_MAX = 8


def _shadow_layer(size: tuple[int, int], box: Box, radius: int, alpha: int,
                  blur: int, dx: int, dy: int) -> Any:
    """The blurred drop shadow, memoized. It depends ONLY on geometry — never on the
    card's content — yet a Gaussian blur is the single most expensive step in a render
    (~20ms of a ~43ms card). Caching it makes a repaint roughly twice as fast with no
    visual change, which is what lets streaming and the expand keep up."""
    from PIL import Image, ImageDraw, ImageFilter
    key = (size, tuple(box), int(radius), int(alpha), int(blur), int(dx), int(dy))
    cached = _SHADOW_CACHE.get(key)
    if cached is None:
        shadow = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle(
            [box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy],
            radius=radius, fill=(0, 0, 0, int(alpha)))
        cached = shadow.filter(ImageFilter.GaussianBlur(max(0, int(blur))))
        if len(_SHADOW_CACHE) >= _SHADOW_CACHE_MAX:
            _SHADOW_CACHE.clear()          # bounded: geometry varies little in practice
        _SHADOW_CACHE[key] = cached
    return cached


def draw_card(img: Any, box: Box, *, radius: int, fill: RGBA,
              edge: RGBA | None = None, edge_width: int = 1,
              shadow_alpha: int = 150, shadow_blur: int = 8,
              shadow_dx: int = 0, shadow_dy: int = 5,
              edge_highlight: RGBA | None = None) -> Any:
    """Composite a soft drop shadow, the rounded fill, and an optional border into
    ``img`` (a supersampled RGBA canvas). Returns the composited image; the caller
    then draws its own content on top and calls :func:`finish`.

    All pixel offsets are in supersampled units — pass ``radius``/``shadow_*`` already
    multiplied by :data:`SS` so the primitive stays unit-agnostic.
    """
    from PIL import Image, ImageDraw
    if int(shadow_alpha) > 0:
        img = Image.alpha_composite(
            img, _shadow_layer(img.size, box, radius, int(shadow_alpha),
                               int(shadow_blur), int(shadow_dx), int(shadow_dy)))
    card = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(card).rounded_rectangle(box, radius=radius, fill=fill)
    img = Image.alpha_composite(img, card)
    if edge is not None:
        ImageDraw.Draw(img).rounded_rectangle(box, radius=radius, outline=edge, width=max(1, int(edge_width)))
        if edge_highlight is not None:
            gradient_edge(img, box, radius=radius, width=max(1, int(edge_width)),
                          colors=(edge, edge_highlight, edge))
    return img


def gradient_edge(img: Any, box: Box, *, radius: int, width: int,
                  colors: tuple[RGBA, ...]) -> None:
    """Composite the stationary edge, reusing geometry/palette artwork.

    Bound retained pixels as well as entry count: supersampled panels can be
    large. Content changes never rebuild this edge, and cached layers are never
    exposed to callers or modified while compositing.
    """
    key = (img.size, tuple(box), radius, width, tuple(colors))
    edge = _GRADIENT_CACHE.get(key)
    if edge is None:
        edge = _gradient_layer(img.size, box, radius, width, colors)
        cost = edge.width * edge.height * 4
        if cost <= _GRADIENT_CACHE_BYTES:
            while _GRADIENT_CACHE and (
                len(_GRADIENT_CACHE) >= _GRADIENT_CACHE_MAX
                or sum(layer.width * layer.height * 4 for layer in _GRADIENT_CACHE.values()) + cost
                > _GRADIENT_CACHE_BYTES
            ):
                del _GRADIENT_CACHE[next(iter(_GRADIENT_CACHE))]
            _GRADIENT_CACHE[key] = edge
    img.alpha_composite(edge)


def _gradient_layer(size: tuple[int, int], box: Box, radius: int, width: int,
                    colors: tuple[RGBA, ...]) -> Any:
    from PIL import Image, ImageDraw, ImageChops
    mask = Image.new("L", size)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, outline=255, width=max(1, width))
    vertical = Image.linear_gradient("L")
    ramp = ImageChops.add(vertical.resize(size), vertical.rotate(90).resize(size), scale=2)
    lookup = []
    for value in range(256):
        position = value*(len(colors)-1)/255
        index = min(len(colors)-2, int(position))
        amount = position-index
        lookup.append(tuple(round(a+(b-a)*amount) for a, b in zip(colors[index], colors[index+1])))
    edge = Image.merge("RGBA", tuple(ramp.point([color[channel] for color in lookup]) for channel in range(4)))
    edge.putalpha(ImageChops.multiply(edge.getchannel("A"), mask))
    return edge


def finish(img: Any, ss: int = SS) -> Any:
    """Premultiply alpha THEN downscale by ``ss`` — premultiply-first avoids the dark
    fringe a straight-alpha LANCZOS resize leaves on soft edges. Returns the blit-ready,
    anti-aliased card at 1x."""
    from PIL import Image
    w, h = img.size
    pm = img.convert("RGBa").resize((max(1, w // ss), max(1, h // ss)), Image.Resampling.LANCZOS)
    # Keep Pillow aware that channels are premultiplied during resampling.
    # RGBA.resize would premultiply a second time and darken curved edges.
    return Image.frombytes("RGBA", pm.size, pm.tobytes())


def role_font(role: str = "small") -> Any:
    from interface.desktop_ui import DESKTOP_TYPOGRAPHY
    from mo_desktop.fonts import load_font
    family, points = getattr(DESKTOP_TYPOGRAPHY, role)
    return load_font(("seguisb.ttf", "segoeuib.ttf") if "Semibold" in family else ("segoeui.ttf", "arial.ttf"),
                     round(points*4/3*SS))


def fold_frame(source: Any, target: Any, size: tuple[int, int], amount: float, *,
               start_color: tuple, end_color: tuple, start_radius: float, end_radius: float) -> Any:
    """Morph the shell and reveal natural-size content, without stretching type."""
    from PIL import Image, ImageDraw, ImageChops
    width, height = size
    radius = start_radius+(end_radius-start_radius)*amount
    color = tuple(round(a+(b-a)*amount) for a, b in zip(start_color, end_color))
    shell = new_canvas(width*SS, height*SS)
    ImageDraw.Draw(shell).rounded_rectangle((0, 0, width*SS-1, height*SS-1), radius=radius*SS, fill=(*color, 255))
    shell = finish(shell)
    mask = shell.getchannel("A")
    base = Image.frombytes("RGBa", shell.size, shell.tobytes()).convert("RGBA")
    for image, opacity in ((source, max(0., 1-amount*2.5)), (target, max(0., (amount-.20)/.80))):
        if opacity <= 0:
            continue
        content = image.crop((0, 0, min(width, image.width), min(height, image.height)))
        content = Image.frombytes("RGBa", content.size, content.tobytes()).convert("RGBA")
        content.putalpha(content.getchannel("A").point(lambda alpha: round(alpha*opacity)))
        base.alpha_composite(content)
    base.putalpha(ImageChops.multiply(base.getchannel("A"), mask))
    return Image.frombytes("RGBA", base.size, base.convert("RGBa").tobytes())


def surface_canvas(size: tuple[int, int], visuals: Any) -> Any:
    from PIL import ImageColor
    p = visuals.palette
    w, h = size
    return draw_card(new_canvas(w*SS, h*SS), (0, 0, w*SS-1, h*SS-1),
                          radius=visuals.metrics.panel_corner_radius*SS,
                          fill=(*ImageColor.getrgb(p.card), 255),
                          edge=(*ImageColor.getrgb(p.border), 155), edge_width=SS,
                          shadow_alpha=0, edge_highlight=(*ImageColor.getrgb(p.accent), 110))
