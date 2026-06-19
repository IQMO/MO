"""MO — provider routing.

Primary: OpenCode/OpenAI-compatible chat completions.
Fallback: OpenAI Codex OAuth from ~/.codex/auth.json via Responses API.
"""

import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import yaml
from dotenv import load_dotenv
import traceback

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

try:
    from openai import OpenAI
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False
    OpenAI = None


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

    def __init__(self, model: str):
        self.model = model
        self.base_url = ""

    def stream(self, *, messages: list[dict], tools: list[dict], temperature: float, max_tokens: int):
        raise NotImplementedError

    def complete(self, *, messages: list[dict], tools: list[dict], temperature: float, max_tokens: int, on_token: object = None):
        raise NotImplementedError


AGENTROUTER_HEADERS = {
    "User-Agent": "codex_cli_rs/0.0.0 (MO Agent)",
    "originator": "codex_cli_rs",
}


class ChatCompletionsProvider(BaseProvider):
    """OpenAI-compatible chat completions provider."""

    api_mode = "chat_completions"

    def __init__(self, *, name: str, base_url: str, api_key: str, model: str, timeout: float = 60.0, headers: dict[str, str] | None = None):
        super().__init__(model=model)
        self.name = name
        self.base_url = base_url
        self.timeout = float(timeout or 60.0)
        self.client = OpenAI(api_key=api_key, base_url=base_url, default_headers=headers or None, timeout=self.timeout, max_retries=0)

    def stream(self, *, messages: list[dict], tools: list[dict], temperature: float, max_tokens: int):
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
                "messages": messages,
                "max_tokens": max_tokens,
                "timeout": self.timeout,
            }
            if temperature > 0:
                request["temperature"] = temperature
            if tools:
                request["tools"] = tools
                request["tool_choice"] = "auto"
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
    base_url = "https://chatgpt.com/backend-api/codex"

    def __init__(self, *, model: str = "gpt-5.5", auth_path: str | None = None, timeout: float = 60.0):
        super().__init__(model=model)
        self.base_url = type(self).base_url
        self.auth_path = Path(auth_path).expanduser() if auth_path else Path.home() / ".codex" / "auth.json"
        self.timeout_seconds = float(timeout or 60.0)
        self.timeout = httpx.Timeout(self.timeout_seconds, connect=min(30.0, self.timeout_seconds))
        access_token = self._read_access_token()
        headers = self._codex_headers(access_token)
        self.access_token = access_token
        self.default_headers = headers
        self.client = OpenAI(
            api_key=access_token,
            base_url=self.base_url,
            default_headers=headers,
            timeout=self.timeout,
            max_retries=0,
        )

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
    def _to_instructions_and_input(messages: list[dict]) -> tuple[str, list[dict]]:
        system_parts: list[str] = []
        input_items: list[dict] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content") or ""
            if role == "system":
                system_parts.append(str(content))
            elif role in {"user", "assistant"}:
                mapped_role = "assistant" if role == "assistant" else "user"
                content_type = "output_text" if mapped_role == "assistant" else "input_text"
                if content:
                    input_items.append({
                        "role": mapped_role,
                        "content": [{"type": content_type, "text": str(content)}],
                    })
                # Do not serialize internal tool-call metadata as assistant prose.
                # The Responses API receives available tools separately; turning
                # prior calls into text like "[tool calls requested]" teaches
                # models to print raw tool-call payloads instead of using tools.
            elif role == "tool":
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
            with httpx.stream("POST", url, headers=headers, json=request, timeout=self.timeout, follow_redirects=True) as response:
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

    # Keep explicit developer checkouts working, but only after private/config
    # sibling env files have had first chance to populate missing variables.
    load_dotenv(override=False)


def _read_pi_auth_api_key(provider_name: str) -> str:
    try:
        auth_path = Path.home() / ".pi" / "agent" / "auth.json"
        if not auth_path.exists():
            return ""
        data = json.loads(auth_path.read_text(encoding="utf-8"))
        cred = data.get(provider_name) or {}
        key = str(cred.get("key") or "").strip() if cred.get("type") == "api_key" else ""
        if not key or key.startswith("!"):
            return ""
        return os.getenv(key, key)
    except Exception:
        return ""


