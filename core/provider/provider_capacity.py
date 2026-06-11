"""Provider capacity tracking — rate-limit awareness for proactive fallback.

Tracks per-provider capacity from response headers and error messages
so MO can skip exhausted providers BEFORE making a call, instead of
reacting to HTTP errors after the fact.
"""

from __future__ import annotations

import re
import time
from typing import Any

# Default block duration (seconds) when rate-limited by error with no Retry-After.
DEFAULT_ERROR_BLOCK_SECONDS = 30

# How long to remember "remaining=0" with no reset timestamp before clearing.
UNKNOWN_RESET_GRACE_SECONDS = 120


class ProviderCapacity:
    """In-memory rate-limit tracker for provider routing."""

    def __init__(self) -> None:
        self._state: dict[str, dict] = {}  # provider_key -> state

    # ── public API ────────────────────────────────────────────────

    def can_accept(self, provider_name: str) -> bool:
        """Return True if the provider is not known to be rate-limited."""
        s = self._get(provider_name)
        now = time.time()

        # Check error-based block
        if s["blocked_until"] is not None and now < s["blocked_until"]:
            return False

        # Check header-based remaining counter
        if s["remaining"] is not None and s["remaining"] <= 0:
            if s["reset_at"] is not None:
                if now >= s["reset_at"]:
                    # Reset window passed — clear and allow
                    s["remaining"] = None
                    s["reset_at"] = None
                    return True
                return False
            # No reset timestamp — block for a grace period then clear
            if s["exhausted_at"] is None:
                s["exhausted_at"] = now
            if now - s["exhausted_at"] >= UNKNOWN_RESET_GRACE_SECONDS:
                s["remaining"] = None
                s["exhausted_at"] = None
                return True
            return False

        # Stale blocked_until cleanup
        if s["blocked_until"] is not None and now >= s["blocked_until"]:
            s["blocked_until"] = None

        return True

    def all_exhausted(self, provider_names: list[str]) -> bool:
        """Return True when every named provider is known to be exhausted."""
        if not provider_names:
            return True
        return not any(self.can_accept(name) for name in provider_names)

    def record_headers(self, provider_name: str, headers: dict | Any) -> None:
        """Parse rate-limit headers from a successful provider response."""
        s = self._get(provider_name)
        h = self._normalize_headers(headers)
        if not h:
            return

        # Remaining requests / tokens
        for key in ("x-ratelimit-remaining-requests", "x-ratelimit-remaining", "x-ratelimit-remaining-tokens"):
            if key in h:
                try:
                    val = int(h[key])
                    s["remaining"] = val
                    if val <= 0 and s["exhausted_at"] is None:
                        s["exhausted_at"] = time.time()
                    break
                except (ValueError, TypeError):
                    pass

        # Reset timestamp
        for key in ("x-ratelimit-reset-requests", "x-ratelimit-reset", "x-ratelimit-reset-tokens"):
            if key in h:
                try:
                    s["reset_at"] = float(h[key])
                    break
                except (ValueError, TypeError):
                    pass

        # Retry-After (always respected — server directive)
        retry = h.get("retry-after")
        retry_set = False
        if retry is not None:
            try:
                s["blocked_until"] = time.time() + float(retry)
                retry_set = True
            except (ValueError, TypeError):
                pass

        # If we now have positive remaining, clear error-based block (but not retry-after)
        if s["remaining"] is not None and s["remaining"] > 0:
            if not retry_set:
                s["blocked_until"] = None
            s["exhausted_at"] = None
            s["last_error"] = ""

    def record_error(self, provider_name: str, error_msg: str) -> None:
        """Block a provider that returned an actionable error.

        Called from the error-handling paths when is_rate_limit_error() or
        fallback_reason() returns a non-None reason (402 balance, 401 auth/disabled,
        403 permission, 429 rate limit, 5xx server error, timeout/connection).
        Also called for empty provider responses (Ghost, Agent, Goal paths).
        Parses Retry-After from the error body when present.
        """
        s = self._get(provider_name)
        s["last_error"] = str(error_msg)[:200]

        block_seconds = DEFAULT_ERROR_BLOCK_SECONDS
        # Try to extract Retry-After from error body
        lowered = str(error_msg).lower()
        retry_match = re.search(r"retry[_-]?after[:\s]+(\d+)", lowered)
        if retry_match:
            try:
                block_seconds = int(retry_match.group(1))
            except ValueError:
                pass
        # Also check for a Unix timestamp
        ts_match = re.search(r"retry[_-]?after[:\s]+(\d{10})", lowered)
        if ts_match:
            try:
                ts = int(ts_match.group(1))
                block_seconds = max(1, ts - int(time.time()))
            except ValueError:
                pass
        s["blocked_until"] = time.time() + block_seconds

    def clear(self, provider_name: str = "") -> None:
        """Clear capacity state for a provider, or all if empty."""
        if provider_name:
            key = str(provider_name).strip().lower()
            self._state.pop(key, None)
        else:
            self._state.clear()

    # ── internal ──────────────────────────────────────────────────

    def _get(self, provider_name: str) -> dict:
        key = str(provider_name or "").strip().lower()
        if key not in self._state:
            self._state[key] = {
                "remaining": None,       # None=unknown, int=requests remaining
                "reset_at": None,        # Unix timestamp when limit resets
                "blocked_until": None,   # Error-block expiry timestamp
                "exhausted_at": None,    # When remaining hit 0 (for grace period)
                "last_error": "",        # Most recent error (diagnostic)
            }
        return self._state[key]

    @staticmethod
    def _normalize_headers(headers: dict | Any) -> dict[str, str]:
        """Normalise headers to a lowercase-keyed dict.

        Handles httpx.Headers, plain dicts, and SimpleNamespace-like objects.
        """
        if headers is None:
            return {}
        if isinstance(headers, dict):
            return {str(k).lower(): str(v) for k, v in headers.items()}
        # httpx.Headers / mapping
        result: dict[str, str] = {}
        try:
            for k, v in headers.items():
                result[str(k).lower()] = str(v)
        except (TypeError, AttributeError):
            pass
        return result


# ── module-level singleton ────────────────────────────────────────

_capacity: ProviderCapacity | None = None


def get_capacity() -> ProviderCapacity:
    """Return the module-level ProviderCapacity singleton."""
    global _capacity
    if _capacity is None:
        _capacity = ProviderCapacity()
    return _capacity


def reset_capacity() -> None:
    """Reset the singleton (primarily for tests)."""
    global _capacity
    _capacity = None
