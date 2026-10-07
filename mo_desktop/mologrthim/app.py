"""Mologrthim: MO's operations floor as a native WebView app (the same window, theme and entrance owners as MO
Files and SystemCare). It shows every running MO from records and acts only through MO's existing handoff."""
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


def open_role_workspace(agent: Any, roles: Any = None) -> str:
    """`/role show` and the Project Architect's role_work show: open the floor at this MO's bay. Opening it
    starts no work and changes no role."""
    from core.runtime.instance import get_instance_id

    companion = getattr(agent, "_companion", None)
    try:
        if companion is not None and callable(getattr(companion, "open_mologrthim", None)):
            companion.open_mologrthim(focus=get_instance_id())
        else:
            from .window import MologrthimWindow

            window = getattr(agent, "_mologrthim_window", None)
            if window is None:
                window = MologrthimWindow(getattr(agent, "config", None) or {})
                agent._mologrthim_window = window
            window.show(focus=get_instance_id())
    except Exception as exc:
        return f"[ROLE WORKSPACE ERROR] Mologrthim could not open ({type(exc).__name__}). Use /role status for the roster."
    return ("[ROLE WORKSPACE OPEN] Mologrthim shows this MO's bay with its real specialists, their checked work "
            "and the other running MOs. No project task was started.")


def _shell(theme: dict[str, Any], *, focus: str = "") -> str:
    root = Path(__file__).parent
    html = (root / "floor.html").read_text(encoding="utf-8")
    css = (root.parent / "app_controls.css").read_text(encoding="utf-8") + (root / "floor.css").read_text(encoding="utf-8")
    css += window_chrome_css()
    html = html.replace("@@MARK@@", cube_mark_html(label="MO"))
    html = html.replace("@@WINDOW_CONTROLS@@", window_controls_html(("minimize", "toggle_maximize", "close")))
    html = html.replace("@@PLUS@@", glyph_html("plus") if _has_glyph("plus") else "+")
    initial = json.dumps({"focus": str(focus or "")}, ensure_ascii=False).replace("<", "\\u003c")
    script = (root / "floor.js").read_text(encoding="utf-8")
    return workspace_document(theme, theme_id="mologrthim-theme", css=f"{cube_mark_css()}\n{css}", body=html,
                              scripts=(f"window.MOLOGRTHIM_INITIAL={initial};", script))


def _has_glyph(name: str) -> bool:
    try:
        glyph_html(name)
        return True
    except Exception:
        return False


def run(config: dict[str, Any] | None = None, *, focus: str = "", launch_snapshot: dict | None = None) -> int:
    if not renderer_available():
        raise RuntimeError("Mologrthim requires the installed MO Design WebView renderer")
    import webview
    from .bridge import MologrthimBridge

    if os.name == "nt":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("MOAgent.Mologrthim")
    config = load_desktop_config(config)
    from core.local_extensions import configure as configure_extensions
    configure_extensions(config)          # the owner desk exists only when the private profile bridge offers it
    visuals = load_desktop_visual_state(config, refresh_skin=True)
    theme = studio_theme(config=config, visuals=visuals)
    bridge = MologrthimBridge(config, focus=focus)

    def status(payload: dict[str, Any]) -> None:
        try:
            print(json.dumps(payload, ensure_ascii=False), flush=True)
        except OSError:
            pass
    bridge.on_status = status
    window = webview.create_window("Mologrthim", html=_shell(theme, focus=focus), js_api=bridge,
                                   width=1440, height=900, min_size=(1100, 700), resizable=True,
                                   frameless=True, easy_drag=False, shadow=False, on_top=False,
                                   hidden=os.name != "nt", background_color=str(theme["tokens"]["background"]))
    bridge.attach_window(window)
    native = _create_design_native_visual_controller(window, theme)
    native.configure_entrance(launch_snapshot, visuals, bridge.on_status, source_reader=False)

    def reveal() -> None:
        from core.runtime.resource_events import emit_component_resource_event
        native.mark_content_ready()
        emit_component_resource_event("desktop_mologrthim_host", "ready", pids=(os.getpid(),))
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
                    window.evaluate_js("window.mologrthimApplyTheme(" + json.dumps(_initial_studio_theme_css(payload)) + ")")
                elif action == "show":
                    bridge.focus = str(command.get("focus") or bridge.focus)
                    bridge.set_visible(True)
                    window.show()
                    window.evaluate_js("window.mologrthimFocus && window.mologrthimFocus("
                                       + json.dumps(bridge.focus) + ")")
                elif action == "hide":
                    bridge.set_visible(False)
                    window.hide()
                elif action == "shutdown":
                    window.destroy()
                    return
            except (ValueError, TypeError, KeyError):
                continue
        window.destroy()            # the host's pipe closed (its Desktop or Terminal exited)

    def start(_window: Any) -> None:
        threading.Thread(target=commands, name="mologrthim-commands", daemon=True).start()
    try:
        webview.start(start, window, debug=False, icon=str(_renderer_icon_path(visuals, config)))
    finally:
        from core.runtime.resource_events import emit_component_resource_event
        bridge.close()
        native.close()
        emit_component_resource_event("desktop_mologrthim_host", "stop", pids=(os.getpid(),))
    return 0


def main() -> int:
    first = read_host_init("Mologrthim")
    return run(first["config"], focus=str(first.get("focus") or ""), launch_snapshot=first.get("launch_snapshot"))


if __name__ == "__main__":
    raise SystemExit(main())
