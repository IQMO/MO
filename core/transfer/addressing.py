"""Stable transfer-target discovery and label-to-device resolution."""
from __future__ import annotations

from typing import Any

from core.state.everywhere_readiness import everywhere_authority

from .model import TransferError
from .service import HUB_TARGET_ID


def transfer_targets(
    config: dict[str, Any] | None = None,
) -> tuple[list[dict[str, str]], bool]:
    cfg = config or {}
    authority = everywhere_authority(cfg)
    settings = cfg.get("file_transfer")
    if not isinstance(settings, dict) or settings.get("enabled") is not True:
        return [], authority.hub_owner
    if not authority.enabled or authority.conflicts:
        return [], authority.hub_owner
    if authority.hub_owner:
        from mo_everywhere.registry import DeviceRegistry

        targets = [
            {"device_id": HUB_TARGET_ID, "label": "This MO host", "kind": "hub"}
        ]
        for item in DeviceRegistry(cfg).list_devices():
            if (
                item.get("revoked_at") is None
                and item.get("capability") == "control"
                and "file_transfer" in set(item.get("scopes") or ())
            ):
                targets.append(
                    {
                        "device_id": str(item["device_id"]),
                        "label": str(item["label"]),
                        "kind": "device",
                    }
                )
                if len(targets) >= 100:
                    break
        return targets, True
    from mo_everywhere.client import TransferClient

    return TransferClient(cfg).targets(), False


def resolve_transfer_target(
    value: Any, targets: list[dict[str, str]]
) -> dict[str, str]:
    requested = str(value or "").strip()
    exact_id = [item for item in targets if item["device_id"] == requested]
    exact_label = [
        item
        for item in targets
        if item["label"].casefold() == requested.casefold()
    ]
    matches = exact_id or exact_label
    if not matches and requested:
        matches = [
            item
            for item in targets
            if requested.casefold() in item["label"].casefold()
        ]
    if len(matches) == 1:
        return matches[0]
    candidates = ", ".join(
        f"{item['label']} [{item['device_id']}]" for item in matches or targets
    )
    if not requested:
        raise TransferError("choose one transfer target: " + candidates)
    raise TransferError(
        "transfer target is ambiguous or unavailable; use its stable device ID"
        + (": " + candidates if candidates else "")
    )
