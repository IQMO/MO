"""MO — Session and context management."""

import time
from typing import Any

from ..sandbox import redact_sensitive_text
from ..text_safety import sanitize_jsonish, sanitize_unicode_text


def _session_ended_clean(messages: list[dict]) -> bool:
    """Check if the last assistant message was a completion, not a blocker/error."""
    if not messages:
        return False
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and not msg.get("tool_calls"):
            content = str(msg.get("content", "")).strip()
            if not content:
                continue
            # Blocked/incomplete patterns
            blocked = (
                "stopped before changing files",
                "[TOOL ARGUMENTS TRUNCATED]",
                "[PROVIDER EMPTY]",
                "[MAX TOOL ROUNDS]",
                "[MAX PROVIDER REQUESTS]",
                "MO provider error:",
            )
            if any(content.startswith(p) for p in blocked):
                return False
            return True
    return False


class Session:
    """Manages conversation history and context window."""

    def __init__(self, system_message: str, max_history: int = 50):
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
        self.token_log: list[dict[str, Any]] = []
        self.trimmed_messages_count = 0
        self.last_trimmed_at = 0.0
        self.compacted_messages_count = 0
        self.last_compacted_at = 0.0

    def add_user(self, content: str):
        self.messages.append({"role": "user", "content": sanitize_unicode_text(content)})
        self._trim()

    def add_assistant(self, content: str, reasoning_content: str | None = None):
        content = str(content or "").strip()
        reasoning = str(reasoning_content or "").strip()
        # Never store assistant messages with no visible text and no reasoning.
        # Tool-call messages (with tool_calls) are added via add_message, not here.
        if not content and not reasoning:
            return
        msg: dict = {"role": "assistant", "content": sanitize_unicode_text(content)}
        if reasoning:
            msg["reasoning_content"] = sanitize_unicode_text(reasoning)
        self.messages.append(msg)
        self._trim()

    def add_tool_result(self, tool_call_id: str, content: str, image_data_uri: str | None = None):
        safe_content = redact_sensitive_text(sanitize_unicode_text(content))
        if image_data_uri:
            # Computer-use vision: carry the screenshot as an image content part so
            # a vision-capable provider can SEE it. Text part stays for non-vision
            # providers / history readability.
            parts: list[dict] = []
            if safe_content:
                parts.append({"type": "text", "text": safe_content})
            parts.append({"type": "image", "image_url": image_data_uri})
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

    def record_usage(self, *, provider: str, model: str, input_tokens: int, output_tokens: int,
                     total_tokens: int | None = None, cache_hit_tokens: int = 0, cache_miss_tokens: int = 0):
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
            "source": "provider_usage",
        }
        self.token_log.append(entry)
        self.total_tokens += total
        self.output_tokens += int(output_tokens or 0)
        self.input_tokens += int(input_tokens or 0)
        self.cache_hit_tokens += int(cache_hit_tokens or 0)
        self.cache_miss_tokens += int(cache_miss_tokens or 0)
        return entry

    def get_messages(self, extra_context: str | None = None, *, consume_handoff: bool = True) -> list[dict]:
        """Build the provider payload with a cache-stable prefix.

        The static system prompt and stored history must stay byte-identical
        across provider calls so OpenAI-compatible automatic prefix caching can
        reuse them. Per-turn dynamic context (handoff seed + context bridge)
        therefore goes into a separate system message **appended at the very
        end** of the payload — never merged into the leading system message.

        Trailing placement (rather than inserting before the latest user
        message) keeps the ENTIRE stored history — including the most recent
        user turn and any in-progress tool chain — inside the cacheable prefix.
        Mid-stream insertion forced the provider's prefix cache to break at the
        injection point, re-billing the prior user+assistant exchange every
        turn and the whole tool chain on every round of a tool loop. The codex
        Responses path folds all system messages into ``instructions``
        regardless of position, so this is purely a chat-completions cache win
        with no behavior change there.
        """
        dynamic_parts: list[str] = []
        handoff = getattr(self, "_handoff_context", "")
        if handoff:
            dynamic_parts.append(handoff)
            if consume_handoff:
                self._handoff_context = ""  # single-use: consume only for the real provider call
        if extra_context:
            dynamic_parts.append(extra_context)
        # Drop stored chain-of-thought from the provider payload: prior-turn
        # `reasoning_content` is re-billed as input on every call (and some
        # providers reject the non-standard key). It stays in self.messages for
        # local display/persistence; stripping here is deterministic so the
        # cacheable prefix remains byte-stable across turns.
        history = [
            {k: v for k, v in m.items() if k != "reasoning_content"}
            if isinstance(m, dict) and "reasoning_content" in m else m
            for m in self.messages
        ]
        payload = [{"role": "system", "content": self.system_message}] + history
        if dynamic_parts:
            payload.append({"role": "system", "content": "\n\n".join(dynamic_parts)})
        return payload

    @staticmethod
    def strip_unanswered_user_tail(messages: list[dict]) -> tuple[list[dict], dict[str, Any]]:
        """Remove terminal user message(s) that never received an assistant answer."""
        original = list(messages or [])
        if not original:
            return original, {"changed": False, "dropped_messages": 0}
        i = len(original) - 1
        while i >= 0 and isinstance(original[i], dict) and original[i].get("role") == "user":
            i -= 1
        start = i + 1
        if start >= len(original):
            return original, {"changed": False, "dropped_messages": 0}
        removed = original[start:]
        first_user = ""
        for msg in removed:
            if isinstance(msg, dict) and msg.get("role") == "user":
                first_user = str(msg.get("content") or "")
                break
        return original[:start], {
            "changed": True,
            "dropped_messages": len(removed),
            "reason": "unanswered_user_turn",
            "user": first_user[:500],
        }

    @staticmethod
    def strip_unfinished_tool_tail(messages: list[dict]) -> tuple[list[dict], dict[str, Any]]:
        """Remove a terminal user/tool-call turn that never produced a final answer.

        Providers reject or loop on dangling assistant tool_calls.  Keeping the
        preceding user request is also unsafe: the next casual input (for example
        "hi") can accidentally resume stale build work.  This only touches the
        terminal tail; completed earlier tool chains are left intact.
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
        removed = original[start:]
        first_user = ""
        for msg in removed:
            if isinstance(msg, dict) and msg.get("role") == "user":
                first_user = str(msg.get("content") or "")
                break
        return original[:start], {
            "changed": True,
            "dropped_messages": len(removed),
            "reason": "unfinished_tool_turn",
            "user": first_user[:500],
        }

    def quarantine_unfinished_tail(self, *, drop_unanswered_user: bool = True) -> dict[str, Any]:
        """Drop unfinished terminal user/tool work before accepting a fresh turn.

        Dangling tool chains are always dropped (providers reject them). A plain
        unanswered USER message is only dropped when ``drop_unanswered_user`` is
        True — the caller sets this False during active continuation so a real
        question that failed on a provider hiccup is kept and answered next turn
        instead of being silently deleted.
        """
        cleaned, meta = self.strip_unfinished_tool_tail(self.messages)
        if not meta.get("changed") and drop_unanswered_user:
            cleaned, meta = self.strip_unanswered_user_tail(self.messages)
        if meta.get("changed"):
            self.messages = cleaned[-self.max_history:]
            dropped = int(meta.get("dropped_messages") or 0)
            self.trimmed_messages_count += dropped
            self.last_trimmed_at = time.time()
            if meta.get("user") and self.turn_count > 0:
                self.turn_count -= 1
        return meta

    def sanitize_for_provider(self, max_chars: int | None = None) -> dict[str, Any]:
        """Remove messages that chat-completion providers reject (stale blockers, orphans, incomplete chains).

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
            role = msg.get("role")

            if role == "tool":
                dropped += 1
                i += 1
                continue

            tool_calls = msg.get("tool_calls") if role == "assistant" else None
            if role == "assistant" and tool_calls:
                chain_start = i
                j = i
                complete = False

                while j < len(original):
                    current = original[j]
                    current_calls = current.get("tool_calls") or []
                    if current.get("role") != "assistant" or not current_calls:
                        break

                    expected_ids = [tc.get("id") for tc in current_calls if tc.get("id")]
                    k = j + 1
                    tool_results = []
                    while k < len(original) and original[k].get("role") == "tool":
                        tool_results.append(original[k])
                        k += 1

                    result_ids = [t.get("tool_call_id") for t in tool_results]
                    if not all(tid in result_ids for tid in expected_ids):
                        break

                    if k < len(original) and original[k].get("role") == "assistant":
                        if original[k].get("tool_calls"):
                            j = k
                            continue
                        complete = True
                        chain_end = k + 1
                        break
                    break

                if complete:
                    cleaned.extend(original[chain_start:chain_end])
                else:
                    dropped += max(1, (j + 1) - chain_start)
                i = max(chain_end if complete else j + 1, chain_start + 1)
                continue

            cleaned.append(msg)
            i += 1

        # Strip leading orphan messages that have no preceding user context.
        # This prevents previous-session assistant answers from leaking into
        # a new session's message list ahead of the first real user prompt.
        while cleaned and cleaned[0].get("role") == "assistant" and not cleaned[0].get("tool_calls"):
            cleaned.pop(0)
            dropped += 1

        if len(cleaned) != len(original):
            next_messages = cleaned[-self.max_history:]
            lost = max(0, len(original) - len(next_messages))
            self.messages = next_messages
            if lost:
                self.trimmed_messages_count += lost
                self.last_trimmed_at = time.time()

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
            "changed": len(cleaned) != len(original),
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
        self._handoff_context = ""
        self.turn_count = 0
        self.total_tokens = 0
        self.output_tokens = 0
        self.token_log = []
        self.trimmed_messages_count = 0
        self.last_trimmed_at = 0.0
        self.compacted_messages_count = 0
        self.last_compacted_at = 0.0
