"""Tests for core/tool_constants.py — shared tool/lane constants."""
from __future__ import annotations

from core.tool_constants import MUTATING_TOOLS, READ_ONLY_LANES


def test_mutating_tools_is_frozenset():
    assert isinstance(MUTATING_TOOLS, frozenset)


def test_mutating_tools_contains_write_and_edit():
    assert "write_file" in MUTATING_TOOLS
    assert "edit_file" in MUTATING_TOOLS
    assert "read_file" not in MUTATING_TOOLS
    assert "shell" not in MUTATING_TOOLS


def test_read_only_lanes_is_frozenset():
    assert isinstance(READ_ONLY_LANES, frozenset)


def test_read_only_lanes_contains_expected_lanes():
    assert "report" in READ_ONLY_LANES
    assert "review-only" in READ_ONLY_LANES
    assert "investigate" in READ_ONLY_LANES
    assert "prt-review-only" in READ_ONLY_LANES


def test_mutating_tools_and_read_only_lanes_disjoint():
    """Verify no overlap — constants serve different purposes."""
    assert not MUTATING_TOOLS & READ_ONLY_LANES
