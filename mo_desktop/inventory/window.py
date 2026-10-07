"""Launcher for Inventory's native WebView basket (the four-cube launcher's Work group)."""
from __future__ import annotations

from typing import Any

from mo_desktop.app_window import NativeAppWindow


class InventoryWindow(NativeAppWindow):
    app_module = "mo_desktop.inventory.app"
    app_title = "Inventory"

    def show(self, *, on_source: Any = None, on_started: Any = None, on_ready: Any = None) -> None:
        super().show(on_source=on_source, on_started=on_started, on_ready=on_ready)
