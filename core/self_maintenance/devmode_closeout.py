"""Terminal closeout gates for owner-only self-maintenance protocols."""
from __future__ import annotations

from pathlib import Path
import re

from ..owner_protocols import (
    is_owner_maintenance_activation,
    is_owner_interface_audit_activation,
    is_owner_comparison_activation,
)
from ..protocol_kernel import (
    OWNER_COMPARISON_PROTOCOL,
    OWNER_MAINTENANCE_PROTOCOL,
    required_artifacts,
    required_closeout_terms,
)

def _owner_maintenance_future_stamp_violation() -> str | None:
    """Block when the active session dir's stamp is implausibly far from the real session
    time — a hand-typed/skewed stamp from skipping session_stamp.py. Catches BOTH skews:
    FUTURE (e.g. the T1930 dir created at 18:56) and PAST relative to the actual session
    start (observed mo-1782177115: a `2026-06-23T0112` dir created at 03:14, ~2h before the
    session started). Past-skew is measured against the live monitor's start time, NOT
    `now`, with a generous 90-min margin so a long-but-legitimate run is never flagged."""
    try:
        from datetime import datetime, timedelta
        from pathlib import Path
        from ..path_defaults import mo_home
        root = mo_home() / "memory" / "devmode"
        if not root.is_dir():
            return None
        dirs = [d for d in root.iterdir() if d.is_dir() and d.name[:1].isdigit()]
        if not dirs:
            return None
        latest = max(dirs, key=lambda d: d.stat().st_mtime)  # the actively-written session, not name-sorted
        try:
            stamp_dt = datetime.strptime(latest.name, "%Y-%m-%dT%H%M")
        except ValueError:
            return None
        now = datetime.now()
        if stamp_dt > now + timedelta(minutes=5):
            drift = (stamp_dt - now).total_seconds() / 60.0
            return (
                f"the session dir '{latest.name}' is stamped ~{drift:.0f} min in the FUTURE "
                f"(local now {now:%Y-%m-%dT%H%M}) — a hand-typed/skewed stamp from "
                "skipping session_stamp.py. Rename the dir to the local-time stamp before finishing."
            )
        # Past-skew vs the session's actual start, parsed from the live monitor filename
        # (backend_monitor-YYYYMMDD-HHMMSS-...). A correct stamp sits within the session
        # window; one stamped well BEFORE the session began is hand-typed/skewed.
        try:
            from ..backend_monitor import latest_monitor_path
            mon = latest_monitor_path()
            m = re.search(r"backend_monitor-(\d{8})-(\d{6})", Path(mon).name) if mon else None
            if m:
                session_start = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
                if stamp_dt < session_start - timedelta(minutes=90):
                    drift = (session_start - stamp_dt).total_seconds() / 60.0
                    return (
                        f"the session dir '{latest.name}' is stamped ~{drift:.0f} min BEFORE this "
                        f"session actually started ({session_start:%Y-%m-%dT%H%M}, from the live "
                        "monitor) — a hand-typed/skewed stamp from skipping session_stamp.py. "
                        "Rename the dir to the correct local-time stamp before finishing."
                    )
        except Exception:
            pass
        return None
    except Exception:
        return None


_MATRIX_REPO_PATH_RE = re.compile(r"(?:core|interface|tools|tests)/[\w./-]+\.py")


def _owner_maintenance_required_artifacts() -> tuple[str, ...]:
    """Session-local artifacts required before a DEVMODE closeout can be terminal."""
    try:
        return required_artifacts(OWNER_MAINTENANCE_PROTOCOL)
    except Exception:
        return (
            "summary.md",
            "workflow.md",
            "catalog.md",
            "capability-matrix.md",
            "economy.md",
            "manifest.json",
        )


def _capability_matrix_missing_paths(text: str) -> list[str]:
    """Return repo .py paths a capability matrix marks EXISTING/ACTIVE that do not
    resolve on disk — i.e. the matrix was built from stale data, not live source.
    Paths resolve against the process cwd (the project root for a real run)."""
    missing: list[str] = []
    for line in str(text or "").splitlines():
        if "existing/active" not in line.lower():
            continue
        for raw in _MATRIX_REPO_PATH_RE.findall(line):
            candidate = raw.strip("`*_ ")
            if candidate and not Path(candidate).exists() and candidate not in missing:
                missing.append(candidate)
    return missing


