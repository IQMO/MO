"""Lightweight behavior shared by local and remote workspace transports."""
from __future__ import annotations

from typing import Callable


class WorkspaceChangeNotifier:
    """Notify a UI owner without letting callback failure stop a transport."""

    on_change: Callable[[], None] | None

    def _notify(self) -> None:
        callback = self.on_change
        if callback is not None:
            try:
                callback()
            except Exception:
                pass
