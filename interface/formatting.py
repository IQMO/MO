from __future__ import annotations

import json
import time
from typing import Any

from .state import TokenStatus

# Single source for the terminal-state markers a GoalRunner result string can
# start with — the TUI and native loops match against these to decide when a
# goal has finished, instead of each re-listing the tuple.
GOAL_TERMINAL_MARKERS: tuple[str, ...] = ("[✓ DONE]", "[✗ BLOCKED]", "[PAUSED]", "[GOAL STOPPED]")


def goal_result_is_terminal(text: str) -> bool:
    """True when a GoalRunner result string signals a terminal state."""
    return str(text or "").startswith(GOAL_TERMINAL_MARKERS)


# Dots4 is the compact one-cell spinner selected for the terminal lane.  Keep
# the exact verified frame order from FGRibreau/spinners so the mark stays on
# the same row as the activity sentence and never changes its cell footprint.
DOTS4_PHASES: tuple[str, ...] = (
    "⠄", "⠆", "⠇", "⠋", "⠙", "⠸", "⠰", "⠠",
    "⠰", "⠸", "⠙", "⠋", "⠇", "⠆",
)
DOTS4_PHASE_SECONDS = 0.08


def brand_spinner_frame(now: float | None = None) -> str:
    """Return the compact one-cell Dots4 frame used by the terminal lane."""
    current = time.time() if now is None else float(now)
    return DOTS4_PHASES[int(current / DOTS4_PHASE_SECONDS) % len(DOTS4_PHASES)]


def format_k(num: int) -> str:
    if num >= 1000:
        return f"{num/1000:.1f}k"
    return str(num)


def token_status_from_agent(agent: Any) -> TokenStatus:
    session = getattr(agent, "session", None)
    token_log = getattr(session, "token_log", [])
    input_total = getattr(session, "input_tokens", None)
    output_total = getattr(session, "output_tokens", None)
    input_tokens = int(input_total) if input_total is not None else sum(
        e.get("input_tokens", 0) for e in token_log
    )
    output_tokens = int(output_total) if output_total is not None else sum(
        e.get("output_tokens", 0) for e in token_log
    )
    reasoning = getattr(agent, "reasoning", getattr(agent, "config", {}).get("agent", {}).get("reasoning", "high"))
    # Explicit result-cap savings
    estimator = getattr(agent, "_context_saved_tokens_estimate", None)
    saved_tokens = int(estimator()) if callable(estimator) else 0
    saved_chars = int(getattr(agent, "_tool_context_saved_chars", lambda: 0)() or 0) if callable(getattr(agent, "_tool_context_saved_chars", None)) else 0
    saving_ops = int(getattr(agent, "_tool_context_saving_ops", lambda: 0)() or 0) if callable(getattr(agent, "_tool_context_saving_ops", None)) else 0
    # Serialized compaction includes opaque replay; it is not tokenized text.
    try:
        compaction_chars = max(0, int(getattr(agent, "session_compaction_total_saved", 0) or 0))
    except (TypeError, ValueError):
        compaction_chars = 0
    return TokenStatus(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        saved_tokens_est=saved_tokens,
        saved_chars=saved_chars,
        saving_ops=saving_ops,
        compaction_chars=compaction_chars,
        provider_name=getattr(agent, "provider_name", ""),
        model=getattr(agent, "model", ""),
        reasoning=reasoning,
    )


def format_context_reduction(status: TokenStatus) -> str:
    """Size reductions, separate from cumulative billed or cached input."""
    parts = []
    if status.saved_tokens_est > 0:
        parts.append(f"capped ~{format_k(status.saved_tokens_est)}t")
    if status.compaction_chars > 0:
        parts.append(f"compacted {format_k(status.compaction_chars)}ch")
    return " · " + " · ".join(parts) if parts else ""


def format_token_status(status: TokenStatus) -> str:
    in_str = format_k(status.input_tokens)
    out_str = format_k(status.output_tokens)
    base = f"↑{in_str} ↓{out_str}"
    base += format_context_reduction(status)
    return f"{base} \u00b7 ({status.provider_name}) {status.model} \u2022 {status.reasoning}"


def format_agent_status(agent: Any) -> str:
    return format_token_status(token_status_from_agent(agent))


