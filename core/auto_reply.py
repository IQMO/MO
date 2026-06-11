"""Deterministic low-risk auto replies for remote surfaces.

This is intentionally tiny. It handles status/heartbeat/help/ping style messages
without a provider call and refuses to answer task-shaped requests. Work still
flows through Gateway/Agent and taskboard truth.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
import traceback


_TASK_WORD_RE = re.compile(
    r"\b(build|fix|write|edit|create|implement|review|audit|inspect|investigate|run|test|deploy|change|update|search|find)\b",
    re.I,
)


@dataclass(frozen=True)
class AutoReply:
    text: str
    reason: str
    final: bool = True


def maybe_auto_reply(
    text: str,
    *,
    agent: Any = None,
    gateway: Any = None,
    surface: str = "telegram",
) -> AutoReply | None:
    """Return a deterministic reply for tiny control/status messages only."""
    raw = str(text or "").strip()
    if not raw:
        return None
    normalized = " ".join(raw.lower().split())
    plain = normalized.strip(" ?!.")
    command = normalized.split(maxsplit=1)[0] if normalized.startswith("/") else normalized

    # Never intercept obvious work. A user can still ask "/status then fix X";
    # that should go through the normal agent path rather than losing the task.
    if _TASK_WORD_RE.search(raw) and normalized not in {"/status", "status", "/heartbeat", "heartbeat"}:
        return None

    if command in {"/help", "help", "?"}:
        return AutoReply(_help_text(surface), "help")

    if normalized in {"ping", "/ping", "are you alive", "you alive", "online?", "online"}:
        return AutoReply("MO is online. Send a task, or send /heartbeat for environment status.", "ping")

    if _identity_or_model_question(normalized):
        return AutoReply(_identity_text(agent), "identity")

    if normalized in {"heartbeat", "/heartbeat", "hb", "/hb"}:
        return AutoReply(_heartbeat_status(agent, gateway), "heartbeat")

    if normalized in {"status", "/status"}:
        return AutoReply(_status_text(agent, gateway), "status")

    if plain in {"hi", "hello", "hey", "yo", "hi mo", "hello mo", "hey mo", "how are you", "how are you mo", "you there"}:
        return AutoReply("MO is online and ready. Send the task when ready, or /help for remote commands.", "greeting")

    return None


def _identity_or_model_question(normalized: str) -> bool:
    # Identity phrases must be the tail of the message, so "what are you going
    # to do" / "who are you deploying for" fall through to the agent instead of
    # being hijacked by the canned identity reply.
    if re.search(r"\b(who are you|what are you|what is mo|who made you|who created you)\s*[?.!]*\s*$", normalized):
        return True
    return bool(re.search(r"\b(what model|which model|current model|your model|model are you using)\b", normalized))


def _identity_text(agent: Any) -> str:
    provider = str(getattr(agent, "provider_name", "") or "provider") if agent is not None else "provider"
    model = str(getattr(agent, "model", "") or "model") if agent is not None else "model"
    return (
        f"I'm MO — a local-first coding agent by IQMO. I'm flying around `{provider}/{model}` right now; "
        "that model is my runtime engine, not my identity."
    )


def _heartbeat_status(agent: Any, gateway: Any) -> str:
    try:
        from .heartbeat import render_heartbeat_status
        return render_heartbeat_status(agent, gateway=gateway)
    except Exception:
        return "Heartbeat: online (status snapshot unavailable)."


def _status_text(agent: Any, gateway: Any) -> str:
    if agent is not None and hasattr(agent, "process_slash_command"):
        try:
            result = agent.process_slash_command("/status")
            if result:
                return str(result)
        except Exception:
            traceback.print_exc()
    return _heartbeat_status(agent, gateway)


def _help_text(surface: str) -> str:
    name = str(surface or "remote").strip() or "remote"
    return (
        f"MO {name} commands:\n"
        "- /status — compact runtime status\n"
        "- /heartbeat — environment/session heartbeat\n"
        "- /stop or cancel — request cancellation for active work\n"
        "- Send normal text to run MO through the regular task pipeline."
    )
