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


_ERROR_LINE_RE = re.compile(
    r"(?i)(?:\b(?:error|failed|failure|fatal|exception|traceback|panic)\b|"
    r"assert(?:ion)?error|\[exit code\s+-?[1-9]\d*\]|\[\.\.\.truncated:)"
)


def words(text: str) -> set[str]:
    """Tokenize text into a set of lowercase alphanumeric+hyphen words."""
    return set(re.findall(r"[a-z0-9_+-]+", str(text or "").lower()))


def format_blocker_reason(kind: str, reason: str) -> str:
    """Format an optional normalized cause before a blocker/stop reason."""
    reason_text = str(reason or "").strip()
    kind_text = str(kind or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not reason_text:
        return "" if kind_text in {"", "none"} else kind_text
    if kind_text in {"", "none"}:
        return reason_text
    prefix = f"{kind_text}:"
    if reason_text.lower().startswith(prefix):
        return reason_text
    return f"{kind_text}: {reason_text}"


def token_aware_truncation_enabled() -> bool:
    raw = os.environ.get("MO_TOKEN_AWARE_TRUNCATION", "0")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def chars_to_tokens(text: str) -> int:
    """Rough token estimate: Latin ~4 chars/token, non-Latin ~1.5."""
    value = str(text or "")
    latin = len(re.findall(r"[\x00-\x7f]", value))
    non_latin = len(value) - latin
    return int(latin / 4 + non_latin / 1.5)


def _cap_text_by_chars(value: str, max_chars: int, marker: str) -> str:
    """Apply the hard character contract, retaining a marker when it can fit."""
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    marker_block = f"\n{marker}"
    if len(marker_block) >= max_chars:
        return value[:max_chars]
    limit = max_chars - len(marker_block)
    raw_clipped = value[:limit].rstrip()
    line_clipped = raw_clipped.rsplit("\n", 1)[0].rstrip()
    clipped = line_clipped if len(line_clipped) >= max(1, limit // 2) else raw_clipped
    return f"{clipped}{marker_block}".strip()


def cap_text(text: str, max_chars: int, *, marker: str = "[truncated]") -> str:
    """Cap text at a character budget with a visible truncation marker.

    The one shared owner for marker-labeled context capping: honors the
    ``MO_TOKEN_AWARE_TRUNCATION`` switch without changing the caller's hard
    character contract. The token-aware path treats the established Latin
    character allowance as its approximate token budget and tightens denser
    scripts; the final character cap remains authoritative. A short heading
    followed by one long content line must not collapse to the heading alone.
    """
    value = str(text or "").strip()
    try:
        limit = int(max_chars)
    except (TypeError, ValueError):
        return value
    if limit <= 0 or len(value) <= limit:
        return value
    if token_aware_truncation_enabled():
        latin_token_budget = max(1, limit // 4)
        value = cap_by_tokens(value, latin_token_budget, marker)
    return _cap_text_by_chars(value, limit, marker)


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


def _error_excerpt(text: str, max_chars: int) -> str:
    """Return bounded, de-duplicated error lines from a text middle section."""
    if max_chars <= 0:
        return ""
    selected: list[str] = []
    seen: set[str] = set()
    used = 0
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or not _ERROR_LINE_RE.search(line):
            continue
        line = line[:240]
        key = line.casefold()
        if key in seen:
            continue
        addition = len(line) + (1 if selected else 0)
        if used + addition > max_chars:
            break
        selected.append(line)
        seen.add(key)
        used += addition
        if len(selected) >= 8:
            break
    return "\n".join(selected)


def cap_text_evidence(text: str, max_chars: int) -> str:
    """Bound text while retaining its beginning, selected errors, and ending.

    Tool output often puts setup at the start and the exit status or summary at
    the end. A head-only slice silently removes the latter. This helper is a
    deterministic safety cap, not semantic compression: it labels the exact
    size of the omitted middle and never claims the result is complete.
    """
    value = str(text or "")
    try:
        limit = int(max_chars)
    except (TypeError, ValueError):
        return value
    if limit <= 0 or len(value) <= limit:
        return value
    # Tiny limits cannot carry an honest marker plus useful evidence. They are
    # not used by MO's runtime caps, but keeping the contract bounded matters.
    if limit < 96:
        return value[:limit]

    marker_reserve = len(f"\n[...truncated: {len(value):,} middle source chars omitted...]\n")
    excerpt_budget = min(1_200, max(0, limit // 5))
    # Select errors from the broad middle before final slice sizes are known;
    # head/tail duplication is harmless but de-duplicated inside the excerpt.
    middle_probe = value[limit // 3: max(limit // 3, len(value) - limit // 3)]
    errors = _error_excerpt(middle_probe, excerpt_budget)
    error_block = f"[selected error lines from omitted middle]\n{errors}\n" if errors else ""

    available = limit - marker_reserve - len(error_block)
    if available < 2:
        return value[:limit]
    head_len = max(1, (available * 3) // 5)
    tail_len = max(1, available - head_len)
    omitted = max(0, len(value) - head_len - tail_len)
    marker = f"\n[...truncated: {omitted:,} middle source chars omitted...]\n"

    # The reserve used the original length (the largest possible digit count),
    # so the final marker cannot exceed it and the output stays within limit.
    return value[:head_len] + marker + error_block + value[-tail_len:]