def _owner_maintenance_closeout_evidence_violation(
    final_text: str,
    *,
    monitor_path: str | Path | None = None,
    session_ids: "set[str] | frozenset[str] | None" = None,
    frozen_error_count: int | None = None,
    session_dir: "str | Path | None" = None,
) -> str | None:
    """Deterministic contradiction between a clean OWNER_MAINTENANCE closeout and runtime
    truth — the internalized watcher. Returns a one-line block reason, or None.
    Fail-open: any error returns None so it can never wedge a legitimate closeout.

    ``frozen_error_count`` (when provided) is the count frozen at the FIRST closeout write;
    the gate owns THAT number instead of re-reading the live monitor, so post-freeze
    closeout-edit errors cannot move the target and loop the gate forever."""
    try:
        text = _owner_maintenance_terminal_marker_text(final_text) or ""
        if not text.startswith("[OWNER_MAINTENANCE COMPLETE]"):
            return None
        # 1. real tool errors must be explicitly owned — not denied, not merely
        #    adjacent to a stray "economy.md" mention or a loose digit. Use the FROZEN
        #    terminal count if one was captured at closeout; else scope to the Main-MO run
        #    (exclude Ghost/desktop turns that share the monitor file) live.
        if frozen_error_count is not None:
            errs = int(frozen_error_count)
        else:
            from ..backend_monitor import GHOST_SURFACES, active_monitor_path, economy_summary
            if monitor_path is None:
                monitor_path = active_monitor_path()
            errs = int(economy_summary(
                monitor_path,
                session_ids=session_ids,
                exclude_surfaces=GHOST_SURFACES,
            ).get("tool_errors", 0) or 0)
        if errs > 0:
            low = final_text.lower()
            denies = any(p in low for p in (
                "no tool error", "0 tool error", "zero tool error", "no errors",
                "all tool calls succeeded", "no tool calls failed",
            ))
            owns = bool(re.search(rf"\b{errs}\b[^.\n]{{0,30}}tool[ _-]?error", final_text, re.I)) or \
                bool(re.search(rf"tool[ _-]?error[^.\n]{{0,30}}\b{errs}\b", final_text, re.I))
            if denies or not owns:
                return (
                    f"economy.md records {errs} tool error(s) this session — state the count "
                    "explicitly and classify each (recovered/benign/unresolved); a clean "
                    "closeout that omits or denies them is blocked."
                )
        # 1b. the error ledger must own the ACTUAL erroring tools (monitor truth), not a
        #     mis-attributed/confabulated tool. The monitor names which tools raised
        #     error=True; a clean closeout that names none of them is a false ledger.
        #     Only when the monitor is explicitly scoped (real run threads session_ids /
        #     monitor_path) — never read an ambient/unscoped monitor that could false-block.
        if monitor_path is not None or session_ids is not None:
            try:
                from ..backend_monitor import (
                    GHOST_SURFACES as _GS,
                    active_monitor_path as _amp,
                    economy_summary as _es,
                )
                _mp = monitor_path or _amp()
                _error_tools = [
                    t for t in (_es(_mp, session_ids=session_ids, exclude_surfaces=_GS).get("error_tools") or [])
                    if t
                ]
                ownership_text = _owner_maintenance_tool_error_ownership_text(final_text)
                missing_tools = [
                    tool for tool in _error_tools
                    if tool.lower() not in ownership_text.lower()
                ]
                if missing_tools:
                    return (
                        "the error ledger is not monitor-truthful: the monitor records tool error(s) "
                        f"on {', '.join(_error_tools)}, but the closeout does not name "
                        f"{', '.join(missing_tools)}. Report each "
                        "erroring tool by its real name from economy/monitor evidence."
                    )
            except Exception:
                pass
        # 2. the closeout artifacts must actually EXIST in the bound session dir. The
        #    manifest defines the session-local artifact contract; if the stop gate only
        #    checks a subset, a run can close with an explicitly missing expected file
        #    (observed live: capability-matrix.md was missing while manifest.status was
        #    complete). Only enforced when a dir is bound.
        if session_dir is not None:
            try:
                sd = Path(session_dir)
                missing = [n for n in _owner_maintenance_required_artifacts()
                           if not (sd / n).is_file()]
                if missing:
                    return (
                        "the session dir is missing required closeout artifact(s): "
                        f"{', '.join(missing)} — write them before [OWNER_MAINTENANCE COMPLETE]."
                    )
                # capability-matrix.md must not mark a deleted/relocated source path as
                # EXISTING/ACTIVE (the stale-baseline blind spot). A missing path means the
                # matrix was carried forward, not rebuilt from live source.
                matrix = sd / "capability-matrix.md"
                if matrix.is_file():
                    stale = _capability_matrix_missing_paths(matrix.read_text(encoding="utf-8", errors="replace"))
                    if stale:
                        return (
                            "capability-matrix.md marks deleted/nonexistent source path(s) as "
                            f"EXISTING/ACTIVE: {', '.join(stale[:3])} — rebuild the matrix from live "
                            "source before [OWNER_MAINTENANCE COMPLETE]."
                        )
            except Exception:
                pass
        # 3. the session dir must carry a local-time stamp, not a future/skewed one.
        return _owner_maintenance_future_stamp_violation()
    except Exception:
        return None


