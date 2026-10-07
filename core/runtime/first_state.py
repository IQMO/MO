"""A build started at launch and served once, while fresh, to the first request that waits on it.

A Desktop app's window takes 1.5-3 s to start. Work its first view waits on (the Dashboard's
state, MO Files' Hub sources) starts alongside the window instead of after it; every later
request builds fresh.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

# A launch build serves only while this fresh; past it, the first request builds anew.
FRESH_SECONDS = 15.0


class LaunchBuild:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, Any] | None = None

    def start(self, build: Callable[[], Any], *, name: str) -> None:
        holder: dict[str, Any] = {"started": time.monotonic()}

        def run() -> None:
            try:
                holder["value"] = build()
            except Exception:
                pass   # the first request builds again itself and meets the error there

        thread = threading.Thread(target=run, name=name, daemon=True)
        holder["thread"] = thread
        with self._lock:
            self._pending = holder
        thread.start()

    def take(self, build: Callable[[], Any]) -> Any:
        """The launch build when one is pending, fresh and succeeded; otherwise ``build()``."""
        with self._lock:
            holder, self._pending = self._pending, None
        if holder is not None and time.monotonic() - holder["started"] < FRESH_SECONDS:
            holder["thread"].join(timeout=30)
            if "value" in holder:
                return holder["value"]
        return build()
