"""Regression for the live mo-1782487827 P0: a doom-loop force-stop on an
OWNER_MAINTENANCE turn must NOT leave a false [OWNER_MAINTENANCE COMPLETE] summary
beside open task rows. The fix converts the stop into a recoverable
[OWNER_MAINTENANCE BLOCKED] boundary so the existing reconciler fires.
"""
from core.agent.agent_turn import AgentTurn


class _Stub:
    """Minimal carrier for the AgentTurn helper under test (no full agent build)."""

    gateway = None
    _owner_maintenance_doom_loop_boundary = AgentTurn._owner_maintenance_doom_loop_boundary

    def __init__(self):
        self.reconciled = []

    def _reconcile_devmode_summary_marker(self, text):
        self.reconciled.append(text)


def test_doom_loop_on_owner_maintenance_emits_blocked_and_reconciles():
    s = _Stub()
    # Use a built-in default alias (pack-independent) so activation is deterministic
    # under the suite's isolated state-home, where the codenamed alias isn't loaded.
    out = s._owner_maintenance_doom_loop_boundary("owner maintenance", "generic doom stop text")
    # The terminal text now carries the recoverable BLOCKED marker the reconciler keys on.
    assert out.startswith("[OWNER_MAINTENANCE BLOCKED]")
    assert "doom-loop" in out
    assert "generic doom stop text" in out  # original stop text preserved after the marker
    # The reconciler was invoked with the BLOCKED text -> it rewrites summary.md and
    # re-projects the manifest (status=blocked + live economy/board).
    assert s.reconciled and s.reconciled[0].startswith("[OWNER_MAINTENANCE BLOCKED]")


def test_doom_loop_off_owner_maintenance_is_noop():
    s = _Stub()
    out = s._owner_maintenance_doom_loop_boundary("just a normal coding task", "generic doom stop text")
    assert out == "generic doom stop text"  # unchanged
    assert s.reconciled == []  # reconciler NOT called outside OWNER_MAINTENANCE
