"""MO task board state model.

TaskBoard stores normalized rows, metadata gates, dependency ordering, evidence
notes, and durable snapshots. Gateway owns board lifecycle; Agent/tool runtime
advances rows through metadata-aware gates; interface code only renders the
already-decided state.

Operations flow:
  Gateway/Ghost structured rows → Board.set_rows()
  Agent/tool runtime            → Board.complete()/activate()/block()
  TUI/monitor/handoff           → read/render Board state only.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import traceback

from ..path_defaults import ENV_MO_STATE_HOME, ENV_TASKBOARD_LEDGER_DISABLE, ENV_TASKBOARD_LEDGER_PATH, TASKBOARD_LEDGER_PATH

STATUSES = {"pending", "active", "completed", "blocked"}
OPEN = {"pending", "active", "blocked"}
KIND_VALUES = {"", "inspect", "edit", "execute", "verify", "report", "ask"}
COMPLETION_GATES = {"", "tool", "verification", "final", "manual"}

# D003 visible status symbols — the single source of truth for every surface
# (TUI board, goal views, ghost context, telegram render). Do not redefine.
STATUS_MARKERS = {"completed": "√", "active": "→", "blocked": "!", "pending": "□"}


def status_marker(status: str) -> str:
    """Return the D003 checklist symbol for a task status."""
    return STATUS_MARKERS.get(str(status or "").strip().lower(), STATUS_MARKERS["pending"])


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
        self.evidence = _normalize_evidence(self.evidence)
        self.kind = _normalize_kind(self.kind)
        self.completion_gate = _normalize_completion_gate(self.completion_gate)
        self.depends_on = _normalize_depends_on(self.depends_on)
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

    def __post_init__(self) -> None:
        now = time.time()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = self.created_at
        self.board_id = str(self.board_id or (f"board-{self.turn_id}" if self.turn_id else f"board-{uuid.uuid4().hex[:8]}"))
        self.session_id = str(self.session_id or "")
        self.source = str(self.source or "gateway")
        self.state = _normalize_board_state(self.state)
        self.tasks = [_coerce_task_item(task, idx) for idx, task in enumerate(list(self.tasks or []), start=1)]
        self._ensure_one_active()
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
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "total": len(self.tasks),
            "done": self.done_count(),
            "open": self.open_count(),
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
            deps = _normalize_depends_on(raw_deps)
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
        """Replace all rows from structured Gateway/Ghost data."""
        self.title = str(title or "MO AGENT is working").strip()
        self.objective = str(objective or "").strip()
        built: list[TaskItem] = []
        for idx, item in enumerate(rows, 1):
            if not isinstance(item, dict):
                continue
            row_status = str(item.get("status") or "pending")
            row_evidence = _normalize_evidence(item.get("evidence") or [])
            # Only the runtime may mint a completed row. A plan/Ghost-supplied row
            # that arrives "completed"/"done" with no evidence is coerced to
            # pending so model prose cannot pre-close work before any tool runs.
            # Completed rows that carry evidence (e.g. a restored board) are kept.
            if row_status in ("completed", "done") and not row_evidence:
                row_status = "pending"
            built.append(TaskItem(
                id=str(item.get("id") or idx),
                title=str(item.get("text") or item.get("title") or f"Task {idx}"),
                status=row_status,
                evidence=row_evidence,
                blocker=str(item.get("blocker") or ""),
                kind=str(item.get("kind") or ""),
                completion_gate=str(item.get("completion_gate") or item.get("gate") or ""),
                depends_on=item.get("depends_on") if item.get("depends_on") is not None else item.get("dependencies"),
                parent_id=str(item.get("parent_id") or item.get("parent") or ""),
                acceptance_criteria=item.get("acceptance_criteria") or [],
                expected_evidence=item.get("expected_evidence") or [],
                test_strategy=str(item.get("test_strategy") or ""),
            ))
        self.tasks = built
        self._ensure_one_active()
        self._touch()

    def activate(self, task_id: str) -> bool:
        row = self._get(task_id)
        if not row or row.status == "completed" or not self.dependencies_satisfied(row.id):
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
        for item in _normalize_evidence(evidence_item):
            if item not in row.evidence:
                row.evidence.append(item)
                added = True
        if added:
            self._touch()
        return added

    def complete(self, task_id: str, *, evidence: Any = None) -> None:
        row = self._get(task_id)
        if row:
            if not self.dependencies_satisfied(task_id):
                return
            if evidence is not None:
                self.append_evidence(task_id, evidence)
            # Evidence-gated rows must not close on a bare complete() with no
            # evidence (e.g. a lone complete_task call before any real tool ran).
            # Surface the gap as blocked instead of minting fake completion; this
            # closes the gate-scoping hole at the root regardless of which
            # provider round the completion happens in.
            if _task_requires_evidence(row) and not row.evidence:
                row.status = "blocked"
                row.blocker = "missing evidence: run a real tool for this task before completing it"
                self._touch()
                return
            row.status = "completed"
            row.blocker = ""
            self._touch()

    def block(self, task_id: str, reason: str) -> None:
        row = self._get(task_id)
        if row and row.status != "completed":
            row.status = "blocked"
            row.blocker = str(reason or "needs input")
            self._touch()

    # ── Render ────────────────────────────────────────────────────

    def render(self) -> str:
        """Plain-text render for monitor logs, tests, and debug."""
        from interface.task_board_view import render_plain
        return render_plain(self)

    def render_rich(self) -> str:
        """Rich-markup render for main terminal live display."""
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
            evidence=_normalize_evidence(item.get("evidence") or []),
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


def _normalize_evidence(evidence: Any) -> list[str]:
    if evidence is None:
        return []
    if isinstance(evidence, (str, int, float)):
        raw_items = [evidence]
    else:
        try:
            raw_items = list(evidence)
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


def _normalize_completion_gate(gate: str) -> str:
    value = str(gate or "").lower().strip().replace("-", "_")
    aliases = {"tests": "verification", "verify": "verification", "answer": "final", "operator": "manual"}
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


def _normalize_depends_on(depends_on: Any) -> list[str]:
    if depends_on is None:
        return []
    if isinstance(depends_on, (str, int, float)):
        raw_items = [depends_on]
    else:
        try:
            raw_items = list(depends_on)
        except TypeError:
            raw_items = []
    seen: set[str] = set()
    result: list[str] = []
    for item in raw_items:
        value = str(item or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _normalize_board_state(state: str) -> str:
    value = str(state or "active").lower().strip().replace("-", "_")
    return value if value in {"active", "completed", "blocked", "abandoned"} else "active"


def _state_for_board(board: TaskBoard) -> str:
    tasks = board.tasks
    if not tasks:
        return _normalize_board_state(board.state)
    if all(task.status == "completed" for task in tasks):
        return "completed"
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


def board_update_event(board: TaskBoard, *, update: str = "updated", rich: str | None = None, rendered: str | None = None) -> dict[str, Any]:
    """Return a small structured update event without changing board truth."""
    contract = check_task_board_contract(board)
    return {
        "type": "taskboard_update",
        "update": str(update or "updated"),
        "turn_id": board.turn_id,
        "board_id": board.board_id,
        "session_id": board.session_id,
        "source": board.source,
        "state": _state_for_board(board),
        "active_task_id": str(board.active_task_id() or ""),
        "done_count": int(board.done_count()),
        "open_count": int(board.open_count()),
        "contract_ok": contract.ok,
        "contract_reasons": list(contract.reasons),
        "rendered": str(rendered if rendered is not None else board.render()),
        "rich": str(rich if rich is not None else board.render_rich()),
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
    if require_completed and int(summary.get("open") or 0) > 0:
        reasons.append(f"taskboard_open:{int(summary.get('open') or 0)}")
    for task in board.tasks:
        if task.status == "blocked":
            reasons.append(f"blocked_task:{task.id}")
        if require_evidence and task.status == "completed" and _task_requires_evidence(task) and not task.evidence:
            reasons.append(f"missing_evidence:{task.id}")

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
    if task.completion_gate in {"tool", "verification"}:
        return True
    return task.kind in {"inspect", "edit", "test", "verify"}


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
    pollute the operator's real ``memory/taskboards`` history. Tests that assert
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
        with ledger_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        # ── also write current.json for fast-access resume ─────
        try:
            from .task_manager import TaskManager
            # Root current.json next to the resolved ledger so explicit/tmp
            # ledger paths (tests, env overrides) never touch live memory.
            tm = TaskManager(Path.cwd(), tasks_dir=ledger_path.parent)
            tm.save(record)
        except Exception:
            pass  # current.json is best-effort; ledger is the authority
        try:
            setattr(board, "_last_snapshot_fingerprint", fingerprint)
        except Exception:
            traceback.print_exc()
        return record
    except Exception:
        return None


def read_recent_snapshots(
    *,
    limit: int = 5,
    path: str | Path | None = None,
    board_id: str = "",
    turn_id: str = "",
    source: str = "",
    session_id: str = "",
) -> list[dict[str, Any]]:
    """Read recent ledger snapshots, newest last. Returns [] on any failure."""
    try:
        ledger_path = _resolve_ledger_path(path)
        if ledger_path is None or not ledger_path.exists() or not ledger_path.is_file():
            return []
        raw_lines = ledger_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    matches: list[dict[str, Any]] = []
    for raw in reversed(raw_lines):
        try:
            item = json.loads(raw)
        except Exception:
            continue
        if not isinstance(item, dict):
            continue
        if board_id and str(item.get("board_id") or "") != str(board_id):
            continue
        if turn_id and str(item.get("turn_id") or "") != str(turn_id):
            continue
        if source and str(item.get("source") or "") != str(source):
            continue
        if session_id and str(item.get("session_id") or "") != str(session_id):
            continue
        matches.append(item)
        if len(matches) >= max(1, int(limit or 1)):
            break
    return list(reversed(matches))


def resume_last_board(
    *,
    path: str | Path | None = None,
    max_age_hours: float = 24.0,
) -> TaskBoard | None:
    """D5 fix: read the most recent incomplete board snapshot and return a
    restorable TaskBoard.  Returns None if all recent boards are completed.

    Prefers ``current.json`` (fast-access single-file state) over the
    append-only ledger. Falls back to ledger scan when current.json is
    missing or corrupt.

    The returned board is a snapshot copy, not live state — the caller owns
    deciding whether to continue/abandon it.
    """
    # 1. Fast path: current.json
    board = _resume_from_current_json(max_age_hours=max_age_hours, ledger_path=path)
    if board is not None:
        return board

    # 2. Fallback: ledger scan
    recent = read_recent_snapshots(limit=10, path=path)
    if not recent:
        return None
    now = time.time()
    cutoff = now - (max(0.25, float(max_age_hours or 1.0)) * 3600.0)
    for item in reversed(recent):
        updated = float(item.get("updated_at") or item.get("created_at") or 0.0)
        if updated < cutoff:
            continue
        state = str(item.get("state") or "").strip()
        if state == "completed":
            continue
        return _task_board_from_snapshot(item)
    return None


def _resume_from_current_json(*, max_age_hours: float = 24.0, ledger_path: str | Path | None = None) -> TaskBoard | None:
    """Try to resume from current.json. Returns None on any failure."""
    try:
        from .task_manager import TaskManager
        # Determine root from ledger path, otherwise default to cwd
        if ledger_path:
            lp = Path(ledger_path)
            root = lp.parent.parent.parent  # memory/taskboards/taskboards.jsonl → root
        else:
            root = Path.cwd()
        tm = TaskManager(root)
        data = tm.load_snapshot()
        tasks_list = list(data.get("tasks") or [])
        if not tasks_list:
            return None
        state = str(data.get("state") or "").strip()
        if state == "completed":
            return None
        now = time.time()
        cutoff = now - (max(0.25, float(max_age_hours or 1.0)) * 3600.0)
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
        state=str(item.get("state") or "active"),
        created_at=float(item.get("created_at") or 0),
        updated_at=float(item.get("updated_at") or 0),
        tasks=[_task_item_from_snapshot(t) for t in tasks_data],
    )


def _task_item_from_snapshot(snapshot: dict[str, Any]) -> TaskItem:
    """Rebuild a TaskItem from a ledger snapshot dict."""
    return TaskItem(
        id=str(snapshot.get("id") or ""),
        title=str(snapshot.get("title") or ""),
        status=str(snapshot.get("status") or "pending"),
        evidence=_normalize_evidence(snapshot.get("evidence") or []),
        blocker=str(snapshot.get("blocker") or ""),
        kind=str(snapshot.get("kind") or ""),
        completion_gate=str(snapshot.get("completion_gate") or snapshot.get("gate") or ""),
        depends_on=snapshot.get("depends_on") if snapshot.get("depends_on") is not None else snapshot.get("dependencies"),
        parent_id=str(snapshot.get("parent_id") or snapshot.get("parent") or ""),
        acceptance_criteria=snapshot.get("acceptance_criteria") or [],
        expected_evidence=snapshot.get("expected_evidence") or [],
        test_strategy=str(snapshot.get("test_strategy") or ""),
    )


def _resolve_ledger_path(path: str | Path | None = None) -> Path | None:
    """Return the ledger path, or None when ledger writes are disabled."""
    if os.environ.get(ENV_TASKBOARD_LEDGER_DISABLE, "").strip().lower() in ("1", "true", "yes"):
        return None
    if path:
        return Path(path)
    env_path = os.environ.get(ENV_TASKBOARD_LEDGER_PATH, "")
    if env_path:
        return Path(env_path)
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    state_home = os.environ.get(ENV_MO_STATE_HOME, "").strip()
    if state_home:
        return Path(state_home) / TASKBOARD_LEDGER_PATH
    return Path(TASKBOARD_LEDGER_PATH)


def _snapshot_fingerprint(record: dict[str, Any]) -> str:
    comparable = {
        "board_id": str(record.get("board_id") or ""),
        "source": str(record.get("source") or ""),
        "event": str(record.get("event") or ""),
        "state": str(record.get("state") or ""),
        "objective": str(record.get("objective") or ""),
        "title": str(record.get("title") or ""),
        "tasks": record.get("tasks") or [],
    }
    return json.dumps(comparable, sort_keys=True)
