"""Small text-list normalization shared by context builders."""
from __future__ import annotations

import re
from collections.abc import Iterable


def unique_text(values: Iterable[str]) -> list[str]:
    """Keep first-seen text while deduplicating collapsed whitespace by case."""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = re.sub(r"\s+", " ", str(value or "")).strip().casefold()
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return result
