"""MO Desktop app catalog, four-cube launcher, and optional system tray.

The cube opens the app catalog; the optional resident tray icon keeps
Show/Hide, toggle controls, advanced controls, panic-stop, restart,
and exit. Pystray is optional.
"""
from __future__ import annotations

import os
import math
import threading
import time
import traceback
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.runtime.subprocess_flags import gui_python_executable
from mo_desktop.layered import path_icon
from interface.desktop_brand import make_four_cube_icon, make_glyph_icon
from interface.desktop_ui import (
    DesktopVisualState,
    active_desktop_visual_state,
)
TRAY_TOOLTIP = "MO Desktop"

APP_GROUPS = (
    ("Work", ("dashboard", "shell", "files", "design", "mologrthim")),
    ("Devices", ("phone", "trackpad")),
    ("Care", ("systemcare",)),
    ("Your apps", ("settings",)),
)
APP_COLOR_ROLES = {"dashboard": "accent", "shell": "accent", "files": "ok",
                   "design": "warn", "phone": "accent", "trackpad": "action",
                   "systemcare": "ok", "settings": "muted", "mologrthim": "accent"}
APP_GLYPHS = {"dashboard": "split", "shell": "open", "files": "folder", "design": "file",
              "phone": "phone", "trackpad": "move", "systemcare": "refresh",
              "settings": "more", "mologrthim": "split"}

# The one app and tray item table. Add a new entry here; the cube launcher
# consumes its app actions and the optional tray retains controls and settings.
# `handler`/`checked`/`badge` are CompanionTray attribute NAMES so the table
# stays declarative; each renderer keeps its own layout policy (the menu
# splits the toggles around its Advanced submenu, the popup groups them).
TRAY_ITEMS: "tuple[dict[str, Any], ...]" = (
    {"id": "show_hide", "kind": "action", "label": "Show / Hide", "handler": "_on_show_hide", "default": True},
    {"id": "dashboard", "kind": "action", "label": "Dashboard", "handler": "_on_dashboard"},
    {"id": "shell", "kind": "action", "label": "MO Shell", "handler": "_on_shell"},
    {"id": "files", "kind": "action", "label": "MO Files", "handler": "_on_files", "badge": "_files_transfer_count"},
    {"id": "design", "kind": "action", "label": "MO Design", "handler": "_on_design"},
    {"id": "mologrthim", "kind": "action", "label": "Mologrthim", "handler": "_on_mologrthim"},
    {"id": "phone", "kind": "action", "label": "MO Phone", "handler": "_on_phone"},
    {"id": "systemcare", "kind": "action", "label": "MO SystemCare", "handler": "_on_systemcare"},
    {"id": "trackpad", "kind": "action", "label": "Phone Trackpad", "handler": "_on_phone_trackpad"},
    {"id": "settings", "kind": "action", "label": "Settings", "handler": "_on_open_settings"},
    {"id": "focus_mode", "kind": "toggle", "label": "Focus mode", "handler": "_on_toggle_focus", "checked": "_focus_enabled"},
    {"id": "voice_chat", "kind": "toggle", "label": "Continuous Voice Chat", "handler": "_on_toggle_voice_chat", "checked": "_voice_chat_enabled"},
    {"id": "run_at_startup", "kind": "toggle", "label": "Run at Startup", "handler": "_on_toggle_startup", "checked": "_startup_enabled"},
    {"id": "action_log", "kind": "advanced", "label": "Action Log", "handler": "_on_show_log"},
    {"id": "edit_config", "kind": "advanced", "label": "Edit config.yaml", "handler": "_on_edit_config"},
    {"id": "panic_stop", "kind": "danger", "label": "Panic Stop", "handler": "_on_panic_stop", "danger": True},
    {"id": "restart", "kind": "danger", "label": "Restart MO Desktop", "handler": "_on_restart"},
    {"id": "exit", "kind": "danger", "label": "Exit MO", "handler": "_on_exit"},
)


