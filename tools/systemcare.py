"""Provider-facing adapters for MO SystemCare's single core service."""
from __future__ import annotations

import json
import time
from typing import Any


def execute_systemcare_status(arguments: dict[str, Any]) -> str:
    runtime = dict(arguments or {})
    config = runtime.pop("_mo_config", None) or {}
    from core.systemcare.service import SystemCareService

    return _render(SystemCareService(config).status())


def execute_systemcare_calibrate(arguments: dict[str, Any]) -> str:
    runtime = dict(arguments or {})
    config = runtime.pop("_mo_config", None) or {}
    force = runtime.get("force", False)
    if not isinstance(force, bool):
        return "SystemCare calibration error: force must be true or false."
    from core.systemcare.service import SystemCareError, SystemCareService

    try:
        calibration = SystemCareService(config).calibrate(force=force)
    except SystemCareError as exc:
        return f"SystemCare calibration error: {exc}"
    return _render({
        "calibration": calibration.public_summary(),
        "message": "Read-only machine calibration is current. No Windows setting was changed.",
    })


def execute_systemcare_inspect(arguments: dict[str, Any]) -> str:
    runtime = dict(arguments or {})
    config = runtime.pop("_mo_config", None) or {}
    from core.systemcare.service import SystemCareService
    try:
        result = SystemCareService(config).inspect(str(runtime.get("context") or "machine"),
                    str(runtime.get("section") or "services"), str(runtime.get("target") or ""))
        # Originals belong to the private action/restore owner, never provider prose.
        public = {**result, "observed_at": result.get("at"),
                  "selection": "Use each row's index from this full inspection, plus observed_at; do not renumber filtered rows. Eligible means eligible for exact plan review, not a performance benefit or permission to apply.",
                  "rows": [{**{k: v for k, v in row.items() if k not in {"original", "subject"}}, "index": index}
                           for index, row in enumerate(result.get("rows", []))]}
        return _render(public)
    except (RuntimeError, ValueError, OSError) as exc:
        return f"SystemCare inspection error: {exc}"


def execute_systemcare_scan(arguments: dict[str, Any]) -> str:
    runtime = dict(arguments or {})
    config = runtime.pop("_mo_config", None) or {}
    mode = str(runtime.get("mode") or "safe").strip().lower()
    force_calibration = runtime.get("force_calibration", False)
    all_checks = runtime.get("all_checks", False)
    domains = runtime.get("advanced_domains")
    if not isinstance(force_calibration, bool):
        return "SystemCare scan error: force_calibration must be true or false."
    if not isinstance(all_checks, bool):
        return "SystemCare scan error: all_checks must be true or false."
    if domains is not None and not isinstance(domains, list):
        return "SystemCare scan error: advanced_domains must be a list."
    from core.systemcare.service import SystemCareError, SystemCareService

    try:
        scan = SystemCareService(config).scan(
            mode,
            force_calibration=force_calibration,
            advanced_domains=domains,
            all_checks=all_checks,
        )
    except SystemCareError as exc:
        return f"SystemCare scan error: {exc}"
    return _render({
        "scan": scan.to_dict(include_private=False),
        "message": "The scan was read-only. Review exact findings before creating a plan.",
    })


def execute_systemcare_plan(arguments: dict[str, Any]) -> str:
    runtime = dict(arguments or {})
    config = runtime.pop("_mo_config", None) or {}
    if runtime.get("action"):
        from core.systemcare.service import SystemCareService
        try:
            plan = SystemCareService(config).build_action_plan(str(runtime["action"]),
                str(runtime.get("section") or ""), runtime.get("indices") or [],
                context=str(runtime.get("context") or "machine"), target=str(runtime.get("target") or ""),
                observed_at=runtime.get("observed_at"))
            return _render({"plan": plan.to_dict(include_private=False), "message": "Review this exact selected action before apply. Planning changes no Windows setting."})
        except (RuntimeError, ValueError, OSError) as exc:
            return f"SystemCare plan error: {exc}"
    finding_ids = runtime.get("finding_ids")
    if not isinstance(finding_ids, list):
        return "SystemCare plan error: finding_ids must be a list from one completed scan."
    from core.systemcare.service import SystemCareError, SystemCareService

    try:
        plan = SystemCareService(config).build_plan(
            finding_ids,
            scan_id=str(runtime.get("scan_id") or ""),
        )
    except SystemCareError as exc:
        return f"SystemCare plan error: {exc}"
    return _render({
        "plan": plan.to_dict(include_private=False),
        "message": "Planning changed no Windows state. Present this exact digest and every non-undo step before applying.",
    })


def execute_systemcare_apply(arguments: dict[str, Any]) -> str:
    runtime = dict(arguments or {})
    config = runtime.pop("_mo_config", None) or {}
    acknowledgement = runtime.get("acknowledge_non_undo", False)
    if not isinstance(acknowledgement, bool):
        return "SystemCare apply error: acknowledge_non_undo must be true or false."
    from core.systemcare.service import SystemCareError, SystemCareService

    try:
        receipt = SystemCareService(config).apply(
            str(runtime.get("plan_id") or ""),
            str(runtime.get("plan_digest") or ""),
            acknowledge_non_undo=acknowledgement,
        )
    except SystemCareError as exc:
        return f"SystemCare apply error: {exc}"
    return _render({
        "receipt": receipt.to_dict(),
        "message": "SystemCare reports completion only from the persisted post-check receipt.",
    })


