"""ReplyBubble's session-history and dashboard renderers and accessible hit labels."""
from __future__ import annotations

from typing import Any
from mo_desktop import card
from mo_desktop.design import (
    DEFAULT_BUBBLE_DESIGN,
    DEFAULT_DESKTOP_PANEL_DESIGN,
)


_SS = card.SS
_BLUR_CARD_ALPHA = 226     # row 32: matches reply_bubble.BLUR_CARD_ALPHA


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
        clipboard = getattr(self, "_list_view", "") == "clipboard"
        draw.text(
            (left, accent_y + 12 * ss),
            "Clipboard" if clipboard else "Conversations",
            font=self._bfont,
            fill=(*self._text, 255),
        )
        if clipboard:
            count_label = f"{len(items)} · memory only" if items else "Empty · memory only"
        else:
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
                "Copy something and it shows here." if clipboard else "Start a new conversation to create history.",
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
            # Row buttons (clipboard: ask MO, remove) sit at the right; their hits come
            # before the row's, so a click on one never restores the row.
            reserved = 0
            from interface.desktop_brand import make_glyph_icon
            for action, glyph in (("remove", "close"), ("ask", "send")):
                if action not in tuple(item.get("actions") or ()):
                    continue
                action_key = f"session:{action}:{index}"
                size = 14 * ss
                ax = right - (12 + reserved) * ss - size
                ay = (y + y1) // 2 - size // 2
                color = self._cyan if self._hovering(action_key) or action == "ask" else self._muted
                image.alpha_composite(make_glyph_icon(glyph, size, color="#%02x%02x%02x" % color), (ax, ay))
                self._hit[action_key] = (int((ax - 5 * ss) / ss), int(y / ss), int((ax + size + 5 * ss) / ss), int(y1 / ss))
                reserved += 24
            thumbnail = item.get("thumbnail")
            if thumbnail is not None:
                thumb = thumbnail.resize((thumbnail.width * ss, thumbnail.height * ss))
                tx = right - (12 + reserved) * ss - thumb.width
                image.alpha_composite(thumb.convert("RGBA"), (tx, (y + y1) // 2 - thumb.height // 2))
                reserved += thumbnail.width + 10
            text_w = content_w * ss - 24 * ss - reserved * ss
            title = self._fit_plain(
                draw,
                str(item.get("title") or "Untitled conversation"),
                text_w,
                self._font,
            )
            detail = self._fit_plain(
                draw,
                str(item.get("detail") or ""),
                text_w,
                self._sfont,
            )
            draw.text((left + 10 * ss, y + 7 * ss), title, font=self._font, fill=(*self._text, 255))
            draw.text((left + 10 * ss, y + 25 * ss), detail, font=self._sfont, fill=(*self._muted, 255))
            if current and not reserved:
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
        label = "Clear all" if clipboard else "New"
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
            shadow_alpha=0 if self._blur_enabled() else int(design.shadow_alpha), shadow_blur=int(design.shadow_blur),
            fill_alpha=_BLUR_CARD_ALPHA if self._blur_enabled() else 255)
        self._hit = hits
        return self._draw_keyboard_focus(img, hits)

    def _accessible_hit_label(self, key: str) -> str:
        if key.startswith("role:"):
            return self._role_choices[int(key.split(":", 1)[1])] or "Default role"
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
            "search_cycle": (f"Searching {provider_label}; switch search" if provider_label
                             else "MO chat; switch to a search"),
            "dots_up": "Earlier message",
            "dots_down": "Later message, or back to your draft",
            "attach": "Attach a file",
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
        clipboard = getattr(self, "_list_view", "") == "clipboard"
        if clipboard and key == "session:new":
            return "Clear the clipboard history"
        if key in names:
            return names[key]
        if key.startswith(("session:ask:", "session:remove:")):
            action, _, raw = key[len("session:"):].partition(":")
            try:
                title = str((getattr(self, "_session_history_items", None) or [])[int(raw)].get("title") or "item")[:52]
            except (IndexError, TypeError, ValueError):
                return ""
            return ("Ask MO about " if action == "ask" else "Remove ") + title
        if key.startswith("session:"):
            try:
                item = (getattr(self, "_session_history_items", None) or [])[
                    int(key.partition(":")[2])
                ]
                if clipboard:
                    return "Copy again: " + str(item.get("title") or "item")[:52]
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
