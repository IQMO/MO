"""MO Desktop package.

This package is the single source for MO Desktop implementation. Keep package
import cheap: terminal paths import small helpers such as ``desktop_launch`` and
``desktop_log`` without loading the companion GUI, tray, voice, or optional
desktop dependencies.
"""
from __future__ import annotations

__all__ = [
    "CompanionSurface",
    "CompanionTray",
    "start_tray_if_enabled",
]


def __getattr__(name: str):
    """Lazily expose historical package-level exports without heavy startup imports."""
    if name == "CompanionSurface":
        from mo_desktop.companion import CompanionSurface

        return CompanionSurface
    if name in {"CompanionTray", "start_tray_if_enabled"}:
        from mo_desktop.tray import CompanionTray, start_tray_if_enabled

        return {
            "CompanionTray": CompanionTray,
            "start_tray_if_enabled": start_tray_if_enabled,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
