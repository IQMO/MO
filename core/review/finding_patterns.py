"""
MO's learning system for reviews.

Grows with each review: learns what issues matter
for this specific codebase and operator.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ..atomic_write import atomic_write_json

if TYPE_CHECKING:
    from core.review.diff_review import ReviewFinding


class FindingPatterns:
    """Learns from review history.
    
    Stored in memory/review_history/patterns.json.
    Patterns feed into future reviews as Ghost context.
    """
    def __init__(self, history_dir: str | None = None):
        if history_dir is None:
            # Resolve under the runtime state home (config OR MO_STATE_HOME) so
            # reviews, the fix-loop learning, and system_health share one patterns
            # file — and it never lands in the project cwd.
            from ..path_defaults import resolve_state_path
            history_dir = resolve_state_path("memory/review_history")
        self.history_dir = Path(history_dir)
        self.patterns_file = self.history_dir / "patterns.json"
        
    def _load(self) -> dict:
        if not self.patterns_file.exists():
            return {"patterns": [], "operator_preferences": {}}
        try:
            return json.loads(self.patterns_file.read_text(encoding="utf-8"))
        except Exception:
            return {"patterns": [], "operator_preferences": {}}
            
    def _save(self, data: dict):
        self.history_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.patterns_file, data, indent=2)

    def record_finding(self, finding: "ReviewFinding", action: str):
        """Store finding with its context for future learning.
        action: 'fixed', 'ignored'
        """
        data = self._load()
        prefs = data.get("operator_preferences", {})
        if action == "ignored":
            count = prefs.get(finding.category, {}).get("ignored", 0)
            if finding.category not in prefs:
                prefs[finding.category] = {}
            prefs[finding.category]["ignored"] = count + 1
        elif action == "fixed":
            count = prefs.get(finding.category, {}).get("fixed", 0)
            if finding.category not in prefs:
                prefs[finding.category] = {}
            prefs[finding.category]["fixed"] = count + 1
        data["operator_preferences"] = prefs
        self._save(data)

    def known_patterns(self, file_path: str) -> list[str]:
        """Return patterns common for this file/module."""
        data = self._load()
        patterns = []
        
        # Check operator preferences for general guidance
        prefs = data.get("operator_preferences", {})
        for category, stats in prefs.items():
            fixed = stats.get("fixed", 0)
            ignored = stats.get("ignored", 0)
            if fixed > ignored and fixed > 0:
                patterns.append(f"Operator prioritizes fixing {category} issues.")
            elif ignored > fixed and ignored > 0:
                patterns.append(f"Operator often ignores {category} issues.")
                
        # Check specific patterns
        for p in data.get("patterns", []):
            p_file = p.get("file", "")
            p_module = p.get("module", "")
            if (p_file and p_file in file_path) or (p_module and p_module in file_path):
                patterns.append(p.get("description", ""))
                
        return [p for p in patterns if p]

    def record_meta_preference(self, category: str, action: str = "fixed") -> None:
        """Record a compact cross-learning preference signal."""
        data = self._load()
        prefs = data.get("operator_preferences", {})
        cat = str(category or "general")[:80]
        if cat not in prefs:
            prefs[cat] = {}
        key = "ignored" if action == "ignored" else "fixed"
        prefs[cat][key] = int(prefs[cat].get(key, 0) or 0) + 1
        data["operator_preferences"] = prefs
        self._save(data)

    def operator_preferences(self) -> dict:
        """What does this operator care about?"""
        return self._load().get("operator_preferences", {})
