"""Display-only transcript viewport helpers."""
from __future__ import annotations

import unicodedata

from .transcript_grammar import GUTTERS, RAIL
from .transcript_view import wrap_fragment_line


_RTL_BIDI_CLASSES = {"R", "AL"}
_STRONG_LTR_BIDI_CLASSES = {"L", "EN", "AN"}
_MIRRORED_CHARACTERS = str.maketrans("()[]{}<>", ")(][}{><")
_GUTTER_FRAGMENTS = frozenset({*GUTTERS.values(), RAIL})


def _is_rtl_char(char: str) -> bool:
    return unicodedata.bidirectional(char) in _RTL_BIDI_CLASSES


def _fallback_bidi_records(records: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Small dependency-free fallback for ordinary mixed-direction prose."""
    groups: list[list[tuple[str, str]]] = []
    for record in records:
        whitespace = record[1].isspace()
        if not groups or groups[-1][0][1].isspace() != whitespace:
            groups.append([])
        groups[-1].append(record)

    def direction(group: list[tuple[str, str]]) -> str:
        classes = {unicodedata.bidirectional(char) for _style, char in group}
        if classes & _RTL_BIDI_CLASSES:
            return "R"
        if classes & _STRONG_LTR_BIDI_CLASSES:
            return "L"
        return "N"

    def display_group(group: list[tuple[str, str]]) -> list[tuple[str, str]]:
        if direction(group) != "R":
            return group
        return [(style, char.translate(_MIRRORED_CHARACTERS)) for style, char in reversed(group)]

    base_rtl = next((direction(group) for group in groups if direction(group) != "N"), "L") == "R"
    if base_rtl:
        return [record for group in reversed(groups) for record in display_group(group)]

    output: list[tuple[str, str]] = []
    index = 0
    while index < len(groups):
        if direction(groups[index]) != "R":
            output.extend(groups[index])
            index += 1
            continue
        end = index + 1
        last_rtl = index
        while end < len(groups) and direction(groups[end]) != "L":
            if direction(groups[end]) == "R":
                last_rtl = end
            end += 1
        output.extend(record for group in reversed(groups[index:last_rtl + 1]) for record in display_group(group))
        index = last_rtl + 1
    return output


def _bidi_records(records: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Return Unicode display order while carrying each character's style."""
    text = "".join(char for _style, char in records)
    try:
        from bidi.algorithm import (
            apply_mirroring,
            explicit_embed_and_overrides,
            get_base_level,
            get_embedding_levels,
            get_empty_storage,
            reorder_resolved_levels,
            resolve_implicit_levels,
            resolve_neutral_types,
            resolve_weak_types,
        )
    except ImportError:
        return _fallback_bidi_records(records)

    storage = get_empty_storage()
    storage["base_level"] = get_base_level(text)
    storage["base_dir"] = ("L", "R")[storage["base_level"]]
    get_embedding_levels(text, storage)
    for char, (style, _text) in zip(storage["chars"], records):
        char["style"] = style
    explicit_embed_and_overrides(storage)
    resolve_weak_types(storage)
    resolve_neutral_types(storage, False)
    resolve_implicit_levels(storage, False)
    reorder_resolved_levels(storage, False)
    apply_mirroring(storage, False)
    if len(storage["chars"]) != len(records):
        return _fallback_bidi_records(records)
    return [(char["style"], char["ch"]) for char in storage["chars"]]


def terminal_display_indexed(text: str) -> list[tuple[int, str]]:
    """Return display-order characters paired with their logical indexes."""
    value = str(text or "")
    return [
        (int(source_index), display_char)
        for source_index, display_char in _bidi_records([
            (str(index), char) for index, char in enumerate(value)
        ])
    ]


def terminal_text_is_rtl(text: str) -> bool:
    """Return whether the first strong character selects an RTL paragraph."""
    for char in str(text or ""):
        bidi_class = unicodedata.bidirectional(char)
        if bidi_class in _RTL_BIDI_CLASSES:
            return True
        if bidi_class == "L":
            return False
    return False


def terminal_display_fragments(fragments: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Convert one logical transcript row to terminal display order."""
    if not any(_is_rtl_char(char) for _style, text in fragments for char in str(text)):
        return list(fragments)
    records = [(style, char) for style, text in fragments for char in str(text)]
    prefix_length = next((
        len(gutter_text)
        for gutter_style, gutter_text in _GUTTER_FRAGMENTS
        if records
        and records[0][0] == gutter_style
        and "".join(char for _style, char in records).startswith(gutter_text)
    ), 0)
    prefix = records[:prefix_length]
    display = prefix + _bidi_records(records[prefix_length:])
    output: list[tuple[str, str]] = []
    for style, char in display:
        if output and output[-1][0] == style:
            output[-1] = (style, output[-1][1] + char)
        else:
            output.append((style, char))
    return output


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
    anchor_to_live_panels: bool = False,
) -> tuple[list[tuple[str, str]], int]:
    visible = max(1, int(visible or 1))
    max_from_bottom = max(0, len(rows) - visible)
    adjusted_scroll = max(0, min(max_from_bottom, scroll_from_bottom))
    start = max(0, len(rows) - visible - adjusted_scroll)
    selected = rows[start : start + visible]
    fragments: list[tuple[str, str]] = []
    slack = max(0, visible - len(selected))
    # Keep the empty/idle landing screen top-anchored. During active work, move
    # short transcript slack above the content so its latest tool row remains
    # visually attached to the live activity/taskboard panels below.
    if anchor_to_live_panels:
        fragments.extend(("", "\n") for _ in range(slack))
    for index, row in enumerate(selected):
        if index:
            fragments.append(("", "\n"))
        fragments.extend(terminal_display_fragments(row))
    if not anchor_to_live_panels:
        fragments.extend(("", "\n") for _ in range(slack))
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
    input_rows: int = 1,
    workspace_rows: int = 0,
) -> int:
    # Fixed overhead: composer top border (1) + composer bottom border (1) +
    # footer (1) = 3. Side borders live inside the input row.
    reserved = 3 + max(1, int(input_rows or 1))
    if busy or goal_worker_active:
        reserved += 1  # activity/status lane
    else:
        reserved += 1  # compact idle/status bar
    if workspace_rows:
        reserved += min(8, max(0, int(workspace_rows or 0)))
    max_board = _board_max_height(terminal_rows)
    if visible_goal_board_text:
        reserved += min(max_board, max(1, len(visible_goal_board_text.splitlines())))
    if board_text:
        reserved += min(max_board, max(1, len(board_text.splitlines())))
    if palette_open:
        reserved += min(12, palette_item_count + 3)
    return max(1, int(terminal_rows or 0) - reserved)


def adjusted_scroll_from_bottom(*, line_count: int, visible: int, current_scroll: int, delta_from_bottom: int) -> int:
    max_from_bottom = max(0, int(line_count or 0) - max(1, int(visible or 1)))
    return max(0, min(max_from_bottom, int(current_scroll or 0) + int(delta_from_bottom or 0)))
