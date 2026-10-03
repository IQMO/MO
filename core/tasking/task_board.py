"""MO task board state model.

TaskBoard stores normalized rows, metadata gates, dependency ordering, evidence
notes, and durable snapshots. Gateway owns board lifecycle; Agent/tool runtime
advances rows through metadata-aware gates; interface code only renders the
already-decided state.

Operations flow:
  Gateway/model structured rows → Board.set_rows()
  Agent/tool runtime            → Board.complete()/activate()/block()
  TUI/monitor/handoff           → read/render Board state only.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import traceback

from ..runtime.lock import file_byte_lock
from ..utils.atomic_write import atomic_write_text
from ..utils.jsonl_utils import read_recent_ledger_entries, resolve_ledger_path
from ..state.paths import ENV_TASKBOARD_LEDGER_DISABLE, ENV_TASKBOARD_LEDGER_PATH, TASKBOARD_LEDGER_PATH

STATUSES = {"pending", "active", "completed", "blocked", "cancelled"}
OPEN = {"pending", "active", "blocked"}
KIND_VALUES = {"", "inspect", "edit", "execute", "verify", "report", "ask"}
COMPLETION_GATES = {"", "tool", "verification", "manual"}
PLAN_OWNERS = {"", "model", "procedure", "extension", "goal"}

# D003 visible status symbols — the single source of truth for every surface
# (TUI board, goal views, secondary context, telegram render). Do not redefine.
STATUS_MARKERS = {"completed": "√", "active": "→", "blocked": "!", "pending": "□", "cancelled": "×"}

TASKBOARD_LEDGER_MAX_LINES = 2_000
TASKBOARD_LEDGER_MAX_BYTES = 8_000_000
_TASKBOARD_THREAD_LOCK = threading.Lock()


@contextmanager
def _taskboard_file_lock(ledger_path: Path):
    """Serialize ledger/current writes across local MO processes."""
    lock_path = ledger_path.with_name(ledger_path.name + ".lock")
    with file_byte_lock(lock_path, _TASKBOARD_THREAD_LOCK):
        yield


def _prune_taskboard_ledger(
    ledger_path: Path,
    *,
    max_lines: int = TASKBOARD_LEDGER_MAX_LINES,
    max_bytes: int = TASKBOARD_LEDGER_MAX_BYTES,
) -> None:
    """Bound taskboard history while preserving the newest complete JSONL rows."""
    try:
        if ledger_path.stat().st_size <= max_bytes:
            return
        lines = ledger_path.read_text(encoding="utf-8", errors="replace").splitlines()
        kept = lines[-max(1, int(max_lines or 1)):]
        while len(("\n".join(kept) + "\n").encode("utf-8")) > max_bytes and len(kept) > 1:
            kept.pop(0)
        atomic_write_text(ledger_path, "\n".join(kept) + "\n", encoding="utf-8")
    except Exception:
        return


def status_marker(status: str) -> str:
    """Return the D003 checklist symbol for a task status."""
    return STATUS_MARKERS.get(str(status or "").strip().lower(), STATUS_MARKERS["pending"])


def attach_taskboard_to_text(owner: Any, text: str) -> str:
    """Append compact taskboard render to text if the owner has a board with tasks.

    Shared by secondary text surfaces so they show the same evidence-gated task
    truth.
    """
    text = str(text or "")
    try:
        board = getattr(owner, "last_task_board", None)
        if board is None or not getattr(board, "tasks", None):
            return text
        rendered = str(board.render() or "").strip()
        if not rendered or rendered in text:
            return text
        return f"{text}\n\n{rendered}" if text else rendered
    except Exception:
        return text


@dataclass
class TaskItem:
    """A single task row in the board."""

    id: str
    title: str
    status: str = "pending"
    evidence: list[str] = field(default_factory=list)
    blocker: str = ""
    kind: str = ""
    completion_gate: str = ""
    depends_on: list[str] = field(default_factory=list)
    parent_id: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    expected_evidence: list[str] = field(default_factory=list)
    test_strategy: str = ""

    def __post_init__(self) -> None:
        self.status = _normalize_status(self.status)
        self.title = str(self.title or "").strip() or "Continue the work"
        self.evidence = _normalize_text_list(self.evidence)
        self.kind = _normalize_kind(self.kind)
        self.completion_gate = _normalize_completion_gate(self.completion_gate)
        self.depends_on = _normalize_text_list(self.depends_on)
        self.parent_id = str(self.parent_id or "").strip()
        self.acceptance_criteria = _normalize_text_list(self.acceptance_criteria)
        self.expected_evidence = _normalize_text_list(self.expected_evidence)
        self.test_strategy = str(self.test_strategy or "").strip()

    @property
    def is_open(self) -> bool:
        return self.status in OPEN


@dataclass
class TaskBoardContractResult:
    """Diagnostic taskboard contract result; it does not mutate board truth."""

    ok: bool
    reasons: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"ok": bool(self.ok), "reasons": list(self.reasons), "summary": dict(self.summary)}


@dataclass
class TaskCompletionResult:
    """Result of an attempted task completion state transition."""

    ok: bool
    task_id: str
    reason: str = ""

    def __bool__(self) -> bool:
        return bool(self.ok)


@dataclass
class TaskBoard:
    """Task board container. Stores state; rendering/consumers do not judge."""

    turn_id: str = ""
    title: str = "MO AGENT is working"
    tasks: list[TaskItem] = field(default_factory=list)
    objective: str = ""
    board_id: str = ""
    session_id: str = ""
    source: str = "gateway"
    state: str = "active"
    created_at: float = 0.0
    updated_at: float = 0.0
    # Added last to preserve the positional constructor contract for callers
    # that predate explicit plan ownership.
    plan_owner: str = ""
    # Goal-owned acceptance coverage is carried by the same live board that
    # owns task progress.  Ordinary turn boards leave both lists empty.
    required_acceptance_criteria: list[str] = field(default_factory=list)
    required_verification_criteria: list[str] = field(default_factory=list)
    # Typed, bounded action state belongs to the board lifecycle. It must never
    # be reconstructed from task titles, objectives, or conversation wording.
    pending_action: dict[str, str] = field(default_factory=dict)
    # Tool evidence belongs to the board lifecycle as well as the row that was
    # active when it was observed. Plan revision may replace an unfinished row,
    # but it must not make already-performed work disappear.
    gathered_evidence: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        now = time.time()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = self.created_at
        self.board_id = str(self.board_id or (f"board-{self.turn_id}" if self.turn_id else f"board-{uuid.uuid4().hex[:8]}"))
        self.session_id = str(self.session_id or "")
        self.source = str(self.source or "gateway")
        self.plan_owner = _normalize_plan_owner(self.plan_owner)
        self.required_acceptance_criteria = _normalize_text_list(
            self.required_acceptance_criteria
        )
        self.required_verification_criteria = _normalize_text_list(
            self.required_verification_criteria
        )
        self.pending_action = normalize_pending_action(self.pending_action)
        self.state = _normalize_board_state(self.state)
        self.tasks = _coerce_work_rows(list(self.tasks or []))
        self.gathered_evidence = _normalize_text_list([
            *list(self.gathered_evidence or []),
            *(item for task in self.tasks for item in task.evidence),
        ])
        self._ensure_one_active()
        if self.state not in {"abandoned", "paused"}:
            self.state = _state_for_board(self)

    # ── Read ──────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        """D1 fix: shared summary dict consumed by render_plain and render_rich.

        Returns a single common intermediate representation so the primary
        render paths don't re-derive the same fields independently.
        ``compile_board_context`` and ``board_update_event`` retain their own
        specialized formats and may adopt summary() in the future.
        """
        tasks_data: list[dict[str, Any]] = []
        for task in self.tasks:
            tasks_data.append({
                "id": task.id,
                "title": task.title,
                "status": task.status,
                "kind": task.kind,
                "completion_gate": task.completion_gate,
                "evidence": list(task.evidence),
                "blocker": task.blocker,
                "depends_on": list(task.depends_on),
                "parent_id": task.parent_id,
                "acceptance_criteria": list(task.acceptance_criteria),
                "expected_evidence": list(task.expected_evidence),
                "test_strategy": task.test_strategy,
                "is_open": task.is_open,
            })
        return {
            "board_id": self.board_id,
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "title": self.title,
            "objective": self.objective,
            "source": self.source,
            "plan_owner": self.plan_owner,
            "required_acceptance_criteria": list(self.required_acceptance_criteria),
            "required_verification_criteria": list(self.required_verification_criteria),
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "total": len(self.tasks),
            "done": self.done_count(),
            "open": self.open_count(),
            "cancelled": sum(task.status == "cancelled" for task in self.tasks),
            "active_task_id": self.active_task_id(),
            "ready_task_id": self.first_ready_pending_id(),
            "tasks": tasks_data,
        }

    def done_count(self) -> int:
        return sum(1 for t in self.tasks if t.status == "completed")

    def open_count(self) -> int:
        return sum(1 for t in self.tasks if t.is_open)

    def task(self, task_id: str) -> TaskItem:
        for t in self.tasks:
            if t.id == str(task_id):
                return t
        raise KeyError(f"unknown task id: {task_id}")

    def active_task_id(self) -> str | None:
        for t in self.tasks:
            if t.status == "active":
                return t.id
        return None

    def dependencies_satisfied(self, task_id: str) -> bool:
        row = self._get(task_id)
        if not row:
            return False
        deps = set(row.depends_on)
        if not deps:
            return True
        completed = {str(t.id) for t in self.tasks if t.status == "completed"}
        return deps.issubset(completed)

    def first_ready_pending_id(self) -> str | None:
        for row in self.tasks:
            if row.status == "pending" and self.dependencies_satisfied(row.id):
                return row.id
        return None

    def next_ready_task(self) -> TaskItem | None:
        """Return the active row, or first dependency-ready pending row."""
        active = self.active_task_id()
        if active:
            return self._get(active)
        ready = self.first_ready_pending_id()
        return self._get(ready) if ready else None

    def child_tasks(self, parent_id: str) -> list[TaskItem]:
        """Return direct child rows for a parent id without changing state."""
        pid = str(parent_id or "").strip()
        if not pid:
            return []
        return [row for row in self.tasks if row.parent_id == pid]

    def validate_graph(self) -> dict[str, Any]:
        """Return dependency/activation diagnostics without mutating the board."""
        rows = list(self.tasks or [])
        issues: list[dict[str, Any]] = []

        def add_issue(code: str, message: str, *, severity: str = "error", task_id: str = "", **extra: Any) -> None:
            issue = {"code": code, "severity": severity, "message": message}
            if task_id:
                issue["task_id"] = str(task_id)
            issue.update(extra)
            issues.append(issue)

        id_counts: dict[str, int] = {}
        for row in rows:
            row_id = str(row.id)
            id_counts[row_id] = id_counts.get(row_id, 0) + 1
        for row_id, count in id_counts.items():
            if row_id and count > 1:
                add_issue("duplicate_task_id", f"Task id {row_id!r} appears {count} times", task_id=row_id, count=count)

        task_ids = {str(row.id) for row in rows if str(row.id)}
        graph: dict[str, list[str]] = {task_id: [] for task_id in task_ids}
        parent_graph: dict[str, str] = {}
        for row in rows:
            row_id = str(row.id)
            parent_id = (str(row.parent_id).strip() if row.parent_id else "")
            if parent_id:
                if parent_id == row_id:
                    add_issue("self_parent", f"Task {row_id!r} names itself as parent", task_id=row_id, parent_id=parent_id)
                elif parent_id not in task_ids:
                    add_issue("missing_parent", f"Task {row_id!r} names missing parent {parent_id!r}", task_id=row_id, parent_id=parent_id)
                else:
                    parent_graph[row_id] = parent_id
            raw_deps = list(row.depends_on or [])
            deps = _normalize_text_list(raw_deps)
            if len(raw_deps) != len(deps):
                add_issue("duplicate_dependency", f"Task {row_id!r} repeats dependency ids", task_id=row_id, depends_on=raw_deps)
            for dep in deps:
                if dep == row_id:
                    add_issue("self_dependency", f"Task {row_id!r} depends on itself", task_id=row_id, depends_on=deps)
                    continue
                if dep not in task_ids:
                    add_issue("missing_dependency", f"Task {row_id!r} depends on missing task {dep!r}", task_id=row_id, dependency=dep)
                    continue
                graph.setdefault(row_id, []).append(dep)

        visiting: set[str] = set()
        visited: set[str] = set()
        stack: list[str] = []
        cycles_seen: set[tuple[str, ...]] = set()

        def visit(node: str) -> None:
            if node in visiting:
                start = stack.index(node) if node in stack else 0
                cycle = stack[start:] + [node]
                key = tuple(sorted(set(cycle)))
                if key not in cycles_seen:
                    cycles_seen.add(key)
                    add_issue("cycle", "Dependency cycle: " + " -> ".join(cycle), task_id=node, cycle=cycle)
                return
            if node in visited:
                return
            visiting.add(node)
            stack.append(node)
            for dep in graph.get(node, []):
                visit(dep)
            stack.pop()
            visiting.remove(node)
            visited.add(node)

        for task_id in list(graph):
            visit(task_id)

        parent_seen: set[tuple[str, ...]] = set()
        for task_id in list(parent_graph):
            trail: list[str] = []
            node = task_id
            while node in parent_graph:
                if node in trail:
                    cycle = trail[trail.index(node):] + [node]
                    key = tuple(sorted(set(cycle)))
                    if key not in parent_seen:
                        parent_seen.add(key)
                        add_issue("parent_cycle", "Parent cycle: " + " -> ".join(cycle), task_id=node, cycle=cycle)
                    break
                trail.append(node)
                node = parent_graph[node]

        active_rows = [row for row in rows if row.status == "active"]
        if len(active_rows) > 1:
            add_issue(
                "multiple_active",
                f"Board has {len(active_rows)} active rows",
                active_task_ids=[str(row.id) for row in active_rows],
            )

        ready_task_id = self.active_task_id() or self.first_ready_pending_id()
        has_open_rows = any(row.status in OPEN for row in rows)
        if has_open_rows and not active_rows:
            add_issue("zero_active", "Open board has no active row", severity="warning", ready_task_id=ready_task_id or "")
        if any(row.status == "pending" for row in rows) and not ready_task_id:
            add_issue("no_ready_task", "Pending rows exist but no task is ready", severity="warning")

        return {
            "valid": not any(issue.get("severity") == "error" for issue in issues),
            "issue_count": len(issues),
            "issues": issues,
            "ready_task_id": ready_task_id or "",
        }

    # ── Write / mutate ────────────────────────────────────────────

    def set_rows(self, title: str, rows: list[dict[str, Any]], *, objective: str = "") -> None:
        """Replace all rows from structured Gateway/provider data."""
        self.title = str(title or "MO AGENT is working").strip()
        self.objective = str(objective or "").strip()
        built: list[TaskItem] = []
        for idx, item in enumerate(rows, 1):
            if not isinstance(item, dict):
                continue
            row = _coerce_task_item(item, idx)
            # Only the runtime may mint a completed row. A provider-supplied
            # evidence-gated row that arrives already "completed" with no evidence
            # is coerced to pending so model prose cannot pre-close real work.
            # (Status aliases like "done"/"complete" are already normalized by
            # TaskItem; rows that don't require evidence keep their status.)
            if row.status == "completed" and _task_requires_evidence(row) and not row.evidence:
                row.status = "pending"
            built.append(row)
        self.tasks = _remove_response_only_rows(built)
        self.gathered_evidence = _normalize_text_list([
            *self.gathered_evidence,
            *(item for task in self.tasks for item in task.evidence),
        ])
        self._ensure_one_active()
        self._touch()

    def activate(self, task_id: str) -> bool:
        row = self._get(task_id)
        if not row or row.status in {"completed", "cancelled"} or not self.dependencies_satisfied(row.id):
            return False
        for t in self.tasks:
            if t.status == "active":
                t.status = "pending"
        row.status = "active"
        row.blocker = ""
        self._touch()
        return True

    def append_evidence(self, task_id: str, evidence_item: Any) -> bool:
        """Append normalized evidence to a task row without duplicating entries."""
        row = self._get(task_id)
        if not row:
            return False
        added = False
        history_added = False
        for item in _normalize_text_list(evidence_item):
            if item not in row.evidence:
                row.evidence.append(item)
                added = True
            if item not in self.gathered_evidence:
                self.gathered_evidence.append(item)
                history_added = True
        if added or history_added:
            self._touch()
        return added

    def bind_pending_action(self, value: Any) -> bool:
        """Persist one typed computer action without reading prose."""
        normalized = normalize_pending_action(value)
        if not normalized or normalized == self.pending_action:
            return False
        self.pending_action = normalized
        self._touch()
        return True

    def clear_pending_action(self) -> bool:
        if not self.pending_action:
            return False
        self.pending_action = {}
        self._touch()
        return True

    def complete(self, task_id: str, *, evidence: Any = None) -> TaskCompletionResult:
        row = self._get(task_id)
        task_id = str(task_id or "")
        if not row:
            return TaskCompletionResult(False, task_id, "task_missing")
        if row.status == "cancelled":
            return TaskCompletionResult(False, task_id, "task_cancelled")
        if not self.dependencies_satisfied(task_id):
            return TaskCompletionResult(False, task_id, "dependencies_unsatisfied")
        if evidence is not None:
            self.append_evidence(task_id, evidence)
        if _task_requires_evidence(row) and not _task_has_qualifying_completion_evidence(row):
            return TaskCompletionResult(False, task_id, "missing_required_evidence")
        row.status = "completed"
        row.blocker = ""
        self._touch()
        if self.state == "completed":
            self.pending_action = {}
        return TaskCompletionResult(True, task_id)

    def block(self, task_id: str, reason: str) -> None:
        row = self._get(task_id)
        if row and row.is_open:
            row.status = "blocked"
            row.blocker = str(reason or "needs input")
            self._touch()

    def cancel(self, reason: str) -> None:
        """Cancel unfinished work without claiming completion or losing evidence."""
        for row in self.tasks:
            if row.is_open:
                row.status = "cancelled"
                row.blocker = str(reason or "Work cancelled")
        self.pending_action = {}
        self._touch()

    def reopen(self, task_id: str, reason: str) -> bool:
        """Reopen one completed/blocked row without changing its evidence."""
        row = self._get(task_id)
        if not row or row.status == "cancelled":
            return False
        for task in self.tasks:
            if task is not row and task.status == "active":
                task.status = "pending"
        row.status = "active"
        row.blocker = str(reason or "additional evidence required")
        self._touch()
        return True

    # ── Render ────────────────────────────────────────────────────

    def render(self) -> str:
        """Return the renderer-neutral board text used by events and logs."""
        summary = self.summary()
        parked = summary["state"] in {"abandoned", "paused"} and bool(summary["open"])
        lifecycle = " · paused/resumable" if parked else ""
        if summary["cancelled"]:
            lifecycle += f" · {summary['cancelled']} cancelled"
        lines = [
            f"{summary['total']} tasks ({summary['done']} done, {summary['open']} open){lifecycle}"
        ]
        for task in summary["tasks"]:
            status = "pending" if parked and task["status"] == "active" else task["status"]
            title = str(task.get("title", ""))
            if len(title) > 100:
                title = title[:97] + "..."
            suffix = (
                f" — {task['blocker']}"
                if task["status"] == "blocked" and task["blocker"]
                else ""
            )
            lines.append(f"  {status_marker(status)} {title}{suffix}")
        return "\n".join(lines)

    def render_rich(self) -> str:
        """Return Rich markup through the interface-owned renderer."""
        from interface.task_board_view import render_rich

        return render_rich(self)

    # ── Internal ──────────────────────────────────────────────────

    def _get(self, task_id: str) -> TaskItem | None:
        for t in self.tasks:
            if t.id == str(task_id):
                return t
        return None

    def _ensure_one_active(self) -> None:
        seen = False
        for t in self.tasks:
            t.status = _normalize_status(t.status)
            if t.status == "active":
                if seen or not self.dependencies_satisfied(t.id):
                    t.status = "pending"
                else:
                    seen = True
        if not seen:
            # No valid active row — promote first dependency-ready pending row.
            ready_id = self.first_ready_pending_id()
            if ready_id:
                for t in self.tasks:
                    if t.id == ready_id:
                        t.status = "active"
                        t.blocker = ""
                        break

    def _touch(self) -> None:
        self.updated_at = time.time()
        self.state = _state_for_board(self)
        # D4 fix: run graph diagnostics on every structural mutation so
        # issues (cycles, dupes, missing deps) are caught at creation time.
        try:
            diag = self.validate_graph()
            issues = [i for i in (diag.get("issues") or []) if i.get("severity") == "error"]
            if issues:
                import logging
                _log = logging.getLogger("mo.taskboard")
                for iss in issues:
                    _log.warning("taskboard graph issue: %s — %s", iss.get("code", "?"), iss.get("message", "?"))
        except Exception:
            traceback.print_exc()


def _coerce_task_item(item: Any, idx: int = 1) -> TaskItem:
    if isinstance(item, TaskItem):
        item.__post_init__()
        return item
    if isinstance(item, dict):
        return TaskItem(
            id=str(item.get("id") or idx),
            title=str(item.get("title") or item.get("text") or f"Task {idx}"),
            status=str(item.get("status") or "pending"),
            evidence=_normalize_text_list(item.get("evidence") or []),
            blocker=str(item.get("blocker") or ""),
            kind=str(item.get("kind") or ""),
            completion_gate=str(item.get("completion_gate") or item.get("gate") or ""),
            depends_on=item.get("depends_on") if item.get("depends_on") is not None else item.get("dependencies"),
            parent_id=str(item.get("parent_id") or item.get("parent") or ""),
            acceptance_criteria=item.get("acceptance_criteria") or [],
            expected_evidence=item.get("expected_evidence") or [],
            test_strategy=str(item.get("test_strategy") or ""),
        )
    return TaskItem(id=str(idx), title=str(item or f"Task {idx}"))


def _coerce_work_rows(items: list[Any]) -> list[TaskItem]:
    """Normalize persisted rows and discard response-only checklist entries.

    A chat response is not work state. Older boards may contain a synthetic
    ``report/final`` tail; load them without that row and reconnect any real
    downstream dependencies to the removed row's prerequisites.
    """
    return _remove_response_only_rows([
        _coerce_task_item(item, idx)
        for idx, item in enumerate(items, start=1)
    ])


def _remove_response_only_rows(rows: list[TaskItem]) -> list[TaskItem]:
    dropped = {
        row.id: list(row.depends_on)
        for row in rows
        if row.kind == "report" and row.completion_gate != "tool"
    }
    if not dropped:
        return rows

    def expand_dependency(task_id: str, seen: set[str] | None = None) -> list[str]:
        if task_id not in dropped:
            return [task_id]
        visited = set(seen or ())
        if task_id in visited:
            return []
        visited.add(task_id)
        expanded: list[str] = []
        for dependency in dropped[task_id]:
            for resolved in expand_dependency(dependency, visited):
                if resolved not in expanded:
                    expanded.append(resolved)
        return expanded

    kept = [row for row in rows if row.id not in dropped]
    for row in kept:
        dependencies: list[str] = []
        for dependency in row.depends_on:
            for resolved in expand_dependency(dependency):
                if resolved != row.id and resolved not in dependencies:
                    dependencies.append(resolved)
        row.depends_on = dependencies
        if row.parent_id in dropped:
            parents = expand_dependency(row.parent_id)
            row.parent_id = parents[0] if parents else ""
    return kept


def _normalize_status(status: str) -> str:
    value = str(status or "pending").lower().strip().replace("-", "_")
    aliases = {
        "done": "completed", "complete": "completed",
        "in_progress": "active",
    }
    value = aliases.get(value, value)
    return value if value in STATUSES else "pending"


def _normalize_kind(kind: str) -> str:
    value = str(kind or "").lower().strip().replace("-", "_")
    aliases = {"read": "inspect", "search": "inspect", "write": "edit", "test": "verify", "final": "report"}
    value = aliases.get(value, value)
    return value if value in KIND_VALUES else ""


def _normalize_plan_owner(owner: str) -> str:
    value = str(owner or "").lower().strip().replace("-", "_")
    return value if value in PLAN_OWNERS else ""


def _normalize_completion_gate(gate: str) -> str:
    value = str(gate or "").lower().strip().replace("-", "_")
    aliases = {"tests": "verification", "verify": "verification", "operator": "manual"}
    value = aliases.get(value, value)
    return value if value in COMPLETION_GATES else ""


def _normalize_text_list(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, int, float)):
        raw_items = [values]
    else:
        try:
            raw_items = list(values)
        except TypeError:
            raw_items = []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def normalize_pending_action(value: Any) -> dict[str, str]:
    """Return a bounded computer action contract or an empty mapping."""
    if not isinstance(value, dict):
        return {}
    capability = str(value.get("capability") or "").strip().lower()[:80]
    action = str(value.get("action") or "").strip().lower()[:80]
    target = str(value.get("target") or "").strip().lower()[:80]
    if capability not in {"computer_control", "web"}:
        return {}
    if not action or action == "none" or target in {"", "none"}:
        return {}
    return {"capability": capability, "action": action, "target": target}


def _normalize_board_state(state: str) -> str:
    value = str(state or "active").lower().strip().replace("-", "_")
    return value if value in {"active", "completed", "blocked", "paused", "abandoned", "cancelled"} else "active"


def _state_for_board(board: TaskBoard) -> str:
    tasks = board.tasks
    if not tasks:
        return _normalize_board_state(board.state)
    if all(task.status == "completed" for task in tasks):
        return "completed"
    if all(not task.is_open for task in tasks):
        return "cancelled"
    if any(task.status == "blocked" for task in tasks):
        return "blocked"
    return "active"


def _task_snapshot(task: TaskItem) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "evidence": list(task.evidence),
        "blocker": task.blocker,
        "kind": task.kind,
        "completion_gate": task.completion_gate,
        "depends_on": list(task.depends_on),
        "parent_id": task.parent_id,
        "acceptance_criteria": list(task.acceptance_criteria),
        "expected_evidence": list(task.expected_evidence),
        "test_strategy": task.test_strategy,
    }


def board_update_event(board: TaskBoard, *, update: str = "updated", rendered: str | None = None) -> dict[str, Any]:
    """Return a small structured update event without changing board truth."""
    contract = check_task_board_contract(board)
    return {
        "type": "taskboard_update",
        "update": str(update or "updated"),
        "turn_id": board.turn_id,
        "board_id": board.board_id,
        "session_id": board.session_id,
        "source": board.source,
        "plan_owner": board.plan_owner,
        "required_acceptance_criteria": list(board.required_acceptance_criteria),
        "required_verification_criteria": list(board.required_verification_criteria),
        "state": str(board.state or "active"),
        "active_task_id": str(board.active_task_id() or ""),
        "done_count": int(board.done_count()),
        "open_count": int(board.open_count()),
        "contract_ok": contract.ok,
        "contract_reasons": list(contract.reasons),
        "rendered": str(rendered if rendered is not None else board.render()),
    }


def check_task_board_contract(
    board: TaskBoard | None,
    *,
    require_completed: bool = False,
    require_evidence: bool = False,
    persisted_tasks: list[dict[str, Any]] | None = None,
) -> TaskBoardContractResult:
    """Return centralized taskboard health diagnostics without side effects.

    When *persisted_tasks* is provided (e.g. from a TaskManager), each entry is
    cross-referenced against the board rows: missing rows, status drift, and
    blocked/completed mismatches are reported as contract reasons.
    """
    if not board:
        return TaskBoardContractResult(False, ["taskboard_missing"], {})
    summary = board.summary()
    contract_summary = {
        "board_id": str(summary.get("board_id") or ""),
        "state": str(summary.get("state") or ""),
        "total": int(summary.get("total") or 0),
        "done": int(summary.get("done") or 0),
        "open": int(summary.get("open") or 0),
        "active_task_id": str(summary.get("active_task_id") or ""),
        "ready_task_id": str(summary.get("ready_task_id") or ""),
    }
    reasons: list[str] = []
    if not board.tasks:
        reasons.append("taskboard_empty")
    graph = board.validate_graph()
    for issue in graph.get("issues") or []:
        if isinstance(issue, dict) and issue.get("severity") == "error":
            code = str(issue.get("code") or "unknown")
            task_id = str(issue.get("task_id") or "")
            reasons.append(f"graph:{code}" + (f":{task_id}" if task_id else ""))
    open_rows = [task for task in board.tasks if task.is_open]
    if require_completed and open_rows:
        reasons.append(f"taskboard_open:{len(open_rows)}")
    if require_completed and summary["cancelled"]:
        reasons.append(f"taskboard_cancelled:{summary['cancelled']}")
    for task in board.tasks:
        if task.status == "blocked":
            reasons.append(f"blocked_task:{task.id}")
        if require_evidence and task.status == "completed" and _task_requires_evidence(task) and not _task_has_qualifying_completion_evidence(task):
            reasons.append(f"missing_evidence:{task.id}")

    def acceptance_id(value: object) -> str:
        match = re.match(r"\s*(AC\d+)\b", str(value or ""), flags=re.I)
        return match.group(1).upper() if match else ""

    required_delivery = {
        criterion
        for value in board.required_acceptance_criteria
        if (criterion := acceptance_id(value))
    }
    required_verification = {
        criterion
        for value in board.required_verification_criteria
        if (criterion := acceptance_id(value))
    }
    planned_delivery = {
        criterion
        for task in board.tasks
        if task.kind in {"inspect", "edit", "execute"}
        for value in task.acceptance_criteria
        if (criterion := acceptance_id(value))
    }
    planned_verification = {
        criterion
        for task in board.tasks
        if task.kind == "verify"
        for value in task.acceptance_criteria
        if (criterion := acceptance_id(value))
    }
    for criterion in sorted(required_delivery - planned_delivery):
        reasons.append(f"acceptance_delivery_missing:{criterion}")
    for criterion in sorted(required_verification - planned_verification):
        reasons.append(f"acceptance_verification_missing:{criterion}")

    # ── persisted-task ↔ board-row sync ─────────────────────
    if persisted_tasks:
        row_by_id = {row.id: row for row in board.tasks}
        for p in persisted_tasks:
            pid = str(p.get("id") or "")
            if not pid:
                continue
            row = row_by_id.get(pid)
            if row is None:
                reasons.append(f"task_sync:missing_board_row:{pid}")
                continue
            p_status = str(p.get("status") or "")
            if p_status == "done" and row.status != "completed":
                reasons.append(f"task_sync:done_not_completed:{pid}")
            elif p_status == "blocked" and row.status != "blocked":
                reasons.append(f"task_sync:blocked_mismatch:{pid}")
            elif p_status in ("active", "pending") and row.status in ("completed", "blocked"):
                reasons.append(f"task_sync:{p_status}_row_{row.status}:{pid}")

    return TaskBoardContractResult(not reasons, reasons, contract_summary)


def _task_requires_evidence(task: TaskItem) -> bool:
    if task.expected_evidence:
        return True
    if task.completion_gate in {"tool", "verification", "manual"}:
        return True
    return task.kind in {"inspect", "edit", "execute", "test", "verify", "ask"}


def _task_has_qualifying_completion_evidence(task: TaskItem) -> bool:
    evidence = [str(item or "").strip() for item in (task.evidence or [])]
    if not evidence:
        return False
    if task.completion_gate in {"tool", "verification", "manual"} or task.kind in {"inspect", "edit", "execute", "test", "verify", "ask"} or task.expected_evidence:
        from .task_evidence import completion_evidence_set_matches_task
        return completion_evidence_set_matches_task(task, evidence)
    return True


def snapshot_dict(board: TaskBoard, *, event: str, state: str | None = None, source: str | None = None) -> dict[str, Any]:
    """Return a serializable taskboard snapshot for the append-only ledger."""
    now = time.time()
    normalized_state = _normalize_board_state(state or _state_for_board(board))
    contract = check_task_board_contract(board, require_completed=normalized_state == "completed")
    return {
        "turn_id": board.turn_id,
        "board_id": board.board_id,
        "session_id": board.session_id,
        "source": str(source or board.source),
        "plan_owner": board.plan_owner,
        "required_acceptance_criteria": list(board.required_acceptance_criteria),
        "required_verification_criteria": list(board.required_verification_criteria),
        "pending_action": dict(board.pending_action),
        "gathered_evidence": list(board.gathered_evidence),
        "objective": board.objective,
        "title": board.title,
        "state": normalized_state,
        "tasks": [_task_snapshot(task) for task in board.tasks],
        "created_at": float(board.created_at or now),
        "updated_at": float(board.updated_at or now),
        "event": str(event or "updated"),
        "contract": contract.as_dict(),
    }


def record_snapshot(
    board: TaskBoard | None,
    event: str,
    *,
    state: str | None = None,
    source: str | None = None,
    path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Append a taskboard snapshot. Ledger failure must never break work.

    Default ledger writes are disabled under pytest so verification runs cannot
    pollute the operator's real private taskboard history. Tests that assert
    ledger behavior pass an explicit temporary ``path``.

    Also writes a fast-access ``current.json`` so resume and contract checks can
    read current state without scanning the append-only ledger.
    """
    if not board:
        return None
    try:
        ledger_path = _resolve_ledger_path(path)
        if ledger_path is None:
            return None
        record = snapshot_dict(board, event=event, state=state, source=source)
        fingerprint = _snapshot_fingerprint(record)
        if getattr(board, "_last_snapshot_fingerprint", "") == fingerprint:
            return record
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with _taskboard_file_lock(ledger_path):
            with ledger_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            _prune_taskboard_ledger(ledger_path)
            # ── also write current.json for fast-access resume ─────
            try:
                from .task_manager import TaskManager
                # Root current.json next to the resolved ledger so explicit/tmp
                # ledger paths (tests, env overrides) never touch live memory.
                tm = TaskManager(Path.cwd(), tasks_dir=ledger_path.parent)
                if record.get("tasks"):
                    tm.save(record)
                elif not tm.load_tasks() and tm.current_file.exists():
                    tm.clear()
            except Exception:
                pass  # current.json is best-effort; ledger is the authority
        try:
            setattr(board, "_last_snapshot_fingerprint", fingerprint)
        except Exception:
            traceback.print_exc()
        return record
    except Exception:
        return None


