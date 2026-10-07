"""Single SystemCare calibration, scan, plan, apply and receipt state machine."""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Any, Callable, Iterable

from .catalog import RuleSpec, rule_for_id, rules_for_mode
from .config import normalized_systemcare_preferences
from .models import (
    CALIBRATION_SCHEMA_VERSION,
    Calibration,
    Domain,
    Finding,
    OperationState,
    Plan,
    PlanStep,
    ProgressEvent,
    Receipt,
    ScanMode,
    ScanResult,
    Severity,
    UndoQuality,
    canonical_digest,
    new_id,
)
from .state import OBSERVATION_FRESHNESS_SECONDS, SystemCareOperationBusy, SystemCareState
from .windows import ScanCancelled, WindowsSystemCareAdapter


ProgressCallback = Callable[[ProgressEvent], None]


class SystemCareError(RuntimeError):
    pass


class SystemCareService:
    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        adapter: Any | None = None,
        state: SystemCareState | None = None,
    ):
        self.config = config if config is not None else {}
        self.state = state or SystemCareState(self.config)
        self.adapter = adapter or WindowsSystemCareAdapter(self.config)

    def status(self, *, progress_only: bool = False) -> dict[str, Any]:
        active = self.state.active_operation()
        activity = {
            "active": bool(active and not active.get("stale")),
            "active_operation": {
                "operation_id": str(active.get("operation_id") or "")[:80],
                "kind": str(active.get("kind") or "")[:32],
                "started_at": float(active.get("started_at") or 0.0),
                "context": str(active.get("context") or ""),
                "section": str(active.get("section") or ""),
            } if active and not active.get("stale") else {},
        }
        if progress_only:
            return activity
        calibration = self.state.latest_calibration()
        scan = self.state.latest_scan()
        receipt = self.state.latest_receipt()
        # Status is a cached projection. The next explicit calibration, scan, or
        # apply performs the lightweight live fingerprint check.
        needs_calibration, reason = self.calibration_needed(calibration, verify_machine=False)
        return {
            "available": bool(getattr(self.adapter, "supported", False)),
            "state": str(active.get("kind") or (scan.state.value if scan else "not_scanned")),
            **activity,
            "calibration": calibration.public_summary() if calibration else None,
            "calibration_needed": needs_calibration,
            "calibration_reason": reason,
            "last_scan": scan.to_dict(include_private=False) if scan else None,
            "last_receipt": receipt.to_dict() if receipt else None,
            "preferences": self.preference_summary(),
            "game_session": self.game_session(),
            "safety": {
                "scan_mutates_windows": False,
                "personal_root_indexed": False,
                "generic_registry_cleaning": False,
                "apply_requires_exact_fresh_plan": True,
            },
        }

    def workspace(self, context: str = "machine", target: str = "") -> dict[str, Any]:
        """Cached UI projection; opening never starts a deep scan or provider turn."""
        self._validate_context(context)
        observations = self.state.observations(context, target)
        if context == "machine" and target:
            observations = {**self.state.observations(context), **observations}
        status = self.status()
        session = status["game_session"] if context == "machine" else {"state": "inactive"}
        return {"context": context, "target": target, "status": status,
                "observations": observations, "history": self.history() if context == "machine" else [],
                "receipts": self.state.recent_receipts() if context == "machine" else [],
                "game_active": session["state"] in {"active", "recovery_required"},
                "game_state": session["state"],
                "game_session": session,
                "recovery": [{"step_id": r["step_id"], "action": r["action"], "state": r["state"], "at": r["at"],
                              "name": r["subject"].get("name") or r["subject"].get("key") or r["subject"].get("kind", "")}
                             for r in self.state.recovery_rows()
                             if context == "machine" and (r["action"].startswith(("service_", "startup_", "task_", "registry_")) or r["action"] in {"game_on", "path_remove_duplicate"})
                             and r["state"] != "restored"],
                "preferences": self.preference_summary(),
                "evidence_freshness_seconds": OBSERVATION_FRESHNESS_SECONDS,
                "scan_scopes": {mode.value: [{"rule_id": r.rule_id, "title": r.title, "domain": r.domain.value,
                                             "section": r.scanner.removeprefix("inspection:") if r.scanner.startswith("inspection:") else ""}
                                            for r in self._scan_rules(mode, all_checks=True)]
                                for mode in ScanMode} if context == "machine" else {}}

    @staticmethod
    def _validate_context(context: str) -> None:
        if context not in {"machine", "mo", "server", "projects"}:
            raise SystemCareError("Unknown SystemCare maintenance target")

    def inspect(self, context: str, section: str, target: str = "", *, cancel_event: Any = None) -> dict[str, Any]:
        """Inspect one selected surface through its existing owner, and retain its evidence."""
        self._validate_context(context)
        if context == "machine" and section not in {"large", "duplicates", "empty", "filesystem"}:
            target = ""
        operation_id = new_id("inspect")
        def cancelled() -> bool:
            return bool((cancel_event is not None and cancel_event.is_set()) or self.state.cancel_requested(operation_id))
        with self._operation(operation_id, "inspecting", context=context, section=section):
            return self._inspect(context, section, target, cancelled=cancelled)

    def _inspect(self, context: str, section: str, target: str, *, cancelled: Callable[[], bool],
                 update_result: dict[str, Any] | None = None) -> dict[str, Any]:
        """Existing inspection dispatch/publication, inside the caller's operation lock."""
        if context == "machine":
            from .inspection import resource_snapshot, windows_inventory
            if section == "resources":
                result = {"state": "measured", "resources": resource_snapshot(self.adapter), "at": time.time()}
            elif section == "software_updates":
                from .inspection import software_updates
                result = software_updates(self.adapter, cancelled=cancelled)
            elif section == "driver_updates":
                from .checks import windows_updates
                result = windows_updates(self.adapter, drivers=True, inventory=update_result, cancelled=cancelled)
            elif section == "registry":
                from .registry import inspect_registry
                result = inspect_registry(self.adapter, cancelled=cancelled)
            elif section == "browser_data":
                from .browser_care import inspect_browsers
                result = inspect_browsers(self.adapter, cancelled=cancelled)
            elif section == "recycle":
                from .recycle import inspect_recycle
                result = inspect_recycle(self.adapter, cancelled=cancelled)
            elif section == "app_leftovers":
                from .storage import inspect_app_leftovers
                result = inspect_app_leftovers(self.adapter, self.state, cancelled=cancelled)
            elif section in {"integrity", "component_store", "updates", "environment", "desktop", "protection", "network", "filesystem"}:
                from .checks import native_check
                result = native_check(self.adapter, section, cancelled=cancelled, target=target,
                                      **({"update_result": update_result} if section == "updates" else {}))
            elif section in {"large", "duplicates", "empty"}:
                from .storage import inspect_storage
                result = inspect_storage(target, self.config, section, cancelled=cancelled)
            else:
                result = windows_inventory(self.adapter, section, cancelled=cancelled)
                if section == "startup":
                    tasks = windows_inventory(self.adapter, "tasks", cancelled=cancelled)
                    result["rows"] += [{**r, "source": "task", "name": r["TaskName"], "state": r["State"]} for r in tasks["rows"]]
                    from .inspection import startup_shortcuts
                    shortcuts = startup_shortcuts(self.adapter, cancelled=cancelled)
                    result["rows"] += shortcuts["rows"]
                    result["shortcut_coverage"] = {k: v for k, v in shortcuts.items() if k != "rows"}
                    result["coverage"] = "Run registrations, startup-trigger tasks and bounded Windows folder shortcuts; registration presence is distinct from startup approval and measured impact"
                    from .inspection import startup_measurements
                    result["measurements"] = startup_measurements(self.adapter, cancelled=cancelled)
                    result["state"] = "partial"
        elif context == "mo":
            from .mo_care import inspect_mo
            result = inspect_mo(self.config, section, cancelled=cancelled)
        else:
            from .targets import inspect_target
            result = inspect_target(context, section, target, self.config, self.adapter, cancelled=cancelled)
        if cancelled():
            raise ScanCancelled("inspection cancelled before publication")
        self.state.save_observation(context, "" if context == "machine" and section == "filesystem" else target, section, result)
        self.state.append_log("inspection_finished", context=context, section=section, state=result.get("state"),
                              rows=len(result.get("rows") or []))
        return result

    def build_action_plan(self, action: str, section: str = "", indices: Iterable[int] = (), *,
                          context: str = "machine", target: str = "", observed_at: float | None = None,
                          finding_ids: Iterable[str] = (), scan_id: str = "") -> Plan:
        """Plan only persisted selected evidence or a fixed explicit quick action."""
        self._validate_context(context)
        if context != "machine":
            raise SystemCareError("This action belongs to the machine maintenance owner")
        rule = rule_for_id("action." + action)
        target = ""
        from .actions import capture
        operation_id = new_id("plan")
        with self._operation(operation_id, "planning"):
            calibration = self._ensure_calibration_inside_scan(force=False, progress=None,
                                                               cancel_event=None, outer_operation_id=operation_id)
            subjects = []
            if action in {"desktop_refresh", "game_on", "game_off", "network_dns"}:
                subject = {"kind": "game"} if action.startswith("game_") else {"kind": "native", "owner": action}
                if action == "game_on":
                    options = self.preference_summary()["game_mode"]
                    subject.update(options)
                    subject.update({
                        "session_id": new_id("game-session"),
                        "calibration_id": calibration.calibration_id,
                        "hardware": {
                            "cpu_logical": calibration.cpu_logical,
                            "memory_bytes": calibration.memory_bytes,
                            "gpu_names": list(calibration.gpu_names),
                            "battery_present": bool(calibration.capabilities.get("battery")),
                        },
                    })
                    if options["power_guid"]:
                        from .inspection import power_schemes
                        if not any(r["guid"] == options["power_guid"] for r in power_schemes(self.adapter)):
                            raise SystemCareError("Selected Game Mode power scheme is no longer available")
                if action == "game_off":
                    recovery = self.state.active_game()
                    if recovery is None:
                        raise SystemCareError("Game Mode has no active captured original to restore")
                    subject["recovery_step"] = recovery["step_id"]
                subjects.append(subject)
            else:
                observation = self.state.observations(context, target).get(section)
                if not observation or observation["stale"] or observation["state"] not in {"measured", "partial"}:
                    raise SystemCareError("Inspect current evidence before planning this action")
                if observed_at is not None and float(observed_at) != observation["observed_at"]:
                    raise SystemCareError("Inspection changed since selection; review its current items")
                rows = observation.get("rows") or []
                selected = list(dict.fromkeys(int(i) for i in indices))
                if action in {"integrity_repair", "component_repair", "component_cleanup"}:
                    expected_section = "integrity" if action == "integrity_repair" else "component_store"
                    if section != expected_section:
                        raise SystemCareError("Check the action's Windows owner first")
                    subjects.append({"kind": "native", "owner": action})
                else:
                    if not selected or len(selected) > 100 or any(i < 0 or i >= len(rows) for i in selected):
                        raise SystemCareError("Select exact current items")
                    for i in selected:
                        row = rows[i]
                        if action.startswith("service_") and section == "services":
                            subject = {"kind": "service", "name": row["Name"]}
                        elif action.startswith("task_") and section == "startup" and row.get("source") == "task":
                            subject = {"kind": "task", "name": row["TaskName"], "path": row["TaskPath"]}
                        elif action.startswith("startup_") and section == "startup" and row.get("source") == "registry":
                            subject = {"kind": "startup", **{k: row[k] for k in ("hive", "key", "value_name")}}
                        elif action == "startup_shortcut_disable" and section == "startup" and row.get("source") == "folder":
                            subject = {"kind": "startup_file", "name": row["name"], "root_key": row["root_key"]}
                        elif action == "registry_remove" and section == "registry" and row.get("eligible"):
                            subject = dict(row["subject"])
                        elif action == "path_remove_duplicate" and section == "environment" and row.get("state") == "duplicate":
                            from .registry import environment_subject
                            subject = {**environment_subject(row["scope"]), "name": row["name"], "index": row["index"]}
                        elif action == "disk_optimize" and section == "storage":
                            subject = {"kind": "volume", "drive": row["DeviceID"]}
                        elif action == "app_uninstall" and section == "apps" and not row.get("SystemComponent"):
                            subject = {"kind": "app", "name": row["DisplayName"], "hive": row["hive"], "key": row["key"]}
                        elif action == "browser_history_clear" and section == "browser_data" and row.get("eligible"):
                            subject = dict(row["subject"])
                        elif action == "recycle_remove" and section == "recycle" and row.get("eligible"):
                            subject = dict(row["subject"])
                        elif action == "app_leftover_remove" and section == "app_leftovers" and row.get("eligible"):
                            subject = dict(row["subject"])
                        elif action == "app_uninstall" and section == "packaged_apps":
                            from .actions import packaged_app_removable
                            if not packaged_app_removable(row):
                                raise SystemCareError("Protected or unproven Windows packages are inspect-only")
                            subject = {"kind": "appx", "name": row["Name"], "identity": row["PackageFullName"], "scope": "current-user"}
                        elif action == "app_update" and section == "software_updates":
                            subject = {"kind": "package", "name": row["DisplayName"], "identity": row["Id"], "source": row["Source"], "version": row["Available"]}
                        elif action == "driver_remove" and section == "driver_packages" and row.get("InUse") is False and row.get("Inbox") is False and row.get("BootCritical") is False:
                            subject = {"kind": "driver", "name": row["Driver"], "inf": row["Driver"]}
                        elif action == "windows_update" and section in {"updates", "driver_updates"} and row.get("eula_accepted") is True:
                            subject = {"kind": "update", "name": row["name"], "identity": row["update_id"], "revision": row["revision"]}
                        else:
                            raise SystemCareError(f"Inspection row {i} is not eligible for {action}; use indices from the full {section} inspection")
                        subjects.append(subject)
            now = time.time()
            steps = tuple(PlanStep(new_id("step"), "", rule.rule_id, rule.title, rule.action, rule.risk,
                                   rule.undo, 0, rule.requires_elevation or subject.get("hive") == "HKLM" or subject.get("root_key") == "startup_all", rule.requires_restart, (),
                                    {**subject, "original": capture(self.adapter, subject, state=self.state)}) for subject in subjects)
            if scan_id:
                scan = self.state.latest_scan()
                if scan is None or scan.scan_id != scan_id or scan.calibration_id != calibration.calibration_id:
                    raise SystemCareError("Scan changed since selection; scan and review again")
                steps = (*self._finding_steps(scan, finding_ids), *steps)
            plan = Plan(new_id("plan"), scan_id, calibration.calibration_id, now,
                        now + self.preference_summary()["plan_ttl_minutes"] * 60, steps).with_digest()
            self.state.save_plan(plan)
            return plan

    def restore(self, step_id: str) -> dict[str, Any]:
        from .actions import restore
        with self._operation(new_id("restore"), "restoring"):
            result = restore(self.adapter, self.state, step_id)
            now = time.time()
            self.state.save_receipt(Receipt(new_id("receipt"), "restore-" + step_id, OperationState.COMPLETED,
                                            now, now, (step_id,), (), (), 0, True, False, "Captured original passed its restoration check."))
            return result

    def calibration_needed(
        self,
        calibration: Calibration | None = None,
        *,
        verify_machine: bool = True,
    ) -> tuple[bool, str]:
        current = calibration if calibration is not None else self.state.latest_calibration()
        if current is None:
            return True, "first run"
        if current.schema_version != CALIBRATION_SCHEMA_VERSION:
            return True, "calibration schema changed"
        if current.refresh_after <= time.time():
            return True, "calibration expired"
        if not verify_machine:
            return False, "current; live shape is checked before scan or apply"
        try:
            fingerprint = str(self.adapter.probe_fingerprint())
        except Exception:
            return True, "machine fingerprint could not be verified"
        if fingerprint != current.fingerprint:
            return True, "Windows or hardware shape changed"
        return False, "current"

    def calibrate(
        self,
        *,
        force: bool = False,
        progress: ProgressCallback | None = None,
        cancel_event: Any | None = None,
    ) -> Calibration:
        current = self.state.latest_calibration()
        needed, _reason = self.calibration_needed(current)
        if current is not None and not force and not needed:
            return current
        operation_id = new_id("calibration")
        cancelled, on_stage = self._calibration_callbacks(
            operation_id, progress=progress, cancel_event=cancel_event,
        )

        with self._operation(operation_id, "calibrating"):
            try:
                calibration = self.adapter.calibrate(progress=on_stage, cancelled=cancelled)
            except ScanCancelled as exc:
                self.state.append_log("calibration_cancelled", operation_id=operation_id)
                raise SystemCareError(str(exc)) from exc
            except Exception as exc:
                self.state.append_log("calibration_failed", operation_id=operation_id, error=type(exc).__name__)
                raise SystemCareError(f"SystemCare calibration failed: {type(exc).__name__}: {exc}") from exc
            self.state.save_calibration(calibration)
            self.state.append_log("calibration_completed", operation_id=operation_id, calibration_id=calibration.calibration_id)
            return calibration

    def scan(
        self,
        mode: ScanMode | str = ScanMode.SAFE,
        *,
        progress: ProgressCallback | None = None,
        cancel_event: Any | None = None,
        force_calibration: bool = False,
        advanced_domains: Iterable[str] | None = None,
        all_checks: bool = False,
    ) -> ScanResult:
        selected_mode = _scan_mode(mode)
        if not bool(getattr(self.adapter, "supported", False)):
            raise SystemCareError("MO SystemCare is available only on Windows")
        scan_id = new_id("scan")
        started = time.time()
        findings: list[Finding] = []
        domains: dict[str, str] = {}
        sequence = 0
        rules = self._scan_rules(selected_mode, advanced_domains, all_checks=all_checks)
        checks = [{"rule_id": rule.rule_id, "title": rule.title, "domain": rule.domain.value,
                   "section": rule.scanner.removeprefix("inspection:") if rule.scanner.startswith("inspection:") else "", "state": "pending", "at": 0}
                  for rule in rules]
        domains = {rule.domain.value: "pending" for rule in rules}
        total_weight = max(1, sum(max(1, rule.weight) for rule in rules))
        completed_weight = 0
        update_result = None

        def cancelled() -> bool:
            return bool(
                (cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)())
                or self.state.cancel_requested(scan_id)
            )

        def emit(rule: RuleSpec, label: str, *, state: str, domain_fraction: float, before: bool = False) -> None:
            nonlocal sequence
            sequence += 1
            current_weight = completed_weight if before else completed_weight
            self._emit(progress, ProgressEvent(
                operation_id=scan_id,
                phase="scan",
                domain=rule.domain,
                label=label,
                completed_weight=current_weight,
                total_weight=total_weight,
                domain_fraction=domain_fraction,
                state=state,
                sequence=sequence,
                checks=tuple(dict(check) for check in checks),
            ))

        with self._operation(scan_id, "scanning", context="machine"):
            try:
                calibration = self._ensure_calibration_inside_scan(
                    force=force_calibration,
                    progress=progress,
                    cancel_event=cancel_event,
                    outer_operation_id=scan_id,
                )
                for index, rule in enumerate(rules):
                    if cancelled():
                        raise ScanCancelled("scan cancelled")
                    current_check = checks[index]
                    current_check.update(state="active", started_at=time.time())
                    check_started = time.monotonic()
                    domains[rule.domain.value] = "active"
                    emit(rule, rule.title, state="active", domain_fraction=0.0, before=True)
                    section = current_check["section"]
                    if section:
                        try:
                            if section in {"updates", "driver_updates"} and update_result is None:
                                from .checks import update_inventory
                                try:
                                    update_result = update_inventory(self.adapter, cancelled=cancelled)
                                except ScanCancelled:
                                    raise
                                except Exception as exc:
                                    update_result = {"error": f"{type(exc).__name__}: {exc}"[:280]}
                            observed = self._inspect("machine", section, "", cancelled=cancelled,
                                                     **({"update_result": update_result} if section in {"updates", "driver_updates"} else {}))
                        except ScanCancelled:
                            raise
                        except Exception as exc:
                            observed = {"state": "failed", "rows": [], "at": time.time(),
                                        "detail": f"{type(exc).__name__}: {exc}"[:280]}
                            self.state.save_observation("machine", "", section, observed)
                        finding = Finding(finding_id=new_id("finding"), scan_id=scan_id, rule_id=rule.rule_id,
                                          domain=rule.domain, title=rule.title, summary=observed.get("detail", "Scoped inspection"),
                                          severity=Severity.REVIEW if observed.get("state") in {"unavailable", "failed"} else Severity.INFO,
                                          item_count=len(observed.get("rows") or []),
                                          evidence={"coverage_state": observed.get("state", "partial"), "section": section})
                        outcome = {"measured": "complete", "partial": "limited"}.get(observed.get("state"), "unavailable")
                        if observed.get("state") == "failed":
                            outcome = "failed"
                    else:
                        finding = self.adapter.scan_rule(rule, calibration, scan_id, cancelled=cancelled)
                        evidence = finding.evidence
                        if evidence.get("available") is False:
                            outcome = "unavailable"
                        elif evidence.get("protected_read_ready") is False or any(
                                evidence.get(key) for key in ("bounded", "protected", "active_owner_excluded")):
                            outcome = "limited"
                        else:
                            outcome = "complete"
                    current_check.update(state=outcome, at=time.time(), detail=finding.summary,
                                         elapsed_ms=round((time.monotonic() - check_started) * 1000))
                    self.state.append_log("scan_check_finished", scan_id=scan_id, rule_id=rule.rule_id,
                                          state=outcome, elapsed_ms=current_check["elapsed_ms"])
                    findings.append(finding)
                    completed_weight += max(1, rule.weight)
                    if all(check["state"] != "pending" for check in checks if check["domain"] == rule.domain.value):
                        domains[rule.domain.value] = "complete"
                    emit(rule, rule.title, state="complete", domain_fraction=1.0)
                result = ScanResult(
                    scan_id=scan_id,
                    mode=selected_mode,
                    state=OperationState.READY,
                    calibration_id=calibration.calibration_id,
                    started_at=started,
                    completed_at=time.time(),
                    findings=tuple(findings),
                    domains=domains,
                    checks=tuple(checks),
                )
                self.state.save_scan(result)
                self.state.append_log(
                    "scan_completed",
                    scan_id=scan_id,
                    mode=selected_mode.value,
                    findings=len(findings),
                    review=result.review_count,
                )
                return result
            except ScanCancelled as exc:
                for check in checks:
                    if check["state"] == "active":
                        check.update(state="cancelled", at=time.time(), detail=str(exc)[:280])
                result = ScanResult(
                    scan_id=scan_id,
                    mode=selected_mode,
                    state=OperationState.CANCELLED,
                    calibration_id=(self.state.latest_calibration().calibration_id if self.state.latest_calibration() else ""),
                    started_at=started,
                    completed_at=time.time(),
                    findings=tuple(findings),
                    domains={**domains, **{key: ("cancelled" if value == "active" else value) for key, value in domains.items()}},
                    error=str(exc),
                    checks=tuple(checks),
                )
                self.state.save_scan(result)
                self.state.append_log("scan_cancelled", scan_id=scan_id, findings=len(findings))
                return result
            except Exception as exc:
                for check in checks:
                    if check["state"] == "active":
                        check.update(state="failed", at=time.time(), detail=f"{type(exc).__name__}: {exc}"[:280])
                result = ScanResult(
                    scan_id=scan_id,
                    mode=selected_mode,
                    state=OperationState.FAILED,
                    calibration_id=(self.state.latest_calibration().calibration_id if self.state.latest_calibration() else ""),
                    started_at=started,
                    completed_at=time.time(),
                    findings=tuple(findings),
                    domains={key: "failed" if value == "active" else value for key, value in domains.items()},
                    error=f"{type(exc).__name__}: {exc}",
                    checks=tuple(checks),
                )
                self.state.save_scan(result)
                self.state.append_log("scan_failed", scan_id=scan_id, error=type(exc).__name__)
                raise SystemCareError(f"SystemCare scan failed: {type(exc).__name__}: {exc}") from exc

    def build_plan(
        self,
        finding_ids: Iterable[str],
        *,
        scan_id: str = "",
    ) -> Plan:
        selected_ids = tuple(dict.fromkeys(str(item or "").strip() for item in finding_ids if str(item or "").strip()))
        if not selected_ids:
            raise SystemCareError("Select at least one actionable SystemCare finding")
        scan = self.state.scan(scan_id) if scan_id else self.state.latest_scan(complete_only=True)
        if scan is None or scan.state not in {OperationState.READY, OperationState.COMPLETED}:
            raise SystemCareError("A completed SystemCare scan is required before planning")
        steps = self._finding_steps(scan, selected_ids)
        now = time.time()
        ttl = int(self.preference_summary()["plan_ttl_minutes"])
        plan = Plan(
            plan_id=new_id("plan"), scan_id=scan.scan_id, calibration_id=scan.calibration_id,
            created_at=now, expires_at=now + ttl * 60, steps=steps,
        ).with_digest()
        self.state.save_plan(plan)
        self.state.append_log("plan_created", plan_id=plan.plan_id, scan_id=scan.scan_id, steps=len(steps))
        return plan

    def _finding_steps(self, scan: ScanResult, selected_ids: Iterable[str]) -> tuple[PlanStep, ...]:
        if scan.state not in {OperationState.READY, OperationState.COMPLETED}:
            raise SystemCareError("A completed SystemCare scan is required before planning")
        selected_ids = tuple(dict.fromkeys(selected_ids))
        by_id = {item.finding_id: item for item in scan.findings}
        unknown = [item for item in selected_ids if item not in by_id]
        if unknown:
            raise SystemCareError("One or more findings do not belong to the selected scan")
        steps: list[PlanStep] = []
        for finding_id in selected_ids:
            finding = by_id[finding_id]
            if not finding.selectable or not finding.candidates or finding.action == "review":
                raise SystemCareError(f"{finding.title} is advisory and cannot be applied")
            try:
                rule = rule_for_id(finding.rule_id)
            except ValueError as exc:
                raise SystemCareError("Finding no longer has a catalog owner") from exc
            if not rule.selectable or rule.action != finding.action or rule.domain != finding.domain:
                raise SystemCareError("Finding action no longer matches the catalog")
            steps.append(PlanStep(
                step_id=new_id("step"),
                finding_id=finding.finding_id,
                rule_id=finding.rule_id,
                title=rule.title,
                action=rule.action,
                risk=rule.risk,
                undo=rule.undo,
                expected_bytes=sum(candidate.size_bytes for candidate in finding.candidates),
                requires_elevation=rule.requires_elevation,
                requires_restart=rule.requires_restart,
                candidates=finding.candidates,
            ))
        return tuple(steps)

    def build_repair_plan(self, scan_id: str, *, registry_observed_at: float | None = None) -> Plan:
        """Review all actionable scan findings through the existing exact plan owners."""
        scan = self.state.latest_scan()
        if scan is None or scan.scan_id != scan_id or scan.state not in {OperationState.READY, OperationState.COMPLETED}:
            raise SystemCareError("Complete a current scan before reviewing all repairs")
        selected = [f.finding_id for f in scan.findings if f.selectable and f.candidates and f.action != "review"]
        registry = self.state.observations("machine").get("registry", {})
        if registry_observed_at is not None:
            if registry.get("observed_at") != registry_observed_at or registry.get("stale"):
                raise SystemCareError("Registry inspection changed or expired; inspect and review again")
            indices = [i for i, row in enumerate(registry.get("rows") or []) if row.get("eligible")]
            if len(indices) > 100:
                raise SystemCareError("Review registry selections in batches of at most 100 exact values")
            if indices:
                return self.build_action_plan("registry_remove", "registry", indices, observed_at=registry_observed_at,
                                              finding_ids=selected, scan_id=scan_id)
        return self.build_plan(selected, scan_id=scan_id)

    def _validate_apply(self, plan_id: str, plan_digest: str, acknowledge_non_undo: bool) -> tuple[Plan, Calibration]:
        plan = self.state.plan(plan_id)
        if plan is None:
            raise SystemCareError("SystemCare plan was not found")
        prior_receipt = self.state.receipt_for_plan(plan.plan_id)
        if prior_receipt is not None:
            raise SystemCareError(
                f"SystemCare plan already has a {prior_receipt.state.value} receipt; it cannot be applied twice"
            )
        expected_digest = canonical_digest(plan.digest_payload())
        if plan.digest != expected_digest or str(plan_digest or "").strip().lower() != expected_digest:
            raise SystemCareError("SystemCare plan digest is missing, changed, or stale")
        self.validate_plan_catalog(plan)
        if plan.expires_at <= time.time():
            raise SystemCareError("SystemCare plan expired; scan and review again")
        if any(step.undo == UndoQuality.NONE for step in plan.steps) and not acknowledge_non_undo:
            raise SystemCareError("This plan has no exact undo; explicit non-undo acknowledgment is required")
        latest_scan = self.state.latest_scan(complete_only=True)
        if plan.scan_id and (latest_scan is None or latest_scan.scan_id != plan.scan_id):
            raise SystemCareError("SystemCare plan is not based on the latest verified scan")
        calibration = self.state.latest_calibration()
        if calibration is None or calibration.calibration_id != plan.calibration_id:
            raise SystemCareError("SystemCare calibration changed; scan and review again")
        needed, reason = self.calibration_needed(calibration)
        if needed:
            raise SystemCareError(f"SystemCare calibration is stale ({reason}); scan and review again")
        if any(step.requires_elevation for step in plan.steps) and not getattr(self.adapter, "_is_admin", lambda: False)():
            raise SystemCareError("This selected Windows action requires an elevated SystemCare process")
        return plan, calibration

    def apply(
        self,
        plan_id: str,
        plan_digest: str,
        *,
        acknowledge_non_undo: bool = False,
        progress: ProgressCallback | None = None,
        cancel_event: Any | None = None,
    ) -> Receipt:
        operation_id = new_id("apply")
        started = time.time()
        applied: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []
        reclaimed = 0

        def cancelled() -> bool:
            return bool(
                (cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)())
                or self.state.cancel_requested(operation_id)
            )

        with self._operation(operation_id, "applying"):
            # Freshness and prior receipts must be checked under the same lock
            # as mutation; another process can finish work before we acquire it.
            plan, calibration = self._validate_apply(plan_id, plan_digest, acknowledge_non_undo)
            total = max(1, len(plan.steps))
            checks = [{"rule_id": step.rule_id, "step_id": step.step_id, "title": step.title,
                       "finding_id": step.finding_id,
                       "domain": rule_for_id(step.rule_id).domain.value, "state": "pending", "at": 0}
                      for step in plan.steps]
            try:
                for index, step in enumerate(plan.steps):
                    if cancelled():
                        raise ScanCancelled("apply cancelled at a safe boundary")
                    checks[index].update(state="active", started_at=time.time())
                    self._emit(progress, ProgressEvent(
                        operation_id=operation_id,
                        phase="apply",
                        domain=rule_for_id(step.rule_id).domain,
                        label=step.title,
                        completed_weight=index,
                        total_weight=total,
                        domain_fraction=0.0,
                        state="active",
                        sequence=index * 2 + 1,
                        checks=tuple(dict(check) for check in checks),
                    ))
                    from core.runtime.backend_monitor import monitor_context
                    step_started, step_error = time.monotonic(), ""
                    summary: dict[str, Any] = {}
                    try:
                        with monitor_context(step_id=step.step_id, action=step.action, rule_id=step.rule_id):
                            if step.subject:
                                from .actions import execute
                                result = execute(self.adapter, self.state, step, cancelled=cancelled)
                            else:
                                result = dict(self.adapter.apply_step(step, calibration, cancelled=cancelled))
                        summary = {"verified": bool(result.get("verified")), "failed": int(result.get("failed") or 0),
                                   "changed": int(result.get("deleted") or result.get("changed") or 0),
                                   "reclaimed_bytes": max(0, int(result.get("reclaimed_bytes") or 0))}
                    except BaseException as exc:
                        step_error = type(exc).__name__
                        raise
                    finally:
                        self.state.append_log("step_finished", operation_id=operation_id, step_id=step.step_id,
                                              action=step.action, rule_id=step.rule_id, error_type=step_error,
                                              elapsed_ms=int((time.monotonic() - step_started) * 1000), **summary)
                    reclaimed += max(0, int(result.get("reclaimed_bytes") or 0))
                    step_verified = bool(result.get("verified"))
                    if int(result.get("failed") or 0) or not step_verified:
                        failed.append(step.step_id)
                    elif int(result.get("deleted") or result.get("changed") or 0):
                        applied.append(step.step_id)
                    else:
                        skipped.append(step.step_id)
                    checks[index].update(state="complete" if step_verified else "failed", at=time.time(),
                                         detail="Applied and verified" if step.step_id in applied else
                                                "No change needed; post-check passed" if step_verified else "Post-check failed",
                                         elapsed_ms=round((time.monotonic() - step_started) * 1000))
                    self._emit(progress, ProgressEvent(
                        operation_id=operation_id,
                        phase="verify",
                        domain=rule_for_id(step.rule_id).domain,
                        label=f"{'Verified' if step_verified else 'Verification failed:'} {step.title}",
                        completed_weight=index + 1,
                        total_weight=total,
                        domain_fraction=1.0,
                        state="complete" if step_verified else "failed",
                        sequence=index * 2 + 2,
                        checks=tuple(dict(check) for check in checks),
                    ))
                state = OperationState.COMPLETED if not failed else OperationState.FAILED
                detail = "Every applied step passed its post-check." if not failed else "One or more steps failed verification; see the exact receipt."
            except ScanCancelled as exc:
                state = OperationState.CANCELLED
                detail = str(exc)
                for check in checks:
                    if check["state"] == "active":
                        check.update(state="cancelled", at=time.time(), detail=detail)
            except Exception as exc:
                state = OperationState.FAILED
                from core.runtime.backend_monitor import redact_monitor_text
                detail = f"SystemCare stopped during apply: {type(exc).__name__}: {redact_monitor_text(str(exc), 600)}. Review the partial receipt before any new plan."
                if plan.steps:
                    attempted = set(applied) | set(skipped) | set(failed)
                    current = next((step.step_id for step in plan.steps if step.step_id not in attempted), "")
                    if current:
                        failed.append(current)
                for check in checks:
                    if check["state"] == "active":
                        check.update(state="failed", at=time.time(), detail=detail)
            receipt = Receipt(
                receipt_id=new_id("receipt"),
                plan_id=plan.plan_id,
                state=state,
                created_at=started,
                completed_at=time.time(),
                applied_steps=tuple(applied),
                skipped_steps=tuple(skipped),
                failed_steps=tuple(failed),
                reclaimed_bytes=reclaimed,
                verified=state == OperationState.COMPLETED and not failed,
                rollback_available=any(step.step_id in (*applied, *failed) and step.undo in {UndoQuality.FULL, UndoQuality.PARTIAL}
                                       and self.state.backup(step.step_id) is not None for step in plan.steps),
                detail=detail,
                checks=tuple(checks),
            )
            self.state.save_receipt(receipt)
            self.state.append_log(
                "apply_finished",
                receipt_id=receipt.receipt_id,
                state=receipt.state.value,
                applied=len(applied),
                failed=len(failed),
                skipped=len(skipped),
                verified=receipt.verified,
            )
            return receipt

    def cancel(self, operation_id: str = "") -> bool:
        return self.state.request_cancel(operation_id)

    def history(self, *, limit: int = 12) -> list[dict[str, Any]]:
        """The Activity list's scan rows: what it shows, never the findings themselves."""
        return [{"scan_id": scan.scan_id, "mode": scan.mode.value, "state": scan.state.value,
                 "completed_at": float(scan.completed_at), "finding_count": len(scan.findings),
                 "reclaimable_bytes": scan.reclaimable_bytes, "review_count": scan.review_count}
                for scan in self.state.recent_scans_for_history(limit=limit)]

    def dashboard_status(self) -> dict[str, Any]:
        active = self.state.active_operation()
        scan = self.state.latest_scan()
        receipt = self.state.latest_receipt()
        if active and not active.get("stale"):
            state = "scanning" if active.get("kind") in {"scanning", "calibrating"} else "active"
            label = "Scan in progress" if state == "scanning" else "Maintenance in progress"
        elif scan is None:
            state, label = "not_scanned", "Not scanned"
        elif scan.state == OperationState.FAILED:
            state, label = "attention", "Last scan needs attention"
        elif scan.state == OperationState.CANCELLED:
            state, label = "ready", "Scan cancelled"
        elif scan.review_count:
            state, label = "review", f"{scan.review_count} item{'s' if scan.review_count != 1 else ''} to review"
        else:
            state, label = "ready", "No review items"
        return {
            "available": bool(getattr(self.adapter, "supported", False)),
            "state": state,
            "label": label,
            "reclaimable_bytes": scan.reclaimable_bytes if scan else 0,
            "last_scan_at": scan.completed_at if scan else 0.0,
            "active": bool(active and not active.get("stale")),
            "last_receipt_at": receipt.completed_at if receipt else 0.0,
        }

    def game_session(self) -> dict[str, Any]:
        """Project the durable Game Mode journal without starting background work."""
        active = self.state.active_game()
        row = active or self.state.latest_game()
        if row is None:
            return {
                "state": "inactive", "active": False, "session_id": "", "started_at": 0.0,
                "ended_at": 0.0, "duration_seconds": 0, "resource_state": "unknown",
                "metrics": {"baseline": {}, "current": {}, "sampled_at": 0.0, "stale": True},
                "items": [], "hardware": {}, "recovery_step_id": "",
            }

        journal_state = str(row.get("state") or "needs_review")
        session = row.get("session") if isinstance(row.get("session"), dict) else {}
        subject = row.get("subject") if isinstance(row.get("subject"), dict) else {}
        started_at = float(session.get("started_at") or row.get("at") or 0.0)
        ended_at = float(session.get("ended_at") or row.get("restored_at") or 0.0)
        if journal_state == "applied":
            state = "active"
        elif journal_state == "restored":
            state = "restored"
        else:
            state = "recovery_required"

        observation = self.state.observations("machine").get("resources", {}) if active else {}
        observed = observation.get("resources") if isinstance(observation.get("resources"), dict) else {}
        baseline = session.get("baseline") if isinstance(session.get("baseline"), dict) else {}
        final = session.get("final") if isinstance(session.get("final"), dict) else {}
        current = observed if active and observed else final

        def metric_view(value: dict[str, Any]) -> dict[str, Any]:
            total = value.get("memory_total")
            used = value.get("memory_used")
            percent = round(100.0 * used / total, 1) if isinstance(total, (int, float)) and total > 0 and isinstance(used, (int, float)) else None
            cpu = value.get("cpu_percent")
            return {
                "at": float(value.get("at") or 0.0),
                "cpu_percent": round(float(cpu), 1) if isinstance(cpu, (int, float)) else None,
                "memory_total": int(total) if isinstance(total, (int, float)) else None,
                "memory_used": int(used) if isinstance(used, (int, float)) else None,
                "memory_percent": percent,
            }

        baseline_view = metric_view(baseline)
        current_view = metric_view(current)
        cpu = current_view["cpu_percent"]
        memory = current_view["memory_percent"]
        resource_state = (
            "attention" if (cpu is not None and cpu >= 95) or (memory is not None and memory >= 90)
            else "watch" if (cpu is not None and cpu >= 85) or (memory is not None and memory >= 80)
            else "normal" if cpu is not None or memory is not None
            else "unknown"
        )
        item_state = "active" if state == "active" else "restored" if state == "restored" else "review"
        power_changed = bool(subject.get("power_guid"))
        schedules_paused = bool(subject.get("pause_schedules"))
        items = [
            {"id": "windows_game_mode", "label": "Windows Game Mode", "state": item_state, "enabled": True,
             "detail": "Enabled for this session" if state == "active" else "Original value restored" if state == "restored" else "Recovery requires review"},
            {"id": "power_scheme", "label": "Windows power plan", "state": item_state if power_changed else "unchanged",
             "enabled": power_changed, "detail": "Selected plan active" if state == "active" and power_changed else "Original plan restored" if state == "restored" and power_changed else "Kept unchanged"},
            {"id": "scheduled_work", "label": "MO scheduled work", "state": item_state if schedules_paused else "unchanged",
             "enabled": schedules_paused, "detail": "Paused without losing due work" if state == "active" and schedules_paused else "Scheduling resumed" if state == "restored" and schedules_paused else "Continues normally"},
        ]
        finish = ended_at or time.time()
        return {
            "state": state,
            "active": state == "active",
            "session_id": str(session.get("session_id") or row.get("step_id") or ""),
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_seconds": max(0, int(finish - started_at)) if started_at else 0,
            "resource_state": resource_state,
            "metrics": {
                "baseline": baseline_view,
                "current": current_view,
                "sampled_at": current_view["at"],
                "stale": bool(observation.get("stale", True)) if active else False,
            },
            "items": items,
            "hardware": dict(session.get("hardware") or subject.get("hardware") or {}),
            "recovery_step_id": str(row.get("step_id") or "") if active else "",
        }

    def preference_summary(self) -> dict[str, Any]:
        return normalized_systemcare_preferences(self.config)

    def _scan_rules(self, mode: ScanMode, requested: Iterable[str] | None = None, *, all_checks: bool = False) -> tuple[RuleSpec, ...]:
        selected = self._advanced_scope(requested)
        preferences = self.preference_summary()["advanced"]
        excluded = {"inspection:integrity": not preferences["system_integrity"],
                    "inspection:component_store": not preferences["component_store"],
                    "inspection:registry": not preferences["registry_advisory"]}
        return tuple(rule for rule in rules_for_mode(mode, advanced_domains=selected, all_checks=all_checks)
                     if self._category_enabled(rule.domain) and not excluded.get(rule.scanner, False))

    def _advanced_scope(self, requested: Iterable[str] | None) -> set[str]:
        if requested is not None:
            return {str(item).strip().lower() for item in requested if str(item).strip()}
        advanced = self.preference_summary()["advanced"]
        selected = set()
        if advanced["deep_storage"]:
            selected.update({
                "storage.windows_temp_advisory",
                "storage.delivery_optimization_advisory",
            })
        if advanced["component_store"]:
            selected.add("health.component_store")
        if advanced["system_integrity"]:
            selected.add("health.integrity")
        if advanced["service_advisory"]:
            selected.add("startup.services_advisory")
        if advanced["registry_advisory"]:
            selected.add("registry.startup_targets")
        return selected

    def _category_enabled(self, domain: Domain) -> bool:
        categories = self.preference_summary()["categories"]
        return bool(categories.get(domain.value, True))

    @staticmethod
    def validate_plan_catalog(plan: Plan) -> None:
        if not plan.steps:
            raise SystemCareError("SystemCare plan has no executable step")
        for step in plan.steps:
            try:
                rule = rule_for_id(step.rule_id)
            except ValueError as exc:
                raise SystemCareError("SystemCare plan contains an unknown catalog owner") from exc
            expected_bytes = sum(candidate.size_bytes for candidate in step.candidates)
            if step.subject:
                from .actions import validate_subject
                validate_subject(step.action, dict(step.subject))
                if rule.scanner != "selected_subject" or step.candidates or not step.subject.get("original") or step.expected_bytes:
                    raise SystemCareError("Selected action has no exact captured subject")
                candidate_matches = True
            else:
                if not plan.scan_id:
                    raise SystemCareError("File removal requires a completed scan")
                candidate_matches = bool(step.candidates) and all(candidate.root_key == rule.root_key for candidate in step.candidates)
            if (
                not rule.selectable
                or not candidate_matches
                or step.title != rule.title
                or step.action != rule.action
                or step.risk != rule.risk
                or step.undo != rule.undo
                or step.requires_elevation != (rule.requires_elevation or step.subject.get("hive") == "HKLM" or step.subject.get("root_key") == "startup_all")
                or step.requires_restart != rule.requires_restart
                or step.expected_bytes != expected_bytes
            ):
                raise SystemCareError("SystemCare plan no longer matches its catalog owner")

    @contextmanager
    def _operation(self, operation_id: str, kind: str, *, context: str = "", section: str = ""):
        try:
            with self.state.operation(operation_id, kind, context=context, section=section):
                yield
        except SystemCareOperationBusy as exc:
            raise SystemCareError("Another SystemCare operation is already active") from exc

    def _ensure_calibration_inside_scan(
        self,
        *,
        force: bool,
        progress: ProgressCallback | None,
        cancel_event: Any | None,
        outer_operation_id: str,
    ) -> Calibration:
        current = self.state.latest_calibration()
        needed, _reason = self.calibration_needed(current)
        if current is not None and not force and not needed:
            return current
        cancelled, on_stage = self._calibration_callbacks(
            outer_operation_id, progress=progress, cancel_event=cancel_event,
        )
        calibration = self.adapter.calibrate(progress=on_stage, cancelled=cancelled)
        self.state.save_calibration(calibration)
        self.state.append_log("calibration_completed", calibration_id=calibration.calibration_id, via="scan")
        return calibration

    def _calibration_callbacks(
        self,
        operation_id: str,
        *,
        progress: ProgressCallback | None,
        cancel_event: Any | None,
    ) -> tuple[Callable[[], bool], Callable[[str, float], None]]:
        sequence = 0

        def cancelled() -> bool:
            return bool(
                (cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)())
                or self.state.cancel_requested(operation_id)
            )

        def on_stage(label: str, fraction: float) -> None:
            nonlocal sequence
            sequence += 1
            self._emit(progress, ProgressEvent(
                operation_id=operation_id,
                phase="calibrate",
                domain=Domain.CALIBRATION,
                label=label,
                completed_weight=int(max(0.0, min(1.0, fraction)) * 100),
                total_weight=100,
                domain_fraction=fraction,
                state="active" if fraction < 1.0 else "complete",
                sequence=sequence,
            ))

        return cancelled, on_stage

    @staticmethod
    def _emit(callback: ProgressCallback | None, event: ProgressEvent) -> None:
        if callback is None:
            return
        try:
            callback(event)
        except Exception:
            return


def _scan_mode(value: ScanMode | str) -> ScanMode:
    if isinstance(value, ScanMode):
        return value
    try:
        return ScanMode(str(value or "safe").strip().lower())
    except ValueError as exc:
        raise SystemCareError("SystemCare scan mode must be safe or advanced") from exc
