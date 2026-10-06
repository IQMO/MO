"""MO terminal workspace state, local PTY ownership, and worker projection."""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .workspace_model import (
    NEW_TERMINAL_DESTINATIONS,
    PaneDestination,
    PaneState,
    WorkspaceModelError,
    WorkspaceState,
)
from .terminal_host import mo_terminal_identity
from .transcript import logical_lines_from_snapshot
from .workspace_pty import LocalTerminalProcess

PANE_WORTHY_KINDS: frozenset[str] = frozenset({"goal", "worker", "prt"})
_KIND_META: dict[str, tuple[str, str]] = {
    "goal": ("Goal", "source-goal"),
    "worker": ("Background", "source-worker"),
    "prt": ("PRT", "source-prt"),
}
STATUS_GLYPHS = {"working": "◐", "blocked": "!", "done": "✓", "idle": "·"}
GENERIC_STYLE_CLASS = "source-generic"
_DONE_STATES = frozenset({"completed"})
_BLOCKED_STATES = frozenset({"blocked"})
_IDLE_STATES = frozenset({"paused", "offered"})
_HEALTH_BASELINE_SECONDS = 1.0
# A local PTY only owns raw keystrokes once it can actually receive them. MO-host
# text is a semantic submit boundary, not a byte stream: those panes retain the
# outer composer and send one complete message on Enter.
_TERMINAL_INPUT_READY_STATES = frozenset({"running", "working", "blocked", "idle"})


# /workspace open N fills one window to at most this many panes (main MO pane included).
_OPEN_PANES_LIMIT = 6

def _adjust_host_terminal_zoom(step: int) -> None:
    """Send one standard font-zoom shortcut to the focused Windows terminal."""
    if os.name != "nt" or step == 0:
        return
    try:
        import ctypes

        user32 = ctypes.windll.user32
        modifiers = [0x11]  # Ctrl
        key = 0xBD  # -
        if step > 0:
            modifiers.append(0x10)  # Shift
            key = 0xBB  # +
        for modifier in modifiers:
            user32.keybd_event(modifier, 0, 0, 0)
        user32.keybd_event(key, 0, 0, 0)
        user32.keybd_event(key, 0, 0x0002, 0)
        for modifier in reversed(modifiers):
            user32.keybd_event(modifier, 0, 0x0002, 0)
    except (AttributeError, OSError):
        return


def _remote_terminal_class():
    """Keep Everywhere and WebSocket imports off ordinary MO startup."""
    from .workspace_remote import RemoteMoTerminalProcess

    return RemoteMoTerminalProcess


def _running_terminal_discovery():
    """Load existing-terminal discovery only when the rail requests it."""
    from .workspace_remote import discover_workspace

    return discover_workspace


@dataclass(frozen=True)
class WorkspaceSource:
    """One canonical worker-registry entry rendered by the worker panel."""

    id: str
    label: str
    kind: str
    status: str
    style_class: str
    detail: str = ""
    claimed_paths: tuple[str, ...] = field(default_factory=tuple)


def activity_enabled(config: Any) -> bool:
    """Return whether the true-worker activity panel is enabled (default off)."""
    if not isinstance(config, dict):
        return False
    interface_cfg = config.get("interface")
    if not isinstance(interface_cfg, dict):
        return False
    activity_cfg = interface_cfg.get("activity")
    return isinstance(activity_cfg, dict) and activity_cfg.get("enabled") is True


def _status_of(state: str) -> str:
    state = str(state or "").strip().lower()
    if state in _DONE_STATES:
        return "done"
    if state in _BLOCKED_STATES:
        return "blocked"
    if state in _IDLE_STATES:
        return "idle"
    return "working"


def _detail_of(record: Any) -> str:
    detail = str(getattr(record, "note", "") or getattr(record, "objective", "") or "").strip()[:120]
    if getattr(record, "kind", "") == "prt":
        from .activity import elapsed_seconds_text
        elapsed = elapsed_seconds_text(getattr(record, "created_at", None))
        if elapsed:
            detail = f"{elapsed} · {detail}" if detail else elapsed
    return detail


def _main_pane_state(tui: Any, agent: Any) -> str:
    if (
        getattr(tui, "busy", False)
        or getattr(tui, "_goal_worker_active", False)
        or getattr(agent, "_goal_active", False)
    ):
        return "working"
    board = getattr(getattr(tui, "gateway", None), "last_task_board", None)
    next_task = getattr(board, "next_ready_task", None)
    try:
        task = next_task() if callable(next_task) else None
    except Exception:
        task = None
    if str(getattr(task, "kind", "") or "").lower() == "ask":
        return "blocked"
    return "idle"


def active_sources(agent: Any) -> list[WorkspaceSource]:
    """Project the Agent's canonical worker registry; never copy worker state."""
    registry = getattr(agent, "workers", None)
    if registry is None:
        return []
    try:
        records = registry.active()
    except Exception:
        return []
    sources: list[WorkspaceSource] = []
    for record in records:
        kind = str(getattr(record, "kind", "") or "").strip().lower()
        if kind not in PANE_WORTHY_KINDS:
            continue
        label, style_class = _KIND_META.get(kind, (kind.title() or "Worker", GENERIC_STYLE_CLASS))
        worker_id = str(getattr(record, "id", "") or "")
        claims = tuple(str(path) for path in (getattr(record, "claimed_paths", None) or ()))
        sources.append(
            WorkspaceSource(
                id=worker_id,
                label=label,
                kind=kind,
                status=_status_of(getattr(record, "state", "")),
                style_class=style_class,
                detail=_detail_of(record),
                claimed_paths=claims,
            )
        )
    return sources


@dataclass(frozen=True)
class WorkspaceTile:
    """One renderable pane; position follows canonical workspace order."""

    position: int
    pane_id: str
    title: str
    destination: PaneDestination
    focused: bool
    state: str
    worker_id: str = ""
    detail: str = ""
    error: str = ""
    lines: tuple[tuple[tuple[str, str], ...], ...] = field(default_factory=tuple)
    project_name: str = ""


@dataclass(frozen=True)
class WorkspaceProject:
    """A rail projection of a local or host profile's project entry."""

    key: str
    path: str
    name: str
    destination: PaneDestination = PaneDestination.THIS_MACHINE


