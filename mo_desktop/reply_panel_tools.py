"""ReplyBubble's attachment panel-tool lane: tool/chip/crop/live-preview
drawing and glyphs."""
from __future__ import annotations

from typing import Any
from mo_desktop.image_crop import draw_crop_overlay
from mo_desktop.design import (
    PanelState,
)


# glyph + label. Add a tool here and in interface.desktop_brand's glyph owner so
# the grid stays scalable without copying icon geometry into this panel.
_PANEL_TOOLS: tuple[tuple[str, str], ...] = (
    ("crop", "Crop"),
    ("resize", "Resize"),
    ("rotate", "Rotate"),
    ("flip", "Flip"),
    ("convert", "Convert"),
    ("send", "Send"),
)

# Parametric quick tools (Phase 2): a tile here opens a one-tap chip row in the same
# panel instead of firing straight. ``arg`` is what the owner's ``_on_panel_tool``
# receives (resize=percent, rotate=clockwise degrees, convert=target format). Tools NOT
# listed (crop, flip) have no chips — they fire the seam directly and their own phase
# owns the interaction. Add a parametric tool by adding a row here; the render/hit/click
# machinery is generic.
_PANEL_TOOL_CHIPS: dict[str, tuple[tuple[str, str], ...]] = {
    "resize": (("25", "25%"), ("50", "50%"), ("75", "75%")),
    "rotate": (("-90", "Left"), ("90", "Right"), ("180", "180°")),
    "flip": (("h", "Horizontal"), ("v", "Vertical")),
    "convert": (("png", "PNG"), ("jpg", "JPG"), ("webp", "WEBP")),
}
# Visual tools keep the image on screen in chip mode and transform it live while a chip is
# hovered (Phase 3) — the transform is an on-demand PIL op on the small preview thumbnail,
# so idle cost is zero. The commit still goes through core/imageedit on click.
_PANEL_LIVE_TOOLS = frozenset({"rotate", "flip"})
_PANEL_TOOL_LABELS = {name: label for name, label in _PANEL_TOOLS}


