"""Tests for core/gateway_helpers.py — template selector and workflow gating."""
from __future__ import annotations

import re


from core.gateway_helpers import (
    WORKFLOW_GATING_RE,
    is_workflow_control_request,
    select_template,
)


class TestWorkflowControlRequest:
    def test_workflow_candidate_hash_triggers(self):
        assert is_workflow_control_request("workflow-candidate:abcdef1234567890")
        assert is_workflow_control_request("adopt workflow-candidate:deadbeef12345678")

    def test_adopt_workflow_triggers(self):
        assert is_workflow_control_request("adopt the workflow we just built")
        assert is_workflow_control_request("learn a new workflow")

    def test_promote_triggers(self):
        assert is_workflow_control_request("promote this workflow-candidate for review")

    def test_approve_triggers(self):
        assert is_workflow_control_request("approve workflow pattern")

    def test_activate_triggers(self):
        assert is_workflow_control_request("activate workflow-candidate:1234567890abcdef")

    def test_use_workflow_style(self):
        assert is_workflow_control_request("use a different workflow style")

    def test_normal_chat_does_not_trigger(self):
        assert not is_workflow_control_request("hello")
        assert not is_workflow_control_request("fix the bug in agent.py")

    def test_empty_string(self):
        assert not is_workflow_control_request("")
        assert not is_workflow_control_request("   ")

    def test_none_input(self):
        assert not is_workflow_control_request(None)  # type: ignore[arg-type]


class TestSelectTemplate:
    def test_problem_triggers_return_problem_solving(self):
        assert select_template("fix the bug") == "problem_solving"
        assert select_template("debug the issue") == "problem_solving"
        assert select_template("solve this problem") == "problem_solving"
        assert select_template("broken pipe") == "problem_solving"

    def test_build_triggers_return_build_create(self):
        assert select_template("build a new feature") == "build_create"
        assert select_template("create a component") == "build_create"
        assert select_template("implement the design") == "build_create"
        assert select_template("remake that module") == "build_create"
        assert select_template("rework the api") == "build_create"

    def test_review_triggers_return_deep_review(self):
        assert select_template("review the codebase") == "deep_review"
        assert select_template("audit the repo") == "deep_review"
        # 'bug' is in PROBLEM_TRIGGERS, which takes priority over REVIEW_TRIGGERS
        assert select_template("investigate the issue") == "deep_review"
        assert select_template("analyze the diff") == "deep_review"
        assert select_template("search the codebase") == "deep_review"
        assert select_template("scan for issues") == "deep_review"

    def test_new_alone_is_build_create(self):
        """'new' is in BUILD_TRIGGERS but is weaker — still maps to build_create."""
        assert select_template("new module") == "build_create"

    def test_default_is_simple_chat(self):
        assert select_template("hello world") == "simple_chat"
        assert select_template("what does this do") == "simple_chat"
        assert select_template("") == "simple_chat"

    def test_workflow_control_overrides(self):
        assert select_template("adopt workflow") == "simple_chat"
        assert select_template("learn workflow pattern") == "simple_chat"

    def test_problem_over_review(self):
        """Problem triggers take priority over review triggers."""
        # 'bug' is in PROBLEM_TRIGGERS and might overlap with review context
        assert select_template("fix the bug") == "problem_solving"

    def test_build_over_review(self):
        """Strong build triggers take priority over review."""
        assert select_template("build a review system") == "build_create"

    def test_interrogative_failure_diagnosis_is_problem_solving(self):
        # Regression: investigative diagnosis of a failure was treated as chat
        # (no taskboard / no verify-before-claiming), so a real debug turn got none
        # of the problem-solving discipline.
        assert select_template("figure out why the worker keeps crashing") == "problem_solving"
        assert select_template("look into the slow startup") == "problem_solving"
        assert select_template("why does the build keep failing") == "problem_solving"
        # Pure analytical review must still map to deep_review, not problem_solving.
        assert select_template("investigate the issue") == "deep_review"
        assert select_template("audit the repo") == "deep_review"
        # Plain questions are still chat.
        assert select_template("what does this do") == "simple_chat"

    def test_none_input(self):
        assert select_template(None) == "simple_chat"  # type: ignore[arg-type]


class TestWorkflowGatingRE:
    def test_pattern_compiles(self):
        assert isinstance(WORKFLOW_GATING_RE, type(re.compile("")))

    def test_matches_workflow_candidate_hash(self):
        assert WORKFLOW_GATING_RE.search("workflow-candidate:abcdef1234567890")
        assert WORKFLOW_GATING_RE.search("please adopt workflow-candidate:deadbeef12345678")

    def test_matches_adopt_workflow(self):
        assert WORKFLOW_GATING_RE.search("adopt this workflow for the project")

    def test_matches_learn_workflow(self):
        assert WORKFLOW_GATING_RE.search("learn a new workflow pattern")

    def test_no_match_plain_text(self):
        assert not WORKFLOW_GATING_RE.search("hello world")