def clear_current_board_if_foreign_session(active_session_id: str, *, path: str | Path | None = None) -> bool:
    """Clear the fast-access ``current.json`` when it holds a board from a DIFFERENT
    session than the active one.

    A new session with no board of its own must never show (to watchers, ``/status``, or
    the resume fast-path) a stale board left by a prior session. Same-session state is
    preserved (the session ids match, so nothing is cleared), and the append-only ledger
    stays the authority, so a legitimate same-session resume is unaffected (it falls back
    to the ledger). Returns True if it cleared. Best-effort: never raises into the turn."""
    sid = str(active_session_id or "").strip()
    if not sid:
        return False
    try:
        ledger_path = _resolve_ledger_path(path)
        if ledger_path is None:
            return False
        with _taskboard_file_lock(ledger_path):
            from .task_manager import TaskManager
            tm = TaskManager(Path.cwd(), tasks_dir=ledger_path.parent)
            data = tm._data or {}
            existing = str(data.get("session_id") or "").strip()
            if existing and existing != sid and data.get("tasks"):
                tm.clear()  # archives, then removes current.json (ledger keeps full history)
                return True
    except Exception:
        pass
    return False


def clear_current_board_if_empty(*, path: str | Path | None = None) -> bool:
    """Clear ``current.json`` when it only contains a boardless/empty working copy.

    Boardless extension turns intentionally have no task rows. They should not leave
    ``current.json`` looking like an active taskboard with zero rows, because
    status/watchers interpret that as live work. The append-only ledger remains the
    durable audit trail; this only removes the fast-access working copy.
    """
    try:
        ledger_path = _resolve_ledger_path(path)
        if ledger_path is None:
            return False
        with _taskboard_file_lock(ledger_path):
            from .task_manager import TaskManager
            tm = TaskManager(Path.cwd(), tasks_dir=ledger_path.parent)
            if not tm.load_tasks() and tm.current_file.exists():
                tm.clear()
                return True
    except Exception:
        pass
    return False


