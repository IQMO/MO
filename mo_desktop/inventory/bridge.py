"""Inventory's WebView bridge: reads the basket while visible and sends picked items to a running MO Terminal
as a prepared message in its composer (the existing `request` handoff), so the user adds what to do and presses
Enter there. It starts no work by itself."""
from __future__ import annotations

import threading
from typing import Any

from .snapshot import InventoryReader, compose_request, running_terminals

OBSERVE_SECONDS = 4.0


class InventoryBridge:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config if isinstance(config, dict) else {}
        self.on_status: Any = None
        self.on_ui_ready: Any = None
        self._window: Any = None
        self._reader = InventoryReader(self.config)
        self._lock = threading.Lock()
        self._latest: dict[str, Any] = {}
        self._visible = True
        self._closed = False
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._observe_loop, name="inventory-observe", daemon=True)

    def attach_window(self, window: Any) -> None:
        self._window = window
        self._thread.start()

    def ui_ready(self) -> bool:
        if callable(self.on_ui_ready):
            self.on_ui_ready()
        return True

    def set_visible(self, visible: bool) -> bool:
        self._visible = bool(visible)
        if self._visible:
            self._wake.set()
        if callable(self.on_status):
            self.on_status({"kind": "visible", "visible": self._visible})
        return True

    def close(self) -> None:
        self._closed = True
        self._wake.set()

    def window_control(self, action: str) -> dict[str, bool]:
        window = self._window
        if window is None:
            raise RuntimeError("Inventory's window is unavailable.")
        if action == "close":
            self.set_visible(False)
            window.destroy()
        elif action == "minimize":
            window.minimize()
        elif action == "toggle_maximize":
            native = getattr(window, "native", None)
            if native is not None and str(native.WindowState).endswith("Maximized"):
                window.restore()
            else:
                window.maximize()
        else:
            raise ValueError("Unknown Inventory window control.")
        return {"ok": True}

    def _observe_loop(self) -> None:
        while not self._closed:
            if self._visible:
                try:
                    data = {"items": self._reader.items(), "terminals": running_terminals(self.config)}
                    with self._lock:
                        self._latest = data
                except Exception as exc:
                    with self._lock:
                        self._latest = {**self._latest, "error": f"{type(exc).__name__}: {exc}"[:200]}
            self._wake.wait(OBSERVE_SECONDS)
            self._wake.clear()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._latest)

    def send(self, instance_id: str, item_ids: list[str]) -> dict[str, Any]:
        """Prepare the picked items in that MO Terminal's composer (it never sends on its own)."""
        from core.design.terminal_handoff import queue_terminal_control

        with self._lock:
            items = {row["id"]: row for row in self._latest.get("items") or []}
            terminals = {row["id"]: row for row in self._latest.get("terminals") or []}
        picked = [items[i] for i in item_ids or [] if i in items][:20]
        target = terminals.get(instance_id)
        if not picked:
            return {"ok": False, "message": "Pick something first."}
        if target is None:
            return {"ok": False, "message": "That MO terminal is no longer running."}
        try:
            queue_terminal_control("request", compose_request(picked), {"instance_id": target["id"], "pid": target["pid"]},
                                   project_root=target["cwd"], expected_slot=target["slot"], config=self.config)
        except Exception as exc:
            return {"ok": False, "message": str(exc) or type(exc).__name__}
        return {"ok": True, "message": f"{len(picked)} item{'s' if len(picked) != 1 else ''} waiting in that terminal's composer: "
                                       "add what to do and press Enter there."}
