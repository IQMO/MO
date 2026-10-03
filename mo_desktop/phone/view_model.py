"""Drive one phone mirroring session, and keep a frame when MO needs to look.

Mirroring is scrcpy's job. It streams hardware-encoded video on one channel and
carries mouse and keyboard on another, which is why it is smooth and responsive
where a frame-grab loop is not. MO does not reimplement any of that: this module
finds the tools, lists devices, and runs one session, so there is exactly one
mirroring path in the product rather than a second, slower one beside it.

MO's own diagnostic frame is separate and deliberately cheap: a single
screenshot on demand, written as an ordinary attachment.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.runtime.subprocess_flags import apply_windows_hidden_process_flags
from core.state.paths import resolve_state_path
from core.utils.atomic_write import atomic_write_text

from . import capture
from .trackpad import TRACKPAD_PORT, TrackpadError, TrackpadServer


# Bounds for one on-demand screenshot: a phone screen PNG, never a stream.
MAX_FRAME_BYTES = 24 * 1024 * 1024
_DEVICE_STATES = {"device", "unauthorized", "offline"}
WIRELESS_PORT = 5555
WIRELESS_ADDRESS_PATH = "memory/surfaces/phone-wireless.txt"
# Wireless debugging is only ever offered on a private network. A public address
# here would expose the phone's debug bridge to the internet.
_PRIVATE_LAN = re.compile(r"^(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)")
_IPV4 = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")
_DEVICE_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_WIRELESS_CONNECT_ATTEMPTS = 6
_WIRELESS_RETRY_SECONDS = 1.0
_TRACKPAD_REVERSE = f"tcp:{TRACKPAD_PORT}"


class PhoneMirrorError(RuntimeError):
    """A safe, operator-readable mirroring failure."""


@dataclass(frozen=True)
class PhoneDevice:
    serial: str
    state: str
    label: str

    @property
    def ready(self) -> bool:
        return self.state == "device"


def parse_devices(output: str) -> list[PhoneDevice]:
    """Read `adb devices -l` output into bounded rows."""
    devices: list[PhoneDevice] = []
    for line in str(output or "").splitlines():
        row = line.strip()
        if not row or row.lower().startswith("list of devices"):
            continue
        parts = row.split()
        if len(parts) < 2 or parts[1] not in _DEVICE_STATES:
            continue
        serial, state = parts[0][:80], parts[1]
        model = ""
        for token in parts[2:]:
            if token.startswith("model:"):
                model = token[len("model:"):].replace("_", " ")[:40]
                break
        label = model or serial
        if state == "unauthorized":
            label = f"{label} · authorize on the phone"
        elif state == "offline":
            label = f"{label} · offline"
        devices.append(PhoneDevice(serial=serial, state=state, label=label))
        if len(devices) >= 16:
            break
    return devices


def wireless_serial(address: str) -> str:
    """Return adb's serial for MO's private-LAN wireless transport."""
    return f"{address}:{WIRELESS_PORT}"


def _device_digest(serial: str) -> str:
    """Bind automatic recovery to one phone without persisting its raw serial."""
    return hashlib.sha256(str(serial or "").encode("utf-8")).hexdigest()


def scrcpy_arguments(
    serial: str, *, fullscreen: bool = False, bitrate: str = "8M"
) -> list[str]:
    """Build one scrcpy invocation.

    `--stay-awake` keeps the phone from sleeping mid-session, and the title
    names the window so it is recognisable when captured by a streaming tool.
    """
    clean = str(serial or "").strip()
    if not clean or any(char.isspace() for char in clean):
        raise PhoneMirrorError("The device serial is invalid.")
    arguments = [
        "--serial", clean,
        "--stay-awake",
        "--video-bit-rate", str(bitrate or "8M"),
        "--audio-codec=aac",
        "--window-title", "MO Phone",
    ]
    if fullscreen:
        arguments.append("--fullscreen")
    return arguments


def _search_paths(name: str, roots: list[Path]) -> str:
    for root in roots:
        try:
            if not root.is_dir():
                continue
            for found in root.rglob(name):
                if found.is_file():
                    return str(found)
        except OSError:
            continue
    return ""


def find_adb() -> str:
    """Locate adb without assuming an install layout."""
    configured = os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
    candidates = []
    if configured:
        candidates.append(Path(configured) / "platform-tools" / "adb.exe")
        candidates.append(Path(configured) / "platform-tools" / "adb")
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "Android" / "Sdk" / "platform-tools" / "adb.exe")
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    from shutil import which

    return which("adb") or ""


def find_scrcpy() -> str:
    """Locate scrcpy, including the WinGet layout that moves between versions."""
    from shutil import which

    found = which("scrcpy")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    roots = []
    if local:
        roots.append(Path(local) / "Microsoft" / "WinGet" / "Packages")
    name = "scrcpy.exe" if sys.platform == "win32" else "scrcpy"
    return _search_paths(name, roots)


class PhoneMirrorModel:
    """Find the tools, list devices, run one mirroring session."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.notice = ""
        self._process: subprocess.Popen[Any] | None = None
        self._trackpad: TrackpadServer | None = None
        self._trackpad_serial = ""

    # ---- tools ---------------------------------------------------------

    def adb(self) -> str:
        return find_adb()

    def scrcpy(self) -> str:
        return find_scrcpy()

    def ready(self) -> bool:
        """Whether both tools are present, setting a notice when they are not."""
        self.notice = ""
        if not self.adb():
            self.notice = (
                "Android platform-tools were not found. Install them, or set "
                "ANDROID_SDK_ROOT."
            )
            return False
        if not self.scrcpy():
            self.notice = (
                "scrcpy was not found. Install it with: "
                "winget install --id Genymobile.scrcpy"
            )
            return False
        return True

    # ---- wireless ------------------------------------------------------

    def _address_file(self) -> Path:
        return Path(resolve_state_path(WIRELESS_ADDRESS_PATH, self.config))

    def _wireless_connections(self) -> list[tuple[str, str]]:
        """Read address/digest pairs from the existing private Wi-Fi record."""
        try:
            values = self._address_file().read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        result = []
        for index in range(0, len(values), 2):
            address = values[index].strip()
            digest = values[index + 1].strip().lower() if index + 1 < len(values) else ""
            if _PRIVATE_LAN.match(address) and address not in {row[0] for row in result}:
                result.append((address, digest if _DEVICE_DIGEST.fullmatch(digest) else ""))
        return result

    def saved_address(self, usb_serial: str = "") -> str:
        """The selected phone's saved endpoint, or the latest without selection."""
        digest = _device_digest(usb_serial) if usb_serial else ""
        return next((address for address, binding in self._wireless_connections()
                     if not digest or binding == digest), "")

    def _remember_address(self, address: str, usb_serial: str = "") -> None:
        try:
            target = self._address_file()
            binding = _device_digest(usb_serial) if usb_serial else ""
            connections = [(address, binding)] + [
                row for row in self._wireless_connections()
                if row[0] != address and (not binding or row[1] != binding)
            ]
            lines = [value for row in connections for value in row]
            atomic_write_text(target, "\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            pass

    def _wireless_online(self, address: str, expected_digest: str = "") -> bool:
        if not address:
            return False
        try:
            self._run([self.adb(), "connect", wireless_serial(address)], timeout=20)
            listing = self._run([self.adb(), "devices"], timeout=20)
        except PhoneMirrorError:
            return False
        online = any(
            device.ready and device.serial == wireless_serial(address)
            for device in parse_devices(listing)
        )
        if online and expected_digest:
            try:
                serial = self._run(
                    [self.adb(), "-s", wireless_serial(address), "shell", "getprop", "ro.serialno"], timeout=20,
                ).strip()
            except PhoneMirrorError:
                return False
            return bool(serial) and _device_digest(serial) == expected_digest
        return online

    def _phone_private_address(self, serial: str) -> str:
        """Resolve the phone's routed private IPv4 without assuming an interface name."""
        for ip_args in (
            ["route", "get", "1.1.1.1"],
            ["-f", "inet", "addr", "show", "up", "scope", "global"],
        ):
            try:
                output = self._run(
                    [self.adb(), "-s", serial, "shell", "ip", *ip_args],
                    timeout=25,
                )
            except PhoneMirrorError:
                continue
            routed = re.search(r"\bsrc\s+(\d{1,3}(?:\.\d{1,3}){3})\b", output)
            addresses = ([routed.group(1)] if routed else []) + _IPV4.findall(output)
            private = next((item for item in addresses if _PRIVATE_LAN.match(item)), "")
            if private:
                return private
        return ""

    @staticmethod
    def _cabled_device(devices: list[PhoneDevice]) -> PhoneDevice | None:
        return next(
            (item for item in devices if item.ready and ":" not in item.serial),
            None,
        )

    def _enable_wireless(self, cabled: PhoneDevice) -> str:
        """Enable and verify TCP adb for one already-authorized USB phone."""
        address = self._phone_private_address(cabled.serial)
        if not address or not _PRIVATE_LAN.match(address):
            raise PhoneMirrorError(
                "The phone is not on a private Wi-Fi network, so wireless "
                "debugging is refused."
            )
        try:
            self._run([self.adb(), "-s", cabled.serial, "tcpip", str(WIRELESS_PORT)], timeout=25)
        except PhoneMirrorError:
            raise PhoneMirrorError("Wireless debugging could not be enabled.") from None
        connected = False
        for attempt in range(_WIRELESS_CONNECT_ATTEMPTS):
            if self._wireless_online(address, _device_digest(cabled.serial)):
                connected = True
                break
            if attempt + 1 < _WIRELESS_CONNECT_ATTEMPTS:
                time.sleep(_WIRELESS_RETRY_SECONDS)
        if not connected:
            raise PhoneMirrorError("Wireless pairing did not take. Try once more.")
        self._remember_address(address, cabled.serial)
        return address

    def connect_wireless(self, selected_serial: str = "") -> str:
        """Reconnect over Wi-Fi, pairing from USB when that is what it takes.

        An explicit selection never pairs another USB phone or reconnects an
        unrelated saved endpoint. Without a selection, retain saved recovery.
        """
        if not self.adb():
            raise PhoneMirrorError("Android platform-tools were not found.")
        devices = parse_devices(self._safe_devices())
        saved = self.saved_address(selected_serial) if selected_serial and ":" not in selected_serial else self.saved_address()
        if selected_serial:
            selected = next((item for item in devices if item.serial == selected_serial and item.ready), None)
            if selected is None:
                raise PhoneMirrorError("Refresh and authorize the selected phone before Wi-Fi setup.")
            if ":" in selected.serial:
                address = selected.serial.rsplit(":", 1)[0]
                if _PRIVATE_LAN.match(address) and selected.serial == wireless_serial(address) and self._wireless_online(address):
                    return address
                raise PhoneMirrorError("The selected wireless connection could not be verified.")
            if saved and self._wireless_online(saved, _device_digest(selected.serial)):
                return saved
            return self._enable_wireless(selected)
        cabled = self._cabled_device(devices)
        binding = next((digest for address, digest in self._wireless_connections() if address == saved), "")
        if saved and self._wireless_online(saved, binding):
            return saved
        if cabled is None:
            raise PhoneMirrorError(
                "Plug the phone in over USB once to pair it for Wi-Fi. "
                "Wireless debugging resets when the phone reboots."
            )
        return self._enable_wireless(cabled)

    def _safe_devices(self) -> str:
        try:
            return self._run([self.adb(), "devices", "-l"], timeout=20)
        except PhoneMirrorError:
            return ""

    # ---- devices -------------------------------------------------------

    def devices(self) -> list[PhoneDevice]:
        # Discovery, Trackpad and frame capture require ADB, independently of scrcpy.
        self.notice = ""
        if not self.adb():
            self.notice = "Android platform-tools were not found. Install them, or set ANDROID_SDK_ROOT."
            return []
        try:
            result = self._run([self.adb(), "devices", "-l"], timeout=20)
        except PhoneMirrorError as exc:
            self.notice = str(exc)
            return []
        found = parse_devices(result)
        preferred = []
        for saved, paired_digest in self._wireless_connections():
            saved_serial = wireless_serial(saved)
            wireless_ready = any(item.ready and item.serial == saved_serial for item in found)
            if (not wireless_ready or paired_digest) and self._wireless_online(saved, paired_digest):
                found = parse_devices(self._safe_devices())
                wireless_ready = any(item.ready and item.serial == saved_serial for item in found)
            elif paired_digest:
                wireless_ready = False
            # Only the exact deliberately paired USB phone may be re-armed.
            cabled = next(
                (
                    item
                    for item in found
                    if item.ready
                    and ":" not in item.serial
                    and _device_digest(item.serial) == paired_digest
                ),
                None,
            )
            if not wireless_ready and paired_digest and cabled is not None:
                try:
                    saved = self._enable_wireless(cabled)
                except PhoneMirrorError as exc:
                    self.notice = f"USB is ready; automatic Wi-Fi recovery failed: {exc}"
                else:
                    saved_serial = wireless_serial(saved)
                    found = parse_devices(self._safe_devices())
                    wireless_ready = any(item.ready and item.serial == saved_serial for item in found)
            if wireless_ready:
                preferred.append(saved_serial)

        if preferred:
            found.sort(key=lambda item: preferred.index(item.serial) if item.serial in preferred else len(preferred))
        if not found:
            self.notice = (
                "No phone is connected. Plug in the USB cable, or press Wi-Fi "
                "to pair. Wireless debugging resets when the phone reboots."
            )
        return found

    # ---- mirroring -----------------------------------------------------

    def mirror(self, serial: str, *, fullscreen: bool = False) -> None:
        """Start one scrcpy session for a device."""
        if self.mirroring:
            raise PhoneMirrorError("A mirroring session is already running.")
        if not self.ready():
            raise PhoneMirrorError(self.notice or "The mirroring tools are missing.")
        command = [self.scrcpy(), *scrcpy_arguments(serial, fullscreen=fullscreen)]
        try:
            self._process = subprocess.Popen(
                command,
                **apply_windows_hidden_process_flags({
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                    "stdin": subprocess.DEVNULL,
                }),
            )
        except OSError:
            raise PhoneMirrorError("scrcpy could not be started.") from None

    def stop(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            process.terminate()
        except Exception:
            pass

    @property
    def mirroring(self) -> bool:
        process = self._process
        if process is None:
            return False
        if process.poll() is not None:
            self._process = None
            return False
        return True

    # ---- trackpad ------------------------------------------------------

    def _remove_trackpad_reverse(self, serial: str) -> None:
        """Best-effort removal of the one Desktop-owned reverse rule."""
        clean = str(serial or "").strip()
        adb = self.adb()
        if not clean or not adb or any(char.isspace() for char in clean):
            return
        try:
            self._run(
                [adb, "-s", clean, "reverse", "--remove", _TRACKPAD_REVERSE],
                timeout=15,
            )
        except PhoneMirrorError:
            pass

    def start_trackpad(
        self, serial: str, *, on_state: Any = None
    ) -> None:
        """Open one nonce-bound phone session through a fresh loopback listener.

        `adb reverse` points the phone's fixed localhost target at an ephemeral
        Desktop port. The one-use nonce authenticates the Activity before any
        pointer message is accepted.
        """
        if self.trackpad_running:
            raise PhoneMirrorError("The trackpad is already running.")
        adb = self.adb()
        if not adb:
            raise PhoneMirrorError("Android platform-tools were not found.")
        clean = str(serial or "").strip()
        if not clean or any(char.isspace() for char in clean):
            raise PhoneMirrorError("The device serial is invalid.")
        # The Activity always dials this fixed device-side port. Remove any
        # orphaned mapping before publishing a fresh one to a new listener.
        self._remove_trackpad_reverse(clean)
        nonce = secrets.token_urlsafe(32)
        server = TrackpadServer(nonce=nonce, port=0, on_state=on_state)
        try:
            server.start()
        except TrackpadError as exc:
            raise PhoneMirrorError(str(exc)) from None
        self._trackpad = server
        try:
            self._run(
                [
                    adb, "-s", clean, "reverse",
                    _TRACKPAD_REVERSE, f"tcp:{server.port}",
                ],
                timeout=20,
            )
            self._run(
                [
                    adb, "-s", clean, "shell", "am", "start",
                    "-n", "app.moagent.mobile/.TrackpadActivity",
                    "--es", "app.moagent.mobile.trackpad.NONCE", nonce,
                ],
                timeout=25,
            )
        except PhoneMirrorError:
            self.stop_trackpad(clean)
            raise PhoneMirrorError("The trackpad could not be opened on the phone.") from None
        self._trackpad_serial = clean

    def stop_trackpad(self, serial: str = "") -> None:
        server, self._trackpad = self._trackpad, None
        clean = str(serial or self._trackpad_serial).strip()
        self._trackpad_serial = ""
        self._remove_trackpad_reverse(clean)
        if server is not None:
            server.stop()

    @property
    def trackpad_running(self) -> bool:
        server = self._trackpad
        if server is None:
            return False
        if server.running:
            return True
        self._trackpad = None
        clean, self._trackpad_serial = self._trackpad_serial, ""
        self._remove_trackpad_reverse(clean)
        return False

    # ---- one frame for MO ----------------------------------------------

    def keep_frame(self, serial: str, label: str) -> Path:
        """Save one screenshot as an ordinary MO attachment.

        This is what lets MO check the phone it is building for instead of
        inferring from logs. It is a single frame on demand, never a stream.
        """
        if not self.adb():
            raise PhoneMirrorError("Android platform-tools were not found.")
        clean = str(serial or "").strip()
        if not clean or any(char.isspace() for char in clean):
            raise PhoneMirrorError("The device serial is invalid.")
        try:
            completed = subprocess.run(
                [self.adb(), "-s", clean, "exec-out", "screencap", "-p"],
                **apply_windows_hidden_process_flags({
                    "capture_output": True,
                    "timeout": 30,
                }),
            )
        except (OSError, subprocess.SubprocessError):
            raise PhoneMirrorError("The screenshot could not be taken.") from None
        image = completed.stdout or b""
        if not image.startswith(b"\x89PNG") or len(image) > MAX_FRAME_BYTES:
            raise PhoneMirrorError("The phone returned an unusable screenshot.")
        return capture.write_frame(self.config, label, image)

    # ---- plumbing ------------------------------------------------------

    def _run(self, command: list[str], *, timeout: float) -> str:
        try:
            completed = subprocess.run(
                command,
                **apply_windows_hidden_process_flags({
                    "capture_output": True,
                    "text": True,
                    "timeout": timeout,
                }),
            )
        except subprocess.TimeoutExpired:
            raise PhoneMirrorError("The device query timed out.") from None
        except (OSError, subprocess.SubprocessError):
            raise PhoneMirrorError("The device query failed.") from None
        return completed.stdout or ""

