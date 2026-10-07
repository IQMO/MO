"""Native Settings view, using the shared MO Design window controller."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
from typing import Any

from interface.desktop_brand import cube_mark_css, cube_mark_html, glyph_html, window_chrome_css, window_controls_html
from mo_desktop.design_studio.theme import studio_theme
from mo_desktop.mo_renderer import (
    _create_design_native_visual_controller,
    _initial_studio_theme_css, _renderer_icon_path, read_host_init, renderer_available, workspace_document,
)
from mo_desktop.visuals import load_desktop_visual_state


def assets() -> tuple[str, str, str]:
    root = Path(__file__).parent
    html = (root / "workspace.html").read_text(encoding="utf8")
    html = html.replace("@@MARK@@", cube_mark_html(label="MO"))
    html = html.replace("@@WINDOW_CONTROLS@@", window_controls_html(("minimize", "close")))
    for name in ("tools", "search", "details", "close", "refresh", "phone", "folder", "resize"):
        html = html.replace("@@" + name.upper() + "@@", glyph_html(name))
    css = cube_mark_css() + (root.parent / "app_controls.css").read_text(encoding="utf8") + (root / "workspace.css").read_text(encoding="utf8") + window_chrome_css()
    return html, css, (root / "workspace.js").read_text(encoding="utf8")


def shell(theme: dict[str, Any]) -> str:
    html, css, script = assets()
    return workspace_document(theme, theme_id="settings-theme", css=css, body=html,
                              scripts=(script,), title="MO Settings")


class SettingsBridge:
    """Value-free settings transport plus one local-only credential entry method.

    Preferences belong to the host. Password entry goes straight to the private
    broker, never through the stdout request/event transport.
    """

    def __init__(self) -> None:
        self._window: Any = None
        self._on_ready: Any = None
        self._exiting = False
        self._config: dict = {}

    def save_media_key(self, value: str) -> dict:
        """A native password field writes through the canonical broker locally.

        The value never enters the stdout request transport, host diagnostics,
        saved snapshot, conversation or model context.
        """
        from core.media.catalog import settings
        from core.state.secrets import save_provider_secret

        try:
            save_provider_secret(str(settings(self._config).get("api_key_env") or "KIE_API_KEY"), value, config=self._config)
            return {"ok": True, "message": "Kie key saved privately. It was not sent to MO's conversation."}
        except (ValueError, OSError):
            return {"ok": False, "message": "The key could not be saved. Check its format and private profile access."}

    def request(self, request_id: str, action: str, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict) or len(json.dumps(payload)) > 16000:
            raise ValueError("Invalid Settings request")
        emit({"kind": "settings_request", "request_id": str(request_id)[:80],
              "action": str(action)[:60], "payload": payload})

    def ui_ready(self) -> None:
        if self._on_ready:
            self._on_ready()

    def window_control(self, action: str, width: int = 0, height: int = 0) -> None:
        if action == "close":
            self._window.evaluate_js("window.moSettingsClose()")
        elif action == "dispose":
            self._exiting = True
            self._window.destroy()
        elif action == "minimize":
            self._window.minimize()
        elif action == "resize":
            self._window.resize(max(760, min(3840, int(width))), max(560, min(2160, int(height))))
        else:
            raise ValueError("Unknown window action")


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def run(config: dict[str, Any], *, launch_snapshot: dict | None = None) -> int:
    if not renderer_available():
        raise RuntimeError("Settings requires the installed MO Design renderer")
    import webview
    from core.runtime.resource_events import emit_component_resource_event
    visuals = load_desktop_visual_state(config, refresh_skin=True)
    theme = studio_theme(config=config, visuals=visuals)
    bridge = SettingsBridge()
    bridge._config = config
    if os.name == "nt":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MOAgent.Settings")
    window = webview.create_window("MO Settings", html=shell(theme), js_api=bridge,
                                   width=1040, height=760, min_size=(760, 560),
                                   frameless=True, easy_drag=False, shadow=False,
                                   hidden=os.name != "nt", background_color=theme["tokens"]["background"])
    bridge._window = window

    native = _create_design_native_visual_controller(window, theme)
    native.configure_entrance(launch_snapshot, visuals, emit, source_reader=False)

    def ready() -> None:
        native.mark_content_ready()
        emit({"kind": "visible", "visible": True})
        handle = getattr(getattr(window, "native", None), "Handle", None)
        if handle is not None:
            emit({"kind": "settings_handle", "hwnd": int(handle.ToInt64())})
        emit_component_resource_event("desktop_settings_host", "ready", pids=(os.getpid(),))

    bridge._on_ready = ready
    window.events.shown += native.start
    exiting = False

    def closing() -> bool:
        if exiting or bridge._exiting:
            return True
        threading.Thread(target=lambda: bridge.window_control("close"), daemon=True).start()
        return False

    window.events.closing += closing

    def commands() -> None:
        nonlocal exiting
        try:
            for line in sys.stdin:
                command = json.loads(line)
                action = command.get("cmd")
                if action == "launch_source":
                    native.supply_launch_source(command.get("source"))
                elif action == "result":
                    window.evaluate_js("window.moSettingsResult(" + json.dumps(command).replace("<", "\\u003c") + ")")
                elif action == "theme":
                    native.refresh(command["theme"])
                    window.evaluate_js("window.moSettingsTheme(" + json.dumps(_initial_studio_theme_css(command["theme"])).replace("<", "\\u003c") + ")")
                elif action == "show":
                    window.show()
                    window.restore()
                    emit({"kind": "visible", "visible": True})
                    window.evaluate_js("window.moSettingsRefresh()")
                elif action == "hide":
                    bridge.window_control("close")
                elif action == "shutdown":
                    break
        finally:
            exiting = True
            window.destroy()

    def start(_window: Any) -> None:
        threading.Thread(target=commands, name="mo-settings-commands", daemon=True).start()

    emit_component_resource_event("desktop_settings_host", "start", pids=(os.getpid(),))
    try:
        webview.start(start, window, debug=False, icon=str(_renderer_icon_path(visuals, config)))
    finally:
        native.close()
        emit_component_resource_event("desktop_settings_host", "stop", pids=(os.getpid(),))
    return 0


def main() -> int:
    first = read_host_init("Settings")
    return run(first["config"], launch_snapshot=first.get("launch_snapshot"))


if __name__ == "__main__":
    raise SystemExit(main())
