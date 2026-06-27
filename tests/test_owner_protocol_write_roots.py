"""Regression guard: DEVMODE05/IFDEV05 write-path access must stay sticky for the
whole protocol session, not drop on continuation / operator-follow-up turns.

Root cause it locks: the runtime write paths (~/.mo/operator, ~/.mo/memory/devmode)
were re-added per turn only when `user_input` matched the activation phrase. A mid-run
turn ("continue", "yes", a non-matching continuation) dropped them and sandbox-blocked
edit_file to MO's own DEVMODE05 dirs — the recurring daily block.
"""
import core.agent.agent_turn_dispatch as dispatch
from core.agent.agent_turn_dispatch import AgentTurnDispatchMixin
from core.path_defaults import operator_pack_root, repo_root


class _Stub:
    allowed_roots = [str(repo_root())]

    def _owner_comparison_source_read_tool(self, name, arguments):
        return False

    def _mo_control_read_root(self):
        return None


def _pack_in_roots(stub, user_input):
    roots = AgentTurnDispatchMixin._effective_allowed_roots_for_tool(
        stub, user_input, "edit_file", {"path": "x"}
    )
    return str(operator_pack_root()) in (roots or [])


def _stub_activation(monkeypatch):
    # Isolate the sticky-roots logic from activation DETECTION (which needs the
    # owner token, absent in the isolated test home): only "start DEVMODE05" is the
    # maintenance activation here.
    monkeypatch.setattr(dispatch, "is_owner_maintenance_activation", lambda ui: ui == "start DEVMODE05")
    monkeypatch.setattr(dispatch, "is_owner_interface_audit_activation", lambda ui: False)
    monkeypatch.setattr(dispatch, "is_owner_comparison_activation", lambda ui: False)


def test_devmode_write_paths_are_sticky_across_continuation_turns(monkeypatch):
    _stub_activation(monkeypatch)
    stub = _Stub()
    # turn 1: explicit activation extends the write roots
    assert _pack_in_roots(stub, "start DEVMODE05") is True
    # turn 2+: a non-matching follow-up must NOT drop them (the fix)
    assert _pack_in_roots(stub, "continue") is True
    assert _pack_in_roots(stub, "yes do that") is True


def test_normal_session_never_extends_write_roots(monkeypatch):
    _stub_activation(monkeypatch)
    # An agent that never ran a write-protocol must not get the private roots —
    # the stickiness is opt-in by activation, it does not widen normal work.
    stub = _Stub()
    assert _pack_in_roots(stub, "fix this bug in gateway.py") is False
