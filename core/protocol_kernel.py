"""Public protocol contracts shared by runtime gates.

Private owner protocol aliases and operator preferences live under the user
profile.  This module contains only product-safe protocol shape: terminal
markers, required artifact slots, and required closeout concepts.
"""

from __future__ import annotations

from dataclasses import dataclass


OWNER_MAINTENANCE_PROTOCOL = "owner_maintenance"
OWNER_COMPARISON_PROTOCOL = "owner_comparison"
OWNER_INTERFACE_AUDIT_PROTOCOL = "owner_interface_audit"
OWNER_DEDUP_PROTOCOL = "owner_dedup"


@dataclass(frozen=True)
class ProtocolContract:
    key: str
    complete_marker: str
    blocked_marker: str
    required_artifacts: tuple[str, ...] = ()
    required_closeout_terms: tuple[str, ...] = ()


def _maintenance_artifacts() -> tuple[str, ...]:
    try:
        from .tasking.devmode_manifest import SESSION_ARTIFACT_NAMES
        return tuple(str(name) for name in SESSION_ARTIFACT_NAMES)
    except Exception:
        return (
            "summary.md",
            "workflow.md",
            "catalog.md",
            "capability-matrix.md",
            "economy.md",
            "manifest.json",
        )


def protocol_contract(key: str) -> ProtocolContract:
    normalized = str(key or "").strip().lower()
    if normalized == OWNER_MAINTENANCE_PROTOCOL:
        return ProtocolContract(
            key=OWNER_MAINTENANCE_PROTOCOL,
            complete_marker="[OWNER_MAINTENANCE COMPLETE]",
            blocked_marker="[OWNER_MAINTENANCE BLOCKED]",
            required_artifacts=_maintenance_artifacts(),
        )
    if normalized == OWNER_COMPARISON_PROTOCOL:
        return ProtocolContract(
            key=OWNER_COMPARISON_PROTOCOL,
            complete_marker="[OWNER_COMPARISON COMPLETE]",
            blocked_marker="[OWNER_COMPARISON BLOCKED]",
            required_closeout_terms=("target", "matrix", "implementation", "reject"),
        )
    if normalized == OWNER_INTERFACE_AUDIT_PROTOCOL:
        return ProtocolContract(
            key=OWNER_INTERFACE_AUDIT_PROTOCOL,
            complete_marker="[OWNER_INTERFACE_AUDIT COMPLETE]",
            blocked_marker="[OWNER_INTERFACE_AUDIT BLOCKED]",
        )
    if normalized == OWNER_DEDUP_PROTOCOL:
        return ProtocolContract(
            key=OWNER_DEDUP_PROTOCOL,
            complete_marker="[OWNER_DEDUP COMPLETE]",
            blocked_marker="[OWNER_DEDUP BLOCKED]",
            required_closeout_terms=("scope", "coverage", "consolidated", "ledger"),
        )
    raise KeyError(f"unknown protocol contract: {key}")


def required_artifacts(key: str) -> tuple[str, ...]:
    return protocol_contract(key).required_artifacts


def required_closeout_terms(key: str) -> tuple[str, ...]:
    return protocol_contract(key).required_closeout_terms


def terminal_markers(key: str) -> tuple[str, str]:
    contract = protocol_contract(key)
    return contract.complete_marker, contract.blocked_marker
