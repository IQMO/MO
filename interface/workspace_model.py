"""Canonical state for MO's local and MO-host terminal workspace.

This module owns ordered pane identity and the selected project's focus.
Rendering derives one responsive grid from this order; process and stream
transports plug into the model later. Commands are never launcher data because
every added pane is an ordinary blank terminal.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

_PANE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")


class WorkspaceModelError(ValueError):
    """The requested workspace state transition is invalid."""


class PaneDestination(str, Enum):
    THIS_MACHINE = "local"
    MO_HOST = "host"

    @property
    def label(self) -> str:
        return "This machine" if self is PaneDestination.THIS_MACHINE else "MO host"


NEW_TERMINAL_DESTINATIONS = (
    PaneDestination.THIS_MACHINE,
    PaneDestination.MO_HOST,
)


@dataclass(frozen=True)
class PaneState:
    pane_id: str
    destination: PaneDestination
    title: str
    cwd_label: str = ""
    lifecycle: str = "starting"
    project_key: str = ""

    def __post_init__(self) -> None:
        if not _PANE_ID_RE.fullmatch(str(self.pane_id or "")):
            raise WorkspaceModelError("pane id is invalid")
        if not isinstance(self.destination, PaneDestination):
            raise WorkspaceModelError("pane destination is invalid")

class WorkspaceState:
    """One pane owner with independent project views and remembered focus."""

    def __init__(self, first_pane: PaneState) -> None:
        self._panes: dict[str, PaneState] = {first_pane.pane_id: first_pane}
        self._pane_ids: list[str] = [first_pane.pane_id]
        self._focused_pane_id = first_pane.pane_id
        self._main_pane_id = first_pane.pane_id
        self._selected_project = first_pane.project_key
        self._project_focus: dict[str, str] = {}

    @property
    def selected_project(self) -> str:
        return self._selected_project

    @property
    def all_panes(self) -> tuple[PaneState, ...]:
        return tuple(self._panes[pane_id] for pane_id in self._pane_ids)

    def select_project(self, project_key: str) -> None:
        self._project_focus[self._selected_project] = self._focused_pane_id
        self._selected_project = str(project_key)
        pane_ids = self.pane_ids
        remembered = self._project_focus.get(self._selected_project, "")
        self._focused_pane_id = remembered if remembered in pane_ids else next(iter(pane_ids), "")

    @property
    def panes(self) -> tuple[PaneState, ...]:
        return tuple(self._panes[pane_id] for pane_id in self.pane_ids)

    @property
    def pane_ids(self) -> tuple[str, ...]:
        return tuple(pane_id for pane_id in self._pane_ids
                     if self._panes[pane_id].project_key == self._selected_project)

    @property
    def focused_pane_id(self) -> str:
        return self._focused_pane_id

    @property
    def split_active(self) -> bool:
        # A single child or an empty selected project still owns the workspace
        # surface; it must never route input to an invisible main MO session.
        return self.pane_ids != (self._main_pane_id,)

    @property
    def count(self) -> int:
        return len(self._panes)

    def add_blank_terminal(
        self,
        *,
        pane_id: str,
        destination: PaneDestination,
        cwd_label: str = "",
    ) -> PaneState:
        """Insert a blank terminal in the selected project after its focus."""
        if pane_id in self._panes:
            raise WorkspaceModelError("pane id already exists")
        pane = PaneState(
            pane_id=pane_id,
            destination=destination,
            title="Terminal",
            cwd_label=str(cwd_label or "")[:160],
            project_key=self._selected_project,
        )
        focused_index = (self._pane_ids.index(self._focused_pane_id)
                         if self._focused_pane_id else len(self._pane_ids) - 1)
        self._panes[pane.pane_id] = pane
        self._pane_ids.insert(focused_index + 1, pane.pane_id)
        self._focused_pane_id = pane.pane_id
        return pane

    def focus(self, pane_id: str) -> None:
        if pane_id not in self._panes:
            raise WorkspaceModelError("pane does not exist")
        if self._panes[pane_id].project_key != self._selected_project:
            self.select_project(self._panes[pane_id].project_key)
        self._focused_pane_id = pane_id

    def focus_position(self, position: int) -> None:
        pane_ids = self.pane_ids
        if position < 1 or position > len(pane_ids):
            raise WorkspaceModelError(f"pane position must be between 1 and {len(pane_ids)}")
        self._focused_pane_id = pane_ids[position - 1]

    def move_focus(self, delta: int) -> None:
        pane_ids = self.pane_ids
        if not pane_ids:
            return
        current = pane_ids.index(self._focused_pane_id)
        self._focused_pane_id = pane_ids[(current + int(delta)) % len(pane_ids)]

    def close(self, pane_id: str) -> PaneState:
        if pane_id == self._main_pane_id:
            raise WorkspaceModelError("the main MO pane cannot be closed")
        if pane_id not in self._panes:
            raise WorkspaceModelError("pane does not exist")
        visible = self.pane_ids
        removed_index = visible.index(pane_id) if pane_id in visible else 0
        removed = self._panes.pop(pane_id)
        self._pane_ids.remove(pane_id)
        if self._focused_pane_id == pane_id:
            remaining = self.pane_ids
            self._focused_pane_id = remaining[min(removed_index, len(remaining) - 1)] if remaining else ""
        return removed
