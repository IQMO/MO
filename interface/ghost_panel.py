"""Display-only Ghost panel rendering and cell wrapping helpers."""
from __future__ import annotations

import re
import time

from prompt_toolkit.utils import get_cwidth

from .formatting import moon_phase_frame

from .ghost import strip_md
from .transcript_view import cell_width


def fit_cells(text: str, width: int) -> str:
    out: list[str] = []
    used = 0
    width = max(0, int(width or 0))
    for ch in str(text or "").expandtabs(4):
        if ch in "\r\n":
            break
        cell_width = max(0, get_cwidth(ch))
        if used + cell_width > width:
            break
        out.append(ch)
        used += cell_width
    return "".join(out) + (" " * max(0, width - used))


def wrap_long_token(token: str, width: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    used = 0
    width = max(1, int(width or 1))
    for ch in str(token or ""):
        ch_width = max(0, get_cwidth(ch))
        if current and used + ch_width > width:
            chunks.append("".join(current))
            current = []
            used = 0
        if ch_width <= width:
            current.append(ch)
            used += ch_width
    if current:
        chunks.append("".join(current))
    return chunks or [""]


def wrap_cells(text: str, width: int) -> list[str]:
    wrapped: list[str] = []
    width = max(1, int(width or 1))
    plain = str(text or "").replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
    for source_line in plain.split("\n") or [""]:
        source_line = source_line.strip()
        if source_line == "":
            wrapped.append("")
            continue
        current = ""
        used = 0
        for word in source_line.split():
            word_width = cell_width(word)
            if word_width > width:
                if current:
                    wrapped.append(current.rstrip())
                    current = ""
                    used = 0
                wrapped.extend(wrap_long_token(word, width))
                continue
            sep = " " if current else ""
            sep_width = 1 if current else 0
            if current and used + sep_width + word_width > width:
                wrapped.append(current.rstrip())
                current = word
                used = word_width
            else:
                current = f"{current}{sep}{word}"
                used += sep_width + word_width
        if current:
            wrapped.append(current.rstrip())
    return wrapped or [""]


def panel_dimensions(columns: int) -> tuple[int, int]:
    available = max(12, int(columns or 0) - 2)
    total_width = min(100, available) if available < 30 else max(30, min(available, 100))
    inner = max(8, total_width - 4)
    return total_width, inner


def route_line_style(style: str, line: str) -> str:
    if style != "class:ghost-response":
        return style
    stripped = str(line or "").strip()
    lowered = stripped.lower()
    if stripped.startswith("! ") and any(word in lowered for word in ("unavailable", "conflict", "limit", "blocked", "error")):
        return "class:ghost-route-blocked"
    if stripped.startswith("→ ") and "unavailable" in lowered:
        return "class:ghost-route-blocked"
    tokens = stripped.split()
    if tokens and all(token in {"↯", "→", "✓"} for token in tokens):
        return "class:ghost-route"
    if tokens and tokens[0] == "!":
        return "class:ghost-route-blocked"
    if lowered in {"mo routed", "mo queued", "worker routed", "receiver accepted"}:
        return "class:ghost-route"
    # Route-receipt rejections ("MO unavailable", "MO queue unavailable", "Worker
    # unavailable") start with the surface name rather than a glyph; flag them blocked
    # so they never read as a normal answer. Anchored on the surface name to avoid
    # false-positives on ordinary prose that merely mentions "unavailable".
    if tokens and tokens[0].lower() in {"mo", "worker"} and \
            any(word in lowered for word in ("unavailable", "conflict")):
        return "class:ghost-route-blocked"
    return style


def _thinking_display(display: str, *, now: float | None = None) -> str:
    base = display.strip().lstrip("🌑🌒🌓🌔🌕🌖🌗🌘○◔◑◕● ").strip() or "Replying"
    current = time.time() if now is None else float(now)
    word = base.rstrip(".")
    return f"{moon_phase_frame(current)} {word}"


def _organize_ghost_response(text: str) -> str:
    """Return display-only Ghost text with compact, readable line breaks."""
    clean = strip_md(text).strip("\n")
    output: list[str] = []
    for raw_line in clean.splitlines() or [""]:
        line = raw_line.strip()
        if not line:
            output.append("")
            continue
        if line.startswith(("- ", "* ", "• ")) or re.match(r"^\d+[.)]\s+", line):
            output.append(line)
            continue
        if re.match(r"^(?:bottom line|status|suggested ask|next|checks?|references?|caveats?|what i can see):", line, re.I):
            output.append(line)
            continue
        if cell_width(line) > 90:
            pieces = [part.strip() for part in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", line) if part.strip()]
            if len(pieces) > 1:
                output.extend(pieces)
                continue
        output.append(line)
    return "\n".join(output)


def content_rows(panel_lines: list[tuple[str, str]], inner: int, *, now: float | None = None) -> list[list[tuple[str, str]]]:
    rows: list[list[tuple[str, str]]] = []
    for idx, (style, text) in enumerate(panel_lines):
        display = str(text)
        if style == "class:ghost-response":
            display = _organize_ghost_response(display)
        elif style == "class:ghost-hint":
            display = strip_md(display).strip("\n")
        if style == "class:ghost-thinking":
            display = _thinking_display(display, now=now)
        source_lines = display.split("\n") if display else [""]
        for source_line in source_lines:
            row_style = route_line_style(style, source_line)
            for chunk in wrap_cells(source_line, inner):
                rows.append([(row_style, fit_cells(chunk, inner))])
        if style == "class:ghost-user" and idx < len(panel_lines) - 1:
            rows.append([("class:ghost-gap", fit_cells("", inner))])
    return rows


def body_row_count(expanded: bool) -> int:
    return 9 if expanded else 5


def max_scroll(panel_open: bool, panel_lines: list[tuple[str, str]], inner: int, *, expanded: bool = True, now: float | None = None) -> int:
    if not panel_open or not panel_lines:
        return 0
    return max(0, len(content_rows(panel_lines, inner, now=now)) - body_row_count(expanded))


def panel_fragments(
    *,
    panel_open: bool,
    panel_lines: list[tuple[str, str]],
    total_width: int,
    inner: int,
    scroll_from_bottom: int,
    expanded: bool = False,
    now: float | None = None,
) -> tuple[list[tuple[str, str]], int]:
    if not panel_open or not panel_lines:
        return [("", "")], scroll_from_bottom

    rows = content_rows(panel_lines, inner, now=now)
    body_rows = body_row_count(expanded)
    adjusted_scroll = max(0, min(max(0, len(rows) - body_rows), scroll_from_bottom))
    start = max(0, len(rows) - body_rows - adjusted_scroll)
    selected = rows[start : start + body_rows]
    action = "Ctrl+O collapse" if expanded else "Ctrl+O expand"
    hint_parts: list[str] = []
    if len(rows) > body_rows:
        hint_parts.append(f"{start + 1}-{start + len(selected)}/{len(rows)}")
    hint_parts.extend(["Alt+G/Esc hide", action])
    if expanded:
        hint_parts.append("hist ↑/↓")
        hint_parts.append("scroll Ctrl+↑/↓")
    hint_tail = " · ".join(hint_parts)
    if cell_width(hint_tail) > inner:
        short_parts: list[str] = []
        if len(rows) > body_rows:
            short_parts.append(f"{start + 1}-{start + len(selected)}/{len(rows)}")
        short_parts.extend(["Alt+G/Esc", "Ctrl+O", "hist ↑↓"])
        if expanded:
            short_parts.append("scroll C↑↓")
        hint_tail = " · ".join(short_parts)

    fragments: list[tuple[str, str]] = []
    fragments.append(("class:ghost-frame", "↯ "))
    ghost_label = fit_cells("Ghost", min(5, max(8, total_width - 2))).rstrip()
    fragments.append(("class:ghost-route", ghost_label))
    fragments.append(("", "\n"))
    for row in selected:
        fragments.append(("class:ghost-frame", "  "))
        for style, text in row:
            fragments.append((style, fit_cells(text, inner).rstrip()))
        fragments.append(("", "\n"))
    divider_width = min(max(16, inner), max(16, total_width - 2))
    fragments.append(("class:ghost-frame", "  " + "─" * divider_width + "\n"))
    if hint_tail:
        fragments.append(("class:ghost-frame", "  "))
        fragments.append(("class:ghost-hint", fit_cells(hint_tail, inner).rstrip()))
        fragments.append(("", "\n"))
    return fragments or [("", "")], adjusted_scroll
