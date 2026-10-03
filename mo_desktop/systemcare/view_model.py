"""Threaded adapter between the native surface and the SystemCare state machine."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Iterable

from core.systemcare.models import Plan, ProgressEvent, ScanMode
from core.systemcare.service import SystemCareError, SystemCareService
from core.systemcare.windows import ScanCancelled


GuiPost = Callable[[Callable[[], None]], Any]
ResultCallback = Callable[[Any, str], None]
ProgressCallback = Callable[[dict[str, Any]], None]


class SystemCareViewModel:
    """Runs bounded SystemCare work away from the UI and serializes local starts."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        post: GuiPost,
        service: SystemCareService | None = None,
    ) -> None:
        self.config = config if config is not None else {}
        self.service = service or SystemCareService(self.config)
        self._post = post
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel_event: threading.Event | None = None
        self._operation_id = ""
        self._resource_used = False
        self._resource_started_at = 0.0

    @property
    def busy(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def status(self) -> dict[str, Any]:
        return self.service.status()

    def history(self, limit: int = 12) -> list[dict[str, Any]]:
        return self.service.history(limit=limit)

    def latest_scan(self):
        return self.service.state.latest_scan()

    def start_calibration(
        self,
        *,
        force: bool = False,
        on_progress: ProgressCallback | None = None,
        on_result: ResultCallback | None = None,
    ) -> bool:
        return self._start(
            "calibration",
            lambda event: self.service.calibrate(
                force=force,
                progress=event,
                cancel_event=self._cancel_event,
            ),
            on_progress,
            on_result,
        )

    def start_scan(
        self,
        mode: ScanMode | str,
        *,
        advanced_domains: Iterable[str] | None = None,
        force_calibration: bool = False,
        all_checks: bool = False,
        on_progress: ProgressCallback | None = None,
        on_result: ResultCallback | None = None,
    ) -> bool:
        selected = ScanMode(str(getattr(mode, "value", mode) or "safe").lower())
        return self._start(
            "scan",
            lambda event: self.service.scan(
                selected,
                advanced_domains=advanced_domains,
                force_calibration=force_calibration,
                all_checks=all_checks,
                progress=event,
                cancel_event=self._cancel_event,
            ),
            on_progress,
            on_result,
        )

    def start_apply(
        self,
        plan: Plan,
        *,
        acknowledge_non_undo: bool,
        on_progress: ProgressCallback | None = None,
        on_result: ResultCallback | None = None,
    ) -> bool:
        return self._start(
            "apply",
            lambda event: self._apply(plan, acknowledge_non_undo, event),
            on_progress,
            on_result,
        )

    def _apply(self, plan: Plan, acknowledge: bool, progress: Any) -> Any:
        if any(step.requires_elevation for step in plan.steps) and not self.service.adapter._is_admin():
            from core.systemcare.elevated import run_elevated
            return run_elevated(self.config, "apply", {"plan_id": plan.plan_id, "plan_digest": plan.digest,
                                "acknowledge_non_undo": acknowledge}, cancel_event=self._cancel_event, progress=progress)
        return self.service.apply(plan.plan_id, plan.digest, acknowledge_non_undo=acknowledge,
                                  progress=progress, cancel_event=self._cancel_event)

    def start_inspection(self, context: str, section: str, target: str = "", *,
                         on_result: ResultCallback | None = None) -> bool:
        def inspect(_event: Any) -> Any:
            if context == "machine" and section in {"integrity", "component_store", "driver_packages", "filesystem"} and not self.service.adapter._is_admin():
                from core.systemcare.elevated import run_elevated
                return run_elevated(self.config, "inspect", {"context": context, "section": section, "target": target if section == "filesystem" else ""}, cancel_event=self._cancel_event)
            return self.service.inspect(context, section, target, cancel_event=self._cancel_event)
        return self._start("inspection", inspect, None, on_result)

    def start_restore(self, step_id: str, *, on_result: ResultCallback | None = None) -> bool:
        def restore(_event: Any) -> Any:
            row = self.service.state.backup(step_id)
            if row and (row["subject"].get("hive") == "HKLM" or row["subject"].get("root_key") == "startup_all" or row["action"].startswith(("service_", "task_"))) and not self.service.adapter._is_admin():
                from core.systemcare.elevated import run_elevated
                return run_elevated(self.config, "restore", {"step_id": step_id}, cancel_event=self._cancel_event)
            return self.service.restore(step_id)
        return self._start("restore", restore, None, on_result)

    def build_plan(self, finding_ids: Iterable[str], *, scan_id: str = "") -> Plan:
        return self.service.build_plan(finding_ids, scan_id=scan_id)

    def cancel(self) -> bool:
        event = self._cancel_event
        if event is not None:
            event.set()
            if self._operation_id:
                self.service.cancel(self._operation_id)
            return True
        if self.busy:
            # Owned work has released its operation and is delivering its
            # result. Do not cancel a peer that acquired the shared lock next.
            return False
        return bool(self.service.cancel())

    def _emit_resource(
        self,
        transition: str,
        reason: str = "",
        *,
        owned_threads: int | None = None,
    ) -> None:
        from core.runtime.resource_events import emit_component_resource_event

        emit_component_resource_event(
            "desktop_systemcare",
            transition,
            owned_thread_count=(int(self.busy) if owned_threads is None else owned_threads),
            elapsed_seconds=(
                time.monotonic() - self._resource_started_at
                if self._resource_started_at else 0.0
            ),
            reason=reason,
        )

    def _start(
        self,
        label: str,
        work: Callable[[Callable[[ProgressEvent], None]], Any],
        on_progress: ProgressCallback | None,
        on_result: ResultCallback | None,
    ) -> bool:
        with self._lock:
            if self.busy:
                return False
            self._cancel_event = threading.Event()
            self._resource_started_at = time.monotonic()

            def report(event: ProgressEvent | dict[str, Any]) -> None:
                payload = event.to_dict() if isinstance(event, ProgressEvent) else dict(event)
                self._operation_id = str(payload.get("operation_id") or "")
                if on_progress is not None:
                    self._post(lambda payload=payload: on_progress(payload))

            def run() -> None:
                result: Any = None
                error = ""
                crashed = False
                try:
                    self._emit_resource("ready")
                    if not self._resource_used:
                        self._resource_used = True
                        self._emit_resource("first_use", label)
                    result = work(report)
                except ScanCancelled:
                    result = {"state": "cancelled", "detail": "Operation stopped; no completed result was published."}
                except SystemCareError as exc:
                    error = str(exc)
                except Exception as exc:  # Surface boundary; core owns detail.
                    crashed = True
                    error = f"{type(exc).__name__}: {exc}"
                finally:
                    self._operation_id = ""
                    self._cancel_event = None
                    self._emit_resource("crash" if crashed else "stop", label)
                    if on_result is not None:
                        self._post(lambda result=result, error=error: on_result(result, error))

            thread = threading.Thread(
                target=run,
                name=f"mo-systemcare-{label}",
                daemon=True,
            )
            self._thread = thread
            self._emit_resource("start", label, owned_threads=1)
            thread.start()
            return True
