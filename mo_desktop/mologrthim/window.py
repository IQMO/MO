"""Launcher for Mologrthim's native WebView floor (Desktop tray, the four-cube launcher, /role show)."""
from __future__ import annotations

from typing import Any

from mo_desktop.app_window import NativeAppWindow


class MologrthimWindow(NativeAppWindow):
    app_module = "mo_desktop.mologrthim.app"
    app_title = "Mologrthim"

    def show(self, focus: str = "", *, on_source: Any = None, on_started: Any = None,
             on_ready: Any = None) -> None:
        """Open (or bring back) the floor; ``focus`` selects that MO's bay."""
        super().show(focus=str(focus or ""), on_source=on_source, on_started=on_started, on_ready=on_ready)
