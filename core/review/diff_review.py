"""
MO-native diff review.

Takes a git diff (committed work), runs analysis using MO's own tools:
- Code graph for impact
- grep/read_file for evidence
- Ghost for structured finding generation
- Scorer for evidence-weighted score

No external dependencies. Pure MO.
"""
from __future__ import annotations

import time
import uuid
import json
import os
import re
import sys
import subprocess
from pathlib import Path
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
import traceback

from ..env_utils import int_env
from ..path_defaults import ENV_MO_STATE_HOME


def _prune_review_audit_log(path: Path) -> None:
    max_bytes = max(0, int_env("MO_REVIEW_AUDIT_MAX_BYTES", 1_000_000))
    if max_bytes <= 0:
        return
    try:
        if not path.exists() or path.stat().st_size <= max_bytes:
            return
        keep = max(1, int_env("MO_REVIEW_AUDIT_KEEP_LINES", 2_000))
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-keep:]
        while len(("\n".join(lines) + "\n").encode("utf-8")) > max_bytes and len(lines) > 1:
            lines.pop(0)
        path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    except Exception:
        return


def append_review_audit(report: "ReviewReport"):
    """Append one review report to the audit log.

    Tests are silent by default to avoid polluting local logs; set
    MO_REVIEW_AUDIT_FORCE=1 when testing the audit file directly.
    """
    if os.environ.get("PYTEST_CURRENT_TEST") and os.environ.get("MO_REVIEW_AUDIT_FORCE") != "1":
        return
    state_home_raw = os.environ.get(ENV_MO_STATE_HOME, "").strip()
    state_home = Path(state_home_raw) if state_home_raw else None
    log_path = (state_home / "logs" / "review_audit.jsonl") if state_home else Path("logs/review_audit.jsonl")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(report.to_dict(), ensure_ascii=False) + "\n")
    _prune_review_audit_log(log_path)


PRT_REVIEW_SYSTEM = (
    "You are MO's automated code reviewer — a thorough, constructive mentor, not a gatekeeper. "
    "You review git diffs for correctness, security, and maintainability.\n\n"
    "Rules:\n"
    "- Evidence-based: only flag issues you can point to specific lines for.\n"
    "- Be constructive: every comment should teach something — explain why it matters.\n"
    "- Also note what's done well: include positive observations about clean patterns.\n"
    "- Honest: if nothing is wrong, return an empty findings list.\n"
    "- Calibrate severity to match these score penalties:\n"
    "  • critical (-1.0): security vulnerability, data loss, credential leak\n"
    "  • major (-0.5): functional bug, broken contract, test regression\n"
    "  • minor (-0.1): style, readability, missing edge case\n"
    "  • info (-0.05): nitpick, suggestion\n"
    "- Treat structural impact as orientation; verify against actual diff lines.\n"
    "- Operator preferences (if provided) override generic rules.\n"
    "- Return ONLY valid JSON. No markdown, no extra text outside the JSON."
)


def _review_failure_finding(message: str, explanation: str) -> "ReviewFinding":
    return ReviewFinding(
        id=str(uuid.uuid4()),
        severity="major",
        category="inconsistency",
        file="<review>",
        line_range=[0, 0],
        message=message[:220],
        explanation=explanation[:800],
        rationale="Review infrastructure failure — treat findings as unreliable until re-run.",
        suggestion="Rerun review after fixing the review/provider failure; do not treat this diff as production-ready yet.",
        confidence=1.0,
        evidence_tools=["review_pipeline"],
    )


def _score_target(agent: "Agent") -> float:
    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    prt_cfg = cfg.get("prt", {}) if isinstance(cfg.get("prt", {}), dict) else {}
    try:
        return float(prt_cfg.get("score_target", 4.5) or 4.5)
    except (TypeError, ValueError):
        return 4.5


if TYPE_CHECKING:
    from core.agent.agent import Agent


@dataclass
class ReviewFinding:
    id: str
    severity: str          # critical | major | minor | info
    category: str          # breaking_change | bug_risk | missing_test | security | inconsistency | dead_code | style
    file: str
    line_range: list[int]
    message: str
    explanation: str
    suggestion: str | None
    confidence: float      # 0.0 - 1.0, evidence-weighted
    rationale: str = ""    # why this matters for code health
    evidence_tools: list[str] = field(default_factory=list)
    resolved: bool = False
    resolution_note: str = ""
    
    def is_actionable(self) -> bool:
        """critical + major are actionable (auto-fix)."""
        return self.severity in ("critical", "major")


