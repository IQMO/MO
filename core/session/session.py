"""MO — Session and context management."""

import time
from typing import Any

from ..tooling.sandbox import redact_sensitive_text
from ..utils.text_safety import sanitize_jsonish, sanitize_unicode_text

DEFAULT_MAX_HISTORY = 500
INTERNAL_CONTINUATION_KEY = "_mo_internal_continuation"
PRESENTATION_KEY = "_mo_presentation"
RESPONSES_OUTPUT_ITEMS_KEY = "_mo_responses_output_items"
RESPONSES_MODEL_KEY = "_mo_responses_model"
RESPONSES_RESPONSE_ID_KEY = "_mo_responses_response_id"
RESPONSES_REASONING_TOKENS_KEY = "_mo_responses_reasoning_tokens"
RESPONSES_REASONING_CONTEXT_KEY = "_mo_responses_reasoning_context"

_INCOMPLETE_ASSISTANT_PREFIXES = (
    "Error:",
    "MO provider error:",
    "MO could not save the terminal recovery checkpoint,",
    "[PROVIDER EMPTY]",
    "[TOOL ARGUMENTS INVALID]",
    "[TOOL ARGUMENTS TRUNCATED]",
    "[REPEATED TOOL BATCH BLOCKED]",
    "[UNVERIFIED]",
    "I couldn't verify that result, so I can't confirm it.",
    "[REQUEST LIMIT REACHED]",
    "Provider requested a tool that was not offered for this request;",
    "Provider repeatedly produced malformed/truncated tool calls;",
    "Provider returned no visible answer after retry;",
)
SESSION_MOMENTUM_SUMMARY_PREFIX = (
    "[SESSION MOMENTUM COMPACTED COMPLETED TOOL CHAIN — orientation only, not proof]"
)


def is_runtime_owned_session_summary(message: Any) -> bool:
    """Return whether a stored row is compaction context, never assistant speech."""
    return (
        isinstance(message, dict)
        and str(message.get("content") or "").lstrip().startswith(
            SESSION_MOMENTUM_SUMMARY_PREFIX
        )
    )


def mark_last_assistant_internal(session: Any) -> bool:
    """Mark a session-like object's latest assistant message as internal."""
    messages = getattr(session, "messages", None)
    if not isinstance(messages, list) or not messages:
        return False
    message = messages[-1]
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return False
    message[INTERNAL_CONTINUATION_KEY] = True
    return True


def clear_internal_continuations(session: Any) -> int:
    """Remove provider-only controls from a real session or session adapter."""
    messages = getattr(session, "messages", None)
    if not isinstance(messages, list):
        return 0
    retained = [
        message
        for message in messages
        if not (
            isinstance(message, dict)
            and message.get(INTERNAL_CONTINUATION_KEY) is True
        )
    ]
    removed = len(messages) - len(retained)
    if removed:
        session.messages = retained
    return removed


def project_messages(
    messages: list[dict],
    *,
    include_reasoning_content: bool = False,
    include_responses_state: bool = False,
) -> list[dict]:
    """Project stored messages without changing evidence or persisted replay state."""
    excluded_keys = {INTERNAL_CONTINUATION_KEY, PRESENTATION_KEY}
    if not include_reasoning_content:
        excluded_keys.add("reasoning_content")
    if not include_responses_state:
        excluded_keys.update({
            RESPONSES_OUTPUT_ITEMS_KEY,
            RESPONSES_MODEL_KEY,
            RESPONSES_RESPONSE_ID_KEY,
            RESPONSES_REASONING_TOKENS_KEY,
            RESPONSES_REASONING_CONTEXT_KEY,
        })
    return [
        {k: v for k, v in message.items() if k not in excluded_keys}
        if isinstance(message, dict) else message
        for message in messages
    ]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def response_replay_metadata(
    items: list[dict] | None, model: str, response_id: str = "",
    *, usage: Any = None, reasoning_context: str = "",
) -> dict[str, Any]:
    """Keep native replay and its reported reasoning usage on the same row."""
    if not items or not model:
        return {}
    metadata = {
        RESPONSES_OUTPUT_ITEMS_KEY: sanitize_jsonish(items),
        RESPONSES_MODEL_KEY: sanitize_unicode_text(model),
    }
    if response_id:
        metadata[RESPONSES_RESPONSE_ID_KEY] = sanitize_unicode_text(response_id)
    details = (usage.get("output_tokens_details") if isinstance(usage, dict)
               else getattr(usage, "output_tokens_details", None))
    tokens = (details.get("reasoning_tokens") if isinstance(details, dict)
              else getattr(details, "reasoning_tokens", None))
    if tokens is not None:
        metadata[RESPONSES_REASONING_TOKENS_KEY] = max(0, _safe_int(tokens))
    if reasoning_context in {"current_turn", "all_turns"}:
        metadata[RESPONSES_REASONING_CONTEXT_KEY] = reasoning_context
    return metadata


