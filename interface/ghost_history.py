"""Ghost side-panel history helpers for the MO TUI."""
from __future__ import annotations

import time
import traceback
from typing import Any

from core.ghost.ghost_audit import append_ghost_audit

GHOST_MODE_HINT = "Ghost panel — Alt+G hides/shows; Ctrl+O expands"


def ghost_history_item_lines(item: dict[str, Any]) -> list[tuple[str, str]]:
    user_text = str(item.get("user") or "").strip()
    response_text = str(item.get("response") or "").strip()
    kind = str(item.get("kind") or "reply")
    lines: list[tuple[str, str]] = []
    if user_text:
        lines.append(("class:ghost-user", user_text))
    if response_text:
        style = "class:ghost-hint" if kind == "notification" else "class:ghost-response"
        lines.append((style, response_text))
    return lines


def ghost_history_panel_lines(history: list[dict[str, Any]]) -> list[tuple[str, str]]:
    if not history:
        return [("class:ghost-hint", GHOST_MODE_HINT)]
    lines: list[tuple[str, str]] = []
    for item in history[-6:]:
        lines.extend(ghost_history_item_lines(item))
    return lines or [("class:ghost-hint", GHOST_MODE_HINT)]


class GhostHistoryMixin:
    def _record_ghost_history(self, kind: str, user_text: str, response_text: str, route: str = ""):
        item = {
            "kind": str(kind or "reply"),
            "user": str(user_text or ""),
            "response": str(response_text or ""),
            "route": str(route or ""),
            "ts": time.time(),
        }
        self._ghost_history.append(item)
        self._ghost_history = self._ghost_history[-20:]
        self._ghost_history_index = len(self._ghost_history) - 1
        append_ghost_audit(kind, user_text=user_text, response_text=response_text, route=route)
        try:
            from core.backend_monitor import get_monitor

            monitor = get_monitor()
            if monitor:
                monitor.emit("ghost_event", {
                    "kind": item["kind"],
                    "route": item["route"],
                    "user_preview": item["user"][:240],
                    "response_preview": item["response"][:420],
                    "response_chars": len(item["response"]),
                })
        except Exception:
            traceback.print_exc()

    def _ghost_history_panel_lines(self) -> list[tuple[str, str]]:
        lines = ghost_history_panel_lines(self._ghost_history)
        prt_sugg = getattr(self.agent, "_prt_ghost_suggestion", None)
        if prt_sugg:
            lines.append(("class:ghost-hint", f"PRT Review recommended for recent commit ({prt_sugg}). Type /prt to review."))
        return lines

    @staticmethod
    def _ghost_history_item_lines(item: dict[str, Any]) -> list[tuple[str, str]]:
        return ghost_history_item_lines(item)

    def _show_ghost_history(self, delta: int) -> bool:
        if not self._ghost_history:
            return False
        if self._ghost_history_index is None:
            self._ghost_history_index = len(self._ghost_history) - 1
        else:
            self._ghost_history_index = max(0, min(len(self._ghost_history) - 1, self._ghost_history_index + delta))
        item = self._ghost_history[self._ghost_history_index]
        self._ghost_panel_lines = self._ghost_history_item_lines(item) or [("class:ghost-hint", "No visible Ghost text for this entry")]
        self._ghost_scroll_from_bottom = 0
        self._ghost_panel_open = True
        self._ghost_unread_count = 0
        if self._app:
            self._app.invalidate()
        return True