def owner_maintenance_final_allows_stop(
    user_input: str,
    final_text: str,
    *,
    monitor_path: str | Path | None = None,
    session_ids: "set[str] | frozenset[str] | None" = None,
    frozen_error_count: int | None = None,
    session_dir: "str | Path | None" = None,
) -> bool:
    """Return True only when a OWNER_MAINTENANCE final answer is a real stop boundary."""
    if not is_owner_maintenance_activation(user_input):
        return True
    text = _owner_maintenance_terminal_prefix_text(final_text)
    if not text:
        return False
    # Don't block the other protocol's completions — OWNER_COMPARISON gate is responsible for those
    if text.startswith("[OWNER_COMPARISON COMPLETE]") or text.startswith("[OWNER_COMPARISON BLOCKED]"):
        return True
    if text.startswith("[OWNER_MAINTENANCE BLOCKED]"):
        return _owner_maintenance_blocked_has_hard_boundary(text)
    if text.startswith("[OWNER_MAINTENANCE COMPLETE]"):
        if _owner_maintenance_completion_reports_open_work(text):
            return False
        if _owner_maintenance_closeout_evidence_violation(
            final_text, monitor_path=monitor_path, session_ids=session_ids,
            frozen_error_count=frozen_error_count, session_dir=session_dir,
        ):
            return False
        return True
    allowed_prefixes = (
        "[MAX PROVIDER REQUESTS]",
        "[MAX TOOL ROUNDS]",
        "MO provider error:",
        "MO interface error:",
        "Provider returned no visible answer",
        "Provider repeatedly produced malformed",
    )
    return text.startswith(allowed_prefixes)


def owner_comparison_final_allows_stop(user_input: str, final_text: str) -> bool:
    """Return True only when a OWNER_COMPARISON answer is a terminal comparison boundary."""
    if not is_owner_comparison_activation(user_input):
        return True
    text = _owner_maintenance_terminal_prefix_text(final_text)
    if not text:
        return False
    # Don't block the other protocol's completions — OWNER_MAINTENANCE gate is responsible for those
    if text.startswith("[OWNER_MAINTENANCE COMPLETE]") or text.startswith("[OWNER_MAINTENANCE BLOCKED]"):
        return True
    if text.startswith("[OWNER_COMPARISON BLOCKED]"):
        return _owner_maintenance_blocked_has_hard_boundary(text)
    if text.startswith("[OWNER_COMPARISON COMPLETE]"):
        if _owner_maintenance_completion_reports_open_work(text):
            return False
        if _owner_comparison_reports_default_target_drift(user_input, text):
            return False
        if _owner_comparison_missing_closeout_terms(text):
            return False
        return True
    allowed_prefixes = (
        "[MAX PROVIDER REQUESTS]",
        "[MAX TOOL ROUNDS]",
        "MO provider error:",
        "MO interface error:",
        "Provider returned no visible answer",
        "Provider repeatedly produced malformed",
    )
    return text.startswith(allowed_prefixes)


