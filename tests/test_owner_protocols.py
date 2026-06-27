"""Owner-protocol activation helpers.

The gateway uses is_owner_protocol_activation to skip generic ghost board-seeding
for all owner protocol slots, so one owner protocol cannot inherit another
protocol's board.
"""
import pytest

from core.owner_protocols import (
    is_owner_protocol_activation,
    is_owner_maintenance_activation,
    is_owner_integrity_audit_activation,
    owner_protocol_name,
)


@pytest.fixture
def installed(monkeypatch):
    monkeypatch.delenv("MO_OPERATOR_PROTOCOLS", raising=False)


CASES = {
    "start owner maintenance": "maintenance",
    "start owner comparison": "comparison",
    "start owner interface audit": "interface_audit",
    "start owner integrity audit": "integrity_audit",
    "expert audit": "integrity_audit",
}


def test_recognizes_all_four_protocols(installed):
    for text, name in CASES.items():
        assert is_owner_protocol_activation(text) is True, text
        assert owner_protocol_name(text) == name, text


def test_owner_integrity_audit_and_owner_interface_audit_now_count_as_protocol_activation(installed):
    # The exact gap that caused the contamination: these used to fall through.
    assert is_owner_protocol_activation("start owner integrity audit") is True
    assert is_owner_protocol_activation("start owner interface audit") is True


def test_normal_input_is_not_a_protocol(installed):
    for text in ("fix the bug in sandbox.py", "review the diff", "what is 2+2"):
        assert is_owner_protocol_activation(text) is False, text
        assert owner_protocol_name(text) == "", text


def test_inert_without_operator_protocols(monkeypatch):
    # No env override and a clone with no pack -> activations are inert. (On the operator
    # box the pack is present, so only assert the env-gated path is off here.)
    monkeypatch.delenv("MO_OPERATOR_PROTOCOLS", raising=False)
    # Activation matches only when operator_protocols_installed gates it on.
    # with the env override removed, a non-operator environment returns False.
    import core.owner_protocols as op
    monkeypatch.setattr(op, "operator_protocols_installed", lambda: False)
    assert is_owner_protocol_activation("start owner integrity audit") is False
    assert is_owner_integrity_audit_activation("start owner integrity audit") is False
    assert is_owner_maintenance_activation("start owner maintenance") is False
