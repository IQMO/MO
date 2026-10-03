"""MO agent utility helpers."""

import inspect
import re
import time
from typing import Any

from ..session.session import Session, restore_session_snapshot_fields
from ..tasking.task_board import TaskBoard, board_update_event
from ..utils.text_utils import cap_by_tokens, token_aware_truncation_enabled


class TurnCancelled(Exception):
    """Raised inside a turn when the UI requests a safe abort."""


_EXTRATHINK_RE = re.compile(r"\bextrathink\b", re.IGNORECASE)


def _is_extrathink(text: str) -> bool:
    """True when the user's message carries the inline ``extrathink`` trigger.

    Word-boundary + case-insensitive: fires on ``extrathink`` / ``EXTRATHINK`` but
    not on substrings like ``extrathinking``. The word is a turn modifier, not a
    command — the full message (word included) is still processed normally; it just
    arms the one-shot re-audit gate and the shine effect for this turn.
    """
    return bool(_EXTRATHINK_RE.search(str(text or "")))


_MAPTHIS_RE = re.compile(r"\bmapthis\b", re.IGNORECASE)


def _is_mapthis(text: str) -> bool:
    """True when the user's message carries the inline ``mapthis`` trigger.

    Like ``extrathink`` it is a word-boundary, case-insensitive turn keyword (not a
    slash command): ``mapthis`` / ``MAPTHIS`` but not ``mapthistoo``. It activates
    the native ``map_project`` tool for this normal turn, allowing MO to drive the
    map request through Gateway/taskboard/final gates instead of a pre-turn route.
    """
    return bool(_MAPTHIS_RE.search(str(text or "")))


# ── Inline turn triggers ──────────────────────────────────────────────────────
# A keyword the operator drops into any message to modify THIS turn (not a slash
# command). This table is the single source: each entry maps a per-turn flag to its
# detector, so a NEW trigger is one row here — not a third hand-wired copy scattered
# across run_turn. Each trigger's CONSUMER (the extrathink re-audit gate, the mapthis
# tool activation) reads its flag and stays where it belongs — the seam is consolidated,
# the diverse behavior is not forced together.
_INLINE_TRIGGERS: "tuple[tuple[str, Any], ...]" = (
    ("_extrathink_active", lambda agent, text: _is_extrathink(text)),
    ("_mapthis_active", lambda agent, text: _is_mapthis(text)),
)


def arm_inline_triggers(agent: Any, text: str) -> None:
    """Set every per-turn inline-trigger flag on the agent from the operator's message —
    one seam for detection + arming. Never raises; a failing detector arms False."""
    for flag, detect in _INLINE_TRIGGERS:
        try:
            setattr(agent, flag, bool(detect(agent, text)))
        except Exception:
            setattr(agent, flag, False)


def _emit_task_board_update(
    task_board: TaskBoard,
    *,
    update: str = "updated",
    on_board_event: object = None,
) -> str:
    """Publish one structured board event and return its renderer-neutral view."""
    rendered = task_board.render()
    if on_board_event:
        on_board_event(board_update_event(task_board, update=update, rendered=rendered))
    return rendered


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


def visible_worker_state(state: str) -> str:
    """Normalize a raw worker state into a display-friendly label.

    Shared by AgentStatusCommands._visible_worker_state and the native
    terminal startup summary so they stay in sync.
    """
    value = str(state or "").strip().lower()
    if value in {"offered", "accepted", "pending"}:
        return "queued"
    if value in {"active"}:
        return "running"
    if value in {"done"}:
        return "completed"
    if value in {"cancelled", "canceled"}:
        return "paused"
    return value if value in {"running", "queued", "blocked", "completed", "paused", "failed", "open"} else "running"


def load_session_from_manager(agent: Any, session_name: str, *, session_id_prefix: str, sanitize: bool = False) -> "Session":
    """Load a saved session from the agent's session manager and hydrate fields.

    Shared by the scheduler and Telegram gateway so both hydrate session data
    the same way. Callers pass a unique session_id_prefix and optionally request
    sanitize_for_provider() (Telegram does, scheduler does not).
    """
    active_session = getattr(agent, "session", None)
    max_history = int(
        getattr(active_session, "max_history", 0)
        or getattr(agent, "session_max_messages", 0)
        or 500
    )
    session = Session(
        str(getattr(agent, "system_message", "") or "You are MO."),
        max_history=max_history,
    )
    manager = getattr(agent, "_sessions", None)
    data = None
    if manager and hasattr(manager, "load"):
        try:
            data = manager.load(session_name)
        except Exception:
            data = None
    if isinstance(data, dict):
        restore_session_snapshot_fields(session, data)
        bind_loaded = getattr(manager, "bind_loaded_session", None)
        if callable(bind_loaded):
            bind_loaded(session, session_name, data)
        else:
            session._loaded_meta = (
                dict(data.get("meta") or {}) if isinstance(data.get("meta"), dict) else {}
            )
        if sanitize:
            try:
                session.sanitize_for_provider()
            except Exception:
                pass
    else:
        session.session_id = f"{session_id_prefix}-{int(time.time())}"
    return session


