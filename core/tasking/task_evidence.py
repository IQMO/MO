"""Shared taskboard evidence and row-advancement classifiers.

The helpers in this module are pure policy functions: they classify task rows,
tool calls, and evidence labels, but they do not mutate ``TaskBoard`` state.
Gateway still owns board lifecycle, and Agent remains the caller that records
runtime evidence and advances rows.
"""
from __future__ import annotations

import difflib
import re
import shlex
from pathlib import Path
from typing import Any, Mapping

from ..runtime.work_signals import tool_is_verification_signal
from ..tooling.sandbox import _ssh_remote_command_text, shell_command_is_inspection_only, shell_command_is_mutating
from ..tooling.tool_constants import (
    FILE_MUTATION_TOOLS,
    PHONE_ACTUATION_TOOLS,
    PHONE_OBSERVATION_TOOLS,
)


DESKTOP_INSPECTION_TOOLS = {
    "computer_targets", "computer_observe", "perceive",
}

STALE_VERIFICATION_MARKER = "[VERIFICATION CANDIDATE CHANGED]"
DESKTOP_EXECUTION_TOOLS = {
    "computer_act", "point_on_screen",
}
CODE_STRUCTURE_INSPECTION_TOOLS = {
    "code_search", "find_callers", "find_callees", "redundancy_scan",
    "graph_explain", "graph_neighbors", "graph_path", "graph_stats",
}
TOOL_BACKED_EVIDENCE_TOOLS = {
    "read_file", "write_file", "edit_file", "shell", "grep",
    "find_files", "git_status", "test_runner", "web_fetch", "map_project", "mo_design",
    "file_transfer", "migrate", "show_viz",
} | CODE_STRUCTURE_INSPECTION_TOOLS | DESKTOP_INSPECTION_TOOLS | DESKTOP_EXECUTION_TOOLS | PHONE_OBSERVATION_TOOLS | PHONE_ACTUATION_TOOLS

TASKBOARD_INSPECTION_TOOLS = {
    "read_file", "grep", "find_files", "git_status", "project_bridge",
    "web_fetch", "web_search", "map_project",
} | CODE_STRUCTURE_INSPECTION_TOOLS | DESKTOP_INSPECTION_TOOLS | PHONE_OBSERVATION_TOOLS
TASKBOARD_EXECUTION_TOOLS = {
    "write_file", "edit_file", "shell", "test_runner", "file_transfer",
} | DESKTOP_EXECUTION_TOOLS | PHONE_ACTUATION_TOOLS
MANUAL_APPROVAL_EVIDENCE = "manual:user-approved"
MANUAL_DECLINE_EVIDENCE = "manual:user-declined"
_MANUAL_APPROVAL_RE = re.compile(
    r"(?i)^\s*(?:(?:yes[,.]?\s*)?(?:(?:i\s+(?:already\s+)?)?approve(?:d)?"
    r"(?:\s+(?:the\s+)?(?:exact\s+)?plan|\s+and\s+authori[sz]e)?|you\s+(?:are|'re)\s+approved|"
    r"(?:i\s+)?authori[sz](?:e|ed)|operator\s+override\s+approved)(?:(?:\s*:\s*|\s+).{1,320})?|"
    r"(?:yes[,.]?\s*)?(?:proceed(?:\s+with\s+(?:the\s+)?(?:exact\s+)?plan)?|go\s+ahead|do\s+it|yes))\s*[.!]*\s*$"
)
_MANUAL_DECLINE_RE = re.compile(
    r"(?i)^\s*(?:"
    r"no|nope|reject(?:ed)?|decline(?:d)?|cancel|stop|"
    r"do\s+not\s+(?:proceed|continue)"
    r")\s*[.!]*\s*$"
)
_MANUAL_REVISION_RE = re.compile(
    r"(?i)^\s*(?:change|adjust|revise)\b.{0,160}[.!]*\s*$"
)
_MANUAL_NEGATED_APPROVAL_RE = re.compile(
    r"(?i)\b(?:did\s+not|didn['’]?t|do\s+not|don['’]?t|dont|never)\s+"
    r"(?:approve|authori[sz](?:e|ed)|proceed|take\s+over|go\s+ahead)\b|\b(?:approve(?:d)?|authori[sz](?:e|ed))\s+not\b"
)
_MANUAL_RESCOPED_APPROVAL_RE = re.compile(
    r"(?i)\b(?:yes|approve(?:d)?|authori[sz](?:e|ed)|proceed|go\s+ahead|take\s+over)\b[^.?!\n]{0,320}"
    r"\b(?:but|except|only|instead|if|when|after|before|unless)\b"
)
_MANUAL_DIRECT_TAKEOVER_RE = re.compile(
    r"(?i)^\s*(?:yes[,.]?\s*)?(?:please\s+)?take\s+over\b"
)


def taskboard_is_closed(task_board: Any | None) -> bool:
    """Return True when a real taskboard exists and all rows are terminal."""
    if task_board is None or not getattr(task_board, "tasks", None):
        return False
    try:
        return int(task_board.open_count()) == 0
    except Exception:
        return False


def task_is_manual_gate(task: object | None) -> bool:
    """Return whether *task* is waiting for an explicit user decision."""
    if task is None:
        return False
    kind = str(getattr(task, "kind", "") or "").lower().strip()
    gate = str(getattr(task, "completion_gate", "") or "").lower().strip()
    return gate == "manual" or kind == "ask"


def active_manual_gate(task_board: Any | None) -> object | None:
    """Return the active manual row from *task_board*, if one exists."""
    if task_board is None:
        return None
    try:
        active_id = str(task_board.active_task_id() or "")
        task = task_board.task(active_id) if active_id else None
    except Exception:
        return None
    return task if task_is_manual_gate(task) else None


