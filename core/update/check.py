"""Live self-update check — surfaces "N commits behind upstream" with no user action.

Mirrors ``core/provider/deepseek_balance``: a throttled background thread runs
``git fetch`` + ``git rev-list --count HEAD..@{u}`` and caches the count; the read
path (``update_count`` / ``update_status_text``) NEVER blocks, so the TUI footer can
call it on every frame. Best-effort: offline, a zip (non-git) download, or no
upstream remote -> no notice (returns None).
"""
from __future__ import annotations

import threading
import time
from typing import Any

from ._git_utils import _git, _is_git_checkout

_TTL_SECONDS = 3600.0  # fetch at most once an hour
_FETCH_TIMEOUT = 20.0

_lock = threading.Lock()
_state: dict[str, Any] = {"behind": None, "checked_at": 0.0, "checking": False}


def _refresh() -> None:
    behind: int | None = None
    try:
        if _is_git_checkout():
            _git(["fetch", "--quiet"], timeout=_FETCH_TIMEOUT)  # offline -> ignored
            out = _git(["rev-list", "--count", "HEAD..@{u}"], timeout=10)
            if out and out.returncode == 0:
                behind = int((out.stdout or "0").strip() or "0")
    except Exception:
        behind = None
    with _lock:
        _state["behind"] = behind
        _state["checked_at"] = time.time()
        _state["checking"] = False


def update_count(*, enabled: bool = True, ttl: float = _TTL_SECONDS) -> int | None:
    """Cached number of commits the checkout is behind upstream, or None
    (offline / non-git / unknown). Triggers a throttled background refresh when
    stale; never blocks the caller, so it is safe on the footer render path."""
    if not enabled:
        return None
    now = time.time()
    with _lock:
        stale = (now - float(_state["checked_at"] or 0.0)) > ttl
        if stale and not _state["checking"]:
            _state["checking"] = True
            threading.Thread(target=_refresh, daemon=True).start()
        behind = _state["behind"]
    return behind if isinstance(behind, int) else None


def update_status_text(*, enabled: bool = True) -> str | None:
    """Footer/notice string like ``⬆ 3 updates`` when behind upstream, else None."""
    n = update_count(enabled=enabled)
    if not n or n <= 0:
        return None
    return f"⬆ {n} update{'s' if n != 1 else ''}"
