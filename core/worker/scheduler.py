"""Tiny worker routing policy layer.

Ghost may suggest routes, but this module gives MO one deterministic place to
check capacity/risk/conflicts before a receiver-confirmed handoff is shown.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .registry import WorkerRecord, WorkerRegistry, extract_worker_paths

WorkerAction = Literal[
    "run_main",
    "queue_main",
    "run_worker",
    "blocked_conflict",
    "blocked_capacity",
]


@dataclass(frozen=True)
class WorkerScheduleDecision:
    action: WorkerAction
    reason: str
    claimed_paths: list[str] = field(default_factory=list)
    conflicts: list[WorkerRecord] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.action in {"run_main", "queue_main", "run_worker"}


def decide_worker_route(
    objective: str,
    *,
    requested_route: str,
    main_busy: bool,
    risky: bool = False,
    registry: WorkerRegistry | None = None,
    background_active_count: int = 0,
    background_limit: int = 3,
) -> WorkerScheduleDecision:
    """Return the smallest safe route decision for current worker state."""
    route = str(requested_route or "main")
    if risky:
        if main_busy:
            return WorkerScheduleDecision("queue_main", "risky work stays queued for main MO")
        return WorkerScheduleDecision("run_main", "risky work stays with main MO")

    if route == "queue":
        return WorkerScheduleDecision("queue_main", "route requested main MO queue")
    if route != "background":
        if main_busy:
            return WorkerScheduleDecision("queue_main", "main MO is busy")
        return WorkerScheduleDecision("run_main", "main MO is available")

    claimed_paths = extract_worker_paths(objective)
    conflicts = registry.conflicts(claimed_paths) if registry else []
    if conflicts:
        conflict_ids = ", ".join(record.id for record in conflicts[:3])
        return WorkerScheduleDecision(
            "blocked_conflict",
            f"workspace conflict with active worker {conflict_ids}",
            claimed_paths=claimed_paths,
            conflicts=conflicts,
        )
    if int(background_active_count or 0) >= max(1, int(background_limit or 1)):
        return WorkerScheduleDecision(
            "blocked_capacity",
            f"background worker limit reached ({max(1, int(background_limit or 1))})",
            claimed_paths=claimed_paths,
        )
    return WorkerScheduleDecision("run_worker", "background worker available", claimed_paths=claimed_paths)
