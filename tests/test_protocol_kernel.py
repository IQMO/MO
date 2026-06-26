from core.protocol_kernel import (
    OWNER_COMPARISON_PROTOCOL,
    OWNER_INTERFACE_AUDIT_PROTOCOL,
    OWNER_MAINTENANCE_PROTOCOL,
    protocol_contract,
    required_artifacts,
    required_closeout_terms,
    terminal_markers,
)
from core.self_maintenance import devmode_closeout as closeout
from core.tasking.devmode_manifest import SESSION_ARTIFACT_NAMES


def test_protocol_kernel_exposes_product_safe_terminal_contracts():
    maintenance = protocol_contract(OWNER_MAINTENANCE_PROTOCOL)
    comparison = protocol_contract(OWNER_COMPARISON_PROTOCOL)
    interface = protocol_contract(OWNER_INTERFACE_AUDIT_PROTOCOL)

    assert maintenance.complete_marker == "[OWNER_MAINTENANCE COMPLETE]"
    assert comparison.blocked_marker == "[OWNER_COMPARISON BLOCKED]"
    assert interface.complete_marker == "[OWNER_INTERFACE_AUDIT COMPLETE]"
    assert terminal_markers(OWNER_COMPARISON_PROTOCOL) == (
        "[OWNER_COMPARISON COMPLETE]",
        "[OWNER_COMPARISON BLOCKED]",
    )


def test_owner_maintenance_artifacts_are_consumed_from_protocol_kernel():
    assert required_artifacts(OWNER_MAINTENANCE_PROTOCOL) == tuple(SESSION_ARTIFACT_NAMES)
    assert closeout._owner_maintenance_required_artifacts() == tuple(SESSION_ARTIFACT_NAMES)


def test_owner_comparison_missing_terms_are_kernel_defined():
    assert required_closeout_terms(OWNER_COMPARISON_PROTOCOL) == ("target", "matrix", "adoption", "reject")

    missing = closeout._owner_comparison_missing_closeout_terms(
        "[OWNER_COMPARISON COMPLETE]\nTarget: current MO\nMatrix: done\nAdoption: none"
    )

    assert missing == ["reject"]
