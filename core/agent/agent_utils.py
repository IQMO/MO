"""MO agent utility helpers."""

import inspect
import os
import re
from pathlib import Path
import traceback

from ..env_utils import int_env
from ..path_defaults import ENV_MO_STATE_HOME, mo_home, private_state_enabled
from ..tasking.task_board import TaskBoard, board_update_event
from ..text_utils import cap_by_tokens, token_aware_truncation_enabled
from interface.task_board_view import render_rich


class TurnCancelled(Exception):
    """Raised inside a turn when the UI requests a safe abort."""


def _emit_task_board_update(task_board: TaskBoard, *, update: str = "updated", on_board_update: object = None, on_board_event: object = None) -> str:
    """Emit legacy render callback and optional structured event; return rich render."""
    rich = render_rich(task_board)
    if on_board_update:
        on_board_update(rich)
    if on_board_event:
        on_board_event(board_update_event(task_board, update=update, rich=rich))
    return rich


def _call_on_first_tool(callback, tool_name: str, arguments: dict):
    """Invoke the board-creation callback with tool signal when supported."""
    try:
        sig = inspect.signature(callback)
        params = list(sig.parameters.values())
        accepts_varargs = any(param.kind == param.VAR_POSITIONAL for param in params)
        positional = [
            param for param in params
            if param.kind in {param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD}
        ]
        if accepts_varargs or len(positional) >= 2:
            return callback(tool_name, arguments)
        if len(positional) == 1:
            return callback(tool_name)
    except (TypeError, ValueError):
        pass
    return callback()


def _prune_tool_audit_log(path: Path) -> None:
    max_bytes = max(0, int_env("MO_TOOL_AUDIT_MAX_BYTES", 2_000_000))
    if max_bytes <= 0:
        return
    try:
        if not path.exists() or path.stat().st_size <= max_bytes:
            return
        keep = max(1, int_env("MO_TOOL_AUDIT_KEEP_LINES", 5_000))
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-keep:]
        while len(("\n".join(lines) + "\n").encode("utf-8")) > max_bytes and len(lines) > 1:
            lines.pop(0)
        path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    except Exception:
        return


GHOST_PROPOSAL_SYSTEM = """You are Ghost, MO's fast planner and intent guardrail layer.

For any work request, produce:
1. Intent guardrails (plain text, 3-5 lines)
2. Structured task rows (JSON)

Separate the two sections with exactly "---" on its own line.

---FORMAT EXAMPLE---
Intent: Fix the login bug in auth.py — the validate() function has a null check that fails for some users.
Scope guardrails: Only auth.py and its tests. Do not touch session management.
Evidence required: Read auth.py trace the null check, fix it, run auth tests to verify.
Unknowns: Exact line of the failing null check, whether edge cases exist for empty strings.
---
{"tasks": [
  {"title": "Inspect auth.py login flow and locate the failing null check", "kind": "inspect", "completion_gate": "tool", "depends_on": []},
  {"title": "Fix the null check in validate_login()", "kind": "edit", "completion_gate": "tool", "depends_on": ["1"]},
  {"title": "Run auth test suite to verify the fix", "kind": "verify", "completion_gate": "verification", "depends_on": ["2"]}
]}

RULES:
- Do not call tools. Do not write code. Text + JSON only.
- Task titles MUST be SHORT (under 80 chars). One line each. Action labels, not descriptions.
- Task titles MUST be specific to the request, not generic templates like "Inspect X / Fix X / Verify X".
- Every task needs: title (string), kind (inspect|edit|verify|report|execute|ask), completion_gate (tool|verification|final|manual), depends_on (list of task ID strings).
- First task: depends_on []. Subsequent tasks: depends_on their prerequisites.
- "kind": "inspect" = read/search/find. "edit" = write/edit/execute. "verify" = test/validate. "report" = deliver final findings.
- "completion_gate": "verification" for verify tasks. "final" for report tasks. "tool" for inspect/edit/execute.
- Number tasks starting from "1".
- For fix/build/create work: ALWAYS include a verify step. Never suggest skipping verification.
- For simple info requests: 1-2 tasks may be enough. For complex work: plan the real steps needed.
- Keep JSON valid and parseable. No trailing commas.
- Do not invent concrete file paths or commands unless the user named them.
- Do NOT put code, file contents, or full paragraphs in titles. Titles are labels."""

WORKFLOW_ADOPTION_RE = re.compile(
    r"\b(?:adopt|learn|save|stage|use)\b.{0,80}\b(?:workflow|skill|style|process|method)\b",
    re.I | re.S,
)
WORKFLOW_APPROVAL_RE = re.compile(
    r"\b(?:approve|promote|activate|use)\b.{0,80}\b(?:workflow|skill)(?: candidate| learning)?\b",
    re.I | re.S,
)
# An UNAMBIGUOUS workflow-adoption signal: the literal word "workflow"/"workflow-candidate".
# WORKFLOW_ADOPTION_RE also fires on bare "use the same method/skill/style/process …",
# which is ordinary English — those must NOT hijack a turn unless a concrete source is
# also supplied. This regex distinguishes "adopt this workflow" from "use that method".
WORKFLOW_EXPLICIT_RE = re.compile(r"\bworkflow(?:[-\s]candidate)?\b", re.I)
URL_RE = re.compile(r"https?://[^\s)\]}>\"']+", re.I)
WORKFLOW_SOURCE_PATH_RE = re.compile(
    r"(?:from|file|path)\s+[`\"']?([^`\"'\s]+\.(?:md|txt|ya?ml|json))[`\"']?|[`\"']([^`\"']+\.(?:md|txt|ya?ml|json))[`\"']",
    re.I,
)


