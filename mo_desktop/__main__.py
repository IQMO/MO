"""Command entrypoint for ``python -m mo_desktop``.

This is the target used by the Windows startup shortcut. It runs the Companion
surface as a resident desktop process without launching the terminal UI.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

AGENT_ROOT = Path(__file__).resolve().parents[1]
CALLER_CWD = os.environ.get("MO_PROJECT_CWD") or os.getcwd()
os.environ.setdefault("MO_PROJECT_CWD", CALLER_CWD)
# This process is authoritatively the Desktop surface even when its launcher
# inherited a terminal shim's MO_INVOKED_AS value.
os.environ["MO_INVOKED_AS"] = "mo-desktop"
os.chdir(AGENT_ROOT)
sys.path.insert(0, str(AGENT_ROOT))


def acquire_runtime_lock(**kwargs):
    from core.runtime.lock import acquire_runtime_lock as acquire

    return acquire(**kwargs)


def release_runtime_lock(lock) -> None:
    from core.runtime.lock import release_runtime_lock as release

    release(lock)


def desktop_transient_dir():
    from mo_desktop.desktop_launch import desktop_transient_dir as resolve

    return resolve()


def default_config_path(**kwargs):
    from core.state.paths import default_config_path as resolve

    return resolve(**kwargs)


def _install_signal_handlers(stop_event) -> None:
    from core.runtime.signals import install_signal_handlers

    install_signal_handlers(stop_event)


def consume_mo_desktop_summon() -> bool | dict:
    from mo_desktop.desktop_launch import consume_mo_desktop_summon as consume

    return consume()


def relaunch_mo_desktop_detached(*, config_path=None):
    from mo_desktop.desktop_launch import relaunch_mo_desktop_detached as relaunch

    return relaunch(config_path=config_path)


def _load_runtime_dependencies():
    """Load the Agent/GUI runtime only after this process wins the singleton lock."""
    from types import SimpleNamespace

    from core.agent.agent import create_agent
    from core.gateway import Gateway
    from core.provider.provider import ConfigLoadError, ProviderError, clean_provider_error
    from core.runtime.heartbeat import start_heartbeat_service_if_enabled
    from core.runtime.scheduler import start_scheduler_service_if_enabled
    from core.utils.text_safety import configure_utf8_stdio
    from mo_desktop.companion import CompanionSurface, mo_desktop_config
    from mo_desktop.desktop_log import log_event, log_exception
    from mo_desktop.everywhere import register_everywhere_desktop

    return SimpleNamespace(
        create_agent=create_agent,
        Gateway=Gateway,
        ConfigLoadError=ConfigLoadError,
        ProviderError=ProviderError,
        clean_provider_error=clean_provider_error,
        start_heartbeat_service_if_enabled=start_heartbeat_service_if_enabled,
        start_scheduler_service_if_enabled=start_scheduler_service_if_enabled,
        CompanionSurface=CompanionSurface,
        mo_desktop_config=mo_desktop_config,
        register_everywhere_desktop=register_everywhere_desktop,
        configure_utf8_stdio=configure_utf8_stdio,
        log_event=log_event,
        log_exception=log_exception,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse
    import threading
    from contextlib import contextmanager

    parser = argparse.ArgumentParser(
        description="Run MO Desktop as a resident window without the TUI.",
    )
    parser.add_argument("--config", default=None, help="Config path, default: ~/.mo/config.yaml (or MO_CONFIG)")
    parser.add_argument("--show", action="store_true", help="Show MO Desktop immediately")
    args = parser.parse_args(argv)

    runtime_lock = acquire_runtime_lock(
        lock_name="mo-desktop.lock",
        lock_dir=desktop_transient_dir(),
        label="MO Desktop",
        fail_open=False,
    )
    if not runtime_lock:
        return 1

    deps = _load_runtime_dependencies()
    deps.configure_utf8_stdio()
    deps.log_event("MO Desktop entrypoint starting")
    config_path = args.config or default_config_path(agent_root=AGENT_ROOT, caller_cwd=CALLER_CWD)
    deps.log_event(f"loading config from {config_path}")
    try:
        agent = deps.create_agent(config_path)
    except deps.ConfigLoadError as exc:
        deps.log_event(f"config error: {exc.message}; path={exc.path}")
        print(f"MO config error: {exc.message}", file=sys.stderr)
        print(f"  path: {exc.path}", file=sys.stderr)
        return 2
    except deps.ProviderError as exc:
        deps.log_event(f"provider error during startup: {deps.clean_provider_error(str(exc))}")
        print(f"MO provider error: {deps.clean_provider_error(str(exc))}", file=sys.stderr)
        print(f"  config: {config_path}", file=sys.stderr)
        return 2
    except Exception:
        deps.log_exception("mo-desktop-agent-create-failed")
        raise

    companion_cfg = deps.mo_desktop_config(getattr(agent, "config", None))
    if not isinstance(companion_cfg, dict) or not companion_cfg.get("enabled", False):
        deps.log_event("MO Desktop disabled in config")
        print(
            "MO Desktop is disabled. Set mo_desktop.enabled: true in your MO config.",
            file=sys.stderr,
        )
        return 0

    gateway = deps.Gateway(agent)
    companion = deps.CompanionSurface(
        agent,
        gateway,
        voice_config=companion_cfg.get("voice", {}),
        companion_config=companion_cfg,
    )
    deps.register_everywhere_desktop(getattr(agent, "config", None))
    deps.log_event("starting companion surface", config=getattr(agent, "config", None))
    if not companion.start():
        deps.log_event("companion surface failed to start", config=getattr(agent, "config", None))
        return 1
    heartbeat = None
    scheduler = None
    try:
        desktop_session = companion._ensure_desktop_session()

        @contextmanager
        def _desktop_heartbeat_scope():
            with agent.isolated_session(desktop_session):
                with agent.surface_session_scope("mo-desktop"):
                    yield

        heartbeat = deps.start_heartbeat_service_if_enabled(
            agent,
            gateway,
            surface="mo_desktop",
            scope_factory=_desktop_heartbeat_scope,
        )
    except Exception:
        deps.log_exception("mo-desktop-heartbeat-start-failed")
    try:
        scheduler = deps.start_scheduler_service_if_enabled(agent, gateway)
    except Exception:
        deps.log_exception("mo-desktop-scheduler-start-failed")
    try:
        from core.state.everywhere_coordinator import start_everywhere_coordinator_if_enabled

        everywhere_coordinator = start_everywhere_coordinator_if_enabled(getattr(agent, "config", {}) or {})
    except Exception:
        everywhere_coordinator = None
        deps.log_exception("mo-desktop-everywhere-coordinator-failed")
    setattr(agent, "_companion", companion)
    if args.show:
        companion.summon()
        deps.log_event("initial summon requested", config=getattr(agent, "config", None))

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)
    try:
        while companion._running and not stop_event.wait(0.5):
            request = consume_mo_desktop_summon()
            if isinstance(request, dict):
                from core.design.service import find_design

                try:
                    companion.open_design_studio(
                        str(find_design(request["design_id"], config=agent.config)),
                        terminal_synced=request["terminal_synced"],
                    )
                    deps.log_event("Design focus request consumed", config=agent.config)
                except (FileNotFoundError, OSError, ValueError):
                    deps.log_event("invalid Design focus request ignored", config=agent.config)
            elif request:
                companion.summon()
                deps.log_event("summon request consumed", config=getattr(agent, "config", None))
        return 0
    finally:
        restart_requested = bool(getattr(companion, "restart_requested", False))
        if heartbeat is not None:
            heartbeat.stop()
        if scheduler is not None:
            scheduler.stop()
        if everywhere_coordinator is not None:
            everywhere_coordinator.stop()
        companion.stop()
        release_runtime_lock(runtime_lock)
        deps.log_event("MO Desktop entrypoint stopped", config=getattr(agent, "config", None))
        if restart_requested:
            result = relaunch_mo_desktop_detached(config_path=config_path)
            deps.log_event(result, config=getattr(agent, "config", None))


if __name__ == "__main__":
    raise SystemExit(main())