class CompanionTray:
    """System-tray icon for MO Desktop."""

    def __init__(self, companion: Any) -> None:
        self._companion = companion
        self._tray: Any = None
        self._tray_thread: threading.Thread | None = None
        self._running = False
        self._popup: TrayPopup | None = None
        self._launcher: CubeLauncher | None = None
        self._focus: Any = None
        self._popup_timer: threading.Timer | None = None
        self._ignore_next_left_up = False
        self._notification_action: Any = None
        self._notification_action_until = 0.0
        self._visuals = active_desktop_visual_state()

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
            target=self._tray_loop, name="mo-desktop-tray", daemon=True
        )
        self._tray_thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        self._cancel_scheduled_popup()
        self._post_gui(self._close_focus)
        if self._popup is not None:
            self._post_gui(self._popup.destroy)
        if self._launcher is not None:
            self._post_gui(self._launcher.hide)
        if self._tray:
            try:
                self._tray.stop()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Tray icon + menu
    # ------------------------------------------------------------------

    def _tray_loop(self) -> None:
        import pystray

        # Build a minimal MO glyph icon (32x32, cyan on dark)
        icon = self._make_icon()

        icon_type = pystray.Icon
        if os.name == "nt":
            try:
                from pystray._win32 import Icon as Win32Icon

                owner = self

                class DesktopTrayIcon(Win32Icon):
                    def _on_notify(self, wparam: Any, lparam: Any) -> None:
                        if owner._handle_tray_notify(self, lparam):
                            return
                        super()._on_notify(wparam, lparam)

                icon_type = DesktopTrayIcon
            except Exception:
                pass

        self._tray = icon_type("mo-desktop", icon, TRAY_TOOLTIP, None)
        try:
            self._tray.run()
        except Exception:
            if self._running:
                traceback.print_exc()

    def _on_open_settings(self, *_args) -> None:
        """Open the compact, live MO Desktop settings panel."""
        opener = getattr(self._companion, "open_settings_panel", None)
        if callable(opener):
            try:
                opener()
                return
            except Exception:
                traceback.print_exc()
                self._notify("MO Desktop Settings could not open.")
                return
        self._notify("MO Desktop Settings is unavailable.")

    def _on_edit_config(self, *_args) -> None:
        """Open MO's config.yaml so the operator can edit the `mo_desktop:` block
        directly (all keys — see settings.py)."""
        from core.state.paths import runtime_config_path

        config_getter = getattr(self._companion, "_config", None)
        config = config_getter() if callable(config_getter) else None
        path = Path(runtime_config_path(config, fallback_to_default=True))
        try:
            os.startfile(str(path) if path.exists() else str(path.parent))  # type: ignore[attr-defined]
        except Exception:
            traceback.print_exc()

    def _make_icon(self) -> Any:
        return make_four_cube_icon(palette=self._visuals.palette)

    def item_specs(self) -> tuple[dict[str, Any], ...]:
        """Merge the fixed public menu with admitted profile-owned apps.

        Private app names and implementations never enter the product table.
        They are projected only by the active profile into the themed launcher.
        """
        getter = getattr(self._companion, "private_desktop_app_specs", None)
        private_rows: list[dict[str, Any]] = []
        if callable(getter):
            try:
                for item in tuple(getter() or ()):
                    if not isinstance(item, dict):
                        continue
                    app_id = str(item.get("id") or "").strip()
                    label = str(item.get("label") or "").strip()
                    if app_id and label:
                        private_rows.append({
                            "id": f"private_app:{app_id}",
                            "kind": "action",
                            "label": label,
                            "private_app_id": app_id,
                        })
            except Exception:
                traceback.print_exc()
        if not private_rows:
            return TRAY_ITEMS
        rows: list[dict[str, Any]] = []
        inserted = False
        for spec in TRAY_ITEMS:
            if not inserted and spec["id"] == "trackpad":
                rows.extend(private_rows)
                inserted = True
            rows.append(spec)
        if not inserted:
            rows.extend(private_rows)
        return tuple(rows)

    def invoke(self, spec: dict[str, Any]) -> None:
        process = spec.get("shell_process")
        if process is not None:
            if process.poll() is None:
                from mo_desktop.mo_renderer import focus_renderer
                focus_renderer(process)
                self.pulse_app("shell")
            return
        app_id = str(spec.get("private_app_id") or "").strip()
        if app_id:
            opener = getattr(self._companion, "open_private_desktop_app", None)
            if callable(opener):
                opener(app_id)
            return
        handler = getattr(self, str(spec.get("handler") or ""), None)
        if callable(handler):
            handler(None, None)

    def apply_visual_state(self, visuals: DesktopVisualState) -> None:
        if not isinstance(visuals, DesktopVisualState):
            raise TypeError("Desktop tray requires DesktopVisualState")
        if self._focus is not None:
            try:
                self._focus.apply_visual_state(visuals)
            except Exception:
                self._focus.apply_visual_state(self._visuals)
                raise
        self._visuals = visuals
        try:
            if self._tray is not None:
                self._tray.icon = self._make_icon()
            if self._popup is not None:
                self._post_gui(lambda: self._popup.apply_visual_state(visuals))
            if self._launcher is not None:
                self._post_gui(lambda: self._launcher.apply_visual_state(visuals))
        except Exception:
            traceback.print_exc()

    def _post_gui(self, callback: Any) -> bool:
        post = getattr(self._companion, "_post_gui_call", None)
        return bool(callable(post) and post(callback))

    def _handle_tray_notify(self, icon: Any, lparam: Any) -> bool:
        """Consume Desktop tray clicks through the themed popup owner."""
        if lparam == 0x0405:  # NIN_BALLOONUSERCLICK
            return self._activate_notification()
        if lparam == 0x0202:  # WM_LBUTTONUP
            if self._ignore_next_left_up:
                self._ignore_next_left_up = False
            else:
                self._schedule_popup()
            return True
        if lparam == 0x0203:  # WM_LBUTTONDBLCLK
            self._cancel_scheduled_popup()
            self._ignore_next_left_up = True
            self._hide_popup()
            self._on_show_hide(icon, None)
            return True
        if lparam == 0x0205:  # WM_RBUTTONUP
            self._cancel_scheduled_popup()
            self._show_popup()
            return True
        return False

    def _show_popup(self, *, positioner: Any = None) -> bool:
        """Post the skin-aware popup when the GUI owner is ready."""
        root = getattr(self._companion, "_root", None)
        if root is None:
            return False

        def show() -> None:
            if self._popup is None:
                self._popup = TrayPopup(root, self)
            if positioner is None:
                self._popup.show()
            else:
                self._popup.show(positioner=positioner)

        return self._post_gui(show)

    def _schedule_popup(self) -> None:
        self._cancel_scheduled_popup()
        delay = 0.5
        try:
            import ctypes

            delay = max(0.15, float(ctypes.windll.user32.GetDoubleClickTime()) / 1000)
        except Exception:
            pass
        timer = threading.Timer(delay, self._show_popup)
        timer.daemon = True
        self._popup_timer = timer
        timer.start()

    def _cancel_scheduled_popup(self) -> None:
        timer, self._popup_timer = self._popup_timer, None
        if timer is not None:
            timer.cancel()

    def _hide_popup(self) -> None:
        if self._popup is not None:
            self._post_gui(self._popup.hide)

    def app_color(self, app_id: str) -> str:
        role = APP_COLOR_ROLES.get(app_id, "accent")
        return str(getattr(self._visuals.palette, role))

    def running_app_specs(self) -> tuple[dict[str, Any], ...]:
        getter = getattr(self._companion, "running_desktop_app_ids", None)
        running = set(getter() if callable(getter) else ())
        rows = []
        for spec in self.item_specs():
            if spec["id"] not in running:
                continue
            if spec["id"] == "shell":
                processes = [process for process in self._companion._shell_processes if process.poll() is None]
                rows.extend({**spec, "shell_process": process,
                             "label": spec["label"] if len(processes) == 1 else f"{spec['label']} {index + 1}"}
                            for index, process in enumerate(processes))
            else:
                rows.append(spec)
        return tuple(rows)

    def show_cube_launcher(self) -> None:
        root = getattr(self._companion, "_root", None)
        if root is None:
            return
        if self._launcher is None:
            self._launcher = CubeLauncher(root, self)
        cube = getattr(self._companion, "_cube", None)
        if cube is not None and getattr(cube, "_label_kind", "") == "running":
            cube._hide_label()
        self._launcher.show_full()

    def show_running_apps(self) -> None:
        if self._focus is not None:
            return
        cube = getattr(self._companion, "_cube", None)
        if cube is None:
            return
        specs = self.running_app_specs()
        if not specs:
            cube.hide_running_apps()
            return
        rows = tuple({**spec, "color": self.app_color(str(spec["id"])),
                      "glyph": APP_GLYPHS.get(str(spec["id"]), "open")}
                     for spec in specs)
        cube.show_running_apps(rows, self.invoke)

    def hide_running_apps(self) -> None:
        cube = getattr(self._companion, "_cube", None)
        if cube is not None:
            cube.hide_running_apps()

    def pulse_app(self, app_id: str) -> None:
        pulse = getattr(getattr(self._companion, "_cube", None), "pulse_app_color", None)
        if callable(pulse):
            pulse(self.app_color(app_id))

    # ------------------------------------------------------------------
    # Menu actions
    # ------------------------------------------------------------------

    def _focus_enabled(self) -> bool:
        return self._focus is not None

    def _close_focus(self) -> None:
        if self._focus is not None:
            focus, self._focus = self._focus, None
            focus.close()

    def _refresh_focus_toggle(self) -> None:
        """Publish the settled mode state, never the pre-queue tray value."""
        popup = self._popup
        if popup is not None:
            popup.refresh_toggles()

    def _on_toggle_focus(self, _icon: Any = None, _item: Any = None) -> None:
        # The tray and lower-right hold share one session-mode owner.
        if self._running:
            self._post_gui(self._toggle_focus_gui)

    def _toggle_focus_gui(self) -> None:
        if self._focus is not None:
            self._close_focus()
            self._refresh_focus_toggle()
            return
        if not self._running:
            return
        if self._launcher is not None and self._launcher.mode == "full":
            self._launcher.hide(on_complete=self._toggle_focus_gui)
            return
        root = getattr(self._companion, "_root", None)
        cube = getattr(self._companion, "_cube", None)
        if root is None or cube is None:
            return
        from mo_desktop.focus import FocusBar
        focus = FocusBar.__new__(FocusBar)
        try:
            focus.__init__(root, self)
            self._focus = focus
        except Exception:
            if getattr(focus, "_surface", None) is not None:
                focus.close()
            traceback.print_exc()
            cube.show_bubble("Focus mode could not start on this desktop.", seconds=3)
        finally:
            self._refresh_focus_toggle()

    def _on_show_hide(self, _icon: Any, _item: Any) -> None:
        if self._companion:
            self._companion.toggle()

    def _on_dashboard(self, _icon: Any, _item: Any) -> None:
        opener = getattr(self._companion, "open_dashboard", None)
        if callable(opener):
            opener()

    def _on_shell(self, _icon: Any, _item: Any) -> None:
        opener = getattr(self._companion, "open_mo_shell", None)
        if callable(opener):
            opener()

    def _on_files(self, _icon: Any, _item: Any) -> None:
        opener = getattr(self._companion, "open_files_panel", None)
        if callable(opener):
            opener()

    def _on_design(self, _icon: Any, _item: Any) -> None:
        opener = getattr(self._companion, "open_design_studio", None)
        if callable(opener):
            opener()

    def _on_phone(self, _icon: Any, _item: Any) -> None:
        opener = getattr(self._companion, "open_phone_panel", None)
        if callable(opener):
            opener()

    def _on_mologrthim(self, _icon: Any, _item: Any) -> None:
        opener = getattr(self._companion, "open_mologrthim", None)
        if callable(opener):
            opener()

    def _on_systemcare(self, _icon: Any, _item: Any) -> None:
        opener = getattr(self._companion, "open_systemcare_panel", None)
        if callable(opener):
            opener()

    def _on_phone_trackpad(self, _icon: Any, _item: Any) -> None:
        opener = getattr(self._companion, "open_phone_trackpad", None)
        if callable(opener):
            opener()

    def _on_show_log(self, _icon: Any, _item: Any) -> None:
        if self._companion:
            self._companion.show_action_log()

    def _files_transfer_count(self) -> int:
        """Live MO Files transfer count for the popup's badge decoration."""
        files = getattr(self._companion, "_files_window", None)
        return int(getattr(files, "active_transfer_count", 0) or 0)

    def _voice_chat_enabled(self) -> bool:
        cfg = getattr(self._companion, "_voice_cfg", {})
        return bool(cfg.get("chat_enabled", False)) if isinstance(cfg, dict) else False

    def _on_toggle_voice_chat(self, _icon: Any, _item: Any) -> None:
        enabled = not self._voice_chat_enabled()
        setter = getattr(self._companion, "set_voice_chat_enabled", None)
        ready = bool(callable(setter) and setter(enabled))
        if enabled and not ready:
            self._notify("Voice Chat needs the optional MO voice installation.")
            return
        persist = getattr(self._companion, "persist_desktop_setting", None)
        if callable(persist):
            persist("voice", "chat_enabled", enabled)

    def _on_toggle_startup(self, _icon: Any, _item: Any) -> None:
        enabled = self._startup_enabled()
        if not self._set_startup(not enabled):
            self._notify("Run-at-Startup needs the optional 'pywin32' package.")

    def _on_panic_stop(self, _icon: Any, _item: Any) -> None:
        if self._companion:
            self._companion.panic_stop()

    def _on_restart(self, _icon: Any, _item: Any) -> None:
        requester = getattr(self._companion, "request_restart", None)
        if callable(requester):
            requester()

    def _on_exit(self, _icon: Any, _item: Any) -> None:
        if self._companion:
            self._companion.stop()
        self._running = False
        if self._tray:
            self._tray.stop()

    def _notify(self, message: str, *, action: Any = None) -> None:
        """Best-effort tray balloon so silent actions (e.g. a failed startup toggle)
        give the user feedback. Degrades quietly if the backend can't notify."""
        self._notification_action = action if callable(action) else None
        self._notification_action_until = time.monotonic() + 30.0 if self._notification_action else 0.0
        try:
            if self._tray is not None:
                self._tray.notify(message, TRAY_TOOLTIP)
        except Exception:
            pass

    def _activate_notification(self) -> bool:
        action = self._notification_action
        self._notification_action = None
        if not callable(action) or time.monotonic() > self._notification_action_until:
            self._notification_action_until = 0.0
            return False
        self._notification_action_until = 0.0
        if not self._post_gui(action):
            try:
                action()
            except Exception:
                pass
        return True

    # ------------------------------------------------------------------
    # Startup management (Windows)
    # ------------------------------------------------------------------

    @staticmethod
    def _startup_enabled() -> bool:
        try:
            startup_dir = Path(os.environ.get("APPDATA", "")) / \
                          "Microsoft/Windows/Start Menu/Programs/Startup"
            return any(
                (startup_dir / name).exists()
                for name in ("MO Desktop.lnk",)
            )
        except Exception:
            return False

    @staticmethod
    def _set_startup(enable: bool) -> bool:
        """Create/remove the Startup shortcut. Returns True on success, False if it
        couldn't (e.g. pywin32 missing) so the caller can tell the user."""
        try:
            import pythoncom
            from win32com.client import Dispatch
            startup_dir = Path(os.environ.get("APPDATA", "")) / \
                          "Microsoft/Windows/Start Menu/Programs/Startup"
            startup_dir.mkdir(parents=True, exist_ok=True)
            shortcut_path = startup_dir / "MO Desktop.lnk"

            if enable:
                pythoncom.CoInitialize()
                try:
                    shell = Dispatch("WScript.Shell")
                    shortcut = shell.CreateShortcut(str(shortcut_path))
                    python_gui = gui_python_executable()
                    shortcut.TargetPath = python_gui
                    shortcut.Arguments = "-m mo_desktop"
                    shortcut.WorkingDirectory = str(Path(__file__).resolve().parent.parent)
                    shortcut.Description = "MO Desktop — on-screen AI assistant"
                    shortcut.IconLocation = python_gui
                    shortcut.Save()
                finally:
                    pythoncom.CoUninitialize()
            else:
                for path in (shortcut_path,):
                    if path.exists():
                        path.unlink()
            return True
        except ImportError:
            return False  # win32com not available
        except Exception:
            traceback.print_exc()
            return False


