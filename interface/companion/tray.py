"""MO Companion Tray — system-tray icon with quick menu.

Phase 4 of the desktop companion: a resident tray icon (MO glyph) with
right-click menu for Show/Hide, Guide/Do mode, action log, run-at-startup,
and panic-stop. Uses pystray (optional dep, degrades gracefully).
"""
from __future__ import annotations

import os
import sys
import threading
import traceback
from pathlib import Path
from typing import Any

TRAY_TOOLTIP = "MO Companion"


class CompanionTray:
    """System-tray icon for the MO Companion."""

    def __init__(self, companion: Any) -> None:
        self._companion = companion
        self._tray: Any = None
        self._tray_thread: threading.Thread | None = None
        self._running = False
        self._mode: str = "guide"  # "guide" or "do"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        try:
            import pystray  # noqa: F401
            from PIL import Image  # noqa: F401
            return True
        except ImportError:
            return False

    def start(self) -> bool:
        if not self.available:
            return False
        if self._running:
            return True
        self._running = True
        self._tray_thread = threading.Thread(
            target=self._tray_loop, name="mo-companion-tray", daemon=True
        )
        self._tray_thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        if self._tray:
            try:
                self._tray.stop()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Mode
    # ------------------------------------------------------------------

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        self._mode = mode

    # ------------------------------------------------------------------
    # Tray icon + menu
    # ------------------------------------------------------------------

    def _tray_loop(self) -> None:
        import pystray

        # Build a minimal MO glyph icon (32x32, cyan on dark)
        icon = self._make_icon()

        menu = pystray.Menu(
            pystray.MenuItem("Show / Hide", self._on_show_hide, default=True),
            pystray.MenuItem("Mode", pystray.Menu(
                pystray.MenuItem(
                    "Guide (point + explain)", self._on_mode_guide,
                    checked=lambda item: self._mode == "guide",
                    radio=True,
                ),
                pystray.MenuItem(
                    "Do (drive cursor/keyboard)", self._on_mode_do,
                    checked=lambda item: self._mode == "do",
                    radio=True,
                ),
            )),
            pystray.MenuItem("Action Log", self._on_show_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Run at Startup", self._on_toggle_startup,
                             checked=lambda item: self._startup_enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Panic Stop", self._on_panic_stop),
            pystray.MenuItem("Exit Companion", self._on_exit),
        )

        self._tray = pystray.Icon("mo-companion", icon, TRAY_TOOLTIP, menu)
        try:
            self._tray.run()
        except Exception:
            if self._running:
                traceback.print_exc()

    @staticmethod
    def _make_icon() -> Any:
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Dark circle background
        draw.ellipse([2, 2, 30, 30], fill=(4, 20, 26, 255))  # #04141a
        # Cyan border
        draw.ellipse([2, 2, 30, 30], outline=(0, 204, 204, 255))  # #00cccc
        # Half-moon glyph (approximate with arc)
        draw.pieslice([6, 6, 26, 26], start=90, end=270, fill=(0, 204, 204, 255))
        draw.pieslice([8, 8, 24, 24], start=90, end=270, fill=(4, 20, 26, 255))
        return img

    # ------------------------------------------------------------------
    # Menu actions
    # ------------------------------------------------------------------

    def _on_show_hide(self, _icon: Any, _item: Any) -> None:
        if self._companion:
            self._companion.toggle()

    def _on_mode_guide(self, _icon: Any, _item: Any) -> None:
        self._mode = "guide"

    def _on_mode_do(self, _icon: Any, _item: Any) -> None:
        self._mode = "do"

    def _on_show_log(self, _icon: Any, _item: Any) -> None:
        if self._companion and hasattr(self._companion, "show_action_log"):
            self._companion.show_action_log()

    def _on_toggle_startup(self, _icon: Any, _item: Any) -> None:
        enabled = self._startup_enabled()
        self._set_startup(not enabled)

    def _on_panic_stop(self, _icon: Any, _item: Any) -> None:
        if self._companion:
            self._companion.panic_stop()

    def _on_exit(self, _icon: Any, _item: Any) -> None:
        if self._companion:
            self._companion.stop()
        self._running = False
        if self._tray:
            self._tray.stop()

    # ------------------------------------------------------------------
    # Startup management (Windows)
    # ------------------------------------------------------------------

    @staticmethod
    def _startup_enabled() -> bool:
        try:
            startup = Path(os.environ.get("APPDATA", "")) / \
                      "Microsoft/Windows/Start Menu/Programs/Startup/MO Companion.lnk"
            return startup.exists()
        except Exception:
            return False

    @staticmethod
    def _set_startup(enable: bool) -> None:
        try:
            import pythoncom
            from win32com.client import Dispatch
            startup_dir = Path(os.environ.get("APPDATA", "")) / \
                          "Microsoft/Windows/Start Menu/Programs/Startup"
            startup_dir.mkdir(parents=True, exist_ok=True)
            shortcut_path = startup_dir / "MO Companion.lnk"

            if enable:
                pythoncom.CoInitialize()
                try:
                    shell = Dispatch("WScript.Shell")
                    shortcut = shell.CreateShortcut(str(shortcut_path))
                    shortcut.TargetPath = sys.executable
                    shortcut.Arguments = "-m interface.companion"
                    shortcut.WorkingDirectory = str(Path(__file__).resolve().parent.parent.parent)
                    shortcut.Description = "MO Companion — on-screen AI assistant"
                    shortcut.IconLocation = sys.executable
                    shortcut.Save()
                finally:
                    pythoncom.CoUninitialize()
            else:
                if shortcut_path.exists():
                    shortcut_path.unlink()
        except ImportError:
            pass  # win32com not available
        except Exception:
            traceback.print_exc()


def start_tray_if_enabled(
    companion: Any,
    companion_config: dict | None = None,
    voice_config: dict | None = None,
) -> CompanionTray | None:
    """Start the system tray if configured."""
    cfg = companion_config or {}
    legacy_voice_cfg = voice_config or {}
    if "tray_enabled" in cfg:
        tray_enabled = bool(cfg.get("tray_enabled"))
    elif "tray_enabled" in legacy_voice_cfg:
        tray_enabled = bool(legacy_voice_cfg.get("tray_enabled"))
    else:
        tray_enabled = bool(cfg.get("enabled", False))
    if not tray_enabled:
        return None
    tray = CompanionTray(companion)
    if tray.start():
        return tray
    return None
