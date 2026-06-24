"""Owner-protocol activation helpers. The gateway uses is_owner_protocol_activation to
skip generic ghost board-seeding for ALL four owner protocols — so IAM05/IFDEV05 no
longer inherit a DEVMODE05-flavored board (live mo-1782300201 cross-protocol contamination)."""
import pytest

from core.owner_protocols import (
    is_owner_protocol_activation,
    is_devmode05_activation,
    is_iam05_activation,
    owner_protocol_name,
)


@pytest.fixture
def installed(monkeypatch):
    monkeypatch.setenv("MO_OPERATOR_PROTOCOLS", "1")


CASES = {
    "start DEVMODE05": "DEVMODE05",
    "start vs05": "VS05",
    "start ifdev05": "IFDEV05",
    "start iam05": "IAM05",
    "expert audit": "IAM05",
}


def test_recognizes_all_four_protocols(installed):
    for text, name in CASES.items():
        assert is_owner_protocol_activation(text) is True, text
        assert owner_protocol_name(text) == name, text


def test_iam05_and_ifdev05_now_count_as_protocol_activation(installed):
    # The exact gap that caused the contamination: these used to fall through.
    assert is_owner_protocol_activation("start iam05") is True
    assert is_owner_protocol_activation("start ifdev05") is True


def test_normal_input_is_not_a_protocol(installed):
    for text in ("fix the bug in sandbox.py", "review the diff", "what is 2+2"):
        assert is_owner_protocol_activation(text) is False, text
        assert owner_protocol_name(text) == "", text


def test_inert_without_operator_protocols(monkeypatch):
    # No env override and a clone with no pack -> activations are inert. (On the operator
    # box the pack is present, so only assert the env-gated path is off here.)
    monkeypatch.delenv("MO_OPERATOR_PROTOCOLS", raising=False)
    # is_iam05_activation's regex matches but operator_protocols_installed gates it;
    # with the env override removed, a non-operator environment returns False.
    import core.owner_protocols as op
    monkeypatch.setattr(op, "operator_protocols_installed", lambda: False)
    assert is_owner_protocol_activation("start iam05") is False
    assert is_iam05_activation("start iam05") is False
    assert is_devmode05_activation("start devmode05") is False
