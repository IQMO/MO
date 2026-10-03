"""Worker status rendering helpers for the MO TUI."""
from __future__ import annotations

from core.tooling.shell_processes import active_shell_processes

from .formatting import brand_spinner_frame
from .activity import elapsed_seconds_text


class WorkerStatusMixin:
    """Status-bar summary for main, queued, goal, and background work."""

    def _show_worker_notice(self, text: str) -> None:
        """Deliver the runtime's existing completion notice to the owning TUI."""
        def publish():
            self._add("class:activity", text)
            self._set_notice(text, ttl=8.0)
            self._reanchor_render()

        loop = getattr(getattr(self, "_app", None), "loop", None)
        if loop is not None:
            loop.call_soon_threadsafe(publish)
        else:
            publish()

    def _workers_status_text(self) -> str:
        workers: list[str] = []
        try:
            shell_count = sum(
                1 for process in active_shell_processes()
                if process.get("background")
            )
        except Exception:
            shell_count = 0
        mapper_count = 0
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
                    # Pending queue depth already has one truthful footer owner
                    # ("Queued (N)").  Listing accepted queue records here too
                    # produced the contradictory/redundant "Active · Queued".
                    # Once promoted, the same record is running MO work.
                    if record.state == "running":
                        workers.append("MO")
                elif record.kind == "prt":
                    elapsed = elapsed_seconds_text(getattr(record, "created_at", None))
                    note = str(getattr(record, "note", "") or "")
                    phase = f" · {note}" if note and not note.startswith("background worker") else ""
                    workers.append(f"PRT {elapsed}{phase}".strip())
                elif record.kind == "main":
                    workers.append("MO")
                # Mapper workers are counted below rather than listed one-by-one.
            # Worker records retain lifecycle history and can outlive a process after
            # an interrupted callback. The shell-process owner above is authoritative
            # for whether the footer may claim that a background test is still running.
            mapper_count = sum(1 for r in active_records if r.kind == "mapper")
        if mapper_count:
            workers.append(f"Mapping ({mapper_count})")
        if self.busy and "MO" not in workers:
            workers.append("MO")
        if self._goal_worker_active and not any(item.startswith("Goal") for item in workers):
            workers.append("Goal")
        # If shell workers are active but no workers listed, show "MO" as the agent
        if not workers and shell_count > 0:
            workers.append("MO")
        mo_workers = sum(item == "MO" for item in workers)
        if mo_workers:
            try:
                mo_workers = max(
                    mo_workers,
                    int(getattr(getattr(self, "_workspace", None), "mo_instance_count", 1)),
                )
            except (TypeError, ValueError):
                pass
            workers = [item for item in workers if item != "MO"]
            workers.append(f"MO ({mo_workers})")
        if not workers:
            return ""
        if len(workers) > 3:
            state = f"{len(workers)} active"
        else:
            state = " · ".join(workers)
        prefix = f"Tests ({shell_count})" if shell_count > 0 else "Active"
        return f"{prefix} {brand_spinner_frame()} {state}"