@dataclass
class ReviewReport:
    diff_ref: str          # commit hash, branch name, or "working-tree"
    files_changed: int
    additions: int
    deletions: int
    findings: list[ReviewFinding]
    score: float           # 0.0 - 5.0
    unresolved_count: int
    affected_tests: list[str]
    created_at: float
    token_usage: dict[str, Any] = field(default_factory=dict)
    structural_impact: dict[str, Any] = field(default_factory=dict)
    score_target: float = 4.5
    positives: list[str] = field(default_factory=list)  # what was done well
    
    @property
    def is_target_met(self) -> bool:
        try:
            target = float(self.score_target)
        except (TypeError, ValueError):
            target = 4.5
        return self.score >= target and self.unresolved_count == 0

    def to_dict(self) -> dict:
        return {
            "diff_ref": self.diff_ref,
            "files_changed": self.files_changed,
            "additions": self.additions,
            "deletions": self.deletions,
            "findings": [vars(f) for f in self.findings],
            "positives": list(self.positives),
            "score": self.score,
            "unresolved_count": self.unresolved_count,
            "affected_tests": self.affected_tests,
            "created_at": self.created_at,
            "token_usage": self.token_usage,
            "structural_impact": self.structural_impact,
            "score_target": self.score_target,
        }


def _removed_symbols_from_diff(diff_text: str) -> list[str]:
    """Function/class names removed by the diff (lines starting with '-')."""
    names: list[str] = []
    for line in diff_text.splitlines():
        if not line.startswith("-") or line.startswith("---"):
            continue
        m = re.search(r"^-\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", line)
        if m:
            names.append(m.group(1))
    return sorted(set(names))


def _callgraph_removed_symbol_findings(diff_text: str, changed_paths: list[str], workspace_root: Path) -> list["ReviewFinding"]:
    """Deterministic check: a removed/renamed symbol still referenced by live callers.

    Uses MO's call graph (no model call) so this finding is fully tool-backed.
    """
    out: list[ReviewFinding] = []
    try:
        from core.graph.callgraph import get_callers
    except Exception:
        return out
    changed_set = {str(p).replace("\\", "/") for p in (changed_paths or []) if p}
    for sym in _removed_symbols_from_diff(diff_text)[:20]:
        try:
            callers = get_callers(sym, cwd=str(workspace_root))
        except Exception:
            continue
        external = []
        for c in callers:
            cf = str(c.get("caller_file") or "").replace("\\", "/")
            if cf and not any(cf.endswith(p) or p.endswith(cf) for p in changed_set):
                external.append(c)
        if external:
            locs = ", ".join(sorted({str(c.get("caller_file") or c.get("caller_label") or "?") for c in external}))[:300]
            out.append(ReviewFinding(
                id=str(uuid.uuid4()), severity="major", category="breaking_change",
                file="<callgraph>", line_range=[0, 0],
                message=f"`{sym}` removed/changed but still referenced by {len(external)} caller(s)",
                explanation=f"The diff removes or renames `{sym}`, but the call graph shows live callers outside the changed files: {locs}",
                rationale="Removing a symbol that other modules still call breaks them at import/run time.",
                suggestion=f"Keep or update `{sym}`, or fix its remaining callers before this lands.",
                confidence=1.0, evidence_tools=["callgraph:get_callers"],
            ))
    return out