def read_recent_snapshots(
    *,
    limit: int = 5,
    path: str | Path | None = None,
    board_id: str = "",
    turn_id: str = "",
    source: str = "",
    session_id: str = "",
    updated_at: float | None = None,
) -> list[dict[str, Any]]:
    """Read correlated ledger snapshots, optionally pinned to a recorded update."""
    try:
        ledger_path = _resolve_ledger_path(path)
        if ledger_path is None or not ledger_path.exists() or not ledger_path.is_file():
            return []
        raw_lines = ledger_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []

    def _filter(item: dict[str, Any]) -> bool:
        if board_id and str(item.get("board_id") or "") != str(board_id):
            return False
        if turn_id and str(item.get("turn_id") or "") != str(turn_id):
            return False
        if source and str(item.get("source") or "") != str(source):
            return False
        if session_id and str(item.get("session_id") or "") != str(session_id):
            return False
        if updated_at is not None and item.get("updated_at") != updated_at:
            return False
        return True

    return read_recent_ledger_entries(raw_lines, limit, filter_fn=_filter)


def resume_last_board(
    *,
    path: str | Path | None = None,
    max_age_hours: float | None = 24.0,
    session_id: str = "",
) -> TaskBoard | None:
    """D5 fix: read the most recent incomplete board snapshot and return a
    restorable TaskBoard. Returns None if all recent boards are completed/cancelled.

    Prefers ``current.json`` (fast-access single-file state) over the
    append-only ledger. Falls back to ledger scan when current.json is
    missing or corrupt.

    The returned board is a snapshot copy, not live state — the caller owns
    deciding whether to continue/abandon it. Explicit session restoration may
    pass ``None`` to retain older work; ambient discovery keeps its age limit.
    """
    # 1. Fast path: current.json
    board = _resume_from_current_json(
        max_age_hours=max_age_hours,
        ledger_path=path,
        session_id=session_id,
    )
    if board is not None:
        return board

    # 2. Fallback: ledger scan
    recent = read_recent_snapshots(limit=50, path=path, session_id=session_id)
    if not recent:
        return None
    now = time.time()
    cutoff = now - (max(0.25, float(max_age_hours or 1.0)) * 3600.0) if max_age_hours is not None else 0.0
    # read_recent_snapshots returns newest-last, so the last entry for each
    # board_id is the definitive state.  Walk forward and overwrite per-board
    # so the final value per board is its most recent entry.  This prevents
    # an older "active" entry from being returned when a later "completed"
    # entry exists for the same board.
    latest: dict[str, dict[str, Any]] = {}
    for item in recent:
        updated = float(item.get("updated_at") or item.get("created_at") or 0.0)
        if updated < cutoff:
            continue
        bid = str(item.get("board_id") or "")
        latest[bid] = item
    # Now check each board's most-recent entry; prefer oldest-first by ledger
    # position but accept only resumable boards with tasks.
    for item in latest.values():
        state = str(item.get("state") or "").strip()
        if state in {"completed", "cancelled"}:
            continue
        if not item.get("tasks"):
            continue
        return _task_board_from_snapshot(item)
    return None


