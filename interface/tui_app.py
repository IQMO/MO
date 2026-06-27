"""Prompt-toolkit app bootstrap mixin for `MoTui`."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

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


def _active_provider_key_missing(agent) -> str:
    """Best-effort: name the active provider's key env var if it is required but
    absent (so the first turn would fail on a TUI that looks ready), else "".

    By the time the TUI renders, ~/.mo/.env is already loaded into the environment,
    so os.environ reflects keys placed there. Providers that authenticate by file
    (auth_path), inline key, or need no key (local hosts) are treated as satisfied.
    """
    try:
        cfg = getattr(agent, "config", {}) or {}
        if not isinstance(cfg, dict):
            return ""
        active = str(getattr(agent, "provider_name", "") or (cfg.get("model") or {}).get("default") or "")
        for provider_cfg in cfg.get("providers") or []:
            if not isinstance(provider_cfg, dict) or str(provider_cfg.get("name") or "") != active:
                continue
            env = str(provider_cfg.get("api_key_env") or "")
            if not env or os.environ.get(env) or provider_cfg.get("api_key"):
                return ""
            auth = provider_cfg.get("auth_path")
            if auth and Path(str(auth)).expanduser().is_file():
                return ""
            return env
    except Exception:
        pass
    return ""


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
    # First-step nudge: a new user knows commands exist (/help) but not what to ASK.
    rows.append([("class:dim", "Try: find issues in this project  ·  explain this codebase  ·  /help")])
    # Cold-start personalization: if MO has no operator name yet, invite the user to
    # seed the profile. Name auto-capture (terms_learning.capture_operator_name)
    # handles "I'm <Name>"; this nudge covers everyone who doesn't say it.
    prof = getattr(agent, "profile", None)
    if prof is not None and not str(getattr(prof, "user_name", "") or "").strip():
        rows.append([("class:info", "MO doesn't know you yet — tell it your name, or run /profile to personalize")])
    # If the active provider has no key, the first turn would fail with a provider
    # error on a TUI that looks ready — surface it upfront and point to the fix.
    missing_env = _active_provider_key_missing(agent)
    if missing_env:
        rows.append([("class:low-balance", f"⚠ no key for {provider} — add {missing_env} to ~/.mo/.env or run /doctor")])
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
        # - mouse_support=False: let the TERMINAL own the mouse, preserving native
        #   click-drag text selection/copy and native wheel scrollback. (Enabling
        #   PTK mouse_support captured the mouse and broke selection without giving
        #   reliable wheel scroll in this inline, non-fullscreen layout.) In-app
        #   scrolling is via the keyboard bindings (Up/Down, PageUp/PageDown).
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
