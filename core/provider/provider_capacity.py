"""Provider-route capacity tracking for reactive fallback selection.

Tracks capacity from response headers and error messages for one exact
provider/model route.  The selected route is still tried reactively; this state
only prevents a failed route from being selected again as a fallback candidate.
"""

from __future__ import annotations

import time
from typing import Any

from .headers import normalize_headers

# Default block duration (seconds) when rate-limited by error with no Retry-After.
DEFAULT_ERROR_BLOCK_SECONDS = 30

# How long to remember "remaining=0" with no reset timestamp before clearing.
UNKNOWN_RESET_GRACE_SECONDS = 120

ACTION_REQUIRED_ERROR_KINDS = frozenset({
    "balance",
    "quota",
    "auth",
    "permission",
    "model",
    "policy",
})


class ProviderCapacity:
    """In-memory rate-limit tracker scoped to exact provider/model routes."""

    def __init__(self) -> None:
        self._state: dict[str, dict] = {}  # provider_key -> state

    # ── public API ────────────────────────────────────────────────

    def can_accept(self, provider_name: str, model_name: str = "") -> bool:
        """Return True if the exact route is not known to be unavailable."""
        s = self._get(provider_name, model_name)
        now = time.time()

        # Action-required failures do not heal with time.  They clear only from
        # explicit clear(), positive route evidence, or process restart.
        if s["persistent_block"]:
            return False

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

    def record_headers(
        self,
        provider_name: str,
        headers: dict | Any,
        model_name: str = "",
    ) -> None:
        """Parse rate-limit headers for one successful provider/model route."""
        s = self._get(provider_name, model_name)
        h = normalize_headers(headers)
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
                s["persistent_block"] = False
                s["blocked_kind"] = ""
            s["exhausted_at"] = None
            s["last_error"] = ""

    def record_success(self, provider_name: str, model_name: str = "") -> None:
        """Clear stale unavailability after this exact route succeeds."""
        s = self._get(provider_name, model_name)
        s["remaining"] = None
        s["reset_at"] = None
        s["blocked_until"] = None
        s["persistent_block"] = False
        s["blocked_kind"] = ""
        s["exhausted_at"] = None
        s["last_error"] = ""

    def record_error(
        self,
        provider_name: str,
        error_msg: str,
        model_name: str = "",
    ) -> None:
        """Block the exact provider/model route that returned an actionable error.

        Called from the error-handling paths when is_rate_limit_error() or
        fallback_reason() returns a non-None reason (policy, balance, auth, model,
        permission, rate/capacity, server, or transport failure).
        Also called for empty provider responses (MO Desktop, Agent, Goal paths).
        Parses Retry-After from the error body when present.
        """
        s = self._get(provider_name, model_name)
        s["last_error"] = str(error_msg)[:200]

        try:
            from .provider import provider_error_kind, provider_retry_after_seconds
            kind = provider_error_kind(error_msg) or ""
            retry_after = provider_retry_after_seconds(error_msg)
        except Exception:
            kind = ""
            retry_after = None

        s["blocked_kind"] = kind
        if kind in ACTION_REQUIRED_ERROR_KINDS:
            s["persistent_block"] = True
            s["blocked_until"] = None
            return

        block_seconds = (
            retry_after
            if retry_after is not None
            else DEFAULT_ERROR_BLOCK_SECONDS
        )
        s["persistent_block"] = False
        s["blocked_until"] = time.time() + block_seconds

    def clear(self, provider_name: str = "", model_name: str = "") -> None:
        """Clear one route, one provider's routes, or all capacity state."""
        if provider_name:
            if model_name:
                self._state.pop(self._route_key(provider_name, model_name), None)
            else:
                prefix = f"{self._route_key(provider_name)}/"
                for key in list(self._state):
                    if key == self._route_key(provider_name) or key.startswith(prefix):
                        self._state.pop(key, None)
        else:
            self._state.clear()

    def snapshot(self, provider_name: str, model_name: str = "") -> dict[str, Any]:
        """Observe one route without creating, expiring or clearing routing state."""
        stored = self._state.get(self._route_key(provider_name, model_name))
        if stored is None:
            return {"state": "unobserved", "kind": "", "retry_after_seconds": None}
        state = dict(stored)
        now = time.time()
        deadline = state["blocked_until"] or 0.0
        if state["remaining"] is not None and state["remaining"] <= 0:
            reset = state["reset_at"]
            if reset is None:
                reset = (state["exhausted_at"] or now) + UNKNOWN_RESET_GRACE_SECONDS
            deadline = max(deadline, reset)
        blocked = bool(state["persistent_block"] or deadline > now)
        return {
            "state": "blocked" if blocked else "no_known_block",
            "kind": state["blocked_kind"] if blocked else "",
            "retry_after_seconds": (
                max(1, int(deadline - now + 0.999))
                if blocked and not state["persistent_block"] else None
            ),
        }

    # ── internal ──────────────────────────────────────────────────

    @staticmethod
    def _route_key(provider_name: str, model_name: str = "") -> str:
        provider = str(provider_name or "").strip().lower()
        model = str(model_name or "").strip().lower()
        return f"{provider}/{model}" if model else provider

    def _get(self, provider_name: str, model_name: str = "") -> dict:
        key = self._route_key(provider_name, model_name)
        if key not in self._state:
            self._state[key] = {
                "remaining": None,       # None=unknown, int=requests remaining
                "reset_at": None,        # Unix timestamp when limit resets
                "blocked_until": None,   # Error-block expiry timestamp
                "persistent_block": False,  # Action-required route failure
                "blocked_kind": "",      # provider_error_kind() diagnostic
                "exhausted_at": None,    # When remaining hit 0 (for grace period)
                "last_error": "",        # Most recent error (diagnostic)
            }
        return self._state[key]

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
