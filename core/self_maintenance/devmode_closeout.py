"""Terminal closeout gates for owner-only self-maintenance protocols."""
from __future__ import annotations

from pathlib import Path
import re

from ..owner_protocols import (
    is_devmode05_activation,
    is_ifdev05_activation,
    is_vs05_activation,
)

def _devmode05_future_stamp_violation() -> str | None:
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


def _devmode05_closeout_evidence_violation(
    final_text: str,
    *,
    monitor_path: str | Path | None = None,
    session_ids: "set[str] | frozenset[str] | None" = None,
    frozen_error_count: int | None = None,
    session_dir: "str | Path | None" = None,
) -> str | None:
    """Deterministic contradiction between a clean DEVMODE05 closeout and runtime
    truth — the internalized watcher. Returns a one-line block reason, or None.
    Fail-open: any error returns None so it can never wedge a legitimate closeout.

    ``frozen_error_count`` (when provided) is the count frozen at the FIRST closeout write;
    the gate owns THAT number instead of re-reading the live monitor, so post-freeze
    closeout-edit errors cannot move the target and loop the gate forever."""
    try:
        text = _devmode05_terminal_prefix_text(final_text) or ""
        if not text.startswith("[DEVMODE05 COMPLETE]"):
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
        # 2. the closeout artifacts must actually EXIST in the bound session dir. A
        #    [DEVMODE05 COMPLETE] with no summary.md/economy.md/manifest.json is an
        #    incomplete closeout — observed live mo-1782208099, where the completed-board
        #    tool guard ended the turn before they were written. Only enforced when a dir
        #    is bound (early states with no dir yet are not blocked here).
        if session_dir is not None:
            try:
                sd = Path(session_dir)
                missing = [n for n in ("summary.md", "economy.md", "manifest.json")
                           if not (sd / n).is_file()]
                if missing:
                    return (
                        "the session dir is missing required closeout artifact(s): "
                        f"{', '.join(missing)} — write them before [DEVMODE05 COMPLETE]."
                    )
            except Exception:
                pass
        # 3. the session dir must carry a local-time stamp, not a future/skewed one.
        return _devmode05_future_stamp_violation()
    except Exception:
        return None


