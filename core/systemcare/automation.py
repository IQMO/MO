"""SystemCare jobs run in MO's existing scheduler, without a provider turn."""
from __future__ import annotations

import ctypes
import json
import os
from typing import Any

from .config import normalized_systemcare_preferences


def _idle_seconds() -> float | None:
    if os.name != "nt":
        return None
    class LastInput(ctypes.Structure):
        _fields_ = [("size", ctypes.c_uint), ("tick", ctypes.c_uint)]
    value = LastInput()
    value.size = ctypes.sizeof(value)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(value)):
        return None
    return ((ctypes.windll.kernel32.GetTickCount() - value.tick) & 0xffffffff) / 1000


def run_scheduled_care(config: dict[str, Any], options: dict[str, Any]) -> str:
    from .service import SystemCareService
    from core.state.paths import runtime_config_path
    from pathlib import Path
    config_path = runtime_config_path(config)
    if config_path:
        from core.provider.provider import load_config
        try:
            loaded = load_config(str(Path(config_path)))
        except Exception:
            return json.dumps({"state": "skipped", "reason": "Current profile authorization could not be loaded"})
        config = {**config, "mo_desktop": loaded.get("mo_desktop") or {}}
    service = SystemCareService(config)
    # Current profile authorization takes precedence over an old queued job.
    current = normalized_systemcare_preferences(config)["automation"]
    if not current["enabled"]:
        return json.dumps({"state": "skipped", "reason": "Automation is disabled"})
    idle = _idle_seconds()
    if current["idle_minutes"] and (idle is None or idle < current["idle_minutes"] * 60):
        return json.dumps({"state": "skipped", "reason": "Idle condition not satisfied"})
    power = service.adapter._power_status()
    if current["battery_pause"] and power.get("source") != "AC":
        return json.dumps({"state": "skipped", "reason": "AC power not established"})
    if current["game_pause"] and service.state.active_game() is not None:
        return json.dumps({"state": "skipped", "reason": "Global Game Mode is active or awaiting recovery"})
    if service.state.active_operation():
        return json.dumps({"state": "skipped", "reason": "SystemCare operation is active"})
    scan = service.scan("safe")
    result = {"state": scan.state.value, "scan_id": scan.scan_id, "review_count": scan.review_count}
    # Both the saved job and current profile must authorize permanent generated
    # data removal. Diagnostic/registry/service actions never join this lane.
    if current["cleanup"] and options.get("cleanup") is True:
        allowed = {"storage.user_temp_old", "storage.thumbnail_cache", "storage.directx_shader_cache"}
        selected = [f.finding_id for f in scan.findings if f.rule_id in allowed and f.selectable and f.candidates]
        if selected:
            plan = service.build_plan(selected, scan_id=scan.scan_id)
            result["receipt"] = service.apply(plan.plan_id, plan.digest, acknowledge_non_undo=True).to_dict()
    return json.dumps(result)


def configure_schedule(agent: Any, options: dict[str, Any]) -> bool:
    from core.runtime.scheduler import manage_scheduler_jobs, parse_schedule, start_scheduler_service_if_enabled
    from core.state.device import device_identity
    owner = {"surface": "systemcare", "device_id": device_identity(agent.config)["device_id"]}
    jobs = manage_scheduler_jobs(agent, "list", required_owner=owner).get("jobs", [])
    prefs = normalized_systemcare_preferences({"mo_desktop": {"systemcare": {"automation": options}}})["automation"]
    schedule = "every " + str(prefs["interval_hours"]) + "h"
    if prefs["enabled"] and len(jobs) == 1 and jobs[0].get("enabled") is True and jobs[0].get("kind") == "systemcare" and jobs[0].get("systemcare") == prefs and jobs[0].get("schedule") == parse_schedule(schedule):
        if getattr(agent, "scheduler_service", None) is None:
            start_scheduler_service_if_enabled(agent)
        return True
    # Replace only changed schedules owned by this app/device.
    for job in jobs:
        manage_scheduler_jobs(agent, "remove", {"job_id": job["id"]}, required_owner=owner)
    if prefs["enabled"]:
        manage_scheduler_jobs(agent, "create", {"kind": "systemcare", "name": "SystemCare maintenance",
            "schedule": schedule, "systemcare": prefs}, owner=owner)
        if getattr(agent, "scheduler_service", None) is None:
            start_scheduler_service_if_enabled(agent)
    return True