class CubeLauncher:
    """The four Desktop cubes grow into app-bearing cubes in the same window."""

    _WIDTH = 420
    _HEIGHT = 410
    _TILE_WIDTH = 190
    _TILE_HEIGHT = 190
    _TILE_POSITIONS = ((14, 8), (216, 8), (14, 210), (216, 210))
    _ROW_HEIGHT = 31
    _VISIBLE_ROWS = 4
    _EXPAND_SECONDS = 0.18

    def __init__(self, root: Any, owner: CompanionTray) -> None:
        self.root, self.owner = root, owner
        self.window: Any = None
        self.mode = ""
        self._surface: Any = None
        self._cube_bound = False
        self._fallback_canvas: Any = None
        self._fallback_photo: Any = None
        self._animation: Any = None
        self._row_offsets = [0] * 4
        self._editing = False
        self._dragging_app = ""
        self._press_point = (0, 0)
        self._drag_point = (0, 0)
        self._menu: dict[str, Any] | None = None
        self._menu_hits: list[tuple[tuple[int, int, int, int], dict[str, Any]]] = []
        self._hovered_control: tuple[str, str] = ("", "")
        self._picking = False
        from mo_desktop.desktop_launch import mo_desktop_config_block

        config_getter = getattr(owner._companion, "_config", None)
        config = config_getter() if callable(config_getter) else {}
        launcher_config = mo_desktop_config_block(config).get("launcher", {})
        saved = launcher_config.get("layout", {}) if isinstance(launcher_config, dict) else {}
        saved = saved if isinstance(saved, dict) else {}
        self._shortcuts = [dict(row) for row in saved.get("shortcuts", [])
                           if isinstance(row, dict) and row.get("id") and row.get("path")]
        groups = saved.get("groups", [])
        self._group_ids = [list(dict.fromkeys(str(value) for value in row)) for row in groups
                           if isinstance(row, list)] if isinstance(groups, list) else []
        if len(self._group_ids) != 4:
            self._group_ids = [list(ids) for _title, ids in APP_GROUPS]
        order = saved.get("order", [])
        self._group_order = order if isinstance(order, list) and sorted(str(i) for i in order) == ["0", "1", "2", "3"] else list(range(4))
        self._group_order = [int(i) for i in self._group_order]
        self._hidden_ids = {str(i) for i in saved.get("hidden", [])} if isinstance(saved.get("hidden"), list) else set()
        self._dragging_group = self._pressed_group = -1
        self._group_motion: tuple[float, dict[int, tuple[float, float]]] | None = None
        self._entry_motion: tuple[float, dict[str, tuple[float, float]]] | None = None
        self._remove_app = ""
        self._press_at = self._last_activity = time.monotonic()
        self._last_pointer: tuple | None = None
        self._hold_consumed = False
        self._buzz_started = 0.0
        self._hovered_app = ""
        self._hovered_tile = -1
        self._focus_amounts = [0.0] * 4
        self._row_amounts: dict[str, float] = {}
        self._pressed_app = ""
        self._hover_tick_at = 0.0
        self._hitboxes: list[tuple[tuple[int, int, int, int], dict[str, Any]]] = []
        self._tile_images: list[Any] = []
        self._dimmed_tiles: list[dict[int, Any]] = []
        self._art: Any = None
        self._origin = (28, 49)
        self._window_position = (0, 0)
        self._work_area: tuple[int, int, int, int] | None = None
        self._paint_size = (self._WIDTH, self._HEIGHT)
        self._starts: list[tuple[float, float]] = []
        self._start_sprites: list[Any] = []
        self._animation_started = 0.0
        self._animation_from = 0.0
        self._animation_target = 1.0
        self._progress = 0.0
        self._after_close: Any = None

    def _use_cube(self) -> bool:
        cube = getattr(self.owner._companion, "_cube", None)
        if cube is None or getattr(cube, "_win", None) is None:
            return False
        self.window = cube._win
        cube._launcher_painter = self._tick_hover
        self._surface = cube._ulw
        self._fallback_canvas = cube._canvas
        if not self._cube_bound:
            targets = (self.window,) if self._fallback_canvas is None else (self.window, self._fallback_canvas)
            for target in targets:
                target.bind("<ButtonPress-1>", self._on_press, add="+")
                target.bind("<ButtonRelease-1>", self._on_click, add="+")
                target.bind("<MouseWheel>", self._on_wheel, add="+")
                target.bind("<Motion>", self._on_motion, add="+")
            self.window.bind("<KeyPress>", self._on_key, add="+")
            self.window.bind("<FocusOut>", self._on_focus_out, add="+")
            self._cube_bound = True
        return True

    def _on_focus_out(self, _event: Any) -> None:
        if self._picking:
            return
        # Tk emits FocusOut while changing focus between children of this same
        # native cube. Decide after that transition, against the actual owner.
        def check() -> None:
            if self.mode != "full" or self._picking or self._animation is not None:
                return
            focused = self.window.focus_get()
            if focused is None or focused.winfo_toplevel() != self.window:
                self.hide()
        self.window.after_idle(check)

    def hide(self, *, on_complete: Any = None) -> None:
        if self.mode != "full" or self._animation_target == 0.0:
            return
        if self._animation is not None:
            self.window.after_cancel(self._animation)
            self._animation = None
        self._after_close = on_complete
        self._menu = None
        self._editing = False
        self._dragging_app = ""
        self._dragging_group = self._pressed_group = -1
        self._group_motion = None
        self._entry_motion = None
        self._remove_app = ""
        self._animation_from = self._progress
        self._animation_target = 0.0
        self._animation_started = time.monotonic()
        self._animate()

    def _finish_hide(self) -> None:
        cube = getattr(self.owner._companion, "_cube", None)
        if cube is not None:
            cube._launcher_active = False
            cube._launcher_painter = None
            cube._launcher_interacting = False
            cube._last_geometry = ""
            if cube._canvas is not None:
                cube._canvas.configure(width=cube._size, height=cube._size)
            cube._reposition()
            cube._win.update_idletasks()
            cube._render(time.monotonic())
            focus = getattr(cube, "_focus_controller", None)
            if focus is not None:
                focus.set_launcher_active(False)
            bubble = getattr(self.owner._companion, "_bubble", None)
            if bubble is not None and bubble._visible:
                bubble.set_launcher_active(False)
        self.mode = ""
        self._animation = None
        action, self._after_close = self._after_close, None
        if callable(action):
            action()

    def apply_visual_state(self, _visuals: DesktopVisualState) -> None:
        if self.mode == "full" and self.window is not None and self.window.winfo_viewable():
            self._render()
            self._blit(self._animation_frame(self._progress) if self._animation is not None else self._art)

    def _position(self, width: int, height: int, pieces: tuple = ()) -> tuple[int, int]:
        from interface.desktop_widgets import _monitor_work_area

        cube = getattr(self.owner._companion, "_cube", None)
        anchor = getattr(cube, "_win", None)
        center_x, center_y = (cube.center() if cube is not None
                              else (self.root.winfo_pointerx(), self.root.winfo_pointery()))
        if pieces:
            center_x = (min(cx-sprite.width/2 for (cx, cy), sprite in pieces)
                        + max(cx+sprite.width/2 for (cx, cy), sprite in pieces))/2
            center_y = (min(cy-sprite.height/2 for (cx, cy), sprite in pieces)
                        + max(cy+sprite.height/2 for (cx, cy), sprite in pieces))/2
        left, top, right, bottom = (_monitor_work_area(anchor) or
                                    (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()))
        self._work_area = (left, top, right, bottom)
        x = round(center_x - width / 2)
        y = round(center_y - height / 2)
        x = max(left + 8, min(x, right - width - 8))
        y = max(top + 8, min(y, bottom - height - 8))
        self._origin = (center_x - x, center_y - y)
        return max(left + 8, min(x, right - width - 8)), max(top + 8, min(y, bottom - height - 8))

    def show_full(self) -> None:
        if self.mode == "full" and self.window.winfo_viewable():
            self.hide()
            return
        if not self._use_cube():
            return
        self.mode = "full"
        self._row_offsets = [0] * 4
        self._hovered_app = ""
        self._hovered_tile = -1
        self._focus_amounts = [0.0] * 4
        self._row_amounts.clear()
        self._pressed_app = ""
        self._hover_tick_at = time.monotonic()
        self._last_activity = self._hover_tick_at
        self._last_pointer = None
        self._buzz_started = 0.0
        self._render()
        cube = self.owner._companion._cube
        now = time.monotonic()
        pieces = tuple(cube.launch_piece(index, now) for index in range(min(4, cube._cube_count())))
        width, height = self._WIDTH, self._HEIGHT
        x, y = self._position(width, height, pieces)
        self._window_position = (x, y)
        self._starts = []
        self._start_sprites = []
        focus = getattr(cube, "_focus_controller", None)
        for (cx, cy), sprite in pieces:
            self._starts.append((cx - x, cy - y))
            self._start_sprites.append(sprite)
        if focus is not None:
            focus.set_launcher_active(True)
        bubble = getattr(self.owner._companion, "_bubble", None)
        if bubble is not None and bubble._visible:
            bubble.set_launcher_active(True)
        cube._launcher_active = True
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self._paint_size = (width, height)
        self._animation_started = time.monotonic()
        self._animation_from = self._progress = 0.0
        self._animation_target = 1.0
        self._blit(self._animation_frame(0.0))
        self.window.deiconify()
        self.window.lift()
        self.window.focus_force()
        self._animate()

    def _animate(self) -> None:
        from mo_desktop.cube_motion import _ease_out
        if self.window is None or self.mode != "full":
            return
        elapsed = time.monotonic() - self._animation_started
        distance = abs(self._animation_target - self._animation_from)
        phase = min(1.0, elapsed / max(0.001, self._EXPAND_SECONDS * distance))
        ease = _ease_out(phase)
        self._progress = self._animation_from + (self._animation_target - self._animation_from) * ease
        frame_started = time.monotonic()
        self._blit(self._animation_frame(self._progress))
        if phase < 1:
            delay = max(1, 16 - round((time.monotonic() - frame_started) * 1000))
            self._animation = self.window.after(delay, self._animate)
        else:
            self._animation = None
            if self._animation_target == 0.0:
                self._finish_hide()

    @staticmethod
    def _font(size: int, *, bold: bool = False) -> Any:
        from mo_desktop.fonts import load_font
        names = ("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf")
        return load_font(names, size)

    @staticmethod
    def _fit_label(draw: Any, value: str, font: Any, width: int) -> str:
        if draw.textlength(value, font=font) <= width:
            return value
        label = value
        while label and draw.textlength(label.rstrip() + "…", font=font) > width:
            label = label[:-1]
        return label.rstrip() + "…"

    def _draw_cube_entry(self, image: Any, box: tuple[int, int, int, int],
                         spec: dict[str, Any], ink: str) -> None:
        from PIL import ImageDraw

        draw = ImageDraw.Draw(image)
        x0, y0, x1, y1 = box
        app_id = str(spec["id"])
        icon = path_icon(str(spec["path"])) if spec.get("path") else None
        icon = icon or make_glyph_icon(APP_GLYPHS.get(app_id, "open"), 15, color=ink)
        image.alpha_composite(icon.resize((30, 30)), ((x0 + 3) * 2, (y0 + 5) * 2))
        font = self._font(24)
        label = self._fit_label(draw, str(spec["label"]), font, (x1 - x0 - 29) * 2)
        draw.text(((x0 + 25) * 2, (y0 + 4) * 2), label, font=font, fill=ink)

    def _entries(self) -> list[list[dict[str, Any]]]:
        specs = {spec["id"]: spec for spec in self.owner.item_specs() if spec["id"] not in self._hidden_ids}
        specs.update({row["id"]: row for row in self._shortcuts})
        placed = {app_id for group in self._group_ids for app_id in group}
        for index, (_title, ids) in enumerate(APP_GROUPS):
            for app_id in ids:
                if app_id in specs and app_id not in placed:
                    self._group_ids[self._group_order.index(index)].append(app_id)
                    placed.add(app_id)
        for app_id in specs:
            if (app_id.startswith(("private_app:", "shortcut:")) and app_id not in placed):
                self._group_ids[self._group_order.index(3)].append(app_id)
                placed.add(app_id)
        return [[specs[app_id] for app_id in group if app_id in specs] for group in self._group_ids]

    def _render(self) -> None:
        from PIL import Image, ImageDraw, ImageFilter
        from interface.theming import contrast_text

        self._hitboxes = []
        groups = self._entries()
        cube = self.owner._companion._cube
        cube_rgb = tuple(cube._color_rgb)
        cube_hex = "#%02x%02x%02x" % cube_rgb
        ink = contrast_text(cube_hex, dark=self.owner._visuals.palette.card, light=self.owner._visuals.palette.text)
        cube_radius = float(cube._corner)
        face = (12, 12, (self._TILE_WIDTH - 6) * 2, (self._TILE_HEIGHT - 6) * 2)
        radius = round((self._TILE_WIDTH - 12) * cube_radius * 2)
        halo = Image.new("RGBA", (self._TILE_WIDTH * 2, self._TILE_HEIGHT * 2))
        ImageDraw.Draw(halo).rounded_rectangle(face, radius=radius,
                                                fill=(*cube_rgb, 105))
        cube_face = halo.filter(ImageFilter.GaussianBlur(8))
        ImageDraw.Draw(cube_face).rounded_rectangle(face, radius=radius, fill=cube_hex)
        self._tile_images = []
        self._dimmed_tiles = []
        self._row_sprites = {}
        for index, group_id in enumerate(self._group_order):
            title = APP_GROUPS[group_id][0]
            x, y = self._TILE_POSITIONS[index]
            tile = cube_face.copy()
            draw = ImageDraw.Draw(tile)
            heading_font = self._font(27, bold=True)
            heading_x = round((self._TILE_WIDTH * 2 - draw.textlength(title, font=heading_font)) / 2)
            draw.text((heading_x, 27), title, font=heading_font, fill=ink)
            if group_id == 0:
                tile.alpha_composite(make_glyph_icon("settings", 26, color=ink), (26, 30))
            entries = groups[index]
            self._row_offsets[index] = min(self._row_offsets[index], max(0, len(entries) - self._VISIBLE_ROWS))
            visible = entries[self._row_offsets[index]:self._row_offsets[index] + self._VISIBLE_ROWS]
            for row, spec in enumerate(visible):
                row_box = (x + 12, y + 43 + row * self._ROW_HEIGHT,
                           x + self._TILE_WIDTH - 12, y + 71 + row * self._ROW_HEIGHT)
                local = tuple(value - (x if pos % 2 == 0 else y)
                              for pos, value in enumerate(row_box))
                row_image = Image.new("RGBA", tile.size)
                self._draw_cube_entry(row_image, local, spec, ink)
                self._row_sprites[str(spec["id"])] = row_image.crop(tuple(v*2 for v in local)).resize(
                    (local[2]-local[0], local[3]-local[1]), Image.Resampling.LANCZOS)
                if not self._editing:
                    tile.alpha_composite(row_image)
                self._hitboxes.append((row_box, spec))
            if len(entries) > self._VISIBLE_ROWS:
                count = len(entries)
                track = (self._TILE_HEIGHT - 58) * 2
                thumb = max(12, round(track * self._VISIBLE_ROWS / count))
                offset = round((track - thumb) * self._row_offsets[index] / (count - self._VISIBLE_ROWS))
                draw.rounded_rectangle((self._TILE_WIDTH * 2 - 21, 91 + offset,
                                        self._TILE_WIDTH * 2 - 17, 91 + offset + thumb),
                                       radius=max(1, self.owner._visuals.metrics.button_corner_radius // 4),
                                       fill=ink)
            tile = tile.resize((self._TILE_WIDTH, self._TILE_HEIGHT), Image.Resampling.LANCZOS)
            self._tile_images.append(tile)
            self._dimmed_tiles.append({0: tile})
        self._art = self._highlight_app()

    @staticmethod
    @lru_cache(maxsize=16)
    def _edge_points(radius: int) -> tuple[tuple[float, float], ...]:
        """Evenly sample the native face perimeter, including square skins."""
        radius = max(0, min(91, radius))
        points = []
        corners = ((186 - radius, 4 + radius, -90),
                   (186 - radius, 186 - radius, 0),
                   (4 + radius, 186 - radius, 90),
                   (4 + radius, 4 + radius, 180))
        for cx, cy, angle in corners:
            for step in range(25):
                theta = math.radians(angle + step * 90 / 24)
                points.append((cx + radius * math.cos(theta),
                               cy + radius * math.sin(theta)))
        lengths = [0.0]
        for a, b in zip(points, points[1:] + points[:1]):
            lengths.append(lengths[-1] + math.dist(a, b))
        sampled = []
        segment = 0
        for index in range(360):
            distance = lengths[-1] * index / 360
            while segment < len(points) - 1 and lengths[segment + 1] <= distance:
                segment += 1
            a, b = points[segment], points[(segment + 1) % len(points)]
            span = lengths[segment + 1] - lengths[segment]
            fraction = (distance - lengths[segment]) / span if span else 0.0
            sampled.append((a[0] + (b[0] - a[0]) * fraction,
                            a[1] + (b[1] - a[1]) * fraction))
        return tuple(sampled)

    @staticmethod
    def _dim_sprite(image: Any, amount: float) -> Any:
        return image.point([round(v*(1-.42*amount)) for v in range(256)]*3
                           +[round(v*(1-.3*amount)) for v in range(256)]) if amount else image

    def _highlight_app(self, now: float | None = None) -> Any:
        from PIL import Image, ImageColor, ImageDraw, ImageFilter
        from mo_desktop.cube_motion import _ease_out
        from interface.theming import contrast_text

        now = time.monotonic() if now is None else now
        image = Image.new("RGBA", (self._WIDTH, self._HEIGHT))
        cube = self.owner._companion._cube
        radius = round((self._TILE_WIDTH - 12) * float(cube._corner)) + 2
        frame_radius = radius * 2
        row_radius = min(self._ROW_HEIGHT, self.owner._visuals.metrics.button_corner_radius * 2)
        points = self._edge_points(radius)
        focus_total = min(1.0, sum(self._focus_amounts))
        animated_rows = []
        remove_row = next((box for box, spec in self._hitboxes if str(spec["id"]) == self._remove_app), None)
        for index, tile in sorted(enumerate(self._tile_images), key=lambda item: item[0] == self._dragging_group):
            x, y = self._TILE_POSITIONS[index]
            amount = self._focus_amounts[index]
            dim_position = max(0.0, focus_total - amount) * 16
            lower = math.floor(dim_position)
            upper = min(16, lower + 1)
            cache = self._dimmed_tiles[index]
            if remove_row and x <= remove_row[0] < x+self._TILE_WIDTH and y <= remove_row[1] < y+self._TILE_HEIGHT:
                # The removal control belongs to this face, before its one
                # brightness/alpha pass. A separate overlay stays visibly opaque.
                from mo_desktop.card import SS
                p = self.owner._visuals.palette
                ink = contrast_text("#%02x%02x%02x" % tuple(cube._color_rgb), dark=p.card, light=p.text)
                fill = tuple(round(a+(b-a)*(.22 if self._hovered_control[0] == "__remove" else .12))
                             for a, b in zip(cube._color_rgb, ImageColor.getrgb(ink)))
                button = Image.new("RGBA", (22*SS, 22*SS))
                ImageDraw.Draw(button).rounded_rectangle((0, 0, 22*SS-1, 22*SS-1),
                    radius=self.owner._visuals.metrics.button_corner_radius*SS, fill=(*fill, 255))
                button.alpha_composite(make_glyph_icon("close", 10*SS, color=ink), (6*SS, 6*SS))
                tile = tile.copy()
                tile.alpha_composite(button.resize((22, 22), Image.Resampling.LANCZOS),
                                     (remove_row[2]-24-x, remove_row[1]+2-y))
                cache = {0: tile}
            for level in (lower, upper):
                if level not in cache:
                    cache[level] = self._dim_sprite(tile, level/16)
            fraction = dim_position - lower
            face = (Image.blend(cache[lower], cache[upper], fraction)
                    if fraction else cache[lower])
            rows = [(box, spec) for box, spec in self._hitboxes
                    if x <= box[0] < x + self._TILE_WIDTH and y <= box[1] < y + self._TILE_HEIGHT
                    and self._row_amounts.get(str(spec["id"]), 0.0)]
            held = _ease_out(min(1., max(0., (now-self._press_at-.12)/.18))) if self._pressed_group >= 0 else 0.
            if amount or rows or held:
                overlay = Image.new("RGBA", (380, 380))
                draw = ImageDraw.Draw(overlay)
                for box, spec in rows:
                    if not (x <= box[0] < x + self._TILE_WIDTH and y <= box[1] < y + self._TILE_HEIGHT):
                        continue
                    row_amount = self._row_amounts.get(str(spec["id"]), 0.0)
                    if row_amount:
                        alpha = 77 if spec["id"] == self._pressed_app else 46
                        local = tuple((value - (x if pos % 2 == 0 else y)) * 2
                                      for pos, value in enumerate(box))
                        draw.rounded_rectangle(local, radius=row_radius,
                                               fill=(255, 255, 255, round(alpha * row_amount)))
                if amount:
                    draw.rounded_rectangle((8, 8, 372, 372), radius=frame_radius,
                                           outline=(255, 255, 255, round(35 * amount)), width=2)
                    stroke = Image.new("RGBA", overlay.size)
                    edge_draw = ImageDraw.Draw(stroke)
                    phase = now / 4.0 % 1.0
                    for step, point in enumerate(points):
                        distance = (step / len(points) - phase) % 1.0
                        if distance >= .32:
                            continue
                        alpha = round(190 * amount * math.sin(math.pi * distance / .32) ** 2)
                        end = points[(step + 1) % len(points)]
                        edge_draw.line((point[0] * 2, point[1] * 2, end[0] * 2, end[1] * 2),
                                       fill=(255, 255, 255, alpha), width=3)
                    overlay.alpha_composite(stroke.filter(ImageFilter.GaussianBlur(1.6)))
                if held and index != self._pressed_group:
                    target = self._dragging_group >= 0 and index == self._hovered_tile
                    # The same faces show their available landing slots while a
                    # heading is held; a brighter inset marks the current slot.
                    draw.rounded_rectangle((12, 12, 368, 368), radius=frame_radius,
                                           outline=(255, 255, 255, round((190 if target else 72)*held)), width=4)
                face = face.copy()
                face.alpha_composite(overlay.resize(face.size, Image.Resampling.LANCZOS))
            if self._editing:
                for row_index, (box, spec) in enumerate(self._hitboxes):
                    if x <= box[0] < x+self._TILE_WIDTH and y <= box[1] < y+self._TILE_HEIGHT:
                        if str(spec["id"]) == self._dragging_app:
                            continue
                        sprite = self._dim_sprite(self._row_sprites[str(spec["id"])], dim_position/16)
                        if str(spec["id"]) == self._remove_app:
                            sprite = sprite.crop((0, 0, sprite.width-24, sprite.height))
                        angle = math.sin(now*11+row_index*1.7)*1.1
                        sprite = sprite.rotate(angle, Image.Resampling.BICUBIC)
                        rx, ry = box[:2]
                        if self._entry_motion:
                            started, starts = self._entry_motion
                            origin = starts.get(str(spec["id"]), (rx, ry))
                            ease = _ease_out(min(1., max(0., (now-started)/.23)))
                            rx, ry = round(origin[0]+(rx-origin[0])*ease), round(origin[1]+(ry-origin[1])*ease)
                        animated_rows.append((sprite, (rx, ry)))
            tx, ty = x, y
            if self._group_motion:
                started, starts = self._group_motion
                phase = min(1., max(0., (now-started)/.23))
                if index in starts:
                    origin = starts[index]
                    ease = _ease_out(phase)
                    tx, ty = (round(origin[0]+(x-origin[0])*ease), round(origin[1]+(y-origin[1])*ease))
            elif index == self._dragging_group:
                tx += self._drag_point[0]-self._press_point[0]
                ty += self._drag_point[1]-self._press_point[1]
            if held and index == self._pressed_group:
                face = face.rotate(math.sin((now-self._press_at)*9)*.8*held, Image.Resampling.BICUBIC)
            if self._buzz_started and 0 <= now-self._buzz_started < .6:
                from mo_desktop.emotes import shake
                dx, dy, _ = shake(index, now-self._buzz_started, .6)
                tx += round(dx*20)
                ty += round(dy*20)
            if self._group_order[index] == 0:
                self._gear_bounds = (tx+8, ty+10, tx+30, ty+32)
            image.alpha_composite(face, (tx, ty))
        for sprite, position in animated_rows:
            image.alpha_composite(sprite, position)
        return self._draw_controls(image, now)

    def _draw_controls(self, image: Any, now: float | None = None) -> Any:
        from PIL import Image, ImageColor, ImageDraw

        controls = Image.new("RGBA", image.size)
        draw = ImageDraw.Draw(controls)
        p = self.owner._visuals.palette
        radius = self.owner._visuals.metrics.button_corner_radius
        self._menu_hits = []
        self._menu_hits.append((self._gear_bounds, {"id": "__settings"}))
        quick_row = next((box for box, spec in self._hitboxes if str(spec["id"]) == "systemcare"), None)
        quick_amount = float(self._row_amounts.get("systemcare", 0.0) or 0.0)
        if (quick_row and quick_amount > .02 and not self._editing and not self._remove_app
                and self._menu is None):
            from interface.theming import contrast_text

            box = (quick_row[2] - 24, quick_row[1] + 2, quick_row[2] - 2, quick_row[1] + 24)
            hovered = self._hovered_control[0] == "__game_mode"
            fill = ImageColor.getrgb(p.ok)
            ink = contrast_text(p.ok, dark=p.card, light=p.text)
            draw.rounded_rectangle(
                box,
                radius=radius,
                fill=(*fill, round((235 if hovered else 190) * quick_amount)),
                outline=(*ImageColor.getrgb(p.border), round(220 * quick_amount)),
            )
            glyph = make_glyph_icon("power", 12, color=ink)
            glyph.putalpha(glyph.getchannel("A").point(lambda value: round(value * quick_amount)))
            controls.alpha_composite(glyph, (box[0] + 5, box[1] + 5))
            if quick_amount >= .16:
                self._menu_hits.append((box, {"id": "__game_mode", "label": "Game Session"}))
        if self._remove_app:
            row = next((box for box, spec in self._hitboxes if str(spec["id"]) == self._remove_app), None)
            if row:
                box = (row[2]-24, row[1]+2, row[2]-2, row[1]+24)
                self._menu_hits.append((box, {"id": "__remove", "app": self._remove_app}))
        if self._dragging_app:
            row = next((spec for group in self._entries() for spec in group if spec["id"] == self._dragging_app), None)
            if row:
                x, y = self._drag_point
                box = (max(8, min(x - 40, 252)), max(8, min(y - 12, 375)))
                draw.rounded_rectangle((box[0], box[1], box[0] + 156, box[1] + 26), radius=radius,
                                       fill=self.owner._visuals.palette.card, outline=self.owner._visuals.palette.border)
                draw.text((box[0] + 9, box[1] + 6), self._fit_label(draw, str(row["label"]), self._font(12), 140),
                          font=self._font(12), fill=self.owner._visuals.palette.text)
        menu = self._menu
        image.alpha_composite(controls)
        if menu is None:
            return image
        if menu.get("needs_listing"):
            self._load_folder(menu, [])
            if menu.get("paint") is not None:
                return self._composite_menu(image, menu["paint"], menu, now)
        paint_key = (id(menu.get("rows")), menu.get("query"), menu.get("offset"), menu.get("selected"),
                     self._hovered_control, tuple((key, round(value, 2)) for key, value in self._row_amounts.items()
                                                  if key.startswith("control:")),
                     menu.get("icon_generation", 0), self.owner._visuals, self._work_area, self._window_position)
        if menu.get("paint_key") == paint_key:
            self._menu_hits.extend(menu["paint_hits"])
            return self._composite_menu(image, menu["paint"], menu, now)
        first_hit = len(self._menu_hits)
        folder = menu.get("kind") == "folder"
        rows = menu.get("rows", [])
        query = str(menu.get("query") or "")
        filtered = [row for row in rows if query.casefold() in str(row["label"]).casefold()]
        available_width = self._work_area[2] - self._window_position[0] if self._work_area else self._WIDTH
        available_height = self._work_area[3] - self._window_position[1] if self._work_area else self._HEIGHT
        anchor_x, anchor_y = menu.get("anchor", (232, 244))
        room = max(available_height - anchor_y - 8, anchor_y - 8)
        header_height = (32 + (28 if query else 0)) if folder else 0
        visible_count = max(1, min(10, int((room - header_height - 30) // 28))) if folder else 10
        menu["visible_count"] = visible_count
        offset = min(int(menu.get("offset", 0)), max(0, len(filtered) - visible_count))
        menu["offset"] = offset
        menu["filtered"] = filtered
        visible = filtered[offset:offset + visible_count]
        if folder:
            self._load_folder(menu, visible)
        height = 12 + max(1 if folder else 0, len(visible)) * 28 + (header_height + 18 if folder else 0)
        label_width = max((draw.textlength(str(row["label"]), self._font(12)) for row in visible), default=130)
        width = min(available_width - 16, menu.get("width") or min(340, max(220, round(label_width) + 58))) if folder else 174
        menu["width"] = width
        x = max(8, min(anchor_x, available_width - width - 8))
        y = anchor_y if anchor_y + max(48, height) <= available_height - 8 else anchor_y - max(48, height)
        y = max(8, min(y, available_height - max(48, height) - 8))
        menu["bounds"] = (x, y, x + width, y + max(48, height))
        extent = (max(self._WIDTH, x + width + 8), max(self._HEIGHT, y + max(48, height) + 8))
        menu["paint_extent"] = extent
        if extent != image.size:
            expanded = Image.new("RGBA", extent)
            expanded.paste(image, (0, 0))
            image = expanded
        from mo_desktop import card
        ss = card.SS
        menu_image = card.surface_canvas((width+1, max(48, height)+1), self.owner._visuals)
        draw = ImageDraw.Draw(menu_image)
        font, small_font = card.role_font("small"), card.role_font("tiny")
        top = y + 6
        if folder:
            path = Path(menu["path"])
            title = card.fit_text(draw, path.name or str(path), (width-72)*ss, font)
            draw.text((35*ss, (top-y+4)*ss), title, font=font, fill=p.text)
            menu_image.alpha_composite(make_glyph_icon("chevron_left", 16*ss, color=p.text), (8*ss, (top-y+5)*ss))
            self._menu_hits.append(((x + 4, top, x + 31, top + 25), {"id": "__parent"}))
            self._menu_hits.append(((x + width - 29, top, x + width - 4, top + 25), {"id": "__explorer"}))
            menu_image.alpha_composite(make_glyph_icon("folder", 16*ss, color=p.text), ((width-25)*ss, (top-y+5)*ss))
            top += 29
            draw.line((9*ss, (top-y)*ss, (width-9)*ss, (top-y)*ss), fill=p.border, width=ss)
            top += 3
            if query:
                draw.text((12*ss, (top-y+4)*ss), card.fit_text(draw, query, (width-24)*ss, font),
                          font=font, fill=p.text)
                top += 28
        for index, row in enumerate(visible):
            box = (x + 5, top + index * 28, x + width - 5, top + (index + 1) * 28)
            hovered = self._row_amounts.get("control:" + str(row["id"]) + str(row.get("path") or ""), 0.0)
            if hovered or index == menu.get("selected", -1):
                amount = hovered or 1.0
                fill = tuple(round(a + (b - a) * amount) for a, b in
                             zip(ImageColor.getrgb(p.card), ImageColor.getrgb(p.entry)))
                draw.rounded_rectangle(((box[0]-x)*ss, (box[1]-y)*ss, (box[2]-x)*ss, (box[3]-y)*ss),
                                       radius=radius*ss, fill=(*fill, 255))
            icon = menu.get("icons", {}).get(str(row["path"])) if row.get("path") else None
            if icon is None and folder and row.get("kind") in {"file", "folder"}:
                icon = make_glyph_icon(row["kind"], 16*ss, color=p.muted)
            if icon is not None:
                menu_image.alpha_composite(icon.resize((16*ss, 16*ss), Image.Resampling.LANCZOS),
                                          ((box[0]-x+5)*ss, (box[1]-y+4)*ss))
            label = card.fit_text(draw, str(row["label"]), (width-48)*ss, font)
            draw.text(((box[0]-x+(27 if folder else 9))*ss, (box[1]-y+5)*ss), label, font=font, fill=p.accent if hovered else p.text)
            if row.get("kind") == "folder":
                menu_image.alpha_composite(make_glyph_icon("chevron_right", 12*ss, color=p.muted),
                                           ((box[2]-x-16)*ss, (box[1]-y+6)*ss))
            if not row.get("disabled"):
                self._menu_hits.append((box, row))
        if folder and not visible:
            draw.text((12*ss, (top-y+3)*ss), "No matches" if query else "Empty folder", font=font, fill=p.muted)
        if folder:
            count = len(filtered)
            label = (f"{offset + 1}–{min(count, offset + visible_count)} of {count}" if count > visible_count
                     else f"{count} item" + ("s" if count != 1 else ""))
            draw.text((12*ss, (height-16)*ss), label, font=small_font, fill=p.muted)
            if not query:
                draw.text(((width-12)*ss, (height-16)*ss), "Type to filter", font=small_font, fill=p.muted, anchor="ra")
            if count > visible_count:
                track = len(visible) * 28
                thumb = max(12, round(track * visible_count / count))
                start = top + round((track - thumb) * offset / (count - visible_count))
                draw.rounded_rectangle(((width-4)*ss, (start-y)*ss, (width-2)*ss, (start-y+thumb)*ss),
                                       radius=ss, fill=p.muted)
        menu["paint_key"] = paint_key
        menu["paint_hits"] = self._menu_hits[first_hit:]
        # Launcher composition expects straight alpha; preserve that boundary
        # while sharing the card's supersampling, typography and edge owner.
        rendered = card.finish(menu_image)
        menu["paint"] = Image.frombytes("RGBa", rendered.size, rendered.tobytes()).convert("RGBA")
        return self._composite_menu(image, menu["paint"], menu, now)

    def _composite_menu(self, image: Any, menu_image: Any, menu: dict[str, Any], now: float | None) -> Any:
        from PIL import Image
        from mo_desktop.cube_motion import _ease_out
        from mo_desktop.design import DEFAULT_DESKTOP_PANEL_DESIGN

        current = time.monotonic() if now is None else now
        x, y, _right, bottom = menu["bounds"]
        closing = bool(menu.get("closing"))
        layout_key = closing
        if menu.get("motion_key") != layout_key:
            started = current if "motion_key" in menu else menu.get("opened_at", current)
            duration = getattr(self.owner._companion._cube, "_panel_design", DEFAULT_DESKTOP_PANEL_DESIGN).transition_ms / 1000
            menu.update(motion_key=layout_key, motion_started=started,
                        motion_until=started + duration, motion_complete=False)
        duration = max(.001, menu["motion_until"] - menu["motion_started"])
        progress = min(1.0, max(0.0, (current - menu["motion_started"]) / duration))
        menu["motion_complete"] = progress >= 1
        ease = _ease_out(progress)
        count = len(menu["paint_hits"])
        if count:
            self._menu_hits = self._menu_hits[:-count]
        if closing and progress >= 1:
            self._menu = None
            return image
        if menu["paint_extent"] != image.size:
            expanded = Image.new("RGBA", menu["paint_extent"])
            expanded.paste(image, (0, 0))
            image = expanded
        up = bottom <= menu.get("anchor", (0, 0))[1]
        alpha = 1 - ease if closing else ease
        if alpha < 1:
            menu_image = menu_image.copy()
            menu_image.putalpha(menu_image.getchannel("A").point(lambda value: round(value * alpha)))
        shift = round(2 * (1 - ease) * (1 if up else -1)) if not closing else 0
        image.alpha_composite(menu_image, (x, y + shift))
        if not closing and alpha > 0:
            for (x0, y0, x1, y1), row in menu["paint_hits"]:
                self._menu_hits.append(((x0, y0 + shift, x1, y1 + shift), row))
        return image

    def _dismiss_menu(self) -> None:
        if self._menu is not None:
            self._menu["closing"] = True

    def _load_folder(self, menu: dict[str, Any], visible: list[dict[str, Any]]) -> None:
        listing = bool(menu.get("needs_listing"))
        paths = tuple(str(row["path"]) for row in visible if row.get("path"))
        if menu.get("icons_loading") or (not listing and all(path in menu.get("icons", {}) for path in paths)):
            return
        menu["icons_loading"] = True
        def collect() -> None:
            rows = None
            requested = paths
            if listing:
                try:
                    with os.scandir(menu["path"]) as directory:
                        rows = [{"id": "__entry", "label": entry.name, "path": entry.path,
                                 "kind": "folder" if entry.is_dir() else "file"} for entry in directory]
                    rows.sort(key=lambda row: (row["kind"] != "folder", row["label"].casefold()))
                except OSError:
                    rows = [{"id": "__unavailable", "label": "Folder unavailable", "disabled": True}]
                requested = tuple(str(row["path"]) for row in rows[:10] if row.get("path"))
                def publish_rows() -> None:
                    if self._menu is menu and self.mode == "full" and not menu.get("closing"):
                        menu.update(rows=rows, needs_listing=False)
                        if not menu.get("paint"):
                            menu["opened_at"] = time.monotonic()
                        self._art = self._highlight_app()
                        self._blit(self._art)
                self.owner._companion._post_gui_call(publish_rows)
            icons = {}
            for path in requested:
                try:
                    icons[path] = path_icon(path)
                except Exception:
                    icons[path] = None
            def publish() -> None:
                if self._menu is menu and self.mode == "full" and not menu.get("closing"):
                    menu["icons_loading"] = False
                    if rows is not None:
                        menu["rows"] = rows
                        menu["needs_listing"] = False
                    menu.setdefault("icons", {}).update(icons)
                    menu["icon_generation"] = menu.get("icon_generation", 0) + 1
                    self._art = self._highlight_app()
                    self._blit(self._art)
            self.owner._companion._post_gui_call(publish)
        threading.Thread(target=collect, name="mo-launcher-folder", daemon=True).start()

    def _save_layout(self) -> bool:
        persist = getattr(self.owner._companion, "persist_desktop_setting", None)
        if callable(persist) and persist("launcher", "layout", {"groups": self._group_ids, "shortcuts": self._shortcuts,
                                                               "order": self._group_order, "hidden": sorted(self._hidden_ids)}):
            return True
        notice = getattr(self.owner._companion, "_cube_notice", None)
        if callable(notice):
            notice("Launcher not saved", "The Desktop configuration could not be updated.")
        return False

    def _move_entry(self, app_id: str, tile: int, before_id: str = "") -> None:
        if app_id == before_id:
            return
        starts = {str(spec["id"]): box[:2] for box, spec in self._hitboxes}
        if self._editing:
            starts[app_id] = (max(8, min(self._drag_point[0]-40, 252)), max(8, min(self._drag_point[1]-12, 375)))
        prior = [list(group) for group in self._group_ids]
        for group in self._group_ids:
            if app_id in group:
                group.remove(app_id)
        group = self._group_ids[tile]
        group.insert(group.index(before_id) if before_id in group else len(group), app_id)
        if not self._save_layout():
            self._group_ids = prior
        elif self._editing:
            self._entry_motion = (time.monotonic(), starts)
        self._render()
        self._blit(self._art)

    def _pick_shortcut(self, kind: str) -> None:
        from tkinter import filedialog
        import uuid

        self._menu = None
        self._picking = True
        self._art = self._highlight_app()
        self._blit(self._art)
        try:
            picker = filedialog.askdirectory if kind == "folder" else filedialog.askopenfilename
            selected = picker(parent=self.window, title="Choose folder" if kind == "folder" else "Choose file")
        finally:
            self._picking = False
        if selected:
            path = Path(selected)
            if not any(Path(row["path"]) == path for row in self._shortcuts):
                row = {"id": "shortcut:" + uuid.uuid4().hex, "label": path.name or str(path), "path": str(path), "kind": kind}
                self._shortcuts.append(row)
                group = self._group_ids[self._group_order.index(3)]
                group.append(row["id"])
                if not self._save_layout():
                    self._shortcuts.remove(row)
                    group.remove(row["id"])
                self._render()
        self.window.focus_force()
        self._art = self._highlight_app()
        self._blit(self._art)

    def _open_folder(self, path: str, *, anchor: tuple[int, int] = (130, 75), root: str = "") -> None:
        previous = self._menu if root and self._menu else None
        menu = {"kind": "folder", "path": str(Path(path)), "root": root or str(Path(path)),
                "anchor": anchor, "rows": [{"id": "__loading", "label": "Loading…", "disabled": True}],
                "query": "", "offset": 0, "opened_at": time.monotonic(), "needs_listing": True}
        self._menu = menu
        if previous:
            menu.update(width=previous.get("width"), opened_at=previous.get("opened_at", time.monotonic()))
            if previous.get("paint") is not None:
                menu.update(paint=previous["paint"], paint_hits=[], bounds=previous["bounds"],
                            paint_extent=previous["paint_extent"])
        self._art = self._highlight_app()
        self._blit(self._art)

    def _menu_action(self, spec: dict[str, Any]) -> None:
        self._last_activity = time.monotonic()
        app_id = spec["id"]
        if app_id == "__game_mode":
            opener = getattr(self.owner._companion, "open_systemcare_game_mode", None)
            if callable(opener):
                self.hide(on_complete=opener)
            return
        if app_id == "__edit":
            self._editing = not self._editing
            self._menu = None
            self._remove_app = ""
            self._render()
        elif app_id == "__settings":
            self._menu = {"rows": [{"id": "__edit", "label": "Done editing" if self._editing else "Edit apps"},
                                     {"id": "__file", "label": "Add file…"},
                                     {"id": "__folder", "label": "Add folder…"},
                                     {"id": "__reset", "label": "Reset layout"},
                                     {"id": "__appearance", "label": "Appearance…"}],
                          "anchor": (self._gear_bounds[0]+4, self._gear_bounds[3]+6), "opened_at": time.monotonic()}
        elif app_id == "__appearance":
            settings = next((row for row in self.owner.item_specs() if row["id"] == "settings"), None)
            if settings:
                self.hide(on_complete=lambda: self.owner.invoke(settings))
            return
        elif app_id == "__reset":
            prior = self._group_ids, self._group_order, self._hidden_ids
            self._group_order, self._hidden_ids = list(range(4)), set()
            self._group_ids = [list(ids) for _title, ids in APP_GROUPS]
            self._group_ids[3].extend(row["id"] for row in self._shortcuts)
            if not self._save_layout():
                self._group_ids, self._group_order, self._hidden_ids = prior
            self._menu, self._remove_app = None, ""
            self._row_offsets = [0]*4
            self._render()
        elif app_id == "__remove":
            self._remove_entry(str(spec["app"]))
            self._remove_app = ""
        elif app_id in {"__file", "__folder"}:
            self._pick_shortcut("folder" if app_id == "__folder" else "file")
            return
        elif app_id == "__parent" and self._menu:
            menu = self._menu
            if Path(menu["path"]) != Path(menu["root"]):
                self._open_folder(str(Path(menu["path"]).parent), anchor=menu["anchor"], root=menu["root"])
            else:
                self._dismiss_menu()
        elif app_id == "__explorer" and self._menu:
            path = self._menu["path"]
            self.hide(on_complete=lambda: self._open_path(path))
            return
        elif spec.get("kind") == "folder":
            self._open_folder(spec["path"], anchor=self._menu["anchor"], root=self._menu["root"])
            return
        elif spec.get("path"):
            self.hide(on_complete=lambda: self._open_path(spec["path"]))
            return
        self._art = self._highlight_app()
        self._blit(self._art)

    def _open_path(self, path: str) -> None:
        try:
            os.startfile(path)
        except OSError:
            self.owner._companion._cube_notice("Shortcut unavailable", "Windows could not open the selected item.")

    def _remove_entry(self, app_id: str) -> None:
        removed = next((row for row in self._shortcuts if row["id"] == app_id), None)
        if removed is None and not any(spec["id"] == app_id for spec in self.owner.item_specs()):
            return
        prior = [list(group) for group in self._group_ids]
        if removed is not None:
            self._shortcuts.remove(removed)
        else:
            self._hidden_ids.add(app_id)
        for group in self._group_ids:
            if app_id in group:
                group.remove(app_id)
        if not self._save_layout():
            if removed is not None:
                self._shortcuts.append(removed)
            else:
                self._hidden_ids.discard(app_id)
            self._group_ids = prior
        self._render()

    def _move_group(self, source: int, target: int) -> None:
        if source == target:
            return
        for values in (self._group_ids, self._group_order, self._row_offsets):
            values[source], values[target] = values[target], values[source]
        if not self._save_layout():
            for values in (self._group_ids, self._group_order, self._row_offsets):
                values[source], values[target] = values[target], values[source]
        else:
            for values in (self._tile_images, self._dimmed_tiles):
                values[source], values[target] = values[target], values[source]
            moved = []
            for box, spec in self._hitboxes:
                index = next(i for i, (x, y) in enumerate(self._TILE_POSITIONS)
                             if x <= box[0] < x+self._TILE_WIDTH and y <= box[1] < y+self._TILE_HEIGHT)
                if index in (source, target):
                    destination = target if index == source else source
                    dx, dy = (b-a for a, b in zip(self._TILE_POSITIONS[index], self._TILE_POSITIONS[destination]))
                    box = tuple(value+(dx if i%2 == 0 else dy) for i, value in enumerate(box))
                moved.append((box, spec))
            self._hitboxes = moved
            origin = self._TILE_POSITIONS[source]
            dropped = (origin[0]+self._drag_point[0]-self._press_point[0],
                       origin[1]+self._drag_point[1]-self._press_point[1])
            self._group_motion = (time.monotonic(), {target: dropped, source: self._TILE_POSITIONS[target]})
        self._art = self._highlight_app()

    def _on_key(self, event: Any) -> str | None:
        if self.mode != "full":
            return None
        self._last_activity = time.monotonic()
        if event.keysym == "Escape":
            if self._menu is not None:
                self._dismiss_menu()
            elif self._editing:
                self._editing = False
                self._render()
            else:
                self.hide()
            self._art = self._highlight_app()
            self._blit(self._art)
            return "break"
        if self._menu:
            if self._menu.get("closing"):
                return "break"
            if event.keysym in {"Up", "Down", "Prior", "Next", "Home", "End", "Return"}:
                menu = self._menu
                filtered = menu.get("filtered", [])
                selected = max(0, int(menu.get("selected", 0)))
                if event.keysym == "Return":
                    index = menu.get("offset", 0) + selected
                    if index < len(filtered) and not filtered[index].get("disabled"):
                        self._menu_action(filtered[index])
                    return "break"
                absolute = menu.get("offset", 0) + selected
                if event.keysym == "Home":
                    absolute = 0
                elif event.keysym == "End":
                    absolute = len(filtered) - 1
                else:
                    page = menu.get("visible_count", 10)
                    absolute += {"Up": -1, "Down": 1, "Prior": -page, "Next": page}[event.keysym]
                absolute = max(0, min(max(0, len(filtered) - 1), absolute))
                offset = min(menu.get("offset", 0), absolute)
                offset = max(offset, absolute - menu.get("visible_count", 10) + 1)
                menu["offset"], menu["selected"] = offset, absolute - offset
            elif self._menu.get("kind") == "folder" and event.keysym == "BackSpace":
                if not self._menu["query"]:
                    self._menu_action({"id": "__parent"})
                    return "break"
                self._menu["query"] = self._menu["query"][:-1]
            elif self._menu.get("kind") == "folder" and event.char and event.char.isprintable():
                self._menu["query"] += event.char
            else:
                return None
            if event.keysym not in {"Up", "Down", "Prior", "Next", "Home", "End"}:
                self._menu["offset"] = 0
                self._menu["selected"] = 0
            self._art = self._highlight_app()
            self._blit(self._art)
            return "break"
        return None

    def _set_hover(self, x: int, y: int) -> None:
        control = next((spec for (x0, y0, x1, y1), spec in reversed(self._menu_hits)
                        if x0 <= x <= x1 and y0 <= y <= y1), None)
        self._hovered_control = ((str(control["id"]), str(control.get("path") or ""))
                                 if control else ("", ""))
        if self._menu is not None:
            self._hovered_tile = -1
            self._hovered_app = "control:" + "".join(self._hovered_control) if control else ""
            return
        self._hovered_tile = next((i for i, (tx, ty) in enumerate(self._TILE_POSITIONS)
                                   if tx + 6 <= x <= tx + 184 and ty + 6 <= y <= ty + 184), -1)
        spec = self._hit(x, y)
        self._hovered_app = (
            "systemcare" if control and str(control.get("id") or "") == "__game_mode"
            else "control:" + "".join(self._hovered_control) if control
            else str(spec["id"]) if spec is not None else ""
        )

    def _tick_hover(self, now: float) -> bool:
        """Paint on the cube clock and request active cadence only for interaction."""
        if self.mode != "full" or self._animation is not None:
            return False
        cube = self.owner._companion._cube
        point = cube._frame_pointer(now)
        previous_control = self._hovered_control
        if point is not None:
            if self._last_pointer != point:
                self._last_activity, self._last_pointer = now, point
                self._buzz_started = 0.0
            self._set_hover(round(point[0] - self._window_position[0]),
                            round(point[1] - self._window_position[1]))
        if self._picking or self._pressed_app or self._pressed_group >= 0:
            self._last_activity = now
        idle = now-self._last_activity
        if idle >= 15:
            self.hide()
            return False
        if idle >= 10 and not self._buzz_started:
            self._buzz_started = now
        buzzing = bool(self._buzz_started and now-self._buzz_started < .6)
        stationary = point is not None and math.dist(self._press_point, (point[0]-self._window_position[0], point[1]-self._window_position[1])) < 6
        if self._pressed_app and stationary and not self._dragging_app and not self._hold_consumed and now-self._press_at >= .55:
            self._remove_app, self._hold_consumed = self._pressed_app, True
            if not self._editing:
                self._editing = True
                self._render()
        finished_move = bool(self._group_motion and now-self._group_motion[0] >= .23)
        if finished_move:
            self._group_motion = None
        finished_entry = bool(self._entry_motion and now-self._entry_motion[0] >= .23)
        if finished_entry:
            self._entry_motion = None
        dt = max(0.0, min(.1, now - self._hover_tick_at))
        self._hover_tick_at = now
        active = (self._editing or buzzing or finished_move or finished_entry or self._group_motion is not None
                  or self._pressed_group >= 0 or bool(self._pressed_app and stationary and not self._hold_consumed)
                  or self._hovered_tile >= 0 or any(self._focus_amounts) or bool(self._row_amounts) or bool(self._hovered_app)
                  or previous_control != self._hovered_control
                  or bool(self._menu and not self._menu.get("motion_complete", False)))
        for index, value in enumerate(self._focus_amounts):
            target = float(index == self._hovered_tile)
            self._focus_amounts[index] = (min(target, value + dt / .16) if value < target
                                           else max(target, value - dt / .16))
        ids = set(self._row_amounts)
        if self._hovered_app:
            ids.add(self._hovered_app)
        for app_id in ids:
            value = self._row_amounts.get(app_id, 0.0)
            target = float(app_id == self._hovered_app)
            value = min(target, value + dt / .10) if value < target else max(target, value - dt / .10)
            if value:
                self._row_amounts[app_id] = value
            else:
                self._row_amounts.pop(app_id, None)
        if active:
            self._art = self._highlight_app(now)
            self._blit(self._art)
        return active

    def _animation_frame(self, progress: float) -> Any:
        from PIL import Image

        if progress >= 1:
            return self._art
        image = Image.new("RGBA", (self._WIDTH, self._HEIGHT))
        for index, tile in enumerate(self._tile_images):
            origin_x, origin_y = self._starts[index]
            source = self._start_sprites[index]
            target_x, target_y = self._TILE_POSITIONS[index]
            width = source.width + (self._TILE_WIDTH - source.width) * progress
            height = source.height + (self._TILE_HEIGHT - source.height) * progress
            center_x = origin_x * (1 - progress) + (target_x + self._TILE_WIDTH / 2) * progress
            center_y = origin_y * (1 - progress) + (target_y + self._TILE_HEIGHT / 2) * progress
            size = max(1, round(width)), max(1, round(height))
            sprite = Image.blend(source.resize(size, Image.Resampling.BILINEAR),
                                 tile.resize(size, Image.Resampling.BILINEAR), progress)
            image.alpha_composite(sprite, (round(center_x - size[0] / 2),
                                           round(center_y - size[1] / 2)))
        return image

    def _blit(self, image: Any) -> None:
        if self.window is None or image is None:
            return
        if image.size != self._paint_size:
            x, y = self._window_position
            self.window.geometry(f"{image.width}x{image.height}+{x}+{y}")
            self._paint_size = image.size
        if self._surface is not None and self._surface.available():
            self._surface.blit(image, *self._window_position)
        elif self._fallback_canvas is not None:
            from PIL import ImageTk

            self._fallback_canvas.configure(width=image.width, height=image.height)
            self._fallback_photo = ImageTk.PhotoImage(image, master=self.window)
            self._fallback_canvas.delete("all")
            self._fallback_canvas.create_image(0, 0, image=self._fallback_photo, anchor="nw")

    def _hit(self, x: int, y: int) -> dict[str, Any] | None:
        return next((spec for (x0, y0, x1, y1), spec in self._hitboxes
                     if x0 <= x <= x1 and y0 <= y <= y1), None)

    def _on_press(self, event: Any) -> None:
        if self.mode != "full" or self._animation is not None or self._group_motion:
            return
        self._press_at = self._last_activity = time.monotonic()
        self._hold_consumed = False
        control = any(x0 <= event.x <= x1 and y0 <= event.y <= y1
                      for (x0, y0, x1, y1), _ in self._menu_hits)
        spec = self._hit(event.x, event.y) if self._menu is None and not control else None
        self._pressed_app = str(spec["id"]) if spec is not None else ""
        self._press_point = (event.x, event.y)
        self._dragging_app = ""
        self._pressed_group = next((i for i, (x, y) in enumerate(self._TILE_POSITIONS)
                                    if x+45 <= event.x <= x+155 and y+10 <= event.y <= y+40), -1) if not self._editing and self._menu is None and not control else -1
        self._dragging_group = -1
        self._art = self._highlight_app()
        self._blit(self._art)

    def _on_click(self, event: Any) -> None:
        if self.mode != "full" or self._animation is not None or self._group_motion:
            return
        self._last_activity = time.monotonic()
        source, self._pressed_group = self._dragging_group, -1
        self._dragging_group = -1
        if source >= 0:
            origin = self._TILE_POSITIONS[source]
            self._group_motion = (time.monotonic(), {source: (
                origin[0]+self._drag_point[0]-self._press_point[0],
                origin[1]+self._drag_point[1]-self._press_point[1])})
            target = next((i for i, (x, y) in enumerate(self._TILE_POSITIONS)
                           if x <= event.x <= x+self._TILE_WIDTH and y <= event.y <= y+self._TILE_HEIGHT), -1)
            if target >= 0:
                self._move_group(source, target)
            self._art = self._highlight_app()
            self._blit(self._art)
            return
        if self._dragging_app:
            dragged = self._dragging_app
            self._dragging_app = self._pressed_app = ""
            tile = next((index for index, (x, y) in enumerate(self._TILE_POSITIONS)
                         if x <= event.x <= x + self._TILE_WIDTH and y <= event.y <= y + self._TILE_HEIGHT), -1)
            if tile >= 0:
                target = self._hit(event.x, event.y)
                self._move_entry(dragged, tile, str(target["id"]) if target else "")
            self._art = self._highlight_app()
            self._blit(self._art)
            return
        self._pressed_app = ""
        if self._hold_consumed:
            self._hold_consumed = False
            self._art = self._highlight_app()
            self._blit(self._art)
            return
        control = next((spec for (x0, y0, x1, y1), spec in reversed(self._menu_hits)
                        if x0 <= event.x <= x1 and y0 <= event.y <= y1), None)
        if control:
            self._menu_action(control)
            return
        if self._menu is not None:
            self._dismiss_menu()
            self._art = self._highlight_app()
            self._blit(self._art)
            return
        spec = self._hit(event.x, event.y)
        if spec is not None and not self._editing:
            if spec.get("kind") == "folder":
                box = next(box for box, item in self._hitboxes if item is spec)
                self._open_folder(spec["path"], anchor=(box[0], box[3] + 4))
                return
            if spec.get("path"):
                self.hide(on_complete=lambda: self._open_path(spec["path"]))
                return
            self.hide(on_complete=lambda item=spec: self.owner.invoke(item))

    def _on_motion(self, event: Any) -> None:
        if self.mode != "full" or self._animation is not None:
            return
        self._set_hover(event.x, event.y)
        self._last_activity = time.monotonic()
        self._buzz_started = 0.0
        if self._pressed_group >= 0 and math.dist(self._press_point, (event.x, event.y)) >= 6:
            self._dragging_group = self._pressed_group
            self._drag_point = (event.x, event.y)
            self._art = self._highlight_app()
            self._blit(self._art)
        if self._editing and self._pressed_app and math.dist(self._press_point, (event.x, event.y)) >= 6:
            self._dragging_app = self._pressed_app
            self._drag_point = (event.x, event.y)
            self._art = self._highlight_app()
            self._blit(self._art)

    def _on_wheel(self, event: Any) -> None:
        if self.mode != "full":
            return
        self._last_activity = time.monotonic()
        delta = -1 if event.delta > 0 else 1
        if self._menu is not None:
            if self._menu.get("kind") == "folder":
                self._menu["offset"] = max(0, self._menu.get("offset", 0) + delta * 3)
                self._art = self._highlight_app()
                self._blit(self._art)
            return
        tile = next((index for index, (x, y) in enumerate(self._TILE_POSITIONS)
                     if x <= event.x <= x + self._TILE_WIDTH and y <= event.y <= y + self._TILE_HEIGHT), -1)
        if tile < 0:
            return
        maximum = max(0, len(self._entries()[tile]) - self._VISIBLE_ROWS)
        offset = max(0, min(maximum, self._row_offsets[tile] + delta))
        if offset != self._row_offsets[tile]:
            self._row_offsets[tile] = offset
            self._render()
            self._blit(self._art)


class TrayPopup:
    """The resident menu, painted through the cube's shared alpha-card surface."""

    def __init__(self, root: Any, owner: CompanionTray) -> None:
        from interface.desktop_widgets import DesktopWindowEffectLayer
        self.root, self.owner = root, owner
        self._visuals = owner._visuals
        self.palette = self._visuals.palette
        self._surface = None
        self._effects = DesktopWindowEffectLayer()
        self._visible = self._advanced = False
        self._hover = self._pressed = None
        self._hits, self._specs, self._switch_amounts = {}, {}, {}
        self._after = None
        self._alpha = 0.0
        self._positioner = None
        self._position = (0, 0)
        self._size = (280, 354)

    def show(self, *, positioner: Any = None) -> None:
        import win32gui
        from mo_desktop.layered import NativeLayeredWindow
        if self._surface is None:
            self._surface = NativeLayeredWindow(on_message=self._message, post=self.owner._post_gui,
                                               title="MO Desktop — Menu")
        self._positioner = positioner
        self._visible = True
        self._hover = None
        self._render()
        win32gui.SetForegroundWindow(self._surface._native_hwnd)
        self._schedule()

    def hide(self) -> None:
        self._visible = False
        self._effects.hide()
        self._schedule()

    def destroy(self) -> None:
        if self._after is not None:
            self.root.after_cancel(self._after)
            self._after = None
        if self._surface is not None:
            self._surface.destroy()
            self._surface = None
        self._effects.destroy()

    def apply_visual_state(self, visuals: DesktopVisualState) -> None:
        if not isinstance(visuals, DesktopVisualState):
            raise TypeError("Desktop tray popup requires DesktopVisualState")
        self._visuals, self.palette = visuals, visuals.palette
        if self._visible:
            self._render()

    def refresh_toggles(self) -> None:
        if self._visible:
            self._schedule()

    def _schedule(self) -> None:
        if self._after is None and self._surface is not None:
            self._frame_at = time.monotonic()
            self._after = self.root.after(16, self._frame)

    def _frame(self) -> None:
        from mo_desktop.cube_motion import _time_scaled_ease
        self._after = None
        now = time.monotonic()
        ease = _time_scaled_ease(.45, now-self._frame_at)
        self._frame_at = now
        target = float(self._visible)
        self._alpha += (target-self._alpha)*ease
        if abs(target-self._alpha) < .003:
            self._alpha = target
        moving = self._alpha != target
        repaint = False
        for spec in self.owner.item_specs():
            if spec["kind"] != "toggle":
                continue
            target = float(bool(getattr(self.owner, spec["checked"])()))
            before = self._switch_amounts.get(spec["id"], target)
            amount = before+(target-before)*ease
            if abs(target-amount) < .003:
                amount = target
            self._switch_amounts[spec["id"]] = amount
            repaint |= amount != before
            moving |= amount != target
        if repaint:
            self._render()
        if self._alpha == 0:
            self._surface.hide()
        else:
            self._surface.set_opacity(round(self._alpha*255))
        if self._alpha == 1:
            self._effects.refresh_hwnd(self._surface._native_hwnd, *self._size,
                self._visuals.metrics.panel_corner_radius, self._visuals.effects, self._visuals.token("_GLOW"))
        if moving:
            self._after = self.root.after(16, self._frame)

    def _render(self) -> None:
        import win32api, win32gui
        from PIL import ImageDraw, Image
        from mo_desktop import card
        from interface.desktop_widgets import render_desktop_switch

        specs = list(self.owner.item_specs())
        rows = [spec for spec in specs if spec["id"] == "show_hide" or spec["kind"] == "toggle"]
        rows.append({"id": "advanced", "label": "Advanced", "kind": "expander"})
        if self._advanced:
            rows.extend(spec for spec in specs if spec["kind"] == "advanced")
        rows.extend(spec for spec in specs if spec["kind"] == "danger")
        size = (280, 82+len(rows)*34)
        image = card.surface_canvas(size, self._visuals)
        draw = ImageDraw.Draw(image)
        ss, p = card.SS, self.palette
        image.alpha_composite(make_four_cube_icon(28*ss, palette=p), (18*ss, 18*ss))
        draw.text((58*ss, 27*ss), "MO Desktop", font=card.role_font("section"), fill=p.text, anchor="lm")
        draw.text((58*ss, 47*ss), "Online" if self.owner._running else "Offline",
                  font=card.role_font("tiny"), fill=p.ok if self.owner._running else p.muted, anchor="lm")
        draw.line((16*ss, 66*ss, 264*ss, 66*ss), fill=p.border, width=ss)
        self._hits, self._specs = {}, {}
        for index, spec in enumerate(rows):
            key, y = spec["id"], 72+index*34
            self._hits[key], self._specs[key] = (8, y, 264, 32), spec
            if key == self._hover:
                draw.rounded_rectangle((8*ss, y*ss, 272*ss, (y+32)*ss),
                    radius=self._visuals.metrics.button_corner_radius*ss, fill=p.entry)
            color = p.error if spec.get("danger") else p.text
            draw.text((19*ss, (y+16)*ss), spec["label"], font=card.role_font("body"), fill=color, anchor="lm")
            if spec["kind"] == "toggle":
                target = float(bool(getattr(self.owner, spec["checked"])()))
                amount = self._switch_amounts.setdefault(key, target)
                switch = render_desktop_switch(p, amount, True).resize((42*ss, 22*ss), Image.Resampling.LANCZOS)
                image.alpha_composite(switch, (222*ss, (y+5)*ss))
            elif key == "advanced":
                glyph = make_glyph_icon("chevron_right", 12*ss, color=p.muted)
                if self._advanced:
                    glyph = glyph.rotate(-90)
                image.alpha_composite(glyph, (248*ss, (y+10)*ss))
        if self._positioner is not None:
            self._position = self._positioner(size)
        elif not self._alpha or size != self._size:
            px, py = win32gui.GetCursorPos()
            left, top, right, bottom = win32api.GetMonitorInfo(win32api.MonitorFromPoint((px, py), 2))["Work"]
            self._position = (max(left+8, min(px-size[0]+16, right-size[0]-8)),
                              max(top+8, min(py-size[1]-8, bottom-size[1]-8)))
        self._size = size
        self._surface.blit(card.finish(image), *self._position, premultiplied=True, opacity=round(self._alpha*255))
        self._surface.set_controls({key: (self._specs[key]["label"]+
            (", On" if getattr(self.owner, self._specs[key]["checked"])() else ", Off")
            if self._specs[key]["kind"] == "toggle" else self._specs[key]["label"], bounds)
            for key, bounds in self._hits.items()}, self._invoke)

    def _invoke(self, key: Any) -> None:
        if key == "advanced":
            self._advanced = not self._advanced
            self._render()
            return
        spec = self._specs.get(key)
        if spec is None:
            return
        if spec["kind"] != "toggle":
            self.hide()
        self.owner.invoke(spec)
        self.owner._post_gui(self.refresh_toggles)

    def _message(self, hwnd: int, message: int, wparam: int, lparam: int) -> int | None:
        if message in (0x0200, 0x0201, 0x0202):
            from mo_desktop.focus import FocusBar
            key = FocusBar._hit((lparam & 0xffff, (lparam >> 16) & 0xffff), self._hits)
            if message == 0x0200 and key != self._hover:
                self._hover = key
                self._render()
            elif message == 0x0201:
                self._pressed = key
            elif message == 0x0202 and key == self._pressed:
                self._invoke(key)
            return 0
        if message == 0x0006 and not wparam:
            self.hide()
            return 0
        if message == 0x0100:
            if wparam == 27:
                self.hide()
            elif wparam in (13, 32):
                self._invoke(self._hover)
            elif wparam in (9, 38, 40):
                keys = list(self._hits)
                index = keys.index(self._hover) if self._hover in keys else -1
                self._hover = keys[(index+(-1 if wparam == 38 else 1)) % len(keys)]
                self._render()
            return 0
        return None



def start_tray_if_enabled(
    companion: Any,
    companion_config: dict | None = None,
    voice_config: dict | None = None,
) -> CompanionTray | None:
    """Start the system tray if configured."""
    cfg = companion_config or {}
    previous_voice_cfg = voice_config or {}
    if "tray_enabled" in cfg:
        tray_enabled = bool(cfg.get("tray_enabled"))
    # COMPAT(desktop-tray-voice-key): replaced-by mo_desktop.tray_enabled; remove-when supported Desktop configs have migrated
    elif "tray_enabled" in previous_voice_cfg:
        tray_enabled = bool(previous_voice_cfg.get("tray_enabled"))
    else:
        tray_enabled = bool(cfg.get("enabled", False))
    if not tray_enabled:
        return None
    tray = CompanionTray(companion)
    if tray.start():
        return tray
    return None
