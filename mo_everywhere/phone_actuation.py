"""Synchronous worker-to-ASGI bridge for origin-bound phone actuation.

API turns run in a bounded worker thread while the authenticated Android host
socket belongs to the FastAPI event loop. This module carries the request's
already-authenticated device principal into that worker and submits semantic
requests back to the owning loop without starting another agent or transport.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator

from .registry import DevicePrincipal, RegistryError


class PhoneActuationError(RuntimeError):
    """Safe tool-facing failure for an unavailable or rejected phone request."""


@dataclass(frozen=True)
class _PhoneContext:
    bridge: "PhoneActuationBridge"
    principal: DevicePrincipal


_CURRENT_PHONE_CONTEXT: ContextVar[_PhoneContext | None] = ContextVar(
    "mo_current_phone_actuation", default=None
)


class PhoneActuationBridge:
    """Submit synchronous tool calls to one bound Live Control event loop."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._broker: Any = None
        self._loop_thread_id = 0
        self._lock = threading.Lock()

    def bind(self, loop: asyncio.AbstractEventLoop, broker: Any) -> None:
        if not loop.is_running():
            raise RuntimeError("phone actuation event loop is not running")
        with self._lock:
            self._loop = loop
            self._broker = broker
            self._loop_thread_id = threading.get_ident()

    def unbind(self) -> None:
        with self._lock:
            self._loop = None
            self._broker = None
            self._loop_thread_id = 0

    @property
    def available(self) -> bool:
        with self._lock:
            return bool(self._loop is not None and self._loop.is_running() and self._broker is not None)

    def request(
        self,
        principal: DevicePrincipal,
        operation: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout_seconds: float = 12.0,
    ) -> dict[str, Any]:
        timeout = max(1.0, min(20.0, float(timeout_seconds)))
        with self._lock:
            loop = self._loop
            broker = self._broker
            loop_thread_id = self._loop_thread_id
        if loop is None or broker is None or not loop.is_running():
            raise PhoneActuationError("phone actuation is unavailable")
        if threading.get_ident() == loop_thread_id:
            raise PhoneActuationError("phone actuation cannot block the hub event loop")
        submitted = asyncio.run_coroutine_threadsafe(
            broker.request_phone(principal, operation, dict(arguments or {}), timeout_seconds=timeout),
            loop,
        )
        try:
            result = submitted.result(timeout=timeout + 1.0)
        except concurrent.futures.TimeoutError:
            submitted.cancel()
            raise PhoneActuationError("phone actuation timed out") from None
        except RegistryError as exc:
            raise PhoneActuationError(str(exc)) from None
        except Exception as exc:
            message = str(exc).strip() or "phone actuation failed"
            raise PhoneActuationError(message[:200]) from None
        if not isinstance(result, dict):
            raise PhoneActuationError("phone actuation response is invalid")
        return result


@contextmanager
def phone_actuation_scope(
    bridge: PhoneActuationBridge,
    principal: DevicePrincipal,
) -> Iterator[None]:
    """Bind one authenticated API phone to tool calls in the current worker."""
    token = _CURRENT_PHONE_CONTEXT.set(_PhoneContext(bridge=bridge, principal=principal))
    try:
        yield
    finally:
        _CURRENT_PHONE_CONTEXT.reset(token)


def request_current_phone(
    operation: str,
    arguments: dict[str, Any] | None = None,
    *,
    timeout_seconds: float = 12.0,
) -> dict[str, Any]:
    """Request the phone that originated the current authenticated API turn."""
    context = _CURRENT_PHONE_CONTEXT.get()
    if context is None:
        raise PhoneActuationError("phone actuation is available only in an authenticated phone turn")
    return context.bridge.request(
        context.principal,
        operation,
        arguments,
        timeout_seconds=timeout_seconds,
    )


def current_phone_principal() -> DevicePrincipal | None:
    context = _CURRENT_PHONE_CONTEXT.get()
    return context.principal if context is not None else None
