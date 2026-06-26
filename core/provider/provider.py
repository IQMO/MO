"""MO — provider routing.

Providers are OpenAI-compatible chat-completions endpoints — e.g. the official
DeepSeek API (``api.deepseek.com``) or an OpenCode relay — plus an optional OpenAI
Codex OAuth provider (Responses API, from ``~/.codex/auth.json``). The active
provider and its fallback come from ``model.default`` / ``model.fallback``, each
matched against a provider NAME or a MODEL id (see ``_order_provider_chain``).
"""

import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml
from dotenv import load_dotenv
import traceback

from ..atomic_write import atomic_write_json
from ..path_defaults import codex_auth_path, default_config_path, mo_home


def _capture_response_headers(provider_name: str, response_or_stream: Any) -> None:
    """Extract rate-limit headers from an OpenAI SDK response/stream object."""
    try:
        from .provider_capacity import get_capacity
    except ImportError:
        return
    raw = getattr(response_or_stream, "response", None) or getattr(response_or_stream, "_response", None)
    if raw is None:
        return
    headers = getattr(raw, "headers", None)
    if headers is not None:
        try:
            get_capacity().record_headers(provider_name, headers)
        except Exception:
            pass

# The OpenAI SDK is heavy (~1.3s, ~1000 modules: its full `openai.types.*` tree)
# yet is only needed when a provider client is actually constructed — never at
# import time. Import it lazily so `import core.agent.agent` stays light (cold
# start ~1.6s -> ~0.3s); this matters because every terminal MO instance is its
# own process and pays the import. `OpenAI` stays a module attribute so tests
# (and any patch("core.provider.provider.OpenAI")) keep working.
OpenAI = None
HAS_OPENAI: bool | None = None


def _ensure_openai():
    """Import the OpenAI SDK on first use; populate the module globals and return the class (or None).

    Idempotent and patch-safe: if ``OpenAI`` is already set (a real import or a
    test patch) it is not overwritten, so the lazy probe never clobbers a mock.
    """
    global OpenAI, HAS_OPENAI
    if OpenAI is None:
        try:
            from openai import OpenAI as _OpenAI
            OpenAI = _OpenAI
        except ImportError:
            OpenAI = None
    HAS_OPENAI = OpenAI is not None
    return OpenAI


_httpx_mod = None


def _httpx():
    """Lazy-import httpx — only the Codex OAuth provider needs it, so keep it off
    the default (DeepSeek/OpenAI-compatible) cold-start path."""
    global _httpx_mod
    if _httpx_mod is None:
        import httpx as _h
        _httpx_mod = _h
    return _httpx_mod


class ProviderError(RuntimeError):
    """Provider setup/runtime error."""


class SimpleResponse:
    """Minimal response object mimicking OpenAI chat completion message."""
    def __init__(self, content: str = "", tool_calls: list = None, usage: Any = None, finish_reason: str = "", reasoning_content: str | None = None):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = usage
        self.finish_reason = finish_reason
        self.reasoning_content = reasoning_content


def make_tool_call(*, call_id: str, name: str, arguments: str):
    return SimpleNamespace(
        id=call_id or f"call_{abs(hash((name, arguments))) % 10_000_000}",
        type="function",
        function=SimpleNamespace(name=name or "", arguments=arguments or "{}"),
    )


class BaseProvider:
    name = "base"
    api_mode = "unknown"
    # Vision: can this provider actually SEE images sent in the message stream
    # (computer-use capture_screen)? False by default — a provider opts in only
    # when its API path delivers image parts to a vision-capable model.
    supports_vision = False

    def __init__(self, model: str):
        self.model = model
        self.base_url = ""

    def stream(self, *, messages: list[dict], tools: list[dict], temperature: float, max_tokens: int):
        raise NotImplementedError

    def complete(self, *, messages: list[dict], tools: list[dict], temperature: float, max_tokens: int, on_token: object = None):
        raise NotImplementedError


