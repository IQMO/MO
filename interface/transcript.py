"""Display-only transcript viewport helpers."""
from __future__ import annotations

import unicodedata

from .transcript_view import wrap_fragment_line


_RTL_BIDI_CLASSES = {"R", "AL", "AN"}


def _is_rtl_char(char: str) -> bool:
    return unicodedata.bidirectional(char) in _RTL_BIDI_CLASSES


def _terminal_rtl_text(text: str) -> str:
    """Return display-order text for RTL runs in prompt-toolkit fragments.

    The transcript stores provider output in logical order. Prompt-toolkit emits
    fragment text left-to-right and does not apply Unicode bidi reordering for
    Arabic runs, so Arabic words appear backwards in the TUI. Reverse only
    contiguous RTL runs at the final display boundary; stored transcript text
    and surrounding LTR text stay unchanged.
    """
    value = str(text or "")
    if not any(_is_rtl_char(char) for char in value):
        return value
    out: list[str] = []
    run: list[str] = []
    for char in value:
        if _is_rtl_char(char):
            run.append(char)
            continue
        if run:
            out.extend(reversed(run))
            run.clear()
        out.append(char)
    if run:
        out.extend(reversed(run))
    return "".join(out)


def logical_lines_from_snapshot(snapshot: tuple[tuple[str, str], ...]) -> list[list[tuple[str, str]]]:
    lines: list[list[tuple[str, str]]] = [[]]
    for style, text in snapshot:
        raw = str(text)
        if raw == "\n":
            lines.append([])
            continue
        parts = raw.split("\n")
        for part_index, part in enumerate(parts):
            if part_index:
                lines.append([])
            if part:
                lines[-1].append((style, part))
    return lines or [[("class:dim", "")]]


def visual_rows(logical_lines: list[list[tuple[str, str]]], width: int) -> list[list[tuple[str, str]]]:
    width = max(20, min(width, 240))
    rows: list[list[tuple[str, str]]] = []
    for fragments in logical_lines:
        rows.extend(wrap_fragment_line(fragments, width))
    return rows or [[("class:dim", "")]]


def transcript_fragments_for_viewport(
    rows: list[list[tuple[str, str]]],
    *,
    visible: int,
    scroll_from_bottom: int,
) -> tuple[list[tuple[str, str]], int]:
    visible = max(1, int(visible or 1))
    max_from_bottom = max(0, len(rows) - visible)
    adjusted_scroll = max(0, min(max_from_bottom, scroll_from_bottom))
    start = max(0, len(rows) - visible - adjusted_scroll)
    selected = rows[start : start + visible]
    fragments: list[tuple[str, str]] = []
    # Bottom-anchor: when content is shorter than the viewport, pad above so
    # the latest message hugs the board/input area instead of leaving a void
    # between the answer and the bottom panels (observed live: a screen-high
    # blank block under short transcripts).
    for _ in range(max(0, visible - len(selected))):
        fragments.append(("", "\n"))
    for index, row in enumerate(selected):
        if index:
            fragments.append(("", "\n"))
        for style, text in row:
            fragments.append((style, _terminal_rtl_text(text)))
    return fragments or [("class:dim", "")], adjusted_scroll


def _board_max_height(terminal_rows: int) -> int:
    """Dynamic board height cap: scales with terminal but guards transcript."""
    return max(8, min(int(terminal_rows or 24) // 3, 20))


def visible_transcript_height(
    *,
    terminal_rows: int,
    busy: bool,
    goal_worker_active: bool,
    visible_goal_board_text: str,
    board_text: str,
    palette_open: bool,
    palette_item_count: int,
    ghost_panel_open: bool,
    ghost_expanded: bool = False,
    ghost_content_rows: int | None = None,
    input_rows: int = 1,
) -> int:
    # Fixed overhead: transcript gap (1) + separator (1) + footer (1) = 3
    reserved = 3 + max(1, int(input_rows or 1))
    if busy or goal_worker_active:
        reserved += 1  # activity/status lane
    else:
        reserved += 1  # compact idle/status bar
    max_board = _board_max_height(terminal_rows)
    if visible_goal_board_text:
        reserved += min(max_board, max(1, len(visible_goal_board_text.splitlines())))
    if board_text:
        reserved += min(max_board, max(1, len(board_text.splitlines())))
    if palette_open:
        reserved += min(12, palette_item_count + 3)
    if ghost_panel_open:
        # Ghost panel: 1 header + body rows + 1 divider + 1 hint row.
        # Use actual content rows when available, fall back to max body size.
        ghost_body = ghost_content_rows if ghost_content_rows is not None else (9 if ghost_expanded else 5)
        ghost_body = min(ghost_body, 9 if ghost_expanded else 5)
        reserved += ghost_body + 3  # header(1) + divider(1) + hint(1)
    return max(1, int(terminal_rows or 0) - reserved)


def adjusted_scroll_from_bottom(*, line_count: int, visible: int, current_scroll: int, delta_from_bottom: int) -> int:
    max_from_bottom = max(0, int(line_count or 0) - max(1, int(visible or 1)))
    return max(0, min(max_from_bottom, int(current_scroll or 0) + int(delta_from_bottom or 0)))