def _run_affected_tests(agent: "Agent", affected_tests: list[str], workspace_root: Path) -> tuple[list["ReviewFinding"], dict]:
    """Run the diff's affected tests (bounded) — real, tool-backed evidence.

    A failing affected test becomes a major finding. Gated by prt.run_affected_tests.
    """
    # Never spawn a nested pytest run from inside the test suite itself.
    if os.environ.get("PYTEST_CURRENT_TEST") and os.environ.get("MO_PRT_RUN_TESTS_FORCE") != "1":
        return [], {}
    cfg = getattr(agent, "config", {}) if isinstance(getattr(agent, "config", {}), dict) else {}
    prt_cfg = cfg.get("prt", {}) if isinstance(cfg.get("prt", {}), dict) else {}
    if not prt_cfg.get("run_affected_tests", True):
        return [], {}
    tests = [t for t in (affected_tests or []) if str(t).endswith(".py")][:8]
    if not tests:
        return [], {}
    summary: dict[str, Any] = {"ran": tests}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *tests],
            text=True, capture_output=True,
            timeout=int(prt_cfg.get("test_timeout_s", 180) or 180),
            cwd=str(workspace_root),
        )
        summary["returncode"] = proc.returncode
        if proc.returncode != 0:
            tail = (proc.stdout or proc.stderr or "")[-600:]
            summary["failed"] = True
            return [ReviewFinding(
                id=str(uuid.uuid4()), severity="major", category="missing_test",
                file=tests[0], line_range=[0, 0],
                message=f"Affected tests failed ({len(tests)} file(s) run)",
                explanation=f"PRT ran the affected tests; pytest exited non-zero. Output tail:\n{tail}",
                rationale="A diff that breaks its own affected tests is not production-ready.",
                suggestion="Fix the failing affected tests before this lands.",
                confidence=1.0, evidence_tools=[f"test_runner:{t}" for t in tests],
            )], summary
    except subprocess.TimeoutExpired:
        summary["timeout"] = True
    except Exception:
        summary["error"] = True
    return [], summary


