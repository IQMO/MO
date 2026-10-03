"""Workroom presentation over the existing Terminal handoff and resource sampler."""
from __future__ import annotations

from dataclasses import asdict
import json
import os
import secrets
import threading
import time


class RoomConnections:
    """Observe on the host's existing background cadence; never run an Agent."""

    def __init__(self, config, project, *, terminal=False):
        from core.runtime.resources import ResourceSampler
        self.config, self.project, self.terminal = config, project, terminal
        self._sampler = ResourceSampler(interval_seconds=2, exclusive_roots=True)
        self._lock = threading.Lock()
        self._request = self._pending = None
        self._message = ""
        self._opened = ""
        self._closed = False

    def request(self, target=None):
        with self._lock:
            if self._closed or self._request is not None or self._pending is not None:
                return False
            self._request = dict(target) if target is not None else "new"
            self._message = "Opening a new Terminal…" if target is None else "Requesting this Terminal's room…"
            return True

    def close(self):
        with self._lock:
            self._closed = True
            self._request = None
            self._pending = None

    def poll(self):
        if self._closed:
            return {}
        from core.state.paths import resolve_state_path
        from mo_desktop.everywhere import terminal_session_candidates
        from mo_desktop.design_studio.routing import launch_normal_terminal

        terminals = terminal_session_candidates(
            self.config, resolve_state_path("memory/sessions", self.config), require_session=False,
        )
        # Only this observer thread consumes requests. The GUI never performs
        # process discovery, starts a console, or samples resources itself.
        with self._lock:
            request, self._request = self._request, None
            if self._closed:
                return {}
            if request is not None:
                self._pending = {"request_id": secrets.token_hex(12), "deadline": time.monotonic() + 45}
            pending = self._pending
        try:
            if request == "new":
                # Serialize the irreversible process launch with close(). Once
                # close returns, an already-consumed request cannot launch.
                with self._lock:
                    if self._closed or self._pending is not pending:
                        return {}
                    instance = launch_normal_terminal(project_root=self.project, config=self.config)
                    pending.update(instance_id=instance, mode="talk", sent=False)
            elif request is not None:
                current = next((row for row in terminals if all(
                    row.get(key) == request.get(key) for key in ("instance_id", "pid", "slot", "cwd")
                )), None)
                if current is None:
                    raise RuntimeError("This Terminal changed or closed. Choose its current entry.")
                if not current.get("mologrthim_available"):
                    raise RuntimeError("This Terminal predates the room connection. Keep its work running; reopen it with current code when ready.")
                with self._lock:
                    if self._closed or self._pending is not pending:
                        return {}
                    pending.update(instance_id=current["instance_id"], mode="observe", sent=False)
            if pending:
                current = next((row for row in terminals if row["instance_id"] == pending["instance_id"]), None)
                if current and not pending["sent"]:
                    from core.design.terminal_handoff import queue_terminal_control

                    # Handoff publication is the second irreversible boundary;
                    # keep it ordered with close for the same reason as launch.
                    with self._lock:
                        if self._closed or self._pending is not pending:
                            return {}
                        queue_terminal_control("mologrthim", json.dumps({
                            "request_id": pending["request_id"], "mode": pending["mode"],
                        }), current, project_root=current["cwd"], expected_slot=current["slot"], config=self.config)
                        pending.update(sent=True, pid=current["pid"], slot=current["slot"])
                receipt = (current or {}).get("mologrthim_control") or {}
                if (current and current["pid"] == pending.get("pid")
                        and current["slot"] == pending.get("slot")
                        and receipt.get("request_id") == pending["request_id"]):
                    if receipt.get("ok") is not True:
                        raise RuntimeError(receipt.get("message") or "The Terminal could not open its room.")
                    with self._lock:
                        if self._pending is pending:
                            self._opened = pending["request_id"]
                            self._message = "Opened in the selected Terminal."
                            self._pending = None
                elif time.monotonic() >= pending["deadline"]:
                    raise RuntimeError("No room acknowledgement. The Terminal keeps running; check its window or select it again when ready.")
        except (OSError, RuntimeError, ValueError) as exc:
            with self._lock:
                if self._pending is pending:
                    self._message = str(exc)
                    self._pending = None

        with self._lock:
            if self._closed:
                return {}

        roots = {"main": os.getpid()}
        roots.update({f"terminal:{row['pid']}": row["pid"] for row in terminals})
        resources = asdict(self._sampler.sample(roots))
        trees = resources["trees"]
        totals = {key: (sum(row[key] for row in trees.values())
                       if all(row[key] is not None for row in trees.values()) else None)
                  for key in ("cpu_percent", "memory_bytes", "process_count")}
        from interface.workspace_panel import health_load_status
        resources.update(
            total=totals, host_pid=os.getpid(), shared_terminal=self.terminal,
            terminal_count=len(terminals) + int(self.terminal),
            pressure=health_load_status(resources["system_cpu_percent"], resources["memory_percent"])[0],
        )
        with self._lock:
            if self._closed:
                return {}
            return {
                "terminals": terminals, "resources": resources,
                "connection_pending": self._pending is not None or self._request is not None,
                "connection_message": self._message, "connection_opened": self._opened,
            }


def resource_text(resources):
    """Full measurements behind the room's compact resource display."""
    if not resources:
        return "Waiting for the first resource sample."
    lines = ["CPU is a share of total machine capacity. Memory is working set.",
             "Mologrthim shares its host process; window-only CPU and memory cannot be separated.",
             "Process trees are counted once in MO total. System includes other applications."]
    trees = resources.get("trees", {})
    rows = [("Mologrthim + Terminal (shared)" if resources.get("shared_terminal") else "Mologrthim + Desktop (shared)", trees.get("main", {}))]
    rows += [(name.replace("terminal:", "Terminal PID "), row) for name, row in trees.items() if name != "main"]
    rows.append(("MO total", resources.get("total", {})))
    for title, row in rows:
        lines.extend(("", title, resource_line(row), f"Processes: {row.get('process_count') if row.get('process_count') is not None else 'unavailable'}"))
    lines.extend(("", "System", resource_line({"cpu_percent": resources.get("system_cpu_percent"), "memory_bytes": resources.get("memory_used_bytes")}),
                  f"Sampling: {resources.get('state', 'unavailable')} · {resources.get('source', 'unavailable')}",
                  "Closing this view releases its artwork and stops its observation. It never stops Terminal work."))
    return "\n".join(lines)


def resource_line(row):
    cpu, memory = row.get("cpu_percent"), row.get("memory_bytes")
    cpu_text = "—" if cpu is None else f"{cpu:.1f}%"
    memory_text = "—" if memory is None else f"{memory / 1024 ** 2:.0f} MB"
    return f"CPU {cpu_text} · RAM {memory_text}"


def resource_cards(resources):
    from interface.workspace_panel import health_load_status
    host = resources.get("trees", {}).get("main", {})
    system = {"cpu_percent": resources.get("system_cpu_percent"), "memory_bytes": resources.get("memory_used_bytes")}
    rows = [("App + Terminal · shared" if resources.get("shared_terminal") else "App + Desktop · shared", host),
            (f"MO total · {resources.get('terminal_count', 0)} Terminals", resources.get("total", {})), ("System", system)]
    cards = []
    for label, row in rows:
        memory, total = row.get("memory_bytes"), resources.get("memory_total_bytes")
        state = health_load_status(row.get("cpu_percent"), memory * 100 / total if memory is not None and total else None)[0]
        cards.append((label, resource_line(row), state))
    return cards
