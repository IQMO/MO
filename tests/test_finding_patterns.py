"""Tests for core/review/finding_patterns.py — cross-session pattern learning."""
from __future__ import annotations

import json
from core.review.finding_patterns import FindingPatterns


class MockFinding:
    """Minimal ReviewFinding stand-in for pattern tests."""
    def __init__(self, category: str = "bug_risk", message: str = "test finding",
                 file: str = "test.py", severity: str = "major"):
        self.category = category
        self.message = message
        self.file = file
        self.severity = severity
        self.id = "test-id"


class TestFindingPatterns:
    """Tests for the cross-session pattern learning system."""

    def test_record_finding_ignored(self, tmp_path):
        """record_finding with action='ignored' increments ignored counter."""
        patterns = FindingPatterns(history_dir=str(tmp_path / "review_history"))
        finding = MockFinding(category="bug_risk")
        patterns.record_finding(finding, "ignored")

        data = json.loads((tmp_path / "review_history" / "patterns.json").read_text())
        assert data["operator_preferences"]["bug_risk"]["ignored"] == 1

    def test_record_finding_fixed(self, tmp_path):
        """record_finding with action='fixed' increments fixed counter."""
        patterns = FindingPatterns(history_dir=str(tmp_path / "review_history"))
        finding = MockFinding(category="security")
        patterns.record_finding(finding, "fixed")

        data = json.loads((tmp_path / "review_history" / "patterns.json").read_text())
        assert data["operator_preferences"]["security"]["fixed"] == 1

    def test_record_multiple_actions_same_category(self, tmp_path):
        """Multiple recordings accumulate in same category."""
        patterns = FindingPatterns(history_dir=str(tmp_path / "review_history"))
        for _ in range(3):
            patterns.record_finding(MockFinding(category="bug_risk"), "fixed")
        for _ in range(2):
            patterns.record_finding(MockFinding(category="bug_risk"), "ignored")

        data = json.loads((tmp_path / "review_history" / "patterns.json").read_text())
        assert data["operator_preferences"]["bug_risk"]["fixed"] == 3
        assert data["operator_preferences"]["bug_risk"]["ignored"] == 2

    def test_known_patterns_empty(self, tmp_path):
        """No history → empty patterns list."""
        patterns = FindingPatterns(history_dir=str(tmp_path / "review_history"))
        assert patterns.known_patterns("") == []

    def test_known_patterns_fixed_over_ignored(self, tmp_path):
        """More fixed than ignored → preference to fix."""
        patterns = FindingPatterns(history_dir=str(tmp_path / "review_history"))
        for _ in range(3):
            patterns.record_finding(MockFinding(category="security"), "fixed")
        patterns.record_finding(MockFinding(category="security"), "ignored")

        prefs = patterns.known_patterns("")
        assert any("prioritizes fixing" in p for p in prefs)

    def test_known_patterns_ignored_over_fixed(self, tmp_path):
        """More ignored than fixed → preference to ignore."""
        patterns = FindingPatterns(history_dir=str(tmp_path / "review_history"))
        patterns.record_finding(MockFinding(category="style"), "fixed")
        for _ in range(3):
            patterns.record_finding(MockFinding(category="style"), "ignored")

        prefs = patterns.known_patterns("")
        assert any("often ignores" in p for p in prefs)

    def test_known_patterns_per_file(self, tmp_path):
        """known_patterns(file_path) returns file-specific results."""
        patterns = FindingPatterns(history_dir=str(tmp_path / "review_history"))
        # Manually add a file-specific pattern
        data = {"patterns": [
            {"file": "auth.py", "module": "", "description": "Check for auth bypass patterns"},
        ], "operator_preferences": {}}
        (tmp_path / "review_history").mkdir(parents=True, exist_ok=True)
        (tmp_path / "review_history" / "patterns.json").write_text(json.dumps(data))

        prefs = patterns.known_patterns("core/auth.py")
        assert "Check for auth bypass patterns" in prefs

    def test_known_patterns_per_module(self, tmp_path):
        """known_patterns with module match."""
        patterns = FindingPatterns(history_dir=str(tmp_path / "review_history"))
        data = {"patterns": [
            {"file": "", "module": "core", "description": "Module-level pattern for core"},
        ], "operator_preferences": {}}
        (tmp_path / "review_history").mkdir(parents=True, exist_ok=True)
        (tmp_path / "review_history" / "patterns.json").write_text(json.dumps(data))

        prefs = patterns.known_patterns("core/diff_review.py")
        assert "Module-level pattern for core" in prefs

    def test_known_patterns_global(self, tmp_path):
        """known_patterns("") returns all patterns (no file/module filter limits)."""
        patterns = FindingPatterns(history_dir=str(tmp_path / "review_history"))
        data = {"patterns": [
            {"file": "auth.py", "module": "", "description": "Auth check"},
            {"file": "", "module": "utils", "description": "Utils check"},
        ], "operator_preferences": {}}
        (tmp_path / "review_history").mkdir(parents=True, exist_ok=True)
        (tmp_path / "review_history" / "patterns.json").write_text(json.dumps(data))

        prefs = patterns.known_patterns("")
        assert len(prefs) == 0  # empty string matches nothing with subpath check

    def test_operator_preferences_returns_dict(self, tmp_path):
        """operator_preferences() returns the preferences dict."""
        patterns = FindingPatterns(history_dir=str(tmp_path / "review_history"))
        patterns.record_finding(MockFinding(category="bug_risk"), "fixed")
        prefs = patterns.operator_preferences()
        assert "bug_risk" in prefs

    def test_corrupted_file_returns_empty(self, tmp_path):
        """Corrupted patterns.json returns empty data gracefully."""
        patterns = FindingPatterns(history_dir=str(tmp_path / "review_history"))
        (tmp_path / "review_history").mkdir(parents=True, exist_ok=True)
        (tmp_path / "review_history" / "patterns.json").write_text("{{invalid json")
        assert patterns.known_patterns("") == []
        assert patterns.operator_preferences() == {}

    def test_no_duplicate_empty_patterns(self, tmp_path):
        """known_patterns filters out empty strings from results."""
        patterns = FindingPatterns(history_dir=str(tmp_path / "review_history"))
        data = {"patterns": [
            {"file": "test.py", "module": "", "description": ""},
        ], "operator_preferences": {}}
        (tmp_path / "review_history").mkdir(parents=True, exist_ok=True)
        (tmp_path / "review_history" / "patterns.json").write_text(json.dumps(data))
        result = patterns.known_patterns("test.py")
        assert "" not in result
