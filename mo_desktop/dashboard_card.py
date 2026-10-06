"""The mini Dashboard card for the resident MO Desktop panel (row 19, approved v2).

A glance that jumps into MO's main Dashboard app, never a copy of it: three views (Now, You,
System), each with three figures and two short lists, on one fixed-size card ("+N" instead of
growing). Pure PIL on the shared card primitives, the skin's palette and typography roles; it
consumes already-synthesized data, owns no polling or actions, and returns named hit regions.
"""
from __future__ import annotations

from typing import Any

from interface.desktop_ui import DESKTOP_TYPOGRAPHY, DesktopVisualState

CONTENT_W = 300
FACE_HEIGHT = 360
VIEWS = ("overview", "personal", "systems")
_TAB_LABELS = {"overview": "Now", "personal": "You", "systems": "System"}


from interface.desktop_brand import draw_four_cube_mark
from mo_desktop.card import fit_text as _fit


def _role(name: str, ss: int) -> Any:
    from mo_desktop.fonts import load_font

    family, points = getattr(DESKTOP_TYPOGRAPHY, name)
    names = ("seguisb.ttf", "segoeuib.ttf") if "Semibold" in family else ("segoeui.ttf", "arial.ttf")
    return load_font(names, round(points * 4 / 3 * ss))


