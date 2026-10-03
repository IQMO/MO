"""Display-only transcript wrapping helpers for the prompt-toolkit TUI."""
from __future__ import annotations

import re

from prompt_toolkit.utils import get_cwidth

from .transcript_grammar import GUTTERS, RAIL

Fragment = tuple[str, str]
FragmentRow = list[Fragment]

_GUTTER_CONTINUATIONS: dict[Fragment, Fragment] = {
    RAIL: RAIL,
    GUTTERS["user"]: GUTTERS["cont"],
    GUTTERS["mo"]: GUTTERS["cont"],
    GUTTERS["system"]: GUTTERS["cont"],
    GUTTERS["cont"]: GUTTERS["cont"],
}


def cell_width(text: str) -> int:
    return sum(max(0, get_cwidth(ch)) for ch in str(text or ""))


def wrap_tokens(text: str) -> list[tuple[str, int, bool]]:
    tokens: list[tuple[str, int, bool]] = []
    for part in re.findall(r"\s+|\S+", str(text or "")):
        if part.isspace():
            value = " " * cell_width(part)
            tokens.append((value, max(1, cell_width(value)), True))
        else:
            tokens.append((part, cell_width(part), False))
    return tokens


def split_cells(text: str, width: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    used = 0
    width = max(1, int(width or 1))
    for ch in str(text or ""):
        cw = max(0, get_cwidth(ch))
        if current and used + cw > width:
            chunks.append("".join(current))
            current = []
            used = 0
        current.append(ch)
        used += cw
    if current:
        chunks.append("".join(current))
    return chunks or [""]



def fit_cells(text: str, width: int) -> str:
    """Pad with spaces or truncate-with-ellipsis to EXACTLY ``width`` cells.

    The one owner for fixed-column panel text: wide characters count by cell,
    so CJK/Arabic content cannot misalign a column the way ``len()`` math does.
    """
    width = max(0, int(width))
    text = str(text or "")
    have = cell_width(text)
    if have == width:
        return text
    if have < width:
        return text + " " * (width - have)
    if width == 0:
        return ""
    if width == 1:
        return "…"
    chunk = split_cells(text, width - 1)[0] if text else ""
    while chunk and cell_width(chunk) > width - 1:
        chunk = chunk[:-1]
    return chunk + " " * max(0, (width - 1) - cell_width(chunk)) + "…"


def fragment_line_text(fragments: list[Fragment]) -> str:
    return "".join(str(text) for _style, text in fragments)


def fragment_line_is_preformatted(fragments: list[Fragment]) -> bool:
    text = fragment_line_text(fragments)
    _gutter, content = _continuation_gutter(fragments)
    content_styles = {
        style for style, value in content if str(value).strip()
    }
    stripped = text.lstrip()
    return (
        (bool(content_styles) and content_styles == {"class:response-code"})
        or stripped.startswith(("|", "+", "```"))
        or ("|" in text and text.count("|") >= 2)
    )


def _continuation_gutter(
    fragments: list[Fragment],
) -> tuple[Fragment | None, list[Fragment]]:
    if not fragments:
        return None, fragments
    gutter = _GUTTER_CONTINUATIONS.get(fragments[0])
    return (gutter, fragments[1:]) if gutter else (None, fragments)


def continuation_prefix(fragments: list[Fragment]) -> str:
    gutter, content = _continuation_gutter(fragments)
    text = fragment_line_text(content)
    leading = len(text) - len(text.lstrip(" "))
    stripped = text.lstrip()
    list_marker = re.match(r"^(?:[-*•⋯]|\d+[.)])\s+", stripped)
    if list_marker:
        indent = " " * (leading + cell_width(list_marker.group(0)))
    elif leading:
        indent = " " * min(leading, 12)
    else:
        indent = "" if gutter else "  "
    return (gutter[1] if gutter else "") + indent


def _continuation_fragments(
    fragments: list[Fragment],
    width: int,
) -> list[Fragment]:
    """Keep canonical gutters and their skin classes on every visual row."""
    gutter, _content = _continuation_gutter(fragments)
    prefix_width = cell_width(continuation_prefix(fragments))
    available = max(0, int(width) - 1)
    if gutter:
        gutter_width = cell_width(gutter[1])
        extra = min(
            max(0, prefix_width - gutter_width),
            max(0, available - gutter_width),
        )
        return [gutter] + ([("", " " * extra)] if extra else [])
    return [("", " " * min(prefix_width, available))] if prefix_width else []


def wrap_preformatted_fragments(fragments: list[Fragment], width: int) -> list[FragmentRow]:
    width = max(8, int(width or 80))
    gutter, _content = _continuation_gutter(fragments)
    continuation = _continuation_fragments(fragments, width) if gutter else []
    continuation_width = sum(cell_width(text) for _style, text in continuation)
    rows: list[FragmentRow] = [[]]
    used = 0

    def new_row() -> None:
        nonlocal used
        rows.append(list(continuation))
        used = continuation_width

    def append(style: str, text: str) -> None:
        if rows[-1] and rows[-1][-1][0] == style:
            previous_style, previous_text = rows[-1][-1]
            rows[-1][-1] = (previous_style, previous_text + text)
        else:
            rows[-1].append((style, text))

    for style, text in fragments:
        for char in str(text):
            char_width = max(0, get_cwidth(char))
            if used and char_width and used + char_width > width:
                new_row()
            append(style, char)
            used += char_width
    return rows or [[("", "")]]


def wrap_fragment_line(fragments: list[Fragment], width: int) -> list[FragmentRow]:
    """Word-wrap transcript rows without splitting normal prose words."""
    if not fragments:
        return [[("", "")]]
    width = max(8, int(width or 80))
    if fragment_line_is_preformatted(fragments):
        return wrap_preformatted_fragments(fragments, width)

    rows: list[FragmentRow] = [[]]
    used = 0
    emitted_any = False
    continuation = _continuation_fragments(fragments, width)
    continuation_width = sum(cell_width(text) for _style, text in continuation)

    def new_row() -> None:
        nonlocal used, emitted_any
        rows.append(list(continuation))
        used = continuation_width
        emitted_any = True

    for style, text in fragments:
        tokens = wrap_tokens(str(text))
        for token, token_width, breakable in tokens:
            if token_width <= 0:
                continue
            if token.isspace() and used == 0 and emitted_any:
                continue
            if used and used + token_width > width:
                new_row()
                if breakable:
                    continue
            if token_width > width - used and not breakable:
                remaining = token
                while remaining:
                    chunk = split_cells(remaining, max(1, width - used))[0]
                    chunk_width = cell_width(chunk)
                    if used and chunk_width and used + chunk_width > width:
                        new_row()
                    rows[-1].append((style, chunk))
                    used += chunk_width
                    remaining = remaining[len(chunk):]
                    if remaining:
                        new_row()
                continue
            rows[-1].append((style, token))
            used += token_width
            emitted_any = True
    return rows or [[("", "")]]
