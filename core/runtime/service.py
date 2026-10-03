"""Headless MO Agent service runtime.

This is the VPS/daemon entrypoint path. It starts MO-owned runtime surfaces
without launching the TUI: Gateway, Telegram, heartbeat, and scheduler when
enabled.
"""
from __future__ import annotations

import argparse
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from core.agent.agent import create_agent
from core.runtime.backend_monitor import get_monitor, redact_monitor_text
from core.gateway import Gateway
from core.runtime.heartbeat import start_heartbeat_service_if_enabled
from core.runtime.scheduler import (
    scheduler_service_enabled,
    start_scheduler_service_if_enabled,
)
from core.runtime.signals import install_signal_handlers
from core.telegram import start_telegram_gateway_if_enabled


@dataclass(frozen=True)
class ServiceComponentStatus:
    """Value-free startup truth for one optional service component."""

    state: str
    error_type: str = ""

    def as_dict(self) -> dict[str, str]:
        data = {"state": self.state}
        if self.error_type:
            data["error_type"] = self.error_type
        return data


@dataclass
class MoServiceRuntime:
    """Started headless MO Agent components."""

    agent: Any
    gateway: Gateway
    telegram: Any = None
    heartbeat: Any = None
    scheduler: Any = None
    state_sync: Any = None
    everywhere_api: Any = None
    surface: str = "server"
    component_status: dict[str, ServiceComponentStatus] = field(default_factory=dict)

    @property
    def degraded(self) -> bool:
        return any(
            status.state in {"failed", "stopped"}
            for status in self.component_status.values()
        )

    def stop(self) -> None:
        """Stop best-effort background components."""
        if self.everywhere_api and hasattr(self.everywhere_api, "stop"):
            try:
                self.everywhere_api.stop()
            except Exception:
                traceback.print_exc()
        if self.telegram and hasattr(self.telegram, "stop"):
            try:
                self.telegram.stop()
            except Exception:
                traceback.print_exc()
        if self.scheduler and hasattr(self.scheduler, "stop"):
            try:
                self.scheduler.stop()
            except Exception:
                traceback.print_exc()
        if self.heartbeat and hasattr(self.heartbeat, "stop"):
            try:
                self.heartbeat.stop()
            except Exception:
                traceback.print_exc()
        if self.state_sync and hasattr(self.state_sync, "stop"):
            try:
                self.state_sync.stop()
            except Exception:
                traceback.print_exc()
        _emit_service_event(self.gateway, "service_stopped", {"surface": self.surface})


def create_service_runtime(config_path: str | None = None, *, surface: str = "server") -> MoServiceRuntime:
    """Create and start headless MO Agent runtime surfaces."""
    agent = create_agent(config_path)
    gateway = Gateway(agent)
    component_status: dict[str, ServiceComponentStatus] = {}
    telegram, component_status["telegram"] = _start_optional_component(
        gateway,
        "telegram",
        lambda: _start_telegram(agent, gateway),
    )
    heartbeat, component_status["heartbeat"] = _start_optional_component(
        gateway,
        "heartbeat",
        lambda: _start_heartbeat(agent, gateway, surface=surface),
    )
    scheduler, component_status["scheduler"] = _start_optional_component(
        gateway,
        "scheduler",
        lambda: _start_scheduler(agent, gateway),
        expected_to_start=scheduler_service_enabled(agent),
    )
    state_sync, component_status["state_sync"] = _start_optional_component(
        gateway,
        "state_sync",
        lambda: _start_state_sync(agent, gateway),
        expected_to_start=_state_sync_enabled(agent),
    )
    everywhere_api, component_status["everywhere_api"] = _start_optional_component(
        gateway,
        "everywhere_api",
        lambda: _start_everywhere_api(agent, gateway),
    )
    runtime = MoServiceRuntime(
        agent=agent,
        gateway=gateway,
        telegram=telegram,
        heartbeat=heartbeat,
        scheduler=scheduler,
        state_sync=state_sync,
        everywhere_api=everywhere_api,
        surface=surface,
        component_status=component_status,
    )
    _emit_service_event(
        gateway,
        "service_started",
        {
            "surface": surface,
            "state": "degraded" if runtime.degraded else "running",
            "degraded": runtime.degraded,
            "components": {
                name: status.as_dict()
                for name, status in component_status.items()
            },
            "telegram_configured": component_status["telegram"].state != "disabled",
            "telegram_running": _component_running(telegram),
            "heartbeat_running": _component_running(heartbeat),
            "scheduler_running": _component_running(scheduler),
            "state_sync_running": _component_running(state_sync),
            "everywhere_api_running": _component_running(everywhere_api),
        },
    )
    return runtime


