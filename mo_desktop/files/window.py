"""Desktop launcher for the native MO Files WebView board."""
from __future__ import annotations

from typing import Any

from mo_desktop.app_window import NativeAppWindow


class MoFilesWindow(NativeAppWindow):
    app_module = "mo_desktop.files.app"
    app_title = "MO Files"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        super().__init__(config)
        self.source_id = "desktop-local"
        self.location_id = ""
        self.active_transfer_count = 0
        self._board_ready = False

    def show(self, source_id: str = "", *, on_source: Any = None,
             on_started: Any = None, on_ready: Any = None) -> None:
        if source_id:
            self.source_id = source_id
        if not self.is_running():
            self._board_ready = False
        super().show(source_id=self.source_id, location_id=self.location_id,
                     on_source=on_source, on_started=on_started, on_ready=on_ready)

    def _handle_status(self, status: dict[str, Any]) -> None:
        if status.get("kind") == "transfer_count":
            self.active_transfer_count = max(0, int(status.get("count") or 0))
        elif status.get("kind") == "board_ready":
            self._board_ready = True