def replayed_reasoning_tokens(messages: list[dict], model: str) -> int:
    """Count measured, compatible replay; never tokenize encrypted transport."""
    compatible = [message for message in messages
                  if message.get(RESPONSES_MODEL_KEY) == model
                  and message.get(RESPONSES_OUTPUT_ITEMS_KEY)]
    mode = next((message[RESPONSES_REASONING_CONTEXT_KEY] for message in reversed(compatible)
                 if message.get(RESPONSES_REASONING_CONTEXT_KEY)), "current_turn")
    if mode != "all_turns":
        start = next((i + 1 for i in range(len(messages) - 1, -1, -1)
                      if messages[i].get("role") == "user"), 0)
        compatible = [message for message in messages[start:]
                      if message.get(RESPONSES_MODEL_KEY) == model]
    return sum(max(0, _safe_int(message.get(RESPONSES_REASONING_TOKENS_KEY)))
               for message in compatible
               if any(isinstance(item, dict) and item.get("type") == "reasoning"
                      for item in message.get(RESPONSES_OUTPUT_ITEMS_KEY, [])))


def _bounded_skill_sources(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    sources: list[str] = []
    for raw in value[:8]:
        source = str(raw or "").strip()[:1024]
        if source and source.replace("\\", "/").casefold().endswith("/skill.md"):
            sources.append(source)
    return tuple(dict.fromkeys(sources))


def _bounded_skill_names(value: Any) -> tuple[str, ...]:
    """Keep a small display-only receipt of skills delivered to one turn."""
    if not isinstance(value, (list, tuple)):
        return ()
    names: list[str] = []
    for raw in value[:8]:
        name = " ".join(sanitize_unicode_text(str(raw or "")).split())[:120]
        if name:
            names.append(name)
    return tuple(dict.fromkeys(names))


def restore_session_snapshot_fields(
    session: Any,
    data: dict[str, Any],
    *,
    messages: list[Any] | None = None,
) -> None:
    """Restore fields shared by every persisted conversation surface."""
    session.session_id = data.get("session_id", session.session_id)
    restored_messages = list(
        (data.get("messages", []) if messages is None else messages) or []
    )
    session.messages = [
        {**message, "role": "system"}
        if is_runtime_owned_session_summary(message)
        else message
        for message in restored_messages
    ]
    for field in (
        "turn_count",
        "total_tokens",
        "output_tokens",
        "input_tokens",
        "cache_hit_tokens",
        "cache_miss_tokens",
        "cache_write_tokens",
        "compacted_messages_count",
    ):
        setattr(session, field, _safe_int(data.get(field)))
    session.token_log = list(data.get("token_log", []) or [])
    session.last_compacted_at = _safe_float(data.get("last_compacted_at"))
    restored_created = _safe_float(data.get("created_at"))
    if restored_created > 0:
        session.created_at = restored_created
    session._pending_learning_skill_sources = _bounded_skill_sources(
        data.get("pending_learning_skill_sources")
    )
    session._turn_selected_learning_skill_sources = _bounded_skill_sources(
        data.get("turn_selected_learning_skill_sources")
    )
    session._turn_selected_skill_names = _bounded_skill_names(
        data.get("turn_selected_skill_names")
    )
    session._turn_selected_skill_turn_count = max(
        0, _safe_int(data.get("turn_selected_skill_turn_count"))
    )
    session._pending_skill_import_context = sanitize_unicode_text(
        str(data.get("pending_skill_import_context") or "")[:2400]
    )


def assistant_result_is_provider_or_runtime_error(result: object) -> bool:
    """Return whether a visible Agent result is a provider/runtime failure."""
    text = str(result or "").strip().casefold()
    return text.startswith(("mo provider error:", "provider error:", "error:"))


def assistant_result_is_incomplete(result: object) -> bool:
    """Classify current visible Agent failure markers in one shared owner."""
    text = str(result or "").strip()
    return any(text.startswith(prefix) for prefix in _INCOMPLETE_ASSISTANT_PREFIXES)


def assistant_result_is_request_limit(result: object) -> bool:
    """Identify the runtime stop that must never trigger automatic retries."""
    return str(result or "").strip().startswith("[REQUEST LIMIT REACHED]")


class Session:
    """Manages conversation history and context window."""

    def __init__(self, system_message: str, max_history: int = DEFAULT_MAX_HISTORY):
        self.system_message = system_message
        self.max_history = max_history
        self.messages: list[dict] = []
        self.session_id = f"mo-{int(time.time())}"
        self.created_at = time.time()
        self.turn_count = 0
        self.total_tokens = 0
        self.output_tokens = 0
        # Running input + cache totals so MO can MEASURE prefix-cache effectiveness
        # instead of estimating it. cache_hit_tokens / input_tokens is the real
        # provider-reported cache-hit ratio (DeepSeek/OpenAI/Anthropic usage).
        self.input_tokens = 0
        self.cache_hit_tokens = 0
        self.cache_miss_tokens = 0
        self.cache_write_tokens = 0
        self.token_log: list[dict[str, Any]] = []
        self.trimmed_messages_count = 0
        self.last_trimmed_at = 0.0
        self.compacted_messages_count = 0
        self.last_compacted_at = 0.0
        self._pending_learning_skill_sources: tuple[str, ...] = ()
        self._turn_selected_learning_skill_sources: tuple[str, ...] = ()
        self._turn_selected_skill_names: tuple[str, ...] = ()
        self._turn_selected_skill_turn_count = 0
        self._pending_skill_import_context = ""
        # Mail content is available to the active provider turn but never to a
        # plaintext checkpoint or later replay. The serving Agent closes it at
        # turn end, after the final reply has been delivered to the surface.
        self._mail_sensitive_turn = False
        self._mail_data_loaded = False

    @staticmethod
    def _mail_placeholder(message: dict) -> dict:
        role = str(message.get("role") or "")
        if role == "tool":
            return {"role": "tool", "tool_call_id": str(message.get("tool_call_id") or ""),
                    "content": "[Mail result omitted from saved conversation]"}
        if role == "assistant" and message.get("tool_calls"):
            calls = []
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                function = call.get("function") or {}
                calls.append({"id": str(call.get("id") or ""), "type": "function",
                              "function": {"name": str(function.get("name") or ""), "arguments": "{}"}})
            return {"role": "assistant", "content": "[Mail action omitted from saved conversation]",
                    "tool_calls": calls}
        return {"role": role, "content": "[Mail turn omitted from saved conversation]"}

    def mail_safe_messages(self, messages: list[dict] | None = None) -> list[dict]:
        rows = list(self.messages if messages is None else messages)
        if not self._mail_sensitive_turn:
            return rows
        start = next((i for i in range(len(rows) - 1, -1, -1)
                      if isinstance(rows[i], dict) and rows[i].get("role") == "user"), 0)
        return rows[:start] + [self._mail_placeholder(row) if isinstance(row, dict) else {}
                               for row in rows[start:]]

    def finish_mail_sensitive_turn(self) -> None:
        if self._mail_sensitive_turn:
            self.messages = self.mail_safe_messages()
            self._mail_sensitive_turn = False
        self._mail_data_loaded = False

    def add_user(self, content: str):
        self.messages.append({"role": "user", "content": sanitize_unicode_text(content)})
        self._trim()

    def mark_reply_suppressed(self) -> bool:
        """Close the current user turn without inventing assistant speech."""
        if not self.messages or self.messages[-1].get("role") != "user":
            return False
        message = self.messages[-1]
        presentation = dict(message.get(PRESENTATION_KEY) or {})
        presentation["reply_suppressed"] = True
        message[PRESENTATION_KEY] = presentation
        return True

    def add_assistant(
        self,
        content: str,
        reasoning_content: str | None = None,
        *,
        response_items: list[dict[str, Any]] | None = None,
        response_model: str = "",
        response_id: str = "",
        response_usage: Any = None,
        reasoning_context: str = "",
    ):
        content = str(content or "").strip()
        reasoning = str(reasoning_content or "").strip()
        # Never store assistant messages with no visible text and no reasoning.
        # Tool-call messages (with tool_calls) are added via add_message, not here.
        if not content and not reasoning:
            return
        msg: dict = {"role": "assistant", "content": sanitize_unicode_text(content)}
        if reasoning:
            msg["reasoning_content"] = sanitize_unicode_text(reasoning)
        msg.update(response_replay_metadata(
            response_items, response_model, response_id,
            usage=response_usage, reasoning_context=reasoning_context,
        ))
        self.messages.append(msg)
        self._trim()

    def mark_last_assistant_internal(self) -> bool:
        """Mark the latest assistant message as provider-only continuation control.

        Final-answer gates append an assistant-role instruction so the next
        provider request can correct the candidate response.  That instruction
        is not conversation: persistence and later turns must never replay it.
        """
        return mark_last_assistant_internal(self)

    def clear_internal_continuations(self) -> int:
        """Remove provider-only continuation controls after they are consumed."""
        return clear_internal_continuations(self)

    def latest_visible_assistant_text(self) -> str:
        return next((str(message.get("content") or "") for message in reversed(self.messages)
                     if message.get("role") == "assistant" and not message.get(INTERNAL_CONTINUATION_KEY)), "")

    def add_tool_result(self, tool_call_id: str, content: str,
                        image_data_uris: list[str] | None = None):
        safe_content = redact_sensitive_text(sanitize_unicode_text(content))
        uris = [u for u in (image_data_uris or []) if u]
        if uris:
            # Computer-use vision: carry the image(s) as content part(s) so a
            # vision-capable provider can SEE them. Text part stays for non-vision
            # providers / history readability.
            parts: list[dict] = []
            if safe_content:
                parts.append({"type": "text", "text": safe_content})
            for uri in uris:
                parts.append({"type": "image", "image_url": uri})
            self.messages.append({
                "role": "tool",
                "tool_call_id": sanitize_unicode_text(tool_call_id),
                "content": parts,
            })
            self._trim()
            return
        self.messages.append({
            "role": "tool",
            "tool_call_id": sanitize_unicode_text(tool_call_id),
            "content": safe_content,
        })
        self._trim()

    def add_message(self, msg: dict):
        self.messages.append(sanitize_jsonish(msg))
        self._trim()

    def omit_image_payloads(self, *, reason: str = "stale") -> dict[str, Any]:
        """Replace stored image bytes with a text marker after provider use.

        Computer-use screenshots must be available to the next vision provider
        request, but once a provider has responded the raw data URI becomes stale
        history. Keeping it would resend the same image on every later tool loop
        round and distort context-pressure accounting.
        """
        changed = False
        omitted_images = 0
        touched_messages = 0
        next_messages: list[dict] = []
        for msg in self.messages:
            if not isinstance(msg, dict):
                next_messages.append(msg)
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                next_messages.append(msg)
                continue
            text_parts: list[str] = []
            image_count = 0
            for part in content:
                if not isinstance(part, dict):
                    text = str(part or "").strip()
                    if text:
                        text_parts.append(text)
                    continue
                ptype = str(part.get("type") or "").strip().lower()
                if ptype in {"image", "image_url", "input_image"}:
                    image_count += 1
                    continue
                if ptype in {"text", "input_text", "output_text"}:
                    text = str(part.get("text") or "").strip()
                    if text:
                        text_parts.append(text)
                    continue
                text = str(part.get("text") or "").strip()
                if text:
                    text_parts.append(text)
            if not image_count:
                next_messages.append(msg)
                continue
            touched_messages += 1
            omitted_images += image_count
            suffix_reason = str(reason or "stale").strip() or "stale"
            marker = (
                f"[{image_count} {suffix_reason} image omitted; "
                "use computer_observe kind=screen for current screen.]"
            )
            text_parts.append(marker)
            replacement = dict(msg)
            replacement["content"] = sanitize_unicode_text("\n".join(text_parts).strip())
            next_messages.append(replacement)
            changed = True
        if changed:
            self.messages = next_messages
        return {
            "changed": changed,
            "omitted_images": omitted_images,
            "messages": touched_messages,
        }

    def record_usage(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int | None = None,
        cache_hit_tokens: int = 0,
        cache_miss_tokens: int = 0,
        cache_write_tokens: int = 0,
    ):
        total = int(total_tokens if total_tokens is not None else int(input_tokens or 0) + int(output_tokens or 0))
        entry = {
            "ts": time.time(),
            "provider": str(provider or ""),
            "model": str(model or ""),
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "total_tokens": total,
            "cache_hit_tokens": int(cache_hit_tokens or 0),
            "cache_miss_tokens": int(cache_miss_tokens or 0),
            "cache_write_tokens": int(cache_write_tokens or 0),
            "source": "provider_usage",
        }
        self.token_log.append(entry)
        self.total_tokens += total
        self.output_tokens += int(output_tokens or 0)
        self.input_tokens += int(input_tokens or 0)
        self.cache_hit_tokens += int(cache_hit_tokens or 0)
        self.cache_miss_tokens += int(cache_miss_tokens or 0)
        self.cache_write_tokens += int(cache_write_tokens or 0)
        return entry

    def get_messages(
        self,
        extra_context: str | None = None,
        *,
        include_reasoning_content: bool = False,
        include_responses_state: bool = False,
    ) -> list[dict]:
        """Build the provider payload with a cache-stable prefix.

        The static system prompt and stored history must stay byte-identical
        across provider calls so OpenAI-compatible automatic prefix caching can
        reuse them. Per-turn dynamic context from the context bridge
        therefore goes into a separate system message **appended at the very
        end** of the payload — never merged into the leading system message.

        Trailing placement (rather than inserting before the latest user
        message) keeps the ENTIRE stored history — including the most recent
        user turn and any in-progress tool chain — inside the cacheable prefix.
        Mid-stream insertion forced the provider's prefix cache to break at the
        injection point, re-billing the prior user+assistant exchange every
        turn and the whole tool chain on every round of a tool loop. Responses
        providers receive trailing system messages as input items, preserving
        the same stable-prefix property. Their private encrypted output state
        is included only for the Responses owner; chat-completions providers
        must never receive MO's internal replay metadata.
        """
        # Drop stored chain-of-thought from most provider payloads: prior-turn
        # `reasoning_content` is re-billed as input on every call (and some
        # providers reject the non-standard key). Providers declaring the
        # reasoning-content compatibility requirement are the exception: their
        # API requires that field to be replayed with the assistant turn.
        history = project_messages(
            self.messages,
            include_reasoning_content=include_reasoning_content,
            include_responses_state=include_responses_state,
        )
        payload = [{"role": "system", "content": self.system_message}] + history
        if extra_context:
            payload.append({"role": "system", "content": extra_context})
        return payload

    @staticmethod
    def close_unanswered_user_tail(messages: list[dict]) -> tuple[list[dict], dict[str, Any]]:
        """Keep unanswered requests verbatim as interrupted, inactive history."""
        original = list(messages or [])
        if not original:
            return original, {"changed": False, "dropped_messages": 0}
        i = len(original) - 1
        while (
            i >= 0 and isinstance(original[i], dict) and original[i].get("role") == "user"
            and not original[i].get(PRESENTATION_KEY, {}).get("attachments")
            and not original[i].get(PRESENTATION_KEY, {}).get("reply_suppressed")
        ):
            i -= 1
        start = i + 1
        if start >= len(original):
            return original, {"changed": False, "dropped_messages": 0}
        unanswered = original[start:]
        first_user = ""
        for msg in unanswered:
            if isinstance(msg, dict) and msg.get("role") == "user":
                first_user = str(msg.get("content") or "")
                break
        original.append({
            "role": "assistant",
            "content": "[Turn interrupted before an answer was recorded. The unanswered requests above are retained history, not authorization to resume. Follow the current user request and verify live state before continuing.]",
        })
        return original, {
            "changed": True,
            "dropped_messages": 0,
            "reason": "unanswered_user_turn",
            "user": first_user[:500],
            "preserved_user": True,
        }

    @staticmethod
    def close_unfinished_tool_tail(messages: list[dict]) -> tuple[list[dict], dict[str, Any]]:
        """Close interrupted tool work without erasing actions already performed.

        A missing final answer does not invalidate tool results. Retain them and
        explicitly mark missing results as unknown, so the next request can
        inspect history without implicitly resuming the interrupted work.
        """
        original = list(messages or [])
        if not original:
            return original, {"changed": False, "dropped_messages": 0}

        i = len(original) - 1
        saw_tool_chain = False
        while i >= 0:
            msg = original[i] if isinstance(original[i], dict) else {}
            role = msg.get("role")
            if role == "tool":
                saw_tool_chain = True
                i -= 1
                continue
            if role == "assistant" and msg.get("tool_calls"):
                saw_tool_chain = True
                i -= 1
                continue
            break

        if not saw_tool_chain:
            return original, {"changed": False, "dropped_messages": 0}

        start = i + 1
        if i >= 0 and isinstance(original[i], dict) and original[i].get("role") == "user":
            start = i
        tail = original[start:]
        first_user = ""
        for msg in tail:
            if isinstance(msg, dict) and msg.get("role") == "user":
                first_user = str(msg.get("content") or "")
                break
        closed = original[:start]
        retained_results = 0
        missing_results = 0
        dropped = 0
        index = 0
        while index < len(tail):
            message = tail[index]
            calls = message.get("tool_calls") if message.get("role") == "assistant" else None
            if calls:
                closed.append(message)
                index += 1
                results = {}
                while index < len(tail) and tail[index].get("role") == "tool":
                    result = tail[index]
                    results[result.get("tool_call_id")] = result
                    index += 1
                for call in calls:
                    call_id = call.get("id")
                    if call_id in results:
                        closed.append(results.pop(call_id))
                        retained_results += 1
                    else:
                        closed.append({
                            "role": "tool", "tool_call_id": call_id,
                            "content": "[Interrupted tool call: no result was recorded. Execution and external changes are unknown. Inspect current state before retrying; do not assume success or no changes.]",
                        })
                        missing_results += 1
                dropped += len(results)
                continue
            if message.get("role") == "tool":
                dropped += 1
            else:
                closed.append(message)
            index += 1
        closed.append({
            "role": "assistant",
            "content": "[Turn interrupted before a final answer. The tool calls and results above are retained historical evidence: some actions may already have completed or partially changed external state. This is not a completion claim or authorization to resume. Follow the current user request and verify live state before reporting completion or repeating actions.]",
        })
        return closed, {
            "changed": True,
            "dropped_messages": dropped,
            "reason": "unfinished_tool_turn",
            "user": first_user[:500],
            "preserved_user": bool(first_user),
            "retained_tool_results": retained_results,
            "missing_tool_results": missing_results,
        }

    def quarantine_unfinished_tail(
        self,
        *,
        close_unanswered_user: bool = True,
    ) -> dict[str, Any]:
        """Close interrupted tool work before accepting a fresh turn.

        Requests and tool results remain historical evidence. The caller leaves
        plain unanswered requests open during active continuation; otherwise an
        interruption marker closes them without losing their instructions.
        """
        original = list(self.messages)
        cleaned, meta = self.close_unfinished_tool_tail(original)
        if not meta.get("changed") and close_unanswered_user:
            cleaned, meta = self.close_unanswered_user_tail(self.messages)
        if meta.get("changed"):
            self.messages = cleaned[-self.max_history:]
            # Quarantining an unfinished tail is semantic cleanup, not history
            # retention pressure.  Counting it as a trim made an interrupted
            # turn trigger an automatic context handoff at ~0% provider pressure,
            # exactly while the operator was trying to resume it. Requests stay
            # in history, so their turn count also stays unchanged.
        return meta

    def sanitize_for_provider(self, max_chars: int | None = None) -> dict[str, Any]:
        """Remove stale controls/orphans while retaining completed tool rounds.

        If max_chars is given, additionally trim messages from the top (after system) until
        the total character count is under the limit, keeping the most recent context.
        """
        import json as _json

        original = list(self.messages)
        cleaned: list[dict] = []
        dropped = 0

        i = 0
        while i < len(original):
            msg = original[i]
            if msg.get(INTERNAL_CONTINUATION_KEY) is True:
                dropped += 1
                i += 1
                continue
            role = msg.get("role")

            if role == "tool":
                dropped += 1
                i += 1
                continue

            tool_calls = msg.get("tool_calls") if role == "assistant" else None
            if role == "assistant" and tool_calls:
                end = i + 1
                while end < len(original) and original[end].get("role") == "tool":
                    end += 1
                result_ids = {result.get("tool_call_id") for result in original[i + 1:end]}
                tool_round = original[i:end]
                if all(call.get("id") in result_ids for call in tool_calls):
                    # A user steer can follow the results directly. Its absence
                    # of an intervening assistant answer does not erase evidence.
                    cleaned.extend(tool_round)
                else:
                    repaired, meta = self.close_unfinished_tool_tail(tool_round)
                    cleaned.extend(repaired)
                    dropped += int(meta.get("dropped_messages") or 0)
                i = end
                continue

            cleaned.append(msg)
            i += 1

        # Strip leading orphan messages that have no preceding user context.
        # This prevents previous-session assistant answers from leaking into
        # a new session's message list ahead of the first real user prompt.
        while cleaned and cleaned[0].get("role") == "assistant" and not cleaned[0].get("tool_calls"):
            cleaned.pop(0)
            dropped += 1

        self.messages = cleaned
        # Semantic cleanup is not retention pressure. The existing retention
        # owner counts only messages actually lost to the history ceiling.
        self._trim()
        dropped += len(cleaned) - len(self.messages)

        # Character-based trim: drop oldest messages until under max_chars
        if max_chars:
            char_trimmed = 0
            while len(self.messages) > 1:
                total = sum(len(_json.dumps(m, default=str)) for m in self.messages)
                if total <= max_chars:
                    break
                dropped_msg = self.messages.pop(0)
                char_trimmed += 1
                if dropped_msg.get("tool_calls"):
                    while self.messages and self.messages[0].get("role") == "tool":
                        self.messages.pop(0)
                        char_trimmed += 1
            if char_trimmed:
                dropped += char_trimmed
                self.trimmed_messages_count += char_trimmed
                self.last_trimmed_at = time.time()

        return {
            "changed": self.messages != original,
            "dropped_messages": dropped,
        }

    def _trim(self):
        if len(self.messages) > self.max_history:
            before = len(self.messages)
            # Find the last user message so we don't trim it away
            last_user_idx = -1
            for i in range(before - 1, -1, -1):
                if self.messages[i].get("role") == "user":
                    last_user_idx = i
                    break
            keep_from = before - self.max_history
            if last_user_idx >= 0 and last_user_idx < keep_from:
                # Preserve at least one user message as a context boundary
                keep_from = last_user_idx
            self.messages = self.messages[keep_from:]
            dropped = before - len(self.messages)
            # Drop orphaned leading tool messages
            while self.messages and self.messages[0].get("role") == "tool":
                self.messages.pop(0)
                dropped += 1
            if dropped:
                self.trimmed_messages_count += dropped
                self.last_trimmed_at = time.time()

    def clear(self):
        self.messages = []
        self.created_at = time.time()
        self.turn_count = 0
        self.total_tokens = 0
        self.output_tokens = 0
        self.input_tokens = 0
        self.cache_hit_tokens = 0
        self.cache_miss_tokens = 0
        self.cache_write_tokens = 0
        self.token_log = []
        self.trimmed_messages_count = 0
        self.last_trimmed_at = 0.0
        self.compacted_messages_count = 0
        self.last_compacted_at = 0.0
        self._pending_learning_skill_sources = ()
        self._turn_selected_learning_skill_sources = ()
        self._turn_selected_skill_names = ()
        self._turn_selected_skill_turn_count = 0
        self._pending_skill_import_context = ""
