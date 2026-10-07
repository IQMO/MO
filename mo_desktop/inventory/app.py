"""Inventory's native WebView host (the same window, theme and entrance owners as MO Files and Mologrthim)."""
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


def _shell(theme: dict[str, Any]) -> str:
    root = Path(__file__).parent
    html = (root / "basket.html").read_text(encoding="utf-8")
    css = (root.parent / "app_controls.css").read_text(encoding="utf-8") + (root / "basket.css").read_text(encoding="utf-8")
    css += window_chrome_css()
    html = html.replace("@@MARK@@", cube_mark_html(label="MO"))
    html = html.replace("@@WINDOW_CONTROLS@@", window_controls_html(("minimize", "toggle_maximize", "close")))
    html = html.replace("@@SEARCH@@", glyph_html("search"))
    icons = {name: glyph_html(name) for name in ("terminal", "file", "folder", "pen", "send", "details", "pulse", "team")}
    initial = json.dumps({"icons": icons}, ensure_ascii=False).replace("<", "\\u003c")
    script = (root / "basket.js").read_text(encoding="utf-8")
    return workspace_document(theme, theme_id="inventory-theme", css=f"{cube_mark_css()}\n{css}", body=html,
                              scripts=(f"window.INVENTORY_INITIAL={initial};", script))


def run(config: dict[str, Any] | None = None, *, launch_snapshot: dict | None = None) -> int:
    if not renderer_available():
        raise RuntimeError("Inventory requires the installed MO Design WebView renderer")
    import webview
    from .bridge import InventoryBridge

    if os.name == "nt":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MOAgent.Inventory")
    config = load_desktop_config(config)
    visuals = load_desktop_visual_state(config, refresh_skin=True)
    theme = studio_theme(config=config, visuals=visuals)
    bridge = InventoryBridge(config)

    def status(payload: dict[str, Any]) -> None:
        try:
            print(json.dumps(payload, ensure_ascii=False), flush=True)
        except OSError:
            pass
    bridge.on_status = status
    window = webview.create_window("Inventory", html=_shell(theme), js_api=bridge, width=1440, height=900,
                                   min_size=(1000, 660), resizable=True, frameless=True, easy_drag=False, shadow=False,
                                   on_top=False, hidden=os.name != "nt", background_color=str(theme["tokens"]["background"]))
    bridge.attach_window(window)
    native = _create_design_native_visual_controller(window, theme)
    native.configure_entrance(launch_snapshot, visuals, bridge.on_status, source_reader=False)

    def reveal() -> None:
        from core.runtime.resource_events import emit_component_resource_event
        native.mark_content_ready()
        emit_component_resource_event("desktop_inventory_host", "ready", pids=(os.getpid(),))
        bridge.on_status({"kind": "visible", "visible": True})
    bridge.on_ui_ready = reveal
    window.events.shown += native.start
    window.events.minimized += lambda: bridge.set_visible(False)
    window.events.restored += lambda: bridge.set_visible(True)

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
                    window.evaluate_js("window.inventoryApplyTheme(" + json.dumps(_initial_studio_theme_css(payload)) + ")")
                elif action == "show":
                    bridge.set_visible(True)
                    window.show()
                elif action == "hide":
                    bridge.set_visible(False)
                    window.hide()
                elif action == "shutdown":
                    window.destroy()
                    return
            except (ValueError, TypeError, KeyError):
                continue
        window.destroy()

    def start(_window: Any) -> None:
        threading.Thread(target=commands, name="inventory-commands", daemon=True).start()
    try:
        webview.start(start, window, debug=False, icon=str(_renderer_icon_path(visuals, config)))
    finally:
        from core.runtime.resource_events import emit_component_resource_event
        bridge.close()
        native.close()
        emit_component_resource_event("desktop_inventory_host", "stop", pids=(os.getpid(),))
    return 0


def main() -> int:
    first = read_host_init("Inventory")
    return run(first["config"], launch_snapshot=first.get("launch_snapshot"))


if __name__ == "__main__":
    raise SystemExit(main())