def _resume_from_current_json(
    *,
    max_age_hours: float | None = 24.0,
    ledger_path: str | Path | None = None,
    session_id: str = "",
) -> TaskBoard | None:
    """Try to resume from current.json. Returns None on any failure."""
    try:
        from .task_manager import TaskManager
        # Determine root from ledger path, otherwise default to cwd
        lp = Path(ledger_path) if ledger_path else _resolve_ledger_path()
        if lp is None:
            return None  # ledger disabled / no private home → cannot resume, never touch cwd
        tm = TaskManager(lp.parent.parent.parent, tasks_dir=lp.parent)
        data = tm.load_snapshot()
        if session_id and str(data.get("session_id") or "") != str(session_id):
            return None
        tasks_list = list(data.get("tasks") or [])
        if not tasks_list:
            return None
        state = str(data.get("state") or "").strip()
        if state in {"completed", "cancelled"}:
            return None
        now = time.time()
        cutoff = now - (max(0.25, float(max_age_hours or 1.0)) * 3600.0) if max_age_hours is not None else 0.0
        updated_raw = data.get("updated_at")
        if updated_raw:
            try:
                from datetime import datetime as dt
                updated = dt.fromisoformat(str(updated_raw)).timestamp()
            except (ValueError, OSError):
                updated = float(data.get("created_at") or 0)
        else:
            updated = float(data.get("created_at") or 0)
        if updated < cutoff:
            return None
        item = {
            "board_id": data.get("board_id", ""),
            "turn_id": data.get("turn_id", ""),
            "session_id": data.get("session_id", ""),
            "title": data.get("title", ""),
            "objective": data.get("objective", ""),
            "source": data.get("source", "gateway"),
            "plan_owner": data.get("plan_owner", ""),
            "required_acceptance_criteria": data.get("required_acceptance_criteria", []),
            "required_verification_criteria": data.get("required_verification_criteria", []),
            "pending_action": data.get("pending_action", {}),
            "gathered_evidence": data.get("gathered_evidence", []),
            "state": state,
            "tasks": tasks_list,
            "created_at": float(data.get("created_at") or updated),
            "updated_at": updated,
        }
        return _task_board_from_snapshot(item)
    except Exception:
        return None


