"""Tests for the opt-in PRT regression-test generation (core/review/review_iteration.py).

The feature is additive and off by default: only actionable bug/security/
missing-test findings become regression-test candidates, and the prompt guidance
requires the test to fail pre-fix and pass after (kept only if it passes).
"""
from __future__ import annotations

from contextlib import contextmanager
from unittest.mock import patch

from core.review.diff_review import ReviewFinding, ReviewReport
from core.review.review_iteration import (
    _REGRESSION_CATEGORIES,
    regression_prompt_block,
    regression_test_candidates,
    run_fix_loop,
)


def _finding(severity: str, category: str, fid: str = "f") -> ReviewFinding:
    return ReviewFinding(
        id=fid, severity=severity, category=category, file="core/x.py",
        line_range=[1, 2], message="msg", explanation="why", suggestion="do x",
        confidence=1.0,
    )


def _report(findings: list[ReviewFinding]) -> ReviewReport:
    return ReviewReport(
        diff_ref="working-tree", files_changed=1, additions=1, deletions=0,
        findings=findings, score=3.0, unresolved_count=len(findings),
        affected_tests=[], created_at=0.0,
    )


def test_actionable_bug_finding_is_a_candidate():
    assert len(regression_test_candidates(_report([_finding("major", "bug_risk")]))) == 1
    assert len(regression_test_candidates(_report([_finding("critical", "security")]))) == 1
    assert len(regression_test_candidates(_report([_finding("major", "missing_test")]))) == 1


def test_style_or_nitpick_finding_is_not_a_candidate():
    # actionable severity but a non-bug category must NOT get a regression test
    assert regression_test_candidates(_report([_finding("major", "style")])) == []
    assert regression_test_candidates(_report([_finding("major", "dead_code")])) == []


def test_non_actionable_severity_never_a_candidate():
    assert regression_test_candidates(_report([_finding("minor", "bug_risk")])) == []
    assert regression_test_candidates(_report([_finding("info", "security")])) == []


def test_resolved_finding_excluded():
    f = _finding("critical", "security")
    f.resolved = True
    assert regression_test_candidates(_report([f])) == []


def test_prompt_block_demands_fail_before_pass_after_and_runs_it():
    block = regression_prompt_block()
    assert "FAILS on the pre-fix" in block
    assert "PASSES after" in block
    assert "test_runner" in block
    assert "remove it" in block  # discard if it doesn't pass


def test_categories_cover_the_bug_classes():
    assert {"bug_risk", "security", "missing_test", "breaking_change"} <= _REGRESSION_CATEGORIES
    assert "style" not in _REGRESSION_CATEGORIES


class _FakeAgent:
    """Minimal running-agent stand-in that captures the fix-loop prompt."""

    def __init__(self, config):
        self.workspace = "/tmp/t"
        self.system_message = "sys"
        self.gateway = None
        self.config = config
        self.tool_definitions = [
            {"type": "function", "function": {"name": "edit_file"}},
            {"type": "function", "function": {"name": "test_runner"}},
        ]
        self.prompts: list[str] = []

    @contextmanager
    def isolated_session(self, session):
        yield

    @contextmanager
    def provider_scope(self, surface, worker_id=""):
        yield

    def run_turn(self, prompt, monitor=None):
        self.prompts.append(prompt)
        return "done"


@patch("core.workspace_awareness.prt_safe_to_mutate")
def test_flag_on_injects_regression_guidance_into_prompt(mock_safe):
    mock_safe.return_value = (True, "")
    agent = _FakeAgent({"prt": {"regression_tests": True}})
    run_fix_loop(agent, _report([_finding("major", "bug_risk")]))
    assert agent.prompts and "Regression tests:" in agent.prompts[0]


@patch("core.workspace_awareness.prt_safe_to_mutate")
def test_flag_off_omits_regression_guidance(mock_safe):
    mock_safe.return_value = (True, "")
    agent = _FakeAgent({"prt": {"regression_tests": False}})
    run_fix_loop(agent, _report([_finding("major", "bug_risk")]))
    assert agent.prompts and "Regression tests:" not in agent.prompts[0]


@patch("core.workspace_awareness.prt_safe_to_mutate")
def test_non_dict_config_defaults_off(mock_safe):
    mock_safe.return_value = (True, "")
    agent = _FakeAgent(config=None)  # non-dict config must not error or enable the feature
    run_fix_loop(agent, _report([_finding("major", "bug_risk")]))
    assert agent.prompts and "Regression tests:" not in agent.prompts[0]
