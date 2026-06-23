"""FB1 — verify-before-claiming detector (VS05 vs Fable 5).

Conservative: real stale-prone claims fire; ordinary coding prose never does.
"""
from core.claim_verification import (
    detect_completion_claim,
    detect_unverified_current_state_claim,
    unverified_claim_signal,
    unverified_completion_claim_signal,
    used_completion_verifying_tools,
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


# ── completion/cleanliness detector: true positives (assumption-claims) ──
def test_completion_claims_fire():
    for text in [
        "Everything's clean now.",
        "It's clean, nothing to commit.",
        "All tests pass.",
        "The suite is green.",
        "No issues found.",
        "No regressions.",
        "Product and VPS are fully synced.",
        "Verified clean.",
        "It all checks out.",
    ]:
        assert detect_completion_claim(text), text


# ── completion detector: true negatives (ordinary prose must NOT fire) ──
def test_completion_detector_does_not_overfire():
    for text in [
        "I edited the function and will run the tests next.",
        "This should fix the import error.",
        "Let me clean up the temp files.",     # 'clean up', not a cleanliness verdict
        "The cleanup routine runs on exit.",
        "I'm done editing; reviewing now.",     # bare 'done' must not fire
    ]:
        assert detect_completion_claim(text) is None, text


# ── completion verifying tools (broader: tests/shell/git count) ──
def test_completion_verifying_tools():
    assert used_completion_verifying_tools({"test_runner": 1}) is True
    assert used_completion_verifying_tools({"shell": 1}) is True
    assert used_completion_verifying_tools({"git_status": 1}) is True
    assert used_completion_verifying_tools({"read_file": 1}) is True
    assert used_completion_verifying_tools({"edit_file": 3}) is False   # editing isn't verifying
    assert used_completion_verifying_tools({}) is False


# ── combined completion signal: claim + no check = flag ──
def test_completion_signal_fires_without_checks():
    assert unverified_completion_claim_signal("All tests pass.", {}) == "tests-pass claim"
    assert unverified_completion_claim_signal("It's clean.", {"edit_file": 2}) is not None


def test_completion_signal_suppressed_when_checked():
    # A real check this turn (tests/shell/git/read) clears the flag.
    assert unverified_completion_claim_signal("All tests pass.", {"test_runner": 1}) is None
    assert unverified_completion_claim_signal("It's clean.", {"git_status": 1}) is None
    assert unverified_completion_claim_signal("No regressions.", {"shell": 1}) is None


def test_completion_signal_none_on_ordinary_answer():
    assert unverified_completion_claim_signal("I refactored the parser.", {}) is None
