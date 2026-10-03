"""Terminal loop composition for MO."""
from __future__ import annotations

import atexit
import os
import sys
import traceback
from typing import Any

from . import input as _input_module
from .native_terminal import record_session
from .terminal_host import TERMINAL_IDLE_TITLE

MoTui: Any | None = None


def _tui_class() -> Any:
    global MoTui
    if MoTui is None:
        from .main_terminal import MoTui as loaded_tui
        MoTui = loaded_tui
    return MoTui


def set_terminal_title(title: str) -> None:
    """Name MO's terminal window, and publish that name to the processes MO spawns.

    MO Desktop is one of those children. A terminal host (Windows Terminal) owns every window
    it hosts from a single process, so the child cannot recognise MO's window by process alone
    — it matches this title, which MO set itself. Nothing here is hardcoded on either side.
    """
    from core.runtime.instance import ENV_MO_TERMINAL_TITLE

    os.environ[ENV_MO_TERMINAL_TITLE] = str(title)
    try:
        sys.stdout.write(f"\033]0;{title}\007")
        sys.stdout.flush()
    except Exception:
        traceback.print_exc()


def _publish_terminal_session() -> None:
    """Tell the processes MO spawns (MO Desktop) which session slot this terminal writes, so they
    can read what MO is working on without scanning or guessing."""
    try:
        from core.runtime.instance import ENV_MO_TERMINAL_SESSION, instance_session_slot

        os.environ[ENV_MO_TERMINAL_SESSION] = instance_session_slot()
    except Exception:
        traceback.print_exc()


def should_open_backend_monitor() -> bool:
    return os.environ.get("MO_OPEN_BACKEND_MONITOR") == "1"


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


def run_main_loop(
    agent: Any,
    gateway: Any,
    *,
    startup_notice: str = "",
    startup_input: str = "",
) -> None:
    monitor_opened = should_open_backend_monitor()
    if monitor_opened:
        gateway.monitor.open_window()
    set_terminal_title(TERMINAL_IDLE_TITLE)
    _publish_terminal_session()
    # Backstop for an unhandled exit that still unwinds the interpreter. It does
    # NOT cover a hard teardown: a closed console window falls back to
    # TerminateProcess and SIGTERM terminates outright, and neither runs atexit
    # (measured, not assumed). Recovery uses the last successfully written
    # checkpoint; unsaved work and exit bookkeeping may be absent. Continuity
    # recovers a missing topic from that snapshot (topic_from_messages).
    # record_session is idempotent, so the normal path + atexit don't double-run.
    atexit.register(record_session, agent)
    # Startup banner = the instance notice + the MO Agent identity lines.
    try:
        banner = [ln for ln in str(startup_notice or "").splitlines() if ln.strip()]
        banner += list(startup_identity_lines(agent))
    except Exception:
        banner = []

    # The tabbed TUI is the only interactive terminal surface. Commands must not
    # fall through to a second scrollback REPL with separate rendering semantics.
    if not _input_module.HAS_PROMPT_TOOLKIT or not sys.stdin.isatty():
        record_session(agent)
        if monitor_opened:
            gateway.monitor.close_window()
        raise RuntimeError("MO Terminal requires an interactive prompt-toolkit TTY")

    tui = _tui_class()(agent, gateway)
    for line in banner:
        tui._add("class:dim", line)
    try:
        tui._startup_input = str(startup_input or "").strip()
        tui.run()
    finally:
        record_session(agent)
        if monitor_opened:
            gateway.monitor.close_window()
