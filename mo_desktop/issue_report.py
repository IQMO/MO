"""User-approved MO Desktop issue report handoff.

This module keeps the desktop companion out of the terminal session: clicking
the report action starts a separate MO process with an injected one-shot prompt.
"""
from __future__ import annotations

import os
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from core.runtime.subprocess_flags import console_python_executable
from core.state.paths import (
    DESKTOP_ISSUE_REPORT_DIR,
    repo_root,
    resolve_state_path,
    runtime_config_path,
)
from core.tooling.sandbox import redact_sensitive_text
from core.utils.atomic_write import atomic_write_text

ISSUE_REPORT_BUTTON_LABEL = "Report"

_ADMISSION_RE = re.compile(
    r"\b(?:"
    r"(?:sorry|apolog(?:y|ize|ise|ized|ised))\b.{0,100}\b(?:wrong|incorrect|mistake|issue|bug|error|failed|broken)"
    r"|(?:i|that|this|the\s+last|the\s+previous)\b.{0,80}\b(?:was|were|am|got|did)\b.{0,80}\b(?:wrong|incorrect|mistake)"
    r"|(?:you(?:['’]?re|\s+are)\s+right)\b.{0,140}\b(?:wrong|wasted|failed|failure|mistake|bug|issue|error|blocked|couldn['’]?t|can['’]?t|cannot)"
    r"|(?:my\s+mistake|i\s+was\s+wrong|i\s+got\s+that\s+wrong|wrong\s+text)"
    r")\b",
    re.I,
)


def looks_like_issue_admission(text: str) -> bool:
    """True when visible desktop text admits a concrete MO-side issue."""
    clean = " ".join(str(text or "").strip().split())
    if not clean or len(clean) < 12:
        return False
    return bool(_ADMISSION_RE.search(clean))


def build_issue_report_prompt(reply_text: str, *, context: dict[str, Any] | None = None) -> str:
    """Build the separate-terminal prompt for a user-approved desktop issue report."""
    excerpt = redact_sensitive_text(" ".join(str(reply_text or "").strip().split()))[:1400]
    identity = {
        key: (context or {})[key]
        for key in ("session_id", "turn_id", "instance_id", "started_at")
        if (context or {}).get(key)
    }
    correlation = json.dumps(identity, ensure_ascii=True)
    return (
        "MO Desktop surfaced a user-approved issue report from its visible bubble.\n\n"
        "Task: investigate the MO Desktop issue root cause end to end and produce a covered report. "
        "Start from live evidence, not assumptions.\n\n"
        "Evidence seed:\n"
        f"- Surface: MO Desktop\n"
        f"- Visible admission: {excerpt or '(empty)'}\n\n"
        f"- Displayed reply correlation (historical data): {correlation}\n\n"
        "Required investigation:\n"
        "- Read `mo_desktop.diagnostics.build_mo_desktop_trace_report` with the exact "
        "session_id and turn_id above. Do not substitute the latest chat or process. "
        "Compare the event timestamps across restarts. If the correlation is absent or a "
        "bounded trace does not contain the requested turn, report that gap and locate the "
        "original timestamped evidence; never assume a newer turn is the same incident.\n"
        "- Use MO graph/MCP or the native structural graph when available for orientation, then verify "
        "with source reads, logs, and focused tests.\n"
        "- Identify the user-visible failure, exact root cause, affected files/functions, and the "
        "smallest safe fix path.\n"
        "- Do not commit, push, deploy, or perform destructive changes unless the operator explicitly "
        "approves that in this terminal.\n"
        "- Report verified findings, any fixes made, and the checks run."
    )


def write_issue_report_prompt(
    reply_text: str, *, config: dict[str, Any] | None = None, context: dict[str, Any] | None = None,
) -> Path:
    """Persist the injected prompt under private MO state and return its path."""
    root = Path(resolve_state_path(DESKTOP_ISSUE_REPORT_DIR, config=config))
    root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = root / f"issue-report-{stamp}.prompt.txt"
    atomic_write_text(path, build_issue_report_prompt(reply_text, context=context), encoding="utf-8")
    return path


def launch_issue_report_terminal(
    reply_text: str,
    *,
    config: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    popen: Any = subprocess.Popen,
) -> tuple[bool, str]:
    """Start a separate MO report process from a desktop admission bubble."""
    if not looks_like_issue_admission(reply_text):
        return False, "No reportable MO Desktop admission was detected."
    prompt_path = write_issue_report_prompt(reply_text, config=config, context=context)
    product_root = Path(repo_root())
    mo_py = product_root / "mo.py"
    env = dict(os.environ)
    env.setdefault("MO_PROJECT_CWD", str(product_root))
    command = [
        console_python_executable(),
        str(mo_py),
        "--prompt-file",
        str(prompt_path),
    ]
    config_path = runtime_config_path(config)
    if config_path:
        command.extend(["--config", config_path])
    try:
        if sys.platform == "win32":
            comspec = env.get("COMSPEC") or "cmd.exe"
            flags = int(getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
            flags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            popen(
                [comspec, "/k", *command],
                cwd=str(product_root),
                env=env,
                creationflags=flags,
            )
        else:
            popen(
                command,
                cwd=str(product_root),
                env=env,
                start_new_session=True,
            )
    except Exception as exc:
        return False, f"Could not launch MO issue report: {type(exc).__name__}"
    return True, "Opened a separate MO issue report terminal."
