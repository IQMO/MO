"""Native MO Phone host using the shared app pipe and Design visual controller."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from interface.desktop_brand import cube_mark_css, cube_mark_html, glyph_html, window_chrome_css, window_controls_html
from mo_desktop.design_studio.theme import studio_theme
from mo_desktop.mo_renderer import (
    _create_design_native_visual_controller, _initial_studio_theme_css,
    _renderer_icon_path, renderer_available,
)
from mo_desktop.visuals import load_desktop_config, load_desktop_visual_state


def _shell(theme: dict[str, Any]) -> str:
    root = Path(__file__).parent
    html = (root / "workspace.html").read_text(encoding="utf-8")
    css = (root.parent / "app_controls.css").read_text(encoding="utf-8") + (root / "workspace.css").read_text(encoding="utf-8")
    script = (root / "workspace.js").read_text(encoding="utf-8")
    html = html.replace("@@MARK@@", cube_mark_html(label="MO"))
    html = html.replace("@@WINDOW_CONTROLS@@", window_controls_html(("pin", "minimize", "maximize", "close"), attribute="data-action"))
    for name in ("phone", "split", "move", "folder", "copy", "refresh", "details", "chevron_right", "close"):
        html = html.replace("@@" + name.upper() + "@@", glyph_html(name))
    css += window_chrome_css()
    return (
        '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'; '
        'script-src \'unsafe-inline\'; img-src data: blob:; connect-src \'none\'; font-src \'none\'; '
        'object-src \'none\'; base-uri \'none\'; form-action \'none\'">'
        f'<style id="mo-phone-theme">{_initial_studio_theme_css(theme)}</style><style>{cube_mark_css()}\n{css}</style>'
        f'</head><body>{html}<script>{script}</script></body></html>'
    )


def run(config: dict[str, Any] | None = None, *, host_status: str = "",
        files_available: bool = False, launch_snapshot: dict | None = None) -> int:
    if not renderer_available():
        raise RuntimeError("MO Phone requires the installed MO Design WebView renderer")
    import webview
    from .bridge import PhoneBridge
    from core.runtime.resource_events import emit_component_resource_event
    from core.runtime.backend_monitor import BackendMonitor, get_monitor, set_monitor
    owns_monitor = get_monitor() is None
    if owns_monitor:
        set_monitor(BackendMonitor())
    config = load_desktop_config(config)
    visuals = load_desktop_visual_state(config, refresh_skin=True)
    theme = studio_theme(config=config, visuals=visuals)
    bridge = PhoneBridge(config, host_status=host_status, files_available=files_available)
    if os.name == "nt":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MOAgent.Phone")
    def status(payload: dict[str, Any]) -> None:
        try:
            print(json.dumps(payload, ensure_ascii=False), flush=True)
        except OSError:
            pass
    bridge._on_status = status
    window = webview.create_window("MO Phone", html=_shell(theme), js_api=bridge,
                                   width=1060, height=760, min_size=(840, 620), resizable=True,
                                   frameless=True, easy_drag=False, shadow=False,
                                   hidden=os.name != "nt", background_color=str(theme["tokens"]["background"]))
    bridge._attach_window(window)

    native = _create_design_native_visual_controller(window, theme)
    native.configure_entrance(launch_snapshot, visuals, status, source_reader=False)
    def reveal() -> None:
        native.mark_content_ready()
        emit_component_resource_event("desktop_phone_host", "ready", pids=(os.getpid(),))
        status({"kind": "visible", "visible": True})
    bridge._on_ui_ready = reveal
    window.events.shown += native.start
    window.events.minimized += lambda: bridge._set_visible(False)
    window.events.restored += lambda: bridge._set_visible(True)
    exiting = False
    def closing() -> bool:
        if exiting:
            return True
        # Native FormClosing owns the GUI lane; defer ADB cleanup to the worker.
        threading.Thread(target=bridge._close_window, name="mo-phone-close", daemon=True).start()
        return False
    window.events.closing += closing

    def commands() -> None:
        nonlocal exiting
        try:
            for line in sys.stdin:
                try:
                    command = json.loads(line)
                    action = command.get("cmd")
                    if action == "launch_source":
                        native.supply_launch_source(command.get("source"))
                    elif action == "theme":
                        payload = command["theme"]
                        native.refresh(payload)
                        window.evaluate_js("window.moPhoneApplyTheme(" + json.dumps(_initial_studio_theme_css(payload)) + ")")
                    elif action == "show":
                        bridge._host_update(command.get("host_status", ""), command.get("files_available"))
                        bridge._set_visible(True)
                        window.show()
                        bridge.refresh()
                        status({"kind": "visible", "visible": True})
                    elif action == "host_status":
                        bridge._host_update(command.get("host_status", ""))
                    elif action == "trackpad":
                        bridge._open_trackpad()
                    elif action == "files_result":
                        bridge._files_result(command.get("ok") is True)
                    elif action == "hide":
                        bridge._close_window()
                    elif action == "shutdown":
                        break
                except (ValueError, TypeError, KeyError):
                    continue
        finally:
            bridge._shutdown()
            exiting = True
            window.destroy()

    def start(_window: Any) -> None:
        threading.Thread(target=commands, name="mo-phone-commands", daemon=True).start()
    emit_component_resource_event("desktop_phone_host", "start", pids=(os.getpid(),))
    try:
        webview.start(start, window, debug=False, icon=str(_renderer_icon_path(visuals, config)))
    finally:
        bridge._shutdown()
        native.close()
        emit_component_resource_event("desktop_phone_host", "stop", pids=(os.getpid(),))
        if owns_monitor:
            set_monitor(None)
    return 0


def main() -> int:
    first = json.loads(sys.stdin.readline())
    if first.get("cmd") != "init" or not isinstance(first.get("config"), dict):
        raise ValueError("Phone requires its Desktop initialization payload")
    return run(first["config"], host_status=str(first.get("host_status") or ""),
               files_available=first.get("files_available") is True, launch_snapshot=first.get("launch_snapshot"))


if __name__ == "__main__":
    raise SystemExit(main())
