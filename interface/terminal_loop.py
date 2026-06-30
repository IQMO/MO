"""Terminal loop composition for MO."""
from __future__ import annotations

import atexit
import os
import sys
import traceback
from typing import Any

from . import input as _input_module
from .native_terminal import record_session, run_native_terminal_loop

MoTui: Any | None = None


def _tui_class() -> Any:
    global MoTui
    if MoTui is None:
        from .main_terminal import MoTui as loaded_tui
        MoTui = loaded_tui
    return MoTui


def set_terminal_title(title: str) -> None:
    try:
        sys.stdout.write(f"\033]0;{title}\007")
        sys.stdout.flush()
    except Exception:
        traceback.print_exc()


def should_open_backend_monitor() -> bool:
    return os.environ.get("MO_OPEN_BACKEND_MONITOR") == "1"


def should_use_prompt_toolkit_tui() -> bool:
    """True unless the operator explicitly opts into plain native scrollback."""
    return os.environ.get("MO_NATIVE_SCROLL") != "1"


def startup_identity_lines(agent: Any) -> list[str]:
    """Anomaly-only startup line — empty in normal use.

    The branded welcome already shows the project, model, and runtime state, so
    a generic identity block here was pure duplication. The one thing it can add
    that the welcome can't: the resolved runtime path is derived from the
    actually-imported ``core`` package, so when you launch a *different* checkout
    than the directory you're working in (the classic "second clone" footgun),
    that mismatch surfaces immediately. When the running checkout matches the
    working dir — the normal case — this returns nothing.
    """
    try:
        import core
        runtime_root = os.path.dirname(os.path.dirname(os.path.abspath(core.__file__)))
    except Exception:
        return []
    cwd = os.getcwd()
    if cwd and os.path.normcase(os.path.abspath(cwd)) != os.path.normcase(os.path.abspath(runtime_root)):
        return [f"⚠ running checkout {runtime_root} (working dir {cwd})"]
    return []


def run_main_loop(agent: Any, gateway: Any, console: Any, has_rich: bool, startup_notice: str = "") -> None:
    monitor_opened = should_open_backend_monitor()
    if monitor_opened:
        gateway.monitor.open_window()
    set_terminal_title("MO")
    # Backstop: if the process is torn down past the normal finally (terminal
    # window closed, SIGTERM, unhandled exit), still run the closeout once. The
    # conversation is already autosaved per turn; this preserves the end-of-session
    # bookkeeping (closeout + profile session stats) that the finally would do.
    # record_session is idempotent, so the normal path + atexit don't double-run.
    atexit.register(record_session, agent)
    # Startup banner = the instance notice + the MO Agent identity lines.
    try:
        banner = [ln for ln in str(startup_notice or "").splitlines() if ln.strip()]
        banner += list(startup_identity_lines(agent))
    except Exception:
        banner = []

    # Prompt-toolkit TUI is the normal interface: styled logo, colors, palette,
    # Ghost/task/status panels, and keyboard-managed transcript scrolling.
    # Plain native scrollback is an explicit fallback via MO_NATIVE_SCROLL=1.
    if _input_module.HAS_PROMPT_TOOLKIT and sys.stdin.isatty() and should_use_prompt_toolkit_tui():
        tui = _tui_class()(agent, gateway)
        # Seed the banner INTO the TUI transcript so it scrolls and /clears with
        # everything else, instead of sitting in native scrollback pinned above the TUI.
        try:
            for line in banner:
                tui._add("class:dim", line)
        except Exception:
            for line in banner:
                print(line)
        try:
            tui.run()
        finally:
            record_session(agent)
            if monitor_opened:
                gateway.monitor.close_window()
            print("MO Agent session ended.")
        return

    # Native fallback has no managed transcript — print the banner to scrollback.
    for line in banner:
        print(line)
    try:
        run_native_terminal_loop(agent, gateway, console)
    finally:
        record_session(agent)
        if monitor_opened:
            gateway.monitor.close_window()
        print("MO Agent session ended.")
