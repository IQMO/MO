"""Shared four-cube MO mark for lightweight Desktop surfaces.

The Dashboard established this 2x2 mark.  Tray and auxiliary windows use the
same geometry instead of inventing a second glyph.  PIL stays behind the icon
factory so importing interface helpers does not make startup heavier.
"""
from __future__ import annotations

from html import escape
from functools import lru_cache
from types import MappingProxyType
from typing import Any


FOUR_CUBE_CELLS = ((0, 0), (1, 0), (0, 1), (1, 1))

# Logical pixels, shared by WebView, Tk and the serialized native Shell theme.
# Configured colors and radii remain owned by DesktopVisualState.
WINDOW_CHROME = MappingProxyType({
    "height": 44, "button": 32, "icon": 15, "gap": 3, "inset": 12,
    "hover_ms": 160, "press_ms": 120, "hover_mix": 7, "close_mix": 14,
})


@lru_cache(maxsize=32)
def glyph_html(name: str) -> str:
    """One crisp, recolorable glyph projection for trusted Desktop HTML."""
    import base64
    from io import BytesIO

    buffer = BytesIO()
    make_glyph_icon(name, 48, color="#ffffff").save(buffer, format="PNG")
    uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    return f'<span class="glyph mo-glyph" aria-hidden="true" style="--glyph:url({uri})"></span>'


def window_controls_html(actions: tuple[str, ...], *, attribute: str = "data-window") -> str:
    """Shared buttons; each host retains its existing bounded action adapter."""
    if attribute not in {"data-window", "data-window-control", "data-action"}:
        raise ValueError("Unknown Desktop window action attribute")
    specs = {"minimize": ("Minimize", "minimize"), "close": ("Close", "close"),
             "toggle_pin": ("Keep on top", "pin"), "pin": ("Keep on top", "pin"),
             "toggle_maximize": ("Maximize", "maximize"), "maximize": ("Maximize", "maximize")}
    buttons = []
    for action in actions:
        label, icon = specs[action]
        state = ' aria-pressed="false"' if icon == "pin" else ""
        alternate = glyph_html("pin_filled" if icon == "pin" else "restore") if icon in {"pin", "maximize"} else ""
        buttons.append(f'<button type="button" class="mo-window-button mo-window-{icon}" '
                       f'{attribute}="{action}" aria-label="{label}" title="{label}"{state}>'
                       f'{glyph_html(icon)}{alternate}</button>')
    return "".join(buttons)