def review_diff(agent: "Agent", diff_ref: str = "HEAD") -> ReviewReport:
    """Full review pipeline:
    1. Parse git diff
    2. Run code graph impact analysis
    3. Generate findings (via Ghost/Agent)
    4. Score each finding by evidence
    5. Return report
    """
    from core.graph.code_graph import affected_tests as graph_affected_tests, analyze_diff_impact
    from core.graph.structural_graph import format_prt_impact, prt_impact_summary
    from core.review.review_scorer import ReviewScorer
    score_target = _score_target(agent)
    raw_workspace = getattr(agent, "workspace", None) or getattr(agent, "project_cwd", None)
    workspace_root = Path(raw_workspace) if isinstance(raw_workspace, (str, Path)) else Path.cwd()
    
    try:
        # 1. Parse diff. A /prt argument may be either a git ref (HEAD) or a
        # project path (README.md). Treat existing project paths as working-tree
        # path reviews so they complete deterministically instead of asking git
        # for an invalid pseudo-ref like README.md~1.
        ref_path = (workspace_root / str(diff_ref or "")).resolve(strict=False)
        is_path_review = bool(str(diff_ref or "").strip()) and ref_path.exists()
        if is_path_review:
            rel_ref = str(ref_path.relative_to(workspace_root.resolve(strict=False))).replace("\\", "/")
            diff_cmd = ["git", "diff", "--", rel_ref]
            stat_cmd = ["git", "diff", "--numstat", "--", rel_ref]
        else:
            rel_ref = ""
            diff_cmd = ["git", "diff", f"{diff_ref}~1", diff_ref]
            stat_cmd = ["git", "diff", "--numstat", f"{diff_ref}~1", diff_ref]
        diff_text = subprocess.check_output(diff_cmd, text=True, stderr=subprocess.DEVNULL, cwd=str(workspace_root))
        stat_out = subprocess.check_output(stat_cmd, text=True, stderr=subprocess.DEVNULL, cwd=str(workspace_root))
        
        files_changed = 0
        additions = 0
        deletions = 0
        changed_file_paths = [rel_ref] if is_path_review and rel_ref else []
        for line in stat_out.strip().splitlines():
            parts = line.split()
            if len(parts) >= 3:
                files_changed += 1
                changed_file_paths.append(parts[2])
                try:
                    additions += int(parts[0])
                    deletions += int(parts[1])
                except ValueError:
                    pass
        if is_path_review and rel_ref and files_changed == 0:
            files_changed = 1
    except Exception:
        diff_text = ""
        files_changed = 0
        additions = 0
        deletions = 0
        changed_file_paths = []

    # Keep the raw, uncompressed diff for deterministic structural checks
    # (call-graph symbol parsing must see the original '-def'/'-class' lines).
    raw_diff_text = diff_text

    # 2. Impact analysis
    impacted_files = analyze_diff_impact(diff_text, root=str(workspace_root))
    affected_tests = graph_affected_tests(diff_text, root=str(workspace_root))
    structural_impact = prt_impact_summary(diff_text, root=workspace_root)
    if not structural_impact.get("impacted_files"):
        structural_impact["impacted_files"] = impacted_files

    # Compute structural risk score for the scorer
    if structural_impact.get("available"):
        communities = int(structural_impact.get("community_count", 0) or 0)
        risk = 0
        if communities >= 2:
            risk += 4
        if communities >= 4:
            risk += 4
        if int(structural_impact.get("cross_community_edge_count", 0) or 0) > 0:
            risk += 3
        if structural_impact.get("god_files_touched"):
            risk += 5
        structural_impact["risk_score"] = risk
    
    # 2.5 Threat Scan
    from core.threat_scan import scan_text
    scan_result = scan_text(diff_text, surface="review")
    if scan_result.blocked:
        report = ReviewReport(
            diff_ref=diff_ref, files_changed=files_changed, additions=additions, deletions=deletions,
            findings=[
                ReviewFinding(
                    id=str(uuid.uuid4()), severity="critical", category="security", file="<diff>",
                    line_range=[0, 0], message=f"Threat scan blocked review: {scan_result.reason()}",
                    explanation="The commit contains patterns that look like prompt injection or secret exfiltration.",
                    rationale="Blocked content can compromise the review pipeline and downstream systems.",
                    suggestion="Remove the injected instructions or secrets before reviewing.",
                    confidence=1.0, evidence_tools=["grep:threat_scan"]
                )
            ],
            positives=[],
            score=0.0, unresolved_count=1, affected_tests=affected_tests, created_at=time.time(),
            structural_impact=structural_impact, score_target=score_target,
        )
        append_review_audit(report)
        return report

    # 2.6 Token Compression & Model Limits
    from core.tool_compress import compress
    diff_text_compressed, compress_stats = compress(diff_text, min_bytes=0)
    compression_saved = 0
    if compress_stats:
        compression_saved = compress_stats.get("saved_chars", 0) // 4
        diff_text = diff_text_compressed
        agent.compression_total_saved += compress_stats["saved_chars"]
        agent.compression_total_ops += 1
        agent.compression_last_pct = compress_stats["saved_pct"]
        
    from core.model_limits import resolve_context_budget_tokens
    budget = resolve_context_budget_tokens("auto", provider="opencode", model="deepseek-v4-pro")
    max_chars = budget * 3
    if len(diff_text) > max_chars:
        diff_text = diff_text[:max_chars] + f"\n... (truncated {len(diff_text) - max_chars} chars due to context budget)"
    
    # 3. Generate findings via Ghost
    findings = []
    token_usage = {}
    
    from core.review.finding_patterns import FindingPatterns
    patterns_mgr = FindingPatterns()
    known_prefs = set()
    for path in changed_file_paths:
        known_prefs.update(patterns_mgr.known_patterns(path))
    
    patterns_text = ""
    if known_prefs:
        patterns_text = "Operator Preferences to consider:\n" + "\n".join([f"- {p}" for p in known_prefs]) + "\n\n"
        
    report_positives: list[str] = []
    if diff_text.strip():
        structural_text = format_prt_impact(structural_impact)
        structural_block = f"{structural_text}\n\n" if structural_text else ""
        prompt = (
            f"Review the following git diff and report any issues:\n\n{diff_text}\n\n"
            f"{structural_block}"
            f"{patterns_text}"
            "Respond ONLY with a JSON object containing two keys:\n"
            "1. \"findings\": a list of findings, each with:\n"
            '  {"id": "unique-id", "severity": "critical|major|minor|info", "category": "bug_risk|security|style|etc", '
            '"file": "filename", "line_range": [start, end], "message": "short msg", '
            '"explanation": "detail", "rationale": "why this matters", '
            '"suggestion": "fix suggestion"}\n'
            "2. \"positives\": a list of strings noting what was done well (can be empty).\n"
            'Example: {"findings": [...], "positives": ["auth.py:12 — clean error boundary pattern"]}'
        )
        try:
            monitor = getattr(getattr(agent, "gateway", None), "monitor", None)
            messages = [{"role": "system", "content": PRT_REVIEW_SYSTEM}, {"role": "user", "content": prompt}]
            response, provider = agent.complete_ghost_no_tools(
                surface="review",
                request=prompt,
                messages=messages,
                max_tokens=4000,
                monitor=monitor
            )
            
            token_usage = getattr(response, "usage", {}) or {}
            if hasattr(token_usage, "total_tokens"):
                token_usage = {"total_tokens": token_usage.total_tokens}
            elif isinstance(token_usage, dict):
                token_usage = dict(token_usage)
            else:
                token_usage = {}
            if compression_saved > 0:
                token_usage["compression_saved"] = compression_saved
            
            content = str(getattr(response, "content", ""))
            
            # Extract JSON list
            json_match = re.search(r"\[.*\]", content, re.DOTALL)
            if json_match:
                raw_root = json.loads(json_match.group(0))
                # Accept both a JSON list (old format) and a JSON object with "findings" key (new format)
                if isinstance(raw_root, dict):
                    raw_findings = raw_root.get("findings", [])
                    report_positives = raw_root.get("positives", [])
                else:
                    raw_findings = raw_root
                    report_positives = []
                for raw in raw_findings:
                    f = ReviewFinding(
                        id=raw.get("id", str(uuid.uuid4())),
                        severity=raw.get("severity", "info"),
                        category=raw.get("category", "inconsistency"),
                        file=raw.get("file", ""),
                        line_range=raw.get("line_range", [0, 0]),
                        message=raw.get("message", ""),
                        explanation=raw.get("explanation", ""),
                        rationale=raw.get("rationale", ""),
                        suggestion=raw.get("suggestion"),
                        confidence=0.3,
                        evidence_tools=[]
                    )
                    
                    # A1. Pure Python Evidence Collection
                    target_path = workspace_root / f.file
                    
                    try:
                        if target_path.exists() and target_path.is_file():
                            f.evidence_tools.append(f"read_file:{f.file}")
                            content = target_path.read_text(errors="ignore")
                            keywords = [w for w in f.message.split() if len(w) > 5]
                            for kw in keywords[:3]:
                                if re.search(re.escape(kw), content, re.IGNORECASE):
                                    f.evidence_tools.append(f"grep:{kw}")
                                    break
                            
                            if f.evidence_tools:
                                f.confidence = 0.8
                    except Exception:
                        traceback.print_exc()
                        
                    if not f.evidence_tools:
                        f.severity = "info"
                        
                    findings.append(f)
                    patterns_mgr.record_finding(f, "reported")
            else:
                preview = content.strip().replace("\n", " ")[:240]
                findings.append(_review_failure_finding(
                    "Review provider returned no JSON findings",
                    f"Expected a JSON list. Provider response preview: {preview or '<empty>'}",
                ))
        except Exception as e:
            findings.append(_review_failure_finding(
                f"Review generation failed: {type(e).__name__}",
                str(e),
            ))

    # 3.5 Deterministic call-graph verification: removed/renamed symbols still
    # referenced by live callers. No model call — fully tool-backed evidence.
    if raw_diff_text.strip():
        findings.extend(_callgraph_removed_symbol_findings(raw_diff_text, changed_file_paths, workspace_root))

    # 3.6 Run the diff's affected tests — real evidence, bounded and gated.
    test_findings, test_summary = _run_affected_tests(agent, affected_tests, workspace_root)
    findings.extend(test_findings)
    if test_summary:
        structural_impact["affected_tests_run"] = test_summary

    report = ReviewReport(
        diff_ref=diff_ref,
        files_changed=files_changed,
        additions=additions,
        deletions=deletions,
        findings=findings,
        positives=report_positives,
        score=5.0,
        unresolved_count=len(findings),
        affected_tests=affected_tests,
        created_at=time.time(),
        token_usage=token_usage,
        structural_impact=structural_impact,
        score_target=score_target,
    )
    
    # 4. Score
    scorer = ReviewScorer()
    report.score = scorer.report_score(report)
    
    append_review_audit(report)
    return report