def devmode05_final_allows_stop(
    user_input: str,
    final_text: str,
    *,
    monitor_path: str | Path | None = None,
    session_ids: "set[str] | frozenset[str] | None" = None,
    frozen_error_count: int | None = None,
    session_dir: "str | Path | None" = None,
) -> bool:
    """Return True only when a DEVMODE05 final answer is a real stop boundary."""
    if not is_devmode05_activation(user_input):
        return True
    text = _devmode05_terminal_prefix_text(final_text)
    if not text:
        return False
    # Don't block the other protocol's completions — VS05 gate is responsible for those
    if text.startswith("[VS05 COMPLETE]") or text.startswith("[VS05 BLOCKED]"):
        return True
    if text.startswith("[DEVMODE05 BLOCKED]"):
        return _devmode05_blocked_has_hard_boundary(text)
    if text.startswith("[DEVMODE05 COMPLETE]"):
        if _devmode05_completion_reports_open_work(text):
            return False
        if _devmode05_closeout_evidence_violation(
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


def vs05_final_allows_stop(user_input: str, final_text: str) -> bool:
    """Return True only when a VS05 answer is a terminal comparison boundary."""
    if not is_vs05_activation(user_input):
        return True
    text = _devmode05_terminal_prefix_text(final_text)
    if not text:
        return False
    # Don't block the other protocol's completions — DEVMODE05 gate is responsible for those
    if text.startswith("[DEVMODE05 COMPLETE]") or text.startswith("[DEVMODE05 BLOCKED]"):
        return True
    if text.startswith("[VS05 BLOCKED]"):
        return _devmode05_blocked_has_hard_boundary(text)
    if text.startswith("[VS05 COMPLETE]"):
        if _devmode05_completion_reports_open_work(text):
            return False
        if _vs05_reports_default_target_drift(user_input, text):
            return False
        if _vs05_missing_closeout_terms(text):
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


def devmode05_continuation_instruction(
    user_input: str,
    final_text: str,
    *,
    monitor_path: str | Path | None = None,
    session_ids: "set[str] | frozenset[str] | None" = None,
    frozen_error_count: int | None = None,
    session_dir: "str | Path | None" = None,
) -> str:
    """Explain why a DEVMODE05 stop claim was rejected and what must happen next."""
    base = (
        "[DEVMODE05 AUTONOMY] Do not stop at a checkpoint, report, or approval question. "
        "Continue with the next evidence-backed action. Finalize only with [DEVMODE05 COMPLETE] "
        "when the protocol is complete or [DEVMODE05 BLOCKED] for a real "
        "tool/provider/timeout/sandbox/permission/safety boundary."
    )
    if not is_devmode05_activation(user_input):
        return base
    text = _devmode05_terminal_prefix_text(final_text)
    if text.startswith("[DEVMODE05 COMPLETE]") and _devmode05_completion_reports_open_work(text):
        return (
            "[DEVMODE05 AUTONOMY] Your last answer claimed [DEVMODE05 COMPLETE] while still "
            "reporting actionable open work (unresolved/open/carried-forward findings, failed "
            "checks, or a next target). That is not a terminal state. Do not repeat the same "
            "completion report. Continue from the named items now: RESOLVE the actionable ones "
            "with verification. Items that are genuinely the operator's call are allowed to remain "
            "— but you must classify each EXPLICITLY as operator-decision pending / supervised "
            "fix-lane / recorded observation / accepted deferred (do NOT rewrite a real deferred "
            "item as RESOLVED, and do NOT claim 'Remaining: none' when such items exist). Finalize "
            "with: 'No actionable product work remains; operator-decision items remain: <list, or none>.'"
        )
    _violation = _devmode05_closeout_evidence_violation(
        final_text, monitor_path=monitor_path, session_ids=session_ids,
        frozen_error_count=frozen_error_count, session_dir=session_dir,
    )
    if text.startswith("[DEVMODE05 COMPLETE]") and _violation:
        return (
            "[DEVMODE05 AUTONOMY] Your [DEVMODE05 COMPLETE] contradicts runtime evidence: "
            f"{_violation} Do not repeat the same completion — read economy.md, correct the "
            "tool-error ledger and report from it, then finalize."
        )
    if text.startswith("[DEVMODE05 BLOCKED]") and not _devmode05_blocked_has_hard_boundary(text):
        return (
            "[DEVMODE05 AUTONOMY] Your last answer used [DEVMODE05 BLOCKED] without a current hard "
            "tool/provider/timeout/sandbox/permission/safety boundary. Work remaining is not a "
            "blocker. Continue from the continuation capsule or next unresolved action now."
        )
    return base


def vs05_continuation_instruction(user_input: str, final_text: str) -> str:
    """Explain why a VS05 stop claim was rejected and what must happen next."""
    base = (
        "[VS05 CONTINUATION] Do not stop at initial capture or preliminary comparison. "
        "Continue the read-only VS05 protocol until source roles, structured evidence usage, "
        "comparison matrix, adoption/reject/defer dispositions, artifact path, and exact next "
        "approval decision are complete. Finalize only with [VS05 COMPLETE] or [VS05 BLOCKED] "
        "for a real tool/provider/timeout/sandbox/permission/safety boundary. Preferred final "
        "labels: Target, Matrix, Adoption, Reject, Defer/Recheck, Artifacts, Approval."
    )
    if not is_vs05_activation(user_input):
        return base
    text = _devmode05_terminal_prefix_text(final_text)
    if text.startswith("[VS05 COMPLETE]") and _devmode05_completion_reports_open_work(text):
        return (
            "[VS05 CONTINUATION] Your last answer claimed [VS05 COMPLETE] while still reporting "
            "remaining, deferred, open, failed, or carried-forward work. Continue from those named "
            "items now, or close them as reject/defer/no-action with evidence before completing."
        )
    if text.startswith("[VS05 COMPLETE]") and _vs05_reports_default_target_drift(user_input, text):
        return (
            "[VS05 CONTINUATION] Your VS05 closeout drifted from the default target. Current MO "
            "workspace is the adoption target; operator-supplied paths are read-only references "
            "unless the operator explicitly named another target. Rewrite/continue the matrix and "
            "adoption plan for current MO, not for a reference path. The closeout must include "
            "Target: current MO workspace."
        )
    if text.startswith("[VS05 COMPLETE]"):
        missing = _vs05_missing_closeout_terms(text)
        if missing:
            return (
                "[VS05 CONTINUATION] Your [VS05 COMPLETE] report is missing required closeout "
                f"terms: {', '.join(missing)}. Continue and produce the final report with these "
                "literal labels before final closeout: Target, Matrix, Adoption, Reject, Defer/Recheck, "
                "Artifacts, Approval. Do not repeat a summary-only closeout."
            )
    if text.startswith("[VS05 BLOCKED]") and not _devmode05_blocked_has_hard_boundary(text):
        return (
            "[VS05 CONTINUATION] Your last answer used [VS05 BLOCKED] without a current hard "
            "tool/provider/timeout/sandbox/permission/safety boundary. Work remaining is not a "
            "blocker. Continue the comparison from the next evidence-backed action."
        )
    return base


def devmode05_task_truth_continuation_instruction() -> str:
    """Tell DEVMODE05 how to recover from a terminal claim with open task truth."""
    return (
        "[DEVMODE05 AUTONOMY] Completion is not allowed while MO's task/protocol truth still "
        "has open work. Do not repeat the same completion report. Continue from the active "
        "taskboard/protocol row: run the next evidence-backed action, or if the active row is "
        "genuinely done, call `complete_task` and verify open task count is zero before the final "
        "[DEVMODE05 COMPLETE]. If the only rejection was `taskboard_done_claim_conflict`, do not "
        "inspect taskboard source, storage, or trace paths before that `complete_task` call; inspect "
        "implementation only if `complete_task` is unavailable or fails. Use [DEVMODE05 BLOCKED] "
        "only for a real hard runtime/tool/provider/safety boundary."
    )


def vs05_task_truth_continuation_instruction() -> str:
    """Tell VS05 how to recover from a terminal claim with open task truth."""
    return (
        "[VS05 CONTINUATION] Completion is not allowed while MO's task/protocol truth still "
        "has open work. Do not repeat the same completion report. Continue from the active "
        "VS05 taskboard row: run the next evidence-backed action, or if the active row is "
        "genuinely done, call `complete_task` and verify open task count is zero before the final "
        "[VS05 COMPLETE]. If the only rejection was `taskboard_done_claim_conflict`, do not "
        "inspect taskboard source, storage, or trace paths before that `complete_task` call; inspect "
        "implementation only if `complete_task` is unavailable or fails. Use [VS05 BLOCKED] "
        "only for a real hard runtime/tool/provider/safety boundary."
    )


def ifdev05_final_allows_stop(user_input: str, final_text: str) -> bool:
    """Return True only when an IFDEV05 final answer is a real stop boundary.

    Mirrors the DEVMODE05 gate (IFDEV05's improve lane is DEVMODE05-shaped):
    completion is rejected while open work is reported; BLOCKED requires a real
    hard boundary. Other protocols' markers are deferred to their own gates.
    """
    if not is_ifdev05_activation(user_input):
        return True
    text = _devmode05_terminal_prefix_text(final_text)
    if not text:
        return False
    if text.startswith(("[DEVMODE05 COMPLETE]", "[DEVMODE05 BLOCKED]", "[VS05 COMPLETE]", "[VS05 BLOCKED]")):
        return True
    if text.startswith("[IFDEV05 BLOCKED]"):
        return _devmode05_blocked_has_hard_boundary(text)
    if text.startswith("[IFDEV05 COMPLETE]"):
        if _devmode05_completion_reports_open_work(text):
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


def ifdev05_continuation_instruction(user_input: str, final_text: str) -> str:
    """Explain why an IFDEV05 stop claim was rejected and what must happen next."""
    base = (
        "[IFDEV05 CONTINUATION] Do not stop at a checkpoint, partial UX audit, or approval "
        "question. Continue the interface diagnosis/adoption protocol with the next "
        "evidence-backed action. Finalize only with [IFDEV05 COMPLETE] when the protocol is "
        "complete or [IFDEV05 BLOCKED] for a real tool/provider/timeout/sandbox/permission/safety "
        "boundary."
    )
    if not is_ifdev05_activation(user_input):
        return base
    text = _devmode05_terminal_prefix_text(final_text)
    if text.startswith("[IFDEV05 COMPLETE]") and _devmode05_completion_reports_open_work(text):
        return (
            "[IFDEV05 CONTINUATION] Your last answer claimed [IFDEV05 COMPLETE] while also "
            "reporting actionable open/failed UX work. That is not a terminal state. "
            "Continue from the named open findings now: fix the actionable ones with verification, "
            "adopt/reject the comparison candidates. Items that are genuinely the operator's call "
            "may remain if classified EXPLICITLY as operator-decision pending / supervised fix-lane "
            "/ recorded observation / accepted deferred (do NOT rewrite a real deferred item as "
            "RESOLVED). Finalize with: 'No actionable UX work remains; operator-decision items "
            "remain: <list, or none>.'"
        )
    if text.startswith("[IFDEV05 BLOCKED]") and not _devmode05_blocked_has_hard_boundary(text):
        return (
            "[IFDEV05 CONTINUATION] Your last answer used [IFDEV05 BLOCKED] without a current hard "
            "tool/provider/timeout/sandbox/permission/safety boundary. Work remaining is not a "
            "blocker. Continue from the next unresolved UX finding now."
        )
    return base


def ifdev05_task_truth_continuation_instruction() -> str:
    """Tell IFDEV05 how to recover from a terminal claim with open task truth."""
    return (
        "[IFDEV05 CONTINUATION] Completion is not allowed while MO's task/protocol truth still "
        "has open work. Do not repeat the same completion report. Continue from the active "
        "IFDEV05 taskboard row: run the next evidence-backed action, or if the active row is "
        "genuinely done, call `complete_task` and verify open task count is zero before the final "
        "[IFDEV05 COMPLETE]. Use [IFDEV05 BLOCKED] only for a real hard "
        "runtime/tool/provider/safety boundary."
    )


def _vs05_missing_closeout_terms(text: str) -> list[str]:
    """Return missing VS05 terminal closeout concepts.

    The gate accepts the preferred literal label ``Matrix`` and the common
    semantic form ``Status: 7 MO-STRONGER ...`` because both are matrix-count
    evidence. It still requires explicit adoption and rejection disposition
    language before VS05 may stop.
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
    checks = (
        ("target", "target" in lowered or "current mo" in lowered),
        ("matrix", has_matrix),
        ("adoption", "adoption" in lowered or "adopt" in lowered),
        ("reject", "reject" in lowered or "by-design" in lowered),
    )
    return [name for name, present in checks if not present]


def _vs05_reports_default_target_drift(user_input: str, text: str) -> bool:
    """Detect VS05 closeouts that improve references instead of current MO."""
    if _vs05_user_named_non_current_target(user_input):
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


def _vs05_user_named_non_current_target(user_input: str) -> bool:
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


def _devmode05_completion_reports_open_work(text: str) -> bool:
    """Detect ACTIONABLE DEVMODE05 leftovers that must continue, not close.

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


def _devmode05_blocked_has_hard_boundary(text: str) -> bool:
    """Accept DEVMODE05 BLOCKED only for real external or deterministic limits."""
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


def _devmode05_terminal_prefix_text(final_text: str) -> str:
    """Normalize harmless formatting before a DEVMODE05 terminal marker."""
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
        r"(\[(?:DEVMODE05|VS05)\s+(?:COMPLETE|BLOCKED)\])",
        text[:480],
    )
    if heading:
        text = text[heading.start(1):]
    return text


def _strip_leading_markdown_prefix(text: str) -> str:
    return re.sub(r"^[\s#>*_`-]+", "", str(text or "")).lstrip()
