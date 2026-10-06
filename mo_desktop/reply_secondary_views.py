"""ReplyBubble's session-history and dashboard renderers and accessible hit labels."""
from __future__ import annotations

from typing import Any
from mo_desktop import card
from mo_desktop.design import (
    DEFAULT_BUBBLE_DESIGN,
    DEFAULT_DESKTOP_PANEL_DESIGN,
)


_SS = card.SS



def _plain_card_row(img: Any, below: int, pad: int) -> Any:
    """A one-pixel row of the card with nothing drawn inside its borders, at or above ``below``
    (a glyph's descender must not be repeated down the grown card)."""
    inner = (pad + 3, img.width - pad - 3)
    for y in range(below, max(pad, below - 40), -1):
        row = img.crop((inner[0], y, inner[1], y + 1))
        if len(set(row.getdata())) == 1:
            return img.crop((0, y, img.width, y + 1))
    return img.crop((0, below, img.width, below + 1))
class ReplySecondaryViewsMixin:
    """Verbatim extraction from reply_bubble.py; state and composition stay
    with the host class."""

    @staticmethod
    def _fit_plain(draw: Any, text: str, width: int, font: Any) -> str:
        from mo_desktop.card import fit_text

        return fit_text(draw, text, width, font)

    def _render_status(self) -> Any:
        """One line: MO's four-cube mark and its current step, sized to the text."""
        from PIL import ImageDraw

        design = getattr(self, "_design", DEFAULT_BUBBLE_DESIGN)
        panel = getattr(self, "_panel_design", DEFAULT_DESKTOP_PANEL_DESIGN)
        ss = int(getattr(self, "_ss", _SS) or _SS)
        self._font, self._bfont, self._sfont, self._ifont = self._fonts(ss)
        visuals = self._visuals
        pad_x = int(visuals.metrics.panel_padding)
        shadow_pad = int(design.shadow_pad)
        mark_w, gap, card_h = 12, 10, 44
        probe = ImageDraw.Draw(card.new_canvas(10, 10))
        text_room = max(24, int(panel.max_width) - 2 * pad_x - mark_w - gap)
        shown = card.fit_text(probe, str(getattr(self, "_status_text", "") or ""), text_room * ss, self._font)
        text_w = int(probe.textlength(shown, font=self._font) / ss) + 1
        card_w = max(int(panel.min_width), min(int(panel.max_width), 2 * pad_x + mark_w + gap + text_w))
        width = (card_w + 2 * shadow_pad) * ss
        height = (card_h + 2 * shadow_pad) * ss
        pad = shadow_pad * ss
        box = (pad, pad, width - pad, height - pad)
        image = card.draw_card(
            card.new_canvas(width, height),
            box,
            radius=int(visuals.metrics.panel_corner_radius) * ss,
            fill=(*self._card, 255),
            edge=(*self._edge, 255),
            edge_highlight=(*self._cyan, 110),
            edge_width=max(1, ss),
            shadow_alpha=int(design.shadow_alpha),
            shadow_blur=int(design.shadow_blur) * ss,
            shadow_dy=6 * ss,
        )
        from interface.desktop_brand import make_four_cube_icon

        draw = ImageDraw.Draw(image)
        mark_x = box[0] + pad_x * ss
        # MO's canonical four-cube mark, in the live skin, never a hand-drawn copy.
        mark = make_four_cube_icon(16 * ss, palette=visuals.palette)
        image.alpha_composite(mark, (int(mark_x - 2 * ss), int(box[1] + (card_h // 2 - 8) * ss)))
        ascent, descent = self._font.getmetrics()
        text_y = box[1] + (card_h * ss - (ascent + descent)) // 2
        draw.text((mark_x + (mark_w + gap) * ss, text_y), shown, font=self._font, fill=(*self._text, 255))
        self._hit = {}
        return card.finish(image, ss)

    def _render_session_history(self) -> Any:
        """Draw a bounded conversation picker in the existing reply-panel footprint."""
        from PIL import ImageDraw

        design = getattr(self, "_design", DEFAULT_BUBBLE_DESIGN)
        ss = int(getattr(self, "_ss", _SS) or _SS)
        self._font, self._bfont, self._sfont, self._ifont = self._fonts(ss)
        content_w = max(
            int(design.content_width),
            int(getattr(design, "reply_content_width", 0) or 0),
        )
        visuals = self._visuals
        pad_x = int(visuals.metrics.panel_padding)
        shadow_pad = int(design.shadow_pad)
        row_h = 48
        row_gap = 6
        header_h = 45
        footer_h = 32
        per_page = 4
        items = list(getattr(self, "_session_history_items", []) or [])
        pages = max(1, (len(items) + per_page - 1) // per_page)
        page = max(0, min(int(getattr(self, "_session_history_page", 0) or 0), pages - 1))
        self._session_history_page = page
        start = page * per_page
        shown = items[start:start + per_page]
        rows_h = max(48, len(shown) * row_h + max(0, len(shown) - 1) * row_gap)
        card_w = content_w + 2 * pad_x
        card_h = int(design.accent_top) + 3 + header_h + rows_h + footer_h + 17
        width = (card_w + 2 * shadow_pad) * ss
        height = (card_h + 2 * shadow_pad) * ss
        pad = shadow_pad * ss
        box = [pad, pad, width - pad, height - pad]
        image = card.new_canvas(width, height)
        image = card.draw_card(
            image,
            tuple(box),
            radius=int(visuals.metrics.panel_corner_radius) * ss,
            fill=(*self._card, 255),
            edge=(*self._edge, 255),
            edge_highlight=(*self._cyan, 110),
            edge_width=max(1, ss),
            shadow_alpha=int(design.shadow_alpha),
            shadow_blur=int(design.shadow_blur) * ss,
            shadow_dy=6 * ss,
        )
        draw = ImageDraw.Draw(image)
        left = pad + pad_x * ss
        right = left + content_w * ss
        accent_y = pad + int(design.accent_top) * ss
        draw.rounded_rectangle(
            [left, accent_y, left + 26 * ss, accent_y + 3 * ss],
            radius=ss,
            fill=(*self._cyan, 255),
        )
        draw.text(
            (left, accent_y + 12 * ss),
            "Conversations",
            font=self._bfont,
            fill=(*self._text, 255),
        )
        count_label = f"{len(items)} saved" if items else "No saved conversations"
        count_w = int(draw.textlength(count_label, font=self._sfont))
        draw.text(
            (right - count_w, accent_y + 14 * ss),
            count_label,
            font=self._sfont,
            fill=(*self._muted, 255),
        )
        self._hit = {}
        y = accent_y + header_h * ss
        button_radius = int(visuals.metrics.button_corner_radius) * ss
        if not shown:
            draw.text(
                (left, y + 14 * ss),
                "Start a new conversation to create history.",
                font=self._sfont,
                fill=(*self._muted, 255),
            )
        for offset, item in enumerate(shown):
            index = start + offset
            key = f"session:{index}"
            y1 = y + row_h * ss
            hover = self._hovering(key)
            current = bool(item.get("current"))
            draw.rounded_rectangle(
                [left, y, right, y1],
                radius=button_radius,
                fill=(*self._entry, 255),
                outline=(*(self._cyan if hover or current else self._edge), 255),
                width=max(1, ss) * (2 if hover else 1),
            )
            title = self._fit_plain(
                draw,
                str(item.get("title") or "Untitled conversation"),
                content_w * ss - 24 * ss,
                self._font,
            )
            detail = self._fit_plain(
                draw,
                str(item.get("detail") or ""),
                content_w * ss - 24 * ss,
                self._sfont,
            )
            draw.text((left + 10 * ss, y + 7 * ss), title, font=self._font, fill=(*self._text, 255))
            draw.text((left + 10 * ss, y + 25 * ss), detail, font=self._sfont, fill=(*self._muted, 255))
            if current:
                draw.ellipse(
                    [right - 14 * ss, y + 10 * ss, right - 8 * ss, y + 16 * ss],
                    fill=(*self._cyan, 255),
                )
            self._hit[key] = (
                int(left / ss),
                int(y / ss),
                int(right / ss),
                int(y1 / ss),
            )
            y = y1 + row_gap * ss

        footer_y = box[3] - footer_h * ss
        back_key = "session:back"
        back_color = self._cyan if self._hovering(back_key) else self._muted
        from interface.desktop_brand import make_glyph_icon
        draw.rounded_rectangle((left, footer_y+2*ss, left+62*ss, footer_y+25*ss),
            radius=self._visuals.metrics.button_corner_radius*ss,
            fill=(*self._entry, 255), outline=(*(self._cyan if self._hovering(back_key) else self._edge), 255), width=ss)
        image.alpha_composite(make_glyph_icon("back", 13*ss, color="#%02x%02x%02x" % back_color),
                            (left+7*ss, footer_y+7*ss))
        draw.text((left + 26 * ss, footer_y + 6 * ss), "Back", font=self._sfont, fill=(*back_color, 255))
        self._hit[back_key] = (
            int(left / ss), int(footer_y / ss), int((left + 62 * ss) / ss),
            int((footer_y + 26 * ss) / ss),
        )
        center_x = (left + right) // 2
        if pages > 1:
            prev_key, next_key = "session:prev", "session:next"
            prev_color = self._cyan if self._hovering(prev_key) else self._muted
            next_color = self._cyan if self._hovering(next_key) else self._muted
            self._chevron(draw, center_x - 28 * ss, footer_y + 13 * ss, 4 * ss, "left", prev_color, ss)
            page_label = f"{page + 1}/{pages}"
            page_w = int(draw.textlength(page_label, font=self._sfont))
            draw.text(
                (center_x - page_w // 2, footer_y + 6 * ss),
                page_label,
                font=self._sfont,
                fill=(*self._muted, 255),
            )
            self._chevron(draw, center_x + 28 * ss, footer_y + 13 * ss, 4 * ss, "right", next_color, ss)
            self._hit[prev_key] = (
                int((center_x - 44 * ss) / ss), int(footer_y / ss),
                int((center_x - 12 * ss) / ss), int((footer_y + 26 * ss) / ss),
            )
            self._hit[next_key] = (
                int((center_x + 12 * ss) / ss), int(footer_y / ss),
                int((center_x + 44 * ss) / ss), int((footer_y + 26 * ss) / ss),
            )
        new_key = "session:new"
        label = "New"
        label_w = int(draw.textlength(label, font=self._sfont))
        button_pad = int(visuals.metrics.button_padding) * ss
        new_right = right
        new_left = new_right - label_w - 2 * button_pad
        draw.rounded_rectangle(
            [new_left, footer_y + 1 * ss, new_right, footer_y + 25 * ss],
            radius=button_radius,
            fill=(*self._cyan, 255),
            outline=(*self._text, 255) if self._hovering(new_key) else None,
            width=max(1, ss),
        )
        draw.text(
            (new_left + button_pad, footer_y + 6 * ss),
            label,
            font=self._sfont,
            fill=(*getattr(self, "_accent_ink", self._card), 255),
        )
        self._hit[new_key] = (
            int(new_left / ss), int(footer_y / ss), int(new_right / ss),
            int((footer_y + 27 * ss) / ss),
        )
        finished = card.finish(image, ss)
        return self._draw_keyboard_focus(finished, self._hit)

    def _render_dashboard(self) -> Any:
        """Draw the one compact Desktop dashboard through its pure renderer."""
        design = getattr(self, "_design", DEFAULT_BUBBLE_DESIGN)
        ss = int(getattr(self, "_ss", _SS) or _SS)
        self._font, self._bfont, self._sfont, self._ifont = self._fonts(ss)
        from mo_desktop.dashboard_card import render_dashboard_card

        palette = {"card": self._card, "edge": self._edge, "text": self._text, "muted": self._muted,
                   "cyan": self._cyan, "entry": getattr(self, "_entry", self._edge), "ink": self._card,
                   "ok": getattr(self, "_ok", self._cyan), "amber": getattr(self, "_amber", self._cyan),
                   "err": getattr(self, "_err", self._cyan)}
        img, hits = render_dashboard_card(
            data=dict(getattr(self, "_dashboard_data", None) or {}), palette=palette,
            fonts=(self._font, self._bfont, self._sfont, self._ifont), ss=ss,
            visuals=self._visuals, shadow_pad=int(design.shadow_pad),
            shadow_alpha=int(design.shadow_alpha), shadow_blur=int(design.shadow_blur))
        img, hits = self._fit_dashboard_face(img, hits, int(design.shadow_pad))
        self._hit = hits
        return self._draw_keyboard_focus(img, hits)

    def _dashboard_column_height(self) -> int:
        """The right column a docked Dashboard spans with Focus open: from its own top (the
        composer's, else the upper-right cube's) to Focus's bottom; 0 when it keeps its height."""
        if getattr(self, "_face", "panel") != "dashboard":
            return 0
        focus = getattr(self._cube, "_focus_controller", None)
        if focus is None or getattr(focus, "_collapsed", False):
            return 0
        top = self.cube_extent()[1]
        bottom = focus._face_offset[1] + focus._target_size[1]
        return max(0, int(round(bottom - top)))

    def _fit_dashboard_face(self, img: Any, hits: dict, pad: int) -> tuple[Any, dict]:
        """Fit the docked Dashboard to the column beside it: keep the card's own rounded top
        and bottom, show the scrolled middle, fade the cut, and drop controls scrolled away."""
        target = self._dashboard_column_height()
        card_h = img.height - 2 * pad
        if not target or card_h == target or target < 140:
            self._dashboard_scroll = self._dashboard_max_scroll = 0
            return img, hits
        from PIL import Image

        cap = int(self._visuals.metrics.panel_corner_radius) + 2
        if card_h < target:
            # Shorter card: grow it to the column with its own plain bottom row, so the
            # composer, Focus and Dashboard end on one line.
            self._dashboard_scroll = self._dashboard_max_scroll = 0
            width = img.width
            out = Image.new("RGBA", (width, target + 2 * pad), (0, 0, 0, 0))
            split = pad + card_h - cap
            out.paste(img.crop((0, 0, width, split)), (0, 0))
            plain = _plain_card_row(img, split - 1, pad)
            for y in range(split, pad + target - cap):
                out.paste(plain, (0, y))
            out.paste(img.crop((0, split, width, img.height)), (0, pad + target - cap))
            return out, hits
        middle = target - 2 * cap
        maximum = card_h - target
        scroll = max(0, min(maximum, int(getattr(self, "_dashboard_scroll", 0) or 0)))
        self._dashboard_scroll, self._dashboard_max_scroll = scroll, maximum
        width = img.width
        out = Image.new("RGBA", (width, target + 2 * pad), (0, 0, 0, 0))
        out.paste(img.crop((0, 0, width, pad + cap)), (0, 0))
        out.paste(img.crop((0, pad + cap + scroll, width, pad + cap + scroll + middle)), (0, pad + cap))
        out.paste(img.crop((0, img.height - pad - cap, width, img.height)), (0, pad + target - cap))
        fade = Image.new("RGBA", out.size, (0, 0, 0, 0))
        steps = 18
        inner = (pad + 2, width - pad - 2)
        for row in range(steps):
            alpha = int(235 * (steps - row) / steps)
            if scroll > 0:      # more above: soften the top seam
                fade.paste((*self._card, alpha), (inner[0], pad + cap + row, inner[1], pad + cap + row + 1))
            if scroll < maximum:  # more below: soften the bottom seam
                y = pad + target - cap - 1 - row
                fade.paste((*self._card, alpha), (inner[0], y, inner[1], y + 1))
        out.alpha_composite(fade)
        low, high = pad + cap + scroll, pad + cap + scroll + middle
        kept = {}
        for key, (x0, y0, x1, y1) in hits.items():
            if y0 >= low and y1 <= high:
                kept[key] = (x0, y0 - scroll, x1, y1 - scroll)
            elif y1 <= pad + cap:
                kept[key] = (x0, y0, x1, y1)
        return out, kept

    def _accessible_hit_label(self, key: str) -> str:
        if key.startswith("role:"):
            return self._role_choices[int(key.split(":", 1)[1])] or "Default role"
        provider = self._composer_search_provider()
        provider_label = self._composer_search_label()
        names = {
            "collapse": "Collapse composer",
            "role": "Conversation role",
            "copy": "Copy reply",
            "action": str(getattr(self, "_footer_action_label", "") or "Action"),
            "send": f"Search {provider_label}" if provider_label else "Send message",
            "reply": "Reply",
            "up": "Previous reply",
            "down": "Next reply",
            "sessions": "Historical conversations",
            "search_google": (
                "Google search selected; activate for MO chat"
                if provider == "google" else "Use Google search"
            ),
            "search_youtube": (
                "YouTube search selected; activate for MO chat"
                if provider == "youtube" else "Use YouTube search"
            ),
            "search_translate": (
                "Google Translate selected; activate for MO chat"
                if provider == "translate" else "Use Google Translate"
            ),
            "session:back": "Back",
            "session:new": "New conversation",
            "session:prev": "Previous conversations",
            "session:next": "Next conversations",
            "options_submit": "Submit options",
            "option:other": "Type another response",
            "panel_tools": "Image tools",
            "panel_share": "Share image with a MO terminal",
            "panel_send": "Send image to MO",
            "panel_open_folder": "Open image folder",
            "panel_copy_path": "Copy image path",
            "panel_tools_back": "Back",
            "panel_crop_apply": "Apply crop",
        }
        if key in names:
            return names[key]
        if key.startswith("session:"):
            try:
                item = (getattr(self, "_session_history_items", None) or [])[
                    int(key.partition(":")[2])
                ]
                return "Open " + str(item.get("title") or "conversation")[:52]
            except (IndexError, TypeError, ValueError):
                return "Conversation"
        if key.startswith("option:"):
            try:
                option = (getattr(self, "_options", None) or [])[int(key.partition(":")[2])]
                return " ".join(str(getattr(option, "label", option) or "Option").split())[:60]
            except (IndexError, TypeError, ValueError):
                return "Option"
        if key.startswith("dash:view:"):
            return "Show dashboard " + key[len("dash:view:"):].replace("_", " ").title()[:40]
        for prefix in ("dash:", "panel_tool:", "panel_chip:"):
            if key.startswith(prefix):
                return key[len(prefix):].replace(":", " ").replace("_", " ").strip().title()[:60]
        return ""
