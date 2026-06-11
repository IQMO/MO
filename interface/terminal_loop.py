"""Terminal loop composition for MO."""
from __future__ import annotations

import os
import sys
import traceback
from typing import Any

from . import input as _input_module
from .main_terminal import MoTui
from .native_terminal import record_session, run_native_terminal_loop


def set_terminal_title(title: str) -> None:
    try:
        sys.stdout.write(f"\033]0;{title}\007")
        sys.stdout.flush()
    except Exception:
        traceback.print_exc()


def should_open_backend_monitor() -> bool:
    return os.environ.get("MO_OPEN_BACKEND_MONITOR") == "1"


def startup_identity_lines(agent: Any) -> list[str]:
    """One-time startup identity banner.

    Surfaces the resolved runtime path — derived from the actually-imported
    ``core`` package, not a hardcoded label — so a checkout launched from an
    unexpected folder (e.g. a second clone) is obvious immediately, instead of
    after a confused mid-session "which codebase am I?" investigation.
    """
    try:
        import core
        runtime_root = os.path.dirname(os.path.dirname(os.path.abspath(core.__file__)))
    except Exception:
        runtime_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cwd = os.getcwd()
    home = str(getattr(agent, "runtime_home", "") or "")
    provider = str(getattr(agent, "provider_name", "") or "")
    model = str(getattr(agent, "model", "") or "")
    lines = ["MO Agent", f"  runtime: {runtime_root}"]
    if cwd and os.path.normcase(os.path.abspath(cwd)) != os.path.normcase(os.path.abspath(runtime_root)):
        lines.append(f"  project: {cwd}")
    if home:
        lines.append(f"  home:    {home}")
    if provider or model:
        sep = " / " if provider and model else ""
        lines.append(f"  model:   {provider}{sep}{model}")
    return lines


def run_main_loop(agent: Any, gateway: Any, console: Any, has_rich: bool) -> None:
    monitor_opened = should_open_backend_monitor()
    if monitor_opened:
        gateway.monitor.open_window()
    set_terminal_title("MO")
    try:
        for line in startup_identity_lines(agent):
            print(line)
    except Exception:
        pass

    # Prompt-toolkit TUI is the default MO design. It avoids alternate-screen
    # fullscreen mode and owns its own transcript scrolling.
    if _input_module.HAS_PROMPT_TOOLKIT and sys.stdin.isatty() and os.environ.get("MO_NATIVE_SCROLL") != "1":
        tui = MoTui(agent, gateway)
        try:
            tui.run()
        finally:
            record_session(agent)
            if monitor_opened:
                gateway.monitor.close_window()
            print("MO Agent session ended.")
        return

    try:
        run_native_terminal_loop(agent, gateway, console)
    finally:
        record_session(agent)
        if monitor_opened:
            gateway.monitor.close_window()
        print("MO Agent session ended.")