def manual_gate_response_kind(user_input: str) -> str:
    """Classify an explicit reply to an active manual gate.

    Natural reassertions such as ``I already approved this`` are approvals, not
    a reason to repeat the same confirmation. Qualified or negated replies stay
    fail-closed so a scope change cannot silently inherit earlier authority.
    """
    text = " ".join(str(user_input or "").split())
    if _MANUAL_DECLINE_RE.fullmatch(text):
        return "declined"
    if _MANUAL_REVISION_RE.fullmatch(text):
        return "revision"
    if _MANUAL_NEGATED_APPROVAL_RE.search(text):
        return ""
    if _MANUAL_RESCOPED_APPROVAL_RE.search(text):
        return ""
    if _MANUAL_APPROVAL_RE.fullmatch(text) or _MANUAL_DIRECT_TAKEOVER_RE.search(text):
        return "approved"
    return ""


def manual_approval_evidence(user_input: str) -> str:
    """Return completion evidence only for an explicit current-turn approval."""
    return MANUAL_APPROVAL_EVIDENCE if manual_gate_response_kind(user_input) == "approved" else ""


def evidence_item_is_tool_backed(item: str) -> bool:
    return any(str(item or "").startswith(f"{tool}:") for tool in TOOL_BACKED_EVIDENCE_TOOLS)


def is_verification_step(title: str, *, kind: str = "") -> bool:
    """Use the declared row contract; infer from titles only for untyped steps."""
    if kind:
        return kind == "verify"
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
    if STALE_VERIFICATION_MARKER in str(text or ""):
        return False
    lowered = str(text or "").lower()
    if (
        has_failing_tests(text)
        or any(marker in lowered for marker in ("[running in background", "timed out", "cancelled", "canceled"))
        or re.search(r"\[exit code\s+(?!0\])[-+]?\d+\]", lowered)
    ):
        return False
    if "passed" in lowered and ("test" in lowered or "check" in lowered):
        return True
    evidence_text = " ".join(str(e or "") for e in (evidence or []))
    if "verification_result:passed" in evidence_text.lower():
        return True
    # Accept a *test-shaped* pass — a clean exit or a non-zero "<N> passed" count —
    # but not merely the word "passed" in prose, and never the degenerate "0 passed".
    # (The previous `or "0 passed" not in lowered` was near-always true, collapsing
    # the gate to "says passed, not failed".)
    if "failed" not in lowered and not re.search(r"(?<!\d)0\s+passed\b", lowered):
        if "[exit code 0]" in lowered or re.search(r"\d+\s+passed", lowered):
            return True
    return False


def verification_result_set_state(results: Mapping[str, str] | None) -> str:
    """Classify the current turn's exact ``test_runner`` result cache.

    Background launch markers are pending work, not verification evidence. Stale
    candidate entries are ignored before lifecycle classification, so a completed
    old marker cannot override newer current-candidate proof. A terminal
    failure/timeout stays authoritative until a successful mutation changes the
    verification epoch; only an entirely passing current cache can close a row.
    """
    values = [str(value or "").strip() for value in (results or {}).values()]
    values = [value for value in values if value]
    if not values:
        return ""
    # Old-candidate completion is still a lifecycle fact, but cannot prove or
    # block the current candidate. A later scoped check need not repeat that old
    # suite merely because its launch marker remains in the bounded cache.
    values = [value for value in values if STALE_VERIFICATION_MARKER not in value]
    if not values:
        return "inconclusive"
    if any(
        "[Running in background" in value
        or "[VERIFICATION ALREADY RUNNING]" in value
        for value in values
    ):
        return "pending"
    if any(
        has_failing_tests(value)
        or "timed out" in value.lower()
        or "cancelled" in value.lower()
        or "error executing" in value.lower()
        or bool(re.search(r"\[exit code\s+(?!0\])[-+]?\d+\]", value, re.IGNORECASE))
        for value in values
    ):
        return "failed"
    if all(has_passing_verification(value, []) for value in values):
        return "passed"
    return "inconclusive"


def has_passing_after_failure(text: str) -> bool:
    lowered = str(text or "").lower()
    return "passed" in lowered and has_failing_tests(text) and "exit code 0" in lowered


def has_verification_tool_evidence(evidence: list[str]) -> bool:
    return any(str(e or "").startswith((
        "test_runner:", "shell:", "computer_observe:", "perceive:",
        "phone_context:", "phone_files:", "phone_storage_report:", "phone_file_read:", "phone_capabilities:",
        "phone_system_status:", "phone_cache_report:", "phone_packages:",
    )) for e in (evidence or []))


def has_concrete_evidence(text: str) -> bool:
    lowered = str(text or "").lower()
    markers = (
        "read_file:", "write_file:", "edit_file:", "shell:", "grep:",
        "test_runner:", "git_status:", "find_files:", "verification_result:",
        "computer_targets:", "computer_observe:", "computer_act:", "point_on_screen:",
        "phone_context:", "phone_click:", "phone_set_text:", "phone_scroll:", "phone_key:",
        "phone_files:", "phone_storage_report:", "phone_file_read:", "phone_file_delete:",
        "phone_capabilities:", "phone_system_status:", "phone_cache_report:",
        "phone_cache_trim:", "phone_packages:", "phone_package_action:", "phone_shell:",
        "exit code", "passed", "failed", "error",
    )
    return any(m in lowered for m in markers)


def _tool_matches_report_artifact(tool_name: str, arguments: dict | None = None) -> bool:
    if tool_name not in {"read_file", "write_file", "edit_file"}:
        return False
    args = arguments or {}
    return bool(str(args.get("path") or args.get("file") or args.get("filename") or "").strip())