def _looks_like_term_lookup(text: str) -> bool:
    value = str(text or "").strip().lower()
    if not value:
        return False
    return bool(re.search(r"\b(what\s+(?:does|do|is)|define|meaning\s+of|remind\s+me)\b.{0,60}\b(mean|means|term|shorthand|definition|stand\s+for)\b", value))


_TRIVIAL_GREETINGS = frozenset({
    "hi", "hello", "hey", "yo", "hi mo", "hello mo", "hey mo",
    "thanks", "thank you", "ok", "okay", "yes", "no", "y", "n", "sup", "gm",
})


def _looks_like_trivial_greeting(text: str) -> bool:
    """True for bare greetings/acks where episodic recall + project-file reads add
    no value. Deliberately strict (exact match on a small set) so real work turns
    never lose memory recall or project context. Mirrors the greeting set that
    should_include_code_graph_context already skips on."""
    return str(text or "").strip().lower().strip("!.?") in _TRIVIAL_GREETINGS


def _looks_like_identity_question(text: str) -> bool:
    """Identity/profile questions need the operator profile in context.

    Without this gate such turns classify as simple chat, the profile bridge is
    skipped, and the model burns multiple provider round-trips re-reading
    profile files that could have been injected up front (observed live:
    "what do you know about me?" cost 4 round-trips / ~27s).
    """
    value = str(text or "").strip().lower()
    if not value:
        return False
    return bool(re.search(
        r"\b(who\s+am\s+i|about\s+me|know\s+(?:about\s+)?me|remember\s+(?:about\s+)?me"
        r"|my\s+(?:profile|name|preferences?|style|projects?)"
        r"|who\s+are\s+you|about\s+yourself|your\s+(?:identity|maker|creator|profile))\b",
        value,
    ))


def _truncate_recall(text: str, max_chars: int) -> str:
    """Truncate recalled memory text to max_chars, preserving word boundary.
    Same cap pattern as memory.py's 200-turn limit and handoff.py's MAX_HANDOFF_DOC_CHARS.
    """
    value = str(text or "").strip()
    if len(value) <= max_chars:
        return value
    if token_aware_truncation_enabled():
        return cap_by_tokens(value, max_chars, "\u2026").replace("\n\u2026", "\u2026")
    truncated = value[:max_chars].rsplit(" ", 1)[0]
    return f"{truncated}\u2026"


def _usage_tokens(usage: object) -> tuple[int, int, int]:
    """Normalize a provider usage object to (input, output, total) tokens.

    Handles both OpenAI chat-completions field names (prompt_tokens/
    completion_tokens) and OpenAI Responses/Codex field names (input_tokens/
    output_tokens). Without this, Codex turns record 0/0 and every end report
    shows no token info.
    """
    if not usage:
        return 0, 0, 0

    def _pick(*names: str) -> int:
        for name in names:
            if isinstance(usage, dict):
                val = usage.get(name)
            else:
                val = getattr(usage, name, None)
            if val:
                try:
                    return int(val)
                except (TypeError, ValueError):
                    continue
        return 0

    input_tokens = _pick("input_tokens", "prompt_tokens")
    output_tokens = _pick("output_tokens", "completion_tokens")
    total_tokens = _pick("total_tokens") or (input_tokens + output_tokens)
    return input_tokens, output_tokens, total_tokens


def _usage_cache_tokens(usage: object) -> tuple[int, int]:
    """Normalize a provider usage object to (cache_hit_tokens, cache_miss_tokens).

    Without this, MO records gross input tokens but never the provider-reported
    prefix-cache split, so the actual saving from the cache-stable payload
    (`Session.get_messages`) is unmeasurable. Covers the three families MO talks
    to: DeepSeek (``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``),
    OpenAI/Codex (``prompt_tokens_details.cached_tokens``), and Anthropic
    (``cache_read_input_tokens`` / ``cache_creation_input_tokens``). Returns
    ``(0, 0)`` when the provider reports no cache info so callers never branch.
    """
    if not usage:
        return 0, 0

    def _get(obj: object, name: str) -> object:
        if isinstance(obj, dict):
            return obj.get(name)
        return getattr(obj, name, None)

    def _int(val: object) -> int:
        try:
            return int(val) if val else 0
        except (TypeError, ValueError):
            return 0

    hit = _int(_get(usage, "prompt_cache_hit_tokens")) or _int(_get(usage, "cache_read_input_tokens"))
    if not hit:
        details = _get(usage, "prompt_tokens_details")
        if details is not None:
            hit = _int(_get(details, "cached_tokens"))
    miss = _int(_get(usage, "prompt_cache_miss_tokens")) or _int(_get(usage, "cache_creation_input_tokens"))
    return hit, miss


def _code_graph_age() -> float:
    """Return mtime of the code graph index file, or 0 if not found."""
    try:
        state_home = os.environ.get(ENV_MO_STATE_HOME, "").strip()
        cache_base = Path(state_home) if state_home else (mo_home() if private_state_enabled() else None)
        if cache_base is not None:
            graphs = list((cache_base / "cache" / "code_graph").glob("*/knowledge-graph.json"))
            return max((path.stat().st_mtime for path in graphs if path.exists()), default=0.0)
        graph_path = Path("memory/code_graph/knowledge-graph.json")
        if graph_path.exists():
            return graph_path.stat().st_mtime
    except Exception:
        traceback.print_exc()
    return 0.0