_ACTIVITY_TOOL_LABELS: dict[str, str] = {
    # Reading / retrieval
    "read_file": "Reading",
    "phone_file_read": "Reading",
    "phone_files": "Reading",
    "phone_storage_report": "Reading",
    # Search / discovery
    "grep": "Searching",
    "find_files": "Searching",
    "web_search": "Searching",
    "tool_search": "Searching",
    "inspect_repo": "Searching",
    "use_repo": "Searching",
    # Graph / project structure
    "code_search": "Graphing",
    "project_history": "History",
    "find_callers": "Graphing",
    "find_callees": "Graphing",
    "build_graph": "Graphing",
    "graph_explain": "Graphing",
    "graph_neighbors": "Graphing",
    "graph_path": "Graphing",
    "graph_stats": "Graphing",
    "map_project": "Graphing",
    # External/provider/MCP calls
    "web_fetch": "Calling",
    "file_transfer": "Running",
    "migrate": "Running",
    "mcp": "Calling",
    "use_mcp": "Calling",
    # File and profile writes
    "edit_file": "Writing",
    "write_file": "Writing",
    "record_profile_fact": "Writing",
    "record_convention": "Writing",
    # Execution / inspection surfaces
    "shell": "Running",
    "test_runner": "Testing",
    "computer_act": "Working",
    "computer_targets": "Searching",
    "computer_observe": "Working",
    "point_on_screen": "Pointing",
    "show_image": "Viewing",
    "show_viz": "Viewing",
    "perceive": "Viewing",
    # MO planning / diagnostics
    "set_plan": "Planning",
    "complete_task": "Planning",
    "mo_design": "Designing",
    "git_status": "Checking",
    "project_bridge": "Checking",
    "system_health": "Checking",
    "credential_status": "Checking",
    "everywhere_readiness": "Checking",
}


_ACTIVITY_RUNTIME_LABELS = {
    "gathering work context...": "Knowledge",
    "checking edited files with the language server...": "LSP",
    "checking affected tests...": "Testing",
    "recording conversation memory...": "Remembering",
    "finalizing response...": "Finalizing",
}


_GENERIC_TOOL_NAMES = frozenset({
    "read_file", "write_file", "edit_file", "find_files", "grep",
    "shell", "git_status", "test_runner", "web_search", "web_fetch",
})


def activity_tool_name(raw: str) -> str:
    """Extract the exact tool verb from the live ``tooling (...)`` event."""
    if not raw.lower().startswith("tooling ("):
        return ""
    inner = raw[len("tooling ("):]
    name = inner.split(None, 1)[0].rstrip(")...")
    if name.startswith("mcp:"):
        return "mcp"
    if name.startswith("mcp__"):
        return "mcp"
    return name.lower()


def is_mo_method_tool(name: str) -> bool:
    """Use the executable catalog, keeping basic operations visually neutral."""
    name = str(name or "").strip().lower()
    if not name or name in _GENERIC_TOOL_NAMES:
        return False
    from tools import TOOL_DEFINITIONS

    return any((tool.get("function") or {}).get("name") == name for tool in TOOL_DEFINITIONS)


def is_mo_method_activity(activity: str) -> bool:
    """Recognize an executing runtime phase or a native catalog tool."""
    raw = str(activity or "").strip()
    return raw.lower() in _ACTIVITY_RUNTIME_LABELS or is_mo_method_tool(activity_tool_name(raw))