def window_chrome_css() -> str:
    """Compact header and controls only; never overwrite an app's workspace."""
    metrics = ";".join(f"--mo-chrome-{name}:{value}{'ms' if name.endswith('_ms') else '%' if name.endswith('_mix') else 'px'}"
                       for name, value in WINDOW_CHROME.items())
    return ":root{" + metrics + "}" + """
.mo-titlebar{height:var(--mo-chrome-height);min-height:var(--mo-chrome-height);flex:none;display:flex;align-items:center;gap:var(--mo-chrome-inset);padding:0 var(--mo-chrome-inset);box-sizing:border-box;background:var(--mo-surface);border-bottom:1px solid color-mix(in srgb,var(--mo-border) 60%,transparent);color:var(--mo-text);font-family:var(--mo-font-body-family),sans-serif;user-select:none}
.mo-title-identity{display:flex;align-items:center;gap:10px;min-width:0;flex-shrink:1}
.mo-title-identity *{pointer-events:none}
.mo-titlebar .mo-mark{--mark-edge:7px;--mark-gap:3px;flex:none}
.mo-titlebar .mo-title{font-family:var(--mo-font-title-family),sans-serif;font-size:var(--mo-font-body-size);font-weight:600;letter-spacing:-.01em;min-width:0;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.mo-titlebar .mo-title-context{font-size:var(--mo-font-small-size);color:var(--mo-muted);overflow:hidden;white-space:nowrap;text-overflow:ellipsis;min-width:0}
.mo-title-spacer{flex:1;min-width:12px;align-self:stretch}
.mo-title-actions,.mo-window-controls{display:flex;align-items:center;gap:var(--mo-chrome-gap);flex:none;cursor:default;-webkit-app-region:no-drag}
.mo-titlebar>.mo-window-controls{margin-left:auto}
.mo-window-controls[hidden]{display:none}
.mo-title-actions{margin-left:auto;gap:var(--mo-chrome-inset)}
.mo-title-tools{display:flex;align-items:center;gap:var(--mo-chrome-gap);min-width:0}
.mo-titlebar button.mo-window-button{display:grid;place-items:center;flex:none;width:var(--mo-chrome-button);height:var(--mo-chrome-button);min-height:0;min-width:0;box-sizing:border-box;padding:0;margin:0;border:0;border-radius:var(--button-radius);color:var(--mo-muted);background:transparent;cursor:pointer;line-height:1;box-shadow:none;transition:background var(--mo-chrome-hover_ms) ease,color var(--mo-chrome-hover_ms) ease,transform var(--mo-chrome-press_ms) ease}
.mo-glyph{display:inline-block;width:15px;height:15px;background:currentColor;mask:var(--glyph) center/contain no-repeat;vertical-align:middle;flex:none}
.mo-titlebar .mo-window-button .mo-glyph{width:var(--mo-chrome-icon);height:var(--mo-chrome-icon);grid-area:1/1}
.mo-titlebar button.mo-window-button:hover{background:color-mix(in srgb,var(--mo-text) var(--mo-chrome-hover_mix),var(--mo-surface));color:var(--mo-text)}
.mo-titlebar button.mo-window-close:hover{color:var(--mo-error);background:color-mix(in srgb,var(--mo-error) var(--mo-chrome-close_mix),var(--mo-surface))}
.mo-titlebar button.mo-window-button:active{transform:scale(.94);background:var(--mo-input)}
.mo-titlebar button.mo-window-button:focus-visible{outline:2px solid var(--mo-brand);outline-offset:1px}
.mo-titlebar button.mo-window-button:disabled{opacity:.4;cursor:default;transform:none;background:transparent}
.mo-titlebar .mo-window-pin[aria-pressed=true]{color:var(--mo-brand);background:color-mix(in srgb,var(--mo-brand) var(--mo-chrome-hover_mix),var(--mo-surface))}
.mo-window-pin .mo-glyph+ .mo-glyph,.mo-window-maximize .mo-glyph+ .mo-glyph{display:none}
.mo-window-pin[aria-pressed=true] .mo-glyph:first-child,[data-mo-maximized=true] .mo-window-maximize .mo-glyph:first-child{display:none}
.mo-window-pin[aria-pressed=true] .mo-glyph+ .mo-glyph,[data-mo-maximized=true] .mo-window-maximize .mo-glyph+ .mo-glyph{display:inline-block}
[data-mo-active=false] .mo-titlebar .mo-title,[data-mo-active=false] .mo-titlebar .mo-mark{opacity:.65}
@media(prefers-reduced-motion:reduce){.mo-titlebar button.mo-window-button{transition:none;transform:none}}
"""

# Explicit external-service identities, separate from the active MO skin.
SEARCH_SERVICE_COLORS = {
    "google": ((66, 133, 244, 255), (52, 168, 83, 255), (251, 188, 5, 255), (234, 67, 53, 255)),
    "youtube": ((255, 50, 62, 255), (200, 20, 35, 255)),
    "translate": ((66, 133, 244, 255), (135, 183, 255, 255)),
}


def cube_mark_html(*, class_name: str = "mo-mark", element_id: str = "", label: str = "") -> str:
    """Return canonical lightweight mark markup for trusted HTML surfaces."""
    identity = f' id="{escape(element_id, quote=True)}"' if element_id else ""
    accessibility = (
        f' role="img" aria-label="{escape(label, quote=True)}"'
        if label else ' aria-hidden="true"'
    )
    cells = "<i></i>" * len(FOUR_CUBE_CELLS)
    return f'<span class="{escape(class_name, quote=True)}"{identity}{accessibility}>{cells}</span>'


def cube_mark_css(selector: str = ".mo-mark") -> str:
    """Return shared mark geometry; surfaces provide only tokens and scale."""
    return f"""{selector} {{ --mark-edge: 6px; --mark-gap: 3px; display: grid; grid-template-columns: repeat(2,var(--mark-edge)); gap: var(--mark-gap); width: max-content; }}
{selector} > i {{ width: var(--mark-edge); height: var(--mark-edge); border-radius: var(--mark-radius,var(--cube-radius,2px)); background: var(--mark-fill,var(--mo-cube,var(--brand))); box-shadow: inset calc(var(--mark-edge) * -.23) calc(var(--mark-edge) * -.23) 0 color-mix(in srgb,#000 24%,transparent), 0 0 calc(var(--mark-edge) * var(--mark-glow-scale,var(--cube-glow,0))) color-mix(in srgb,var(--mark-glow,var(--mo-glow,var(--brand))) var(--mark-glow-opacity,28%),transparent); }}
{selector}.five {{ position: relative; }}
{selector}.five::after {{ content: \"\"; position: absolute; width: var(--mark-edge); height: var(--mark-edge); left: calc((var(--mark-edge) + var(--mark-gap)) / 2); top: calc((var(--mark-edge) + var(--mark-gap)) / 2); border-radius: var(--mark-radius,var(--cube-radius,2px)); background: var(--mark-fill,var(--mo-cube,var(--brand))); }}"""


