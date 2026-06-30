"""Shared runtime intent and tool-signal classifiers.

These helpers keep Gateway lifecycle decisions and Agent evidence checks aligned
without letting either layer mutate taskboard truth. They are pure classifiers;
Gateway still owns board creation and Agent still owns row advancement.
"""
from __future__ import annotations

import re

from ..tooling.sandbox import shell_command_is_mutating

_VERIFICATION_COMMAND_RE = re.compile(
    r"\b("
    r"pytest|unittest|compileall|py_compile|ruff|mypy|pyright|eslint|"
    r"npm\s+test|npm\s+run\s+test|pnpm\s+test|pnpm\s+run\s+test|yarn\s+test|"
    r"vitest|jest|go\s+test|cargo\s+test|dotnet\s+test|git\s+diff\s+--check"
    r")\b"
)


def normalized_operator_text(text: str) -> str:
    """Normalize operator text for intent regexes without interpreting content."""
    return " ".join(str(text or "").strip().lower().split())


def looks_like_interrupted_resume_request(user_input: str) -> bool:
    """Return True only for explicit requests to resume parked interrupted work."""
    text = normalized_operator_text(user_input)
    if not text:
        return False
    if re.fullmatch(r"(?:continue|contuine|resume|carry\s+on|proceed)[.!?]*", text):
        return True
    if re.search(r"\b(?:continue|contuine|resume|carry\s+on)\b", text) and re.search(
        r"\b(?:unfinished|previous|paused)\s+work\b|\bworking\s+on\s+(?:the\s+)?unfinished\b",
        text,
    ):
        return True
    if re.fullmatch(r"(?:yes\s+)?proceed(?:\s+with\s+(?:it|this|that|them|these|those|all))?[.!?]*", text):
        return True
    declined = bool(re.search(r"\b(?:don'?t|do\s+not|dont|stop|leave|cancel)\b", text))
    if declined:
        return False
    if re.search(
        r"\b(?:finish|complete|jump\s+back|go\s+back|work\s+on|keep\s+working|pick\s+(?:it|that|this|them)?\s*(?:back\s+)?up)\b",
        text,
    ) and re.search(r"\b(?:this|that|it|work|unfinished|parked|previous|back|left)\b", text):
        return True
    return bool(
        re.search(r"\b(?:focus|refocus)\b", text)
        and re.search(r"\b(?:again|back|left|unfinished|parked|previous|work|what\s+was\s+left)\b", text)
    )


def shell_is_verification_command(command: str) -> bool:
    """Return True for shell commands that are evidence-grade verification."""
    return bool(_VERIFICATION_COMMAND_RE.search(str(command or "").lower()))


def tool_is_verification_signal(tool_name: str, arguments: dict | None = None) -> bool:
    """Return True when a tool call represents verification/test evidence."""
    name = str(tool_name or "").strip()
    if name == "test_runner":
        return True
    if name != "shell":
        return False
    command = str((arguments or {}).get("command") or "")
    return shell_is_verification_command(command)


def tool_is_runtime_work_signal(tool_name: str, arguments: dict | None = None) -> bool:
    """Return True for tool calls that imply doing work, not read-only orientation."""
    name = str(tool_name or "").strip()
    args = arguments or {}
    if name in {"write_file", "edit_file", "test_runner"}:
        return True
    if name == "shell":
        command = str(args.get("command") or "")
        if shell_is_verification_command(command):
            return True
        return shell_command_is_mutating(command)
    return False
