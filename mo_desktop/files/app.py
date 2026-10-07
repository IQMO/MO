"""Standalone MO Files WebView host using the existing Desktop visual owner."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from interface.desktop_brand import cube_mark_css, cube_mark_html, window_chrome_css, window_controls_html
from mo_desktop.design_studio.theme import studio_theme
from mo_desktop.mo_renderer import (
    _create_design_native_visual_controller,
    _initial_studio_theme_css,
    _renderer_icon_path,
    read_host_init,
    renderer_available,
    workspace_document,
)
from mo_desktop.visuals import load_desktop_config, load_desktop_visual_state

from .bridge import FilesBridge


def _shell(theme: dict[str, Any], initial: dict[str, str]) -> str:
    root = Path(__file__).parent
    html = (root / "board.html").read_text(encoding="utf-8")
    css = (root / "board.css").read_text(encoding="utf-8")
    css += window_chrome_css()
    html = html.replace("{{WINDOW_CONTROLS}}", window_controls_html(("toggle_pin", "minimize", "toggle_maximize", "close")))
    script = (root / "board.js").read_text(encoding="utf-8")
    initial_json = json.dumps(initial, ensure_ascii=False).replace("<", "\\u003c")
    source_mark = json.dumps(
        cube_mark_html(class_name="mo-mark source-cube"), ensure_ascii=False,
    ).replace("<", "\\u003c")
    return workspace_document(
        theme, theme_id="mo-files-theme", css=f'{cube_mark_css(".mo-mark")}\n{css}',
        body=html.replace("{{MO_MARK}}", cube_mark_html(class_name="mo-mark", label="MO")),
        scripts=(f"window.MO_FILES_INITIAL={initial_json};window.MO_FILES_MARK={source_mark};", script),
    )


def run(
    config: dict[str, Any] | None = None,
    *,
    source_id: str = "",
    location_id: str = "",
    launch_snapshot: dict | None = None,
) -> int:
    if not renderer_available():
        raise RuntimeError("MO Files requires the installed MO Design WebView renderer.")
    import webview

    if os.name == "nt":
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MOAgent.Files")
    loaded = load_desktop_config(config)
    visuals = load_desktop_visual_state(loaded, refresh_skin=True)
    theme = studio_theme(config=loaded, visuals=visuals)
    bridge = FilesBridge(loaded)
    bridge.prefetch_sources()   # the Hub round trip runs while the window starts
    bridge.on_status = lambda payload: print(json.dumps(payload), flush=True)
    shell = _shell(theme, {"source_id": source_id, "location_id": location_id})
    window = webview.create_window(
        "MO Files", html=shell, js_api=bridge,
        width=1220, height=760, min_size=(980, 620),
        resizable=True, frameless=True, easy_drag=False, shadow=False,
        on_top=False,
        hidden=os.name != "nt",
        background_color=str(theme["tokens"]["background"]),
    )
    bridge.attach_window(window)
    native_visuals = _create_design_native_visual_controller(window, theme)

    native_visuals.configure_entrance(launch_snapshot, visuals, bridge.on_status, source_reader=False)
    def reveal_board() -> None:
        native_visuals.mark_content_ready()
        bridge.on_status({"kind": "visible", "visible": True})
    bridge.on_ui_ready = reveal_board
    window.events.shown += native_visuals.start

    def bind_external_drop() -> None:
        from webview.dom import DOMEventHandler

        def on_drop(event: dict[str, Any]) -> None:
            files = event.get("dataTransfer", {}).get("files", [])
            paths = [
                str(item.get("pywebviewFullPath") or "")
                for item in files if item.get("pywebviewFullPath")
            ]
            if not paths:
                return
            source_id, location_id, directory = bridge._current
            try:
                if source_id != "desktop-local":
                    raise RuntimeError("Explorer files can move into a local MO Files location only.")
                result = bridge.import_managed_drop(paths, location_id, directory)
                message = result["error"] or f"Moved {len(result['completed'])} item(s) into this folder."
                ok = not bool(result["error"])
            except Exception as exc:
                message, ok = str(exc), False
            window.evaluate_js(
                "window.moFilesExternalDropResult(" + json.dumps({
                    "ok": ok, "message": message,
                }) + ")"
            )

        window.dom.document.events.drop += DOMEventHandler(on_drop, True, True)

    window.events.loaded += bind_external_drop

    def receive_commands() -> None:
        for line in sys.stdin:
            try:
                command = json.loads(line)
                selected = str(command.get("cmd") or "")
                if selected == "launch_source":
                    native_visuals.supply_launch_source(command.get("source"))
                elif selected == "theme":
                    payload = command["theme"]
                    css = _initial_studio_theme_css(payload)
                    native_visuals.refresh(payload)
                    window.evaluate_js(
                        "window.moFilesApplyTheme(" + json.dumps(css) + ")"
                    )
                elif selected == "show":
                    window.show()
                    bridge.on_status({"kind": "visible", "visible": True})
                    window.evaluate_js(
                        "window.moFilesNavigate(" + json.dumps({
                            "source_id": command.get("source_id") or "",
                            "location_id": command.get("location_id") or "",
                        }) + ")"
                    )
                elif selected == "hide":
                    window.destroy()
                    bridge.on_status({"kind": "visible", "visible": False})
                elif selected == "shutdown":
                    window.destroy()
                    return
            except Exception:
                # An invalid parent command never alters the file boundary.
                continue
        native_visuals.supply_launch_source(None)

    def start(_window: Any) -> None:
        threading.Thread(
            target=receive_commands, name="mo-files-window-commands", daemon=True,
        ).start()

    try:
        webview.start(start, window, debug=False, icon=str(_renderer_icon_path(visuals, loaded)))
    finally:
        try:
            bridge.close()
        finally:
            native_visuals.close()
    return 0


def main() -> int:
    command = read_host_init("Files", required=False) or {}
    return run(command.get("config"), source_id=str(command.get("source_id") or ""),
               location_id=str(command.get("location_id") or ""), launch_snapshot=command.get("launch_snapshot"))


if __name__ == "__main__":
    raise SystemExit(main())