def draw_four_cube_mark(
    draw: Any,
    x: int,
    y: int,
    *,
    cube_size: int = 8,
    gap: int = 3,
    radius: int = 2,
    fill: Any,
    shade: Any,
    line_width: int = 1,
) -> tuple[int, int, int, int]:
    """Draw the Dashboard's four-cube mark and return its bounding box."""
    edge = max(2, int(cube_size))
    spacing = max(0, int(gap))
    rounding = max(0, min(int(radius), edge // 2))
    stroke = max(1, int(line_width))
    left, top = int(x), int(y)

    for column, row in FOUR_CUBE_CELLS:
        x0 = left + column * (edge + spacing)
        y0 = top + row * (edge + spacing)
        draw.rounded_rectangle(
            [x0, y0, x0 + edge, y0 + edge], radius=rounding, fill=fill,
        )
        inset = max(1, round(edge * 0.25))
        shade_x = x0 + edge - inset
        draw.line(
            [shade_x, y0 + inset, shade_x, y0 + edge - 1],
            fill=shade,
            width=stroke,
        )

    extent = edge * 2 + spacing
    return (left, top, left + extent, top + extent)


def make_four_cube_icon(
    size: int = 32,
    *,
    palette: Any = None,
) -> Any:
    """Return a skin-aware tray image containing the canonical cube mark."""
    from PIL import Image, ImageDraw
    from interface.desktop_ui import DesktopPalette, active_desktop_visual_state

    palette = palette or active_desktop_visual_state().palette
    if not isinstance(palette, DesktopPalette):
        raise TypeError("Desktop brand icon requires DesktopPalette")
    side = max(16, int(size))
    scale = side / 32.0
    background = palette.card
    brand = palette.accent
    ink = palette.entry
    edge = max(4, round(8 * scale))
    gap = max(1, round(3 * scale))
    extent = edge * 2 + gap
    origin = (side - extent) // 2

    image = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    margin = max(1, round(scale))
    draw.rounded_rectangle(
        [margin, margin, side - margin - 1, side - margin - 1],
        radius=max(4, round(7 * scale)),
        fill=background,
    )
    draw_four_cube_mark(
        draw,
        origin,
        origin,
        cube_size=edge,
        gap=gap,
        radius=max(1, round(2 * scale)),
        fill=brand,
        shade=ink,
        line_width=max(1, round(scale)),
    )
    return image


def make_glyph_icon(
    name: str,
    size: int = 14,
    *,
    color: str,
) -> Any:
    """Return an anti-aliased RGBA UI glyph for Desktop buttons.

    Drawn with PIL at 4x and LANCZOS-downscaled so edges stay smooth; the
    background stays transparent so the themed button surface shows through.
    ``color`` is a caller-supplied palette role, never a constant here.
    """
    import math

    from PIL import Image, ImageDraw

    side = max(8, int(size))
    s = side * 4
    stroke = max(3, round(s * 0.1))
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def pt(x: float, y: float) -> tuple[float, float]:
        return (x * (s - 1), y * (s - 1))

    if name == "up":
        draw.line([pt(0.5, 0.2), pt(0.5, 0.85)], fill=color, width=stroke)
        draw.line(
            [pt(0.2, 0.46), pt(0.5, 0.15), pt(0.8, 0.46)],
            fill=color,
            width=stroke,
            joint="curve",
        )
    elif name == "sleep":
        draw.ellipse((*pt(.13, .12), *pt(.89, .88)), fill=color)
        draw.ellipse((*pt(.40, -.02), *pt(1.04, .64)), fill=(0, 0, 0, 0))
    elif name == "power":
        draw.arc((*pt(.13, .17), *pt(.87, .91)), 310, 230, fill=color, width=round(s*.15))
        draw.line((pt(.5, .06), pt(.5, .52)), fill=color, width=round(s*.15))
    elif name == "timer":
        draw.ellipse((*pt(.12, .16), *pt(.88, .92)), fill=color)
        draw.ellipse((*pt(.26, .30), *pt(.74, .78)), fill=(0, 0, 0, 0))
        draw.line((pt(.38, .06), pt(.62, .06)), fill=color, width=stroke)
        draw.line((pt(.5, .34), pt(.5, .55), pt(.67, .55)), fill=color, width=stroke)
    elif name == "translate":
        draw.line([pt(.1, .25), pt(.57, .25)], fill=color, width=stroke)
        draw.line([pt(.34, .1), pt(.34, .25)], fill=color, width=stroke)
        draw.line([pt(.49, .26), pt(.4, .48), pt(.15, .68)], fill=color, width=stroke, joint="curve")
        draw.line([pt(.22, .32), pt(.35, .48), pt(.51, .6)], fill=color, width=stroke, joint="curve")
        draw.line([pt(.49, .89), pt(.71, .4), pt(.93, .89)], fill=color, width=stroke, joint="curve")
        draw.line([pt(.58, .72), pt(.84, .72)], fill=color, width=stroke)
    elif name == "settings":
        draw.ellipse([pt(.25, .25), pt(.75, .75)], outline=color, width=stroke)
        draw.ellipse([pt(.42, .42), pt(.58, .58)], outline=color, width=max(2, stroke//2))
        for index in range(8):
            angle = index*math.pi/4
            draw.line([pt(.5+.27*math.cos(angle), .5+.27*math.sin(angle)),
                       pt(.5+.39*math.cos(angle), .5+.39*math.sin(angle))], fill=color, width=stroke)
    elif name == "tray":
        draw.line([pt(.12, .44), pt(.2, .82), pt(.8, .82), pt(.88, .44),
                   pt(.64, .44), pt(.59, .58), pt(.41, .58), pt(.36, .44), pt(.12, .44)],
                  fill=color, width=stroke, joint="curve")
        for x in (.3, .5, .7):
            draw.line([pt(x, .16), pt(x, .29)], fill=color, width=stroke)
    elif name == "search":
        draw.ellipse([pt(0.13, 0.13), pt(0.65, 0.65)], outline=color, width=stroke)
        draw.line([pt(0.61, 0.61), pt(0.87, 0.87)], fill=color, width=stroke)
    elif name == "split":
        draw.rounded_rectangle(
            [pt(0.1, 0.17), pt(0.9, 0.83)],
            radius=s * 0.07,
            outline=color,
            width=stroke,
        )
        draw.line([pt(0.5, 0.17), pt(0.5, 0.83)], fill=color, width=stroke)
    elif name == "folder":
        draw.rounded_rectangle(
            [pt(0.08, 0.28), pt(0.92, 0.84)],
            radius=s * 0.07,
            fill=color,
        )
        draw.rounded_rectangle(
            [pt(0.12, 0.16), pt(0.49, 0.43)],
            radius=s * 0.055,
            fill=color,
        )
    elif name == "clip":
        # A paperclip: two nested rounded loops open at the top.
        draw.line([pt(0.62, 0.28), pt(0.62, 0.70)], fill=color, width=stroke)
        draw.arc([pt(0.30, 0.56), pt(0.62, 0.86)], 0, 180, fill=color, width=stroke)
        draw.line([pt(0.30, 0.71), pt(0.30, 0.22)], fill=color, width=stroke)
        draw.arc([pt(0.30, 0.08), pt(0.70, 0.36)], 180, 360, fill=color, width=stroke)
        draw.line([pt(0.70, 0.22), pt(0.70, 0.30)], fill=color, width=stroke)
        draw.line([pt(0.46, 0.30), pt(0.46, 0.66)], fill=color, width=stroke)
    elif name == "file":
        outline = [
            pt(0.22, 0.1),
            pt(0.62, 0.1),
            pt(0.83, 0.31),
            pt(0.83, 0.9),
            pt(0.22, 0.9),
            pt(0.22, 0.1),
        ]
        draw.line(outline, fill=color, width=stroke, joint="curve")
        draw.line(
            [pt(0.62, 0.1), pt(0.62, 0.31), pt(0.83, 0.31)],
            fill=color,
            width=stroke,
            joint="curve",
        )
    elif name == "open":
        draw.rounded_rectangle(
            [pt(0.1, 0.28), pt(0.9, 0.84)],
            radius=s * 0.07,
            outline=color,
            width=stroke,
        )
        draw.line([pt(0.5, 0.72), pt(0.5, 0.14)], fill=color, width=stroke)
        draw.line(
            [pt(0.28, 0.38), pt(0.5, 0.14), pt(0.72, 0.38)],
            fill=color,
            width=stroke,
            joint="curve",
        )
    elif name == "copy":
        draw.rounded_rectangle(
            [pt(0.28, 0.12), pt(0.88, 0.72)],
            radius=s * 0.06,
            outline=color,
            width=stroke,
        )
        draw.rounded_rectangle(
            [pt(0.12, 0.28), pt(0.72, 0.88)],
            radius=s * 0.06,
            outline=color,
            width=stroke,
        )
    elif name == "back":
        draw.line([pt(0.82, 0.5), pt(0.18, 0.5)], fill=color, width=stroke)
        draw.line([pt(0.46, 0.22), pt(0.18, 0.5), pt(0.46, 0.78)],
                  fill=color, width=stroke, joint="curve")
    elif name in {"chevron_left", "chevron_right"}:
        points = [pt(.62, .22), pt(.34, .5), pt(.62, .78)]
        if name == "chevron_right":
            points = [(s - 1 - x, y) for x, y in points]
        draw.line(points, fill=color, width=stroke, joint="curve")
        for x, y in points:
            draw.ellipse((x - stroke / 2, y - stroke / 2, x + stroke / 2, y + stroke / 2), fill=color)
    elif name == "share":
        draw.line([pt(0.27, 0.5), pt(0.73, 0.25)], fill=color, width=stroke)
        draw.line([pt(0.27, 0.5), pt(0.73, 0.75)], fill=color, width=stroke)
        for x, y in ((0.22, 0.5), (0.78, 0.22), (0.78, 0.78)):
            draw.ellipse([pt(x - 0.1, y - 0.1), pt(x + 0.1, y + 0.1)], fill=color)
    elif name == "tools":
        for y, knob in ((0.26, 0.66), (0.5, 0.34), (0.74, 0.59)):
            draw.line([pt(0.12, y), pt(0.88, y)], fill=color, width=stroke)
            draw.ellipse([pt(knob - 0.09, y - 0.09), pt(knob + 0.09, y + 0.09)],
                         fill=color)
    elif name == "crop":
        draw.line([pt(0.16, 0.1), pt(0.16, 0.72), pt(0.78, 0.72)],
                  fill=color, width=stroke, joint="curve")
        draw.line([pt(0.28, 0.28), pt(0.84, 0.28), pt(0.84, 0.9)],
                  fill=color, width=stroke, joint="curve")
    elif name == "rotate":
        draw.arc([pt(0.14, 0.14), pt(0.86, 0.86)], start=55, end=340,
                 fill=color, width=stroke)
        draw.line([pt(0.78, 0.13), pt(0.85, 0.39), pt(0.59, 0.32)],
                  fill=color, width=stroke, joint="curve")
    elif name == "flip":
        draw.line([pt(0.5, 0.13), pt(0.5, 0.87)], fill=color, width=stroke)
        draw.polygon([pt(0.4, 0.24), pt(0.13, 0.5), pt(0.4, 0.76)], outline=color)
        draw.polygon([pt(0.6, 0.24), pt(0.87, 0.5), pt(0.6, 0.76)], outline=color)
    elif name == "convert":
        draw.line([pt(0.14, 0.32), pt(0.83, 0.32), pt(0.67, 0.16)],
                  fill=color, width=stroke, joint="curve")
        draw.line([pt(0.83, 0.32), pt(0.67, 0.48)],
                  fill=color, width=stroke, joint="curve")
        draw.line([pt(0.86, 0.68), pt(0.17, 0.68), pt(0.33, 0.52)],
                  fill=color, width=stroke, joint="curve")
        draw.line([pt(0.17, 0.68), pt(0.33, 0.84)],
                  fill=color, width=stroke, joint="curve")
    elif name == "move":
        draw.line([pt(0.12, 0.5), pt(0.88, 0.5)], fill=color, width=stroke)
        draw.line(
            [pt(0.62, 0.24), pt(0.88, 0.5), pt(0.62, 0.76)],
            fill=color,
            width=stroke,
            joint="curve",
        )
    elif name == "rename":
        draw.line([pt(0.18, 0.78), pt(0.72, 0.24)], fill=color, width=stroke)
        draw.polygon([pt(0.68, 0.2), pt(0.84, 0.16), pt(0.8, 0.32)], fill=color)
        draw.line([pt(0.14, 0.84), pt(0.34, 0.8)], fill=color, width=stroke)
    elif name == "delete":
        draw.rounded_rectangle(
            [pt(0.26, 0.28), pt(0.74, 0.88)],
            radius=s * 0.05,
            outline=color,
            width=stroke,
        )
        draw.line([pt(0.18, 0.22), pt(0.82, 0.22)], fill=color, width=stroke)
        draw.line([pt(0.4, 0.12), pt(0.6, 0.12)], fill=color, width=stroke)
    elif name == "phone":
        draw.rounded_rectangle([pt(.29, .06), pt(.71, .94)], radius=s * .07,
                               outline=color, width=stroke)
        draw.line([pt(.43, .16), pt(.57, .16)], fill=color, width=stroke)
        draw.line([pt(.44, .84), pt(.56, .84)], fill=color, width=stroke)
    elif name == "send":
        draw.polygon([pt(0.08, 0.15), pt(0.92, 0.5), pt(0.08, 0.85)], outline=color)
        draw.line([pt(0.14, 0.5), pt(0.68, 0.5)], fill=color, width=stroke)
    elif name == "details":
        for y in (0.24, 0.5, 0.76):
            draw.ellipse([pt(0.1, y - 0.06), pt(0.22, y + 0.06)], fill=color)
            draw.line([pt(0.34, y), pt(0.9, y)], fill=color, width=stroke)
    elif name == "refresh":
        radius = 0.35 * (s - 1)
        centre = (s - 1) / 2
        draw.arc(
            [pt(0.15, 0.15), pt(0.85, 0.85)],
            start=300,
            end=240,
            fill=color,
            width=stroke,
        )
        angle = math.radians(240)
        end_x = centre + radius * math.cos(angle)
        end_y = centre + radius * math.sin(angle)
        tangent = (-math.sin(angle), math.cos(angle))
        radial = (math.cos(angle), math.sin(angle))
        head = s * 0.18
        half = s * 0.12
        draw.polygon(
            [
                (end_x + head * tangent[0], end_y + head * tangent[1]),
                (end_x + half * radial[0], end_y + half * radial[1]),
                (end_x - half * radial[0], end_y - half * radial[1]),
            ],
            fill=color,
        )
    elif name == "more":
        for x in (0.18, 0.5, 0.82):
            draw.ellipse(
                [pt(x - 0.08, 0.42), pt(x + 0.08, 0.58)],
                fill=color,
            )
    elif name == "close":
        draw.line([pt(0.2, 0.2), pt(0.8, 0.8)], fill=color, width=stroke)
        draw.line([pt(0.8, 0.2), pt(0.2, 0.8)], fill=color, width=stroke)
    elif name == "minimize":
        draw.line([pt(0.18, 0.5), pt(0.82, 0.5)], fill=color, width=stroke)
    elif name in ("pin", "pin_filled"):
        # A thumbtack with a distinct cap, shoulder and needle at small sizes.
        body = [pt(.29, .12), pt(.71, .12), pt(.66, .43),
                pt(.80, .59), pt(.20, .59), pt(.34, .43)]
        if name == "pin_filled":
            draw.polygon(body, fill=color)
        else:
            draw.line(body + body[:1], fill=color, width=stroke, joint="curve")
        draw.line([pt(.5, .59), pt(.5, .90)], fill=color, width=stroke)
    elif name == "maximize":
        draw.rectangle([pt(0.2, 0.2), pt(0.8, 0.8)], outline=color, width=stroke)
    elif name == "restore":
        draw.line([pt(0.35, 0.28), pt(0.35, 0.15), pt(0.85, 0.15), pt(0.85, 0.65), pt(0.72, 0.65)], fill=color, width=stroke)
        draw.rectangle([pt(0.15, 0.35), pt(0.65, 0.85)], outline=color, width=stroke)
    elif name == "resize":
        draw.polygon([pt(0.85, 0.3), pt(0.85, 0.85), pt(0.3, 0.85)], fill=color)
    else:
        raise ValueError(f"unknown desktop glyph: {name}")
    return image.resize((side, side), Image.LANCZOS)


__all__ = [
    "WINDOW_CHROME", "glyph_html", "window_chrome_css", "window_controls_html",
    "FOUR_CUBE_CELLS",
    "cube_mark_css",
    "cube_mark_html",
    "draw_four_cube_mark",
    "make_four_cube_icon",
    "make_glyph_icon",
]
