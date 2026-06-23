"""Command entrypoint for ``python -m interface.companion``.

This is the target used by the Windows startup shortcut. It runs the Companion
surface as a resident desktop process without launching the terminal UI.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[2]
CALLER_CWD = os.environ.get("MO_PROJECT_CWD") or os.getcwd()
os.environ.setdefault("MO_PROJECT_CWD", CALLER_CWD)
os.environ.setdefault("MO_INVOKED_AS", "mo-companion")
os.chdir(AGENT_ROOT)
sys.path.insert(0, str(AGENT_ROOT))

from core.agent.agent import create_agent
from core.gateway import Gateway
from core.path_defaults import default_config_path
from core.provider.provider import ConfigLoadError, ProviderError, clean_provider_error
from core.runtime_lock import acquire_runtime_lock
from core.text_safety import configure_utf8_stdio
from interface.companion.companion import CompanionSurface


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Run MO Ghost (desktop surface) as a resident window without the TUI.",
    )
    parser.add_argument("--config", default=None, help="Config path, default: ~/.mo/config.yaml (or MO_CONFIG)")
    parser.add_argument("--show", action="store_true", help="Show the Ghost window immediately")
    args = parser.parse_args(argv)

    if not acquire_runtime_lock(lock_name="mo-companion.lock", label="MO Companion"):
        return 1

    config_path = args.config or default_config_path(agent_root=AGENT_ROOT, caller_cwd=CALLER_CWD)
    try:
        agent = create_agent(config_path)
    except ConfigLoadError as exc:
        print(f"MO config error: {exc.message}", file=sys.stderr)
        print(f"  path: {exc.path}", file=sys.stderr)
        return 2
    except ProviderError as exc:
        print(f"MO provider error: {clean_provider_error(str(exc))}", file=sys.stderr)
        print(f"  config: {config_path}", file=sys.stderr)
        return 2

    companion_cfg = agent.config.get("desktop_companion", {}) if isinstance(agent.config, dict) else {}
    if not isinstance(companion_cfg, dict) or not companion_cfg.get("enabled", False):
        print("MO Companion is disabled. Set desktop_companion.enabled: true in your MO config.", file=sys.stderr)
        return 0

    gateway = Gateway(agent)
    companion = CompanionSurface(
        agent,
        gateway,
        voice_config=companion_cfg.get("voice", {}),
        companion_config=companion_cfg,
    )
    if not companion.start():
        return 1
    setattr(agent, "_companion", companion)
    if args.show:
        companion.show()

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)
    try:
        while companion._running and not stop_event.wait(0.5):
            time.sleep(0.05)
        return 0
    finally:
        companion.stop()


def _install_signal_handlers(stop_event: threading.Event) -> None:
    if threading.current_thread() is not threading.main_thread():
        return

    def _handle(_signum: int, _frame: object) -> None:
        stop_event.set()

    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handle)
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