def explainer_activity_lines(output: str) -> tuple[str, ...]:
    """Format complete explainer progress from an owned shell's bounded tail.

    Child output is presentation evidence only; it never authorizes work or
    completes a task. Ordinary, partial and malformed output stays ordinary.
    """
    phases = {
        "draft": "Draft", "validation": "Validation", "validate": "Validation",
        "quality": "Checks", "check": "Checks", "narration": "Narration",
        "narrate": "Narration", "contact_sheet": "Contact sheet",
        "preview": "Preview", "render": "Render",
    }

    def label(value: Any) -> str:
        return " ".join("".join(char for char in str(value or "") if char.isprintable() or char.isspace()).split())[:64]

    for line in reversed(str(output or "")[-4096:].splitlines()):
        try:
            event = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(event, dict) or event.get("event") != "explainer_progress":
            continue
        phase, state = event.get("phase"), event.get("state", "running")
        if not isinstance(phase, str) or phase not in phases:
            continue
        if state not in ("running", "ready", "completed", "failed", "interrupted", "unknown"):
            continue
        completed, total = event.get("completed"), event.get("total")
        if type(completed) is not int or type(total) is not int or not 0 <= completed <= total:
            continue
        style, artifacts, verification = (event.get(key, {}) for key in ("style", "artifacts", "verification"))
        if not all(isinstance(item, dict) for item in (style, artifacts, verification)):
            continue
        headline = f"Explainer · {phases[phase]}"
        if state == "running" and total:
            headline += f" {completed * 100 // total}%"
        elif state != "running":
            headline += f" · {state}"
        style_name = label(style.get("skin")) if style.get("source") == "mo-system" else label(style.get("source"))
        style_text = " · ".join(filter(None, (style_name, label(style.get("layout"))))) or "unspecified"
        available = []
        for name, item in artifacts.items():
            if not isinstance(item, dict) or item.get("available") is not True:
                continue
            detail = label(name)
            if str(name).endswith(".mp4"):
                checked = label(item.get("verification")) or "unchecked"
                audio = "narration unchecked"
                if checked == "passed" and type(item.get("narrated")) is bool:
                    # A music bed is audio but not narration; never call it silent.
                    audio = "narrated" if item["narrated"] else "music only" if item.get("music") is True else "silent"
                    if item["narrated"] and item.get("music") is True:
                        audio = "narrated + music"
                detail += f" ({audio}, {checked})"
            available.append(detail)
        checks = label(verification.get("status")) or "not checked"
        if verification.get("kind"):
            checks += f" ({label(verification['kind'])})"
        return (
            headline,
            f"Style: {style_text}",
            "Artifacts: " + (", ".join(available) or "none available"),
            f"Verification: {checks}",
        )
    return ()


def computer_activity_descriptor(activity: str) -> tuple[str, str, str] | None:
    """Decode the normalized computer mode shared by Terminal and Desktop."""
    raw = str(activity or "").strip()
    name = activity_tool_name(raw)
    if name not in {"computer_targets", "computer_observe", "computer_act"}:
        return None
    marker = f"{name} "
    index = raw.lower().find(marker)
    if index < 0:
        return None
    mode = raw[index + len(marker):].split(None, 1)[0].rstrip(").")
    kind, separator, operation = mode.partition(":")
    if not separator:
        return None
    return name, kind, operation


def _computer_activity_label(activity: str) -> str:
    detail = computer_activity_descriptor(activity)
    if detail is None:
        return ""
    name, kind, operation = detail

    if name == "computer_targets":
        return "Checking" if kind == "owned" else "Searching"
    if name == "computer_observe":
        if kind == "screen" and operation == "capture":
            return "Viewing"
        if kind == "browser":
            return {"capture": "Viewing", "read": "Reading", "wait": "Checking"}.get(operation, "Working")
        return "Working"
    if kind == "browser" and operation == "open":
        return "Opening"
    if kind == "desktop" and operation == "launch":
        return "Opening"
    return "Working"


def activity_label(activity: str) -> str:
    """Return the compact live-lane label for current activity."""
    raw = str(activity or "").strip()
    text = raw.lower()
    if text in _ACTIVITY_RUNTIME_LABELS:
        return _ACTIVITY_RUNTIME_LABELS[text]
    tool_name = activity_tool_name(raw)
    computer_label = _computer_activity_label(raw)
    if computer_label:
        return computer_label
    if text.startswith("explainer · "):
        return _compact_activity_detail(raw)
    if tool_name in _ACTIVITY_TOOL_LABELS:
        return _ACTIVITY_TOOL_LABELS[tool_name]
    if is_mo_method_tool(tool_name):
        return _compact_activity_detail(tool_name.removeprefix("mo_").replace("_", " ").capitalize())
    if text.startswith("map_project:"):
        return "Graphing"
    if text.startswith("image gen:"):
        return "Generating"
    if "preparing" in text:
        return "Preparing"
    if "waiting on model" in text or "awaiting model" in text or "thinking" in text:
        # The request is sent and the model is producing its response — surface it
        # as active thinking rather than idle waiting. (Genuine reasoning is still
        # shown separately as dim reasoning lines.)
        return "Thinking"
    if "finalizing" in text or "critiquing" in text or "critique" in text:
        return "Finalizing"
    if "streaming" in text or "receiving" in text or "answer" in text:
        return "Answering"
    if "goal" in text:
        return "Goaling"
    return "Working"


def _compact_activity_detail(text: str, limit: int = 88) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"



def idle_status_text(now: float | None = None) -> str:
    """Return the compact idle heartbeat shown in the status lane."""
    current = time.time() if now is None else float(now)
    return f"{brand_spinner_frame(current)} idle"



# (activity_display removed 2026-06-10: its verbose intermediate was chained
# straight into activity_label, which discarded the detail — the live activity
# lane shows only the compact label.)
