"""MO — Profile-aware goal auditor.

Zero-token deterministic auditor that reads operator profile files for
quality guardrails during /goal execution. Profile style rules are bounded:
they do not reopen steps that already have tool-backed evidence.

Runs after each iteration and as the final completion gate.
No model calls, no workers, no DoneCleanPacket — just evidence checks
personalized by the operator's own profile preferences.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import traceback

from ..path_defaults import resolve_state_path
from ..tasking import task_evidence
from ..work_patterns import select_work_pattern

FALSE_CERTAINTY_MARKERS = (
    "guaranteed", "definitely fixed", "fully fixed", "certainly fixed",
    "100%", "completely solved", "works perfectly", "can't fail", "cannot fail",
)

@dataclass
class AuditVerdict:
    approved: bool
    findings: list[str] = field(default_factory=list)
    push: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"approved": self.approved, "findings": self.findings, "push": self.push}


@dataclass
class ProfileRule:
    """A single enforceable rule derived from operator profile."""
    name: str
    check_fn: Any  # Callable[[str, list[str]], str | None]

    def check(self, content: str, tool_results: list[str]) -> str | None:
        try:
            return self.check_fn(content, tool_results)
        except Exception:
            return None


class GoalAuditor:
    """Profile-personalized quality gate for /goal iterations."""

    def __init__(self, profile: Any):
        self.profile = profile
        self.rules: list[ProfileRule] = self._load_profile_rules()

    def _load_profile_rules(self) -> list[ProfileRule]:
        """Read operator profile files and build enforceable rules."""
        rules: list[ProfileRule] = []

        # Core rules always active
        rules.append(ProfileRule(
            "no_false_certainty",
            lambda content, tools: "uses certainty language without evidence"
            if any(m in content.lower() for m in FALSE_CERTAINTY_MARKERS)
            and not tools
            and not task_evidence.has_concrete_evidence(content)
            else None
        ))

        rules.append(ProfileRule(
            "evidence_before_claims",
            lambda content, tools: "claims completion without tool evidence"
            if _claims_completion(content) and not tools and not task_evidence.has_concrete_evidence(content)
            else None
        ))

        # Profile-driven rules from operator.md
        profile_path = self._profile_dir()
        if not profile_path:
            return rules

        # Read operator.md for communication style enforcement
        operator_text = self._read_profile_file(profile_path / "operator.md")
        if "evidence-backed" in operator_text.lower():
            rules.append(ProfileRule(
                "operator_evidence_backed",
                lambda content, tools: "operator profile requires evidence-backed answers"
                if len(content.strip()) > 200 and not tools and not task_evidence.has_concrete_evidence(content)
                else None
            ))

        if "concise" in operator_text.lower():
            rules.append(ProfileRule(
                "operator_concise",
                lambda content, _tools: "operator profile prefers concise responses"
                if len(content.strip()) > 8000 and not _has_structured_sections(content)
                else None
            ))

        # Read thinking_model.md for reasoning enforcement
        thinking_text = self._read_profile_file(profile_path / "thinking_model.md")
        if "verify current reality" in thinking_text.lower():
            rules.append(ProfileRule(
                "verify_before_proposing",
                lambda content, tools: "thinking model requires verifying reality before proposing"
                if _proposes_change(content) and not tools
                else None
            ))

        # Read learning.md for learned preferences
        learning_text = self._read_profile_file(profile_path / "learning.md")
        if learning_text.strip() and len(learning_text) > 100:
            # Extract any explicit preferences
            for line in learning_text.splitlines():
                line = line.strip()
                if line.startswith("- ") and ":" in line:
                    key_val = line[2:].split(":", 1)
                    if len(key_val) == 2:
                        key = key_val[0].strip().lower()
                        val = key_val[1].strip().lower()
                        if "minimal" in val or "small" in val:
                            rules.append(ProfileRule(
                                f"learned_{key}",
                                lambda content, tools, _v=val: f"learned preference: {_v}"
                                if not tools and _is_overengineered(content)
                                else None
                            ))
                            break  # One learning rule is enough

        return rules

    def review_iteration(self, step: Any, content: str) -> AuditVerdict:
        """After each iteration: is this step honestly complete?"""
        findings: list[str] = []
        step_title = str(getattr(step, "title", "") or "").lower()
        step_evidence = getattr(step, "evidence", []) or []
        step_status = str(getattr(step, "status", "") or "")
        reopened_count = max(0, int(getattr(step, "reopened_count", 0) or 0))
        iterations_run = max(0, int(getattr(step, "iterations_run", 0) or 0))

        # 0. Approach convergence checks — re-plan repeated/stale step loops.
        if reopened_count >= 3:
            findings.append("approach not converging, re-plan needed")
        if iterations_run >= 4 and not any(task_evidence.evidence_item_is_tool_backed(str(e)) for e in step_evidence):
            findings.append("stale approach, re-plan needed")

        # 1. Evidence check — tool evidence required for completed steps
        if step_status == "completed" and not step_evidence:
            findings.append("step completed without tool evidence")

        # 2. Verification check — test/verify steps need passing tests
        if task_evidence.is_verification_step(step_title):
            if step_status == "completed":
                has_test_evidence = task_evidence.has_verification_tool_evidence(step_evidence)
                if not has_test_evidence:
                    findings.append("verification step lacks test runner evidence")
                if task_evidence.has_failing_tests(content) and not task_evidence.has_passing_after_failure(content):
                    findings.append("verification shows failing tests")
                if not task_evidence.has_passing_verification(content, step_evidence):
                    findings.append("verification step lacks passing test result")

        # 3. Profile-driven rules
        tool_results = [str(e) for e in step_evidence]
        for rule in self.rules:
            violation = rule.check(content, tool_results)
            if violation:
                findings.append(violation)

        push = ""
        if findings:
            push = "Address: " + "; ".join(findings[:3])

        return AuditVerdict(
            approved=not findings,
            findings=findings,
            push=push,
        )

    def extract_learnings(
        self,
        findings: list[str],
        *,
        objective: str = "",
        iterations_run: int = 0,
        reason: str = "",
    ) -> dict[str, Any]:
        """Convert auditor findings into durable profile learning insights.

        Only returns non-empty when findings are high-signal and not already
        recorded. Deduplicates via the source marker so repeated identical
        patterns do not bloat learning.md.
        """
        insights: dict[str, Any] = {}
        if not findings:
            return insights

        text = " ".join(str(f or "").lower() for f in findings)
        iterations = max(0, int(iterations_run or 0))

        # Only record when we have enough signal — a single stray finding is noise.
        if len(findings) < 2 and iterations < 3:
            return insights

        current_focus: list[str] = []
        core_traits: list[str] = []
        evolution: list[str] = []

        # ── Pattern detection ──────────────────────────────────────

        # Verification consistently missing or failing
        verify_markers = {"verification step lacks", "verification shows failing",
                          "contains failing test evidence",
                          "work pattern verification is not proven passing"}
        if any(m in text for m in verify_markers):
            current_focus.append(
                "Goal verification: models often skip or fail verification steps — "
                "require explicit test runner evidence and passing results before marking verify steps complete"
            )
            if "verification step lacks" in text:
                core_traits.append(
                    "Goal audit rule: verification steps must include test_runner tool evidence "
                    "with a passing exit code before the step can be marked complete"
                )

        # Steps completed without tool evidence
        if "without evidence" in text or "without tool evidence" in text or "without any tool-backed evidence" in text:
            evolution.append(
                "Goal evidence gate: steps claimed complete without tool evidence must be "
                "reopened until read_file, grep, shell, test_runner, or other tools produce concrete proof"
            )
            core_traits.append(
                "Goal step completion requires tool-backed evidence — provider prose alone is not proof"
            )

        # Stale goal — no tool progress after many iterations
        if "stale" in text or ("no tool-backed evidence" in text and iterations >= 4):
            current_focus.append(
                "Goal staleness detection: if no tool-backed evidence appears after several "
                "iterations, pause the goal instead of looping — the model may need a smaller step"
            )
            evolution.append(
                "Goal decomposition: when a step produces no tool evidence after 3+ iterations, "
                "it should be split into smaller sub-steps rather than retried as-is"
            )

        # Provider errors blocking goal progress
        if "provider" in text and ("unavailable" in text or "error" in text):
            current_focus.append(
                "Goal resilience: provider errors should pause the goal after 3 consecutive "
                "failures rather than burning iterations on retries"
            )

        # Blocked steps at completion
        if "still blocked" in text or "not completed" in text:
            core_traits.append(
                "Goal completion rule: all steps must reach completed status with evidence "
                "before the auditor can approve the goal"
            )

        # PRT Review Fixes
        if "fix" in text and ("finding" in text or "prt" in text):
            evolution.append(
                "PRT fix loop: when iterating to resolve a PRT finding, the step must "
                "produce passing test or linter evidence to prove the finding is resolved"
            )

        # ── Assemble ───────────────────────────────────────────────
        if current_focus:
            insights["current_focus"] = current_focus
        if core_traits:
            insights["core_traits"] = core_traits
        if evolution:
            insights["evolution"] = evolution

        return insights

    def review_completion(self, plan: Any) -> AuditVerdict:
        """Final gate: is the entire goal honestly done?"""
        findings: list[str] = []
        steps = getattr(plan, "steps", []) or []

        # Every required plan step must be completed with evidence.
        for step in steps:
            if step.status != "completed":
                findings.append(f"step '{step.title}' not completed ({step.status})")
            elif not step.evidence:
                findings.append(f"step '{step.title}' completed without evidence")

        # At least one step must have tool evidence (not just content)
        tool_backed = any(
            any(task_evidence.evidence_item_is_tool_backed(str(e)) for e in step.evidence)
            for step in steps
            if step.status == "completed" and step.evidence
        )
        from core.goal import GoalRunner
        if not tool_backed and GoalRunner._requires_tool_backed_progress(plan):
            findings.append("goal completed without any tool-backed evidence")

        # Check for blocked steps
        blocked = [s for s in steps if s.status == "blocked"]
        if blocked:
            findings.append(f"{len(blocked)} step(s) still blocked: {', '.join(s.title for s in blocked[:3])}")

        # Reuse MO work-pattern DNA: build/fix work cannot complete cleanly
        # unless the verify phase has concrete passing verification evidence.
        pattern = select_work_pattern(getattr(plan, "objective", ""))
        if pattern and pattern.requires_verification:
            verify_steps = [s for s in steps if task_evidence.is_verification_step(getattr(s, "title", ""))]
            if not verify_steps:
                findings.append("work pattern requires a verification step")
            elif not any(
                s.status == "completed"
                and task_evidence.has_verification_tool_evidence(getattr(s, "evidence", []) or [])
                and task_evidence.has_passing_verification("", getattr(s, "evidence", []) or [])
                for s in verify_steps
            ):
                findings.append("work pattern verification is not proven passing")

        for step in steps:
            title = str(getattr(step, "title", "") or "")
            if task_evidence.is_verification_step(title):
                evidence_text = "\n".join(str(e) for e in (getattr(step, "evidence", []) or []))
                if task_evidence.has_failing_tests(evidence_text) and not task_evidence.has_passing_after_failure(evidence_text):
                    findings.append(f"verification step '{title}' contains failing test evidence")

        push = ""
        if findings:
            push = "Before completing: " + "; ".join(findings[:3])

        return AuditVerdict(
            approved=not findings,
            findings=findings,
            push=push,
        )

    def _profile_dir(self) -> Path | None:
        """Resolve the profile directory from the Profile object."""
        profile = self.profile
        if not profile:
            return None
        # Profile._path points to memory/mo.db; profile dir is memory/profile/
        profile_path = getattr(profile, "_path", None)
        if profile_path:
            pdir = Path(profile_path).parent / "profile"
            if pdir.exists():
                return pdir
        # Fallback
        default = Path(resolve_state_path("memory/profile"))
        return default if default.exists() else None

    @staticmethod
    def _read_profile_file(path: Path) -> str:
        """Safely read a profile file."""
        try:
            if path.exists():
                return path.read_text(encoding="utf-8")
        except Exception:
            traceback.print_exc()
        return ""


# ── Profile/local helper functions ────────────────────────────────

def _claims_completion(text: str) -> bool:
    lowered = text.lower()
    return any(m in lowered for m in (
        "fixed", "done", "complete", "completed", "verified", "implemented", "built", "resolved",
    ))


def _has_structured_sections(text: str) -> bool:
    return text.count("##") >= 2 or text.count("- **") >= 3


def _proposes_change(text: str) -> bool:
    lowered = text.lower()
    return any(m in lowered for m in ("should change", "recommend", "suggest", "propose", "let me fix"))


def _is_overengineered(text: str) -> bool:
    lowered = text.lower()
    return any(m in lowered for m in (
        "abstract factory", "design pattern", "enterprise", "microservice",
        "event bus", "plugin system", "dependency injection framework",
    ))
