"""Tests for core/work_patterns.py — work complexity estimation.

Note: estimate_work_complexity() uses `words()` which splits input into a
set of lowercase alphanumeric tokens via regex, so repeating the same word
does not increase the term count (set dedup).
"""
from __future__ import annotations

from core.work_patterns import build_work_pattern_context, estimate_work_complexity, is_prd_request, is_research_method_question, select_work_pattern


class TestEstimateWorkComplexity:
    """Tests for complexity estimation based on unique text features."""

    def test_empty_simple(self):
        assert estimate_work_complexity("") == "simple"

    def test_none_simple(self):
        assert estimate_work_complexity(None) == "simple"

    def test_short_input_simple(self):
        """Short input without complexity keywords → simple."""
        assert estimate_work_complexity("fix typo in readme") == "simple"

    def test_30_unique_words_triggers_complex(self):
        """> 30 unique terms → complex."""
        # Generate 35 unique words
        words_list = [f"word{i}" for i in range(35)]
        text = " ".join(words_list)
        assert estimate_work_complexity(text) == "complex"

    def test_18_unique_words_triggers_moderate(self):
        """> 18 unique terms → moderate."""
        words_list = [f"word{i}" for i in range(20)]
        text = " ".join(words_list)
        assert estimate_work_complexity(text) == "moderate"

    def test_complex_words_triggers_moderate(self):
        """Has _COMPLEX_WORDS like 'multi' or 'system' → moderate."""
        assert estimate_work_complexity("fix the multi component system architecture") == "moderate"

    def test_surface_words_with_review_triggers_moderate(self):
        """Has surface words + audit/review → moderate."""
        assert estimate_work_complexity("audit the entire codebase") == "moderate"

    def test_four_surface_hits_triggers_complex(self):
        """4+ surface word hits → complex."""
        text = "review the codebase repo performance profile session"
        assert estimate_work_complexity(text) == "complex"

    def test_simple_task_remains_simple(self):
        """Short focused task → simple."""
        assert estimate_work_complexity("add error handling to parse function") == "simple"

    def test_reference_word_triggers_moderate(self):
        """Has reference words like 'clone' or 'mimic' → moderate."""
        assert estimate_work_complexity("clone the existing auth module") == "moderate"

    def test_git_diff_small_simple(self):
        """Small diff → simple (no complexity keywords)."""
        diff = "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-old\n+new"
        assert estimate_work_complexity(diff) == "simple"

    def test_surface_hits_with_review_moderate(self):
        """Surface words like 'codebase' + audit/review → moderate."""
        assert estimate_work_complexity("review the full codebase") == "moderate"


class TestSelectWorkPattern:
    """Tests for select_work_pattern()."""

    def test_empty_returns_none(self):
        assert select_work_pattern("") is None

    def test_fix_returns_pattern(self):
        pat = select_work_pattern("fix the login bug")
        assert pat is not None
        assert pat.complexity in ("simple", "moderate", "complex")

    def test_build_returns_pattern(self):
        pat = select_work_pattern("create a new dashboard component")
        assert pat is not None
        assert pat.category in ("build_create", "design")

    def test_design_build_flag_drives_dna_context(self):
        pat = select_work_pattern("design a new dashboard component")
        assert pat is not None
        assert pat.requires_design_dna is True

        context = build_work_pattern_context("design a new dashboard component")

        assert "MO Internal Build/Design DNA" in context
        assert "Hard rules:" in context
        assert "Lean-build ladder" in context
        assert "Ponytail" not in context

    def test_build_context_includes_mo_native_lean_ladder(self):
        context = build_work_pattern_context("build a small parser helper")

        assert "Lean-build ladder" in context
        assert "already present in MO or the target codebase" in context
        assert "Python stdlib" in context
        assert "Ponytail" not in context

    def test_fix_context_includes_lean_ladder_without_replacing_verification(self):
        context = build_work_pattern_context("fix the broken parser")

        assert "fix/verify" in context
        assert "Lean-build ladder" in context
        assert "Verify" in context or "verify" in context
        assert "Never claim fixed without verification evidence" in context


class TestResearchMethodQuestion:
    """Tests for MO-native research-method guidance."""

    def test_research_method_question_gets_mo_native_context(self):
        assert is_research_method_question("how would you research this codebase?") is True

        context = build_work_pattern_context("how would you research this codebase?")

        assert "research method explanation" in context
        assert "project cwd" in context
        assert "sandbox boundaries" in context
        assert "structural graph" in context
        assert "code graph fallback" in context
        assert "taskboard only when doing real research work" in context
        assert "absent" in context
        assert "Graph context is orientation, not proof" in context

    def test_non_method_research_request_uses_review_pattern(self):
        assert is_research_method_question("research the full codebase and report risks") is False
        assert "review/evidence" in build_work_pattern_context("research the full codebase and report risks")


class TestIsPrdRequest:
    """Tests for is_prd_request()."""

    def test_prd_request(self):
        assert is_prd_request("write a PRD for the new feature") is True

    def test_not_prd_request(self):
        assert is_prd_request("what is a PRD?") is False  # chat question

    def test_empty(self):
        assert is_prd_request("") is False


# ── universalized self-maintenance mindset (adaptive, never gated) ────────────

def test_project_audit_pattern_triggers_on_user_issue_hunting():
    from core.work_patterns import build_work_pattern_context, select_work_pattern

    for text in (
        "find issues in my project please",
        "audit this codebase for problems",
        "diagnose the weaknesses in this repo",
        "run a full audit of the project",
    ):
        pattern = select_work_pattern(text)
        assert pattern is not None and pattern.name == "project_audit", text
        ctx = build_work_pattern_context(text)
        assert "project audit" in ctx
        assert "Catalog confirmed findings BEFORE fixing" in ctx
        assert "empty catalog is a valid result" in ctx


def test_reference_comparison_pattern_triggers_on_compare_requests():
    from core.work_patterns import build_work_pattern_context, select_work_pattern

    for text in (
        "compare my project against the aider repo",
        "benchmark this codebase with that reference framework",
        "what should I adopt from that library?",
    ):
        pattern = select_work_pattern(text)
        assert pattern is not None and pattern.name == "reference_comparison", text
        ctx = build_work_pattern_context(text)
        assert "reference comparison" in ctx
        assert "Zero-adoption is a valid" in ctx


def test_reference_comparison_requires_measurement_for_economy_claims():
    from core.work_patterns import build_work_pattern_context

    ctx = build_work_pattern_context("compare MO token compression against that reference repo")

    assert "baseline-vs-adopt measurement" in ctx
    assert "current behavior" in ctx
    assert "candidate behavior" in ctx
    assert "recoverability/fallback" in ctx


def test_mindset_patterns_do_not_hijack_normal_turns():
    from core.work_patterns import select_work_pattern

    assert select_work_pattern("hi mo") is None
    build = select_work_pattern("build me a snake game")
    assert build is not None and build.name in {"design_build", "build_verify"}
    fix = select_work_pattern("fix the login bug in auth.py")
    assert fix is not None and fix.name == "fix_verify"