class ChatCompletionsProvider(BaseProvider):
    """OpenAI-compatible chat completions provider."""

    api_mode = "chat_completions"

    def __init__(self, *, name: str, base_url: str, api_key: str, model: str, timeout: float = 60.0, headers: dict[str, str] | None = None, reasoning_effort: str | None = None, supports_vision: bool = False):
        super().__init__(model=model)
        self.name = name
        # Opt-in per provider config (`vision: true`). Off by default because most
        # OpenAI-compatible chat endpoints are text-only; the operator declares it.
        self.supports_vision = bool(supports_vision)
        self.base_url = base_url
        self.timeout = float(timeout or 60.0)
        # Optional per-provider OpenAI-style reasoning_effort. Default None → NOT sent,
        # so providers that reject unknown params (unverified support) are unaffected.
        # Operators enable it only for providers known to accept it (o-series, etc.).
        self.reasoning_effort = str(reasoning_effort).strip().lower() if reasoning_effort else None
        openai_cls = _ensure_openai()
        if openai_cls is None:
            raise RuntimeError("openai package not installed. Run: pip install -r requirements.txt")
        self.client = openai_cls(api_key=api_key, base_url=base_url, default_headers=headers or None, timeout=self.timeout, max_retries=0)

    @staticmethod
    def _image_urls(content: list) -> list[str]:
        """Pull image data-URIs out of a list-content message."""
        urls: list[str] = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") not in ("image", "image_url", "input_image"):
                continue
            url = part.get("image_url") or part.get("url") or part.get("data")
            if isinstance(url, dict):
                url = url.get("url")
            if url:
                urls.append(str(url))
        return urls

    @staticmethod
    def _normalize_messages(messages: list[dict], supports_vision: bool = False) -> list[dict]:
        """Normalize list-content (image-bearing computer-use tool results) for the
        chat-completions API.

        The chat-completions ``tool`` role only accepts string content, so when the
        provider is vision-capable we keep the tool text in the tool message and
        re-deliver the screenshot in a following ``user`` message using the proper
        ``image_url`` part shape — the only place chat-completions accepts images.
        When the provider can't see images, we flatten to text with an actionable
        note instead of sending a payload the model will never receive."""
        out: list[dict] = []
        for msg in messages:
            content = msg.get("content")
            if not isinstance(content, list):
                out.append(msg)
                continue
            texts = [str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") in ("text", "input_text", "output_text")]
            image_urls = ChatCompletionsProvider._image_urls(content)
            flat = "\n".join(t for t in texts if t)
            if image_urls and supports_vision:
                # tool message: text only (valid); image rides in a trailing user turn.
                out.append({**msg, "content": flat or "[image]"})
                out.append({
                    "role": "user",
                    "content": [{"type": "text", "text": "[screen capture]"}]
                    + [{"type": "image_url", "image_url": {"url": u}} for u in image_urls],
                })
                continue
            if image_urls:
                flat = (flat + "\n[image omitted: provider is not vision-capable — set `vision: true` "
                        "on a vision-capable provider, or use openai-codex, to let MO see the screen]").strip()
            out.append({**msg, "content": flat})
        return out

    def stream(self, *, messages: list[dict], tools: list[dict], temperature: float, max_tokens: int):
        messages = self._normalize_messages(messages, self.supports_vision)
        request = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": True,
            "timeout": self.timeout,
        }
        if temperature > 0:
            request["temperature"] = temperature
        if tools:
            request["tools"] = tools
            request["tool_choice"] = "auto"
        if self.reasoning_effort:
            request["reasoning_effort"] = self.reasoning_effort
        request["stream_options"] = {"include_usage": True}
        try:
            stream = self.client.chat.completions.create(**request)
            _capture_response_headers(self.name, stream)
            return stream
        except Exception as exc:
            if "stream_options" not in str(exc).lower():
                raise
            request.pop("stream_options", None)
            stream = self.client.chat.completions.create(**request)
            _capture_response_headers(self.name, stream)
            return stream

    def complete(self, *, messages: list[dict], tools: list[dict], temperature: float, max_tokens: int, on_token: object = None):
        if on_token is None:
            request = {
                "model": self.model,
                "messages": self._normalize_messages(messages, self.supports_vision),
                "max_tokens": max_tokens,
                "timeout": self.timeout,
            }
            if temperature > 0:
                request["temperature"] = temperature
            if tools:
                request["tools"] = tools
                request["tool_choice"] = "auto"
            if self.reasoning_effort:
                request["reasoning_effort"] = self.reasoning_effort
            response = self.client.chat.completions.create(**request)
            _capture_response_headers(self.name, response)
            message = response.choices[0].message
            try:
                message.usage = response.usage
            except Exception:
                traceback.print_exc()
            try:
                finish_reason = response.choices[0].finish_reason
                if finish_reason:
                    object.__setattr__(message, "finish_reason", finish_reason)
            except Exception:
                traceback.print_exc()
            return message

        # Streaming mode
        stream_generator = self.stream(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        content_accum = []
        reasoning_accum = []
        tool_calls_accum = []
        finish_reason = "stop"
        usage = None

        for chunk in stream_generator:
            if hasattr(chunk, "usage") and chunk.usage:
                usage = chunk.usage
            elif isinstance(chunk, dict) and chunk.get("usage"):
                usage = chunk["usage"]

            choices = getattr(chunk, "choices", None) or (chunk.get("choices") if isinstance(chunk, dict) else None)
            if not choices:
                continue

            choice = choices[0]
            if hasattr(choice, "finish_reason") and choice.finish_reason:
                finish_reason = choice.finish_reason
            elif isinstance(choice, dict) and choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]

            delta = getattr(choice, "delta", None) or (choice.get("delta") if isinstance(choice, dict) else None)
            if not delta:
                continue

            # Accumulate content
            text = getattr(delta, "content", None) or (delta.get("content") if isinstance(delta, dict) else None)
            if text:
                content_accum.append(text)
                on_token(text)

            # Accumulate reasoning
            reasoning_text = getattr(delta, "reasoning_content", None) or (delta.get("reasoning_content") if isinstance(delta, dict) else None)
            if reasoning_text:
                reasoning_accum.append(reasoning_text)

            # Accumulate tool calls
            tcs = getattr(delta, "tool_calls", None) or (delta.get("tool_calls") if isinstance(delta, dict) else None)
            if tcs:
                for tc in tcs:
                    idx = getattr(tc, "index", None)
                    if idx is None and isinstance(tc, dict):
                        idx = tc.get("index")
                    if idx is None:
                        idx = 0

                    while len(tool_calls_accum) <= idx:
                        tool_calls_accum.append({
                            "id": None,
                            "type": "function",
                            "function": {"name": "", "arguments": ""}
                        })

                    item = tool_calls_accum[idx]

                    tc_id = getattr(tc, "id", None) or (tc.get("id") if isinstance(tc, dict) else None)
                    if tc_id:
                        item["id"] = tc_id

                    fn = getattr(tc, "function", None) or (tc.get("function") if isinstance(tc, dict) else None)
                    if fn:
                        name = getattr(fn, "name", None) or (fn.get("name") if isinstance(fn, dict) else None)
                        if name:
                            item["function"]["name"] += name
                        args = getattr(fn, "arguments", None) or (fn.get("arguments") if isinstance(fn, dict) else None)
                        if args:
                            item["function"]["arguments"] += args

        # Convert accumulated tool calls to SimpleNamespace
        final_tool_calls = []
        for tc_dict in tool_calls_accum:
            if tc_dict.get("id") or tc_dict["function"]["name"]:
                final_tool_calls.append(
                    make_tool_call(
                        call_id=tc_dict.get("id") or "",
                        name=tc_dict["function"]["name"],
                        arguments=tc_dict["function"]["arguments"],
                    )
                )

        return SimpleResponse(
            content="".join(content_accum),
            tool_calls=final_tool_calls,
            usage=usage,
            finish_reason=finish_reason,
            reasoning_content="".join(reasoning_accum) if reasoning_accum else None,
        )