def completion_evidence_matches_task(task: object, item: str) -> bool:
    """Return True when an evidence label is strong enough to complete *task*.

    This is the runtime completion twin of ``tool_should_advance_task``. It
    works from compact evidence labels already stored on the taskboard, so the
    board does not need a second tool-policy language.
    """
    evidence = str(item or "").strip()
    if not evidence:
        return False
    if evidence.startswith("final:"):
        return False

    requirement = _completion_requirement(task)
    if requirement == "any":
        return True
    if requirement == "manual":
        return evidence == MANUAL_APPROVAL_EVIDENCE
    if requirement == "verification":
        return _evidence_is_verification_for_task(task, evidence)
    if requirement == "edit":
        tool_name, arguments = _evidence_tool_arguments(evidence)
        return tool_matches_task_kind(tool_name, "edit", arguments, title=str(getattr(task, "title", "") or ""))
    if requirement == "inspect":
        tool_name, arguments = _evidence_tool_arguments(evidence)
        return tool_matches_task_kind(tool_name, "inspect", arguments, title=str(getattr(task, "title", "") or ""))
    if requirement == "execute":
        tool_name, arguments = _evidence_tool_arguments(evidence)
        return tool_matches_task_kind(tool_name, "execute", arguments, title=str(getattr(task, "title", "") or ""))
    if requirement == "report_artifact":
        tool_name, arguments = _evidence_tool_arguments(evidence)
        return _tool_matches_report_artifact(tool_name, arguments)
    if requirement == "inspect_or_verification":
        if _evidence_is_verification(evidence):
            return True
        tool_name, arguments = _evidence_tool_arguments(evidence)
        return tool_matches_task_kind(tool_name, "inspect", arguments, title=str(getattr(task, "title", "") or ""))
    return evidence_item_is_tool_backed(evidence)


def completion_evidence_set_matches_task(task: object, evidence: list[str]) -> bool:
    """Require every named delivery action while preserving ordinary one-item gates."""
    items = [str(item or "").strip() for item in (evidence or []) if str(item or "").strip()]
    required = execution_actions_from_text(str(getattr(task, "title", "") or ""))
    if _completion_requirement(task) == "execute" and required:
        observed = {
            action
            for item in items
            for action in shell_execution_actions(*_evidence_tool_arguments(item))
        }
        return required.issubset(observed)
    return any(completion_evidence_matches_task(task, item) for item in items)


def _completion_requirement(task: object) -> str:
    kind = str(getattr(task, "kind", "") or "").lower().strip()
    gate = str(getattr(task, "completion_gate", "") or "").lower().strip()
    title = str(getattr(task, "title", "") or "")
    expected = " ".join(str(item or "") for item in (getattr(task, "expected_evidence", []) or []))
    combined = f"{title} {expected}".lower()

    if gate == "manual" or kind == "ask":
        return "manual"
    if kind == "report" and gate == "tool":
        return "report_artifact"
    if gate == "verification":
        return "verification"
    if kind == "edit" or _expected_requires_edit(combined):
        return "edit"
    if kind == "verify":
        # Explicit analytical rows use gate=tool and may close from matching
        # inspection evidence. An absent/unknown gate fails closed so malformed
        # or incomplete verify rows cannot degrade into generic tool completion.
        return "inspect_or_verification" if gate == "tool" else "verification"
    if kind == "inspect" or _expected_requires_inspection(combined):
        return "inspect"
    if kind == "execute":
        return "execute"
    if gate == "tool":
        return "tool"
    if getattr(task, "expected_evidence", None):
        return "tool"
    return "any"


def _expected_requires_edit(text: str) -> bool:
    return bool(re.search(r"\b(?:edit_file|write_file|edit applied|edits applied|applied to|fix target|build target)\b", text))


def _expected_requires_inspection(text: str) -> bool:
    return bool(re.search(
        r"\b(?:read_file|grep|find_files|git_status|read/search|targeted read|route/depth|"
        r"graph|tree|listing|matrix|catalog|screenshot|dom snapshot|computed-style|evidence per row)\b",
        text,
    ))

def _evidence_is_verification(evidence: str) -> bool:
    if evidence == "verification_result:passed":
        return True
    tool_name, arguments = _evidence_tool_arguments(evidence)
    return tool_is_verification_signal(tool_name, arguments)


def _evidence_is_verification_for_task(task: object, evidence: str) -> bool:
    if _evidence_is_verification(evidence):
        return True
    tool_name, arguments = _evidence_tool_arguments(evidence)
    return _task_allows_inspection_verification(task) and tool_matches_task_kind(
        tool_name,
        "inspect",
        arguments,
        title=str(getattr(task, "title", "") or ""),
    )


def _task_allows_inspection_verification(task: object) -> bool:
    """Allow observation to verify an artifact's explicit state, not a code fix.

    A test/build/runtime gate is still required for generic rows such as
    ``Verify resolution``.  But exact content/existence requests are genuinely
    verified by reading or locating that artifact; requiring pytest for those
    states made their task rows impossible to close.
    """
    title = " ".join(str(getattr(task, "title", "") or "").lower().split())
    if not title:
        return False
    artifact = bool(re.search(
        r"\b(?:file|directory|folder|path|artifact|output|content|contents|config|"
        r"setting|value)\b",
        title,
    )) or bool(re.search(
        # A row normally names the artifact (``probe.txt`` or ``tmp/probe``)
        # instead of spelling out "file".  Recognize explicit filename/path
        # tokens without letting a generic "verify resolution" row close from
        # an arbitrary source read.
        r"(?:\b[\w.-]+\.[a-z][a-z0-9]{0,11}\b|(?:^|\s)[^\s]+[\\/][^\s]+)",
        title,
    ))
    state = bool(re.search(
        r"\b(?:exact|match(?:es|ed|ing)?|contain(?:s|ed|ing)?|exist(?:s|ed|ence)?|"
        r"present|absent|gone|missing|created|written|deleted|removed|unchanged)\b",
        title,
    ))
    return artifact and state


