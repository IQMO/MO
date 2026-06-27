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
    mcp = getattr(agent, "mcp_manager", None)
    if mcp:
        try:
            clients = getattr(mcp, "_clients", {}) or {}
            if clients:
                summary = ", ".join(f"{n} ({len(getattr(c, 'tools', []) or [])} tools)" for n, c in clients.items())
                degraded = list(getattr(mcp, "degraded", []) or [])
                if degraded:
                    summary += f"; degraded: {', '.join(degraded)}"
                lines.append(f"  mcp:     {summary}")
        except Exception:
            pass
    return lines


def run_main_loop(agent: Any, gateway: Any, console: Any, has_rich: bool) -> None:
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
    try:
        for line in startup_identity_lines(agent):
            print(line)
    except Exception:
        pass

    # Prompt-toolkit TUI is the normal interface: styled logo, colors, palette,
    # Ghost/task/status panels, and keyboard-managed transcript scrolling.
    # Plain native scrollback is an explicit fallback via MO_NATIVE_SCROLL=1.
    if _input_module.HAS_PROMPT_TOOLKIT and sys.stdin.isatty() and should_use_prompt_toolkit_tui():
        tui = _tui_class()(agent, gateway)
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