def execute_systemcare_rollback(arguments: dict[str, Any]) -> str:
    runtime = dict(arguments or {})
    config = runtime.pop("_mo_config", None) or {}
    receipt_id = str(runtime.get("receipt_id") or "").strip()
    if not receipt_id:
        return "SystemCare rollback error: receipt_id is required."
    from core.systemcare.service import SystemCareService

    service = SystemCareService(config)
    receipt = service.state.receipt(receipt_id)
    if receipt is None:
        return "SystemCare rollback error: receipt was not found."
    if not receipt.rollback_available:
        return "SystemCare rollback blocked: this receipt contains no reversible applied step."
    step_id = str(runtime.get("step_id") or "")
    steps = [s for s in (*receipt.applied_steps, *receipt.failed_steps) if service.state.backup(s)]
    if not step_id and len(steps) == 1:
        step_id = steps[0]
    if step_id not in steps:
        return _render({"error": "Select one exact reversible receipt step", "step_ids": steps})
    try:
        return _render(service.restore(step_id))
    except (RuntimeError, ValueError, OSError) as exc:
        return f"SystemCare restoration error: {exc}"


def execute_systemcare_cancel(arguments: dict[str, Any]) -> str:
    runtime = dict(arguments or {})
    config = runtime.pop("_mo_config", None) or {}
    from core.systemcare.service import SystemCareService

    requested = SystemCareService(config).cancel(str(runtime.get("operation_id") or ""))
    return (
        "SystemCare cancellation requested; the operation will stop at its next safe boundary."
        if requested
        else "SystemCare has no matching active operation to cancel."
    )


def systemcare_action_precondition_block_reason(
    tool_name: str,
    arguments: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> str | None:
    """Fail closed before the generic action-confirmation gate."""
    name = str(tool_name or "").strip()
    if name not in {"systemcare_apply", "systemcare_rollback", "systemcare_cancel"}:
        return None
    from core.systemcare.models import UndoQuality, canonical_digest
    from core.systemcare.service import SystemCareError, SystemCareService

    service = SystemCareService(config or {})
    if name == "systemcare_cancel":
        active = service.state.active_operation()
        requested = str(arguments.get("operation_id") or active.get("operation_id") or "").strip()
        if not active or active.get("stale") or requested != str(active.get("operation_id") or ""):
            return "[SYSTEMCARE SAFETY BLOCK] No matching live SystemCare operation can be cancelled."
        return None
    if name == "systemcare_rollback":
        receipt = service.state.receipt(str(arguments.get("receipt_id") or ""))
        if receipt is None:
            return "[SYSTEMCARE SAFETY BLOCK] The exact SystemCare receipt was not found."
        if not receipt.rollback_available:
            return "[SYSTEMCARE SAFETY BLOCK] The exact receipt has no reversible applied step."
        steps = [s for s in (*receipt.applied_steps, *receipt.failed_steps) if service.state.backup(s)]
        step_id = str(arguments.get("step_id") or (steps[0] if len(steps) == 1 else ""))
        if step_id not in steps or service.state.backup(step_id)["state"] == "restored":
            return "[SYSTEMCARE SAFETY BLOCK] Select one unrestored original from this exact receipt."
        return None

    plan = service.state.plan(str(arguments.get("plan_id") or ""))
    if plan is None:
        return "[SYSTEMCARE SAFETY BLOCK] The exact SystemCare plan was not found."
    try:
        service.validate_plan_catalog(plan)
    except SystemCareError:
        return "[SYSTEMCARE SAFETY BLOCK] The SystemCare plan no longer matches its catalog owner."
    prior_receipt = service.state.receipt_for_plan(plan.plan_id)
    if prior_receipt is not None:
        return "[SYSTEMCARE SAFETY BLOCK] This SystemCare plan already has a receipt and cannot be applied twice."
    expected = canonical_digest(plan.digest_payload())
    if plan.digest != expected or str(arguments.get("plan_digest") or "").strip().lower() != expected:
        return "[SYSTEMCARE SAFETY BLOCK] The SystemCare plan digest is missing or changed."
    if plan.expires_at <= time.time():
        return "[SYSTEMCARE SAFETY BLOCK] The SystemCare plan expired; scan and review again."
    if any(step.undo == UndoQuality.NONE for step in plan.steps) and arguments.get("acknowledge_non_undo") is not True:
        return "[SYSTEMCARE SAFETY BLOCK] Permanent-removal steps require exact non-undo acknowledgment."
    latest = service.state.latest_scan(complete_only=True)
    if plan.scan_id and (latest is None or latest.scan_id != plan.scan_id):
        return "[SYSTEMCARE SAFETY BLOCK] The plan is not based on the latest verified scan."
    calibration = service.state.latest_calibration()
    if calibration is None or calibration.calibration_id != plan.calibration_id:
        return "[SYSTEMCARE SAFETY BLOCK] Machine calibration changed; scan and review again."
    return None


def _render(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
