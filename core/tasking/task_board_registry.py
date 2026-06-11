"""Compatibility taskboard registry and event log.

This is intentionally small and in-memory. It does not replace
``Gateway.last_task_board``; it gives future registry/event-store work a tested
MO-native seam while existing consumers continue using the live Gateway board.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .task_board import TaskBoard, board_update_event


@dataclass
class TaskBoardRegistry:
    """Track boards by surface plus recent structured update events."""

    boards: dict[str, TaskBoard] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    def set_board(self, surface: str, board: TaskBoard | None) -> TaskBoard | None:
        key = _surface_key(surface)
        if board is None:
            self.boards.pop(key, None)
            return None
        self.boards[key] = board
        return board

    def get_board(self, surface: str = "main") -> TaskBoard | None:
        return self.boards.get(_surface_key(surface))

    def clear_board(self, surface: str = "main") -> None:
        self.boards.pop(_surface_key(surface), None)

    def record_event(self, surface: str, board: TaskBoard, *, update: str = "updated", event: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(event or board_update_event(board, update=update))
        payload.setdefault("type", "taskboard_update")
        payload["surface"] = _surface_key(surface)
        payload["ts"] = float(payload.get("ts") or time.time())
        self.events.append(payload)
        if len(self.events) > 200:
            self.events = self.events[-200:]
        return payload

    def recent_events(self, *, surface: str = "", limit: int = 20) -> list[dict[str, Any]]:
        rows = self.events
        if surface:
            key = _surface_key(surface)
            rows = [event for event in rows if event.get("surface") == key]
        return list(rows[-max(0, int(limit or 0)):])


def _surface_key(surface: str) -> str:
    return str(surface or "main").strip().lower() or "main"