class ReplyPanelToolsMixin:
    """Verbatim extraction from reply_bubble.py; state and composition stay
    with the host class."""

    def _panel_tools_layout(self, w: int, ss: int, preview_h: int = 0) -> dict:
        """Grid geometry for the panel-tools morph, in supersampled px. ``w`` is the slot
        width; ``preview_h`` is the live image height (visual tools keep the image on
        screen). Shared by _render (card sizing) and _draw_panel_tools so the two never
        drift. Purely arithmetic — safe to call for sizing before any drawing."""
        gap = 8 * ss
        header_h = 22 * ss
        active = getattr(self, "_panel_tool_active", "")
        if active == "crop" and int(preview_h) > 0:
            foot_h = 30 * ss
            img_h = int(preview_h)
            return {"mode": "crop", "active": "crop", "gap": gap, "header_h": header_h,
                    "img_h": img_h, "foot_h": foot_h,
                    "total_h": header_h + gap + img_h + gap + foot_h}
        if active and active in _PANEL_TOOL_CHIPS:
            chips = _PANEL_TOOL_CHIPS[active]
            chip_h = 32 * ss
            n = max(1, len(chips))
            chip_w = (int(w) - (n - 1) * gap) // n
            live = active in _PANEL_LIVE_TOOLS and int(preview_h) > 0
            img_h = (int(preview_h) + gap) if live else 0
            return {"mode": "live" if live else "chips", "active": active, "chips": chips,
                    "gap": gap, "header_h": header_h, "chip_w": chip_w, "chip_h": chip_h,
                    "img_h": img_h, "total_h": header_h + gap + img_h + chip_h}
        tools = _PANEL_TOOLS
        min_tile = 76 * ss
        cols = max(2, min(3, max(1, (int(w) + gap) // (min_tile + gap))))
        cols = min(cols, len(tools)) or 1
        rows = (len(tools) + cols - 1) // cols
        tile_w = (int(w) - (cols - 1) * gap) // cols
        tile_h = 52 * ss
        grid_h = rows * tile_h + max(0, rows - 1) * gap
        return {"mode": "grid", "tools": tools, "gap": gap, "header_h": header_h,
                "cols": cols, "rows": rows, "tile_w": tile_w, "tile_h": tile_h,
                "grid_h": grid_h, "total_h": header_h + gap + grid_h}

    def _panel_icon(self, name: str, ss: int, color: tuple[int, int, int], size: int = 13) -> Any:
        """Reuse the Desktop glyph geometry at the panel's active render scale."""
        from interface.desktop_brand import make_glyph_icon

        key = (name, size, ss, color)
        cache = getattr(self, "_panel_icon_cache", None)
        if cache is None:
            cache = self._panel_icon_cache = {}
        if key not in cache:
            if len(cache) >= 24:
                cache.clear()
            cache[key] = make_glyph_icon(name, size * ss,
                                        color=f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}")
        return cache[key]

    def _draw_panel_shortcut(self, d: Any, img: Any, key: str, icon: str,
                             label: str, right: int, top: int, ss: int) -> int:
        """Draw one themed, keyboard-reachable shortcut; return its left edge."""
        pad = 6 * ss
        glyph = 13 * ss
        height = 22 * ss
        width = 2 * pad + glyph + (4 * ss + int(d.textlength(label, font=self._sfont)) if label else 0)
        left = right - width
        selected = self._hovering(key)
        ink = self._cyan if selected else self._text
        d.rounded_rectangle([left, top, right, top + height],
                            radius=min(height // 2, int(self._visuals.metrics.button_corner_radius) * ss),
                            fill=(*self._entry, 255),
                            outline=(*(self._cyan if selected else self._edge), 255),
                            width=max(1, ss))
        img.alpha_composite(self._panel_icon(icon, ss, ink), (left + pad, top + (height - glyph) // 2))
        d.text((left + pad + glyph + 4 * ss, top + 4 * ss), label,
               font=self._sfont, fill=(*ink, 255))
        self._hit[key] = (left // ss, top // ss, right // ss, (top + height) // ss)
        return left

    def _draw_panel_preview_actions(self, d: Any, img: Any, x: int, py: int,
                                     slot_w: int, ph: int, ss: int) -> None:
        """One compact action row below the unobstructed image."""
        if self._panel_state != PanelState.IMAGE or not bool(getattr(self, "_attachment_tools_allowed", True)):
            return
        show_tools = bool(getattr(self, "_panel_tools_enabled", False))
        show_share = callable(getattr(self, "_on_panel_share", None))
        right = x + slot_w
        top = py + ph + 6 * ss
        left = self._draw_panel_shortcut(d, img, "panel_copy_path", "copy",
                                         "", right, top, ss)
        right = self._draw_panel_shortcut(d, img, "panel_open_folder", "folder",
                                         "", left - 5 * ss, top, ss) - 5 * ss
        if show_tools:
            right = self._draw_panel_shortcut(d, img, "panel_tools", "tools",
                                               "", right, top, ss) - 5 * ss
        if show_share:
            right = self._draw_panel_shortcut(d, img, "panel_share", "share",
                                              "", right, top, ss) - 5 * ss
        if callable(getattr(self, "_on_panel_send", None)):
            self._draw_panel_shortcut(d, img, "panel_send", "send", "Send to MO", right, top, ss)

    def _draw_panel_tools(self, d: Any, img: Any, x: int, y: int, w: int, ss: int,
                          preview: Any = None) -> None:
        """Render the morphed preview slot: a "<" back header plus either the edit-tools
        grid, a parametric tool's one-tap chip row, or (visual tools) a live-transforming
        image + chips. Tiles register ``panel_tool:<name>`` and chips
        ``panel_chip:<tool>:<arg>``; both route through ``_on_panel_tool`` (companion wires
        it; unset -> inert)."""
        pv_h = preview.size[1] if preview is not None else 0
        tl = self._panel_tools_layout(w, ss, preview_h=pv_h)
        bh = tl["header_h"]
        back_hover = self._hovering("panel_tools_back")
        bcol = self._cyan if back_hover else self._text
        img.alpha_composite(self._panel_icon("back", ss, bcol),
                            (x + 2 * ss, y + (bh - 13 * ss) // 2))
        self._hit["panel_tools_back"] = (int(x / ss), int(y / ss),
                                         int((x + 32 * ss) / ss), int((y + bh) / ss))
        if tl["mode"] == "crop":
            self._draw_panel_crop(d, img, x, y, w, ss, tl, preview)
            return
        if tl["mode"] in {"chips", "live"}:
            self._draw_panel_chips(d, img, x, y, w, ss, tl, preview)
            return
        d.text((x + 22 * ss, y + 3 * ss), "Edit tools", font=self._sfont, fill=(*bcol, 255))
        gy = y + bh + tl["gap"]
        for idx, (name, label) in enumerate(tl["tools"]):
            r, c = divmod(idx, tl["cols"])
            tx = x + c * (tl["tile_w"] + tl["gap"])
            tyy = gy + r * (tl["tile_h"] + tl["gap"])
            hover = self._hovering(f"panel_tool:{name}")
            d.rounded_rectangle([tx, tyy, tx + tl["tile_w"], tyy + tl["tile_h"]],
                                radius=int(self._visuals.metrics.button_corner_radius) * ss,
                                fill=(*self._entry, 255),
                                outline=(*(self._cyan if hover else self._edge), 255),
                                width=max(1, ss))
            glyph = self._panel_icon(name, ss, self._cyan if hover else self._text, 18)
            img.alpha_composite(glyph,
                                (int(tx + (tl["tile_w"] - glyph.width) / 2), int(tyy + 8 * ss)))
            lw = int(d.textlength(label, font=self._sfont))
            d.text((tx + (tl["tile_w"] - lw) / 2, tyy + tl["tile_h"] - 16 * ss), label,
                   font=self._sfont, fill=(*self._text, 255))
            self._hit[f"panel_tool:{name}"] = (int(tx / ss), int(tyy / ss),
                                               int((tx + tl["tile_w"]) / ss),
                                               int((tyy + tl["tile_h"]) / ss))

    def _draw_panel_chips(self, d: Any, img: Any, x: int, y: int, w: int, ss: int,
                          tl: dict, preview: Any = None) -> None:
        """A parametric tool's one-tap chip row (resize/rotate/flip/convert). The header
        names the tool; each chip registers ``panel_chip:<tool>:<arg>`` and fires the seam.
        For a visual tool (mode 'live') the image stays above the chips and transforms live
        while a chip is hovered — commit still goes through core/imageedit on click."""
        active = tl["active"]
        d.text((x + 22 * ss, y + 3 * ss), _PANEL_TOOL_LABELS.get(active, active.title()),
               font=self._sfont, fill=(*self._muted, 255))
        cy0 = y + tl["header_h"] + tl["gap"]
        if tl.get("mode") == "live" and preview is not None:
            cy0 = self._draw_live_preview(img, d, x, cy0, w, ss, active, preview, tl)
        chip_w, chip_h, gap = tl["chip_w"], tl["chip_h"], tl["gap"]
        for i, (arg, label) in enumerate(tl["chips"]):
            cx0 = x + i * (chip_w + gap)
            key = f"panel_chip:{active}:{arg}"
            hover = self._hovering(key)
            d.rounded_rectangle([cx0, cy0, cx0 + chip_w, cy0 + chip_h],
                                radius=int(self._visuals.metrics.button_corner_radius) * ss,
                                fill=(*self._entry, 255),
                                outline=(*(self._cyan if hover else self._edge), 255),
                                width=max(1, ss))
            lw = int(d.textlength(label, font=self._sfont))
            d.text((cx0 + (chip_w - lw) / 2, cy0 + (chip_h - 13 * ss) / 2), label,
                   font=self._sfont, fill=(*(self._cyan if hover else self._text), 255))
            self._hit[key] = (int(cx0 / ss), int(cy0 / ss),
                              int((cx0 + chip_w) / ss), int((cy0 + chip_h) / ss))

    def _draw_live_preview(self, img: Any, d: Any, x: int, y: int, w: int, ss: int,
                           tool: str, preview: Any, tl: dict) -> int:
        """Composite the preview image (transformed live when a chip of ``tool`` is hovered)
        centered in the slot; returns the y below it for the chip row. On-demand: the
        transform is a single PIL op on the small thumbnail, only while a chip is hovered."""
        surf = preview
        hv = str(getattr(self, "_hover", "") or "")
        pfx = f"panel_chip:{tool}:"
        if hv.startswith(pfx):
            surf = self._live_preview_transform(preview, tool, hv[len(pfx):])
        box_w, box_h = preview.size
        if surf.size != (box_w, box_h):          # rotate can swap dims — fit the box, no jump
            from PIL import Image
            surf = surf.copy()
            surf.thumbnail((box_w, box_h), Image.LANCZOS)
        pxc = x + max(0, (int(w) - surf.size[0]) // 2)
        img.alpha_composite(surf, (int(pxc), int(y)))
        d.rounded_rectangle([pxc, y, pxc + surf.size[0], y + surf.size[1]],
                            radius=int(self._visuals.metrics.panel_corner_radius) * ss,
                            outline=(*self._edge, 200), width=max(1, ss))
        return y + box_h + tl["gap"]

    def _live_preview_transform(self, img: Any, tool: str, arg: str) -> Any:
        """The same transform the commit applies, for the on-screen preview (display only).
        Mirrors core/imageedit: rotate is clockwise, flip mirrors h/v."""
        from PIL import Image
        if tool == "flip":
            if arg == "h":
                return img.transpose(Image.FLIP_LEFT_RIGHT)
            if arg == "v":
                return img.transpose(Image.FLIP_TOP_BOTTOM)
        elif tool == "rotate":
            try:
                deg = int(arg)
            except (TypeError, ValueError):
                deg = 0
            if deg:
                return img.rotate(-deg, expand=True)
        return img

    def _draw_panel_crop(self, d: Any, img: Any, x: int, y: int, w: int, ss: int,
                         tl: dict, preview: Any) -> None:
        """Interactive crop: the image with a drag-rectangle selection + an apply button.
        Stores the shown image's window rect in ``_crop_disp_box`` so the pointer handlers
        and the commit map display px -> source px via ``CropSelection``."""
        d.text((x + 22 * ss, y + 3 * ss), "Crop", font=self._sfont, fill=(*self._muted, 255))
        pw, ph = preview.size
        pcx = x + max(0, (int(w) - pw) // 2)
        py_img = y + tl["header_h"] + tl["gap"]
        img.alpha_composite(preview, (int(pcx), int(py_img)))
        d.rounded_rectangle([pcx, py_img, pcx + pw, py_img + ph],
                            radius=int(self._visuals.metrics.panel_corner_radius) * ss,
                            outline=(*self._edge, 200), width=max(1, ss))
        db = (pcx / ss, py_img / ss, (pcx + pw) / ss, (py_img + ph) / ss)   # window coords
        self._crop_disp_box = db
        rect_win = self._crop.rect_display(bounds=db)
        if rect_win is not None:
            rc = tuple(v * ss for v in rect_win)
            draw_crop_overlay(img, d, (pcx, py_img, pcx + pw, py_img + ph), rc,
                              dim_rgba=(6, 8, 12, 150), line_rgb=self._cyan, ss=ss)
        else:
            hint = "Drag to select"
            hw = int(d.textlength(hint, font=self._sfont))
            d.text((pcx + (pw - hw) // 2, py_img + ph // 2 - 6 * ss), hint,
                   font=self._sfont, fill=(*self._text, 210))
        has_sel = rect_win is not None
        fy = py_img + ph + tl["gap"]
        fh = tl["foot_h"] - 6 * ss
        label = "Crop"
        lw = int(d.textlength(label, font=self._sfont))
        padp = 12 * ss
        rx1 = x + int(w)
        rx0 = rx1 - lw - 2 * padp
        d.rounded_rectangle([rx0, fy, rx1, fy + fh],
                            radius=int(self._visuals.metrics.button_corner_radius) * ss,
                            fill=(*(self._cyan if has_sel else self._edge), 255),
                            outline=(*self._cyan, 255), width=max(1, ss))
        d.text((rx0 + padp, fy + (fh - 13 * ss) / 2), label, font=self._sfont,
               fill=(*(self._text if has_sel else self._muted), 255))
        self._hit["panel_crop_apply"] = (int(rx0 / ss), int(fy / ss),
                                         int(rx1 / ss), int((fy + fh) / ss))