URL_RE = re.compile(r"https?://[^\s)\]}>\"']+", re.I)
WORKFLOW_SOURCE_PATH_RE = re.compile(
    r"(?:from|file|path)\s+[`\"']?([^`\"'\s]+\.(?:md|txt|ya?ml|json))[`\"']?|[`\"']([^`\"']+\.(?:md|txt|ya?ml|json))[`\"']",
    re.I,
)


def _truncate_recall(text: str, max_chars: int) -> str:
    """Bound injected recall text without limiting stored turns or current work."""
    value = str(text or "").strip()
    if len(value) <= max_chars:
        return value
    if token_aware_truncation_enabled():
        return cap_by_tokens(value, max_chars, "\u2026").replace("\n\u2026", "\u2026")
    truncated = value[:max_chars].rsplit(" ", 1)[0]
    return f"{truncated}\u2026"


def _usage_accounting(usage: object) -> tuple[int, int, int, int, int, int]:
    """Return canonical ``(input, output, total, hit, ordinary, write)`` tokens.

    OpenAI/Z.ai/DeepSeek report inclusive input totals, while Anthropic reports
    ordinary, cache-read, and cache-creation input as separate top-level fields.
    Cache writes are kept separate from ordinary uncached input in every shape.
    """
    if not usage:
        return 0, 0, 0, 0, 0, 0

    def _get(obj: object, name: str) -> object:
        return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)

    def _present(obj: object, name: str) -> bool:
        return name in obj if isinstance(obj, dict) else hasattr(obj, name)

    def _int(value: object) -> int:
        try:
            return max(0, int(value)) if value is not None else 0
        except (TypeError, ValueError):
            return 0

    def _first(obj: object, *names: str) -> tuple[bool, int]:
        for name in names:
            if _present(obj, name):
                return True, _int(_get(obj, name))
        return False, 0

    raw_input = _int(_get(usage, "input_tokens")) or _int(_get(usage, "prompt_tokens"))
    output = _int(_get(usage, "output_tokens")) or _int(_get(usage, "completion_tokens"))
    details = _get(usage, "input_tokens_details") or _get(usage, "prompt_tokens_details")

    hit_reported, hit = _first(usage, "prompt_cache_hit_tokens", "cache_read_input_tokens")
    if not hit_reported and details is not None:
        hit_reported, hit = _first(details, "cached_tokens")

    write_reported, write = _first(
        usage,
        "cache_write_tokens",
        "prompt_cache_write_tokens",
        "cache_write_input_tokens",
        "cache_creation_input_tokens",
    )
    if not write_reported and details is not None:
        write_reported, write = _first(details, "cache_write_tokens", "cache_creation_tokens")

    anthropic_shape = _present(usage, "cache_read_input_tokens") or _present(
        usage, "cache_creation_input_tokens"
    )
    if anthropic_shape:
        ordinary = raw_input
        input_tokens = raw_input + hit + write
    else:
        miss_reported, ordinary = _first(usage, "prompt_cache_miss_tokens")
        if not miss_reported and (hit_reported or write_reported):
            ordinary = max(0, raw_input - hit - write)
        input_tokens = raw_input or (hit + ordinary + write)

    reported_total = _int(_get(usage, "total_tokens"))
    total = max(reported_total, input_tokens + output)
    return input_tokens, output, total, hit, ordinary, write


def _usage_tokens(usage: object) -> tuple[int, int, int]:
    """Normalize provider usage to gross input, output, and total tokens."""
    return _usage_accounting(usage)[:3]


def _usage_cache_tokens(usage: object) -> tuple[int, int]:
    """Normalize provider usage to cache-read and ordinary uncached input."""
    normalized = _usage_accounting(usage)
    return normalized[3], normalized[4]


def _usage_cache_write_tokens(usage: object) -> int:
    """Normalize provider-reported prompt-cache write/creation tokens."""
    return _usage_accounting(usage)[5]