class MockProvider(BaseProvider):
    """Deterministic local provider used only when config selects type: mock."""

    name = "mock"
    api_mode = "mock"

    def __init__(self, *, name: str = "mock", model: str = "mock-model"):
        super().__init__(model=model)
        self.name = name

    def stream(self, *, messages: list[dict], tools: list[dict], temperature: float, max_tokens: int):
        for token in self._answer(messages).split(" "):
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=token + " "), finish_reason="")],
                usage=None,
            )
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=""), finish_reason="stop")], usage=None)

    def complete(self, *, messages: list[dict], tools: list[dict], temperature: float, max_tokens: int, on_token: object = None):
        content = self._answer(messages)
        if on_token:
            for token in content.split(" "):
                on_token(token + " ")
        return SimpleResponse(content=content, finish_reason="stop")

    @staticmethod
    def _answer(messages: list[dict]) -> str:
        user_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_text = str(msg.get("content") or "")
                break
        lowered = user_text.lower()
        if any(word in lowered for word in ("review", "audit", "inspect", "analyze", "analyse", "deep")):
            return "Confirmed findings:\n- Found no runtime issue in this mock smoke test.\n\nRecommendations:\n- Use full tests for code-level proof."
        return "Mock response from MO."


class CodexOAuthProvider(BaseProvider):
    """OpenAI Codex OAuth provider using ~/.codex/auth.json and Responses API."""

    name = "openai-codex"
    api_mode = "codex_responses"
    # Responses API maps tool-result image parts to input_image, so a vision model
    # behind Codex genuinely sees the screen (computer-use capture_screen).
    supports_vision = True
    base_url = "https://chatgpt.com/backend-api/codex"
    oauth_token_url = "https://auth.openai.com/oauth/token"
    oauth_client_id = "app_EMoamEEZ73f0CkXaXp7hrann"

    def __init__(self, *, model: str = "gpt-5.5", auth_path: str | None = None, timeout: float = 60.0, reasoning_effort: str | None = None):
        super().__init__(model=model)
        self.base_url = type(self).base_url
        self.auth_path = Path(auth_path).expanduser() if auth_path else Path.home() / ".codex" / "auth.json"
        # Responses API reasoning effort; default None → not sent (no behavior change).
        self.reasoning_effort = str(reasoning_effort).strip().lower() if reasoning_effort else None
        self.timeout_seconds = float(timeout or 60.0)
        self.timeout = _httpx().Timeout(self.timeout_seconds, connect=min(30.0, self.timeout_seconds))
        access_token = self._read_access_token()
        if self._access_token_expired(access_token):
            refreshed = self._refresh_access_token()
            if refreshed:
                access_token = refreshed
        headers = self._codex_headers(access_token)
        self.access_token = access_token
        self.default_headers = headers
        openai_cls = _ensure_openai()
        if openai_cls is None:
            raise RuntimeError("openai package not installed. Run: pip install -r requirements.txt")
        self.client = openai_cls(
            api_key=access_token,
            base_url=self.base_url,
            default_headers=headers,
            timeout=self.timeout,
            max_retries=0,
        )

    @staticmethod
    def _jwt_exp(token: str) -> int | None:
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            return int(json.loads(base64.urlsafe_b64decode(payload)).get("exp"))
        except Exception:
            return None

    @classmethod
    def _access_token_expired(cls, token: str, *, skew_seconds: int = 120) -> bool:
        """True only when we can prove the token is at/near expiry. If exp can't be
        read, return False (don't force an unnecessary refresh)."""
        import time
        exp = cls._jwt_exp(token)
        if not exp:
            return False
        return time.time() >= (exp - skew_seconds)

    def _refresh_access_token(self) -> str | None:
        """Mint a fresh access token from the stored refresh_token and persist it.

        Same OAuth refresh the Codex CLI performs on use; MO reads the token file
        directly, so without this an expired access token (tokens last only days)
        hard-fails every call with 401. Best-effort: any failure returns None and
        the caller proceeds with the existing token.
        """
        try:
            data = json.loads(self.auth_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        tokens = data.get("tokens") or {}
        refresh_token = str(tokens.get("refresh_token") or "").strip()
        if not refresh_token:
            return None
        payload = {
            "client_id": self.oauth_client_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": "openid profile email",
        }
        try:
            resp = _httpx().post(self.oauth_token_url, json=payload, timeout=self.timeout, follow_redirects=True)
            if resp.status_code >= 400:
                return None
            body = resp.json()
        except Exception:
            return None
        new_access = str(body.get("access_token") or "").strip()
        if not new_access:
            return None
        for key in ("access_token", "id_token", "refresh_token"):
            if body.get(key):
                tokens[key] = body[key]
        data["tokens"] = tokens
        try:
            atomic_write_json(self.auth_path, data, indent=2)
        except Exception:
            pass
        return new_access

    def _read_access_token(self) -> str:
        if not self.auth_path.exists():
            raise ProviderError(f"OpenAI Codex OAuth auth file not found: {self.auth_path}")
        data = json.loads(self.auth_path.read_text(encoding="utf-8"))
        token = ((data.get("tokens") or {}).get("access_token") or "").strip()
        if not token:
            raise ProviderError(f"OpenAI Codex OAuth access token missing in: {self.auth_path}")
        return token

    @staticmethod
    def _codex_headers(access_token: str) -> dict[str, str]:
        headers = {
            "User-Agent": "codex_cli_rs/0.0.0 (MO Agent)",
            "originator": "codex_cli_rs",
        }
        try:
            parts = access_token.split(".")
            if len(parts) >= 2:
                payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
                claims = json.loads(base64.urlsafe_b64decode(payload_b64))
                acct_id = claims.get("https://api.openai.com/auth", {}).get("chatgpt_account_id")
                if isinstance(acct_id, str) and acct_id:
                    headers["ChatGPT-Account-ID"] = acct_id
        except Exception:
            traceback.print_exc()
        return headers

    @staticmethod
    def _responses_content_parts(content: Any, mapped_role: str) -> list[dict]:
        """Map a message ``content`` (str or list-of-parts) to Responses API parts.

        Backward compatible: a plain string yields the same single text part as
        before. A list lets a turn mix text and images — image parts become
        ``input_image`` data-URI parts so vision-capable models can see them
        (computer-use ``capture_screen``). Assistant text uses ``output_text``.
        """
        text_type = "output_text" if mapped_role == "assistant" else "input_text"
        if not isinstance(content, list):
            text = str(content or "")
            return [{"type": text_type, "text": text}] if text else []
        parts: list[dict] = []
        for part in content:
            if not isinstance(part, dict):
                if str(part):
                    parts.append({"type": text_type, "text": str(part)})
                continue
            ptype = part.get("type")
            if ptype in {"text", "input_text", "output_text"}:
                text = str(part.get("text") or "")
                if text:
                    parts.append({"type": text_type, "text": text})
            elif ptype in {"image", "image_url", "input_image"}:
                url = part.get("image_url") or part.get("url") or part.get("data")
                if isinstance(url, dict):
                    url = url.get("url")
                if url:
                    parts.append({"type": "input_image", "image_url": str(url)})
        return parts

    @classmethod
    def _to_instructions_and_input(cls, messages: list[dict]) -> tuple[str, list[dict]]:
        system_parts: list[str] = []
        input_items: list[dict] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content") or ""
            if role == "system":
                system_parts.append(str(content) if not isinstance(content, list) else "".join(p.get("text", "") for p in content if isinstance(p, dict)))
            elif role in {"user", "assistant"}:
                mapped_role = "assistant" if role == "assistant" else "user"
                parts = cls._responses_content_parts(content, mapped_role)
                if parts:
                    input_items.append({
                        "role": mapped_role,
                        "content": parts,
                    })
                # Do not serialize internal tool-call metadata as assistant prose.
                # The Responses API receives available tools separately; turning
                # prior calls into text like "[tool calls requested]" teaches
                # models to print raw tool-call payloads instead of using tools.
            elif role == "tool":
                if isinstance(content, list):
                    # Image-bearing tool result (computer-use capture_screen): map
                    # text→input_text and image→input_image so the model sees it.
                    parts = cls._responses_content_parts(content, "user")
                    parts = [{"type": "input_text", "text": "[tool result]"}] + parts
                    input_items.append({"role": "user", "content": parts})
                else:
                    input_items.append({
                        "role": "user",
                        "content": [{"type": "input_text", "text": f"[tool result]\n{str(content)}"}],
                    })
        instructions = "\n\n".join(system_parts).strip() or "You are MO."
        if not input_items:
            input_items = [{"role": "user", "content": [{"type": "input_text", "text": "Continue."}]}]
        return instructions, input_items

    @staticmethod
    def _to_responses_tools(tools: list[dict]) -> list[dict]:
        converted: list[dict] = []
        for tool in tools or []:
            if tool.get("type") != "function":
                continue
            fn = tool.get("function") or {}
            name = fn.get("name")
            if not name:
                continue
            converted.append({
                "type": "function",
                "name": name,
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
            })
        return converted

    @staticmethod
    def _tool_call_from_responses_item(item: Any):
        item_type = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
        if item_type not in {"function_call", "function_call_output"}:
            return None
        name = getattr(item, "name", None) or (item.get("name") if isinstance(item, dict) else None)
        arguments = getattr(item, "arguments", None) or (item.get("arguments") if isinstance(item, dict) else None) or "{}"
        call_id = (
            getattr(item, "call_id", None)
            or getattr(item, "id", None)
            or (item.get("call_id") if isinstance(item, dict) else None)
            or (item.get("id") if isinstance(item, dict) else None)
        )
        if not name:
            return None
        return make_tool_call(call_id=str(call_id or ""), name=str(name), arguments=str(arguments))

    def stream(self, *, messages: list[dict], tools: list[dict], temperature: float, max_tokens: int):
        instructions, input_items = self._to_instructions_and_input(messages)
        response_tools = self._to_responses_tools(tools)
        request: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": input_items,
            "store": False,
            "stream": True,
        }
        if response_tools:
            request["tools"] = response_tools
            request["tool_choice"] = "auto"
        if self.reasoning_effort:
            request["reasoning"] = {"effort": self.reasoning_effort}

        headers = {
            **getattr(self, "default_headers", {}),
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url.rstrip('/')}/responses"

        def _event_from_sse(event_type: str, data_lines: list[str]):
            if not data_lines:
                return None
            data = "\n".join(data_lines).strip()
            if not data or data == "[DONE]":
                return None
            try:
                payload = json.loads(data)
            except Exception:
                return SimpleNamespace(type=event_type or "message", delta=data)
            etype = str(payload.get("type") or event_type or "")
            if etype in {"response.failed", "response.incomplete"}:
                detail = payload.get("error") or payload.get("response") or payload
                raise ProviderError(f"OpenAI Codex Responses stream failed: {detail}")
            response_payload = payload.get("response")
            if isinstance(response_payload, dict) and isinstance(response_payload.get("usage"), dict):
                response_payload = SimpleNamespace(usage=SimpleNamespace(**response_payload["usage"]))
            return SimpleNamespace(
                type=etype,
                delta=payload.get("delta") or "",
                item=payload.get("item"),
                response=response_payload,
                error=payload.get("error"),
            )

        def _events():
            event_type = ""
            data_lines: list[str] = []
            with _httpx().stream("POST", url, headers=headers, json=request, timeout=self.timeout, follow_redirects=True) as response:
                if response.status_code >= 400:
                    detail = response.read().decode("utf-8", errors="replace")[:1000]
                    raise ProviderError(f"OpenAI Codex Responses stream failed ({response.status_code}): {detail}")
                try:
                    from .provider_capacity import get_capacity
                    get_capacity().record_headers(self.name, response.headers)
                except Exception:
                    pass
                for line in response.iter_lines():
                    if isinstance(line, bytes):
                        line = line.decode("utf-8", errors="replace")
                    line = str(line)
                    if not line:
                        event = _event_from_sse(event_type, data_lines)
                        event_type = ""
                        data_lines = []
                        if event is not None:
                            yield event
                        continue
                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                event = _event_from_sse(event_type, data_lines)
                if event is not None:
                    yield event
        return _events()

    def complete(self, *, messages: list[dict], tools: list[dict], temperature: float, max_tokens: int, on_token: object = None):
        collected_text: list[str] = []
        tool_calls: list[Any] = []
        usage = None
        for event in self.stream(messages=messages, tools=tools, temperature=temperature, max_tokens=max_tokens):
            etype = str(getattr(event, "type", None) or "")
            if etype == "response.output_text.delta":
                delta_text = str(getattr(event, "delta", "") or "")
                collected_text.append(delta_text)
                if on_token and delta_text:
                    on_token(delta_text)
            elif etype == "response.output_item.done":
                tc = self._tool_call_from_responses_item(getattr(event, "item", None))
                if tc:
                    tool_calls.append(tc)
            elif etype == "response.completed":
                response_payload = getattr(event, "response", None)
                usage = getattr(response_payload, "usage", None)
                if usage is None and isinstance(response_payload, dict):
                    usage = response_payload.get("usage")
                    if isinstance(usage, dict):
                        usage = SimpleNamespace(**usage)
            # Important: do not append arbitrary event.delta/content here.
            # Codex Responses emits function-call argument deltas as JSON text;
            # treating those as assistant text leaks raw tool payloads into chat.
        return SimpleResponse(content="".join(collected_text), tool_calls=tool_calls, usage=usage)


