"""Canonical, dependency-free SystemCare preference normalization."""
from __future__ import annotations

from typing import Any
import re

from core.utils.number_utils import as_bounded_int


def systemcare_settings(config: dict[str, Any] | None) -> dict[str, Any]:
    """Return the raw profile-owned SystemCare settings block, if present."""
    root = config if isinstance(config, dict) else {}
    desktop = root.get("mo_desktop") if isinstance(root.get("mo_desktop"), dict) else {}
    settings = desktop.get("systemcare")
    return settings if isinstance(settings, dict) else {}


def normalized_systemcare_preferences(
    config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return the one bounded runtime projection of supported SystemCare preferences."""
    settings = systemcare_settings(config)
    advanced = settings.get("advanced_scan") if isinstance(settings.get("advanced_scan"), dict) else {}
    categories = settings.get("categories") if isinstance(settings.get("categories"), dict) else {}
    scan_mode = str(settings.get("scan_mode_default") or "safe").strip().lower()
    automation = settings.get("automation") if isinstance(settings.get("automation"), dict) else {}
    game = settings.get("game_mode") if isinstance(settings.get("game_mode"), dict) else {}
    power_guid = str(game.get("power_guid") or "").lower()
    notification_scope = str(settings.get("notification_scope") or "background")
    if notification_scope not in {"background", "attention", "all"}:
        notification_scope = "background"
    if scan_mode not in {"safe", "advanced"}:
        scan_mode = "safe"
    return {
        "scan_mode_default": scan_mode,
        "retention_days": as_bounded_int(settings.get("retention_days"), 30, 1, 365),
        "history_limit": as_bounded_int(settings.get("history_limit"), 40, 4, 200),
        "plan_ttl_minutes": as_bounded_int(settings.get("plan_ttl_minutes"), 15, 5, 60),
        "notifications": bool(settings.get("notifications", True)),
        "notification_scope": notification_scope,
        "game_mode": {"pause_schedules": bool(game.get("pause_schedules", False)),
                      "power_guid": power_guid if re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", power_guid) else ""},
        "advanced": {
            "deep_storage": bool(advanced.get("deep_storage", True)),
            "component_store": bool(advanced.get("component_store", True)),
            "system_integrity": bool(advanced.get("system_integrity", True)),
            "service_advisory": bool(advanced.get("service_advisory", True)),
            "registry_advisory": bool(advanced.get("registry_advisory", False)),
        },
        "categories": {
            str(key)[:48]: bool(value)
            for key, value in list(categories.items())[:32]
        },
        "automation": {
            "enabled": bool(automation.get("enabled", False)),
            "interval_hours": as_bounded_int(automation.get("interval_hours"), 24, 1, 168),
            "idle_minutes": as_bounded_int(automation.get("idle_minutes"), 10, 0, 120),
            "battery_pause": bool(automation.get("battery_pause", True)),
            "game_pause": bool(automation.get("game_pause", True)),
            "cleanup": bool(automation.get("cleanup", False)),
        },
    }


__all__ = ["normalized_systemcare_preferences", "systemcare_settings"]
