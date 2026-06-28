"""Lightweight internal worker/handoff registry.

Tracks truthful handoffs between Ghost, main MO, queues, and background goals
without exposing a subagent platform to the user.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass, field
import traceback

from ..runtime.backend_monitor import get_monitor, redact_monitor_text

TERMINAL_STATES = {"completed", "blocked", "cancelled", "paused"}


@dataclass
class WorkerRecord:
    id: str
    kind: str
    source: str
    route: str
    objective: str
    state: str = "offered"
    role: str = ""
    note: str = ""
    claimed_paths: list[str] = field(default_factory=list)
    result_summary: str = ""
    evidence: list[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    finished_at: float = 0.0

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def active(self) -> bool:
        return self.state not in TERMINAL_STATES


class WorkerRegistry:
    """Small in-memory registry for active/recent MO workers."""

    def __init__(self, *, keep: int = 50):
        self.keep = keep
        self._records: dict[str, WorkerRecord] = {}
        self._order: list[str] = []

    def create(
        self,
        *,
        kind: str,
        source: str,
        route: str,
        objective: str,
        state: str = "offered",
        note: str = "",
        worker_id: str | None = None,
        claimed_paths: list[str] | None = None,
        role: str = "",
    ) -> WorkerRecord:
        now = time.time()
        record = WorkerRecord(
            id=worker_id or f"w-{uuid.uuid4().hex[:8]}",
            kind=str(kind or "main"),
            source=str(source or "user"),
            route=str(route or kind or "main"),
            objective=str(objective or "").strip(),
            state=str(state or "offered"),
            role=str(role or ""),
            note=str(note or ""),
            claimed_paths=normalize_worker_paths(claimed_paths if claimed_paths is not None else extract_worker_paths(objective)),
            created_at=now,
            updated_at=now,
        )
        self._records[record.id] = record
        self._order.append(record.id)
        self._trim()
        self._emit(record)
        return record

    def update(
        self,
        worker_id: str | None,
        state: str,
        note: str = "",
        *,
        result_summary: str = "",
        evidence: list[str] | None = None,
    ) -> WorkerRecord | None:
        if not worker_id:
            return None
        record = self._records.get(worker_id)
        if not record:
            return None
        record.state = str(state or record.state)
        if note:
            record.note = str(note)
        if result_summary:
            record.result_summary = redact_monitor_text(result_summary, 500)
        if evidence is not None:
            record.evidence = [redact_monitor_text(item, 240) for item in evidence[:12] if str(item or "").strip()]
        now = time.time()
        record.updated_at = now
        if record.state in TERMINAL_STATES and not record.finished_at:
            record.finished_at = now
        self._emit(record)
        return record

    def get(self, worker_id: str | None) -> WorkerRecord | None:
        return self._records.get(worker_id or "")

    def active(self) -> list[WorkerRecord]:
        return [self._records[worker_id] for worker_id in self._order if worker_id in self._records and self._records[worker_id].active]

    def recent(self, limit: int = 10) -> list[WorkerRecord]:
        ids = [worker_id for worker_id in self._order if worker_id in self._records]
        return [self._records[worker_id] for worker_id in ids[-limit:]]

    def conflicts(self, claimed_paths: list[str], *, exclude: str = "") -> list[WorkerRecord]:
        paths = normalize_worker_paths(claimed_paths)
        if not paths:
            return []
        conflicts: list[WorkerRecord] = []
        for record in self.active():
            if record.id == exclude:
                continue
            if any(paths_conflict(path, other) for path in paths for other in normalize_worker_paths(record.claimed_paths)):
                conflicts.append(record)
        return conflicts

    def summary(self, *, limit: int = 5) -> str:
        records = self.active() or self.recent(limit=limit)
        lines: list[str] = []
        for record in records[-limit:]:
            objective = redact_monitor_text(record.objective, 120)
            note = f" — {redact_monitor_text(record.note, 80)}" if record.note else ""
            result = f" => {redact_monitor_text(record.result_summary, 100)}" if record.result_summary else ""
            lines.append(f"- {record.kind}/{record.route}: {record.state} · {objective}{note}{result}")
        return "\n".join(lines)

    def _trim(self) -> None:
        while len(self._order) > self.keep:
            old = self._order.pop(0)
            self._records.pop(old, None)

    def _emit(self, record: WorkerRecord) -> None:
        monitor = get_monitor()
        if not monitor:
            return
        monitor.emit("worker_event", {
            "worker_id": record.id,
            "kind": record.kind,
            "source": record.source,
            "route": record.route,
            "state": record.state,
            "role": record.role,
            "objective": redact_monitor_text(record.objective, 240),
            "note": redact_monitor_text(record.note, 240),
            "claimed_paths": [redact_monitor_text(path, 160) for path in record.claimed_paths[:10]],
            "result_summary": redact_monitor_text(record.result_summary, 240),
            "evidence": [redact_monitor_text(item, 160) for item in record.evidence[:8]],
        })


def extract_worker_paths(text: str) -> list[str]:
    """Extract conservative path/file claims from an objective."""
    raw = str(text or "")
    candidates: list[str] = []
    patterns = [
        r"`([^`]+)`",
        r"(?<!\w)(?:[A-Za-z]:[\\/])?[\w.@()\-]+(?:[\\/][\w.@()\-]+)+(?!\w)",
        r"(?<!\w)[\w.@()\-]+\.(?:py|md|txt|json|ya?ml|toml|ini|js|jsx|ts|tsx|css|html|sh|ps1|bat|sql|sqlite|db)(?!\w)",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, raw, flags=re.IGNORECASE):
            value = match.group(1) if match.lastindex else match.group(0)
            candidates.append(value)
    return normalize_worker_paths(candidates)


def normalize_worker_paths(paths: list[str] | tuple[str, ...] | None) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for raw in paths or []:
        path = str(raw or "").strip().strip("`'\".,;:()[]{}")
        if not path or len(path) > 240:
            continue
        path = path.replace("\\", "/")
        path = re.sub(r"/+(?=/)", "/", path)
        path = path.rstrip("/")
        lowered = path.lower()
        if lowered in {".", "..", "the", "and", "or"}:
            continue
        if lowered not in seen:
            seen.add(lowered)
            result.append(path)
    filtered: list[str] = []
    for path in result:
        lowered = path.lower()
        if any(other.lower().endswith("/" + lowered) for other in result if other != path):
            continue
        filtered.append(path)
    return filtered


def paths_conflict(left: str, right: str) -> bool:
    left_n = normalize_worker_paths([left])
    right_n = normalize_worker_paths([right])
    if not left_n or not right_n:
        return False
    a = left_n[0].lower()
    b = right_n[0].lower()
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def ensure_worker_registry(agent) -> WorkerRegistry:
    registry = getattr(agent, "workers", None)
    if not isinstance(registry, WorkerRegistry):
        registry = WorkerRegistry()
        try:
            setattr(agent, "workers", registry)
        except Exception as e:
            try:
                from ..runtime.backend_monitor import get_monitor
                monitor = get_monitor()
                if monitor:
                    monitor.emit("worker_registry_setattr_error", {"error": str(e)[:200]})
            except Exception:
                traceback.print_exc()
    return registry