class WorkspaceController:
    """Own one ordered workspace and the local PTYs attached to its panes."""

    def __init__(self, tui: Any) -> None:
        self.tui = tui
        agent = getattr(tui, "agent", None)
        cwd = str(Path(getattr(agent, "project_cwd", "") or os.getcwd()).resolve())
        project_key = "local:" + os.path.normcase(cwd)
        self._projects = {project_key: WorkspaceProject(project_key, cwd, Path(cwd).name or cwd)}
        self._expanded_projects = {project_key}
        self._state = WorkspaceState(PaneState(
            pane_id="main",
            destination=PaneDestination.THIS_MACHINE,
            title="MO",
            lifecycle="running",
            project_key=project_key,
        ))
        self._terminals: dict[str, LocalTerminalProcess] = {}
        self._terminal_notice_phases: dict[str, str] = {}
        self._next_pane_number = 2
        self._launcher_open = False
        self._launcher_index = 0
        self._rail_open = False
        self._rail_index = 0
        self._health_open = False
        self._pane_full_window = False
        self._health_sampler: Any = None
        self._health_cached: Any = None
        self._health_loading = False
        self._health_refresh_pending = False
        self._health_runtime: dict[str, Any] = {}
        self._running_terminals: tuple[Any, ...] = ()
        self._running_terminals_loading = False
        self._running_terminals_error = ""
        self._input_drafts: dict[str, str] = {}
        self._pane_scroll_offsets: dict[str, int] = {}
        self._pane_scroll_limits: dict[str, int] = {}
        self._lock = threading.RLock()
        self._shutting_down = False
        self._render_surface_active = False
        self._load_local_projects()

    def _load_local_projects(self) -> None:
        profile = getattr(getattr(self.tui, "agent", None), "profile", None)
        entries = profile.project_locations() if profile is not None else ()
        available = {}
        for entry in entries:
            path = entry.path
            key = "local:" + (os.path.normcase(path) if path else "profile:" + entry.name.casefold())
            available[key] = WorkspaceProject(key, path, entry.name or Path(path).name or path)
        selected = self._projects[self._state.selected_project]
        if selected.destination is PaneDestination.THIS_MACHINE and not selected.path:
            located = next((project for project in available.values()
                            if project.path and project.name.casefold() == selected.name.casefold()), None)
            if located is not None:
                self._state.select_project(located.key)
                self._expanded_projects.add(located.key)
        retained = {pane.project_key for pane in self._state.all_panes} | {self._state.selected_project}
        self._projects = {key: project for key, project in self._projects.items()
                          if project.destination is PaneDestination.MO_HOST or key in available or key in retained}
        self._projects.update(available)
        self._expanded_projects.intersection_update(self._projects)

    @property
    def projects(self) -> tuple[WorkspaceProject, ...]:
        with self._lock:
            return tuple(self._projects.values())

    @property
    def selected_project(self) -> WorkspaceProject:
        with self._lock:
            return self._projects[self._state.selected_project]

    def project_expanded(self, key: str) -> bool:
        return key in self._expanded_projects

    def select_project(self, key: str) -> str:
        with self._lock:
            project = self._projects.get(key)
            if project is None:
                return "That project is no longer available."
            self._save_input_draft(self._state.focused_pane_id)
            self._state.select_project(key)
            self._expanded_projects.add(key)
            self._pane_full_window = False
            self._health_open = False
            self._restore_input_draft(self._state.focused_pane_id)
            self._set_rail_selection_locked("project", key)
        self.invalidate()
        return f"Selected {project.name} · {project.destination.label}."

    @property
    def active(self) -> bool:
        with self._lock:
            return self._state.split_active or self._rail_open

    @property
    def split_active(self) -> bool:
        with self._lock:
            return self._state.split_active

    @property
    def count(self) -> int:
        with self._lock:
            return self._state.count

    @property
    def mo_instance_count(self) -> int:
        """Count live MO terminals already represented by this workspace."""
        with self._lock:
            terminals = tuple(self._terminals.values())
        return 1 + sum(
            1
            for terminal in terminals
            if bool(getattr(terminal, "alive", False))
            and (
                bool(str(getattr(terminal, "instance_id", "") or ""))
                or mo_terminal_identity(getattr(terminal, "terminal_title", "")) is not None
            )
        )

    @property
    def focused_pane_id(self) -> str:
        with self._lock:
            return self._state.focused_pane_id

    @property
    def focused_position(self) -> int:
        with self._lock:
            pane_ids = self._state.pane_ids
            return pane_ids.index(self._state.focused_pane_id) + 1 if pane_ids else 0

    @property
    def focused_terminal(self) -> LocalTerminalProcess | None:
        with self._lock:
            return self._terminals.get(self._state.focused_pane_id)

    @property
    def rail_open(self) -> bool:
        with self._lock:
            return self._rail_open

    @property
    def rail_index(self) -> int:
        with self._lock:
            return self._rail_index

    @property
    def health_open(self) -> bool:
        with self._lock:
            return self._health_open

    @property
    def pane_full_window(self) -> bool:
        with self._lock:
            return self._pane_full_window

    def _available_running_terminals_locked(self) -> tuple[Any, ...]:
        agent = getattr(self.tui, "agent", None)
        occupied = {str(getattr(agent, "instance_id", "") or "")}
        for terminal in self._terminals.values():
            instance_id = str(getattr(terminal, "instance_id", "") or "")
            if instance_id:
                occupied.add(instance_id)
            identity = mo_terminal_identity(getattr(terminal, "terminal_title", ""))
            if identity:
                occupied.add(identity[0])
        return tuple(
            terminal
            for terminal in self._running_terminals
            if str(getattr(terminal, "instance_id", "") or "") not in occupied
            and getattr(terminal, "connected", True)
        )

    @property
    def running_terminals(self) -> tuple[Any, ...]:
        with self._lock:
            return self._available_running_terminals_locked()

    @property
    def running_terminals_error(self) -> str:
        with self._lock:
            return self._running_terminals_error

    @property
    def health_hosts(self) -> tuple[Any, ...]:
        with self._lock:
            return self._running_terminals

    @property
    def health_runtime(self) -> dict[str, Any]:
        with self._lock:
            return self._health_runtime

    @property
    def local_machine_key(self) -> str:
        return str(getattr(getattr(self.tui, "_live_control_host", None), "machine_key", "") or "")

    def _rail_selection_locked(self) -> tuple[str, str]:
        rows = self._rail_entries_locked()
        return rows[min(self._rail_index, len(rows) - 1)]

    def _rail_entries_locked(self) -> tuple[tuple[str, str], ...]:
        rows = [("new", ""), ("health", "")]
        for project in self._projects.values():
            rows.append(("project", project.key))
            if project.key in self._expanded_projects:
                rows.extend(("pane", pane.pane_id) for pane in self._state.all_panes
                            if pane.project_key == project.key)
        rows.extend(("running", terminal.instance_id) for terminal in self._available_running_terminals_locked())
        return tuple(rows)

    @property
    def rail_entries(self) -> tuple[tuple[str, str], ...]:
        with self._lock:
            return self._rail_entries_locked()

    def _set_rail_selection_locked(self, kind: str, identity: str = "") -> None:
        rows = self._rail_entries_locked()
        target = (kind, "" if kind in {"new", "health"} else identity)
        if kind == "pane" and target not in rows:
            pane = next((pane for pane in self._state.all_panes if pane.pane_id == identity), None)
            if pane is not None:
                target = ("project", pane.project_key)
        self._rail_index = rows.index(target) if target in rows else 0

    def _rail_entry_count_locked(self) -> int:
        return len(self._rail_entries_locked())

    def raw_terminal_active(self) -> bool:
        """True when keystrokes belong directly to the focused local PTY."""
        with self._lock:
            if not self._state.focused_pane_id:
                return False
            pane = next(item for item in self._state.panes if item.pane_id == self._state.focused_pane_id)
            terminal = self._terminals.get(pane.pane_id)
            return (
                not self._rail_open
                and pane.pane_id != "main"
                and pane.destination is PaneDestination.THIS_MACHINE
                and terminal is not None
                and callable(getattr(terminal, "send_input", None))
                and terminal.alive
                and str(getattr(terminal, "state", "")) in _TERMINAL_INPUT_READY_STATES
            )

    def send_raw_input(self, text: str) -> bool:
        """Forward exact interactive input to the focused terminal transport."""
        terminal = self.focused_terminal
        if not self.raw_terminal_active() or terminal is None:
            return False
        sender = getattr(terminal, "send_input", None)
        return bool(sender(text)) if callable(sender) else False

    def _save_input_draft(self, pane_id: str) -> None:
        buffer = getattr(self.tui, "_input_buf", None)
        if buffer is None:
            return
        text = str(getattr(buffer, "text", "") or "")
        if text:
            self._input_drafts[pane_id] = text
        else:
            self._input_drafts.pop(pane_id, None)

    def _restore_input_draft(self, pane_id: str) -> None:
        buffer = getattr(self.tui, "_input_buf", None)
        if buffer is None:
            return
        if pane_id != "main":
            palette = getattr(self.tui, "_palette", None)
            close = getattr(palette, "close", None)
            if callable(close):
                close()
        text = self._input_drafts.get(pane_id, "")
        try:
            buffer.text = text
            buffer.cursor_position = len(text)
        except Exception:
            pass

    def input_draft(self, pane_id: str) -> str:
        """Return the visible draft owned by one pane without changing focus."""
        pane_id = str(pane_id or "")
        with self._lock:
            if pane_id != self._state.focused_pane_id:
                return self._input_drafts.get(pane_id, "")
        buffer = getattr(self.tui, "_input_buf", None)
        return str(getattr(buffer, "text", "") or "")

    def input_placeholder(self) -> str:
        """Describe the exact pane that owns the focused workspace composer."""
        with self._lock:
            if not self._state.focused_pane_id:
                return "Choose New terminal for this project · Ctrl+B projects"
            pane = next(
                item for item in self._state.panes
                if item.pane_id == self._state.focused_pane_id
            )
            position = self._state.pane_ids.index(pane.pane_id) + 1
        if pane.pane_id == "main":
            return "Message MO in pane 1 · F4 commands"
        if pane.destination is PaneDestination.MO_HOST:
            return f"Send to pane {position} · MO host"
        return f"Run a command in pane {position} · This machine"

    def pane_scroll_offset(self, pane_id: str) -> int:
        """Return one pane's distance from its latest visible output row."""
        with self._lock:
            return self._pane_scroll_offsets.get(str(pane_id or ""), 0)

    def set_pane_scroll_limit(self, pane_id: str, maximum: int) -> None:
        """Update a rendered pane's scroll bound without owning layout math here."""
        pane_id = str(pane_id or "")
        limit = max(0, int(maximum))
        with self._lock:
            self._pane_scroll_limits[pane_id] = limit
            current = min(self._pane_scroll_offsets.get(pane_id, 0), limit)
            if current:
                self._pane_scroll_offsets[pane_id] = current
            else:
                self._pane_scroll_offsets.pop(pane_id, None)

    def scroll_focused(self, delta: int) -> bool:
        """Scroll the focused pane without stealing ordinary arrow keys."""
        with self._lock:
            if self._health_open:
                current = self._pane_scroll_offsets.get("health", 0)
                limit = self._pane_scroll_limits.get("health", 0)
                self._pane_scroll_offsets["health"] = max(0, min(limit, current - int(delta)))
                self.invalidate()
                return True
            if self._rail_open or not self._state.split_active or not self._state.focused_pane_id:
                return False
            pane = next(
                item for item in self._state.panes
                if item.pane_id == self._state.focused_pane_id
            )
            pane_id = pane.pane_id
            terminal = self._terminals.get(pane_id)

        if pane.destination is PaneDestination.THIS_MACHINE and terminal is not None:
            scroll_view = getattr(terminal, "scroll_view", None)
            if callable(scroll_view) and scroll_view(delta):
                self.invalidate()
                return True

        with self._lock:
            limit = self._pane_scroll_limits.get(pane_id, 0)
            current = self._pane_scroll_offsets.get(pane_id, 0)
            updated = max(0, min(limit, current + int(delta)))
            if updated:
                self._pane_scroll_offsets[pane_id] = updated
            else:
                self._pane_scroll_offsets.pop(pane_id, None)
        self.invalidate()
        return True

    def scroll_focused_to_edge(self, *, oldest: bool) -> bool:
        """Move the focused pane to its oldest or latest available output."""
        with self._lock:
            if self._health_open:
                limit = self._pane_scroll_limits.get("health", 0)
                return self.scroll_focused(limit if oldest else -limit)
            if self._rail_open or not self._state.split_active or not self._state.focused_pane_id:
                return False
            pane = next(
                item for item in self._state.panes
                if item.pane_id == self._state.focused_pane_id
            )
            pane_id = pane.pane_id
            terminal = self._terminals.get(pane_id)

        if pane.destination is PaneDestination.THIS_MACHINE and terminal is not None:
            scroll_view_to_edge = getattr(terminal, "scroll_view_to_edge", None)
            if callable(scroll_view_to_edge) and scroll_view_to_edge(oldest=oldest):
                self.invalidate()
                return True

        with self._lock:
            limit = self._pane_scroll_limits.get(pane_id, 0)
            if oldest and limit:
                self._pane_scroll_offsets[pane_id] = limit
            else:
                self._pane_scroll_offsets.pop(pane_id, None)
        self.invalidate()
        return True

    def _flush_scrollback_if_inactive(self) -> None:
        if self.active:
            return
        self.tui._flush_background_updates()

    @property
    def launcher_open(self) -> bool:
        with self._lock:
            return self._launcher_open

    @property
    def launcher_index(self) -> int:
        with self._lock:
            return self._launcher_index

    @property
    def launcher_destinations(self):
        return NEW_TERMINAL_DESTINATIONS

    def _main_lines(self) -> tuple[tuple[tuple[str, str], ...], ...]:
        try:
            raw_lines = list(self.tui._logical_transcript_lines())
        except Exception:
            raw_lines = []
        # The normal terminal landing masthead is useful before split mode, but
        # the workspace already gives the main pane a compact role title while
        # its footer retains runtime metadata. Repeating the four logo rows
        # inside that tile wastes scarce vertical space and makes the title read
        # as a duplicated header. Keep later continuity/auth/task notices because
        # they remain actionable.
        if len(raw_lines) >= 4 and all(
            isinstance(line, (list, tuple))
            and any(
                isinstance(fragment, (list, tuple))
                and len(fragment) >= 2
                and str(fragment[0] or "") == "class:logo"
                for fragment in line
            )
            for line in raw_lines[:4]
        ):
            raw_lines = raw_lines[4:]
        raw_lines = raw_lines[-240:]
        lines: list[tuple[tuple[str, str], ...]] = []
        for raw_line in raw_lines:
            fragments: list[tuple[str, str]] = []
            for fragment in raw_line if isinstance(raw_line, (list, tuple)) else ():
                if isinstance(fragment, (list, tuple)) and len(fragment) >= 2:
                    fragments.append((
                        str(fragment[0] or "")[:160],
                        str(fragment[1] or "")[:4_000],
                    ))
            lines.append(tuple(fragments))
        live_fragments: list[tuple[str, str]] = []
        foreground_work = bool(
            getattr(self.tui, "busy", False)
            or (
                getattr(self.tui, "_goal_worker_active", False)
                and not getattr(self.tui, "_goal_backgrounded", False)
            )
        )
        if foreground_work:
            render_activity = getattr(self.tui, "_get_activity_fragments", None)
            if callable(render_activity):
                live_fragments.extend(render_activity() or [])
        visible_goal = getattr(self.tui, "_visible_goal_board_text", None)
        goal_text = str(visible_goal() or "") if callable(visible_goal) else ""
        if goal_text:
            render_goal = getattr(self.tui, "_get_goal_board_fragments", None)
            if callable(render_goal):
                if any(text for _style, text in live_fragments):
                    live_fragments.append(("", "\n"))
                live_fragments.extend(render_goal() or [])
        foreground_goal = bool(
            getattr(self.tui, "_goal_worker_active", False)
            and not getattr(self.tui, "_goal_backgrounded", False)
        )
        if str(getattr(self.tui, "board_text", "") or "") and not foreground_goal:
            render_board = getattr(self.tui, "_get_board_fragments", None)
            if callable(render_board):
                if any(text for _style, text in live_fragments):
                    live_fragments.append(("", "\n"))
                live_fragments.extend(render_board() or [])
        live_lines = logical_lines_from_snapshot(tuple(live_fragments)) if live_fragments else []
        while live_lines and not live_lines[-1]:
            live_lines.pop()
        if live_lines:
            if lines and lines[-1]:
                lines.append(())
            lines.extend(tuple(tuple(fragment) for fragment in line) for line in live_lines)
        return tuple(lines)

    def tiles(self, *, all_projects: bool = False, include_output: bool = True) -> list[WorkspaceTile]:
        agent = getattr(self.tui, "agent", None)
        with self._lock:
            source_panes = self._state.all_panes if all_projects else self._state.panes
            panes = {pane.pane_id: pane for pane in source_panes}
            pane_ids = tuple(panes)
            focused = self._state.focused_pane_id
            terminals = dict(self._terminals)
        output: list[WorkspaceTile] = []
        positions: dict[str, int] = {}
        for pane_id in pane_ids:
            pane = panes[pane_id]
            index = positions[pane.project_key] = positions.get(pane.project_key, 0) + 1
            project_name = self._projects[pane.project_key].name
            if pane_id == "main":
                main_identity = mo_terminal_identity(
                    getattr(self.tui, "_terminal_title_text", "")
                )
                main_detail = (
                    main_identity[1]
                    if main_identity and " · " in main_identity[1]
                    else str(getattr(agent, "model", "") or "")
                )
                output.append(WorkspaceTile(
                    position=index,
                    pane_id=pane_id,
                    title="MO",
                    destination=pane.destination,
                    focused=pane_id == focused,
                    state=_main_pane_state(self.tui, agent),
                    worker_id=str(getattr(agent, "instance_id", "") or "")[:64],
                    detail=main_detail[:80],
                    lines=self._main_lines() if include_output else (),
                    project_name=project_name,
                ))
                continue
            terminal = terminals.get(pane_id)
            if terminal is None or not include_output:
                lines = ()
            elif hasattr(terminal, "scrollback_fragments"):
                lines = terminal.scrollback_fragments()
            elif hasattr(terminal, "screen_fragments"):
                lines = terminal.screen_fragments()
            else:
                lines = tuple(
                    (("class:workspace-terminal", line),)
                    for line in terminal.screen_lines()
                )
            identity = mo_terminal_identity(getattr(terminal, "terminal_title", ""))
            terminal_state = str(getattr(terminal, "state", "error") or "error")
            if identity:
                worker_id, model, busy = identity
                title = "MO"
                detail = model
                if terminal_state not in {"blocked", "error", "exited"}:
                    terminal_state = "working" if busy else "idle"
            else:
                worker_id = str(getattr(terminal, "instance_id", "") or "")
                title = "MO" if pane.destination is PaneDestination.MO_HOST else pane.title
                detail = str(getattr(terminal, "cwd_label", pane.cwd_label) or "")
                if pane.destination is PaneDestination.MO_HOST and detail == f"MO Terminal · {worker_id}":
                    detail = ""
            output.append(WorkspaceTile(
                position=index,
                pane_id=pane_id,
                title=title,
                destination=pane.destination,
                focused=pane_id == focused,
                state=terminal_state,
                worker_id=worker_id,
                detail=detail[:80],
                error=str(getattr(terminal, "error", "") or "")[:300],
                lines=lines,
                project_name=project_name,
            ))
        return output

    def health_snapshot(self):
        """Return the last snapshot and start only an explicitly requested read."""
        with self._lock:
            if (not self._health_open or self._shutting_down or self._health_loading
                    or not self._health_refresh_pending):
                return self._health_cached
            self._health_refresh_pending = False
            self._health_loading = True
            terminals = dict(self._terminals)

        def collect() -> None:
            from core.runtime.resources import ResourceSampler, ResourceSnapshot

            try:
                if self._health_sampler is None:
                    self._health_sampler = ResourceSampler()
                roots = {"main": os.getpid()}
                for pane_id, terminal in terminals.items():
                    try:
                        pid = getattr(terminal, "root_pid", None)
                        roots[pane_id] = int(pid) if pid else None
                    except (TypeError, ValueError):
                        roots[pane_id] = None
                snapshot = self._health_sampler.sample(roots)
                # CPU percentages require two readings. Complete that baseline in
                # this bounded worker so an open Health view does not need a timer.
                if getattr(snapshot, "state", "") == "warming":
                    time.sleep(_HEALTH_BASELINE_SECONDS)
                    snapshot = self._health_sampler.sample(roots)
                with self._lock:
                    self._health_cached = snapshot
                agent = getattr(self.tui, "agent", None)
                read_status = getattr(agent, "health_status", None)
                if callable(read_status):
                    try:
                        rows = read_status()
                    except Exception:
                        rows = (("Runtime", "Observation unavailable"),)
                    with self._lock:
                        self._health_runtime = {"observed_at": time.monotonic(), "rows": rows}
            except Exception:
                with self._lock:
                    self._health_cached = ResourceSnapshot(
                        time.monotonic(), None, None, None, None, {}, "local", "unavailable",
                    )
            finally:
                with self._lock:
                    self._health_loading = False
                self.invalidate()

        threading.Thread(target=collect, daemon=True, name="mo-workspace-health").start()
        with self._lock:
            return self._health_cached

    def _begin_running_terminal_discovery(self) -> None:
        agent = getattr(self.tui, "agent", None)
        config = getattr(agent, "config", None)
        everywhere = config.get("consistent_everywhere") if isinstance(config, dict) else None
        continuity = everywhere.get("continuity") if isinstance(everywhere, dict) else None
        enabled = bool(
            isinstance(everywhere, dict)
            and everywhere.get("enabled") is True
            and (not isinstance(continuity, dict) or continuity.get("enabled") is not False)
        )
        with self._lock:
            if not enabled or self._running_terminals_loading or self._shutting_down:
                if not enabled:
                    self._running_terminals = ()
                    self._running_terminals_error = ""
                return
            self._running_terminals_loading = True

        def discover() -> None:
            discovery = None
            try:
                discover_terminals = _running_terminal_discovery()
                discovery = discover_terminals(config, include_resources=True)
                terminals = discovery.terminals
                projects = discovery.projects
                error = discovery.project_error
            except Exception as exc:
                terminals = ()
                projects = ()
                error = f"Could not load running MO terminals: {type(exc).__name__}."
            with self._lock:
                if self._shutting_down:
                    return
                selection = self._rail_selection_locked()
                for project in projects:
                    path = project["path"]
                    key = "host:" + path
                    self._projects[key] = WorkspaceProject(key, path, project["name"], PaneDestination.MO_HOST)
                if discovery is not None:
                    seen = {terminal.instance_id for terminal in terminals}
                    attached = {str(getattr(terminal, "instance_id", "") or "")
                                for terminal in self._terminals.values()}
                    missing = tuple(replace(terminal, connected=False)
                                    for terminal in self._running_terminals
                                    if terminal.instance_id not in seen and terminal.instance_id in attached)
                    self._running_terminals = terminals + missing
                self._running_terminals_loading = False
                self._running_terminals_error = error
                if self._rail_open and not self._launcher_open:
                    self._set_rail_selection_locked(*selection)
            self.invalidate()

        threading.Thread(
            target=discover,
            daemon=True,
            name="mo-workspace-terminal-discovery",
        ).start()

    def open_rail(self) -> str:
        """Open the side rail on the current terminal or Health destination."""
        with self._lock:
            self._load_local_projects()
            self._rail_open = True
            self._launcher_open = False
            if self._health_open:
                self._health_refresh_pending = True
            self._set_rail_selection_locked(
                "health" if self._health_open else "pane" if self._state.focused_pane_id else "project",
                self._state.focused_pane_id or self._state.selected_project,
            )
        self._begin_running_terminal_discovery()
        self.invalidate()
        return "Terminal panel opened."

    def rail_move(self, delta: int) -> None:
        with self._lock:
            if not self._rail_open:
                return
            if self._launcher_open:
                self._launcher_index = (
                    self._launcher_index + int(delta)
                ) % len(NEW_TERMINAL_DESTINATIONS)
            else:
                self._rail_index = (
                    self._rail_index + int(delta)
                ) % self._rail_entry_count_locked()
        self.invalidate()

    def open_health(self) -> str:
        """Keep the rail visible and replace only the terminal grid with Health."""
        with self._lock:
            self._rail_open = True
            self._launcher_open = False
            self._health_open = True
            self._health_refresh_pending = True
            self._set_rail_selection_locked("health")
        self.invalidate()
        return "Health: live system and per-terminal resources."

    def rail_accept(self) -> str:
        with self._lock:
            if not self._rail_open:
                return "Workspace side rail is not open."
            launcher_open = self._launcher_open
            selection = self._rail_selection_locked()
        if launcher_open:
            return self.launcher_accept()
        kind, identity = selection
        if kind == "new":
            return self.open_launcher()
        if kind == "health":
            return self.open_health()
        if kind == "running":
            return self.attach_running_terminal(identity)
        if kind == "project":
            with self._lock:
                collapse = identity == self._state.selected_project and identity in self._expanded_projects
            result = self.select_project(identity)
            if collapse:
                with self._lock:
                    self._expanded_projects.discard(identity)
                self.invalidate()
            return result
        result = self.focus_pane(identity)
        with self._lock:
            self._rail_open = False
            self._health_open = False
        self.invalidate()
        return result

    def rail_close_selected(self) -> str:
        with self._lock:
            if not self._rail_open or self._launcher_open:
                return "Select a terminal before closing it."
            kind, pane_id = self._rail_selection_locked()
            if kind == "running":
                return "Attach the running MO terminal before closing it."
            if kind == "new":
                return "The new-terminal action cannot be closed."
            if kind == "health":
                return "The Health view cannot be closed."
            if kind == "project":
                return "Select a terminal inside the project before closing it."
            if pane_id == "main":
                return "The main MO pane cannot be closed."
            self.focus_pane(pane_id)
            position = self.focused_position
        result = self.close(position)
        with self._lock:
            self._rail_index = min(self._rail_index, self._rail_entry_count_locked() - 1)
            self._rail_open = self._state.split_active
        self.invalidate()
        return result

    def rail_cancel(self) -> bool:
        with self._lock:
            if not self._rail_open:
                return False
            if self._launcher_open:
                self._launcher_open = False
                self._launcher_index = 0
            else:
                self._rail_open = False
                self._health_open = False
        self._flush_scrollback_if_inactive()
        self.invalidate()
        return True

    def open_launcher(self) -> str:
        with self._lock:
            self._rail_open = True
            self._set_rail_selection_locked("new")
            self._launcher_open = True
            self._launcher_index = NEW_TERMINAL_DESTINATIONS.index(self.selected_project.destination)
            self._health_open = False
        self.invalidate()
        return "New terminal: choose This machine or MO host."

    def _project_for_destination_locked(self, destination: PaneDestination) -> WorkspaceProject | None:
        """The project a new terminal at ``destination`` opens in: the same-named project there,
        else (on this machine) the folder MO started in, else the first one listed."""
        candidates = [project for project in self._projects.values()
                      if project.destination is destination and project.path]
        if not candidates:
            return None
        selected = self._projects[self._state.selected_project].name.casefold()
        same = next((project for project in candidates if project.name.casefold() == selected), None)
        if same is not None:
            return same
        if destination is PaneDestination.THIS_MACHINE:
            started = str(Path(getattr(getattr(self.tui, "agent", None), "project_cwd", "") or os.getcwd()).resolve())
            home = next((project for project in candidates
                         if os.path.normcase(project.path) == os.path.normcase(started)), None)
            if home is not None:
                return home
        return candidates[0]

    def launcher_move(self, delta: int) -> None:
        self.rail_move(delta)

    def launcher_cancel(self) -> bool:
        return self.rail_cancel()

    def launcher_accept(self) -> str:
        with self._lock:
            if not self._launcher_open:
                return "New-terminal launcher is not open."
            destination = NEW_TERMINAL_DESTINATIONS[self._launcher_index]
            self._launcher_open = False
            if destination is not self.selected_project.destination:
                # Choosing where is enough: open there in the matching project, never a detour.
                project = self._project_for_destination_locked(destination)
                if project is None:
                    self.invalidate()
                    return ("MO host has no project yet; it appears once the host connects."
                            if destination is PaneDestination.MO_HOST
                            else "No project on this machine has a recorded folder yet.")
                self.select_project(project.key)
            self._rail_open = False
        result = self.new_terminal(destination)
        self._flush_scrollback_if_inactive()
        return result

    def new_terminal(
        self,
        destination: PaneDestination | str | None = None,
        *,
        attached_instance_id: str = "",
        attached_label: str = "",
    ) -> str:
        try:
            if destination is None:
                destination = self.selected_project.destination
            destination = (
                destination
                if isinstance(destination, PaneDestination)
                else PaneDestination(str(destination).lower())
            )
        except ValueError:
            return self.usage()
        is_host = destination is PaneDestination.MO_HOST
        attached_instance_id = str(attached_instance_id or "").strip()
        attached_label = str(attached_label or "").strip()
        if attached_instance_id and not is_host:
            return "A running MO terminal can only attach through the MO host destination."
        if attached_instance_id:
            with self._lock:
                discovered = next((item for item in self._running_terminals
                                   if item.instance_id == attached_instance_id), None)
                path = str(getattr(discovered, "project_path", "") or "")
                key = "host:" + path if path and "host:" + path in self._projects else "host:attached"
                # Keep unknown project identity explicit. Only the serving
                # host's own catalog can associate an existing terminal.
                self._projects.setdefault(key, WorkspaceProject(key, "", "Attached MO terminals", PaneDestination.MO_HOST))
            self.select_project(key)
        elif destination is not self.selected_project.destination:
            return f"Select a {destination.label} project in the rail before opening its terminal."
        elif is_host and not self.selected_project.path:
            return "Select a host project before starting a new terminal."
        elif not is_host and not self.selected_project.path:
            return "This project's folder is not recorded in your profile yet."
        elif not is_host and not Path(self.selected_project.path).is_dir():
            return "This project directory no longer exists; choose another project."
        try:
            terminal_class = _remote_terminal_class() if is_host else LocalTerminalProcess
        except Exception as exc:
            self.invalidate()
            return f"Could not load MO host terminal transport: {type(exc).__name__}."
        previous_pane_id = self.focused_pane_id
        self._save_input_draft(previous_pane_id)
        with self._lock:
            pane_id = f"{'host' if is_host else 'local'}-{self._next_pane_number}"
            self._next_pane_number += 1
            agent = getattr(self.tui, "agent", None)
            project = self.selected_project
            self._expanded_projects.add(project.key)
            cwd = project.path
            cwd_label = "MO host" if is_host else (Path(cwd).name if cwd else "")
            try:
                self._state.add_blank_terminal(
                    pane_id=pane_id,
                    destination=destination,
                    cwd_label=cwd_label,
                )
                if self._health_open:
                    self._set_rail_selection_locked("health")
            except WorkspaceModelError as exc:
                return str(exc)
            if is_host:
                terminal = terminal_class(
                    pane_id=pane_id,
                    config=getattr(agent, "config", None),
                    on_change=lambda: self._terminal_changed(pane_id),
                    attached_instance_id=attached_instance_id,
                    attached_label=attached_label,
                    project_path=project.path,
                )
            else:
                terminal = terminal_class(
                    pane_id=pane_id,
                    cwd=cwd,
                    on_change=self.invalidate,
                )
            self._terminals[pane_id] = terminal
            if is_host:
                self._terminal_notice_phases[pane_id] = "starting"
            opened_position = self._state.pane_ids.index(pane_id) + 1
        self._restore_input_draft(pane_id)
        try:
            terminal.start()
        except Exception as exc:
            startup_error = terminal.error or f"{type(exc).__name__}: {exc}"
            try:
                terminal.close()
            except Exception:
                pass
            with self._lock:
                self._terminals.pop(pane_id, None)
                self._terminal_notice_phases.pop(pane_id, None)
                try:
                    self._state.close(pane_id)
                except WorkspaceModelError:
                    pass
                if self._health_open:
                    self._set_rail_selection_locked("health")
                restored_pane_id = self._state.focused_pane_id
            self._input_drafts.pop(pane_id, None)
            self._restore_input_draft(restored_pane_id)
            self.invalidate()
            self._flush_scrollback_if_inactive()
            return f"Could not open workspace pane {opened_position}: {startup_error}"
        if not previous_pane_id:
            pending_draft = self._input_drafts.pop("", "")
            if pending_draft:
                self._input_drafts[pane_id] = pending_draft
                self._restore_input_draft(pane_id)
        _adjust_host_terminal_zoom(-1)
        self.invalidate()
        if attached_instance_id:
            label = attached_label or "running MO terminal"
            return f"Attaching workspace pane {opened_position} · {label}."
        verb = "Opening" if is_host else "Opened"
        return f"{verb} workspace pane {opened_position} · {destination.label}."

    def open_panes(self, total: int, where: str = "local") -> str:
        """Fill this window to ``total`` panes (the main MO pane counts as one), each a new
        terminal on this machine or on the MO host. Stops with the reason if one cannot open."""
        total = max(1, min(_OPEN_PANES_LIMIT, int(total)))
        host = str(where or "").lower() in {"host", "mo-host", "server"}
        destination = PaneDestination.MO_HOST if host else PaneDestination.THIS_MACHINE
        if host and self.selected_project.destination is not PaneDestination.MO_HOST:
            with self._lock:
                key = next((key for key, project in self._projects.items()
                            if project.destination is PaneDestination.MO_HOST and project.path), "")
            if not key:
                return "No MO host project is listed yet; open the side panel (Ctrl+B) once the host connects."
            self.select_project(key)
        while self.count < total:
            before = self.count
            result = self.new_terminal(destination)
            if self.count == before:
                return result
        return self.status_text()

    def attach_running_terminal(self, instance_id: str) -> str:
        """Attach one discovered live MO terminal as a normal host pane."""
        instance_id = str(instance_id or "").strip()
        with self._lock:
            terminal = next(
                (
                    item
                    for item in self._available_running_terminals_locked()
                    if item.instance_id == instance_id
                ),
                None,
            )
            if terminal is None:
                return "That running MO terminal is no longer available."
            self._rail_open = False
            self._launcher_open = False
            self._health_open = False
        return self.new_terminal(
            PaneDestination.MO_HOST,
            attached_instance_id=terminal.instance_id,
            attached_label=terminal.label,
        )

    def focus_pane(self, pane_id: str) -> str:
        previous_pane_id = self.focused_pane_id
        self._save_input_draft(previous_pane_id)
        with self._lock:
            try:
                self._state.focus(str(pane_id))
            except WorkspaceModelError as exc:
                return str(exc)
            focused_pane_id = self._state.focused_pane_id
            self._expanded_projects.add(self._state.selected_project)
            position = self._state.pane_ids.index(focused_pane_id) + 1
            self._health_open = False
        self._restore_input_draft(focused_pane_id)
        self.invalidate()
        return f"Focused workspace pane {position}."

    def focus(self, position: int) -> str:
        previous_pane_id = self.focused_pane_id
        self._save_input_draft(previous_pane_id)
        with self._lock:
            try:
                self._state.focus_position(int(position))
            except WorkspaceModelError as exc:
                return str(exc)
            focused_pane_id = self._state.focused_pane_id
            self._health_open = False
        self._restore_input_draft(focused_pane_id)
        self.invalidate()
        return f"Focused workspace pane {position}."

    def move_focus(self, delta: int) -> str:
        previous_pane_id = self.focused_pane_id
        self._save_input_draft(previous_pane_id)
        with self._lock:
            self._state.move_focus(int(delta))
            focused_pane_id = self._state.focused_pane_id
            position = self.focused_position
            self._health_open = False
        self._restore_input_draft(focused_pane_id)
        self.invalidate()
        return f"Focused workspace pane {position}."

    def toggle_pane_full_window(self) -> str:
        """Toggle the focused pane between the grid and the full workspace."""
        with self._lock:
            if not self._state.split_active or not self._state.focused_pane_id:
                return "Open another terminal before using full-window mode."
            self._pane_full_window = not self._pane_full_window
            full_window = self._pane_full_window
            position = self._state.pane_ids.index(self._state.focused_pane_id) + 1
        self.invalidate()
        if full_window:
            return f"Pane {position} fills the workspace · Alt+F restore"
        return "Workspace grid restored."

    def route_input(self, text: str) -> bool:
        if not self.focused_pane_id:
            self._input_drafts[""] = str(text or "")
            self._restore_input_draft("")
            self._notice("Choose New terminal in the project rail first; your draft is preserved.")
            return True
        terminal = self.focused_terminal
        if terminal is None:
            return False
        value = str(text or "")
        pane_id = self.focused_pane_id
        if not terminal.send_line(value):
            # prompt_toolkit has already accepted (and therefore cleared) the
            # composer by the time routing reaches us. Preserve the exact
            # message when a starting or unavailable pane cannot take it yet.
            self._input_drafts[pane_id] = value
            self._restore_input_draft(pane_id)
            self._notice(terminal.error or "Focused terminal is unavailable.")
        else:
            self._input_drafts.pop(pane_id, None)
            self._pane_scroll_offsets.pop(pane_id, None)
        self.invalidate()
        return True

    def cancel_focused(self) -> bool:
        terminal = self.focused_terminal
        if terminal is None:
            return False
        sent = terminal.send_interrupt()
        self.invalidate()
        return sent

    def resize_pane(self, pane_id: str, *, columns: int, rows: int) -> None:
        with self._lock:
            terminal = self._terminals.get(str(pane_id))
        if terminal is not None:
            terminal.resize(columns=columns, rows=rows)

    def close(self, position: int | None = None) -> str:
        previous_pane_id = self.focused_pane_id
        self._save_input_draft(previous_pane_id)
        with self._lock:
            target = self.focused_position if position is None else int(position)
            pane_ids = self._state.pane_ids
            if target < 1 or target > len(pane_ids):
                return f"Workspace pane must be between 1 and {len(pane_ids)}."
            pane_id = pane_ids[target - 1]
            if pane_id == "main":
                return "Pane 1 owns this MO window; close its terminal panes to leave split mode."
            closed_focused = pane_id == self._state.focused_pane_id
            try:
                removed = self._state.close(pane_id)
            except WorkspaceModelError as exc:
                return str(exc)
            if closed_focused or not self._state.split_active:
                self._pane_full_window = False
            terminal = self._terminals.pop(pane_id, None)
            self._terminal_notice_phases.pop(pane_id, None)
            if self._health_open:
                self._set_rail_selection_locked("health")
            focused_pane_id = self._state.focused_pane_id
        self._input_drafts.pop(pane_id, None)
        self._pane_scroll_offsets.pop(pane_id, None)
        self._pane_scroll_limits.pop(pane_id, None)
        self._restore_input_draft(focused_pane_id)
        if terminal is not None:
            # Pane closure is a lifecycle boundary: do not report the pane as
            # closed while its exact shell can still own files or the working
            # directory.  LocalTerminalProcess.close() is already bounded and
            # force-closes only this PTY when a graceful exit does not finish.
            terminal.close()
            _adjust_host_terminal_zoom(1)
        self._flush_scrollback_if_inactive()
        self.invalidate()
        return f"Closed workspace pane {target} · {removed.destination.label}."

    def single(self) -> str:
        with self._lock:
            pane_ids = [pane_id for pane_id in self._state.pane_ids if pane_id != "main"]
        for pane_id in pane_ids:
            with self._lock:
                position = self._state.pane_ids.index(pane_id) + 1
            self.close(position)
        self.focus_pane("main")
        return "Project terminals closed; returned to the main MO pane."

    def shutdown(self) -> None:
        with self._lock:
            if self._shutting_down:
                return
            self._shutting_down = True
            terminals = list(self._terminals.values())
            self._terminals.clear()
            self._terminal_notice_phases.clear()
            self._pane_scroll_offsets.clear()
            self._pane_scroll_limits.clear()
            self._launcher_open = False
            self._rail_open = False
            self._health_open = False
            self._pane_full_window = False
            self._health_sampler = None
        for terminal in terminals:
            terminal.close(timeout=0.35)
            _adjust_host_terminal_zoom(1)

    def command(self, rest: str) -> str:
        parts = str(rest or "").strip().split()
        if not parts:
            return self.status_text() if self.split_active else self.new_terminal()
        action = parts[0].lower()
        if action in {"status", "list"} and len(parts) == 1:
            return self.status_text()
        if action == "new":
            if len(parts) == 1:
                return self.open_launcher()
            if len(parts) == 2 and parts[1].lower() in {"local", "this-machine", "machine"}:
                return self.new_terminal(PaneDestination.THIS_MACHINE)
            if len(parts) == 2 and parts[1].lower() in {"host", "mo-host"}:
                return self.new_terminal(PaneDestination.MO_HOST)
            return self.usage()
        if action == "open" and len(parts) in {2, 3} and parts[1].isdigit():
            return self.open_panes(int(parts[1]), parts[2].lower() if len(parts) == 3 else "local")
        if action in {"next", "prev"} and len(parts) == 1:
            return self.move_focus(1 if action == "next" else -1)
        if action == "focus" and len(parts) == 2 and parts[1].isdigit():
            return self.focus(int(parts[1]))
        if action == "close" and len(parts) in {1, 2}:
            if len(parts) == 2 and not parts[1].isdigit():
                return self.usage()
            return self.close(int(parts[1]) if len(parts) == 2 else None)
        if action in {"single", "collapse"} and len(parts) == 1:
            return self.single()
        return self.usage()

    def status_text(self) -> str:
        rows = [
            f"Workspace: {self.count} panes · {self.selected_project.name} · focused {self.focused_position}"
        ]
        for tile in self.tiles():
            marker = "*" if tile.focused else " "
            owner = "MO" if tile.pane_id == "main" else tile.destination.label
            detail = tile.error or tile.detail
            rows.append(
                f"  {marker} pane {tile.position} · {owner} · {tile.state}"
                + (f" · {detail}" if detail else "")
            )
        return "\n".join(rows)

    @staticmethod
    def usage() -> str:
        return (
            "Usage: /workspace [new [local|host]|status|next|prev|focus N|"
            "close [N]|single]"
        )

    def invalidate(self) -> None:
        # The full TUI owns its prompt-toolkit Application as ``_app``. Keep the
        # public-name fallback for narrow harnesses and compatibility adapters.
        app = getattr(self.tui, "_app", None) or getattr(self.tui, "app", None)
        with self._lock:
            surface_active = self._state.split_active or self._rail_open
            surface_changed = surface_active != getattr(
                self, "_render_surface_active", False
            )
            self._render_surface_active = surface_active
        if app is None:
            return

        if not surface_changed:
            self.tui._request_background_redraw()
            return

        def repaint() -> None:
            try:
                if surface_changed:
                    renderer = getattr(app, "renderer", None)
                    if renderer is not None:
                        base_full_screen = bool(
                            getattr(self.tui, "_terminal_full_screen", False)
                        )
                        target_full_screen = surface_active or base_full_screen
                        buffer_changed = bool(renderer.full_screen) != target_full_screen
                        # Workspace owns a full-height surface. Enter the
                        # alternate buffer only for that surface. Erase the
                        # inline frame while its cursor offset is still known,
                        # so the alternate buffer saves the clean main anchor.
                        app.full_screen = target_full_screen
                        renderer.full_screen = target_full_screen
                        if buffer_changed and target_full_screen:
                            renderer.erase(leave_alternate_screen=False)
                        elif buffer_changed:
                            renderer.reset()
                            # The restored cursor is already at the inline
                            # origin. Measure the space below it; clearing or
                            # assuming full height destroys native transcript
                            # rows and can scroll stale chrome into history.
                            renderer.request_absolute_cursor_position()
                    # Alternate/main-buffer transitions can recreate host-owned
                    # cells. Reuse the TUI's one OSC/background owner at this exact
                    # boundary; its periodic call remains only a missed-event backstop.
                    sync_background = getattr(self.tui, "_sync_terminal_background", None)
                    if callable(sync_background):
                        sync_background(force=True)
                app.invalidate()
            except Exception:
                pass

        loop = getattr(app, "loop", None)
        if surface_changed and loop is not None:
            try:
                loop.call_soon_threadsafe(repaint)
                return
            except Exception:
                pass
        repaint()

    def _terminal_changed(self, pane_id: str) -> None:
        """Replace the launch notice when an asynchronous host start resolves."""
        notice = ""
        with self._lock:
            terminal = self._terminals.get(str(pane_id))
            if terminal is not None:
                state = str(getattr(terminal, "state", "") or "")
                phase = self._terminal_notice_phases.get(str(pane_id))
                pane = next((pane for pane in self._state.all_panes if pane.pane_id == pane_id), None)
                if pane_id in self._state.pane_ids:
                    label = f"workspace pane {self._state.pane_ids.index(pane_id) + 1}"
                else:
                    project = self._projects.get(pane.project_key) if pane is not None else None
                    label = f"terminal in {project.name}" if project is not None else f"terminal {pane_id}"
                if state == "error" and phase != "failed":
                    self._terminal_notice_phases[str(pane_id)] = "failed"
                    error = str(getattr(terminal, "error", "") or "MO host is unavailable")
                    notice = f"Could not open {label}: {error}"
                elif phase == "starting" and state in {"running", "idle", "working", "blocked"}:
                    self._terminal_notice_phases[str(pane_id)] = "opened"
                    notice = f"Opened {label} · MO host."
        if notice:
            setter = getattr(self.tui, "_set_notice", None)
            if callable(setter):
                setter(notice)
        self.invalidate()

    def _notice(self, text: str) -> None:
        setter = getattr(self.tui, "_set_notice", None)
        if callable(setter):
            setter(str(text or ""))
