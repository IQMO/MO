"""MO Phone's native presentation adapter over its existing device owners."""
from __future__ import annotations

import queue
import threading
import time
from pathlib import Path
from typing import Any

from .view_model import PhoneDevice, PhoneMirrorError, PhoneMirrorModel, wireless_serial


def live_control_display(value: str) -> tuple[str, str]:
    """Describe the host connection; never infer selected-phone permissions."""
    clean = str(value or "").strip().casefold()
    if clean == "connected":
        return "Host connected", "ok"
    if clean in {"starting", "securing"}:
        return "Host linking", "warn"
    if clean == "unpaired":
        return "Host needs pairing", "warn"
    if clean == "disabled":
        return "Control off", "muted"
    if clean == "unavailable:_unauthorized":
        return "Host needs pairing", "error"
    if clean.startswith("unavailable"):
        return "Host unavailable", "error"
    return "Host stopped", "muted"


def device_transport(serial: str) -> str:
    clean = str(serial or "").strip()
    return ("Wi-Fi ADB" if ":" in clean else "USB ADB") if clean else "ADB unavailable"


class PhoneBridge:
    """One serialized device worker and a bounded, query-free UI snapshot."""

    def __init__(self, config: dict[str, Any] | None = None, *, model: Any = None,
                 host_status: str = "", files_available: bool = False) -> None:
        self._model = model if model is not None else PhoneMirrorModel(config)
        self._config = config or {}
        self._on_status: Any = None
        self._on_ui_ready: Any = None
        self._on_idle_close: Any = None
        self._window: Any = None
        self._lock = threading.RLock()
        self._queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
        self._closed = False
        self._visible = True
        self._closing = False
        self._exit_when_idle = False
        self._refresh_pending = False
        self._auto_action = ""      # "trackpad" or "mirror": start it on the ready phone after refresh
        self._pairing_image: Path | None = None
        self._devices: list[PhoneDevice] = []
        self._state: dict[str, Any] = {
            "revision": 0, "devices": [], "serial": "", "label": "Connect your phone",
            "connection": "loading", "transport": "ADB unavailable", "busy": "",
            "message": "Looking for connected phones…", "error": "", "fullscreen": False,
            "adb_available": False, "scrcpy_available": False, "discovered": False,
            "mirror": False, "mirror_device": "", "trackpad": False, "trackpad_device": "",
            "trackpad_connection": "", "captures": 0, "host_status": host_status,
            "files_available": bool(files_available),
            "hub": {"label": "Hub not checked", "tone": "muted", "detail": "Open Phones & access to check setup.",
                    "coordinator": "Not checked", "can_pair": False, "checked_at": 0, "steps": [],
                    "devices": [], "inventory": "Not checked"},
            "pairing_expires_at": 0,
        }
        self._thread = threading.Thread(target=self._work, name="mo-phone-device-worker", daemon=True)
        self._thread.start()

    def _attach_window(self, window: Any) -> None:
        self._window = window

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            host_label, host_tone = live_control_display(self._state["host_status"])
            return {**self._state, "devices": [dict(row) for row in self._state["devices"]],
                    "hub": {**self._state["hub"], "steps": [dict(step) for step in self._state["hub"]["steps"]],
                            "devices": [{**row, "scopes": list(row["scopes"])} for row in self._state["hub"]["devices"]]},
                    "host_label": host_label, "host_tone": host_tone}

    def _publish(self, **changes: Any) -> None:
        with self._lock:
            if self._closed:
                return
            self._state.update(changes)
            self._state["revision"] += 1
            payload = self.snapshot()
            visible = self._visible
        if self._on_status:
            self._on_status({"kind": "phone_state", "trackpad": payload["trackpad"]})
        if visible and self._window is not None:
            import json
            try:
                self._window.evaluate_js("window.moPhoneUpdate && window.moPhoneUpdate(" + json.dumps(payload).replace("<", "\\u003c") + ")")
            except Exception:
                pass  # The native window may close while a safe device boundary finishes.

    def ui_ready(self) -> None:
        if self._on_ui_ready:
            self._on_ui_ready()
        self.refresh()

    def _host_update(self, status: str, files_available: bool | None = None) -> None:
        changes: dict[str, Any] = {"host_status": str(status or "")[:80]}
        if files_available is not None:
            changes["files_available"] = bool(files_available)
        self._publish(**changes)

    def check_connection(self) -> dict[str, bool]:
        self.dismiss_pairing()
        return {"accepted": self._submit("check_connection")}

    def pair_phone(self) -> dict[str, bool]:
        # An explicit click requests a normal companion grant, never remote_host.
        return {"accepted": self._submit("pair_phone")}

    def dismiss_pairing(self) -> None:
        with self._lock:
            self._clear_pairing()

    def _clear_pairing(self) -> None:
        target, self._pairing_image = self._pairing_image, None
        if target is None and not self._state["pairing_expires_at"]:
            return
        if target is not None:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass  # The canonical expiry cleanup still owns this private image.
        self._state["pairing_expires_at"] = 0
        self._publish(pairing_expires_at=0)

    def _check_connection(self) -> None:
        from core.state.everywhere_readiness import build_everywhere_readiness
        from mo_everywhere.client import ContinuityClient, EverywhereClientError, _Unauthorized

        readiness = build_everywhere_readiness(self._config)
        hub = {"label": "Hub unavailable", "tone": "error", "detail": "", "coordinator": "Not checked",
               "can_pair": False, "checked_at": time.time(), "steps": [], "devices": [], "inventory": "Unavailable"}
        if not readiness.authority.enabled or not readiness.authority.role_explicit or readiness.locally_disabled or readiness.authority.conflicts:
            hub.update(label="Hub setup needed", tone="warn", detail=readiness.next_action)
            hub["steps"] = [{"title": "Review Everywhere setup", "detail": "Run /everywhere setup in MO Shell. Review its plan before /everywhere setup --confirm."}]
        elif readiness.endpoint_state != "reachable":
            label = "Hub incompatible" if readiness.endpoint_state == "incompatible" else "Hub unavailable"
            hub.update(label=label, detail="The configured serving Hub did not pass MO’s health check.")
            hub["steps"] = [
                {"title": "Start the serving Hub", "detail": "Start or restart the existing MO service and its HTTPS/WSS proxy. Run /everywhere setup on the Hub to review configuration and dependencies."},
                {"title": "Check this workstation", "detail": "Use the Hub’s verified HTTPS address and ensure this PC can reach it. Then press Check connection."},
            ]
            if not readiness.endpoint:
                hub.update(label="Hub setup needed", tone="warn", detail="This Desktop has no paired Hub address.")
        else:
            hub.update(label="Hub reachable", tone="ok", detail="The serving Hub passed MO’s health check.")
        if (readiness.authority.enabled and readiness.authority.role_explicit and not readiness.authority.conflicts
                and not readiness.locally_disabled and readiness.endpoint_state == "reachable" and readiness.credentials.primary == "valid"):
            try:
                client = ContinuityClient(self._config, timeout=4)
                device = client.device_status()
                eligible = device["capability"] == "notify" and {"continuity_read", "continuity_sync"}.issubset(device["scopes"])
                hub.update(coordinator="Verified" if eligible else "Pairing scopes missing", can_pair=eligible and readiness.dependencies.qr)
                if eligible:
                    try:
                        hub.update(devices=client.android_devices(), inventory="Observed")
                    except EverywhereClientError:
                        hub["inventory"] = "Unavailable; check that the serving Hub is up to date"
            except _Unauthorized:
                hub.update(coordinator="Pairing rejected")
            except EverywhereClientError:
                hub.update(coordinator="Could not verify")
        elif readiness.credentials.primary != "valid":
            hub.update(coordinator="Not paired")
        if hub["coordinator"] in {"Not paired", "Pairing rejected", "Pairing scopes missing"}:
            hub["steps"].extend([
                {"title": "Pair this Desktop as a coordinator", "detail": "On the serving Hub, create a one-use coordinator grant:",
                 "command": "python -m mo_everywhere.cli pair --capability notify --scope continuity_read --scope continuity_sync"},
                {"title": "Join from this PC", "detail": "Use the Hub’s verified HTTPS address and the newly issued code. Do not create another Hub registry here.",
                 "command": 'python -m mo_everywhere.cli join --hub https://YOUR_HUB --code ONE_TIME_CODE --label "My workstation"'},
            ])
        if not readiness.dependencies.qr:
            hub["steps"].append({"title": "Enable existing QR support", "detail": "Install MO’s existing optional requirements on this PC:",
                                  "command": "python -m pip install -r requirements-everywhere.txt"})
        self._publish(hub=hub)

    def _pair_phone(self) -> None:
        import base64
        import json
        from mo_everywhere.pairing_qr import create_android_pairing_image

        self._clear_pairing()
        self._check_connection()
        if not self._state["hub"]["can_pair"]:
            raise PhoneMirrorError("Pairing is not ready. Follow the steps in Phones & access, then check again.")
        target, expiry = create_android_pairing_image(self._config, phone_control=False)
        with self._lock:
            self._clear_pairing()
            if self._closed or self._closing or not self._visible:
                target.unlink(missing_ok=True)
                return
            self._pairing_image = target
            # QR pixels travel only to this local renderer, never snapshots, pipes or telemetry.
            uri = "data:image/png;base64," + base64.b64encode(target.read_bytes()).decode("ascii")
            if self._window is None:
                self._clear_pairing()
                raise PhoneMirrorError("Phone’s QR display is unavailable. Reopen Phone and try again.")
            self._publish(pairing_expires_at=expiry, message="Scan the one-use QR in MO Everywhere on your phone.")
            self._window.evaluate_js("window.moPhonePairingImage(" + json.dumps(uri) + "," + str(expiry) + ")")

    def _submit(self, action: str, **payload: Any) -> bool:
        with self._lock:
            if self._closed or self._closing or self._state["busy"]:
                return False
            self._publish(busy=action, error="")
            self._queue.put((action, payload))
            return True

    def refresh(self) -> dict[str, bool]:
        with self._lock:
            if self._closed or self._closing:
                return {"accepted": False}
            if self._state["busy"]:
                self._refresh_pending = True
                return {"accepted": True}
            return {"accepted": self._submit("refresh")}

    def select_device(self, serial: str) -> dict[str, Any]:
        with self._lock:
            if self._state["busy"] or self._closed or self._closing:
                raise PhoneMirrorError("Wait for the current phone action to finish.")
            device = next((d for d in self._devices if d.serial == serial), None)
            if device is None:
                raise PhoneMirrorError("Refresh and choose a currently discovered phone.")
            self._select(device)
        return self.snapshot()

    def _select(self, device: PhoneDevice | None) -> None:
        self._publish(serial=device.serial if device else "", label=device.label if device else "Connect your phone",
                      connection=device.state if device else "none", transport=device_transport(device.serial if device else ""))

    def set_fullscreen(self, enabled: bool) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise ValueError("Full screen requires a boolean")
        self._publish(fullscreen=enabled)
        return self.snapshot()

    def perform(self, action: str) -> dict[str, bool]:
        if action not in {"mirror", "trackpad", "frame", "wifi"}:
            raise ValueError("Unknown Phone action")
        with self._lock:
            device = next((d for d in self._devices if d.serial == self._state["serial"]), None)
            stopping = action in {"mirror", "trackpad"} and self._state[action]
            if action != "wifi" and not stopping and (device is None or not device.ready):
                raise PhoneMirrorError("Connect and authorize the selected phone first.")
            return {"accepted": self._submit(action, serial=device.serial if device else "",
                                              label=device.label if device else "", fullscreen=self._state["fullscreen"], stopping=stopping)}

    def _open_auto(self, action: str) -> None:
        """One step from outside the window (the launcher's quick actions): start Trackpad or
        Mirror on the ready phone."""
        if action not in {"trackpad", "mirror"}:
            return
        with self._lock:
            if self._closed or self._closing or self._state[action]:
                return
            self._exit_when_idle = False
            self._auto_action = action
        self.refresh()

    def open_files(self) -> dict[str, bool]:
        if not self._state["files_available"] or self._on_status is None:
            raise PhoneMirrorError("MO Files is unavailable from this Desktop host.")
        self._on_status({"kind": "open_files"})
        return {"accepted": True}

    def _files_result(self, ok: bool) -> None:
        self._publish(message="MO Files opened." if ok else "MO Files could not be opened.",
                      error="" if ok else "MO Files could not be opened.")

    def _set_visible(self, visible: bool) -> None:
        with self._lock:
            self._visible = bool(visible)
            if visible:
                self._exit_when_idle = False
            if not visible:
                self._clear_pairing()
        if visible:
            self._publish()

    def _close_window(self) -> None:
        """Hide promptly; serialize Trackpad cleanup after any in-flight start."""
        with self._lock:
            if self._closed or self._closing:
                return
            self._visible = False
            self._clear_pairing()
            self._closing = True
            self._exit_when_idle = True
            self._auto_action = ""
            self._refresh_pending = False
        try:
            if self._window is not None:
                self._window.hide()
            if self._on_status:
                self._on_status({"kind": "visible", "visible": False})
        finally:
            self._queue.put(("close", {}))

    def _exit_if_idle(self) -> None:
        """Called only at the serialized worker's safe device boundary."""
        with self._lock:
            if (self._closed or self._closing or not self._exit_when_idle
                    or self._state["mirror"] or self._state["trackpad"]):
                return
            self._exit_when_idle = False
            callback = self._on_idle_close
        if callback is not None:
            callback()

    def window_control(self, action: str, width: int = 0, height: int = 0) -> dict[str, Any]:
        window = self._window
        if window is None:
            raise RuntimeError("Phone window is unavailable")
        if action == "close":
            self._close_window()
        elif action == "minimize":
            window.minimize()
        elif action == "pin":
            window.on_top = not window.on_top
            return {"accepted": True, "on_top": bool(window.on_top)}
        elif action == "toggle_maximize":
            if getattr(window, "native", None) is not None and str(window.native.WindowState).endswith("Maximized"):
                window.restore()
            else:
                window.maximize()
        elif action == "resize":
            window.resize(max(840, min(3840, int(width))), max(620, min(2160, int(height))))
        else:
            raise ValueError("Unknown Phone window control")
        return {"accepted": True}

    def _shutdown(self) -> None:
        with self._lock:
            if not self._closed:
                self._closed = True
                self._clear_pairing()
                self._auto_action = ""
                self._refresh_pending = False
                self._queue.put(("shutdown", {}))
        if self._thread is not threading.current_thread():
            self._thread.join()

    def _trackpad_state(self, value: str) -> None:
        # The transport callback supplies evidence; it never calls ADB on its input thread.
        self._queue.put(("trackpad_state", {"value": str(value)}))

    def _sessions(self) -> None:
        mirror, trackpad = bool(self._model.mirroring), bool(self._model.trackpad_running)
        if mirror != self._state["mirror"] or trackpad != self._state["trackpad"]:
            self._publish(mirror=mirror, trackpad=trackpad,
                          message="Mirroring ended." if self._state["mirror"] and not mirror else self._state["message"])
        self._exit_if_idle()

    def _discover(self) -> None:
        devices = self._model.devices()
        self._devices = devices
        current = next((d for d in devices if d.serial == self._state["serial"]), None)
        if current is None:
            current = next((d for d in devices if d.ready), devices[0] if devices else None)
        self._select(current)
        adb, scrcpy = bool(self._model.adb()), bool(self._model.scrcpy())
        self._publish(devices=[{"serial":d.serial,"label":d.label,"state":d.state} for d in devices],
                      adb_available=adb, scrcpy_available=scrcpy, discovered=True,
                      message=str(self._model.notice or (f"{current.label} is ready." if current and current.ready else "Connect and authorize your phone."))[:300])

    def _work(self) -> None:
        while True:
            try:
                action, payload = self._queue.get(timeout=1 if self._state["mirror"] or self._state["trackpad"] else None)
            except queue.Empty:
                self._sessions()
                continue
            if action == "shutdown":
                self._model.stop_trackpad()
                return
            if self._closed:
                continue
            try:
                if action == "close":
                    self._model.stop_trackpad()
                    self._publish(trackpad=False, trackpad_connection="", busy="")
                    with self._lock:
                        self._closing = False
                    self._sessions()
                    continue
                if action == "trackpad_state":
                    self._sessions()
                    self._publish(trackpad_connection=payload["value"])
                    continue
                if self._closing:
                    continue
                if action == "refresh":
                    self._discover()
                    if not self._state["hub"]["checked_at"]:
                        action = "check_connection"
                        self._publish(busy=action)
                        self._check_connection()
                elif action == "check_connection":
                    self._check_connection()
                elif action == "pair_phone":
                    self._pair_phone()
                elif action == "wifi":
                    address = self._model.connect_wireless(payload["serial"])
                    self._publish(serial=wireless_serial(address))
                    self._discover()
                    if self._state["serial"] != wireless_serial(address) or self._state["connection"] != "device":
                        raise PhoneMirrorError("Wi-Fi verification changed. Refresh before removing the cable.")
                    self._publish(message="Connected over Wi-Fi. The cable can come out.")
                elif action == "mirror":
                    if payload["stopping"]:
                        self._model.stop()
                        self._publish(message="Mirroring stopped.")
                    else:
                        self._model.mirror(payload["serial"], fullscreen=payload["fullscreen"])
                        self._publish(mirror_device=payload["label"], message=f"Mirroring {payload['label']} in its own window.")
                elif action == "trackpad":
                    if payload["stopping"]:
                        self._model.stop_trackpad()
                        self._publish(message="Trackpad stopped.", trackpad_connection="")
                    else:
                        self._model.start_trackpad(payload["serial"], on_state=self._trackpad_state)
                        self._publish(trackpad_device=payload["label"], trackpad_connection="waiting",
                                      message=f"Trackpad opened on {payload['label']}. Waiting for its connection.")
                elif action == "frame":
                    self._model.keep_frame(payload["serial"], payload["label"])
                    self._publish(captures=self._state["captures"]+1, message="Frame kept in MO Files.")
                    if self._on_status and not self._closed:
                        self._on_status({"kind": "notice", "title": "Phone frame kept", "detail": "Saved in MO Files."})
                self._sessions()
            except Exception as exc:
                if action == "pair_phone":
                    self.dismiss_pairing()
                message = " ".join(str(exc or "Phone action failed").split())[:300]
                self._publish(error=message, message=message)
                if action == "refresh":
                    self._devices = []
                    self._select(None)
                    self._publish(devices=[], discovered=True)
            finally:
                if action not in {"trackpad_state", "close"}:
                    self._publish(busy="")
                    with self._lock:
                        again, self._refresh_pending = self._refresh_pending, False
                        auto, self._auto_action = self._auto_action, ""
                    if not self._closed and not self._closing:
                        if auto:
                            ready = next((d for d in self._devices if d.serial == self._state["serial"] and d.ready), None)
                            ready = ready or next((d for d in self._devices if d.ready), None)
                            if ready:
                                self._select(ready)
                                self.perform(auto)
                            else:
                                text = f"No phone is ready for {auto.title()}."
                                self._publish(error=text, message=text)
                                if self._on_status:
                                    self._on_status({"kind": "notice", "title": "MO Phone", "detail": text})
                        if again:
                            self.refresh()
