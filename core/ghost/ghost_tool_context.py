"""Read-only tool scout for Ghost side-panel context.

Ghost remains a coordinator, not task truth owner. This module lets Ghost receive a
small, audited, sandbox-gated read-only snapshot before it writes side-panel or
route text, so its suggestions can be grounded in local workspace reality without
letting Ghost edit files or complete taskboards.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any
import traceback

from ..runtime.backend_monitor import get_monitor, redact_monitor_text
from ..utils.text_utils import words as _words
from ..tooling.sandbox import guard_tool_call, redact_sensitive_text

GHOST_TOOL_SURFACE = "ghost_panel"
_READ_ONLY_TOOLS = {"git_status", "find_files", "read_file", "grep", "project_bridge"}
_GAME_WORDS = {"game", "wordle", "zombie", "runner", "running", "cow", "horse", "meteor", "arcade", "web", "terminal"}
_CODE_WORDS = {"ghost", "route", "routing", "taskboard", "gateway", "worker", "queue", "provider", "sandbox", "tool", "tools"}


def build_ghost_tool_context(
    agent: Any,
    question: str,
    *,
    route_suggestion: Any | None = None,
    max_chars: int = 2200,
) -> str:
    """Return compact read-only tool context for Ghost, or "" when not useful."""
    text = str(question or "").strip()
    if _is_light_chat(text):
        return ""
    calls = _planned_calls(text, route_suggestion)
    if not calls:
        return ""

    lines = [
        "### Ghost read-only tool scout",
        "Audited read-only workspace facts for orientation; main MO/Gateway must still verify before claims.",
    ]
    for name, args in calls:
        if name not in _READ_ONLY_TOOLS:
            continue
        result, block_reason = _run_readonly_tool(agent, name, args)
        summary = _summarize_tool_result(name, args, result, block_reason)
        if summary:
            lines.append(f"- {summary}")
        if len("\n".join(lines)) >= max_chars:
            break

    result = redact_monitor_text("\n".join(lines), max_chars)
    return result[:max_chars].rstrip()


def _is_light_chat(text: str) -> bool:
    lowered = " ".join(str(text or "").lower().split())
    return lowered in {"hi", "hello", "hey", "yo", "hi ghost", "hello ghost", "hey ghost"}


def _planned_calls(question: str, route_suggestion: Any | None) -> list[tuple[str, dict[str, Any]]]:
    words = _words(question)
    objective = str(getattr(route_suggestion, "objective", "") or "")
    all_words = words | _words(objective)
    if not all_words:
        return []

    routeable = route_suggestion is not None
    gameish = bool(all_words & _GAME_WORDS)
    codeish = bool(all_words & _CODE_WORDS)
    explicit_path = _extract_path(question) or _extract_path(objective)

    if not (routeable or gameish or codeish or explicit_path):
        return []

    calls: list[tuple[str, dict[str, Any]]] = [("git_status", {})]
    if explicit_path:
        calls.append(("project_bridge", {"path": explicit_path, "limit": 1800}))
        calls.append(("read_file", {"path": explicit_path, "limit": 80}))
    elif gameish:
        terms = [w for w in sorted(all_words) if w in _GAME_WORDS]
        pattern = terms[0] if terms else "game"
        calls.extend([
            ("find_files", {"root": ".", "pattern": pattern, "limit": 80}),
            ("find_files", {"root": "tests", "pattern": pattern, "limit": 40}),
        ])
    elif codeish:
        terms = [w for w in sorted(all_words) if w in _CODE_WORDS][:3]
        pattern = terms[0] if terms else "ghost"
        calls.extend([
            ("find_files", {"root": "core", "pattern": pattern, "limit": 40}),
            ("find_files", {"root": "interface", "pattern": pattern, "limit": 40}),
        ])
    elif routeable:
        calls.append(("find_files", {"root": ".", "pattern": "", "limit": 60}))
    return calls[:5]


def _run_readonly_tool(agent: Any, name: str, args: dict[str, Any]) -> tuple[str, str | None]:
    sandbox_config = getattr(agent, "sandbox_config", {}) if agent is not None else {}
    allowed_roots = getattr(agent, "allowed_roots", None) if agent is not None else None
    monitor = get_monitor()
    worker_id = _ghost_worker_id(agent)
    summary = _monitor_tool_summary(name, args)
    if monitor:
        monitor.emit("tool_call", {
            "request": "ghost-scout",
            "surface": GHOST_TOOL_SURFACE,
            "worker_id": worker_id,
            "tool": name,
            "summary": summary,
        })
    block_reason = guard_tool_call(
        name,
        args,
        lane="review-only",
        allowed_roots=allowed_roots,
        sandbox_config=sandbox_config,
    )
    if monitor:
        monitor.emit("sandbox_guard", {"tool": name, "lane": "review-only", "surface": GHOST_TOOL_SURFACE, "enabled": bool(sandbox_config.get("enabled", True)) if isinstance(sandbox_config, dict) else True})
    if block_reason:
        result = block_reason
    else:
        try:
            from tools import TOOL_EXECUTORS

            executor = TOOL_EXECUTORS.get(name)
            result = executor(dict(args)) if executor else f"Error: Unknown tool '{name}'"
        except Exception as exc:
            result = f"Error executing {name}: {exc}"
    if monitor:
        monitor.emit("tool_result", {
            "request": "ghost-scout",
            "surface": GHOST_TOOL_SURFACE,
            "worker_id": worker_id,
            "tool": name,
            "blocked": bool(block_reason),
            "error": _tool_result_is_error(result),
            "chars": len(str(result)),
        })
    _write_ghost_tool_audit(agent, name, args, result, block_reason)
    return str(result), block_reason


def _ghost_worker_id(agent: Any) -> str:
    try:
        value = getattr(agent, "_provider_worker_id", None)
        if callable(value):
            return str(value() or "")
    except Exception:
        traceback.print_exc()
    return ""


def _write_ghost_tool_audit(agent: Any, name: str, args: dict[str, Any], result: str, block_reason: str | None) -> None:
    sandbox_config = getattr(agent, "sandbox_config", {}) if agent is not None else {}
    audit_path = sandbox_config.get("audit_log") if isinstance(sandbox_config, dict) else ""
    if not audit_path:
        return
    safe_args = _safe_audit_arguments(agent, name, args)
    entry = {
        "ts": time.time(),
        "surface": GHOST_TOOL_SURFACE,
        "worker_id": _ghost_worker_id(agent),
        "tool": name,
        "arguments": safe_args,
        "result_chars": len(str(result)),
        "blocked": bool(block_reason),
        "block_reason": redact_sensitive_text(block_reason or "")[:300],
    }
    try:
        p = Path(audit_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception:
        return


def _monitor_tool_summary(name: str, args: dict[str, Any]) -> str:
    target = args.get("path") or args.get("root") or args.get("pattern") or ""
    return redact_monitor_text(str(target or ""), 160)


def _tool_result_is_error(result: Any) -> bool:
    text = str(result or "").strip().lower()
    return text.startswith("error") or "[path blocked]" in text or "[shell blocked]" in text


def _safe_audit_arguments(agent: Any, name: str, args: dict[str, Any]) -> dict[str, str | int]:
    try:
        safe = getattr(agent, "_safe_audit_arguments", None)
        if callable(safe):
            return safe(name, args)
    except Exception:
        traceback.print_exc()

    def clean(value: object, limit: int = 300) -> str:
        return redact_sensitive_text(str(value or ""))[:limit]

    return {str(k): clean(v) for k, v in (args or {}).items() if not str(k).startswith("_")}


def _summarize_tool_result(name: str, args: dict[str, Any], result: str, block_reason: str | None) -> str:
    label = _tool_label(name, args)
    if block_reason:
        return f"{label}: blocked ({redact_monitor_text(block_reason, 180)})"
    text = str(result or "").strip()
    if not text:
        return f"{label}: no output"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if name == "git_status":
        return f"{label}: {redact_monitor_text(lines[0] if lines else text, 180)}"
    if name == "find_files":
        shown = ", ".join(lines[:12])
        suffix = f" (+{len(lines) - 12} more)" if len(lines) > 12 else ""
        return f"{label}: {redact_monitor_text(shown + suffix, 360)}"
    if name == "read_file":
        body = " | ".join(lines[:8])
        return f"{label}: {redact_monitor_text(body, 420)}"
    return f"{label}: {redact_monitor_text(' | '.join(lines[:8]), 420)}"


def _tool_label(name: str, args: dict[str, Any]) -> str:
    target = args.get("path") or args.get("root") or args.get("pattern") or args.get("workdir") or ""
    suffix = f":{target}" if target else ""
    return f"{name}{suffix}"



def _extract_path(text: str) -> str:
    match = re.search(r"(?:[A-Za-z]:\\[^\s)\]}>'\"]+|(?:core|interface|tools|tests|docs)/[^\s)\]}>'\"]+)", str(text or ""))
    return match.group(0).rstrip(".,;:") if match else ""