def render_dashboard_card(
    *,
    data: dict,
    palette: dict,
    fonts: tuple,
    ss: int,
    visuals: DesktopVisualState,
    shadow_pad: int = 28,
    shadow_alpha: int = 150,
    shadow_blur: int = 8,
) -> tuple:
    """Render the selected mini view and its hit regions (``dash:view:*``, ``dash:open`` and each
    row's or chip's ``dash:<hit>``)."""
    from PIL import ImageDraw

    from mo_desktop import card

    _font, bfont, _sfont, _ifont = fonts
    if not isinstance(visuals, DesktopVisualState):
        raise TypeError("dashboard renderer requires DesktopVisualState")
    panel_padding = int(visuals.metrics.panel_padding)
    panel_radius = int(visuals.metrics.panel_corner_radius)
    button_radius = int(visuals.metrics.button_corner_radius)
    C = palette
    view = str(data.get("view") or "overview").strip().lower()
    if view not in VIEWS:
        view = "overview"
    views = data.get("views") if isinstance(data.get("views"), dict) else {}
    payload = views.get(view) if isinstance(views.get(view), dict) else {}
    small, tiny, section, heading = (_role(name, ss) for name in ("small", "tiny", "section", "heading"))

    def mix(a: Any, b: Any, amount: float) -> tuple[int, int, int]:
        return tuple(int(a[index] + (b[index] - a[index]) * amount) for index in range(3))

    tile_c = mix(C["card"], C["text"], 0.06)
    tones = {"good": C["ok"], "attention": C["amber"], "accent": C["cyan"], "muted": C["muted"],
             "neutral": C["cyan"], "quiet": C["muted"]}
    card_w = CONTENT_W + 2 * panel_padding
    card_h = FACE_HEIGHT
    W, H = (card_w + 2 * shadow_pad) * ss, (card_h + 2 * shadow_pad) * ss
    pad = shadow_pad * ss
    box = [pad, pad, W - pad, H - pad]
    image = card.draw_card(card.new_canvas(W, H), tuple(box), radius=panel_radius * ss, fill=(*C["card"], 255),
                           edge=(*C["edge"], 255), edge_highlight=(*C["cyan"], 110), edge_width=max(1, ss),
                           shadow_alpha=shadow_alpha, shadow_blur=shadow_blur * ss, shadow_dy=6 * ss)
    draw = ImageDraw.Draw(image)
    hits: dict[str, tuple[int, int, int, int]] = {}
    left, right = pad + panel_padding * ss, box[2] - panel_padding * ss

    def y(px: float) -> int:
        return pad + round(px * ss)

    def hit(key: str, x0: int, y0: int, x1: int, y1: int) -> None:
        hits[key] = (int(x0 / ss), int(y0 / ss), int(x1 / ss), int(y1 / ss))

    # Header: the four-cube mark and name; the way into the main Dashboard on the right.
    draw_four_cube_mark(draw, left, y(11), cube_size=8 * ss, gap=3 * ss, radius=2 * ss, fill=(*C["cyan"], 255),
                        shade=(*mix(C["cyan"], C["ink"], 0.28), 255), line_width=max(1, ss))
    draw.text((left + 28 * ss, y(10)), "MO", font=bfont, fill=(*C["text"], 255))
    link = "Open Dashboard \u2197"
    link_w = int(draw.textlength(link, font=small))
    draw.text((right - link_w, y(13)), link, font=small, fill=(*C["cyan"], 255))
    hit("dash:open", right - link_w - 6 * ss, y(6), right + 2 * ss, y(32))

    # Three tabs.
    gap = 6 * ss
    tab_w = (right - left - 2 * gap) // 3
    for index, item in enumerate(VIEWS):
        x0, selected = left + index * (tab_w + gap), item == view
        draw.rounded_rectangle([x0, y(42), x0 + tab_w, y(66)], radius=button_radius * ss, fill=(*tile_c, 255),
                               outline=(*(C["cyan"] if selected else C["edge"]), 255), width=max(1, ss))
        label = _TAB_LABELS[item]
        label_w = int(draw.textlength(label, font=small))
        draw.text((x0 + (tab_w - label_w) // 2, y(47)), label, font=small,
                  fill=(*(C["cyan"] if selected else C["muted"]), 255))
        hit(f"dash:view:{item}", x0, y(42), x0 + tab_w, y(66))

    # Three figures.
    tile_w = (right - left - 2 * gap) // 3
    for index, tile in enumerate(list(payload.get("tiles") or [])[:3]):
        x0 = left + index * (tile_w + gap)
        draw.rounded_rectangle([x0, y(76), x0 + tile_w, y(126)], radius=button_radius * ss, fill=(*tile_c, 255),
                               outline=(*C["edge"], 255), width=max(1, ss))
        tone = tones.get(str(tile.get("tone") or "neutral"), C["cyan"])
        draw.rounded_rectangle([x0 + 9 * ss, y(87), x0 + 12 * ss, y(115)], radius=ss, fill=(*tone, 255))
        draw.text((x0 + 19 * ss, y(80)), _fit(draw, str(tile.get("value") or "—"), tile_w - 24 * ss, heading),
                  font=heading, fill=(*C["text"], 255))
        draw.text((x0 + 19 * ss, y(106)), _fit(draw, str(tile.get("label") or ""), tile_w - 24 * ss, tiny),
                  font=tiny, fill=(*C["muted"], 255))

    # Two short lists (rows, or chips for apps).
    top = 138
    for block in list(payload.get("sections") or [])[:2]:
        draw.text((left, y(top)), str(block.get("title") or ""), font=section, fill=(*C["text"], 255))
        more = str(block.get("more") or "")
        if more:
            draw.text((right - draw.textlength(more, font=small), y(top + 1)), more, font=small, fill=(*C["muted"], 255))
        cursor = top + 21
        chips = list(block.get("chips") or [])
        if chips:
            x0 = left
            for chip in chips[:3]:
                label = _fit(draw, str(chip.get("label") or ""), 90 * ss, small)
                chip_w = int(draw.textlength(label, font=small)) + 20 * ss
                draw.rounded_rectangle([x0, y(cursor), x0 + chip_w, y(cursor + 24)], radius=12 * ss, fill=(*tile_c, 255),
                                       outline=(*C["edge"], 255), width=max(1, ss))
                draw.text((x0 + 10 * ss, y(cursor + 4)), label, font=small, fill=(*C["text"], 255))
                if chip.get("hit"):
                    hit("dash:" + str(chip["hit"]), x0, y(cursor), x0 + chip_w, y(cursor + 24))
                x0 += chip_w + 6 * ss
            cursor += 28
        for row in list(block.get("rows") or [])[:2]:
            draw.rounded_rectangle([left, y(cursor), right, y(cursor + 24)], radius=button_radius * ss, fill=(*tile_c, 255),
                                   outline=(*C["edge"], 255), width=max(1, ss))
            tone = tones.get(str(row.get("tone") or "neutral"), C["cyan"])
            draw.ellipse([left + 9 * ss, y(cursor + 9.5), left + 14 * ss, y(cursor + 14.5)], fill=(*tone, 255))
            name = _fit(draw, str(row.get("name") or ""), 110 * ss, section)
            draw.text((left + 21 * ss, y(cursor + 4)), name, font=section, fill=(*C["text"], 255))
            detail_x = left + 21 * ss + int(draw.textlength(name, font=section)) + 7 * ss
            draw.text((detail_x, y(cursor + 5)), _fit(draw, str(row.get("detail") or ""), right - detail_x - 8 * ss, small),
                      font=small, fill=(*C["muted"], 255))
            if row.get("hit"):
                hit("dash:" + str(row["hit"]), left, y(cursor), right, y(cursor + 24))
            cursor += 28
        top = cursor + 8

    chip = payload.get("chip") if isinstance(payload.get("chip"), dict) else None
    if chip:
        label = str(chip.get("label") or "")
        chip_w = int(draw.textlength(label, font=small)) + 24 * ss
        draw.rounded_rectangle([left, y(card_h - 44), left + chip_w, y(card_h - 20)], radius=12 * ss,
                               fill=(*tile_c, 255), outline=(*C["cyan"], 255), width=max(1, ss))
        draw.text((left + 12 * ss, y(card_h - 40)), label, font=small, fill=(*C["cyan"], 255))
        hit("dash:" + str(chip.get("hit") or ""), left, y(card_h - 44), left + chip_w, y(card_h - 20))
    return card.finish(image, ss), hits
