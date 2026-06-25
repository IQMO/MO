"""Controller and backend boundary for the isolated UX surface."""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, Protocol

from .models import BoardRow, LaneSnapshot, SessionSnapshot, TranscriptItem, demo_snapshot


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


class PreviewBackend:
    """Local-only backend used for UX development and smoke tests."""

    name = "preview"

    def __init__(self, snapshot: SessionSnapshot | None = None) -> None:
        self._base = snapshot or demo_snapshot()
        self._transcript = list(self._base.transcript)
        self._busy = False
        self._notice = self._base.notice

    def snapshot(self) -> SessionSnapshot:
        return replace(
            self._base,
            busy=self._busy,
            notice=self._notice,
            transcript=tuple(self._transcript[-12:]),
            composer_hint="preview only; /exit closes",
        )

    def submit(self, text: str, callbacks: UxCallbacks | None = None) -> str:
        clean = str(text or "").strip()
        if not clean:
            return ""
        self._busy = True
        self._notice = "Preview input captured locally"
        if callbacks:
            callbacks.on_activity("previewing input")
        self._transcript.append(TranscriptItem("user", clean))
        if clean.startswith("/"):
            reply = local_command_response(clean)
        else:
            reply = "Preview only: this message was not sent to the MO runtime. Use --live to run real turns."
        self._transcript.append(TranscriptItem("mo", reply))
        self._busy = False
        self._notice = "Preview only - input was not sent to MO runtime"
        if callbacks:
            callbacks.on_activity("")
        return reply


class RuntimeBackend:
    """Backend that talks to an already-created MO runtime handle."""

    name = "runtime"

    def __init__(self, handle: object) -> None:
        self.handle = handle
        self._notice = "Connected to MO runtime"
        self._busy = False
        self._overlay_transcript: list[TranscriptItem] = []

    def snapshot(self) -> SessionSnapshot:
        snapshot_fn = getattr(self.handle, "snapshot")
        base = snapshot_fn()
        transcript = tuple(list(base.transcript) + self._overlay_transcript[-6:])
        return replace(
            base,
            busy=self._busy,
            notice=self._notice,
            transcript=transcript[-12:],
            composer_hint="live runtime; /exit closes UX",
        )

    def submit(self, text: str, callbacks: UxCallbacks | None = None) -> str:
        clean = str(text or "").strip()
        if not clean:
            return ""
        self._busy = True
        self._notice = "MO runtime turn running"
        self._overlay_transcript.append(TranscriptItem("user", clean))
        try:
            run_turn = getattr(self.handle, "run_turn")
            result = str(run_turn(clean, callbacks=callbacks) or "").strip()
            if result:
                self._overlay_transcript.append(TranscriptItem("mo", result))
            self._notice = "MO runtime turn finished"
            return result
        except Exception as exc:
            message = f"UX runtime error: {type(exc).__name__}: {exc}"
            self._overlay_transcript.append(TranscriptItem("mo", message))
            self._notice = message
            return message
        finally:
            self._busy = False
            if callbacks:
                callbacks.on_activity("")


def local_command_response(text: str) -> str:
    command = str(text or "").strip().lower()
    if command in {"/help", "/h"}:
        return "UX commands: /help, /status, /exit. Runtime slash commands are available only in --live mode."
    if command == "/status":
        return "UX preview status: isolated, local-only, runtime disconnected."
    if command in {"/exit", "/quit", "/q"}:
        return "[EXIT]"
    return f"Unknown UX preview command: {text.split()[0]}"


class UxController:
    """Small app state machine shared by CLI, tests, and future TUI shells."""

    def __init__(self, backend: UxBackend | None = None) -> None:
        self.backend = backend or PreviewBackend()
        self.exit_requested = False
        self.last_result = ""
        self.callbacks = UxCallbacks()

    def snapshot(self) -> SessionSnapshot:
        return self.backend.snapshot()

    def handle_input(self, text: str) -> str:
        clean = str(text or "").strip()
        if clean.lower() in {"/exit", "/quit", "/q", "exit", "quit"}:
            self.exit_requested = True
            self.last_result = "[EXIT]"
            return self.last_result
        self.callbacks = UxCallbacks()
        self.last_result = self.backend.submit(clean, callbacks=self.callbacks)
        if self.last_result == "[EXIT]":
            self.exit_requested = True
        return self.last_result


def read_only_snapshot(handle: object) -> SessionSnapshot:
    """Return a runtime snapshot without creating a runtime backend."""
    snapshot_fn = getattr(handle, "snapshot")
    snapshot = snapshot_fn()
    rows = snapshot.board or (BoardRow("readonly", "No active runtime task board", "pending", kind="read-only"),)
    lanes = snapshot.lanes or (LaneSnapshot("runtime", "ready", "MO runtime loaded", snapshot.model_label),)
    return replace(snapshot, board=rows, lanes=lanes, composer_hint="read-only mode; no messages are sent")