def _task_board_from_snapshot(item: dict[str, Any]) -> TaskBoard:
    """Build a TaskBoard from a snapshot dict (ledger or current.json shape)."""
    tasks_data = list(item.get("tasks") or [])
    return TaskBoard(
        board_id=str(item.get("board_id") or ""),
        turn_id=str(item.get("turn_id") or ""),
        session_id=str(item.get("session_id") or ""),
        title=str(item.get("title") or "MO AGENT is working"),
        objective=str(item.get("objective") or ""),
        source=str(item.get("source") or "gateway"),
        plan_owner=str(item.get("plan_owner") or ""),
        required_acceptance_criteria=list(item.get("required_acceptance_criteria") or []),
        required_verification_criteria=list(item.get("required_verification_criteria") or []),
        pending_action=dict(item.get("pending_action") or {}),
        gathered_evidence=list(item.get("gathered_evidence") or []),
        state=str(item.get("state") or "active"),
        created_at=float(item.get("created_at") or 0),
        updated_at=float(item.get("updated_at") or 0),
        tasks=tasks_data,
    )


def _resolve_ledger_path(path: str | Path | None = None) -> Path | None:
    """Return the ledger path, or None when ledger writes are disabled."""
    return resolve_ledger_path(
        path=path,
        disable_env=ENV_TASKBOARD_LEDGER_DISABLE,
        path_env=ENV_TASKBOARD_LEDGER_PATH,
        default_name=TASKBOARD_LEDGER_PATH,
    )


def _snapshot_fingerprint(record: dict[str, Any]) -> str:
    comparable = {
        "board_id": str(record.get("board_id") or ""),
        "source": str(record.get("source") or ""),
        "plan_owner": str(record.get("plan_owner") or ""),
        "required_acceptance_criteria": record.get("required_acceptance_criteria") or [],
        "required_verification_criteria": record.get("required_verification_criteria") or [],
        "pending_action": record.get("pending_action") or {},
        "gathered_evidence": record.get("gathered_evidence") or [],
        "event": str(record.get("event") or ""),
        "state": str(record.get("state") or ""),
        "objective": str(record.get("objective") or ""),
        "title": str(record.get("title") or ""),
        "tasks": record.get("tasks") or [],
    }
    return json.dumps(comparable, sort_keys=True)
