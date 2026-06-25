"""Runtime and preview backends for the isolated UX controller."""
from __future__ import annotations

from dataclasses import replace

from UX.state.controller import UxCallbacks
from UX.state.models import BoardRow, LaneSnapshot, SessionSnapshot, TranscriptItem, demo_snapshot


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


def read_only_snapshot(handle: object) -> SessionSnapshot:
    """Return a runtime snapshot without creating a runtime backend."""
    snapshot_fn = getattr(handle, "snapshot")
    snapshot = snapshot_fn()
    rows = snapshot.board or (BoardRow("readonly", "Idle - no active runtime task board", "pending", kind="read-only"),)
    lanes = snapshot.lanes or (LaneSnapshot("runtime", "ready", "MO runtime loaded", snapshot.model_label),)
    return replace(snapshot, board=rows, lanes=lanes, composer_hint="read-only mode; no messages are sent")
