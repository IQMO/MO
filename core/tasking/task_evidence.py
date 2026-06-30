"""Shared taskboard evidence and row-advancement classifiers.

The helpers in this module are pure policy functions: they classify task rows,
tool calls, and evidence labels, but they do not mutate ``TaskBoard`` state.
Gateway still owns board lifecycle, and Agent remains the caller that records
runtime evidence and advances rows.
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any

from ..runtime.work_signals import tool_is_verification_signal


TOOL_BACKED_EVIDENCE_TOOLS = {
    "read_file", "write_file", "edit_file", "shell", "grep",
    "find_files", "git_status", "test_runner", "web_fetch", "web_snapshot",
}

TASKBOARD_INSPECTION_TOOLS = {
    "read_file", "grep", "find_files", "git_status", "project_bridge",
    "web_fetch", "web_snapshot", "web_search",
}
TASKBOARD_EDIT_TOOLS = {"write_file", "edit_file"}
TASKBOARD_EXECUTION_TOOLS = {"write_file", "edit_file", "shell", "test_runner"}


def evidence_item_is_tool_backed(item: str) -> bool:
    return any(str(item or "").startswith(f"{tool}:") for tool in TOOL_BACKED_EVIDENCE_TOOLS)


def is_verification_step(title: str) -> bool:
    text = str(title or "").lower()
    # "write ... tests" is a build task, not a verification step
    if "write" in text and "test" in text and any(w in text for w in ("verify", "run")) is False:
        if any(m in text for m in ("write code for test", "write the test", "write tests for")):
            return False
    if "skipped" in text:
        return False
    return any(word in text for word in ("verify", "test", "run", "passes", "resolution"))


def has_failing_tests(text: str) -> bool:
    lowered = str(text or "").lower()
    # Note: bare "error" is intentionally excluded — it false-matches ordinary
    # prose ("added error handling", "no errors found") and wrongly forced verify
    # steps back to active. Real failures still surface via failed/failure/
    # traceback/non-zero exit codes.
    return any(m in lowered for m in ("failed", "failure", "exit code 1", "traceback"))


def has_passing_verification(text: str, evidence: list[str]) -> bool:
    lowered = str(text or "").lower()
    if any("passed" in lowered and ("test" in lowered or "check" in lowered) for _ in [1]):
        if not has_failing_tests(text):
            return True
    evidence_text = " ".join(str(e or "") for e in (evidence or []))
    if "verification_result:passed" in evidence_text.lower():
        return True
    # Accept a *test-shaped* pass — a clean exit or a non-zero "<N> passed" count —
    # but not merely the word "passed" in prose, and never the degenerate "0 passed".
    # (The previous `or "0 passed" not in lowered` was near-always true, collapsing
    # the gate to "says passed, not failed".)
    if "failed" not in lowered and "0 passed" not in lowered:
        if "[exit code 0]" in lowered or re.search(r"\d+\s+passed", lowered):
            return True
    return False


def has_passing_after_failure(text: str) -> bool:
    lowered = str(text or "").lower()
    return "passed" in lowered and has_failing_tests(text) and "exit code 0" in lowered


def has_verification_tool_evidence(evidence: list[str]) -> bool:
    return any(str(e or "").startswith(("test_runner:", "shell:")) for e in (evidence or []))


def has_concrete_evidence(text: str) -> bool:
    lowered = str(text or "").lower()
    markers = (
        "read_file:", "write_file:", "edit_file:", "shell:", "grep:",
        "test_runner:", "git_status:", "find_files:", "verification_result:",
        "exit code", "passed", "failed", "error",
    )
    return any(m in lowered for m in markers)


def tool_evidence_label(tool: str, arguments: dict, max_detail_chars: int = 100) -> str:
    tool = str(tool or "")
    if tool in {"read_file", "write_file", "edit_file"}:
        return f"{tool}:{str((arguments or {}).get('path', '?'))}"
    if tool in {"grep", "find_files"}:
        return f"{tool}:{str((arguments or {}).get('pattern', '?'))[:80]}"
    if tool == "shell":
        return f"shell:{str((arguments or {}).get('command', '?'))[:max_detail_chars]}"
    if tool == "test_runner":
        return f"test_runner:{str((arguments or {}).get('command', '?'))[:80]}"
    if tool == "git_status":
        return "git_status"  # old format for test compatibility
    if tool == "web_fetch" or tool == "web_snapshot":
        return f"{tool}:{str((arguments or {}).get('url', '?'))[:80]}"
    return f"{tool}:called"


def tool_should_advance_task(
    tool_name: str,
    task: object,
    idx: int,
    total: int,
    *,
    arguments: dict | None = None,
) -> bool:
    """Return True when a successful tool call can satisfy the active row."""
    kind = str(getattr(task, "kind", "") or "").lower().strip()
    gate = str(getattr(task, "completion_gate", "") or "").lower().strip()
    has_metadata = bool(kind or gate)
    if has_metadata:
        if gate in {"manual", "final"} or kind in {"report", "ask"}:
            return False
        if gate == "verification" or kind == "verify":
            return tool_is_verification_signal(tool_name, arguments or {})
        if gate == "tool" or kind:
            title = str(getattr(task, "title", "") or "")
            return tool_matches_task_kind(tool_name, kind, arguments or {}, title=title)

    title = str(getattr(task, "title", "") or "").lower()
    if re.search(r"\b(?:deliver|report|respond|answer|final)\b", title):
        return False
    if tool_name in TASKBOARD_EXECUTION_TOOLS:
        return True
    if tool_name in TASKBOARD_INSPECTION_TOOLS and re.search(
        r"\b(?:inspect|read|locate|find|search|scan|grep|review|audit|investigate|check|map|identify|inventory)\b",
        title,
    ):
        return True
    return False


def tool_matches_task_kind(tool_name: str, kind: str, arguments: dict, *, title: str = "") -> bool:
    """Return True when a tool call matches a metadata-bearing task kind."""
    if kind == "inspect":
        if task_requires_broad_scope_evidence(title):
            return tool_name in {"grep", "find_files", "git_status", "project_bridge"} or tool_is_inspection_shell(tool_name, arguments)
        return tool_name in TASKBOARD_INSPECTION_TOOLS or tool_is_inspection_shell(tool_name, arguments)
    if kind == "edit":
        return tool_name in TASKBOARD_EDIT_TOOLS or tool_is_editing_shell(tool_name, arguments)
    if kind == "execute":
        return tool_name in {"shell", "test_runner"}
    if kind == "verify":
        return tool_is_verification_signal(tool_name, arguments)
    if not kind:
        return tool_name in TASKBOARD_INSPECTION_TOOLS or tool_name in TASKBOARD_EDIT_TOOLS or tool_name in {"shell", "test_runner"}
    return False


def taskboard_tool_evidence_item(tool_name: str, arguments: dict | None = None) -> str:
    """Return the Agent-compatible evidence label for a taskboard tool event."""
    summary = taskboard_tool_summary(tool_name, arguments or {})
    return f"{tool_name}:{summary}" if summary else str(tool_name or "tool")


def taskboard_tool_summary(name: str, arguments: dict[str, Any]) -> str:
    """Summarize tool arguments exactly as main taskboard evidence expects."""
    if name in {"read_file", "write_file", "edit_file"}:
        return str(arguments.get("path") or "")[:240]
    if name in {"find_files", "grep", "git_status", "test_runner", "project_bridge"}:
        return str(
            arguments.get("root")
            or arguments.get("workdir")
            or arguments.get("path")
            or arguments.get("pattern")
            or ""
        )[:240]
    if name == "shell":
        return str(arguments.get("command") or "")[:240]
    return ""


def _count_diff_lines(old: str, new: str) -> tuple[int, int]:
    """Return (added, removed) line counts between two texts, git-diff semantics."""
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    added = removed = 0
    for line in difflib.unified_diff(old_lines, new_lines, lineterm=""):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def edit_diffstat(name: str, arguments: dict[str, Any]) -> tuple[int, int] | None:
    """Return (added, removed) line counts an edit/write would apply, else None.

    Computed from the tool arguments (the *intended* change) so the activity line
    can show a git-style ``+A -R`` before the write executes. ``edit_file`` diffs
    old_text→new_text directly. ``write_file`` diffs the existing file (if any)
    against the new content; a brand-new file reports ``(N, 0)``.
    """
    args = arguments or {}
    if name == "edit_file":
        old_text = args.get("old_text")
        new_text = args.get("new_text")
        if old_text is None or new_text is None:
            return None
        return _count_diff_lines(str(old_text), str(new_text))
    if name == "write_file":
        content = args.get("content")
        if content is None:
            return None
        old_text = ""
        try:
            p = Path(str(args.get("path") or ""))
            if p.is_file():
                old_text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            old_text = ""
        return _count_diff_lines(old_text, str(content))
    return None


def task_requires_broad_scope_evidence(title: str) -> bool:
    text = str(title or "").lower()
    # Titles that explicitly scope to specific files/areas are NOT broad.
    if re.search(r"\b(?:map|trace|flow|flows|dependencies|risk|risks|finding|findings)\b", text):
        return False
    if re.search(r"\b(?:inspect|read)\s+(?:actual\s+)?(?:files?|docs?|runtime|context)\b", text):
        return False
    # Broad scope: title signals scoping/identification/discovery work
    # that should use grep/find_files first, not targeted file reads.
    return bool(re.search(r"\b(?:scope|identify|inventory|discover|survey|scan all|map out)\b", text))


def tool_is_inspection_shell(tool_name: str, arguments: dict) -> bool:
    if tool_name != "shell":
        return False
    command = str((arguments or {}).get("command") or "").lower().strip()
    return bool(re.search(
        r"^(?:python\s+-m\s+)?(?:rg|grep|find|ls|dir|git\s+status|git\s+diff(?:\s+--stat)?|git\s+show|git\s+log|pwd|tree)\b",
        command,
    ))


def tool_is_editing_shell(tool_name: str, arguments: dict) -> bool:
    if tool_name != "shell":
        return False
    command = str((arguments or {}).get("command") or "").lower()
    return bool(re.search(
        r"(>\s*[^&]|>>|\bsed\s+-i\b|\bperl\s+-pi\b|\btouch\b|\bmkdir\b|\bcp\b|\bmv\b|\bwrite_text\b|\bopen\([^)]*['\"]w)",
        command,
        re.S,
    ))


def final_should_complete_task(task: object) -> bool:
    kind = str(getattr(task, "kind", "") or "").lower().strip()
    gate = str(getattr(task, "completion_gate", "") or "").lower().strip()
    if not kind and not gate:
        return True
    return gate == "final" or (kind == "report" and gate in {"", "final"})


def final_report_task_id(task_board: object) -> str:
    tasks = list(getattr(task_board, "tasks", []) or [])
    for row in reversed(tasks):
        kind = str(getattr(row, "kind", "") or "").lower().strip()
        gate = str(getattr(row, "completion_gate", "") or "").lower().strip()
        if gate == "final" or kind == "report":
            return str(getattr(row, "id", "") or "")
    if tasks:
        return str(getattr(tasks[-1], "id", "") or "")
    return ""
