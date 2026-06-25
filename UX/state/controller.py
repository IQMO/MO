"""Backend-independent controller for the isolated UX surface."""
from __future__ import annotations

from typing import Callable, Protocol

from .models import SessionSnapshot


class UxBackend(Protocol):
    name: str

    def snapshot(self) -> SessionSnapshot:
        ...

    def submit(self, text: str, callbacks: "UxCallbacks | None" = None) -> str:
        ...


class UxCallbacks:
    """Mutable callback sink used while a real runtime turn is executing."""

    def __init__(self, on_change: Callable[[], None] | None = None) -> None:
        self.activity = ""
        self.assistant_chunks: list[str] = []
        self._on_change = on_change

    def changed(self) -> None:
        if self._on_change:
            self._on_change()

    def on_activity(self, value: str) -> None:
        self.activity = str(value or "").strip()
        self.changed()

    def on_token(self, _token: str) -> None:
        self.activity = "receiving answer"
        self.changed()

    def on_assistant_text(self, text: str) -> None:
        clean = str(text or "").strip()
        if clean:
            self.assistant_chunks.append(clean)
            self.changed()


class UxController:
    """Small app state machine shared by CLI, tests, and future TUI shells."""

    def __init__(self, backend: UxBackend) -> None:
        self.backend = backend
        self.exit_requested = False
        self.last_result = ""
        self.callbacks = UxCallbacks()

    def snapshot(self) -> SessionSnapshot:
        return self.backend.snapshot()

    def handle_input(self, text: str, *, on_change: Callable[[], None] | None = None) -> str:
        clean = str(text or "").strip()
        if clean.lower() in {"/exit", "/quit", "/q", "exit", "quit"}:
            self.exit_requested = True
            self.last_result = "[EXIT]"
            return self.last_result
        self.callbacks = UxCallbacks(on_change=on_change)
        self.last_result = self.backend.submit(clean, callbacks=self.callbacks)
        if self.last_result == "[EXIT]":
            self.exit_requested = True
        return self.last_result
