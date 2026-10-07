"""Mologrthim's WebView bridge: one observer thread while visible, and actions that reach a running MO only
through MO's existing handoff (a normal turn, a typed command, focusing its terminal). It never runs an Agent,
never dispatches a specialist directly (the lead does, so rank stays checked work) and never stops work."""
from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict
from typing import Any

from .snapshot import FloorObserver, conversation

OBSERVE_SECONDS = 2.0


class MologrthimBridge:
    def __init__(self, config: dict[str, Any] | None = None, *, focus: str = "") -> None:
        self.config = config if isinstance(config, dict) else {}
        self.focus = str(focus or "")
        self.on_status: Any = None
        self.on_ui_ready: Any = None
        self._window: Any = None
        self._observer = FloorObserver(self.config)
        self._lock = threading.Lock()
        self._latest: dict[str, Any] = {}
        self._visible = True
        self._closed = False
        self._wake = threading.Event()
        self._pending_new: list[dict[str, Any]] = []
        self._sampler: Any = None
        self._thread = threading.Thread(target=self._observe_loop, name="mologrthim-observe", daemon=True)

    # ---------------------------------------------------------------- lifecycle
    def attach_window(self, window: Any) -> None:
        self._window = window
        self._thread.start()

    def ui_ready(self) -> bool:
        if callable(self.on_ui_ready):
            self.on_ui_ready()
        return True

    def set_visible(self, visible: bool) -> bool:
        """Hidden = no observation and no sampling (his msg 34: MO apps must not load the machine)."""
        self._visible = bool(visible)
        if self._visible:
            self._wake.set()
        if callable(self.on_status):
            self.on_status({"kind": "visible", "visible": self._visible})
        return True

    def close(self) -> None:
        self._closed = True
        self._wake.set()

    def window_control(self, action: str) -> dict[str, bool]:
        """The shared frameless window buttons (same bounded adapter as MO Files)."""
        window = self._window
        if window is None:
            raise RuntimeError("Mologrthim's window is unavailable.")
        if action == "close":
            self.set_visible(False)
            window.destroy()
        elif action == "minimize":
            window.minimize()
        elif action == "toggle_maximize":
            native = getattr(window, "native", None)
            if native is not None and str(native.WindowState).endswith("Maximized"):
                window.restore()
            else:
                window.maximize()
        else:
            raise ValueError("Unknown Mologrthim window control.")
        return {"ok": True}

    # ---------------------------------------------------------------- observation
    def _observe_loop(self) -> None:
        while not self._closed:
            if self._visible:
                try:
                    data = self._observer.observe()
                    data["resources"] = self._resources(data.get("mos") or [])
                    data["focus"] = self.focus
                    self._deliver_new_assignments(data.get("mos") or [])
                    with self._lock:
                        self._latest = data
                except Exception as exc:
                    with self._lock:
                        self._latest = {**self._latest, "error": f"{type(exc).__name__}: {exc}"[:200]}
            self._wake.wait(OBSERVE_SECONDS)
            self._wake.clear()

    def _resources(self, mos: list[dict[str, Any]]) -> dict[str, Any]:
        from core.runtime.resources import ResourceSampler
        from interface.workspace_panel import health_load_status

        if self._sampler is None:
            self._sampler = ResourceSampler(interval_seconds=OBSERVE_SECONDS, exclusive_roots=True)
        roots = {"app": os.getpid()}
        roots.update({f"mo:{m['id']}": m["pid"] for m in mos if m.get("pid")})
        sample = asdict(self._sampler.sample(roots))
        trees = sample.get("trees") or {}
        mo_rows = [row for name, row in trees.items() if name != "app"]
        mo_cpu = sum(row["cpu_percent"] for row in mo_rows if row.get("cpu_percent") is not None) if mo_rows else None
        mo_memory = sum(row["memory_bytes"] for row in mo_rows if row.get("memory_bytes") is not None) if mo_rows else None
        return {
            "system_cpu": sample.get("system_cpu_percent"), "memory_percent": sample.get("memory_percent"),
            "mo_cpu": mo_cpu, "mo_memory": mo_memory, "app": trees.get("app") or {},
            "per_mo": {name.split(":", 1)[1]: row for name, row in trees.items() if name.startswith("mo:")},
            # the Health bands: "pressure" (85%+) shows as error, "working" (65%+) as warn
            "pressure": {"pressure": "error", "working": "warn"}.get(
                health_load_status(sample.get("system_cpu_percent"), sample.get("memory_percent"))[0], "ok"),
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._latest)

    def conversation(self, instance_id: str) -> list[dict[str, str]]:
        mo = self._mo(instance_id)
        return conversation(self.config, mo["slot"]) if mo else []

    # ---------------------------------------------------------------- actions through MO
    def _mo(self, instance_id: str) -> dict[str, Any] | None:
        with self._lock:
            mos = list(self._latest.get("mos") or [])
        return next((m for m in mos if m.get("id") == instance_id), None)

    def _target(self, instance_id: str) -> dict[str, Any]:
        mo = self._mo(instance_id)
        if mo is None:
            raise RuntimeError("That MO is no longer running.")
        if mo.get("desktop"):
            raise RuntimeError("MO Desktop takes requests in its own composer.")
        return {"instance_id": mo["id"], "pid": mo["pid"], "cwd": mo["cwd"], "slot": mo["slot"]}

    def _result(self, action: Any, done: str) -> dict[str, Any]:
        try:
            action()
            self._wake.set()
            return {"ok": True, "message": done}
        except Exception as exc:
            return {"ok": False, "message": str(exc) or type(exc).__name__}

    def assign(self, instance_id: str, text: str) -> dict[str, Any]:
        """A normal request to that MO (its lead decides who works on it and checks the report)."""
        from core.design.terminal_handoff import queue_terminal_turn

        text = " ".join(str(text or "").split())[:2000]
        if not text:
            return {"ok": False, "message": "Write the assignment first."}
        return self._result(lambda: queue_terminal_turn(text, self._target(instance_id), config=self.config),
                            "Sent to that MO as a normal request.")

    def ask_specialist(self, instance_id: str, name: str, text: str) -> dict[str, Any]:
        """Still through MO: its lead dispatches the specialist and checks the report, so rank stays true."""
        name = " ".join(str(name or "").split())[:90]
        return self.assign(instance_id, f"Have the {name} {str(text or '').strip()}")

    def hire(self, instance_id: str, name: str) -> dict[str, Any]:
        from core.design.terminal_handoff import queue_terminal_turn

        name = " ".join(str(name or "").split())[:90]
        return self._result(lambda: queue_terminal_turn(f"hire {name}", self._target(instance_id), config=self.config),
                            f"{name} joins once that MO handles your hire.")

    def pause_goal(self, instance_id: str) -> dict[str, Any]:
        """/goal pause in that MO: the running step finishes, no new step starts (never a hard stop)."""
        from core.design.terminal_handoff import queue_terminal_control

        def send() -> None:
            target = self._target(instance_id)
            queue_terminal_control("command", "/goal pause", target, project_root=target["cwd"],
                                   expected_slot=target["slot"], config=self.config)
        return self._result(send, "Pausing after the current step.")

    def resume_goal(self, instance_id: str) -> dict[str, Any]:
        from core.design.terminal_handoff import queue_terminal_control

        def send() -> None:
            target = self._target(instance_id)
            queue_terminal_control("command", "/goal resume", target, project_root=target["cwd"],
                                   expected_slot=target["slot"], config=self.config)
        return self._result(send, "Resuming the goal.")

    def open_terminal(self, instance_id: str) -> dict[str, Any]:
        from core.design.terminal_handoff import queue_terminal_control

        def send() -> None:
            target = self._target(instance_id)
            queue_terminal_control("terminal", "", target, project_root=target["cwd"],
                                   expected_slot=target["slot"], config=self.config)
        return self._result(send, "Bringing that terminal forward.")

    def run_owner_command(self, instance_id: str, command: str) -> dict[str, Any]:
        """Owner desk (only from the private profile bridge): run one of its commands in that MO."""
        from core.design.terminal_handoff import queue_terminal_control
        from core.local_extensions import command_specs

        command = str(command or "").strip()
        names = {str(spec.get("name") or spec.get("command") or "") for spec in command_specs()}
        if command not in names:
            return {"ok": False, "message": "That command is not on your owner desk."}

        def send() -> None:
            target = self._target(instance_id)
            queue_terminal_control("command", command, target, project_root=target["cwd"],
                                   expected_slot=target["slot"], config=self.config)
        return self._result(send, f"{command} sent to that MO.")

    def new_mo(self, project: str = "", text: str = "") -> dict[str, Any]:
        """Start a normal MO Terminal; an assignment, if any, goes to it as its first request once it runs."""
        from mo_desktop.design_studio.routing import launch_normal_terminal

        with self._lock:
            mos = list(self._latest.get("mos") or [])
        project = str(project or "") or next((m["cwd"] for m in mos if not m.get("desktop")), "") or os.getcwd()

        def launch() -> None:
            instance = launch_normal_terminal(project_root=project, config=self.config)
            text_clean = " ".join(str(text or "").split())[:2000]
            if text_clean:
                with self._lock:
                    self._pending_new.append({"id": instance, "text": text_clean, "deadline": time.monotonic() + 60})
        return self._result(launch, "Starting a new MO terminal." + (" Its first request follows." if text else ""))

    def _deliver_new_assignments(self, mos: list[dict[str, Any]]) -> None:
        from core.design.terminal_handoff import queue_terminal_turn

        with self._lock:
            pending, self._pending_new = self._pending_new, []
        keep = []
        for item in pending:
            mo = next((m for m in mos if m.get("id") == item["id"]), None)
            if mo is not None:
                try:
                    queue_terminal_turn(item["text"], {"instance_id": mo["id"], "pid": mo["pid"]}, config=self.config)
                except Exception:
                    keep.append(item)
            elif time.monotonic() < item["deadline"]:
                keep.append(item)
        with self._lock:
            self._pending_new.extend(keep)

    def investigate(self, finding_id: str) -> dict[str, Any]:
        from core.systemcare.mo_care import recent_findings
        from mo_desktop.issue_report import launch_care_report_terminal

        finding = next((f for f in recent_findings(self.config) if f.get("id") == finding_id), None)
        if finding is None:
            return {"ok": False, "message": "That MO Care report is gone."}
        ok, message = launch_care_report_terminal(finding, config=self.config)
        return {"ok": ok, "message": message}

    def dismiss(self, finding_id: str) -> dict[str, Any]:
        from core.systemcare.mo_care import dismiss_finding

        return self._result(lambda: dismiss_finding(self.config, finding_id), "Dismissed.")
