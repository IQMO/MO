"""Lightweight process signal handling shared by resident entrypoints."""
from __future__ import annotations

import signal
import threading


def install_signal_handlers(stop_event: threading.Event) -> None:
    """Set ``stop_event`` for SIGINT/SIGTERM when called on the main thread."""
    if threading.current_thread() is not threading.main_thread():
        return

    def _handle(_signum: int, _frame: object) -> None:
        stop_event.set()

    for name in ("SIGINT", "SIGTERM"):
        candidate = getattr(signal, name, None)
        if candidate is None:
            continue
        try:
            signal.signal(candidate, _handle)
        except Exception:
            pass
