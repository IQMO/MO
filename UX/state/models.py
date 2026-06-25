"""Pure display models for the isolated UX surface.

These classes describe what the interface can render.  They do not execute
tools, advance tasks, write runtime state, or decide whether work is complete.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

VALID_TASK_STATUSES = frozenset({"pending", "active", "completed", "blocked"})


def normalize_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    return status if status in VALID_TASK_STATUSES else "pending"


@dataclass(frozen=True)
class BoardRow:
    id: str
    title: str
    status: str = "pending"
    blocker: str = ""
    kind: str = ""

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BoardRow":
        return cls(
            id=str(value.get("id", "") or ""),
            title=str(value.get("title", "") or value.get("text", "") or "").strip(),
            status=normalize_status(value.get("status")),
            blocker=str(value.get("blocker", "") or "").strip(),
            kind=str(value.get("kind", "") or "").strip(),
        )


@dataclass(frozen=True)
class LaneSnapshot:
    name: str
    status: str
    detail: str = ""
    model: str = ""


@dataclass(frozen=True)
class TranscriptItem:
    speaker: str
    text: str
    style: str = ""


@dataclass(frozen=True)
class SessionSnapshot:
    product: str = "MO Agent"
    project: str = ""
    runtime: str = ""
    provider: str = ""
    model: str = ""
    busy: bool = False
    notice: str = ""
    lanes: tuple[LaneSnapshot, ...] = field(default_factory=tuple)
    board: tuple[BoardRow, ...] = field(default_factory=tuple)
    transcript: tuple[TranscriptItem, ...] = field(default_factory=tuple)
    composer_placeholder: str = "Type a message"
    composer_hint: str = "enter to submit"

    @property
    def model_label(self) -> str:
        if self.provider and self.model:
            return f"{self.provider} / {self.model}"
        return self.provider or self.model or "model not configured"


def demo_snapshot() -> SessionSnapshot:
    """Return a realistic static snapshot for visual and regression checks."""
    return SessionSnapshot(
        project="E:\\MO-clean",
        runtime="isolated UX preview",
        provider="opencode",
        model="deepseek-v4-pro",
        busy=True,
        notice="Preview only - not wired to runtime task ownership",
        lanes=(
            LaneSnapshot("thinking", "ready", "scope, risks, next action", "flash"),
            LaneSnapshot("execution", "running", "gateway turn in progress", "pro"),
            LaneSnapshot("compaction", "idle", "watching context pressure", "local"),
        ),
        board=(
            BoardRow("1", "Inspect interface contracts", "completed", kind="inspect"),
            BoardRow("2", "Design isolated UX package", "active", kind="design"),
            BoardRow("3", "Verify no current interface imports", "pending", kind="verify"),
        ),
        transcript=(
            TranscriptItem("user", "Build the next interface in a new isolated UX folder."),
            TranscriptItem(
                "mo",
                "New UX is isolated: model, render, controller, adapter. Runtime truth stays outside display code.",
            ),
        ),
        composer_hint="preview only; /exit closes",
    )