def run_service(
    *,
    config_path: str | None = None,
    surface: str = "server",
    stop_event: threading.Event | None = None,
    poll_interval: float = 1.0,
    install_signals: bool = True,
) -> int:
    """Run MO Agent as a headless long-lived service."""
    stop = stop_event or threading.Event()
    if install_signals:
        install_signal_handlers(stop)
    runtime = create_service_runtime(config_path, surface=surface)
    try:
        while not stop.wait(max(0.1, float(poll_interval or 1.0))):
            pass
        return 0
    finally:
        runtime.stop()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run MO Agent headless service surfaces without the TUI.")
    parser.add_argument("--config", default=None, help="Config path, default: ~/.mo/config.yaml (or MO_CONFIG)")
    parser.add_argument("--surface", default="server", help="Heartbeat surface label, default: server")
    args = parser.parse_args(argv)
    return run_service(config_path=args.config, surface=args.surface)


def _start_telegram(agent: Any, gateway: Gateway) -> Any:
    return start_telegram_gateway_if_enabled(agent, gateway)


def _start_heartbeat(agent: Any, gateway: Gateway, *, surface: str) -> Any:
    return start_heartbeat_service_if_enabled(agent, gateway, surface=surface)


def _start_scheduler(agent: Any, gateway: Gateway) -> Any:
    return start_scheduler_service_if_enabled(agent, gateway)


def _start_state_sync(agent: Any, gateway: Gateway) -> Any:
    """Start the one optional Everywhere coordinator lazily."""
    from core.state.everywhere_coordinator import start_everywhere_coordinator_if_enabled

    return start_everywhere_coordinator_if_enabled(getattr(agent, "config", {}) or {})


def _state_sync_enabled(agent: Any) -> bool:
    """Read coordinator startup intent from its canonical configuration owner."""
    from core.state.everywhere_coordinator import everywhere_coordinator_enabled

    return everywhere_coordinator_enabled(getattr(agent, "config", {}) or {})


def _start_everywhere_api(agent: Any, gateway: Gateway) -> Any:
    """Start the optional web surface lazily; FastAPI never enters Agent imports."""
    from mo_everywhere import start_everywhere_api_if_enabled

    return start_everywhere_api_if_enabled(agent, gateway)


def _start_optional_component(
    gateway: Gateway,
    name: str,
    starter: Callable[[], Any],
    *,
    expected_to_start: bool = False,
) -> tuple[Any, ServiceComponentStatus]:
    """Start one optional component without conflating disabled with stopped."""
    try:
        component = starter()
    except Exception as exc:
        error_type = type(exc).__name__
        _emit_service_event(
            gateway,
            f"{name}_start_error",
            {
                "error": redact_monitor_text(exc, 240),
                "error_type": error_type,
            },
        )
        return None, ServiceComponentStatus("failed", error_type)
    if component is None:
        state = "stopped" if expected_to_start else "disabled"
        return None, ServiceComponentStatus(state)
    running = _component_running(component, unknown=None)
    if running is True:
        return component, ServiceComponentStatus("running")
    if running is False:
        return component, ServiceComponentStatus("stopped")
    return component, ServiceComponentStatus("started")


def _component_running(component: Any, *, unknown: bool | None = False) -> bool | None:
    """Return observable thread liveness without inventing it for other owners."""
    if component is None:
        return False
    thread_owner = False
    missing = object()
    for name in ("_poll_thread", "_thread"):
        try:
            thread = getattr(component, name, missing)
        except Exception:
            return False
        if thread is missing:
            continue
        thread_owner = True
        if thread is None:
            continue
        try:
            return bool(thread.is_alive())
        except Exception:
            return False
    return False if thread_owner else unknown


def _emit_service_event(gateway: Any, kind: str, payload: dict[str, Any] | None = None) -> None:
    try:
        monitor = getattr(gateway, "monitor", None) or get_monitor()
        if monitor:
            data = dict(payload or {})
            data["kind"] = kind
            data["component"] = "mo_service"
            data["created_at"] = time.time()
            monitor.emit("session_event", data)
    except Exception:
        traceback.print_exc()