def _evidence_tool_arguments(evidence: str) -> tuple[str, dict[str, Any]]:
    if ":" in evidence:
        tool_name, detail = evidence.split(":", 1)
    else:
        tool_name, detail = evidence, ""
    tool_name = str(tool_name or "").strip()
    detail = str(detail or "").strip()
    if tool_name in {"read_file", "write_file", "edit_file"}:
        return tool_name, {"path": detail}
    if tool_name in {"grep", "find_files"}:
        return tool_name, {"pattern": detail}
    if tool_name == "shell":
        return tool_name, {"command": detail}
    if tool_name == "test_runner":
        return tool_name, {"command": detail}
    if tool_name in {"web_fetch", "web_search"}:
        return tool_name, {"url": detail}
    if tool_name in {"migrate", "file_transfer"}:
        action, _, rest = detail.partition(" · ")
        parsed = {"action": action.strip()}
        if tool_name == "migrate" and rest:
            parsed["source"] = rest.split(" · ", 1)[0].strip()
        return tool_name, parsed
    if tool_name == "project_bridge":
        return tool_name, {"path": detail}
    if tool_name == "map_project":
        return tool_name, {"root": detail}
    if tool_name == "computer_targets":
        return tool_name, {"kind": detail}
    if tool_name == "computer_observe":
        return tool_name, {"operation": detail}
    if tool_name == "computer_act":
        return tool_name, {"action": detail}
    if tool_name == "mo_design":
        return tool_name, {"action": detail.split(" · ", 1)[0]}
    if tool_name == "perceive":
        return tool_name, {"source": detail}
    if tool_name == "phone_context":
        return tool_name, {"query": detail}
    if tool_name in {"phone_files", "phone_storage_report", "phone_file_read", "phone_file_delete"}:
        return tool_name, {"path": detail}
    if tool_name in {"phone_capabilities", "phone_system_status"}:
        return tool_name, {}
    if tool_name in {"phone_cache_report", "phone_cache_trim"}:
        return tool_name, {"bytes_to_free": detail} if tool_name == "phone_cache_trim" else {}
    if tool_name in {"phone_packages", "phone_package_action"}:
        return tool_name, {"package": detail} if tool_name == "phone_package_action" else {}
    if tool_name == "phone_shell":
        return tool_name, {"command": detail}
    if tool_name in {"phone_click", "phone_set_text", "phone_scroll"}:
        return tool_name, {"target": detail}
    if tool_name == "phone_key":
        return tool_name, {"action": detail}
    return tool_name, {}


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
        return f"git_status:{taskboard_tool_summary(tool, arguments or {})}"
    if tool == "web_fetch":
        return f"{tool}:{str((arguments or {}).get('url', '?'))[:80]}"
    if tool == "map_project":
        root = str((arguments or {}).get("root") or "cwd")
        return f"map_project:{root[:80]}"
    if tool == "computer_targets":
        return f"computer_targets:{str((arguments or {}).get('kind') or 'windows')[:80]}"
    if tool == "computer_observe":
        return f"computer_observe:{str((arguments or {}).get('operation') or (arguments or {}).get('kind') or 'observe')[:80]}"
    if tool == "computer_act":
        return f"computer_act:{str((arguments or {}).get('action') or '?')[:80]}"
    if tool == "phone_context":
        return f"phone_context:{str((arguments or {}).get('query') or 'screen')[:80]}"
    if tool in {"phone_files", "phone_storage_report", "phone_file_read", "phone_file_delete"}:
        return f"{tool}:{str((arguments or {}).get('path') or 'selected folder')[:80]}"
    if tool in {"phone_capabilities", "phone_system_status"}:
        return f"{tool}:origin phone"
    if tool == "phone_cache_report":
        return "phone_cache_report:Android caches"
    if tool == "phone_cache_trim":
        return f"phone_cache_trim:{str((arguments or {}).get('bytes_to_free') or '?')[:80]} bytes"
    if tool == "phone_packages":
        return f"phone_packages:{str((arguments or {}).get('scope') or 'user')[:80]}"
    if tool == "phone_package_action":
        args = arguments or {}
        return f"phone_package_action:{str(args.get('action') or '?')[:24]}:{str(args.get('package') or '?')[:50]}"
    if tool == "phone_shell":
        return f"phone_shell:{len(str((arguments or {}).get('command') or ''))} chars"
    if tool in {"phone_click", "phone_set_text", "phone_scroll"}:
        return f"{tool}:{str((arguments or {}).get('target') or 'screen')[:80]}"
    if tool == "phone_key":
        return f"phone_key:{str((arguments or {}).get('action') or '?')[:80]}"
    if tool == "perceive":
        return f"perceive:{str((arguments or {}).get('source') or '?')[:80]}"
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
        if gate == "manual" or kind == "ask":
            return False
        if kind == "report" and gate == "tool":
            return _tool_matches_report_artifact(tool_name, arguments)
        if gate == "verification":
            args = arguments or {}
            return tool_is_verification_signal(tool_name, args) or (
                _task_allows_inspection_verification(task)
                and tool_matches_task_kind(tool_name, "inspect", args, title=str(getattr(task, "title", "") or ""))
            )
        if kind == "verify":
            args = arguments or {}
            if gate != "tool":
                return tool_is_verification_signal(tool_name, args)
            return tool_is_verification_signal(tool_name, args) or tool_matches_task_kind(
                tool_name,
                "inspect",
                args,
                title=str(getattr(task, "title", "") or ""),
            )
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


def should_record_taskboard_tool_evidence(
    task: object,
    tool_name: str,
    arguments: dict | None = None,
    *,
    idx: int = 0,
    total: int = 1,
) -> bool:
    """Return True when a tool result belongs on the active task row."""
    kind = str(getattr(task, "kind", "") or "").lower().strip()
    gate = str(getattr(task, "completion_gate", "") or "").lower().strip()
    expected = bool(getattr(task, "expected_evidence", None))
    if not (kind or gate or expected):
        return True
    return tool_should_advance_task(tool_name, task, idx, total, arguments=arguments or {})


