"""Desktop launcher for the native MO Phone workspace."""
from __future__ import annotations

from typing import Any
from mo_desktop.app_window import NativeAppWindow


class MoPhoneWindow(NativeAppWindow):
    app_module = "mo_desktop.phone.app"
    app_title = "MO Phone"

    def __init__(self, gui: Any, config: dict[str, Any] | None = None, *,
                 on_notice: Any = None, on_open_files: Any = None,
                 live_control_status: Any = None) -> None:
        super().__init__(config)
        self.gui = gui
        self.on_notice = on_notice
        self.on_open_files = on_open_files
        self.live_control_status = live_control_status
        self._trackpad_running = False

    @property
    def trackpad_running(self) -> bool:
        return self._trackpad_running and self.is_running()

    def _host_status(self) -> str:
        try:
            return str(self.live_control_status() if self.live_control_status else "")[:80]
        except Exception:
            return "unavailable"

    def show(self, *, on_source: Any = None, on_started: Any = None, on_ready: Any = None) -> None:
        if not self.is_running():
            self._trackpad_running = False
        super().show(on_source=on_source, on_started=on_started, on_ready=on_ready,
                     host_status=self._host_status(), files_available=callable(self.on_open_files))

    def refresh_live_control_status(self) -> None:
        if self.is_running():
            self._send({"cmd": "host_status", "host_status": self._host_status()})

    def open_trackpad(self) -> None:
        self.open_action("trackpad")

    def open_action(self, action: str) -> None:
        """Start Trackpad or Mirror on the ready phone, opening the window first if needed."""
        if not self.is_running():
            self.show()
        self._send({"cmd": action})

    def _handle_status(self, status: dict[str, Any]) -> None:
        kind = status.get("kind")
        if kind == "phone_state":
            self._trackpad_running = status.get("trackpad") is True
        elif kind == "open_files":
            def open_files() -> None:
                ok = False
                try:
                    if callable(self.on_open_files):
                        self.on_open_files()
                        ok = True
                except Exception:
                    ok = False
                finally:
                    if self.is_running():
                        self._send({"cmd": "files_result", "ok": ok})
            self.gui.schedule(0, open_files)
        elif kind == "notice" and self.on_notice:
            self.gui.schedule(0, lambda: self.on_notice(str(status.get("title") or "Phone"), str(status.get("detail") or "")))

    def destroy(self) -> None:
        super().destroy()
        self._trackpad_running = False
