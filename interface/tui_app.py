"""Prompt-toolkit app bootstrap mixin for `MoTui`."""
from __future__ import annotations

import os
import threading
import time

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.layout.layout import Layout

from .input import SlashAndPathCompleter
from .theme import build_tui_style

LOGO_LINES: tuple[str, ...] = (
    "  █   █   ███ ",
    "  ██ ██  █   █",
    "  █ █ █  █   █",
    "  █   █   ███ ",
)


def startup_header_fragment_lines(agent, gateway) -> list[list[tuple[str, str]]]:
    """Return the TUI landing header: logo first, orientation beside it."""
    from .native_terminal import _startup_runtime_summary
    from .layout import STARTUP_HINT

    provider = str(getattr(agent, "provider_name", "") or "unknown")
    model = str(getattr(agent, "model", "") or "unknown")
    project = str(getattr(agent, "project_cwd", "") or os.environ.get("MO_PROJECT_CWD") or os.getcwd())
    runtime = _startup_runtime_summary(agent, gateway)
    info: tuple[tuple[str, str], ...] = (
        ("class:response-heading", f"MO v1.0 — {provider} / {model}"),
        ("class:dim", f"Project: {project}"),
        ("class:dim", f"Runtime: {runtime}" if runtime else "Runtime: clear"),
        ("class:dim", STARTUP_HINT),
    )
    rows: list[list[tuple[str, str]]] = []
    for index, logo in enumerate(LOGO_LINES):
        fragments: list[tuple[str, str]] = [("class:logo", logo)]
        if index < len(info):
            style, text = info[index]
            fragments.extend([("", "  "), (style, text)])
        rows.append(fragments)
    return rows


class TuiAppMixin:
    def _seed_startup_header(self) -> None:
        agent = getattr(self, "agent", None)
        gateway = getattr(self, "gateway", None)
        for fragments in startup_header_fragment_lines(agent, gateway):
            if hasattr(self, "_add_fragments_line"):
                self._add_fragments_line(fragments)
            else:
                # Compatibility for narrow TuiAppMixin harnesses that only test
                # the application contract and do not include transcript mixins.
                logo_text = fragments[0][1] if fragments else ""
                self._add("class:logo", logo_text)
        self._add("", "")

    def run(self):
        # Seed logo + MO-native orientation before transcript activity.
        self._seed_startup_header()

        self._input_buf = Buffer(completer=SlashAndPathCompleter(), complete_while_typing=False, on_text_changed=self._on_input_changed, history=self._input_history)

        from .keybindings import build_tui_key_bindings
        from .layout import build_tui_root, prompt_prefix

        kb = build_tui_key_bindings(self)
        root = build_tui_root(self, self._input_buf, prompt_prefix())

        style = build_tui_style()

        # Scroll/selection contract:
        # - full_screen=False keeps MO out of alternate-screen fullscreen capture.
        # - mouse_support=False preserves native terminal drag-select/copy.
        self._app = Application(
            layout=Layout(root, focused_element=self._input_buf),
            key_bindings=kb,
            full_screen=False,
            mouse_support=False,
            paste_mode=True,
            style=style,
        )

        # Invalidate while the app is alive; prompt_toolkit is not running yet
        # when this thread starts, so don't exit just because is_running is false.
        self._refresh_stop.clear()

        def _refresh_loop():
            while not self._refresh_stop.is_set():
                if self._app and (self.busy or self._goal_running or self._goal_worker_active or self._ghost_panel_open):
                    self._app.invalidate()
                time.sleep(0.25)

        threading.Thread(target=_refresh_loop, daemon=True).start()
        try:
            self._app.run()
        except KeyboardInterrupt:
            pass
        finally:
            self._refresh_stop.set()