def owner_maintenance_continuation_instruction(
    user_input: str,
    final_text: str,
    *,
    monitor_path: str | Path | None = None,
    session_ids: "set[str] | frozenset[str] | None" = None,
    frozen_error_count: int | None = None,
    session_dir: "str | Path | None" = None,
) -> str:
    """Explain why a OWNER_MAINTENANCE stop claim was rejected and what must happen next."""
    base = (
        "[OWNER_MAINTENANCE AUTONOMY] Do not stop at a checkpoint, report, or approval question. "
        "Continue with the next evidence-backed action. Finalize only with [OWNER_MAINTENANCE COMPLETE] "
        "when the protocol is complete or [OWNER_MAINTENANCE BLOCKED] for a real "
        "tool/provider/timeout/sandbox/permission/safety boundary."
    )
    if not is_owner_maintenance_activation(user_input):
        return base
    text = _owner_maintenance_terminal_prefix_text(final_text)
    if text.startswith("[OWNER_MAINTENANCE COMPLETE]") and _owner_maintenance_completion_reports_open_work(text):
        return (
            "[OWNER_MAINTENANCE AUTONOMY] Your last answer claimed [OWNER_MAINTENANCE COMPLETE] while still "
            "reporting actionable open work (unresolved/open/carried-forward findings, failed "
            "checks, or a next target). That is not a terminal state. Do not repeat the same "
            "completion report. Continue from the named items now: RESOLVE the actionable ones "
            "with verification. Items that are genuinely the operator's call are allowed to remain "
            "— but you must classify each EXPLICITLY as operator-decision pending / supervised "
            "fix-lane / recorded observation / accepted deferred (do NOT rewrite a real deferred "
            "item as RESOLVED, and do NOT claim 'Remaining: none' when such items exist). Finalize "
            "with: 'No actionable product work remains; operator-decision items remain: <list, or none>.'"
        )
    _violation = _owner_maintenance_closeout_evidence_violation(
        final_text, monitor_path=monitor_path, session_ids=session_ids,
        frozen_error_count=frozen_error_count, session_dir=session_dir,
    )
    if text.startswith("[OWNER_MAINTENANCE COMPLETE]") and _violation:
        return (
            "[OWNER_MAINTENANCE AUTONOMY] Your [OWNER_MAINTENANCE COMPLETE] contradicts runtime evidence: "
            f"{_violation} Do not repeat the same completion — read economy.md, correct the "
            "tool-error ledger and report from it, then finalize."
        )
    if text.startswith("[OWNER_MAINTENANCE BLOCKED]") and not _owner_maintenance_blocked_has_hard_boundary(text):
        return (
            "[OWNER_MAINTENANCE AUTONOMY] Your last answer used [OWNER_MAINTENANCE BLOCKED] without a current hard "
            "tool/provider/timeout/sandbox/permission/safety boundary. Work remaining is not a "
            "blocker. Continue from the continuation capsule or next unresolved action now."
        )
    return base


def owner_comparison_continuation_instruction(user_input: str, final_text: str) -> str:
    """Explain why a OWNER_COMPARISON stop claim was rejected and what must happen next."""
    base = (
        "[OWNER_COMPARISON CONTINUATION] Do not stop at initial capture or preliminary comparison. "
        "Continue the read-only OWNER_COMPARISON protocol until source roles, structured evidence usage, "
        "comparison matrix, adoption/reject/defer dispositions, artifact path, and exact next "
        "approval decision are complete. Finalize only with [OWNER_COMPARISON COMPLETE] or [OWNER_COMPARISON BLOCKED] "
        "for a real tool/provider/timeout/sandbox/permission/safety boundary. Preferred final "
        "labels: Target, Matrix, Adoption, Reject, Defer/Recheck, Artifacts, Approval."
    )
    if not is_owner_comparison_activation(user_input):
        return base
    text = _owner_maintenance_terminal_prefix_text(final_text)
    if text.startswith("[OWNER_COMPARISON COMPLETE]") and _owner_maintenance_completion_reports_open_work(text):
        return (
            "[OWNER_COMPARISON CONTINUATION] Your last answer claimed [OWNER_COMPARISON COMPLETE] while still reporting "
            "remaining, deferred, open, failed, or carried-forward work. Continue from those named "
            "items now, or close them as reject/defer/no-action with evidence before completing."
        )
    if text.startswith("[OWNER_COMPARISON COMPLETE]") and _owner_comparison_reports_default_target_drift(user_input, text):
        return (
            "[OWNER_COMPARISON CONTINUATION] Your OWNER_COMPARISON closeout drifted from the default target. Current MO "
            "workspace is the adoption target; operator-supplied paths are read-only references "
            "unless the operator explicitly named another target. Rewrite/continue the matrix and "
            "adoption plan for current MO, not for a reference path. The closeout must include "
            "Target: current MO workspace."
        )
    if text.startswith("[OWNER_COMPARISON COMPLETE]"):
        missing = _owner_comparison_missing_closeout_terms(text)
        if missing:
            return (
                "[OWNER_COMPARISON CONTINUATION] Your [OWNER_COMPARISON COMPLETE] report is missing required closeout "
                f"terms: {', '.join(missing)}. Continue and produce the final report with these "
                "literal labels before final closeout: Target, Matrix, Adoption, Reject, Defer/Recheck, "
                "Artifacts, Approval. Do not repeat a summary-only closeout."
            )
    if text.startswith("[OWNER_COMPARISON BLOCKED]") and not _owner_maintenance_blocked_has_hard_boundary(text):
        return (
            "[OWNER_COMPARISON CONTINUATION] Your last answer used [OWNER_COMPARISON BLOCKED] without a current hard "
            "tool/provider/timeout/sandbox/permission/safety boundary. Work remaining is not a "
            "blocker. Continue the comparison from the next evidence-backed action."
        )
    return base


