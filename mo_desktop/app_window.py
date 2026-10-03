"""Shared Desktop process and launch pipe for native WebView apps."""
from __future__ import annotations

import ctypes
import json
import subprocess
import threading
from pathlib import Path
from typing import Any

from core.runtime.subprocess_flags import gui_python_executable
from interface.desktop_ui import DesktopVisualState
from mo_desktop.design_studio.theme import studio_theme
from mo_desktop.mo_renderer import renderer_available


class NativeAppWindow:
    """One process/theme/source handshake; app data stays with its surface."""

    app_module = ""
    app_title = "MO"

    def __init__(
        self,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.config = config if config is not None else {}
        self._process: subprocess.Popen[str] | None = None
        self._hidden = True
        self._lock = threading.RLock()
        self._on_launch_ready: Any = None
        self._on_launch_source: Any = None
        self._on_launch_started: Any = None

    def is_visible(self) -> bool:
        process = self._process
        if process is None or process.poll() is not None or self._hidden:
            return False
        if not hasattr(ctypes, "windll"):
            return True
        from ctypes import wintypes

        visible = False

        def inspect(hwnd: int, _param: int) -> bool:
            nonlocal visible
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == process.pid and ctypes.windll.user32.IsWindowVisible(hwnd):
                visible = True
            return True

        callback = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(inspect)
        ctypes.windll.user32.EnumWindows(callback, 0)
        return visible

    def is_running(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def _send(self, command: dict[str, Any]) -> None:
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None or process.stdin is None:
                raise RuntimeError(f"{self.app_title} is not running.")
            process.stdin.write(json.dumps(command, ensure_ascii=False) + "\n")
            process.stdin.flush()

    def show(self, *, on_source: Any = None, on_started: Any = None,
             on_ready: Any = None, **payload: Any) -> None:
        if not renderer_available():
            raise RuntimeError(f"{self.app_title} needs the installed MO Design WebView renderer.")
        process = self._process
        if process is None or process.poll() is not None:
            self._on_launch_ready = on_ready
            self._on_launch_source = on_source
            self._on_launch_started = on_started
            command = [gui_python_executable(), "-m", self.app_module]
            process = subprocess.Popen(
                command, cwd=str(Path(__file__).resolve().parents[1]),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                encoding="utf-8", bufsize=1,
            )
            self._process = process
            self._send({
                **payload, "cmd": "init", "config": self.config,
                "launch_snapshot": {"deferred": True} if on_source else None,
            })
            threading.Thread(
                target=self._read_status, args=(process,), name=f"{self.app_module}-status", daemon=True,
            ).start()
        else:
            self._send({**payload, "cmd": "show"})
            if on_ready:
                on_ready(True)
        self._hidden = False

    def _read_status(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is None:
            return
        try:
            for line in process.stdout:
                try:
                    status = json.loads(line)
                    if not isinstance(status, dict) or self._process is not process:
                        continue
                    if status.get("kind") == "visible":
                        self._hidden = not bool(status.get("visible"))
                    elif status.get("kind") == "launch_source" and self._process is process:
                        def send(source: dict | None) -> None:
                            if self._process is process and process.poll() is None:
                                self._send({"cmd": "launch_source", "source": source})
                        if self._on_launch_source:
                            self._on_launch_source(send)
                    elif status.get("kind") == "launch_started" and self._process is process:
                        if self._on_launch_started:
                            self._on_launch_started()
                    elif status.get("kind") == "launch_ready":
                        if self._process is not process:
                            continue
                        callback, self._on_launch_ready = self._on_launch_ready, None
                        if callback:
                            callback(status.get("confirmed") is True)
                    else:
                        self._handle_status(status)
                except (ValueError, TypeError):
                    continue
        finally:
            if self._process is process:
                callback, self._on_launch_ready = self._on_launch_ready, None
                self._on_launch_source = self._on_launch_started = None
                if callback:
                    callback(False)

    def _handle_status(self, status: dict[str, Any]) -> None:
        """App data messages are handled by the surface, never this pipe."""

    def apply_visual_state(self, visuals: DesktopVisualState) -> None:
        if not isinstance(visuals, DesktopVisualState):
            raise TypeError(f"{self.app_title} requires DesktopVisualState")
        if self.is_running():
            self._send({"cmd": "theme", "theme": studio_theme(config=self.config, visuals=visuals)})

    def close(self) -> None:
        if self.is_running():
            self._send({"cmd": "hide"})
            self._hidden = True

    def destroy(self) -> None:
        if self.is_running():
            try:
                self._send({"cmd": "shutdown"})
            except (OSError, RuntimeError):
                pass
        process = self._process
        if process is not None and process.stdin is not None:
            process.stdin.close()
        self._process = None
        self._hidden = True
