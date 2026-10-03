"""Telegram formatting helpers."""
from __future__ import annotations


def compact_for_telegram(text: str, *, limit: int = 3500) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 80)] + "\n\n[truncated for Telegram; full output remains in MO session/logs]"
