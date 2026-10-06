"""Label, notice, and guidance rendering for the Desktop cube."""

from __future__ import annotations

from mo_desktop.gui_loop import screen_size

import time
from typing import Any

from mo_desktop import card
from mo_desktop.design import (
    DEFAULT_LABEL_BUBBLE_DESIGN,
    guidance_label_design,
    split_token_to_width,
)
from mo_desktop.visual_text import parse_inline_emphasis, plain_spans, shape_line

_DEFAULT_POINT_LABEL = "here"
_NOTICE_GLANCE_SECONDS = 5.0
_NOTICE_HOVER_MAX_SECONDS = 6.0


class CubePanelMixin:
    """Label and notice behavior mixed into the concrete Desktop cube."""

    def show_running_apps(
        self,
        specs: tuple[dict[str, Any], ...],
        activate: Any,
        *,
        heading: str = "",
    ) -> None:
        """Show clickable rows in the existing activity/volume label."""
        if not specs or self._guidance_owns_label():
            return
        if (self._label_kind != "running" and self._label_value
                and time.perf_counter() < self._label_until):
            return
        if self._label_kind != "running" and self._label_value:
            self._hide_label()
        self._cancel_running_hide()
        if (self._label_kind == "running" and specs == getattr(self, "_running_specs", ())
                and str(heading or "") == getattr(self, "_running_heading", "")):
            return
        self._running_specs = specs
        self._running_activate = activate
        self._running_heading = str(heading or "")
        self._label_kind = "running"
        self._label_variant = "glance"
        self._label_value = self._running_heading.casefold() or "running apps"
        self._label_until = float("inf")
        self._label_active_design = self._label_design
        if self._label.set_click_through(False):
            self._refresh_running_label()

    def show_game_session(self, specs: tuple[dict[str, Any], ...], activate: Any) -> None:
        """Show the compact Game Session HUD through the shared skinned row panel."""
        self.show_running_apps(specs, activate, heading="Game Session")

    def _on_label_event(self, kind: str, event: Any) -> None:
        if self._label_kind != "running":
            return
        if kind == "release" and event.num == 1:
            self._on_running_click(event)
        elif kind == "motion":
            self._on_running_motion(event)
        elif kind == "enter":
            self._cancel_running_hide()
        elif kind == "leave":
            self.hide_running_apps()

    def hide_running_apps(self) -> None:
        if self._label_kind == "running" and getattr(self, "_running_hide_after", None) is None:
            self._running_hide_after = self._gui.schedule(300, self._hide_running_now)

    def _cancel_running_hide(self) -> None:
        timer = getattr(self, "_running_hide_after", None)
        if timer is not None:
            self._gui.cancel(timer)
            self._running_hide_after = None

    def _hide_running_now(self) -> None:
        self._running_hide_after = None
        if self._label_kind == "running":
            self._hide_label()

    def _clear_running_label(self) -> None:
        self._cancel_running_hide()
        if getattr(self, "_running_specs", ()) or self._label_kind == "running":
            self._label.set_click_through(True)
        self._running_specs = ()
        self._running_activate = None
        self._running_heading = ""
        self._running_boxes = ()
        self._running_hovered = -1

    def _on_running_click(self, event: Any) -> None:
        if self._label_kind != "running":
            return
        for index, (x0, y0, x1, y1) in enumerate(getattr(self, "_running_boxes", ())):
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                spec = self._running_specs[index]
                activate = self._running_activate
                self._hide_label()
                if callable(activate):
                    activate(spec)
                return

    def _on_running_motion(self, event: Any) -> None:
        if self._label_kind != "running":
            return
        hovered = next((index for index, (x0, y0, x1, y1) in enumerate(self._running_boxes)
                        if x0 <= event.x <= x1 and y0 <= event.y <= y1), -1)
        if hovered != getattr(self, "_running_hovered", -1):
            self._running_hovered = hovered
            self._refresh_running_label()

    def _refresh_running_label(self) -> None:
        if self._label_kind != "running" or not getattr(self, "_running_specs", ()):
            return
        self._label_img = self._render_running_label()
        self._reposition_label()
        self._label_win.show()

    def _render_running_label(self) -> Any:
        from PIL import Image, ImageDraw
        from interface.desktop_brand import make_glyph_icon
        from mo_desktop.fonts import load_font

        ss = card.SS
        design = self._label_design
        font = load_font(("segoeui.ttf", "arial.ttf"), int(design.font_size) * ss)
        heading_font = load_font(("segoeuib.ttf", "arialbd.ttf"), int(design.font_size) * ss)
        measure = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
        max_label_width = min(168, int(design.max_width)) * ss
        heading = str(getattr(self, "_running_heading", "") or "")
        labels = []
        for spec in self._running_specs:
            label = str(spec["label"])
            while label and measure.textlength(label, font=font) > max_label_width:
                label = label[:-2].rstrip() + "…"
            labels.append(label)
        text_width = max((measure.textlength(label, font=font) for label in labels), default=0)
        heading_width = measure.textlength(heading, font=heading_font) if heading else 0
        content_width = max(70, 24 + int((max(text_width, heading_width) + ss - 1) // ss) + 6)
        row_height = max(24, int(design.line_height) + 6)
        heading_height = row_height if heading else 0
        inset = int(self._visuals.metrics.panel_padding)
        pad = int(design.shadow_pad)
        width = content_width + 2 * inset + 2 * pad
        height = heading_height + row_height * len(labels) + 2 * inset + 2 * pad
        box = (pad * ss, pad * ss, (width - pad) * ss, (height - pad) * ss)
        img = card.draw_card(
            card.new_canvas(width * ss, height * ss), box,
            radius=int(self._visuals.metrics.panel_corner_radius) * ss,
            fill=(*self._plate_color(), 245),
            edge=(*self._color_rgb, int(design.edge_alpha)), edge_width=ss,
            edge_highlight=(*self._color_rgb, 110),
            shadow_alpha=int(design.shadow_alpha),
            shadow_blur=int(design.shadow_blur) * ss, shadow_dy=3 * ss,
        )
        self._running_boxes = tuple(
            (pad + inset, pad + inset + heading_height + index * row_height,
             pad + inset + content_width, pad + inset + heading_height + (index + 1) * row_height)
            for index in range(len(labels))
        )
        hovered = getattr(self, "_running_hovered", -1)
        if 0 <= hovered < len(labels):
            highlight = Image.new("RGBA", img.size)
            x0, y0, x1, y1 = self._running_boxes[hovered]
            ImageDraw.Draw(highlight).rounded_rectangle(
                (x0 * ss, y0 * ss, x1 * ss, y1 * ss),
                radius=int(self._visuals.metrics.button_corner_radius) * ss,
                fill=(*self._color_rgb, 42),
            )
            img = Image.alpha_composite(img, highlight)
        draw = ImageDraw.Draw(img)
        if heading:
            draw.text(((pad + inset + 1) * ss, (pad + inset + 3) * ss), heading,
                      font=heading_font, fill=(*self._label_text_rgb, 255))
        for index, (spec, label) in enumerate(zip(self._running_specs, labels)):
            x0, y0, _x1, _y1 = self._running_boxes[index]
            color = str(spec["color"])
            icon = make_glyph_icon(str(spec["glyph"]), 16, color=color)
            img.alpha_composite(icon.resize((16 * ss, 16 * ss)),
                                ((x0 + 1) * ss, (y0 + (row_height - 16) // 2) * ss))
            draw.text(((x0 + 23) * ss, (y0 + 4) * ss), label, font=font,
                      fill=(*self._label_text_rgb, 255))
        return card.finish(img, ss)

    def show_bubble(self, text: str, seconds: float = 2.6, side: str | None = None) -> None:
        """Show a small speech bubble next to the cubes (e.g. 'what's up?')."""
        now = time.perf_counter()
        duration = max(0.5, float(seconds or 2.6))
        if self._guidance_owns_label(now):
            pending = getattr(self, "_pending_glance", None)
            if pending is None or pending[4] != "notice":
                self._pending_glance = (str(text or ""), duration, side, "", "bubble")
            return
        self._show_glance(str(text or ""), duration, side=side, kind="bubble")

    def show_notice(
        self,
        title: str,
        detail: str = "",
        seconds: float = 3.0,
        *,
        activate: Any = None,
        tone: str = "",
    ) -> None:
        """A glance: the notifier pill beside the cubes, the title with its short detail inline.

        The label window is click-through, so it can never receive <Enter>. The cube already ticks
        every frame, so the pointer is polled there instead (looking at it keeps it up a little).
        """
        now = time.perf_counter()
        duration = max(0.5, float(seconds or 3.0))
        self._notice_tone = str(tone or "")
        if self._guidance_owns_label(now):
            self._pending_glance = (str(title or ""), duration, None, str(detail or ""), "notice")
            self._pending_notice_action = activate if callable(activate) else None
            return
        self._show_glance(
            str(title or ""), duration, detail=str(detail or ""), kind="notice", activate=activate,
        )

    def _guidance_owns_label(self, now: float | None = None) -> bool:
        current = time.perf_counter() if now is None else float(now)
        return (
            getattr(self, "_label_variant", "glance") == "guidance"
            and bool(getattr(self, "_label_value", ""))
            and current < float(getattr(self, "_label_until", 0.0) or 0.0)
        )

    def _show_glance(
        self,
        text: str,
        seconds: float,
        *,
        side: str | None = None,
        detail: str = "",
        kind: str = "bubble",
        activate: Any = None,
    ) -> None:
        now = time.perf_counter()
        self._label_variant = "glance"
        self._label_kind = "notice" if kind == "notice" else "bubble"
        self._notice_title = str(text or "") if kind == "notice" else ""
        self._notice_detail = str(detail or "")
        self._notice_expanded = False
        self._notice_until = now + _NOTICE_GLANCE_SECONDS if self._notice_detail else 0.0
        self._notice_hard_until = now + _NOTICE_HOVER_MAX_SECONDS if self._notice_detail else 0.0
        self._notice_action = activate if kind == "notice" and callable(activate) else None
        self._notice_action_until = now + max(float(seconds or 0.5), _NOTICE_GLANCE_SECONDS)
        self._set_label(text, until=now + max(0.5, float(seconds or 0.5)), side=self._label_side(side))

    def _replay_pending_glance(self) -> bool:
        pending = getattr(self, "_pending_glance", None)
        self._pending_glance = None
        if pending is None:
            return False
        text, seconds, side, detail, kind = pending
        activate = getattr(self, "_pending_notice_action", None) if kind == "notice" else None
        self._pending_notice_action = None
        self._show_glance(text, seconds, side=side, detail=detail, kind=kind, activate=activate)
        return True

    def activate_notice(self) -> bool:
        """Consume the current glance action when the operator clicks the cube."""
        action = getattr(self, "_notice_action", None)
        if not callable(action) or time.perf_counter() > float(getattr(self, "_notice_action_until", 0.0) or 0.0):
            self._notice_action = None
            self._notice_action_until = 0.0
            return False
        self._hide_label()
        try:
            action()
        except Exception:
            pass
        return True

    def _pointer_over_label(self) -> bool:
        left, top, right, bottom = getattr(self, "_label_rect", (0, 0, 0, 0))
        if right <= left:
            return False
        point = self._frame_pointer()
        if point is None:
            return False
        px, py = point
        return left <= px <= right and top <= py <= bottom

    def _poll_notice(self, now: float) -> None:
        if self._guidance_owns_label(now):
            return
        detail = getattr(self, "_notice_detail", "")
        if not detail:
            return
        if now >= getattr(self, "_notice_hard_until", 0.0):
            self._notice_detail = ""  # a parked cursor cannot pin the glance open forever
            return
        if self._notice_expanded:
            if not self._pointer_over_label():
                self._notice_detail = ""  # let the label expire on its own timer
            return
        if now < getattr(self, "_notice_until", 0.0) and self._pointer_over_label():
            # The detail is already inline in the pill: being looked at keeps it up a little longer.
            self._notice_expanded = True
            self._label_until = max(float(getattr(self, "_label_until", 0.0) or 0.0), min(now + 2.4, self._notice_hard_until))

    def clear_bubble(self) -> None:
        """Hide the floating label bubble (e.g. when a turn's activity readout ends)."""
        if self._guidance_owns_label():
            pending = getattr(self, "_pending_glance", None)
            if pending is not None and pending[4] != "notice":
                self._pending_glance = None
            return
        self._hide_label()

    def _label_side(self, preferred: str | None = None) -> str | None:
        if preferred in {"left", "right"}:
            return preferred
        provider = getattr(self, "_label_side_provider", None)
        if callable(provider):
            try:
                value = provider()
                if value in {"left", "right"}:
                    return value
            except Exception:
                pass
        return None

    # --- label bubble ---
    def _set_label(self, label: str, *, until: float, side: str | None = None) -> None:
        if self._label_kind != "running":
            self._clear_running_label()
        text = str(label or "").strip()
        base_design = getattr(self, "_label_design", DEFAULT_LABEL_BUBBLE_DESIGN)
        design = (
            guidance_label_design(base_design)
            if getattr(self, "_label_variant", "glance") == "guidance"
            else base_design
        )
        self._label_active_design = design
        max_chars = int(getattr(design, "max_chars", 54) or 54)
        if len(text) > max_chars:
            text = text[: max(0, max_chars - 3)] + "..."
        self._label_value = text
        self._label_until = float(until or 0.0)
        self._label_side_override = side if side in {"left", "right"} else None
        if not text:
            self._hide_label()
            return
        try:
            self._label_img = self._render_label(text)
            if self._label_img is not None:
                self._reposition_label()  # blit at the right spot WHILE hidden…
                self._label_win.show()  # …then show, so it never flashes at a stale pos
        except Exception:
            pass

    def _render_label(self, text: str) -> Any:
        """A small layered card (skin-tone text + border on a dark plate, soft shadow),
        drawn through the shared card primitive so it matches the reply/dashboard panel."""
        if not self._label.available():
            return None
        from PIL import Image, ImageDraw

        ss = card.SS
        design = getattr(
            self,
            "_label_active_design",
            getattr(self, "_label_design", DEFAULT_LABEL_BUBBLE_DESIGN),
        )
        from mo_desktop.fonts import load_font

        guidance = getattr(self, "_label_variant", "glance") == "guidance"
        if not guidance:
            return self._render_glance_pill(text)
        faces = (
            ("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf", "segoeui.ttf", "arial.ttf")
            if guidance
            else ("segoeui.ttf", "arial.ttf")
        )
        # The glance stays regular and quiet; only pointed guidance earns the
        # larger semibold face. Both paths share the same cached font loader.
        font = load_font(faces, int(design.font_size) * ss)
        measure = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
        max_width = max(80, int(getattr(design, "max_width", 280) or 280)) * ss
        lines = _wrap_label_text(text, measure, font, max_width)
        widths = [int(measure.textlength(line, font=font)) for line in lines] or [0]
        panel_padding = int(self._visuals.metrics.panel_padding)
        px = panel_padding * ss
        py = panel_padding * ss
        pad = int(design.shadow_pad) * ss
        line_height = max(int(getattr(design, "line_height", 17) or 17) * ss, int(design.font_size) * ss + 4)
        cw = max(widths) + 2 * px
        ch = max(line_height, len(lines) * line_height) + 2 * py
        W, H = cw + 2 * pad, ch + 2 * pad
        rad = int(self._visuals.metrics.panel_corner_radius) * ss
        box = (pad, pad, W - pad, H - pad)
        img = card.new_canvas(W, H)
        img = card.draw_card(
            img,
            box,
            radius=rad,
            fill=(*self._plate_color(), 245),
            edge=(*self._color_rgb, int(getattr(design, "edge_alpha", 46) or 46)),
            edge_highlight=(*self._color_rgb, 110),
            edge_width=ss,
            shadow_alpha=int(design.shadow_alpha),
            shadow_blur=int(design.shadow_blur) * ss,
            shadow_dy=3 * ss,
        )
        d = ImageDraw.Draw(img)
        # PIL has no Raqm here, so use the same optional shaping helper as the panel.
        lines = [shape_line(line)[0] for line in lines]
        # Centre the INK, not the em box. A line's em box reserves descender space the string may
        # never use ("volume 62%" has no descenders), so laying it out from the cell top leaves the
        # text sitting off the card's middle. Measure where the glyphs actually land and centre that.
        try:
            boxes = [font.getbbox(line) for line in lines]
            ink_top = min(b[1] for b in boxes)
            ink_bottom = max(b[3] for b in boxes)
        except Exception:
            ink_top, ink_bottom = 0, line_height
        block = (len(lines) - 1) * line_height + (ink_bottom - ink_top)
        top = pad + (ch - block) // 2 - ink_top
        for index, line in enumerate(lines):
            ink = getattr(self, "_label_text_rgb", None) or self._color_rgb
            if guidance and index == 0:
                ink = self._color_rgb
            d.text((pad + px, top + index * line_height), line, font=font, fill=(*ink, 255))
        return card.finish(img, ss)

    def _render_glance_pill(self, text: str) -> Any:
        """The notifier glance (approved 2026-10-06): one compact pill in the skin's neutral card
        colour, never tinted by the cubes' current shade, with a status dot, the title, and a
        notice's short detail inline in the muted tone. Drawn with the shared card primitive."""
        from PIL import Image, ImageColor, ImageDraw
        from mo_desktop.fonts import load_font

        ss = card.SS
        design = getattr(self, "_label_design", DEFAULT_LABEL_BUBBLE_DESIGN)
        palette = self._visuals.palette
        size = int(design.font_size) * ss
        bold = load_font(("seguisb.ttf", "segoeuib.ttf", "segoeui.ttf", "arial.ttf"), size)
        regular = load_font(("segoeui.ttf", "arial.ttf"), size)
        detail = str(getattr(self, "_notice_detail", "") or "") if getattr(self, "_label_kind", "") == "notice" else ""
        title = shape_line(str(text or ""))[0]
        detail = shape_line(detail)[0] if detail else ""
        measure = ImageDraw.Draw(Image.new("RGBA", (4, 4)))
        limit = max(80, int(getattr(design, "max_width", 280) or 280)) * ss
        title_w = int(measure.textlength(title, font=bold))
        gap = 6 * ss
        if detail:
            room = limit - title_w - gap
            while detail and measure.textlength(detail, font=regular) > room:
                detail = detail[:-2].rstrip() + "\u2026" if len(detail) > 2 else ""
        detail_w = int(measure.textlength(detail, font=regular)) if detail else 0
        dot_r = 3 * ss
        pad_x = int(self._visuals.metrics.panel_padding) * ss
        height = max(int(getattr(design, "line_height", 17) or 17) + 9, int(design.font_size) + 14) * ss
        width = pad_x + 2 * dot_r + 8 * ss + title_w + (gap + detail_w if detail else 0) + pad_x
        pad = int(design.shadow_pad) * ss
        img = card.new_canvas(width + 2 * pad, height + 2 * pad)
        border = ImageColor.getrgb(palette.border)
        img = card.draw_card(
            img, (pad, pad, pad + width, pad + height), radius=height // 2,
            fill=(*ImageColor.getrgb(palette.card), 238), edge=(*border, 150), edge_highlight=(*border, 190),
            edge_width=ss, shadow_alpha=int(design.shadow_alpha), shadow_blur=int(design.shadow_blur) * ss,
            shadow_dy=2 * ss,
        )
        draw = ImageDraw.Draw(img)
        warn = getattr(self, "_label_kind", "") == "notice" and getattr(self, "_notice_tone", "") == "warn"
        tone = palette.warn if warn else palette.accent
        cx, cy = pad + pad_x + dot_r, pad + height // 2
        draw.ellipse((cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r), fill=ImageColor.getrgb(tone))
        # Centre the INK of what is written (as the label always did), not a reference glyph.
        boxes = [bold.getbbox(title)] + ([regular.getbbox(detail)] if detail else [])
        top = cy - (min(b[1] for b in boxes) + max(b[3] for b in boxes)) // 2
        x = cx + dot_r + 8 * ss
        draw.text((x, top), title, font=bold, fill=(*ImageColor.getrgb(palette.text), 255))
        if detail:
            draw.text((x + title_w + gap, top), detail, font=regular, fill=(*ImageColor.getrgb(palette.muted), 255))
        return card.finish(img, ss)

    def _hide_label(self) -> None:
        self._clear_running_label()
        self._label_until = 0.0
        self._label_value = ""
        self._label_img = None
        self._label_painted_img = None
        self._label_side_override = None
        self._label_variant = "glance"
        self._label_kind = "bubble"
        self._pending_glance = None
        self._notice_title = ""
        self._notice_detail = ""
        self._notice_until = 0.0
        self._notice_hard_until = 0.0
        self._notice_action = None
        self._notice_action_until = 0.0
        self._pending_notice_action = None
        try:
            self._label_win.hide()
        except Exception:
            pass

    def _reposition_label(self) -> None:
        if not self._label_value or self._label_img is None:
            return
        try:
            sw = int(screen_size()[0])
            sh = int(screen_size()[1])
        except Exception:
            return
        w, h = self._label_img.size
        design = getattr(
            self,
            "_label_active_design",
            getattr(self, "_label_design", DEFAULT_LABEL_BUBBLE_DESIGN),
        )
        offset = self._size * float(design.offset_x_ratio)
        # Place the CARD, then step back over its shadow margin — otherwise growing the
        # margin to contain the blur would drag the label away from the cube.
        pad = int(design.shadow_pad)
        card_w = w - 2 * pad
        side = getattr(self, "_label_side_override", None)
        if side == "left":
            x = int(round(self._x - offset - card_w)) - pad
        else:
            x = int(round(self._x + offset)) - pad
            if side != "right" and x + w > sw:
                x = int(round(self._x - offset - card_w)) - pad
        launcher = getattr(self, "_launcher_rect", None) if getattr(self, "_launcher_active", False) else None
        if launcher is not None:
            # The open launcher holds the cubes' space: the glance shows beside it, on the side
            # with room, instead of over the four app cubes.
            lx, _ly, lw, _lh = launcher
            right_x, left_x = lx + lw + 8 - pad, lx - 8 - card_w - pad
            x = right_x if right_x + w <= sw or left_x < 0 else left_x
        y = int(round(self._y - (h / 2.0) + self._size * float(design.offset_y_ratio)))
        y = max(0, min(y, sh - h))
        x = max(0, min(x, max(0, sw - w)))
        self._label_rect = (x, y, x + w, y + h)
        try:
            if getattr(self, "_label_painted_img", None) is self._label_img:
                self._label_win.position(x, y, w, h)
            elif self._label.blit(self._label_img, x, y, premultiplied=True):
                self._label_painted_img = self._label_img
        except Exception:
            pass


def _wrap_label_text(text: str, draw: Any, font: Any, max_width: int) -> list[str]:
    lines: list[str] = []
    for raw in str(text or "").splitlines() or [""]:
        words = raw.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            for part in split_token_to_width(word, max_width, lambda value: draw.textlength(value, font=font)):
                candidate = f"{current} {part}".strip()
                if not current or draw.textlength(candidate, font=font) <= max_width:
                    current = candidate
                    continue
                lines.append(current)
                current = part
        if current:
            lines.append(current)
    return lines or [""]


def _guidance_label_text(text: str) -> str:
    """Turn a point label into a short heading plus readable explanation.

    The model commonly emits ``**1. Home** — opens the feed``. Reuse the reply
    surface's safe emphasis parser so the cube label does not show raw Markdown,
    then place the numbered title and explanation on separate lines. Unnumbered
    labels remain unchanged apart from safe emphasis removal.
    """
    raw = str(text or "").strip()
    raw = plain_spans(parse_inline_emphasis(raw))
    raw = "\n".join(part.strip() for part in raw.splitlines() if part.strip())
    if not raw:
        return _DEFAULT_POINT_LABEL

    heading, separator, body = raw.partition("\n")
    if not separator:
        for divider in (" — ", " – "):
            if divider in heading:
                heading, body = (part.strip() for part in heading.split(divider, 1))
                break

    compact = heading.strip()
    for marker in (".", ")"):
        number, found, title = compact.partition(marker)
        if found and number.strip().isdigit() and title.strip():
            compact = f"{number.strip()}  ·  {title.strip()}"
            break
    return f"{compact}\n{body.strip()}" if body.strip() else compact
