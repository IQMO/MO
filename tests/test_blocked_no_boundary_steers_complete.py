"""Regression guard for the daily DEVMODE05 dead-end loop.

When a protocol emits `[OWNER_* BLOCKED]` with NO real hard boundary (e.g. the
taskboard was briefly marked blocked while open tasks = 0, or a recovered
sandbox/tool error), the gate must steer the model to `[OWNER_* COMPLETE]`, NOT
tell it to "continue" (it has nothing to continue, so it re-asserts BLOCKED and
dead-ends at the stop-gate cap — the loop the operator was sick of).
"""
import core.self_maintenance.devmode_closeout as dc

_CASES = [
    ("owner_maintenance_continuation_instruction", "is_owner_maintenance_activation",
     "start DEVMODE05", "[OWNER_MAINTENANCE BLOCKED]", "[OWNER_MAINTENANCE COMPLETE]"),
    ("owner_comparison_continuation_instruction", "is_owner_comparison_activation",
     "start VS05", "[OWNER_COMPARISON BLOCKED]", "[OWNER_COMPARISON COMPLETE]"),
    ("owner_dedup_continuation_instruction", "is_owner_dedup_activation",
     "start DEDUP05", "[OWNER_DEDUP BLOCKED]", "[OWNER_DEDUP COMPLETE]"),
    ("owner_interface_audit_continuation_instruction", "is_owner_interface_audit_activation",
     "start IFDEV05", "[OWNER_INTERFACE_AUDIT BLOCKED]", "[OWNER_INTERFACE_AUDIT COMPLETE]"),
]


def test_blocked_without_hard_boundary_steers_to_complete(monkeypatch):
    for fn_name, activation_name, user_input, blocked_marker, complete_marker in _CASES:
        # only this protocol is "active" for the test
        for case in _CASES:
            monkeypatch.setattr(dc, case[1], (lambda exp: (lambda ui: ui == exp))(user_input))
        fn = getattr(dc, fn_name)
        final = f"{blocked_marker} diff clean, 0 findings, all tool errors recovered, closeout written"
        out = fn(user_input, final)
        assert complete_marker in out, f"{fn_name} must steer to {complete_marker}: {out[:120]}"
        assert "do not re-assert" in out.lower(), f"{fn_name} must forbid re-asserting BLOCKED"
