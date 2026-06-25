"""Headless MO Agent service runtime.

This is the VPS/daemon entrypoint path. It starts MO-owned runtime surfaces
without launching the TUI: Gateway, Telegram when enabled, and heartbeat. Future
MO-native scheduler startup belongs here once implemented.
"""
from __future__ import annotations

import argparse
import signal
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any

from core.agent.agent import create_agent
from core.backend_monitor import get_monitor, redact_monitor_text
from core.gateway import Gateway
from core.heartbeat import start_heartbeat_service_if_enabled
from core.ipc_service import serve_gateway
from core.scheduler import start_scheduler_service_if_enabled
from core.telegram import start_telegram_gateway_if_enabled


@dataclass
class MoServiceRuntime:
    """Started headless MO Agent components."""

    agent: Any
    gateway: Gateway
    telegram: Any = None
    heartbeat: Any = None
    scheduler: Any = None
    ipc: Any = None
    surface: str = "server"

    def stop(self) -> None:
        """Stop best-effort background components."""
        if self.ipc and hasattr(self.ipc, "stop"):
            try:
                self.ipc.stop()
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
        _emit_service_event(self.gateway, "service_stopped", {"surface": self.surface})


def create_service_runtime(config_path: str | None = None, *, surface: str = "server", warm: bool = False) -> MoServiceRuntime:
    """Create and start headless MO Agent runtime surfaces.

    ``warm`` additionally exposes the Gateway over local IPC (warm-daemon mode) so a
    thin client can drive turns against this already-constructed agent. Default off:
    the existing VPS/systemd service path is unchanged.
    """
    agent = create_agent(config_path)
    gateway = Gateway(agent)
    telegram = _start_telegram(agent, gateway)
    heartbeat = _start_heartbeat(agent, gateway, surface=surface)
    scheduler = _start_scheduler(agent, gateway)
    ipc = _start_ipc(gateway) if warm else None
    runtime = MoServiceRuntime(agent=agent, gateway=gateway, telegram=telegram, heartbeat=heartbeat, scheduler=scheduler, ipc=ipc, surface=surface)
    _emit_service_event(
        gateway,
        "service_started",
        {
            "surface": surface,
            "telegram_configured": telegram is not None,
            "telegram_running": bool(getattr(telegram, "_poll_thread", None) and telegram._poll_thread.is_alive()) if telegram else False,
            "heartbeat_running": bool(getattr(heartbeat, "_thread", None) and heartbeat._thread.is_alive()) if heartbeat else False,
            "scheduler_running": bool(getattr(scheduler, "_thread", None) and scheduler._thread.is_alive()) if scheduler else False,
            "ipc_running": ipc is not None,
        },
    )
    return runtime


def run_service(
    *,
    config_path: str | None = None,
    surface: str = "server",
    warm: bool = False,
    stop_event: threading.Event | None = None,
    poll_interval: float = 1.0,
    install_signals: bool = True,
) -> int:
    """Run MO Agent as a headless long-lived service."""
    stop = stop_event or threading.Event()
    if install_signals:
        install_signal_handlers(stop)
    runtime = create_service_runtime(config_path, surface=surface, warm=warm)
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
    parser.add_argument("--warm", action="store_true", help="Expose the Gateway over local IPC for a thin client (warm-daemon mode)")
    args = parser.parse_args(argv)
    return run_service(config_path=args.config, surface=args.surface, warm=args.warm)


def _start_telegram(agent: Any, gateway: Gateway) -> Any:
    try:
        return start_telegram_gateway_if_enabled(agent, gateway)
    except Exception as exc:
        _emit_service_event(gateway, "telegram_start_error", {"error": redact_monitor_text(exc, 240), "error_type": type(exc).__name__})
        return None


def _start_heartbeat(agent: Any, gateway: Gateway, *, surface: str) -> Any:
    try:
        return start_heartbeat_service_if_enabled(agent, gateway, surface=surface)
    except Exception as exc:
        _emit_service_event(gateway, "heartbeat_start_error", {"error": redact_monitor_text(exc, 240), "error_type": type(exc).__name__})
        return None


def _start_scheduler(agent: Any, gateway: Gateway) -> Any:
    try:
        return start_scheduler_service_if_enabled(agent, gateway)
    except Exception as exc:
        _emit_service_event(gateway, "scheduler_start_error", {"error": redact_monitor_text(exc, 240), "error_type": type(exc).__name__})
        return None


def _start_ipc(gateway: Gateway) -> Any:
    try:
        return serve_gateway(gateway)
    except Exception as exc:
        _emit_service_event(gateway, "ipc_start_error", {"error": redact_monitor_text(exc, 240), "error_type": type(exc).__name__})
        return None


def install_signal_handlers(stop_event: threading.Event) -> None:
    """Install SIGINT/SIGTERM handlers when running in the main thread."""
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
