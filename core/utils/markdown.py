"""Shared source-line Markdown recognition, not a renderer or knowledge store."""
from __future__ import annotations

import re


def prose_lines(text: str) -> list[tuple[int, str]]:
    """Keep source line numbers while excluding fenced examples and comments."""
    text = re.sub(r"<!--[\s\S]*?-->", lambda m: "\n" * m.group().count("\n"), text)
    lines: list[tuple[int, str]] = []
    fence = ""
    for number, line in enumerate(text.splitlines(), 1):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = ""
            continue
        if marker:
            fence = marker[1]
            continue
        lines.append((number, line))
    return lines


def headings(lines: list[tuple[int, str]]) -> list[dict[str, int | str]]:
    """Recognize ATX/setext headings in prose, retaining title-line locations."""
    result: list[dict[str, int | str]] = []
    previous = ""
    previous_number = 0
    for number, line in lines:
        heading = re.match(r"^ {0,3}(#{1,6})(?:\s+(.*?)\s*#*\s*|\s*)$", line)
        if heading:
            result.append({"title": heading[2] or "", "line": number, "level": len(heading[1])})
        elif previous.strip() and number == previous_number + 1 and re.fullmatch(r" {0,3}(?:=+|-+)\s*", line):
            result.append({"title": previous.strip(), "line": previous_number, "level": 1 if line.strip()[0] == "=" else 2})
        previous, previous_number = ("", number) if heading else (line, number)
    return result