def owner_maintenance_task_truth_continuation_instruction() -> str:
    """Tell OWNER_MAINTENANCE how to recover from a terminal claim with open task truth."""
    return (
        "[OWNER_MAINTENANCE AUTONOMY] Completion is not allowed while MO's task/protocol truth still "
        "has open work. Do not repeat the same completion report. Continue from the active "
        "taskboard/protocol row: run the next evidence-backed action, or if the active row is "
        "genuinely done, call `complete_task` and verify open task count is zero before the final "
        "[OWNER_MAINTENANCE COMPLETE]. If the only rejection was `taskboard_done_claim_conflict`, do not "
        "inspect taskboard source, storage, or trace paths before that `complete_task` call; inspect "
        "implementation only if `complete_task` is unavailable or fails. Use [OWNER_MAINTENANCE BLOCKED] "
        "only for a real hard runtime/tool/provider/safety boundary."
    )


def owner_comparison_task_truth_continuation_instruction() -> str:
    """Tell OWNER_COMPARISON how to recover from a terminal claim with open task truth."""
    return (
        "[OWNER_COMPARISON CONTINUATION] Completion is not allowed while MO's task/protocol truth still "
        "has open work. Do not repeat the same completion report. Continue from the active "
        "OWNER_COMPARISON taskboard row: run the next evidence-backed action, or if the active row is "
        "genuinely done, call `complete_task` and verify open task count is zero before the final "
        "[OWNER_COMPARISON COMPLETE]. If the only rejection was `taskboard_done_claim_conflict`, do not "
        "inspect taskboard source, storage, or trace paths before that `complete_task` call; inspect "
        "implementation only if `complete_task` is unavailable or fails. Use [OWNER_COMPARISON BLOCKED] "
        "only for a real hard runtime/tool/provider/safety boundary."
    )


def owner_interface_audit_final_allows_stop(user_input: str, final_text: str) -> bool:
    """Return True only when an OWNER_INTERFACE_AUDIT final answer is a real stop boundary.

    Mirrors the OWNER_MAINTENANCE gate (OWNER_INTERFACE_AUDIT's improve lane is OWNER_MAINTENANCE-shaped):
    completion is rejected while open work is reported; BLOCKED requires a real
    hard boundary. Other protocols' markers are deferred to their own gates.
    """
    if not is_owner_interface_audit_activation(user_input):
        return True
    text = _owner_maintenance_terminal_prefix_text(final_text)
    if not text:
        return False
    if text.startswith(("[OWNER_MAINTENANCE COMPLETE]", "[OWNER_MAINTENANCE BLOCKED]", "[OWNER_COMPARISON COMPLETE]", "[OWNER_COMPARISON BLOCKED]")):
        return True
    if text.startswith("[OWNER_INTERFACE_AUDIT BLOCKED]"):
        return _owner_maintenance_blocked_has_hard_boundary(text)
    if text.startswith("[OWNER_INTERFACE_AUDIT COMPLETE]"):
        if _owner_maintenance_completion_reports_open_work(text):
            return False
        return True
    allowed_prefixes = (
        "[MAX PROVIDER REQUESTS]",
        "[MAX TOOL ROUNDS]",
        "MO provider error:",
        "MO interface error:",
        "Provider returned no visible answer",
        "Provider repeatedly produced malformed",
    )
    return text.startswith(allowed_prefixes)