def _duration_seconds(value: str, unit: str) -> float:
    number = float(value)
    normalized = str(unit or "s").lower()
    if normalized.startswith("ms") or normalized.startswith("millisecond"):
        return number / 1000.0
    if normalized == "m" or normalized.startswith("min"):
        return number * 60.0
    if normalized == "h" or normalized.startswith("hour"):
        return number * 3600.0
    if normalized == "d" or normalized.startswith("day"):
        return number * 86400.0
    return number


def _command_wait_seconds(command: str) -> list[float]:
    """Extract explicit wait durations from supported shell command forms."""
    text = str(command or "").lower()
    durations: list[float] = []

    for match in re.finditer(r"\btime\.sleep\s*\(\s*(\d+(?:\.\d+)?)\s*\)", text):
        durations.append(_duration_seconds(match.group(1), "s"))
    for match in re.finditer(
        r"\bstart-sleep\b\s+(?:-(seconds?|s|milliseconds?|ms)\s+)?(\d+(?:\.\d+)?)",
        text,
    ):
        durations.append(_duration_seconds(match.group(2), match.group(1) or "s"))
    for match in re.finditer(r"\btimeout\b\s+(?:/t\s+)?(\d+(?:\.\d+)?)", text):
        durations.append(_duration_seconds(match.group(1), "s"))
    for match in re.finditer(r"(?<![.\w-])sleep\s+(\d+(?:\.\d+)?)\s*([smhd]?)\b", text):
        durations.append(_duration_seconds(match.group(1), match.group(2) or "s"))
    return durations


def _specific_execution_matches(title: str, tool_name: str, arguments: dict) -> bool:
    """Match narrow command-shaped rows without pretending any shell proves them."""
    text = " ".join(str(title or "").lower().split())
    required_actions = execution_actions_from_text(text)
    if required_actions:
        return bool(required_actions.intersection(shell_execution_actions(tool_name, arguments)))
    duration = re.search(
        r"\b(?:wait|sleep)(?:\s+for)?\s+(\d+(?:\.\d+)?)\s*"
        r"(s|sec(?:ond)?s?|m|min(?:ute)?s?|h|hours?|d|days?)\b",
        text,
    )
    names_time_sleep = any(marker in text for marker in ("time.sleep", "start-sleep"))
    if not duration and not names_time_sleep:
        return True
    if tool_name != "shell":
        return False
    command = str((arguments or {}).get("command") or "").lower()
    if not re.search(r"\b(?:sleep|timeout|start-sleep)\b", command):
        return False
    command_durations = _command_wait_seconds(command)
    if duration:
        requested = _duration_seconds(duration.group(1), duration.group(2))
        return any(abs(actual - requested) < 0.001 for actual in command_durations)
    return bool(command_durations)


def execution_actions_from_text(text: str) -> set[str]:
    """Return explicit delivery/lifecycle actions named in task or claim text."""
    value = " ".join(str(text or "").lower().split())
    actions: set[str] = set()
    if re.search(r"\bcommit(?:ted|ting|s)?\b", value):
        actions.add("commit")
    if re.search(r"\bpush(?:ed|ing|es)?\b", value):
        actions.add("push")
    if re.search(r"\b(?:deploy(?:ed|ing|s|ment)?|publish(?:ed|ing|es)?)\b", value):
        actions.add("deploy")
    return actions


def shell_execution_actions(tool_name: str, arguments: dict) -> set[str]:
    """Classify only successful-command-shaped delivery actions, without payload text."""
    if str(tool_name or "").strip().lower() != "shell":
        return set()
    command = str((arguments or {}).get("command") or "").lower()
    command = _ssh_remote_command_text(command) or command
    actions: set[str] = set()
    if re.search(r"\bgit\s+(?:-[^\s]+\s+)*commit\b", command):
        actions.add("commit")
    if re.search(r"\bgit\s+(?:-[^\s]+\s+)*push\b", command):
        actions.add("push")
    if re.search(
        r"(?:^|[;&|]\s*)\s*(?:\.?[\\/]?\S*[\\/])?(?:deploy|publish)(?:\.[a-z0-9]+)?\b|"
        r"(?:^|[;&|]\s*|['\"]\s*)(?:sudo\s+)?systemctl\s+(?:--user\s+)?(?:start|restart|reload)\b|"
        r"(?:^|[;&|]\s*|['\"]\s*)service\s+\S+\s+(?:start|restart|reload)\b|"
        r"(?:^|[;&|]\s*|['\"]\s*)docker(?:\s+compose)?\s+(?:up|start|restart)\b|"
        r"\b(?:python|py|powershell|pwsh)\b[^;&|]{0,160}[\\/](?:deploy|publish)(?:\.[a-z0-9]+)?\b",
        command,
    ):
        actions.add("deploy")
    return actions


def shell_delivery_evidence_actions(tool_name: str, arguments: dict) -> set[str]:
    """Return delivery actions established by execution or exact state checks.

    A compound deploy command can restart the service successfully and then
    return nonzero from a later post-check. The failed record must stay failed,
    but a later successful, AND-linked remote check of both the exact Git HEAD
    and active service establishes the deployed outcome for final claim gates.
    This broader evidence classifier is not used to advance execute task rows.
    """
    actions = shell_execution_actions(tool_name, arguments)
    if str(tool_name or "").strip().lower() != "shell":
        return actions
    command = str((arguments or {}).get("command") or "").lower()
    command = _ssh_remote_command_text(command) or command
    for segment in re.split(r"[;\r\n]+", command):
        if "&&" not in segment:
            continue
        service_active = re.search(
            r"\bsystemctl\s+(?:--user\s+)?is-active(?:\s+--quiet)?\s+\S+",
            segment,
        )
        exact_head = re.search(
            r"(?:\btest\b|\[)[^;&|]{0,220}"
            r"\$\(\s*git\s+(?:-[^\s]+\s+)*rev-parse\s+head\s*\)"
            r"[^;&|]{0,120}=\s*[0-9a-f]{7,40}\b",
            segment,
        )
        if service_active and exact_head:
            actions.add("deploy")
            break
    return actions


