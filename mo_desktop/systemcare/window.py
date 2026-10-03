"""Desktop launcher for SystemCare's native maintenance workspace."""
from __future__ import annotations

from typing import Any

from core.systemcare.config import normalized_systemcare_preferences
from core.systemcare.models import ScanMode
from mo_desktop.app_window import NativeAppWindow


class MoSystemCareWindow(NativeAppWindow):
    app_module = "mo_desktop.systemcare.app"
    app_title = "MO SystemCare"

    def __init__(self, parent: Any, config: dict[str, Any] | None = None, *,
                 on_notice: Any = None, on_persist: Any = None,
                 on_state_changed: Any = None, on_request: Any = None, on_schedule: Any = None) -> None:
        super().__init__(config)
        self.parent = parent
        self.on_notice = on_notice
        self.on_persist = on_persist
        self.on_state_changed = on_state_changed
        self.on_request = on_request
        self.on_schedule = on_schedule
        self.mode = ScanMode(normalized_systemcare_preferences(self.config)["scan_mode_default"])

    def show(self, *, start_scan: bool = False, quick_action: str = "", on_source: Any = None,
             on_started: Any = None, on_ready: Any = None) -> None:
        super().show(start_scan=start_scan, quick_action=str(quick_action or ""), on_source=on_source,
                     on_started=on_started, on_ready=on_ready)

    def cancel(self) -> bool:
        if not self.is_running():
            return False
        self._send({"cmd": "cancel"})
        return True

    def start_scan(self) -> None:
        self.show(start_scan=True)

    def close(self) -> None:
        if self.is_running():
            self._send({"cmd": "close"})

    def _post(self, callback: Any) -> None:
        if callable(callback):
            self.parent.after(0, callback)

    def _handle_status(self, status: dict[str, Any]) -> None:
        kind = status.get("kind")
        if kind == "state_changed":
            self._post(self.on_state_changed)
        elif kind == "notice" and self.on_notice:
            self._post(lambda: self.on_notice(str(status.get("title") or "SystemCare"),
                                              str(status.get("detail") or "")))
        elif kind == "persist" and self.on_persist:
            request_id = str(status.get("request_id") or "")
            def save() -> None:
                try:
                    ok = self.on_persist(status["changes"]) is not False
                except Exception:
                    ok = False
                if self.is_running():
                    self._send({"cmd": "persist_result", "request_id": request_id, "ok": ok})
            self._post(save)
        elif kind in {"agent_request", "schedule_request"}:
            def request() -> None:
                ok = False
                try:
                    if kind == "schedule_request" and self.on_schedule:
                        ok = self.on_schedule(status.get("options") or {}) is True
                    elif self.on_request:
                        ok = self.on_request(str(status.get("prompt") or "")) is True
                finally:
                    if self.is_running():
                        self._send({"cmd": "persist_result", "request_id": str(status.get("request_id") or ""), "ok": ok})
            self._post(request)