def owner_interface_audit_continuation_instruction(user_input: str, final_text: str) -> str:
    """Explain why an OWNER_INTERFACE_AUDIT stop claim was rejected and what must happen next."""
    base = (
        "[OWNER_INTERFACE_AUDIT CONTINUATION] Do not stop at a checkpoint, partial UX audit, or approval "
        "question. Continue the interface diagnosis/adoption protocol with the next "
        "evidence-backed action. Finalize only with [OWNER_INTERFACE_AUDIT COMPLETE] when the protocol is "
        "complete or [OWNER_INTERFACE_AUDIT BLOCKED] for a real tool/provider/timeout/sandbox/permission/safety "
        "boundary."
    )
    if not is_owner_interface_audit_activation(user_input):
        return base
    text = _owner_maintenance_terminal_prefix_text(final_text)
    if text.startswith("[OWNER_INTERFACE_AUDIT COMPLETE]") and _owner_maintenance_completion_reports_open_work(text):
        return (
            "[OWNER_INTERFACE_AUDIT CONTINUATION] Your last answer claimed [OWNER_INTERFACE_AUDIT COMPLETE] while also "
            "reporting actionable open/failed UX work. That is not a terminal state. "
            "Continue from the named open findings now: fix the actionable ones with verification, "
            "adopt/reject the comparison candidates. Items that are genuinely the operator's call "
            "may remain if classified EXPLICITLY as operator-decision pending / supervised fix-lane "
            "/ recorded observation / accepted deferred (do NOT rewrite a real deferred item as "
            "RESOLVED). Finalize with: 'No actionable UX work remains; operator-decision items "
            "remain: <list, or none>.'"
        )
    if text.startswith("[OWNER_INTERFACE_AUDIT BLOCKED]") and not _owner_maintenance_blocked_has_hard_boundary(text):
        return (
            "[OWNER_INTERFACE_AUDIT CONTINUATION] Your last answer used [OWNER_INTERFACE_AUDIT BLOCKED] without a current hard "
            "tool/provider/timeout/sandbox/permission/safety boundary. Work remaining is not a "
            "blocker. Continue from the next unresolved UX finding now."
        )
    return base


def owner_interface_audit_task_truth_continuation_instruction() -> str:
    """Tell OWNER_INTERFACE_AUDIT how to recover from a terminal claim with open task truth."""
    return (
        "[OWNER_INTERFACE_AUDIT CONTINUATION] Completion is not allowed while MO's task/protocol truth still "
        "has open work. Do not repeat the same completion report. Continue from the active "
        "OWNER_INTERFACE_AUDIT taskboard row: run the next evidence-backed action, or if the active row is "
        "genuinely done, call `complete_task` and verify open task count is zero before the final "
        "[OWNER_INTERFACE_AUDIT COMPLETE]. Use [OWNER_INTERFACE_AUDIT BLOCKED] only for a real hard "
        "runtime/tool/provider/safety boundary."
    )


def _owner_comparison_missing_closeout_terms(text: str) -> list[str]:
    """Return missing OWNER_COMPARISON terminal closeout concepts.

    The gate accepts the preferred literal label ``Matrix`` and the common
    semantic form ``Status: 7 MO-STRONGER ...`` because both are matrix-count
    evidence. It still requires explicit adoption and rejection disposition
    language before OWNER_COMPARISON may stop.
    """
    lowered = str(text or "").lower()
    has_matrix = "matrix" in lowered or (
        "status" in lowered
        and any(
            marker in lowered
            for marker in (
                "mo-stronger",
                "reference-stronger",
                "existing-but-weak",
                "missing",
                "by-design",
                "unknown",
            )
        )
    )
    checks_by_term = {
        "target": "target" in lowered or "current mo" in lowered,
        "matrix": has_matrix,
        "adoption": "adoption" in lowered or "adopt" in lowered,
        "reject": "reject" in lowered or "by-design" in lowered,
    }
    checks = tuple((term, bool(checks_by_term.get(term))) for term in required_closeout_terms(OWNER_COMPARISON_PROTOCOL))
    return [name for name, present in checks if not present]


