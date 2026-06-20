"""Display-only assistant response typography helpers."""
from __future__ import annotations

import re

from prompt_toolkit.utils import get_cwidth


DEFAULT_RESPONSE_COLUMNS = 100
TABLE_GAP = 2
TABLE_MIN_CELL_WIDTH = 4
_SECTION_LABEL_RE = re.compile(r"^(\s*)(?:([-*•])\s+)?[*_`\s]*([A-Za-z][A-Za-z0-9 /&().-]{1,52})\s*:\s*(.*)$")
_TOKEN_LINE_RE = re.compile(r"^\s*(?:tokens?|token usage|usage)\s*:", re.I)


def _clean_inline_emphasis(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"^(?:[*_`]+\s*)+", "", text)
    text = re.sub(r"(?:\s*[*_`]+)+$", "", text)
    return text.strip()


def _section_label_fragments(text: str) -> list[tuple[str, str]] | None:
    match = _SECTION_LABEL_RE.match(str(text or ""))
    if not match:
        return None
    indent, marker, label, rest = match.groups()
    label = _clean_inline_emphasis(label)
    rest = _clean_inline_emphasis(rest)
    if not label:
        return None
    if len(label) == 1 and str(text).lstrip().startswith(f"{label}:"):
        return None
    fragments: list[tuple[str, str]] = [("class:mo-response", indent)] if indent else []
    if marker and rest:
        fragments.append(("class:response-bullet-marker", f"{marker} "))
    fragments.append(("class:response-heading", f"{label}:"))
    if rest:
        fragments.append(("class:mo-response", f" {rest}"))
    return fragments


def response_line_fragments(line: str) -> list[tuple[str, str]]:
    """Return one logical response line with the current lightweight typography."""
    text = str(line)
    stripped = text.strip()
    if not stripped:
        return [("class:mo-response", text)]
    code_like = text.startswith("      ") or text.startswith("\t")
    section = _section_label_fragments(text)
    if section and (not code_like or stripped.startswith(("-", "*", "•"))):
        return section
    if _TOKEN_LINE_RE.match(text):
        return [("class:response-heading", text)]
    if stripped.endswith(":") and not stripped.startswith(("-", "*", "•")):
        return [("class:response-heading", text)]
    # Code-like lines (4+ space indent or tab-indented). Existing response
    # blocks pass rest lines through with a two-space prefix, so six spaces here
    # preserves the prior effective threshold.
    if code_like:
        return [("class:response-code", text)]
    if section:
        return section
    bullet = re.match(r"^(\s*)([-*•])\s+(.+)$", text)
    if bullet:
        indent, marker, body = bullet.groups()
        body = _clean_inline_emphasis(body)
        words = body.split(maxsplit=2)
        if len(words) >= 2:
            lead = f"{words[0]} {words[1]}"
            rest = f" {words[2]}" if len(words) > 2 else ""
        else:
            lead = body
            rest = ""
        return [
            ("class:mo-response", indent),
            ("class:response-bullet-marker", f"{marker} "),
            ("class:response-bullet-head", lead),
            ("class:response-bullet-rest", rest),
        ]
    return [("class:mo-response", text)]


def _split_markdown_table_row(line: str) -> list[str]:
    raw = str(line or "").strip()
    if "|" not in raw:
        return []
    if raw.startswith("|"):
        raw = raw[1:]
    if raw.endswith("|"):
        raw = raw[:-1]
    cells = [re.sub(r"\s+", " ", cell.strip()) for cell in raw.split("|")]
    return cells if len(cells) >= 2 and any(cells) else []


def _is_markdown_table_separator(cells: list[str]) -> bool:
    if len(cells) < 2:
        return False
    return all(bool(re.fullmatch(r":?-{3,}:?", cell.replace(" ", ""))) for cell in cells)


def _wrap_cell(text: str, width: int) -> list[str]:
    clean = re.sub(r"\s+", " ", str(text or "").strip())
    if not clean:
        return [""]
    words = clean.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        if get_cwidth(word) > width:
            if current:
                lines.append(current)
                current = ""
            chunk = ""
            for char in word:
                if get_cwidth(chunk + char) > width and chunk:
                    lines.append(chunk)
                    chunk = char
                else:
                    chunk += char
            if chunk:
                current = chunk
            continue
        candidate = word if not current else f"{current} {word}"
        if get_cwidth(candidate) <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines or [""]


def _table_total_width(widths: list[int]) -> int:
    # left/right borders + one separator per column + configured cell padding.
    return sum(widths) + ((TABLE_GAP + 1) * len(widths)) + 1


def _table_widths(header: list[str], rows: list[list[str]], max_width: int) -> list[int]:
    count = len(header)
    normalized = [row[:count] + [""] * max(0, count - len(row)) for row in rows]
    natural = [max(TABLE_MIN_CELL_WIDTH, get_cwidth(header[index])) for index in range(count)]
    for row in normalized:
        for index, cell in enumerate(row):
            natural[index] = max(natural[index], min(48, get_cwidth(cell)))
    limit = int(max_width or DEFAULT_RESPONSE_COLUMNS)
    border_overhead = ((TABLE_GAP + 1) * count) + 1
    available = max(count * TABLE_MIN_CELL_WIDTH, limit - border_overhead)
    if _table_total_width(natural) <= limit:
        return natural
    widths = natural[:]
    while sum(widths) > available and max(widths) > TABLE_MIN_CELL_WIDTH:
        index = max(range(count), key=lambda i: widths[i])
        widths[index] -= 1
    return [max(TABLE_MIN_CELL_WIDTH, width) for width in widths]


def _format_table_border(widths: list[int], left: str, mid: str, right: str) -> str:
    return left + mid.join("-" * (width + 2) for width in widths) + right


