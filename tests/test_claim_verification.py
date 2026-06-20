"""FB1 — verify-before-claiming detector (VS05 vs Fable 5).

Conservative: real stale-prone claims fire; ordinary coding prose never does.
"""
from core.claim_verification import (
    detect_unverified_current_state_claim,
    unverified_claim_signal,
    used_verifying_tools,
)


# ── detector: true positives (stale-prone current-state claims) ──
def test_latest_version_claim_fires():
    assert detect_unverified_current_state_claim("The latest version of pytest is 8.2.")
    assert detect_unverified_current_state_claim("The most recent release adds async support.")


def test_knowledge_cutoff_hedge_fires():
    assert detect_unverified_current_state_claim("As of my knowledge, the API uses v2 auth.")
    assert detect_unverified_current_state_claim("As of 2024, the default branch is main.")


def test_current_version_claim_fires():
    assert detect_unverified_current_state_claim("It is currently on version 3.")
    assert detect_unverified_current_state_claim("Version 2.1.0 is the latest release.")


# ── detector: true negatives (ordinary coding prose must NOT fire) ──
def test_ordinary_coding_prose_does_not_fire():
    for text in [
        "I refactored the loop to use enumerate and added a test.",
        "The function returns None when the path is missing.",
        "Set timeout=30 in the config and re-run pytest.",
        "Here's version 2 of the helper I wrote for you.",   # bare 'version 2', not a current-state claim
        "Bump the dependency to 2.1.0 in pyproject.toml.",   # an instruction, not a 'latest' assertion
    ]:
        assert detect_unverified_current_state_claim(text) is None, text


def test_empty_text_is_none():
    assert detect_unverified_current_state_claim("") is None
    assert detect_unverified_current_state_claim("   ") is None


# ── verifying-tool detection ──
def test_used_verifying_tools():
    assert used_verifying_tools({"read_file": 2}) is True
    assert used_verifying_tools({"web_fetch": 1}) is True
    assert used_verifying_tools({"grep": 1, "edit_file": 3}) is True
    assert used_verifying_tools({"edit_file": 3, "shell": 1}) is False  # no read/search/web
    assert used_verifying_tools({}) is False
    assert used_verifying_tools(None) is False


# ── combined signal: claim + no verification = flag ──
def test_signal_fires_on_claim_without_tools():
    assert unverified_claim_signal("The latest version is 8.2.", {}) == "latest-version claim"
    assert unverified_claim_signal("The latest version is 8.2.", {"edit_file": 1}) is not None  # edit isn't verification


def test_signal_suppressed_when_verified():
    # Same claim, but the turn read/searched something -> not flagged.
    assert unverified_claim_signal("The latest version is 8.2.", {"read_file": 1}) is None
    assert unverified_claim_signal("The latest version is 8.2.", {"web_fetch": 1}) is None


def test_signal_none_on_ordinary_answer():
    assert unverified_claim_signal("I added a regression test; 12 pass.", {}) is None