def _resolve_api_key(provider_cfg: dict) -> str:
    name = provider_cfg.get("name", "")
    api_key_env = provider_cfg.get("api_key_env")
    api_key = os.getenv(api_key_env or "") if api_key_env else ""
    if not api_key:
        api_key = str(provider_cfg.get("api_key") or "").strip()
    if not api_key and name == "agentrouter":
        api_key = _read_pi_auth_api_key("agentrouter")
    return api_key


def _agentrouter_model_ids(provider_cfg: dict, api_key: str) -> list[str]:
    base_url = str(provider_cfg.get("base_url") or "https://agentrouter.org/v1").rstrip("/")
    timeout = float(provider_cfg.get("timeout", 60.0) or 60.0)
    try:
        response = httpx.get(
            f"{base_url}/models",
            headers={**AGENTROUTER_HEADERS, "Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        return [str(item.get("id")) for item in data.get("data", []) if item.get("id")]
    except Exception as exc:
        raise ProviderError(f"AgentRouter model discovery failed: {exc}") from exc


def _expand_provider_configs(providers_cfg: list[dict], model_cfg: dict) -> list[dict]:
    expanded: list[dict] = []
    for pcfg in providers_cfg:
        kind = pcfg.get("type") or pcfg.get("api_mode") or "chat_completions"
        if pcfg.get("name") != "agentrouter" and kind != "agentrouter":
            expanded.append(pcfg)
            continue
        api_key = _resolve_api_key(pcfg)
        api_key_env = pcfg.get("api_key_env")
        if not api_key:
            raise ProviderError(f"API key not found for provider agentrouter. env={api_key_env or '<none>'}")
        model_ids = _agentrouter_model_ids(pcfg, api_key)
        if not model_ids:
            raise ProviderError("AgentRouter model discovery returned no models")
        for model_id in model_ids:
            item = dict(pcfg)
            item["type"] = "chat_completions"
            item["model"] = model_id
            item["_api_key"] = api_key
            item["_headers"] = AGENTROUTER_HEADERS
            expanded.append(item)
    return expanded


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
    )


def _provider_matches_selector(provider: BaseProvider, selector: str) -> bool:
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
    if not HAS_OPENAI:
        raise RuntimeError("openai package not installed. Run: pip install -r requirements.txt")
    if config is None:
        config = load_config()
    _load_runtime_env(config)

    agent_cfg = config.get("agent", {})
    model_cfg = config.get("model", {})

    providers_cfg = list(config.get("providers") or [])
    providers: list[BaseProvider] = []
    setup_errors: list[str] = []

    try:
        providers_cfg = _expand_provider_configs(providers_cfg, model_cfg)
    except Exception as exc:
        setup_errors.append(f"agentrouter/*: {type(exc).__name__}: {exc}")
        providers_cfg = [p for p in providers_cfg if p.get("name") != "agentrouter" and p.get("type") != "agentrouter"]

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


def review_providers(config: dict = None) -> list[BaseProvider]:
    """Return the restricted provider chain for PRT diff review."""
    if config is None:
        config = load_config()
    _load_runtime_env(config)

    prt_cfg = config.get("prt", {}) if isinstance(config.get("prt", {}), dict) else {}
    review_model = prt_cfg.get("default_model") or PRT_REVIEW_MODEL_ORDER[0]
    fallback_model = prt_cfg.get("fallback_model") or PRT_REVIEW_MODEL_ORDER[1]

    providers_cfg = list(config.get("providers") or [])
    providers: list[BaseProvider] = []

    try:
        providers_cfg = _expand_provider_configs(providers_cfg, {"default": review_model, "fallback": fallback_model})
    except Exception:
        traceback.print_exc()

    for pcfg in providers_cfg:
        try:
            model = pcfg.get("model") or review_model
            providers.append(_provider_from_config(pcfg, model))
        except Exception:
            continue

    chain = prt_review_provider_chain(
        providers,
        default_model=str(review_model),
        fallback_model=str(fallback_model),
    )
    if not chain:
        raise ProviderError("No DeepSeek v4 Pro or Codex providers found for PRT review.")

    return chain


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
