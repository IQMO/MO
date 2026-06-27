"""Helpers for recognizing compact market symbols in user requests."""
from __future__ import annotations

import re


_QUOTE_ASSETS = (
    "USDT",
    "USDC",
    "USD",
    "BTC",
    "ETH",
    "EUR",
    "GBP",
    "BNB",
)

_QUOTE_PATTERN = "|".join(_QUOTE_ASSETS)
_COMPACT_PAIR_RE = re.compile(rf"\b[A-Z0-9]{{2,15}}(?:{_QUOTE_PATTERN})\b", re.I)
_SEPARATED_PAIR_RE = re.compile(rf"\b[A-Z0-9]{{2,15}}\s*[/:\-]\s*(?:{_QUOTE_PATTERN})\b", re.I)


def looks_like_market_pair(text: str) -> bool:
    """Return true for compact/separated asset pairs such as NEARUSDT or NEAR/USDT."""
    value = str(text or "")
    if not value:
        return False
    return bool(_COMPACT_PAIR_RE.search(value) or _SEPARATED_PAIR_RE.search(value))


def market_pair_intent_terms(text: str) -> str:
    """Append generic trading intent terms when the user typed a market pair."""
    value = str(text or "")
    if not looks_like_market_pair(value):
        return value
    return (
        f"{value} trading trade coin crypto market pair symbol setup analysis "
        "technical indicator chart price"
    )
