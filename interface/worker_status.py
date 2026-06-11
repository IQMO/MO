"""Worker status rendering helpers for the MO TUI."""
from __future__ import annotations

from .formatting import moon_phase_frame


class WorkerStatusMixin:
    """Status-bar summary for main, queued, Ghost, goal, and background workers."""

    def _workers_status_text(self) -> str:
        workers: list[str] = []
        registry = getattr(self.agent, "workers", None)
        if registry and hasattr(registry, "active"):
            try:
                active_records = registry.active()
            except Exception:
                active_records = []
            for record in active_records[-3:]:
                if record.kind == "goal":
                    workers.append("Goal")
                elif record.kind == "worker":
                    workers.append("Background")
                elif record.kind == "queue":
                    workers.append("MO" if record.state == "running" else "Queued")
                elif record.kind == "prt":
                    workers.append("PRT")
                elif record.kind == "main":
                    workers.append("MO")
        if self.busy and "MO" not in workers:
            workers.append("MO")
        if self._goal_worker_active and not any(item.startswith("Goal") for item in workers):
            workers.append("Goal")
        if self._ghost_enabled:
            if any(style == "class:ghost-thinking" for style, _ in self._ghost_panel_lines):
                workers.append("Ghost active")
            else:
                workers.append("Ghost")
        if not workers:
            return ""
        elif len(workers) > 3:
            state = f"{len(workers)} active"
        else:
            state = " · ".join(workers)
        return f"Active {moon_phase_frame()} {state}"