def _format_table_row(row: list[str], widths: list[int]) -> list[str]:
    cells = [str(cell or "") for cell in row[:len(widths)]] + [""] * max(0, len(widths) - len(row))
    wrapped = [_wrap_cell(cell, widths[index]) for index, cell in enumerate(cells)]
    height = max(len(lines) for lines in wrapped)
    rendered: list[str] = []
    for line_index in range(height):
        parts: list[str] = []
        for col_index, lines in enumerate(wrapped):
            value = lines[line_index] if line_index < len(lines) else ""
            pad = max(0, widths[col_index] - get_cwidth(value))
            parts.append(value + " " * pad)
        rendered.append("| " + " | ".join(parts) + " |")
    return rendered


def _format_markdown_table(header: list[str], rows: list[list[str]], *, max_width: int = DEFAULT_RESPONSE_COLUMNS) -> list[str]:
    width_count = len(header)
    normalized = [row[:width_count] + [""] * max(0, width_count - len(row)) for row in rows]
    widths = _table_widths(header, normalized, max_width)
    rendered = [_format_table_border(widths, "+", "+", "+")]
    rendered.extend(_format_table_row(header, widths))
    rendered.append(_format_table_border(widths, "+", "+", "+"))
    for row in normalized:
        rendered.extend(_format_table_row(row, widths))
    rendered.append(_format_table_border(widths, "+", "+", "+"))
    return rendered


def _normalize_markdown_table_lines(text: str, *, columns: int = DEFAULT_RESPONSE_COLUMNS) -> list[tuple[str, bool]]:
    source = str(text or "")
    lines = source.splitlines()
    output: list[tuple[str, bool]] = []
    in_code = False
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_code = not in_code
            output.append((line, False))
            index += 1
            continue
        if not in_code and re.fullmatch(r"[-_\u2500]{3,}", stripped):
            output.append(("", False))
            index += 1
            continue
        if not in_code and index + 1 < len(lines):
            header = _split_markdown_table_row(line)
            separator = _split_markdown_table_row(lines[index + 1])
            if header and _is_markdown_table_separator(separator):
                rows: list[list[str]] = []
                index += 2
                while index < len(lines):
                    row = _split_markdown_table_row(lines[index])
                    if not row or _is_markdown_table_separator(row):
                        break
                    rows.append(row)
                    index += 1
                formatted = _format_markdown_table(header, rows, max_width=max(40, int(columns or DEFAULT_RESPONSE_COLUMNS) - 4))
                output.extend((formatted_line, True) for formatted_line in formatted)
                continue
        # Prose lines pass through unchanged — natural word-wrap (visual_rows)
        # handles width. (IFDEV05 P1-004: an earlier sentence-split here chopped
        # every multi-sentence paragraph onto separate lines, breaking flow.)
        output.append((line, False))
        index += 1
    return output


def normalize_markdown_tables(text: str, *, columns: int = DEFAULT_RESPONSE_COLUMNS) -> str:
    """Convert simple Markdown tables into bordered, wrapped terminal rows."""
    return "\n".join(line for line, _is_table in _normalize_markdown_table_lines(text, columns=columns))


def _strip_response_markdown_lines(lines: list[tuple[str, bool]]) -> list[tuple[str, bool]]:
    result: list[tuple[str, bool]] = []
    in_code_block = False
    for line, is_table in lines:
        s = line.strip()
        if not is_table and (s.startswith("```") or s.startswith("~~~")):
            in_code_block = not in_code_block
            continue
        if in_code_block:
            result.append(("    " + line.rstrip(), False))
            continue
        if is_table:
            clean = re.sub(r"\*\*(.+?)\*\*", r"\1", line.rstrip())
            clean = re.sub(r"`(.+?)`", r"\1", clean)
            result.append((clean, True))
            continue
        if line.startswith(("    ", "\t")):
            result.append(("    " + line.rstrip(), False))
            continue
        h = re.match(r"^(#{1,4})\s+(.+)$", s)
        if h:
            result.append((f"  {h.group(2)}", False))
            continue
        if re.fullmatch(r"[-\u2500]{3,}", s):
            result.append(("  " + "\u2500" * 40, False))
            continue
        if re.fullmatch(r"`{1,3}", s):
            continue
        if not s:
            result.append(("", False))
            continue
        clean = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
        clean = re.sub(r"`(.+?)`", r"\1", clean)
        if re.match(r"^[-*\u2022]\s", clean):
            result.append((f"    {clean}", False))
        else:
            result.append((f"  {clean}", False))
    return result


def response_block_fragment_lines(text: str, *, columns: int = DEFAULT_RESPONSE_COLUMNS, hide_marker: bool = False) -> list[list[tuple[str, str]]]:
    """Return logical transcript lines for an assistant response block."""
    lines = _strip_response_markdown_lines(_normalize_markdown_table_lines(str(text or ""), columns=columns))
    if not lines:
        return []
    first, first_is_table = lines[0]
    marker = "  " if hide_marker else "* "
    if first_is_table:
        rendered: list[list[tuple[str, str]]] = [[("class:mo-marker", marker), ("class:mo-response", first.lstrip())]]
    else:
        rendered = [[("class:mo-marker", marker)] + response_line_fragments(first.lstrip())]
    for line, is_table in lines[1:]:
        if is_table:
            rendered.append([("class:mo-response", f"  {line.rstrip()}")])
        elif line.startswith(("    ", "\t")):
            stripped = line.lstrip()
            if stripped.startswith(("- ", "* ", "• ")):
                rendered.append(response_line_fragments(f"  {stripped}"))
            else:
                rendered.append(response_line_fragments(f"  {line}"))
        else:
            rendered.append(response_line_fragments(f"  {line.lstrip()}"))
    return rendered
