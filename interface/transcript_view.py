"""Display-only transcript wrapping helpers for the prompt-toolkit TUI."""
from __future__ import annotations

import re

from prompt_toolkit.utils import get_cwidth

Fragment = tuple[str, str]
FragmentRow = list[Fragment]


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


def fragment_line_text(fragments: list[Fragment]) -> str:
    return "".join(str(text) for _style, text in fragments)


def fragment_line_is_preformatted(fragments: list[Fragment]) -> bool:
    text = fragment_line_text(fragments)
    styles = {style for style, _text in fragments}
    stripped = text.lstrip()
    return (
        "class:response-code" in styles
        or stripped.startswith(("|", "+", "```"))
        or ("|" in text and text.count("|") >= 2)
    )


def wrap_preformatted_fragments(fragments: list[Fragment], width: int) -> list[FragmentRow]:
    rows: list[FragmentRow] = [[]]
    used = 0
    width = max(8, int(width or 80))
    for style, text in fragments:
        for chunk in split_cells(str(text), width):
            chunk_width = cell_width(chunk)
            if used and used + chunk_width > width:
                rows.append([])
                used = 0
            rows[-1].append((style, chunk))
            used += chunk_width
            if used >= width:
                rows.append([])
                used = 0
    if rows and not rows[-1]:
        rows.pop()
    return rows or [[("", "")]]


def continuation_prefix(fragments: list[Fragment]) -> str:
    text = fragment_line_text(fragments)
    leading = len(text) - len(text.lstrip(" "))
    stripped = text.lstrip()
    bullet = re.match(r"^([-*•])\s+", stripped)
    if bullet:
        return " " * (leading + len(bullet.group(0)))
    if leading:
        return " " * min(leading, 12)
    return "  "


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
    prefix = continuation_prefix(fragments)
    prefix_width = cell_width(prefix)

    def new_row() -> None:
        nonlocal used, emitted_any
        rows.append([])
        used = 0
        emitted_any = True
        if prefix:
            rows[-1].append(("class:mo-response", prefix))
            used = prefix_width

    for style, text in fragments:
        tokens = wrap_tokens(str(text))
        for token, token_width, breakable in tokens:
            if token_width <= 0:
                continue
            if token.isspace() and used == 0 and emitted_any:
                continue
            if used and used + token_width > width:
                new_row()
                if token == " ":
                    continue
            if token_width > width - used and not breakable:
                for chunk in split_cells(token, max(1, width - used)):
                    chunk_width = cell_width(chunk)
                    if used and used + chunk_width > width:
                        new_row()
                    rows[-1].append((style, chunk))
                    used += chunk_width
                continue
            rows[-1].append((style, token))
            used += token_width
            emitted_any = True
    return rows or [[("", "")]]