def tool_matches_task_kind(tool_name: str, kind: str, arguments: dict, *, title: str = "") -> bool:
    """Return True when a tool call matches a metadata-bearing task kind."""
    if tool_name == "mo_design":
        action = str(arguments.get("action") or "").strip().lower()
        if kind == "inspect":
            return action in {"read", "list", "board_read"}
        if kind == "edit":
            return action in {"update", "board_propose"}
        if kind == "execute":
            return action in {"open", "show", "complete"}
        return False
    if tool_name == "migrate":
        action = str(arguments.get("action") or "").strip().lower()
        return (kind == "inspect" and action in {"inspect", "plan"}) or (
            kind == "edit" and action == "apply"
        )
    if tool_name == "file_transfer":
        action = str(arguments.get("action") or "list").strip().lower()
        return (kind == "inspect" and action == "list") or (
            kind == "execute" and action in {"send", "accept", "cancel", "retry"}
        )
    if kind == "inspect":
        if task_requires_broad_scope_evidence(title):
            return _tool_matches_broad_scope_inspect(tool_name, arguments)
        return tool_name in TASKBOARD_INSPECTION_TOOLS or tool_is_inspection_shell(tool_name, arguments)
    if kind == "edit":
        return (
            tool_name in FILE_MUTATION_TOOLS
            or tool_is_editing_shell(tool_name, arguments)
            or (tool_name in DESKTOP_EXECUTION_TOOLS and task_title_is_desktop_work(title))
            or tool_name in PHONE_ACTUATION_TOOLS
        )
    if kind == "execute":
        return (
            tool_name in {"shell", "test_runner"} or tool_name in DESKTOP_EXECUTION_TOOLS or tool_name in PHONE_ACTUATION_TOOLS
        ) and _specific_execution_matches(title, tool_name, arguments)
    if kind == "verify":
        return tool_is_verification_signal(tool_name, arguments)
    if kind == "report":
        return tool_name in FILE_MUTATION_TOOLS or tool_name in TASKBOARD_INSPECTION_TOOLS or tool_name in DESKTOP_EXECUTION_TOOLS or tool_name in PHONE_ACTUATION_TOOLS
    if not kind:
        return tool_name in TASKBOARD_INSPECTION_TOOLS or tool_name in FILE_MUTATION_TOOLS or tool_name in {"shell", "test_runner"}
    return False


def taskboard_tool_evidence_item(tool_name: str, arguments: dict | None = None) -> str:
    """Return the Agent-compatible evidence label for a taskboard tool event."""
    summary = taskboard_tool_summary(tool_name, arguments or {})
    return f"{tool_name}:{summary}" if summary else str(tool_name or "tool")


