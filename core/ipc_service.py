"""Expose a live Gateway over the local IPC transport (warm-daemon Phase 2).

``make_gateway_handler`` turns an :class:`~core.ipc.IpcServer` request into a
Gateway turn: it streams the turn's tokens / activity / board updates / proposal
back to the client as IPC events and returns the final answer text as the terminal
response. ``serve_gateway`` starts an IpcServer carrying that handler.

This is the SERVER half of the warm daemon. The client (TUI) half lands in Phase 3;
until then the only consumers are tests and a manual ``IpcClient``. Exposing the
Gateway over IPC is OFF unless explicitly requested (``mo_service --warm``), so the
existing headless service path is byte-for-byte unchanged.

Concurrency: a handler call runs ``Gateway.run_turn`` synchronously on its
connection thread, and run_turn already serializes turns on the shared agent via
its own lock. Phase 2 targets a single client; multi-client sessions are Phase 4.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from .ipc import IpcClient, IpcError, IpcServer

if TYPE_CHECKING:
    from .gateway import Gateway


def make_gateway_handler(gateway: "Gateway") -> Callable[[dict, Callable[[dict], None]], "dict | None"]:
    """Return an IpcServer handler that runs one Gateway turn per ``run_turn`` request."""

    def handler(request: dict, emit: Callable[[dict], None]) -> "dict | None":
        kind = request.get("type")
        if kind == "ping":
            session = getattr(gateway.agent, "session", None)
            return {
                "pong": True,
                "session_id": str(getattr(session, "session_id", "") or ""),
                "provider": str(getattr(gateway.agent, "provider_name", "") or ""),
                "model": str(getattr(gateway.agent, "model", "") or ""),
            }
        if kind == "run_turn":
            user_input = str(request.get("input", ""))
            route_source = str(request.get("route_source", "user") or "user")
            text = gateway.run_turn(
                user_input,
                on_token=lambda t: emit({"kind": "token", "text": str(t)}),
                on_activity=lambda a: emit({"kind": "activity", "text": str(a)}),
                on_board_update=lambda rich: emit({"kind": "board", "rich": str(rich)}),
                on_proposal=lambda p: emit({"kind": "proposal", "text": str(p)}),
                route_source=route_source,
            )
            return {"text": str(text or "")}
        raise ValueError(f"unknown request type: {kind!r}")

    return handler


def serve_gateway(gateway: "Gateway", *, name: str = "mo", mo_home_path: str | None = None) -> IpcServer:
    """Start (and return) an IpcServer exposing ``gateway`` over local IPC."""
    return IpcServer(make_gateway_handler(gateway), name=name, mo_home_path=mo_home_path).start()


def request_turn(
    prompt: str,
    *,
    route_source: str = "user",
    on_event: Callable[[dict], None] | None = None,
    name: str = "mo",
    mo_home_path: str | None = None,
    timeout: float = 5.0,
) -> str:
    """Drive ONE turn against a warm daemon over IPC and return its final text.

    This is the CLIENT half used by the non-interactive one-shot (``mo -p``). It
    raises :class:`~core.ipc.IpcUnavailable` when no daemon is reachable, so the
    caller can fall back to in-process; it raises :class:`~core.ipc.IpcError` if the
    daemon reports a turn error. ``on_event`` receives each streamed event frame
    (token / activity / board / proposal) for optional progress display.
    """
    client = IpcClient.connect(name=name, mo_home_path=mo_home_path, timeout=timeout)
    try:
        final = ""
        for frame in client.request({"type": "run_turn", "input": str(prompt), "route_source": route_source}):
            ftype = frame.get("type")
            if ftype == "event":
                if on_event:
                    on_event(frame)
            elif ftype == "response":
                result = frame.get("result")
                final = str(result.get("text", "")) if isinstance(result, dict) else ""
            elif ftype == "error":
                raise IpcError(str(frame.get("message", "daemon turn error")))
        return final
    finally:
        client.close()