# ── Config loading ─────────────────────────────────────────────────

class ConfigLoadError(RuntimeError):
    """Operator-facing config loading failure without traceback details."""

    def __init__(self, path: str, message: str):
        self.path = path
        self.message = message
        super().__init__(f"Config error in {path}: {message}")


def load_config(config_path: str | None = None) -> dict:
    """Load MO runtime config.

    No-arg callers use the private default config (`~/.mo/config.yaml`). A
    checkout-local `config.yaml` is only active when passed explicitly, via CLI
    `--config`, or through `MO_CONFIG` resolution.
    """
    resolved = config_path or default_config_path()
    path = str(Path(resolved).expanduser().resolve(strict=False))
    try:
        with open(resolved, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"line {int(getattr(mark, 'line', -1)) + 1}, column {int(getattr(mark, 'column', -1)) + 1}" if mark is not None else "YAML parse error"
        problem = str(getattr(exc, "problem", "") or "invalid YAML").strip()
        raise ConfigLoadError(path, f"{where}: {problem}") from exc
    except OSError as exc:
        raise ConfigLoadError(path, f"could not read config: {exc.strerror or type(exc).__name__}") from exc
    if not isinstance(config, dict):
        raise ConfigLoadError(path, "top-level YAML value must be a mapping/object")
    config["_config_path"] = path
    return config


def _load_runtime_env(config: dict) -> None:
    """Load private runtime .env files without letting repo files take priority."""
    candidates: list[Path] = []
    cfg_path = str((config or {}).get("_config_path") or "").strip()
    if cfg_path:
        candidates.append(Path(cfg_path).expanduser().resolve(strict=False).parent / ".env")
    try:
        candidates.append(mo_home(config) / ".env")
    except Exception:
        traceback.print_exc()

    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.exists():
            load_dotenv(dotenv_path=path, override=False)


def _resolve_api_key(provider_cfg: dict) -> str:
    api_key_env = provider_cfg.get("api_key_env")
    api_key = os.getenv(api_key_env or "") if api_key_env else ""
    if not api_key:
        api_key = str(provider_cfg.get("api_key") or "").strip()
    return api_key


def _provider_from_config(provider_cfg: dict, model: str) -> BaseProvider:
    name = provider_cfg["name"]
    kind = provider_cfg.get("type") or provider_cfg.get("api_mode") or "chat_completions"

    if kind == "mock":
        return MockProvider(name=name, model=model or "mock-model")

    if name == "openai-codex" or kind == "codex_responses":
        return CodexOAuthProvider(
            model=model,
            auth_path=codex_auth_path(provider_cfg.get("auth_path")),
            timeout=float(provider_cfg.get("timeout", 60.0) or 60.0),
            reasoning_effort=provider_cfg.get("reasoning_effort"),
        )

    api_key_env = provider_cfg.get("api_key_env")
    api_key = provider_cfg.get("_api_key") or _resolve_api_key(provider_cfg)
    if not api_key:
        raise ProviderError(f"API key not found for provider {name}. env={api_key_env or '<none>'}")
    return ChatCompletionsProvider(
        name=name,
        base_url=provider_cfg["base_url"],
        api_key=api_key,
        model=model,
        timeout=float(provider_cfg.get("timeout", 60.0) or 60.0),
        headers=provider_cfg.get("_headers") or provider_cfg.get("headers"),
        reasoning_effort=provider_cfg.get("reasoning_effort"),
        supports_vision=bool(provider_cfg.get("vision", False)),
    )


def first_vision_provider_index(providers, *, can_accept=None) -> int | None:
    """Index of the first vision-capable provider that has capacity, or None.

    Used to route a screenshot (capture_screen) to a provider that can actually
    SEE it (e.g. openai-codex) when the active provider is text-only.
    """
    for index, provider in enumerate(providers or []):
        if not getattr(provider, "supports_vision", False):
            continue
        if can_accept is not None and not can_accept(getattr(provider, "name", "")):
            continue
        return index
    return None


def _provider_matches_selector(provider: BaseProvider, selector: str) -> bool:
    """True when ``selector`` (a ``model.default``/``fallback`` value) matches this
    provider by its NAME, its MODEL id, or ``name/model``."""
    value = str(selector or "").strip().lower()
    if not value:
        return False
    name = str(getattr(provider, "name", "") or "").lower()
    model = str(getattr(provider, "model", "") or "").lower()
    return value in {name, model, f"{name}/{model}"}


PRT_REVIEW_MODEL_ORDER = ("deepseek-v4-pro", "codex")


def _model_matches_review_target(model: str, target: str) -> bool:
    value = str(model or "").strip().lower()
    selector = str(target or "").strip().lower()
    if not value or not selector:
        return False
    return value == selector or value.endswith(f"/{selector}") or selector in value


def _provider_is_codex(provider: BaseProvider | None) -> bool:
    if provider is None:
        return False
    name = str(getattr(provider, "name", "") or "").strip().lower()
    api_mode = str(getattr(provider, "api_mode", "") or "").strip().lower()
    return "codex" in name or "codex" in api_mode


def _provider_matches_review_target(provider: BaseProvider, target: str) -> bool:
    selector = str(target or "").strip().lower()
    if selector == "codex":
        return _provider_is_codex(provider)
    return _model_matches_review_target(str(getattr(provider, "model", "") or ""), selector)


def is_prt_review_provider(provider: BaseProvider | None) -> bool:
    """Return True when a provider is allowed on the PRT review lane.

    PRT reviews are deliberately narrowed to DeepSeek v4 Pro plus Codex.
    The review lane must not drain broad fallback providers such as Anthropic or
    big-pickle just because they are present in the general provider chain.
    """
    if provider is None:
        return False
    model = str(getattr(provider, "model", "") or "").strip().lower()
    if "free" in model:
        return False
    if _provider_is_codex(provider):
        return True
    return any(_model_matches_review_target(model, target) for target in PRT_REVIEW_MODEL_ORDER if target != "codex")


def prt_review_provider_chain(
    providers: list[BaseProvider],
    *,
    active_provider: BaseProvider | None = None,
    default_model: str = "",
    fallback_model: str = "",
) -> list[BaseProvider]:
    """Return the PRT review provider chain.

    Honors the configured prt.default_model -> prt.fallback_model order so PRT
    works on whatever capable providers the user configured — not only the
    DeepSeek/Codex default. Falls back to the explicit DeepSeek -> Codex order
    when no review models are configured, and finally to the active provider so
    PRT degrades gracefully on any provider stack instead of failing outright.
    """
    pool = list(providers or [])
    chain: list[BaseProvider] = []

    def add(provider: BaseProvider | None) -> None:
        if provider is None:
            return
        if any(existing is provider for existing in chain):
            return
        chain.append(provider)

    # 1. Configured review models win (user's explicit choice, any provider).
    for target in (default_model, fallback_model):
        if not str(target or "").strip():
            continue
        for provider in pool:
            if _provider_matches_review_target(provider, target):
                add(provider)

    # 2. No config match -> the explicit DeepSeek -> Codex default order.
    if not chain:
        for target in PRT_REVIEW_MODEL_ORDER:
            for provider in pool:
                if is_prt_review_provider(provider) and _provider_matches_review_target(provider, target):
                    add(provider)

    # 3. Graceful degrade: never hard-fail PRT — use the active provider.
    if not chain:
        add(active_provider)

    return chain


def _order_provider_chain(providers: list[BaseProvider], model_cfg: dict) -> list[BaseProvider]:
    """Respect model.default/model.fallback selectors while preserving provider order."""
    ordered = list(providers)
    default_selector = str((model_cfg or {}).get("default") or "").strip()
    fallback_selector = str((model_cfg or {}).get("fallback") or "").strip()

    if default_selector:
        default_index = next((idx for idx, provider in enumerate(ordered) if _provider_matches_selector(provider, default_selector)), None)
        if default_index is not None:
            ordered.insert(0, ordered.pop(default_index))

    if fallback_selector and len(ordered) > 1:
        fallback_index = next(
            (idx for idx, provider in enumerate(ordered[1:], start=1) if _provider_matches_selector(provider, fallback_selector)),
            None,
        )
        if fallback_index is not None:
            ordered.insert(1, ordered.pop(fallback_index))

    return ordered


def init_provider(config: dict = None):
    """Initialize provider chain from config."""
    if config is None:
        config = load_config()
    _load_runtime_env(config)

    agent_cfg = config.get("agent", {})
    model_cfg = config.get("model", {})

    providers_cfg = list(config.get("providers") or [])
    providers: list[BaseProvider] = []
    setup_errors: list[str] = []

    for pcfg in providers_cfg:
        try:
            model = pcfg.get("model") or model_cfg.get("default")
            providers.append(_provider_from_config(pcfg, model))
        except Exception as exc:
            setup_errors.append(f"{pcfg.get('name','?')}/{pcfg.get('model','?')}: {type(exc).__name__}: {exc}")

    if not providers:
        raise ProviderError("No providers initialized. " + " | ".join(setup_errors))

    providers = _order_provider_chain(providers, model_cfg)
    active = providers[0]
    return {
        "providers": providers,
        "provider_index": 0,
        "client": getattr(active, "client", None),
        "model": active.model,
        "fallback_model": providers[1].model if len(providers) > 1 else None,
        "provider_name": active.name,
        "base_url": active.base_url,
        "api_mode": active.api_mode,
        "setup_errors": setup_errors,
        "reasoning": agent_cfg.get("reasoning", "high"),
        "temperature": agent_cfg.get("temperature", 0.7),
        "max_tokens": agent_cfg.get("max_tokens", 8192),
    }


# ── Error utilities ────────────────────────────────────────────────

def is_rate_limit_error(error_msg: str) -> bool:
    e = str(error_msg or "").lower()
    markers = ("429", "too many requests", "rate limit", "rate_limit", "concurrency", "pace your requests", "usage limit", "quota exceeded", "billing quota")
    return any(marker in e for marker in markers)


def is_context_overflow_error(error_msg: str) -> bool:
    """Return True when a provider error means the request exceeded context/input size.

    This is intentionally separate from provider fallback classification. Context
    overflow should first trigger MO's deterministic compact/handoff recovery and
    exactly one retry of the same request shape; falling through to another
    provider can hide the real context-pressure evidence.
    """
    e = (error_msg or "").lower()
    if not e:
        return False
    markers = (
        "context_length_exceeded",
        "context length exceeded",
        "maximum context length",
        "max context length",
        "context window",
        "context_window",
        "input length",
        "input too long",
        "input too large",
        "prompt is too long",
        "prompt too long",
        "messages too long",
        "too many tokens",
        "token limit exceeded",
        "exceeds the token limit",
        "tokens exceed",
        "request too large",
        "payload too large",
        "body too large",
        "413",
    )
    if any(marker in e for marker in markers):
        return True
    import re as _re
    if _re.search(r"\b(context|prompt|input|messages)\b.{0,100}\b(too\s+(?:large|long)|exceed(?:ed|s)?|limit|maximum|max)\b", e):
        return True
    if _re.search(r"\b(too\s+(?:large|long)|exceed(?:ed|s)?)\b.{0,100}\b(context|prompt|input|messages|tokens)\b", e):
        return True
    return False


def fallback_reason(error_msg: str) -> str | None:
    e = (error_msg or "").lower()
    if "402" in e or "insufficient balance" in e:
        return "primary provider balance/route blocked"
    if "401" in e or "model is disabled" in e or "modelerror" in e or "not supported" in e:
        return "primary provider auth/model route failed"
    if "403" in e or "permissiondenied" in e:
        return "primary provider permission denied"
    if is_rate_limit_error(e):
        return "primary provider rate/concurrency limit"
    if any(code in e for code in ("500", "502", "503", "504", "529")):
        return "primary provider server error"
    if "timeout" in e or "timed out" in e or "connection" in e:
        return "primary provider timeout/connection error"
    return None


def clean_provider_error(error_msg: str) -> str:
    raw = str(error_msg or "").strip()
    if not raw:
        return "Unknown provider error"
    import re as _re
    value = _re.sub(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,'\"}]+", r"\1[redacted]", raw)
    value = _re.sub(r"\bsk-[A-Za-z0-9_-]{6,}\b", "sk-[redacted]", value)
    try:
        brace = value.find("{")
        if brace >= 0:
            import ast
            payload = None
            try:
                payload = json.loads(value[brace:])
            except Exception:
                try:
                    payload = ast.literal_eval(value[brace:])
                except Exception:
                    traceback.print_exc()
            if isinstance(payload, dict):
                err = payload.get("error", payload)
                message = err.get("message") if isinstance(err, dict) else None
                if message:
                    return str(message)
    except Exception:
        traceback.print_exc()
    return value