def taskboard_tool_summary(name: str, arguments: dict[str, Any]) -> str:
    """Summarize tool arguments exactly as main taskboard evidence expects."""
    if name == "mail":
        return str(arguments.get("action") or "status")[:32]
    if name in {"read_file", "write_file", "edit_file"}:
        return str(arguments.get("path") or "")[:240]
    if name == "test_runner":
        return str(arguments.get("command") or "")[:240]
    if name == "git_status":
        action = str(arguments.get("action") or "status").strip().lower()
        root = str(arguments.get("workdir") or arguments.get("path") or "")[:200]
        return f"{action}:{root}" if root else action
    if name in {"find_files", "grep", "project_bridge", "map_project"}:
        return str(
            arguments.get("root")
            or arguments.get("workdir")
            or arguments.get("path")
            or arguments.get("pattern")
            or ""
        )[:240]
    if name == "shell":
        actions = shell_execution_actions(name, arguments)
        if actions:
            markers = (("commit", "git commit"), ("push", "git push"), ("deploy", "deploy"))
            return "; ".join(marker for action, marker in markers if action in actions)
        return str(arguments.get("command") or "")[:240]
    if name == "phone_context":
        return str(arguments.get("query") or "screen")[:240]
    if name in {"phone_files", "phone_storage_report", "phone_file_read", "phone_file_delete"}:
        return str(arguments.get("path") or "selected folder")[:240]
    if name in {"phone_capabilities", "phone_system_status"}:
        return "origin phone"
    if name in {"phone_cache_report", "phone_cache_trim"}:
        return str(arguments.get("bytes_to_free") or "Android caches")[:240]
    if name == "phone_packages":
        return str(arguments.get("scope") or "user")[:240]
    if name == "phone_package_action":
        return (
            f"{arguments.get('action') or ''} {arguments.get('package') or ''}"
        ).strip()[:240]
    if name == "phone_shell":
        return f"{len(str(arguments.get('command') or ''))} command character(s)"
    if name in {"phone_click", "phone_set_text", "phone_scroll"}:
        return str(arguments.get("target") or "screen")[:240]
    if name == "phone_key":
        return str(arguments.get("action") or "")[:240]
    if name in {"computer_targets", "computer_observe", "computer_act"}:
        return str(arguments.get("action") or arguments.get("operation") or arguments.get("kind") or "computer")[:240]
    if name == "perceive":
        return str(arguments.get("source") or "")[:240]
    if name == "point_on_screen":
        x = arguments.get("x", arguments.get("end_x"))
        y = arguments.get("y", arguments.get("end_y"))
        return f"{x},{y}" if x is not None and y is not None else "pointer"
    # Graph and code-structure tools
    if name == "code_search":
        return str(arguments.get("query") or "")[:240]
    if name == "project_history":
        action = str(arguments.get("action") or "").strip()
        reference = str(arguments.get("id") or arguments.get("owner") or "").strip()
        summary = f"{action} {reference}".strip()
        if action == "trace" and arguments.get("source") is not None:
            summary += f" source {arguments['source']}"
        return summary[:240]
    if name in {"find_callers", "find_callees"}:
        return str(arguments.get("symbol") or "")[:240]
    if name == "tool_search":
        return str(arguments.get("query") or "")[:240]
    if name == "build_graph":
        return "code graph"
    if name in {"graph_explain", "graph_neighbors"}:
        return str(arguments.get("query") or "")[:240]
    if name == "graph_path":
        src = str(arguments.get("source") or "")
        tgt = str(arguments.get("target") or "")
        return f"{src} → {tgt}"[:240] if src and tgt else (src or tgt)[:240]
    if name == "graph_stats":
        return "graph stats"
    # Tasking and planning
    if name == "set_plan":
        mode = str(arguments.get("mode") or "")
        if mode in {"pause", "cancel"}:
            return mode
        tasks = arguments.get("tasks") or []
        count = len(tasks) if isinstance(tasks, list) else 0
        return f"{mode} · {count} task(s)" if mode else f"{count} task(s)"
    if name == "complete_task":
        return str(arguments.get("task_id") or "current")[:240]
    if name == "schedule_job":
        action = str(arguments.get("action") or "")
        job_name = str(arguments.get("name") or "")
        return f"{action} · {job_name}".strip(" ·")[:240]
    # Web tools
    if name == "web_search":
        return str(arguments.get("query") or "")[:240]
    if name == "web_fetch":
        return str(arguments.get("url") or "")[:240]
    # Visual / design tools
    if name == "mo_design":
        action = str(arguments.get("action") or "")
        title = str(arguments.get("title") or "")
        return f"{action} · {title}".strip(" ·")[:240]
    if name == "show_viz":
        return str(arguments.get("kind") or arguments.get("title") or "")[:240]
    if name in {"show_image", "edit_image"}:
        return str(arguments.get("path") or "")[:240]
    if name == "generate_image":
        return str(arguments.get("prompt") or "")[:240]
    # Profile / learning
    if name == "record_profile_fact":
        category = str(arguments.get("category") or "").strip().lower()
        fact = " ".join(str(arguments.get("fact") or "").split())
        from ..profile.facts import validate_profile_fact
        if fact and not validate_profile_fact(category, fact):
            return f"{category} · {fact}"[:240]
        return category[:240]
    if name == "record_convention":
        return str(arguments.get("name") or "")[:240]
    # System / diagnostics
    if name == "system_health":
        return "MO runtime"
    if name == "credential_status":
        return str(arguments.get("service") or "all")[:240]
    # Repo tools
    if name in {"inspect_repo", "use_repo"}:
        return str(arguments.get("url") or "")[:240]
    # Migration
    if name == "migrate":
        parts = [
            str(arguments.get("action") or "").strip(),
            str(arguments.get("source") or "").strip(),
            str(arguments.get("asset") or "").strip(),
        ]
        return " · ".join(part for part in parts if part)[:240]
    # Desktop / everywhere
    if name == "desktop_sync":
        return "terminal context"
    if name == "everywhere_readiness":
        return str(arguments.get("view") or "status")[:240]
    if name == "everywhere_pair_android":
        return "Android QR"
    # File transfer
    if name == "file_transfer":
        action = str(arguments.get("action") or "list").strip()
        if action == "send":
            path = str(arguments.get("path") or "").strip()
            target = str(arguments.get("target") or "").strip()
            detail = f"{path} → {target}" if path and target else path or target
        else:
            detail = str(arguments.get("transfer_id") or arguments.get("direction") or "").strip()
        return " · ".join(part for part in (action, detail) if part)[:240]
    # SystemCare
    if name == "systemcare_status":
        return "Windows health"
    if name == "systemcare_scan":
        return str(arguments.get("mode") or "safe")[:240]
    if name == "systemcare_plan":
        ids = arguments.get("finding_ids") or []
        return f"{len(ids)} finding(s)" if isinstance(ids, list) else "findings"
    if name == "systemcare_apply":
        return str(arguments.get("plan_id") or "")[:240]
    if name == "systemcare_rollback":
        return str(arguments.get("receipt_id") or "")[:240]
    if name == "systemcare_calibrate":
        return "calibration"
    if name == "systemcare_cancel":
        return str(arguments.get("operation_id") or "current")[:240]
    # Fallback: try common argument names so new tools are not silently blank.
    for key in ("query", "path", "name", "action", "url", "command", "symbol", "text", "prompt"):
        val = str(arguments.get(key) or "").strip()
        if val:
            return val[:240]
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


def edit_line_range(name: str, arguments: dict[str, Any]) -> str:
    """Return the existing source line range affected by an edit, or ``""``.

    The activity event is emitted before the tool executes, so this identifies
    the old source location that the edit will replace.
    """
    args = arguments or {}
    path = str(args.get("path") or "")
    if not path:
        return ""
    try:
        target = Path(path)
        source = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else ""
    except OSError:
        return ""

    if name == "edit_file":
        old_text = str(args.get("old_text") or "")
        if not old_text:
            return ""
        offset = source.find(old_text)
        if offset < 0:
            return ""
        start = source.count("\n", 0, offset) + 1
        count = max(1, len(old_text.splitlines()))
    elif name == "write_file":
        start, count = 1, max(1, len(source.splitlines())) if source else 1
    else:
        return ""
    end = start + count - 1
    return f"L{start}" if end == start else f"L{start}-L{end}"


def task_requires_broad_scope_evidence(title: str) -> bool:
    text = str(title or "").lower()
    if _task_title_is_history_scoped(text):
        return False
    # Titles that explicitly scope to specific files/areas are NOT broad.
    if re.search(r"\b(?:trace|flow|flows|dependencies|risk|risks|finding|findings)\b", text):
        return False
    if re.search(r"\b(?:inspect|read)\s+(?:actual\s+)?(?:files?|docs?|runtime|context)\b", text):
        return False
    # Identification alone does not imply a broad inventory. A targeted source
    # read can identify a particular owner or configuration just as accurately.
    return bool(re.search(r"\b(?:scope|inventory|discover|survey|scan all|map|mapping|map out)\b", text))


