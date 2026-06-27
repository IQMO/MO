"""
MO's opt-in PRT fix loop.

Runs one isolated worker-style repair turn:
- safety gate -> fix actionable findings -> commit --amend
- the operator or caller can rerun /prt to verify the amended commit
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.agent.agent import Agent
    from core.review.diff_review import ReviewReport


def _tool_name(tool_definition: dict) -> str:
    return str(tool_definition.get("name") or (tool_definition.get("function") or {}).get("name") or "")


# Finding categories that warrant an auto-generated regression test, opt-in via
# prt.regression_tests. Style/nitpick findings never get a test.
_REGRESSION_CATEGORIES = {"bug_risk", "security", "missing_test", "breaking_change"}


def regression_test_candidates(report: "ReviewReport") -> list:
    """Actionable bug/security/missing-test findings that warrant a regression test."""
    return [
        f for f in report.findings
        if f.is_actionable() and not f.resolved and f.category in _REGRESSION_CATEGORIES
    ]


def regression_prompt_block() -> str:
    """Prompt guidance: write a test that fails-before / passes-after, kept only if it passes."""
    return (
        "\nRegression tests: for each bug/security/missing-test finding above, also write a focused test "
        "under tests/ that FAILS on the pre-fix behavior and PASSES after your fix. Run it with test_runner; "
        "if it does not pass, remove it. Do not add tests for style or nitpick findings.\n"
    )


def run_fix_loop(agent: "Agent", report: "ReviewReport"):
    """Starts a goal-driven fix loop to resolve actionable findings."""
    from core.goal import GoalPlan, GoalStep
    from core.context.workspace_awareness import prt_safe_to_mutate
    
    is_safe, reason = prt_safe_to_mutate(agent)
    if not is_safe:
        print(f"PRT Fix Loop Aborted: {reason}")
        return report
        
    _cfg = getattr(agent, "config", None)
    regression_tests = bool(_cfg.get("prt", {}).get("regression_tests", False)) if isinstance(_cfg, dict) else False
    steps = []
    for i, finding in enumerate(report.findings):
        if finding.is_actionable() and not finding.resolved:
            steps.append(GoalStep(
                id=f"fix-{i}",
                title=f"Fix {finding.severity} finding in {finding.file}: {finding.message}",
                status="pending"
            ))
            
    if not steps:
        return report
        
    reg_candidates = regression_test_candidates(report) if regression_tests else []
    if reg_candidates:
        steps.append(GoalStep(
            id="regression-tests",
            title=f"Write regression tests for {len(reg_candidates)} bug/security finding(s)",
            status="pending"
        ))

    steps.append(GoalStep(
        id="amend",
        title="Commit --amend changes after fixing",
        status="pending"
    ))
    
    objective = f"Fix PRT findings to reach target score (Current: {report.score}/5.0)"
    
    plan = GoalPlan(
        objective=objective,
        steps=steps
    )
    
    prompt = f"[PRT FIX LOOP]\nObjective: {objective}\n\nFindings to fix:\n"
    for finding in report.findings:
        if finding.is_actionable() and not finding.resolved:
            prompt += f"- [{finding.severity}] {finding.file} ({finding.line_range}): {finding.message}\n  Suggestion: {finding.suggestion}\n"
    if reg_candidates:
        prompt += regression_prompt_block()
    prompt += "\nUse edit_file to fix these issues. When done, use shell to 'git commit --amend --no-edit'. Stop when finished."
    
    try:
        # Phase D: isolated worker-style session on the running agent/provider chain.
        from contextlib import nullcontext

        from core.session.session import Session

        allowed = {"edit_file", "read_file", "grep", "shell", "git_status", "test_runner"}
        original_tools = list(getattr(agent, "tool_definitions", []) or [])
        agent.tool_definitions = [tool for tool in original_tools if _tool_name(tool) in allowed]

        fix_system = str(getattr(agent, "system_message", "You are MO.") or "You are MO.")
        fix_session = Session(fix_system + "\n\n## PRT Fix Loop\nResolve only the provided PRT findings. Keep edits minimal, verify locally, and amend only when fixes are complete.")
        isolated = agent.isolated_session(fix_session) if hasattr(agent, "isolated_session") else nullcontext()
        scoped = agent.provider_scope("worker", worker_id="prt-fix-loop") if hasattr(agent, "provider_scope") else nullcontext()
        monitor = getattr(getattr(agent, "gateway", None), "monitor", None)
        try:
            with isolated:
                with scoped:
                    if monitor is not None:
                        agent.run_turn(prompt, monitor=monitor)
                    else:
                        agent.run_turn(prompt)
        finally:
            agent.tool_definitions = original_tools
    except Exception as e:
        print(f"Error in fix loop: {e}")

    # Close the learning loop — honestly. Re-review the amended commit and record
    # only the findings that are actually gone now as "fixed" (verified, not
    # optimistic). This feeds finding_patterns so future reviews adapt, and lights
    # the system_health feedback bridge.
    try:
        from core.review.diff_review import review_diff
        from core.review.finding_patterns import FindingPatterns
        after = review_diff(agent, report.diff_ref)
        after_keys = {(str(f.file), str(f.message)) for f in after.findings}
        pm = FindingPatterns()
        fixed_categories: list[str] = []
        for finding in report.findings:
            if finding.is_actionable() and (str(finding.file), str(finding.message)) not in after_keys:
                finding.resolved = True
                pm.record_finding(finding, "fixed")
                fixed_categories.append(finding.category)
        profile = getattr(agent, "profile", None)
        if fixed_categories and profile is not None and hasattr(profile, "append_profile_learning"):
            try:
                profile.append_profile_learning("prt_fix", {"fixed_categories": sorted(set(fixed_categories))})
            except Exception:
                pass
    except Exception:
        pass

    return plan
