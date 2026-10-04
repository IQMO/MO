"""SystemCare's native WebView host, using MO's existing visual controller."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from interface.desktop_brand import cube_mark_css, cube_mark_html, glyph_html, window_chrome_css, window_controls_html
from mo_desktop.design_studio.theme import studio_theme
from mo_desktop.mo_renderer import (_create_design_native_visual_controller, _initial_studio_theme_css,
                                    _renderer_icon_path, read_host_init, renderer_available, workspace_document)
from mo_desktop.visuals import load_desktop_config, load_desktop_visual_state


def _shell(theme: dict[str, Any], *, start_scan: bool = False, quick_action: str = "") -> str:
    root = Path(__file__).parent
    html = (root / "workspace.html").read_text(encoding="utf-8")
    css = (root.parent / "app_controls.css").read_text(encoding="utf-8") + (root / "workspace.css").read_text(encoding="utf-8")
    script = (root / "workspace.js").read_text(encoding="utf-8")
    glyphs = {}
    for name in ("search", "tools", "refresh", "chevron_right", "details", "folder", "convert", "close"):
        glyphs[name] = glyph_html(name)
    mark = cube_mark_html(label="MO")
    html = html.replace("@@MARK@@", mark)
    html = html.replace("@@WINDOW_CONTROLS@@", window_controls_html(("minimize", "toggle_maximize", "close")))
    css += window_chrome_css()
    for name in ("search", "tools", "refresh", "close"):
        html = html.replace(f"@@{name.upper()}@@", glyphs[name])
    html = html.replace("@@CHEVRON@@", glyphs["chevron_right"])
    initial = json.dumps({"icons": glyphs, "mark": mark, "start_scan": start_scan,
                          "quick_action": str(quick_action or "")}, ensure_ascii=False).replace("<", "\\u003c")
    return workspace_document(theme, theme_id="mo-care-theme", css=f"{cube_mark_css()}\n{css}", body=html,
                              scripts=(f"window.MO_CARE_INITIAL={initial};", script))


def run(config: dict[str, Any] | None = None, *, start_scan: bool = False, quick_action: str = "",
        launch_snapshot: dict | None = None) -> int:
    if not renderer_available():
        raise RuntimeError("SystemCare requires the installed MO Design WebView renderer")
    import webview
    from .bridge import SystemCareBridge
    from core.runtime.backend_monitor import BackendMonitor, get_monitor, set_monitor
    owns_monitor = get_monitor() is None
    if owns_monitor:
        set_monitor(BackendMonitor())
    if os.name == "nt":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MOAgent.SystemCare")
    config = load_desktop_config(config)
    visuals = load_desktop_visual_state(config, refresh_skin=True)
    theme = studio_theme(config=config, visuals=visuals)
    bridge = SystemCareBridge(config)
    def status(payload: dict[str, Any]) -> None:
        try:
            print(json.dumps(payload, ensure_ascii=False), flush=True)
        except OSError:
            pass  # A detached Desktop cannot receive the final safe boundary.
    bridge.on_status = status
    window = webview.create_window("MO SystemCare", html=_shell(theme, start_scan=start_scan, quick_action=quick_action), js_api=bridge,
                                   width=1080, height=760, min_size=(860, 560), resizable=True,
                                   frameless=True, easy_drag=False, shadow=False, on_top=False,
                                   hidden=os.name != "nt", background_color=str(theme["tokens"]["background"]))
    bridge.attach_window(window)

    native = _create_design_native_visual_controller(window, theme)
    native.configure_entrance(launch_snapshot, visuals, bridge.on_status, source_reader=False)
    def reveal() -> None:
        from core.runtime.resource_events import emit_component_resource_event
        native.mark_content_ready()
        emit_component_resource_event("desktop_systemcare_host", "ready", pids=(os.getpid(),))
        bridge.on_status({"kind": "visible", "visible": True})
    bridge.on_ui_ready = reveal
    window.events.shown += native.start
    window.events.minimized += lambda: bridge.set_visible(False)
    window.events.restored += lambda: bridge.set_visible(True)

    def closing() -> bool:
        if bridge.model.busy and not bridge._exiting:
            # FormClosing runs synchronously on the GUI lane. WebView script
            # evaluation waits for that lane, so ask after returning the veto.
            threading.Thread(target=lambda: window.evaluate_js("window.moCareRequestClose && window.moCareRequestClose()"),
                             name="mo-systemcare-close-choice", daemon=True).start()
            return False
        return True
    window.events.closing += closing

    def shutdown() -> None:
        bridge._exiting = True
        bridge._finish_then_close = False
        if bridge.model.busy:
            bridge.cancel()
            bridge.set_visible(False)
            window.hide()
            # Finish the current safe boundary and receipt before disposing
            # the host, including when Desktop's control pipe closes.
            thread = bridge.model._thread
            if thread is not None:
                thread.join()
        window.destroy()

    def commands() -> None:
        for line in sys.stdin:
            try:
                command = json.loads(line)
                action = command.get("cmd")
                if action == "launch_source":
                    native.supply_launch_source(command.get("source"))
                elif action == "theme":
                    payload = command["theme"]
                    native.refresh(payload)
                    window.evaluate_js("window.moCareApplyTheme(" + json.dumps(_initial_studio_theme_css(payload)) + ")")
                elif action == "show":
                    bridge.set_visible(True)
                    window.show()
                    if command.get("start_scan"):
                        bridge.scan(bridge.model.service.preference_summary()["scan_mode_default"])
                    if command.get("quick_action"):
                        quick = json.dumps(str(command.get("quick_action") or ""), ensure_ascii=False)
                        window.evaluate_js(f"window.moCareOpenQuick && window.moCareOpenQuick({quick})")
                elif action == "cancel":
                    bridge.cancel()
                elif action == "persist_result":
                    bridge.persist_result(str(command.get("request_id") or ""), command.get("ok") is True)
                elif action == "hide":
                    bridge.set_visible(False)
                    window.hide()
                elif action == "close":
                    window.evaluate_js("window.moCareRequestClose && window.moCareRequestClose()")
                elif action == "shutdown":
                    shutdown()
                    return
            except (ValueError, TypeError, KeyError):
                continue
        shutdown()

    def start(_window: Any) -> None:
        threading.Thread(target=commands, name="mo-systemcare-commands", daemon=True).start()
    try:
        webview.start(start, window, debug=False, icon=str(_renderer_icon_path(visuals, config)))
    finally:
        from core.runtime.resource_events import emit_component_resource_event
        bridge.close()
        thread = bridge.model._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        native.close()
        emit_component_resource_event("desktop_systemcare_host", "stop", pids=(os.getpid(),))
        if owns_monitor:
            set_monitor(None)
    return 0


def main() -> int:
    first = read_host_init("SystemCare")
    return run(first["config"], start_scan=first.get("start_scan") is True,
               quick_action=str(first.get("quick_action") or ""), launch_snapshot=first.get("launch_snapshot"))


if __name__ == "__main__":
    raise SystemExit(main())