def _task_title_is_history_scoped(text: str) -> bool:
    return bool(re.search(
        r"\b(?:history|historical|git\s+(?:log|show)|recent\s+commits?|"
        r"commit\s+history|changelog|release\s+notes)\b",
        str(text or "").lower(),
    ))


def _tool_matches_broad_scope_inspect(tool_name: str, arguments: dict | None) -> bool:
    if tool_name in {"grep", "find_files", "git_status", "project_bridge", "map_project"}:
        return True
    if not tool_is_inspection_shell(tool_name, arguments or {}):
        return False
    command = str((arguments or {}).get("command") or "")
    return not _shell_command_is_history_only_inspection(command)


def _shell_command_is_history_only_inspection(command: str) -> bool:
    value = str(command or "").strip()
    if not value or not shell_command_is_inspection_only(value):
        return False
    clauses = [
        clause.strip()
        for clause in re.split(r"(?:\r?\n|&&|\|\||[;&])", value)
        if clause.strip()
    ]
    # A pipeline still inspects only history when its source is `git log` or
    # `git show`; `head`, `tail`, grep, and similar consumers merely format the
    # same historical stream. Sequential current-source inspection remains
    # valid evidence for the broad row.
    return bool(clauses) and all(
        _shell_segment_is_history_only_inspection(clause.split("|", 1)[0])
        for clause in clauses
    )


def _shell_segment_is_history_only_inspection(segment: str) -> bool:
    text = str(segment or "").strip()
    if not text:
        return False
    if re.match(r"(?i)^(?:history|get-history|doskey\s+/history)\b", text):
        return True
    subcommand = _git_shell_segment_subcommand(text)
    return subcommand in {"log", "show", "reflog", "shortlog", "whatchanged"}


def _git_shell_segment_subcommand(segment: str) -> str:
    try:
        parts = shlex.split(str(segment or ""), posix=True)
    except ValueError:
        parts = str(segment or "").split()
    if len(parts) < 2:
        return ""
    executable = Path(str(parts[0]).strip("'\"")).name.lower()
    if executable.endswith(".exe"):
        executable = executable[:-4]
    if executable != "git":
        return ""
    value_options = {
        "-C", "-c", "--config-env", "--exec-path", "--git-dir",
        "--namespace", "--super-prefix", "--work-tree",
    }
    idx = 1
    while idx < len(parts):
        option = str(parts[idx]).strip("'\"")
        if not option.startswith("-") or option == "-":
            break
        if option in value_options and "=" not in option:
            idx += 2
        else:
            idx += 1
    if idx >= len(parts):
        return ""
    return str(parts[idx]).strip("'\"").lower()


def task_title_is_desktop_work(title: str) -> bool:
    text = str(title or "").lower()
    return bool(re.search(
        r"\b(?:desktop|screen|window|terminal|console|app|application|browser|ui|uia|"
        r"click|type|keyboard|press|focus|open|close|invoke|mouse|pointer)\b",
        text,
    ))


def tool_is_inspection_shell(tool_name: str, arguments: dict) -> bool:
    if tool_name != "shell":
        return False
    return shell_command_is_inspection_only(str((arguments or {}).get("command") or ""))


def tool_is_editing_shell(tool_name: str, arguments: dict) -> bool:
    if tool_name != "shell":
        return False
    command = str((arguments or {}).get("command") or "")
    if shell_command_is_mutating(command):
        return True
    # This CLI repairs a verification input without changing source files.
    # Treat the successful write as a mutation so cached failures cannot survive it.
    from tools.shell import _test_invocation

    module, options = _test_invocation(command)
    if module == "core.diagnostics.test_preflight" and "--write-overlay-manifest" in options:
        return True
    # The sandbox's shared shell classifier deliberately masks quoted strings
    # so comparisons inside ``python -c`` are not mistaken for redirection.
    # Inspect the unmasked command only for explicit inline-language writes that
    # the shell-level classifier cannot see through those quotes.
    return bool(re.search(
        r"\bblack\b(?![^;&|]*(?:--check|--diff)\b)|"
        r"\bruff\s+format\b(?![^;&|]*(?:--check|--diff)\b)|"
        r"\b(?:sed\s+-i|perl\s+-pi)\b|"
        r"\b(?:write_text|write_bytes|unlink|mkdir|rmdir|rename|touch)\s*\(|"
        r"\b(?:pathlib\.)?Path\s*\([^)]*\)\.replace\s*\(|"
        r"\bos\.(?:remove|unlink|mkdir|makedirs|rmdir|removedirs|rename|replace)\s*\(|"
        r"\bshutil\.(?:copy|copy2|copyfile|copytree|move|rmtree)\s*\(|"
        r"\bopen\([^)]*,\s*['\"][^'\"]*(?:w|a|x|\+)[^'\"]*['\"]",
        command,
        re.I | re.S,
    ))


def tool_is_execution_shell(tool_name: str, arguments: dict) -> bool:
    """Recognize lifecycle/mutation commands that belong under an execute row."""
    if tool_name != "shell":
        return False
    if tool_is_inspection_shell(tool_name, arguments) or tool_is_editing_shell(tool_name, arguments):
        return False
    command = str((arguments or {}).get("command") or "").lower()
    if tool_is_verification_signal(tool_name, arguments):
        return False
    if shell_command_is_mutating(command):
        return True
    return bool(re.search(
        r"\b(?:start-process|uvicorn|gunicorn|streamlit\s+run|flask\s+run|"
        r"python\s+-m\s+http\.server|systemctl\s+(?:--user\s+)?(?:start|restart|reload)|"
        r"service\s+\S+\s+(?:start|restart|reload)|docker(?:\s+compose)?\s+(?:run|up|start|restart)|"
        r"git\s+(?:commit|push|pull|merge|rebase|cherry-pick|tag)|gh\s+pr\s+create|"
        r"deploy|publish)\b",
        command,
    ))
