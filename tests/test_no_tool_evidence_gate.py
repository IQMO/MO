"""Owner-protocol no-tool-evidence gate (IAM05 mo-1782320175 extraction).

The three near-identical inline blocks at agent_turn.py:655-683 (DEVMODE05/VS05/IFDEV05
"no tools were called this turn -> fabrication" continuations) were collapsed into one
pure module-level helper. These tests pin the exact behavior that was inline: precedence
order, the DEVMODE05 completed-board exemption, the zero-tool-calls trigger condition, and
the exact label/marker of each continuation."""
from core.agent.agent_turn import _no_tool_evidence_continuation


def _call(**kw):
    base = dict(
        devmode05_active=False,
        vs05_active=False,
        ifdev05_active=False,
        total_tool_calls=0,
        devmode_taskboard_completed=False,
    )
    base.update(kw)
    return _no_tool_evidence_continuation(**base)


def test_no_protocol_active_returns_none():
    assert _call() is None


def test_any_tool_call_short_circuits_even_when_active():
    # A single tool call this turn means there IS evidence — gate must not fire.
    assert _call(devmode05_active=True, total_tool_calls=1) is None
    assert _call(vs05_active=True, total_tool_calls=3) is None
    assert _call(ifdev05_active=True, total_tool_calls=1) is None


def test_devmode05_no_tools_fires():
    res = _call(devmode05_active=True)
    assert res is not None
    label, msg = res
    assert label == "DEVMODE05: no tool evidence — continuing..."
    assert msg.startswith("[DEVMODE05 AUTONOMY]")


def test_devmode05_exempt_when_taskboard_completed():
    # A clean closeout turn with a completed board legitimately needs no new tools.
    assert _call(devmode05_active=True, devmode_taskboard_completed=True) is None


def test_devmode05_completed_falls_through_to_other_active_protocol():
    # If the DEVMODE05 board is complete but VS05 is also active, VS05 still fires.
    res = _call(devmode05_active=True, devmode_taskboard_completed=True, vs05_active=True)
    assert res is not None
    assert res[1].startswith("[VS05 CONTINUATION]")


def test_vs05_no_tools_fires():
    res = _call(vs05_active=True)
    assert res is not None
    label, msg = res
    assert label == "VS05: no tool evidence - continuing..."
    assert msg.startswith("[VS05 CONTINUATION]")


def test_ifdev05_no_tools_fires():
    res = _call(ifdev05_active=True)
    assert res is not None
    label, msg = res
    assert label == "IFDEV05: no tool evidence - continuing..."
    assert msg.startswith("[IFDEV05 CONTINUATION]")


def test_precedence_devmode_over_vs05_and_ifdev05():
    # All three active, no tools, board not complete -> DEVMODE05 wins (original order).
    res = _call(devmode05_active=True, vs05_active=True, ifdev05_active=True)
    assert res is not None
    assert res[1].startswith("[DEVMODE05 AUTONOMY]")