def _owner_comparison_reports_default_target_drift(user_input: str, text: str) -> bool:
    """Detect OWNER_COMPARISON closeouts that improve references instead of current MO."""
    if _owner_comparison_user_named_non_current_target(user_input):
        return False
    lowered = str(text or "").lower()
    if "not a comparison target" in lowered and ("running mo" in lowered or "current mo" in lowered):
        return True
    if "current runtime instance; not a comparison target" in lowered:
        return True
    external_edit_plan = re.search(r"source edits?\s+in\s+[`\"']?[a-z]:\\", lowered)
    if external_edit_plan and "current mo" not in lowered:
        return True
    return False


def _owner_comparison_user_named_non_current_target(user_input: str) -> bool:
    """Return True only for explicit operator target override wording."""
    lowered = str(user_input or "").lower()
    return bool(
        re.search(r"\btarget\s+[`\"']?[a-z]:\\", lowered)
        or "target repo" in lowered
        or "target path" in lowered
    )


# Operator-owned remainder classes — items whose disposition is the operator's call,
# NOT actionable autonomous work. An honest closeout may carry these without being forced
# to a false "Remaining: none" (external-watcher governance fix 2026-06-23). Examples:
# B2 (supervised fix-lane) and OBS-PERF-1 (recorded observation) from the T0000 run.
_OPERATOR_OWNED_REMAINDER = re.compile(
    r"(?i)\b(?:operator[-\s]?decision|operator[-\s]?owned|awaiting\s+operator|"
    r"operator\s+decision\s+pending|supervised\s+fix[-\s]?lane|recorded\s+observation|"
    r"accepted\s+deferred)\b"
)


def _owner_maintenance_completion_reports_open_work(text: str) -> bool:
    """Detect ACTIONABLE OWNER_MAINTENANCE leftovers that must continue, not close.

    A terminal report may legitimately carry **operator-owned** remainders — items
    explicitly classified as operator-decision pending, supervised fix-lane, recorded
    observation, or accepted deferred. Those are NOT autonomous work, so reporting them
    is a valid terminal state and must NOT be forced to a false "Remaining: none" (which
    pressured the model to rewrite genuinely-deferred items as RESOLVED — external-watcher
    governance fix 2026-06-23). Only work the model could itself resolve blocks completion:
    failures, unresolved/open/carried-forward findings, and actionable next targets.
    """
    body = str(text or "")
    lowered = body.lower()
    # Hard, non-deferrable: failures + unresolved/open/carried-forward findings. These are
    # actionable and can NEVER be reclassified as operator-owned, so they are checked
    # body-wide first — operator-owned wording elsewhere must not mask a real failure.
    if re.search(r"(?im)^\s*\[fail\]", body):
        return True
    if re.search(r"(?i)\[issues\]\s*[1-9]\d*\s+check\(s\)\s+failed", body):
        return True
    if re.search(r"(?i)\b[1-9]\d*\s+(?:fail|fails|failed|unresolved|open|carried forward)\b", body):
        return True
    if re.search(r"(?i)\b(?:unresolved|not addressed|carried forward)\b[^.\n]*\b[1-9]\d*\b", body):
        return True
    if any(marker in lowered for marker in (
        "highest-priority unresolved",
        "highest-value next target",
        "remaining (not addressed)",
    )):
        return True
    # "deferred / remaining / next" reporting is accepted ONLY when the line classifies its
    # items as operator-owned (operator-decision pending / supervised fix-lane / recorded
    # observation / accepted deferred). Evaluated PER LINE so an operator-owned exemption
    # on one line can't excuse an un-owned actionable deferral on another.
    for line in body.splitlines():
        if _OPERATOR_OWNED_REMAINDER.search(line):
            continue
        if re.search(r"(?i)\b(?:remaining|deferred)\b[^.\n]*\b[1-9]\d*\b", line):
            return True
        m = re.match(r"(?i)^\s*(?:[-*]\s*)?(?:next|next targets?|remaining|deferred|unresolved)\s*:\s*(.+)$", line)
        if m:
            value = m.group(1).strip().strip("`*_ ")
            if value and not re.fullmatch(
                r"(?i)(?:none|no(?:ne)?|n/a|0|zero|nothing|closed|complete|completed|clean)\.?", value
            ):
                return True
    return False


