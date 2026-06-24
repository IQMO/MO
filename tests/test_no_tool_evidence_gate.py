"""Owner-protocol no-tool-evidence gate (OWNER_INTEGRITY_AUDIT mo-1782320175 extraction).

The three near-identical inline blocks at agent_turn.py:655-683 (OWNER_MAINTENANCE/OWNER_COMPARISON/OWNER_INTERFACE_AUDIT
"no tools were called this turn -> fabrication" continuations) were collapsed into one
pure module-level helper. These tests pin the exact behavior that was inline: precedence
order, the OWNER_MAINTENANCE completed-board exemption, the zero-tool-calls trigger condition, and
the exact label/marker of each continuation."""
from core.agent.agent_turn import _no_tool_evidence_continuation


def _call(**kw):
    base = dict(
        owner_maintenance_active=False,
        owner_comparison_active=False,
        owner_interface_audit_active=False,
        total_tool_calls=0,
        devmode_taskboard_completed=False,
    )
    base.update(kw)
    return _no_tool_evidence_continuation(**base)


def test_no_protocol_active_returns_none():
    assert _call() is None


def test_any_tool_call_short_circuits_even_when_active():
    # A single tool call this turn means there IS evidence — gate must not fire.
    assert _call(owner_maintenance_active=True, total_tool_calls=1) is None
    assert _call(owner_comparison_active=True, total_tool_calls=3) is None
    assert _call(owner_interface_audit_active=True, total_tool_calls=1) is None


def test_owner_maintenance_no_tools_fires():
    res = _call(owner_maintenance_active=True)
    assert res is not None
    label, msg = res
    assert label == "OWNER_MAINTENANCE: no tool evidence — continuing..."
    assert msg.startswith("[OWNER_MAINTENANCE AUTONOMY]")


def test_owner_maintenance_exempt_when_taskboard_completed():
    # A clean closeout turn with a completed board legitimately needs no new tools.
    assert _call(owner_maintenance_active=True, devmode_taskboard_completed=True) is None


def test_owner_maintenance_completed_falls_through_to_other_active_protocol():
    # If the OWNER_MAINTENANCE board is complete but OWNER_COMPARISON is also active, OWNER_COMPARISON still fires.
    res = _call(owner_maintenance_active=True, devmode_taskboard_completed=True, owner_comparison_active=True)
    assert res is not None
    assert res[1].startswith("[OWNER_COMPARISON CONTINUATION]")


def test_owner_comparison_no_tools_fires():
    res = _call(owner_comparison_active=True)
    assert res is not None
    label, msg = res
    assert label == "OWNER_COMPARISON: no tool evidence - continuing..."
    assert msg.startswith("[OWNER_COMPARISON CONTINUATION]")


def test_owner_interface_audit_no_tools_fires():
    res = _call(owner_interface_audit_active=True)
    assert res is not None
    label, msg = res
    assert label == "OWNER_INTERFACE_AUDIT: no tool evidence - continuing..."
    assert msg.startswith("[OWNER_INTERFACE_AUDIT CONTINUATION]")


def test_precedence_devmode_over_owner_comparison_and_owner_interface_audit():
    # All three active, no tools, board not complete -> OWNER_MAINTENANCE wins (original order).
    res = _call(owner_maintenance_active=True, owner_comparison_active=True, owner_interface_audit_active=True)
    assert res is not None
    assert res[1].startswith("[OWNER_MAINTENANCE AUTONOMY]")
