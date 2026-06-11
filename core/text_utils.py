"""Shared text utilities for MO.

Word tokenization remains the default public helper. Token-aware caps are gated
behind ``MO_TOKEN_AWARE_TRUNCATION=1`` so existing character limits stay stable.
"""
from __future__ import annotations

import os
import re


DEFAULT_CONTEXT_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "have", "what", "when", "where", "how",
    "please", "about", "into", "onto", "your", "you", "are", "can", "could", "would", "should",
    "build", "create", "make", "fix", "review", "investigate", "check", "analyze", "analyse", "work",
})


def words(text: str) -> set[str]:
    """Tokenize text into a set of lowercase alphanumeric+hyphen words."""
    return set(re.findall(r"[a-z0-9_+-]+", str(text or "").lower()))


def token_aware_truncation_enabled() -> bool:
    raw = os.environ.get("MO_TOKEN_AWARE_TRUNCATION", "0")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def chars_to_tokens(text: str) -> int:
    """Rough token estimate: Latin ~4 chars/token, non-Latin ~1.5."""
    value = str(text or "")
    latin = len(re.findall(r"[\x00-\x7f]", value))
    non_latin = len(value) - latin
    return int(latin / 4 + non_latin / 1.5)


def cap_by_tokens(text: str, max_tokens: int, marker: str = "[truncated]") -> str:
    """Cap text by estimated tokens using binary search; no external tokenizer."""
    value = str(text or "")
    if max_tokens <= 0 or chars_to_tokens(value) <= max_tokens:
        return value
    lo, hi = 0, len(value)
    marker_tokens = max(1, chars_to_tokens(marker))
    budget = max(1, max_tokens - marker_tokens)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if chars_to_tokens(value[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return f"{value[:lo].rstrip()}\n{marker}".strip()