def _owner_maintenance_blocked_has_hard_boundary(text: str) -> bool:
    """Accept OWNER_MAINTENANCE BLOCKED only for real external or deterministic limits."""
    body = str(text or "").lower()
    hard_markers = (
        "max provider",
        "max tool",
        "budget exhaustion",
        "tool budget",
        "tool rounds",
        "provider error",
        "provider timeout",
        "timeout",
        "sandbox block",
        "sandboxed",
        "permission denied",
        "approval required",
        "credential",
        "external boundary",
        "hard boundary",
        "safety boundary",
        "operator interrupt",
        "user stopped",
        "aborted",
    )
    return any(marker in body for marker in hard_markers)


def _owner_maintenance_terminal_prefix_text(final_text: str) -> str:
    """Normalize harmless formatting before a OWNER_MAINTENANCE terminal marker."""
    text = str(final_text or "").lstrip()
    if not text:
        return ""
    text = re.sub(r"^(?:[-*_]{3,}\s*)+", "", text).lstrip()
    text = _strip_leading_markdown_prefix(text)
    status = re.match(r"^(?:clean|done|complete|blocked|status)\.?\s*[:\-–—]?\s*", text, re.I)
    if status and status.end() <= 24:
        text = _strip_leading_markdown_prefix(text[status.end():])
    heading = re.search(
        r"(?im)^\s*(?:[-*_]{3,}\s*)?(?:#{1,6}\s*)?(?:[*_`]+\s*)?"
        r"(\[(?:OWNER_MAINTENANCE|OWNER_COMPARISON)\s+(?:COMPLETE|BLOCKED)\])",
        text[:480],
    )
    if heading:
        text = text[heading.start(1):]
    return text


def _owner_maintenance_terminal_marker_text(final_text: str) -> str:
    """Return text starting at a OWNER_MAINTENANCE/OWNER_COMPARISON marker even in persisted summaries.

    Provider final answers normally start with the marker. Session summaries often place
    the marker near the closeout section after headings and evidence, so prefix-only
    normalization would skip the closeout evidence checks when validating artifacts.
    """
    text = _owner_maintenance_terminal_prefix_text(final_text)
    if text.startswith(("[OWNER_MAINTENANCE COMPLETE]", "[OWNER_MAINTENANCE BLOCKED]", "[OWNER_COMPARISON COMPLETE]", "[OWNER_COMPARISON BLOCKED]")):
        return text
    raw = str(final_text or "")
    marker = re.search(r"(?is)\[(?:OWNER_MAINTENANCE|OWNER_COMPARISON)\s+(?:COMPLETE|BLOCKED)\]", raw)
    if not marker:
        return text
    return raw[marker.start():]


def _owner_maintenance_tool_error_ownership_text(final_text: str) -> str:
    """Extract the text that is allowed to satisfy tool-error attribution.

    A real tool name appearing elsewhere in a long report (for example in a passing test
    list) must not accidentally satisfy the tool-error ledger. Prefer the explicit
    Tool Error Ledger section when present, plus the terminal closeout line.
    """
    raw = str(final_text or "")
    sections: list[str] = []
    ledger = re.search(
        r"(?ims)^#{1,6}\s*Tool Error Ledger\s*$"
        r"(?P<body>.*?)(?=^#{1,6}\s|\Z)",
        raw,
    )
    if ledger:
        sections.append(ledger.group("body"))
    # The terminal closeout LINE may own tools inline (short closeouts with no ledger
    # section). Scope to the marker's OWN paragraph only — not a broad window, which
    # would let an incidental tool name elsewhere (e.g. a passing-tests list) satisfy
    # the ledger without owning the error.
    marker_text = _owner_maintenance_terminal_marker_text(raw)
    if marker_text:
        sections.append(re.split(r"\n\s*\n", marker_text, maxsplit=1)[0])
    if not sections:
        sections.append(raw[:400])
    return "\n".join(sections)


def _strip_leading_markdown_prefix(text: str) -> str:
    return re.sub(r"^[\s#>*_`-]+", "", str(text or "")).lstrip()
