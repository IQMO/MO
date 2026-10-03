"""OpenAI Codex OAuth quota usage — for the TUI footer.

The Codex OAuth auth (``~/.codex/auth.json``, ChatGPT backend) exposes no account
*balance* — ``x-codex-credits-balance`` is empty and ``has-credits`` is False. What
it DOES return, on the headers of every Responses call, is live rate-limit usage:
percent of each limit window consumed and when it resets. So for Codex the footer
shows USAGE instead of a balance.

There is no cheap standalone usage endpoint, so the snapshot is captured
piggyback from real response headers (``record_from_headers``) — never a separate
network call — and the footer read (``usage_text``) is a cached, non-blocking dict
lookup that computes the reset countdown live from the absolute reset timestamp.
Best-effort: any parse error → no usage shown.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any

from .headers import normalize_headers

_lock = threading.Lock()
# windows: list of {used_percent:int, window_minutes:int, reset_at:float|None}
_state: dict[str, Any] = {"windows": [], "plan": "", "fetched_at": 0.0}


def is_codex_provider(provider: Any) -> bool:
    """True for the OpenAI Codex OAuth provider (matches ``_provider_is_codex``)."""
    if provider is None:
        return False
    name = str(getattr(provider, "name", "") or "").strip().lower()
    api_mode = str(getattr(provider, "api_mode", "") or "").strip().lower()
    return "codex" in name or "codex" in api_mode


def _int(value: str) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _float(value: str) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def record_from_headers(headers: Any) -> None:
    """Parse ``x-codex-*`` rate-limit usage headers into the cached snapshot.

    Only windows with a positive ``window-minutes`` are kept (Codex reports an
    inactive limit as an all-zero secondary window). No-op when the headers carry
    no codex usage, so it is safe to call on every response.
    """
    h = normalize_headers(headers)
    # Opt-in diagnostic (names only — never values, so no secrets): dumps the
    # rate-limit-ish header keys actually present, so a single gpt turn reveals
    # the live header schema if it ever changes prefix. Off unless MO_DEBUG_CODEX_HEADERS.
    if os.environ.get("MO_DEBUG_CODEX_HEADERS"):
        try:
            from ..runtime.backend_monitor import get_monitor
            monitor = get_monitor()
            if monitor:
                monitor.emit("codex_headers_seen", {"keys": sorted(
                    k for k in h if k.startswith("x-") or "ratelimit" in k or "rate-limit" in k
                )})
        except Exception:
            pass
    if not any(k.startswith("x-codex-") for k in h):
        return
    now = time.time()
    windows: list[dict[str, Any]] = []
    for tag in ("primary", "secondary"):
        window_minutes = _int(h.get(f"x-codex-{tag}-window-minutes", "")) or 0
        used = _int(h.get(f"x-codex-{tag}-used-percent", "")) or 0
        reset_at = _float(h.get(f"x-codex-{tag}-reset-at", ""))
        if reset_at is None:
            after = _float(h.get(f"x-codex-{tag}-reset-after-seconds", ""))
            reset_at = now + after if after is not None else None
        # A window is active when it reports a positive window length (historical
        # behavior) OR when it reports REAL usage (>0) with a reset even though
        # window-minutes is absent/zero. Requiring usage>0 (not merely a reset)
        # keeps the historical drop of Codex's inactive secondary window, which
        # reports 0% + an imminent reset and must not surface as "0% · resets <1m".
        active = window_minutes > 0 or (used > 0 and reset_at is not None)
        if not active:
            continue
        windows.append(
            {"used_percent": used, "window_minutes": window_minutes, "reset_at": reset_at}
        )
    with _lock:
        _state["windows"] = windows
        _state["plan"] = str(h.get("x-codex-plan-type", "") or "").strip()
        _state["fetched_at"] = now


def _human_duration(seconds: float) -> str:
    """Compact top-two-unit duration, e.g. ``6d 7h`` / ``2h 5m`` / ``<1m``."""
    total = max(0, int(seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes and not days:
        parts.append(f"{minutes}m")
    if not parts:
        return "<1m"
    return " ".join(parts[:2])


def _window_label(window_minutes: int) -> str:
    """Human window name — only used to disambiguate when >1 window is active."""
    if window_minutes == 10080:
        return "weekly"
    if window_minutes % 1440 == 0:
        return f"{window_minutes // 1440}d"
    if window_minutes % 60 == 0:
        return f"{window_minutes // 60}h"
    return f"{window_minutes}m"


def usage_text(provider: Any) -> str | None:
    """Cached Codex usage string for the footer, or None.

    Non-blocking: reads the snapshot captured from prior response headers and
    computes each reset countdown live. Returns None for non-codex providers or
    before the first Codex response of the session.
    """
    if not is_codex_provider(provider):
        return None
    with _lock:
        windows = [dict(w) for w in (_state.get("windows") or [])]
    if not windows:
        return None
    now = time.time()
    single = len(windows) == 1
    parts: list[str] = []
    for w in windows:
        wm = int(w.get("window_minutes") or 0)
        label = "" if (single or wm <= 0) else f"{_window_label(wm)} "
        segment = f"{label}{int(w['used_percent'])}%"
        reset_at = w.get("reset_at")
        if reset_at:
            segment = f"{segment} · R{_human_duration(float(reset_at) - now).upper()}"
        parts.append(segment)
    return "Codex " + " · ".join(parts)
