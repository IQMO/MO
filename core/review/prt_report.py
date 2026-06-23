"""PRT report rendering sink.

Relocated from ``core/ghost/ghost_routing.py`` (Ghost-consolidation proposal #13):
rendering a PRT report is not a Ghost-routing concern — it merely reuses the Ghost
panel as a display sink. This module writes the report to the transcript when the
foreground turn is idle, steers/queues it during an active turn, and mirrors a
compact view into the Ghost panel. Behavior is unchanged by the move.
"""
from __future__ import annotations

import traceback


def route_prt_report(agent: object, report: object):
    """Route PRT reports based on agent state (idle->show, busy->steer, empty->silent)."""
    if not report:
        return

    findings = getattr(report, "findings", None) or []

    severity_style = {
        "critical": "class:prt-critical",
        "major": "class:prt-major",
        "minor": "class:prt-minor",
        "info": "class:prt-info",
    }
    severity_label = {"critical": "BLOCKER", "major": "SUGGESTION", "minor": "MINOR", "info": "NOTE"}
    status_label = "clean" if getattr(report, "is_target_met", False) else "unresolved" if findings else "attention"
    if report.score < 3.0:
        status_label = "failed"

    # Build report text without emoji glyphs; terminal emoji width/color support
    # is inconsistent and made live PRT output hard to read.
    styled_lines: list[tuple[str, str]] = [
        ("class:prt-header", f"PRT checked commit {report.diff_ref} (+{report.additions}/-{report.deletions})"),
        ("", ""),
    ]
    positives = getattr(report, "positives", None) or []
    if positives:
        styled_lines.append(("class:prt-clean", "  ✅ What's good:"))
        for p in positives:
            styled_lines.append(("class:prt-clean", f"     → {p}"))
        styled_lines.append(("", ""))

    if findings:
        for f in findings:
            sev = str(getattr(f, "severity", "info") or "info").lower()
            loc = f"{f.file}" + (f":{f.line_range[0]}" if f.line_range and f.line_range[0] else "")
            label = severity_label.get(sev, sev.upper() or "INFO")
            styled_lines.append((severity_style.get(sev, "class:prt-info"), f"  [{label}] {loc} - {f.message}"))
            rationale = str(getattr(f, "rationale", "") or "").strip()
            if rationale:
                styled_lines.append(("class:prt-summary", f"          Why: {rationale}"))
        styled_lines.append(("", ""))
    else:
        styled_lines.append(("class:prt-clean", "  No issues found [clean]"))
        styled_lines.append(("", ""))

    score_style = "class:prt-clean" if status_label == "clean" else "class:prt-critical" if status_label == "failed" else "class:prt-major"
    styled_lines.append((score_style, f"  Score: {report.score}/5.0 [{status_label}]"))
    if findings:
        styled_lines.append(("class:prt-summary", f"  {len(findings)} finding(s), {report.unresolved_count} unresolved"))
    token_info = f"Tokens: {report.token_usage.get('total_tokens', 'N/A')}"
    compression_saved = report.token_usage.get("compression_saved", 0)
    if compression_saved:
        token_info += f"  ·  ~{compression_saved} tok saved"
    styled_lines.append(("class:prt-summary", f"  Files changed: {report.files_changed}  ·  {token_info}"))

    # Encouragement based on status
    if status_label == "clean":
        styled_lines.append(("class:prt-clean", "  Solid work — everything looks good."))
    elif status_label == "unresolved":
        styled_lines.append(("class:prt-summary", "  Good foundation — clean up the items above and re-check."))
    elif status_label == "failed":
        styled_lines.append(("class:prt-critical", "  The critical items need attention before merge."))
    elif status_label == "attention":
        styled_lines.append(("class:prt-summary", "  Review complete — take a look when you're ready."))

    text = "\n".join(line for _style, line in styled_lines)

    try:
        from ..backend_monitor import get_monitor
        monitor = get_monitor()
        if monitor:
            monitor.emit("prt_review", {
                "diff_ref": str(getattr(report, "diff_ref", "") or ""),
                "score": getattr(report, "score", 0),
                "unresolved_count": int(getattr(report, "unresolved_count", 0) or 0),
                "findings": len(findings),
            })
    except Exception:
        traceback.print_exc()

    tui = getattr(agent, "tui", None)
    agent._prt_last_report = report
    if not tui:
        return

    # Write the full transcript report only when the foreground turn is idle.
    # During an active main turn, route it through the unread Ghost/PRT panel so
    # PRT text cannot interleave with the assistant's in-flight answer.
    if hasattr(tui, "_add") and not getattr(tui, "busy", False):
        for style, line in styled_lines:
            tui._add(style, f"  {line}" if line else "")

    tui._prt_done_unread = True
    if hasattr(tui, "_set_notice"):
        try:
            tui._set_notice(f"PRT finished: {report.score}/5.0 [{status_label}] - Alt+G", ttl=8.0)
        except Exception:
            traceback.print_exc()
    if getattr(tui, "busy", False):
        injector = getattr(agent, "add_live_steer", None)
        if callable(injector):
            injector(f"PRT Review Update:\n{text}", source="prt", worker_id="prt-review")
        else:
            queue_input = getattr(tui, "_queue_input", None)
            if callable(queue_input):
                queue_input(f"PRT Review Update:\n{text}", source="prt", note="PRT Review completed")
    # Use PRT-specific styles for Ghost panel so theme.py prt-* styles are actually rendered
    tui._ghost_panel_lines = [
        ("class:prt-header", "PRT Review"),
        ("class:prt-summary", f"PRT score: {report.score}/5.0 [{status_label}]"),
    ]
    if positives:
        tui._ghost_panel_lines.append(("class:prt-clean", f"✅ {len(positives)} positive(s)"))
        for p in positives[:3]:
            tui._ghost_panel_lines.append(("class:prt-clean", f"  → {p}"))
    if findings:
        tui._ghost_panel_lines.append(("class:prt-summary", f"{report.unresolved_count} unresolved:"))
        for f in findings[:5]:
            sev = str(getattr(f, "severity", "info") or "info").lower()
            label = severity_label.get(sev, sev.upper() or "INFO")
            tui._ghost_panel_lines.append((severity_style.get(sev, "class:prt-info"), f"[{label}] {f.message}"))
            rationale = str(getattr(f, "rationale", "") or "").strip()
            if rationale:
                tui._ghost_panel_lines.append(("class:prt-summary", f"  Why: {rationale}"))
    if status_label == "clean":
        tui._ghost_panel_lines.append(("class:prt-clean", "Solid work — everything looks good."))
    elif status_label == "unresolved":
        tui._ghost_panel_lines.append(("class:prt-summary", "Clean up the items above and re-check."))
    tui._ghost_panel_lines.append(("class:ghost-hint", "Alt+G to open · ask MO to inspect or fix."))
    tui._ghost_expanded = True

    if hasattr(tui, "_app") and tui._app:
        tui._app.invalidate()
