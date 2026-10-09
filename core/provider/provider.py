"""MO — provider routing.

Providers are OpenAI-compatible chat-completions endpoints — e.g. official
DeepSeek/Z.ai APIs or an OpenCode relay — plus an optional OpenAI
Codex OAuth provider (Responses API, from ``~/.codex/auth.json``). The active
provider and its fallback come from ``model.default`` / ``model.fallback``, each
matched against a provider NAME or a MODEL id (see ``_order_provider_chain``).
"""

from core.state.configuration_defaults import DEFAULT_PREFERENCES

import base64
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
from html import unescape
from importlib.util import find_spec
import inspect
import json
from pathlib import Path
import re
import threading
import time
from types import SimpleNamespace
from typing import Any
import urllib.parse

import traceback

from ..utils.atomic_write import atomic_write_json
from ..state.paths import codex_auth_path, default_config_path
from ..state.secrets import resolve_secret
from ..runtime.backend_monitor import monitor_phase


_REQUEST_OVERRIDES: ContextVar[dict[str, Any]] = ContextVar(
    "mo_provider_request_overrides",
    default={},
)
_REQUEST_LIMIT_LOCK = threading.Lock()


class ProviderRequestLimitReached(RuntimeError):
    """An explicit operator request limit stopped the next provider attempt."""


def current_provider_request_limit() -> dict[str, Any] | None:
    return _REQUEST_OVERRIDES.get().get("_request_limit")


@contextmanager
def provider_request_limit(max_requests: int = 0):
    """Share one opt-in completion-attempt ceiling across a turn and its helpers.

    Count SDK create/Codex POST attempts, including model/authentication retries;
    HTTP redirect hops and credential refresh are not separate completions.
    """
    if type(max_requests) is not int or max_requests < 0:
        raise ValueError("Provider request limit must be a nonnegative integer.")
    current = current_provider_request_limit()
    if current is not None:
        yield current
        return
    state = {
        "max_requests": max_requests,
        "requests": 0,
        "exhausted": False,
        "started_at": time.monotonic(),
    }
    with provider_request_overrides({"_request_limit": state}):
        yield state


def check_provider_request_limit() -> None:
    state = current_provider_request_limit()
    if state is None:
        return
    with _REQUEST_LIMIT_LOCK:
        _check_request_limit_state(state)


def _check_request_limit_state(state: dict[str, Any]) -> dict[str, Any] | None:
    reservation = _REQUEST_OVERRIDES.get().get("_request_reservation")
    if reservation and reservation["state"] is state and reservation["available"]:
        return reservation
    if state["max_requests"] and state["requests"] >= state["max_requests"]:
        state["exhausted"] = True
        raise ProviderRequestLimitReached(
            f"Provider request limit reached ({state['requests']}/{state['max_requests']}); "
            "stopped before another request."
        )
    return None


def reserve_provider_request(*, provider_name: str = "", model_name: str = "") -> None:
    """Reserve a completion attempt; redirects/credential refresh are not separate."""
    state = current_provider_request_limit()
    if state is None:
        return
    with _REQUEST_LIMIT_LOCK:
        reservation = _check_request_limit_state(state)
        if reservation is not None:
            reservation["available"] = False
            return
        state["requests"] += 1
        admitted = {"requests": state["requests"], "max_requests": state["max_requests"]}
    if state["max_requests"]:
        try:
            from ..runtime.backend_monitor import get_monitor

            monitor = _request_option("monitor") or get_monitor()
            if monitor is not None:
                monitor.emit("provider_request_admitted", {
                    **admitted, "provider": provider_name, "model": model_name,
                })
        except Exception:
            pass


@contextmanager
def provider_request_overrides(overrides: dict[str, Any] | None = None):
    """Apply request-local provider options without mutating a shared provider.

    MO can serve terminal, Desktop, Telegram, and scheduler requests concurrently
    through the same provider object. Context-local options keep one lightweight
    turn from changing another turn's configured reasoning behavior.
    """
    current = dict(_REQUEST_OVERRIDES.get())
    current.update(dict(overrides or {}))
    token = _REQUEST_OVERRIDES.set(current)
    try:
        yield
    finally:
        _REQUEST_OVERRIDES.reset(token)


def _request_option(name: str, default: Any = None) -> Any:
    options = _REQUEST_OVERRIDES.get()
    return options.get(name, default)


def _request_client(client: Any) -> Any:
    """Disable hidden SDK retries for a capped request without changing its client."""
    state = current_provider_request_limit()
    if state is None or not state["max_requests"] or getattr(client, "max_retries", None) == 0:
        return client
    with_options = getattr(client, "with_options", None)
    if not callable(with_options):
        raise ProviderRequestLimitReached(
            "Provider request limit cannot be enforced: client cannot disable automatic retries."
        )
    return with_options(max_retries=0)


def _capture_request_metadata(provider_name: str, model_name: str, request: dict[str, Any], *, client: Any = None) -> None:
    """Observe final Chat Completions arguments without changing the SDK call."""
    try:
        from ..runtime.backend_monitor import get_monitor

        monitor = _request_option("monitor") or get_monitor()
        if monitor is not None and getattr(monitor, "enabled", True):
            retries = getattr(client, "max_retries", None)
            retries = retries if isinstance(retries, int) and not isinstance(retries, bool) else None
            monitor.emit("provider_stream", {
                "phase": "request_payload",
                "api": "chat_completions",
                "provider": provider_name,
                "model": model_name,
                "sdk_max_retries": retries,
                "sdk_retries_disabled": retries == 0 if retries is not None else None,
                "timeout_seconds": request.get("timeout") if isinstance(request.get("timeout"), (int, float)) else None,
                **monitor.provider_request_metadata(request),
            })
    except Exception:
        # Optional diagnostics must not change delivery or retry behavior.
        pass


def _capture_response_headers(
    provider_name: str,
    model_name: str,
    response_or_stream: Any,
) -> None:
    """Extract rate-limit headers from an OpenAI SDK response/stream object."""
    try:
        from .provider_capacity import get_capacity
    except ImportError:
        return
    try:
        capacity = get_capacity()
        capacity.record_success(provider_name, model_name)
    except Exception:
        return
    raw = getattr(response_or_stream, "response", None) or getattr(response_or_stream, "_response", None)
    if raw is None:
        return
    headers = getattr(raw, "headers", None)
    if headers is not None:
        try:
            capacity.record_headers(provider_name, headers, model_name)
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


def _openai_available() -> bool:
    """Prove the SDK can be loaded without importing its heavy module tree."""
    if OpenAI is not None:
        return True
    try:
        return find_spec("openai") is not None
    except (ImportError, AttributeError, ValueError):
        return False


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
    def __init__(
        self,
        content: str = "",
        tool_calls: list = None,
        usage: Any = None,
        finish_reason: str = "",
        reasoning_content: str | None = None,
        response_items: list[dict[str, Any]] | None = None,
        response_id: str = "",
        reasoning_context: str = "",
        prompt_cache_key_tag: str = "",
        transport_resumes: int = 0,
    ):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = usage
        self.finish_reason = finish_reason
        self.reasoning_content = reasoning_content
        self.response_items = response_items or []
        self.response_id = str(response_id or "")
        self.reasoning_context = str(reasoning_context or "")
        self.prompt_cache_key_tag = str(prompt_cache_key_tag or "")
        self.transport_resumes = int(transport_resumes)


def make_tool_call(*, call_id: str, name: str, arguments: str):
    return SimpleNamespace(
        id=call_id or f"call_{abs(hash((name, arguments))) % 10_000_000}",
        type="function",
        function=SimpleNamespace(name=name or "", arguments=arguments or "{}"),
    )


_DSML_MARKER = r"[|｜]+\s*DSML\s*[|｜]+"
_DSML_TOOL_CALLS_RE = re.compile(
    rf"^\s*<\s*{_DSML_MARKER}\s*tool_calls\s*>(?P<body>.*)"
    rf"</\s*{_DSML_MARKER}\s*tool_calls\s*>\s*$",
    re.IGNORECASE | re.DOTALL,
)
_DSML_INVOKE_RE = re.compile(
    rf"<\s*{_DSML_MARKER}\s*invoke(?P<attrs>[^>]*)>(?P<body>.*?)"
    rf"</\s*{_DSML_MARKER}\s*invoke\s*>",
    re.IGNORECASE | re.DOTALL,
)
_DSML_PARAMETER_RE = re.compile(
    rf"<\s*{_DSML_MARKER}\s*parameter(?P<attrs>[^>]*)>(?P<value>.*?)"
    rf"</\s*{_DSML_MARKER}\s*parameter\s*>",
    re.IGNORECASE | re.DOTALL,
)
_DSML_ATTRIBUTE_RE = re.compile(
    r"""(?P<name>[A-Za-z_][\w.-]*)\s*=\s*(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)')"""
)


def _dsml_attributes(raw: str) -> dict[str, str] | None:
    """Parse one strict DSML attribute list without accepting stray syntax."""
    attrs: dict[str, str] = {}
    cursor = 0
    for match in _DSML_ATTRIBUTE_RE.finditer(str(raw or "")):
        if str(raw or "")[cursor:match.start()].strip():
            return None
        name = str(match.group("name") or "")
        if name in attrs:
            return None
        value = match.group("double")
        if value is None:
            value = match.group("single")
        attrs[name] = unescape(str(value or ""))
        cursor = match.end()
    if str(raw or "")[cursor:].strip():
        return None
    return attrs


def _recover_dsml_tool_calls(content: str, tools: list[dict] | None) -> list[Any]:
    """Normalize a complete DeepSeek DSML envelope into guarded tool calls.

    Recovery is deliberately strict: the response must contain only one DSML
    tool-call envelope, every requested tool must have been offered in this
    provider request, and all attributes/parameters must parse completely.
    The resulting calls still pass through MO's normal sandbox, taskboard, and
    local-extension dispatch gate.
    """
    text = str(content or "")
    if not text.strip() or len(text) > 65_536:
        return []
    offered = {
        str((tool.get("function") or {}).get("name") or "")
        for tool in (tools or [])
        if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
    }
    offered.discard("")
    if not offered:
        return []
    envelope = _DSML_TOOL_CALLS_RE.fullmatch(text)
    if envelope is None:
        return []

    body = str(envelope.group("body") or "")
    calls: list[Any] = []
    cursor = 0
    for invoke in _DSML_INVOKE_RE.finditer(body):
        if body[cursor:invoke.start()].strip():
            return []
        attrs = _dsml_attributes(invoke.group("attrs"))
        if attrs is None or set(attrs) != {"name"}:
            return []
        tool_name = str(attrs.get("name") or "")
        if tool_name not in offered:
            return []

        arguments: dict[str, Any] = {}
        parameter_body = str(invoke.group("body") or "")
        parameter_cursor = 0
        for parameter in _DSML_PARAMETER_RE.finditer(parameter_body):
            if parameter_body[parameter_cursor:parameter.start()].strip():
                return []
            parameter_attrs = _dsml_attributes(parameter.group("attrs"))
            if (
                parameter_attrs is None
                or "name" not in parameter_attrs
                or set(parameter_attrs) - {"name", "string"}
            ):
                return []
            parameter_name = str(parameter_attrs.get("name") or "")
            if not parameter_name or parameter_name in arguments:
                return []
            raw_value = unescape(str(parameter.group("value") or "")).strip()
            string_flag = str(parameter_attrs.get("string") or "").lower()
            if string_flag == "true":
                value: Any = raw_value
            elif string_flag in {"", "false"}:
                try:
                    value = json.loads(raw_value)
                except (TypeError, ValueError):
                    return []
            else:
                return []
            arguments[parameter_name] = value
            parameter_cursor = parameter.end()
        if parameter_body[parameter_cursor:].strip():
            return []

        calls.append(
            make_tool_call(
                call_id="",
                name=tool_name,
                arguments=json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
            )
        )
        if len(calls) > 16:
            return []
        cursor = invoke.end()
    if not calls or body[cursor:].strip():
        return []
    return calls


def provider_accepts_image_input(provider: Any) -> bool:
    """Return whether ``provider`` can consume image parts in model input.

    Explicit capabilities and vision flags are authoritative, including false.
    An unspecified flag can use known official model capabilities, evaluated
    against the current model so cloned provider choices do not inherit them.
    """
    capabilities = getattr(provider, "capabilities", None)
    if isinstance(capabilities, dict) and "image_input" in capabilities:
        return bool(capabilities["image_input"])
    declared = getattr(provider, "supports_vision", None)
    if declared is not None:
        return bool(declared)
    try:
        host = urllib.parse.urlsplit(str(getattr(provider, "base_url", "") or "")).hostname
    except ValueError:
        return False
    model = str(getattr(provider, "model", "") or "").lower()
    return host == "api.deepseek.com" and model in {
        "deepseek-flash", "deepseek-v4-flash", "deepseek-v4-flash-vision-exp",
    }


def complete_provider(provider: Any, *, cancel_event: object = None, **kwargs: Any):
    """Call one provider while forwarding cancellation only when supported.

    Provider adapters and test doubles do not all expose ``cancel_event``. This
    is the single compatibility owner for main and auxiliary provider calls, so
    a cancellable adapter can stop transport work without breaking older ones.
    """
    if getattr(cancel_event, "is_set", lambda: False)():
        return SimpleResponse()
    complete = provider.complete
    implementation = getattr(complete, "__func__", complete)
    custom = not any(implementation is method for method in _TRANSPORT_COUNTED_COMPLETIONS)
    if custom:
        reserve_provider_request(
            provider_name=str(getattr(provider, "name", "") or ""),
            model_name=str(getattr(provider, "model", "") or ""),
        )
    if cancel_event is not None:
        try:
            parameters = inspect.signature(complete).parameters
        except (TypeError, ValueError):
            parameters = {}
        if (
            "cancel_event" in parameters
            or any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
        ):
            kwargs["cancel_event"] = cancel_event
    state = current_provider_request_limit()
    if custom and state is not None:
        # A custom adapter may delegate its first attempt to a native adapter.
        # Reuse this reservation once; native retries must reserve separately.
        with provider_request_overrides({
            "_request_reservation": {"state": state, "available": True},
        }):
            return complete(**kwargs)
    return complete(**kwargs)


class BaseProvider:
    name = "base"
    api_mode = "unknown"
    mo_desktop_model = ""
    # Name of the provider request field that receives Agent.max_tokens. Empty
    # means the adapter accepts the shared argument but does not transmit a
    # provider-side output limit (for example the private Codex Responses lane).
    output_token_limit_field = ""
    # Vision: can this provider actually SEE images sent in the message stream
    # (canonical computer screen observation)? False by default — a provider opts in only
    # when its API path delivers image parts to a vision-capable model.
    supports_vision = False
    # Optional provider metadata (`capabilities:` config block). The canonical
    # provider_accepts_image_input predicate reads image_input; other adapters
    # may consume their own explicitly named metadata.
    capabilities: dict = {}

    def __init__(self, model: str):
        self.model = model
        self.base_url = ""

    def stream(self, *, messages: list[dict], tools: list[dict], temperature: float, max_tokens: int):
        raise NotImplementedError

    def complete(self, *, messages: list[dict], tools: list[dict], temperature: float, max_tokens: int, on_token: object = None):
        raise NotImplementedError


def requested_output_token_limit(provider: object, configured: object) -> tuple[int | None, str]:
    """Return the transmitted output-token limit and its provider field.

    ``Agent.max_tokens`` is a shared adapter argument, not proof that every
    transport sends a provider-side limit. Callers use this contract for honest
    status/telemetry only; it never changes a request payload.
    """
    try:
        limit = int(configured or 0)
    except (TypeError, ValueError):
        limit = 0
    field = str(getattr(provider, "output_token_limit_field", "") or "").strip()
    return (limit if field else None), field


class ChatCompletionsProvider(BaseProvider):
    """OpenAI-compatible chat completions provider."""

    api_mode = "chat_completions"
    output_token_limit_field = "max_tokens"

    def __init__(self, *, name: str, base_url: str, api_key: str, model: str, timeout: float = 60.0, headers: dict[str, str] | None = None, reasoning_effort: str | None = None, supports_vision: bool | None = None, thinking_disabled: bool = False, capabilities: dict | None = None):
        super().__init__(model=model)
        self.name = name
        # Preserve optional provider metadata. An image_input entry overrides
        # supports_vision; when omitted, `vision: true` remains sufficient.
        self.capabilities = dict(capabilities or {})
        # Opt-in per provider config (`thinking_disabled: true`). Sends
        # `thinking: {type: disabled}` for compatible providers so structured
        # calls do not burn max_tokens on reasoning and return empty content.
        self.thinking_disabled = bool(thinking_disabled)
        # Keep omission distinct from an explicit opt-in/out. The image predicate
        # can infer a known official model while unknown routes remain text-only.
        self.supports_vision = None if supports_vision is None else bool(supports_vision)
        self.base_url = base_url
        self.timeout = float(timeout or 60.0)
        # Optional per-provider OpenAI-style reasoning_effort. Default None → NOT sent,
        # so providers that reject unknown params (unverified support) are unaffected.
        # Operators enable it only for providers known to accept it (o-series, GLM-5.2, etc.).
        self.reasoning_effort = str(reasoning_effort).strip().lower() if reasoning_effort else None
        if not _openai_available():
            raise RuntimeError("openai package not installed. Run: pip install -r requirements.txt")
        # A profile may configure many fallbacks while a normal turn uses only
        # one. Keep their routing metadata ready, but defer the ~950-module SDK
        # tree and each concrete client until that provider's first request.
        self._api_key = api_key
        self._default_headers = dict(headers or {}) or None
        self._opencode_session = ""
        if urllib.parse.urlparse(base_url).hostname == "opencode.ai":
            # Direct callers without an Agent seed still need a stable private
            # routing identity for this provider instance.
            from uuid import uuid4

            self._opencode_session = uuid4().hex
        self.client = None
        self._client_lock = threading.Lock()

    def _ensure_client(self):
        client = getattr(self, "client", None)
        if client is not None:
            return client
        lock = getattr(self, "_client_lock", None)
        if lock is None:  # Compatibility for narrowly constructed test doubles.
            lock = threading.Lock()
            self._client_lock = lock
        with lock:
            client = getattr(self, "client", None)
            if client is not None:
                return client
            openai_cls = _ensure_openai()
            if openai_cls is None:
                raise RuntimeError("openai package not installed. Run: pip install -r requirements.txt")
            client = openai_cls(
                api_key=self._api_key,
                base_url=self.base_url,
                default_headers=self._default_headers,
                timeout=self.timeout,
                max_retries=0,
            )
            self.client = client
            return client

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
    def _normalize_messages(messages: list[dict], accepts_image_input: bool = False) -> list[dict]:
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
            if image_urls and accepts_image_input:
                # tool message: text only (valid); image rides in a trailing user turn.
                out.append({**msg, "content": flat or "[image]"})
                out.append({
                    "role": "user",
                    "content": [{"type": "text", "text": "[screen capture]"}]
                    + [{"type": "image_url", "image_url": {"url": u}} for u in image_urls],
                })
                continue
            if image_urls:
                flat = (
                    flat
                    + "\n[image omitted: this provider route does not accept image input; "
                    "select or configure an image-capable provider route]"
                ).strip()
            out.append({**msg, "content": flat})
        return out

    def _completion_request(self, *, messages: list[dict], tools: list[dict], temperature: float, max_tokens: int) -> dict:
        """Build the common wire payload for streamed and ordinary completions."""
        reasoning_effort = _request_option("reasoning_effort", self.reasoning_effort)
        thinking_disabled = bool(_request_option("thinking_disabled", self.thinking_disabled))
        request = {
            "model": self.model,
            "messages": self._normalize_messages(messages, provider_accepts_image_input(self)),
            "max_tokens": max_tokens,
            "timeout": self.timeout,
        }
        if self._opencode_session:
            seed = str(_request_option("prompt_cache_seed", "") or self._opencode_session)
            request["extra_headers"] = {
                "User-Agent": "MO-Agent",
                "x-opencode-session": "mo-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:48],
            }
        if temperature > 0:
            request["temperature"] = temperature
        if tools:
            request["tools"] = tools
            request["tool_choice"] = "auto"
        direct_deepseek = urllib.parse.urlsplit(str(getattr(self, "base_url", "") or "")).hostname == "api.deepseek.com"
        if reasoning_effort and not (thinking_disabled and direct_deepseek):
            request["reasoning_effort"] = reasoning_effort
        if thinking_disabled:
            request.setdefault("extra_body", {})["thinking"] = {"type": "disabled"}
        return request

    def stream(self, *, messages: list[dict], tools: list[dict], temperature: float, max_tokens: int):
        check_provider_request_limit()
        with monitor_phase("provider_prepare", monitor=_request_option("monitor"), provider=self.name):
            client = _request_client(self._ensure_client())
            request = self._completion_request(messages=messages, tools=tools, temperature=temperature, max_tokens=max_tokens)
        request["stream"] = True
        request["stream_options"] = {"include_usage": True}
        try:
            return self._create_completion(client, request)
        except Exception as exc:
            if "stream_options" not in str(exc).lower():
                raise
            request.pop("stream_options", None)
            return self._create_completion(client, request)

    def _create_completion(self, client: Any, request: dict[str, Any]):
        reserve_provider_request(provider_name=self.name, model_name=self.model)
        _capture_request_metadata(self.name, self.model, request, client=client)
        with monitor_phase(
            "provider_transport", monitor=_request_option("monitor"), provider=self.name,
            operation="stream_open" if request.get("stream") else "complete",
        ):
            response = client.chat.completions.create(**request)
        _capture_response_headers(self.name, self.model, response)
        return response

    def complete(self, *, messages: list[dict], tools: list[dict], temperature: float, max_tokens: int, on_token: object = None):
        if on_token is None:
            check_provider_request_limit()
            with monitor_phase("provider_prepare", monitor=_request_option("monitor"), provider=self.name):
                client = _request_client(self._ensure_client())
                request = self._completion_request(messages=messages, tools=tools, temperature=temperature, max_tokens=max_tokens)
            response = self._create_completion(client, request)
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
            recovered_calls = []
            if not (getattr(message, "tool_calls", None) or []):
                recovered_calls = _recover_dsml_tool_calls(
                    str(getattr(message, "content", "") or ""),
                    tools,
                )
            if recovered_calls:
                normalized = SimpleResponse(
                    content="",
                    tool_calls=recovered_calls,
                    usage=getattr(response, "usage", None),
                    finish_reason="tool_calls",
                    reasoning_content=getattr(message, "reasoning_content", None),
                )
                normalized.tool_call_recovery = "deepseek_dsml"
                return normalized
            return message

        # Streaming mode
        stream_started = time.monotonic()
        stream_generator = self.stream(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        content_accum = []
        reasoning_accum = []
        tool_calls_accum = []
        design_stream_keys: set[str] = set()
        finish_reason = "stop"
        usage = None

        for chunk_index, chunk in enumerate(stream_generator):
            if chunk_index == 0:
                try:
                    from ..runtime.backend_monitor import get_monitor

                    monitor = _request_option("monitor") or get_monitor()
                    if monitor is not None:
                        monitor.emit("provider_stream", {
                            "phase": "first_chunk", "api": "chat_completions",
                            "provider": self.name, "model": self.model,
                            "elapsed_seconds": round(time.monotonic() - stream_started, 3),
                        })
                except Exception:
                    pass
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
                    stream_key = f"chat:{id(tool_calls_accum)}:{idx}"
                    design_stream_keys.add(stream_key)

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
                        try:
                            from core.design.streaming import feed_tool_delta

                            feed_tool_delta(stream_key, name_delta=name or "", arguments_delta=args or "")
                        except Exception:
                            pass

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

        if design_stream_keys:
            try:
                from core.design.streaming import finish_tool_stream

                for stream_key in design_stream_keys:
                    finish_tool_stream(stream_key)
            except Exception:
                pass

        final_content = "".join(content_accum)
        recovered_calls = (
            _recover_dsml_tool_calls(final_content, tools)
            if not final_tool_calls
            else []
        )
        if recovered_calls:
            final_tool_calls = recovered_calls
            final_content = ""
            finish_reason = "tool_calls"

        normalized = SimpleResponse(
            content=final_content,
            tool_calls=final_tool_calls,
            usage=usage,
            finish_reason=finish_reason,
            reasoning_content="".join(reasoning_accum) if reasoning_accum else None,
        )
        if recovered_calls:
            normalized.tool_call_recovery = "deepseek_dsml"
        return normalized


class MockProvider(BaseProvider):
    """Deterministic local provider used only when config selects type: mock."""

    name = "mock"
    api_mode = "mock"

    def __init__(self, *, name: str = "mock", model: str = "mock-model"):
        super().__init__(model=model)
        self.name = name

    def stream(self, *, messages: list[dict], tools: list[dict], temperature: float, max_tokens: int):
        reserve_provider_request(provider_name=self.name, model_name=self.model)
        for token in self._answer(messages).split(" "):
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=token + " "), finish_reason="")],
                usage=None,
            )
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=""), finish_reason="stop")], usage=None)

    def complete(self, *, messages: list[dict], tools: list[dict], temperature: float, max_tokens: int, on_token: object = None):
        reserve_provider_request(provider_name=self.name, model_name=self.model)
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
    # behind Codex genuinely sees the screen (canonical computer observation).
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
        # httpx accepts a float directly; constructing Timeout here imported the
        # transport stack before MO had made any request.
        self.timeout = self.timeout_seconds
        access_token = self._read_access_token()
        self.access_token = access_token
        self.default_headers = self._codex_headers(access_token)
        self._auth_lock = threading.Lock()
        self._refresh_attempted_token = ""
        self._auth_file_stamp = self._current_auth_file_stamp()
        # Codex requests below already use the raw Responses SSE endpoint. The
        # former OpenAI SDK client was never read after construction.
        self.client = None

    def _current_auth_file_stamp(self) -> tuple[int, int] | None:
        try:
            stat = self.auth_path.stat()
            return int(stat.st_mtime_ns), int(stat.st_size)
        except OSError:
            return None

    def _set_access_token(self, token: str) -> None:
        token = str(token or "").strip()
        if not token:
            return
        self.access_token = token
        self.default_headers = self._codex_headers(token)
        self._auth_file_stamp = self._current_auth_file_stamp()

    def _reload_access_token_from_disk(self, *, force: bool = False) -> bool:
        """Adopt a token written by another Codex process without exposing it."""
        stamp = self._current_auth_file_stamp()
        if not force and stamp == getattr(self, "_auth_file_stamp", None):
            return False
        try:
            token = self._read_access_token()
        except Exception:
            # Atomic writers should make this rare, but an unreadable external
            # auth update must not erase the last usable in-memory credential.
            return False
        changed = token != str(getattr(self, "access_token", "") or "")
        self._set_access_token(token)
        return changed

    def _ensure_access_token(self) -> None:
        """Reload shared auth and refresh a provably expired token at request time."""
        lock = getattr(self, "_auth_lock", None)
        if lock is None:  # Compatibility for narrowly constructed test doubles.
            lock = threading.Lock()
            self._auth_lock = lock
        with lock:
            self._reload_access_token_from_disk()
            token = str(getattr(self, "access_token", "") or "")
            if not self._access_token_expired(token):
                return
            if getattr(self, "_refresh_attempted_token", "") == token:
                return
            self._refresh_attempted_token = token
            refreshed = self._refresh_access_token()
            if refreshed:
                self._set_access_token(refreshed)

    def _recover_rejected_access_token(self, rejected_token: str) -> bool:
        """Recover one server-rejected token from disk or OAuth refresh.

        Long-lived MO and Codex processes share ``auth.json``. A peer may have
        refreshed it after this provider was constructed, while a server can
        also reject a non-JWT token whose expiry cannot be predicted locally.
        The request caller retries at most once after this method succeeds.
        """
        lock = getattr(self, "_auth_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._auth_lock = lock
        with lock:
            current = str(getattr(self, "access_token", "") or "")
            if current and current != rejected_token:
                return True
            if self._reload_access_token_from_disk(force=True):
                return True
            if getattr(self, "_refresh_attempted_token", "") == rejected_token:
                return False
            self._refresh_attempted_token = rejected_token
            refreshed = self._refresh_access_token()
            if not refreshed or refreshed == rejected_token:
                return False
            self._set_access_token(refreshed)
            return True

    @staticmethod
    def _is_auth_rejection(status_code: int, detail: str) -> bool:
        """Recognize bounded HTTP or pre-output SSE authentication failures."""
        if status_code == 401:
            return True
        if status_code not in {0, 403}:
            return False
        return provider_error_kind(detail) == "auth"

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

        A plain string yields one text part. A list lets a turn mix text and
        images — image parts become
        ``input_image`` data-URI parts so vision-capable models can see them
        (computer-use screen observation). Assistant text uses ``output_text``.
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
    def _jsonable_response_value(cls, value: Any) -> Any:
        """Convert one Responses object to private, JSON-persistable state."""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, dict):
            return {
                str(key): cls._jsonable_response_value(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls._jsonable_response_value(item) for item in value]
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                return cls._jsonable_response_value(model_dump(mode="json", exclude_none=True))
            except TypeError:
                return cls._jsonable_response_value(model_dump())
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return cls._jsonable_response_value(to_dict())
        attributes = getattr(value, "__dict__", None)
        if isinstance(attributes, dict):
            return cls._jsonable_response_value({
                key: item
                for key, item in attributes.items()
                if not str(key).startswith("_")
            })
        return str(value)

    @classmethod
    def _jsonable_response_item(cls, item: Any) -> dict[str, Any] | None:
        normalized = cls._jsonable_response_value(item)
        return normalized if isinstance(normalized, dict) else None

    @staticmethod
    def _response_item_type(item: Any) -> str:
        return str(
            getattr(item, "type", None)
            or (item.get("type") if isinstance(item, dict) else "")
            or ""
        )

    @staticmethod
    def _response_item_call_id(item: Any) -> str:
        return str(
            getattr(item, "call_id", None)
            or getattr(item, "id", None)
            or (item.get("call_id") if isinstance(item, dict) else "")
            or (item.get("id") if isinstance(item, dict) else "")
            or ""
        )

    @classmethod
    def _to_instructions_and_input(
        cls,
        messages: list[dict],
        *,
        model: str = "",
    ) -> tuple[str, list[dict]]:
        from ..session.session import RESPONSES_MODEL_KEY, RESPONSES_OUTPUT_ITEMS_KEY

        system_parts: list[str] = []
        input_items: list[dict] = []
        pending_call_ids: list[str] = []
        leading_instructions = True
        for message_index, msg in enumerate(messages):
            role = msg.get("role")
            content = msg.get("content") or ""
            if role == "system" and leading_instructions:
                system_parts.append(str(content) if not isinstance(content, list) else "".join(p.get("text", "") for p in content if isinstance(p, dict)))
                continue
            leading_instructions = False
            if role in {"system", "user", "assistant"}:
                mapped_role = "assistant" if role == "assistant" else role
                replay_items: list[dict[str, Any]] = []
                if role == "assistant" and str(msg.get(RESPONSES_MODEL_KEY) or "") == str(model or ""):
                    replay_items = [
                        item
                        for raw_item in list(msg.get(RESPONSES_OUTPUT_ITEMS_KEY) or [])
                        if (item := cls._jsonable_response_item(raw_item)) is not None
                    ]
                replay_call_ids: set[str] = set()
                replay_has_message = False
                for replay_item in replay_items:
                    item_type = cls._response_item_type(replay_item)
                    replay_has_message = replay_has_message or item_type == "message"
                    if item_type == "function_call":
                        call_id = cls._response_item_call_id(replay_item)
                        if call_id:
                            replay_call_ids.add(call_id)
                            pending_call_ids.append(call_id)
                    input_items.append(replay_item)
                parts = cls._responses_content_parts(content, mapped_role)
                if parts and not replay_has_message:
                    input_items.append({
                        "role": mapped_role,
                        "content": parts,
                    })
                # Preserve prior tool calls as native Responses input items. They
                # must never be flattened into assistant prose, but their call_id
                # linkage is required for the following function_call_output.
                for call_index, tool_call in enumerate(msg.get("tool_calls") or []):
                    if isinstance(tool_call, dict):
                        function = tool_call.get("function") or {}
                        call_id = tool_call.get("id")
                    else:
                        function = getattr(tool_call, "function", None) or tool_call
                        call_id = getattr(tool_call, "id", None)
                    if isinstance(function, dict):
                        name = function.get("name")
                        arguments = function.get("arguments")
                    else:
                        name = getattr(function, "name", None)
                        arguments = getattr(function, "arguments", None)
                    if not name:
                        continue
                    stable_call_id = str(call_id or f"call_mo_{message_index}_{call_index}")
                    if stable_call_id in replay_call_ids:
                        continue
                    pending_call_ids.append(stable_call_id)
                    input_items.append({
                        "type": "function_call",
                        "call_id": stable_call_id,
                        "name": str(name),
                        "arguments": str(arguments or "{}"),
                    })
            elif role == "tool":
                call_id = str(msg.get("tool_call_id") or "")
                if call_id in pending_call_ids:
                    pending_call_ids.remove(call_id)
                elif not call_id and pending_call_ids:
                    call_id = pending_call_ids.pop(0)
                if not call_id:
                    # A sanitized session should never contain an orphan tool
                    # result. Keep conversion fail-closed if malformed stored data does.
                    continue
                if isinstance(content, list):
                    # Responses accepts an array of text/image/file objects as a
                    # function output, retaining both vision content and linkage.
                    output: Any = cls._responses_content_parts(content, "user")
                else:
                    output = str(content)
                input_items.append({
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
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
                "strict": bool(fn.get("strict", False)),
            })
        return converted

    @staticmethod
    def _prompt_cache_key(
        *,
        model: str,
        instructions: str,
        input_items: list[dict],
    ) -> str:
        """Return a private session partition key for Codex Responses.

        OpenAI documents this key as unnecessary for cache routing on GPT-5.6+.
        MO retains it across Codex models to keep session/surface accounting
        isolated without exposing prompt content; it is not treated as a
        universal token-saving control.
        """
        seed = str(_request_option("prompt_cache_seed", "") or "").strip()
        if seed:
            material = f"{model}\0{seed}"
        else:
            # Direct provider callers lack an Agent session seed. Hash a stable
            # prefix instead of exposing prompt content in the routing key.
            prefix = input_items[:1]
            material = json.dumps(
                [model, instructions, prefix],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        return "mo-" + hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()[:48]

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

    def stream(
        self,
        *,
        messages: list[dict],
        tools: list[dict],
        temperature: float,
        max_tokens: int,
        cancel_event: object = None,
    ):
        check_provider_request_limit()
        self._ensure_access_token()
        instructions, input_items = self._to_instructions_and_input(messages, model=self.model)
        response_tools = self._to_responses_tools(tools)
        reasoning_effort = _request_option("reasoning_effort", self.reasoning_effort)
        prompt_cache_key = self._prompt_cache_key(
            model=self.model,
            instructions=instructions,
            input_items=input_items,
        )
        prompt_cache_key_tag = prompt_cache_key[-12:]
        reasoning_context = "all_turns" if re.search(r"(?:^|[/_-])gpt-5\.6(?:$|[/_-])", self.model.lower()) else ""
        request: dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "input": input_items,
            "include": ["reasoning.encrypted_content"],
            "prompt_cache_key": prompt_cache_key,
            "store": False,
            "stream": True,
        }
        if response_tools:
            request["tools"] = response_tools
            request["tool_choice"] = "auto"
        if reasoning_effort or reasoning_context:
            request["reasoning"] = {}
            if reasoning_effort:
                request["reasoning"]["effort"] = reasoning_effort
            if reasoning_context:
                request["reasoning"]["context"] = reasoning_context

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
            if isinstance(response_payload, dict):
                response_data = dict(response_payload)
                if isinstance(response_data.get("usage"), dict):
                    response_data["usage"] = SimpleNamespace(**response_data["usage"])
                response_payload = SimpleNamespace(**response_data)
            return SimpleNamespace(
                type=etype,
                delta=payload.get("delta") or "",
                item=payload.get("item"),
                item_id=payload.get("item_id"),
                output_index=payload.get("output_index", 0),
                response=response_payload,
                error=payload.get("error"),
                prompt_cache_key_tag=prompt_cache_key_tag,
                reasoning_context=reasoning_context,
            )

        def _cancelled() -> bool:
            return bool(getattr(cancel_event, "is_set", lambda: False)())

        from ..runtime.backend_monitor import get_monitor
        from uuid import uuid4

        monitor = _request_option("monitor") or get_monitor()
        started = time.monotonic()
        stream_id = uuid4().hex[:12]
        last_event_at = None
        last_trace_at = started
        traced_phases: set[str] = set()
        counts = {"events_received": 0, "reasoning_items": 0, "output_text_chars": 0}
        completed = False

        def _trace_stream(phase: str, *, force: bool = False, **details: Any) -> None:
            nonlocal last_trace_at
            activity = _request_option("stream_activity")
            if (monitor is None or not getattr(monitor, "enabled", True)) and not callable(activity):
                return
            now = time.monotonic()
            if not force and phase in traced_phases and now - last_trace_at < 15:
                return
            last_trace_at = now
            traced_phases.add(phase)
            if callable(activity):
                try:
                    activity(phase)
                except Exception:
                    pass  # Presentation cannot interrupt a provider response.
            if monitor is None or not getattr(monitor, "enabled", True):
                return
            if phase == "request_started":
                try:
                    details.update(monitor.provider_request_metadata(request))
                except Exception:
                    pass
            # BackendMonitor owns best-effort writes and diagnostic failures.
            monitor.emit("provider_stream", {
                "stream_id": stream_id,
                "provider": self.name,
                "model": self.model,
                "reasoning_effort": reasoning_effort or "default",
                "phase": phase,
                "elapsed_seconds": round(now - started, 3),
                "last_event_age_seconds": round(now - last_event_at, 3) if last_event_at is not None else None,
                "completed": completed,
                **counts,
                **details,
            })

        def _events():
            nonlocal last_event_at, completed, started
            started = time.monotonic()
            phase = "streaming"
            failed = False
            _trace_stream("request_started", force=True)
            try:
                if _cancelled():
                    return
                for auth_attempt in range(2):
                    if _cancelled():
                        return
                    request_token = str(getattr(self, "access_token", "") or "")
                    headers = {
                        **getattr(self, "default_headers", {}),
                        "Authorization": f"Bearer {request_token}",
                        "Accept": "text/event-stream",
                        "Content-Type": "application/json",
                    }
                    reserve_provider_request(provider_name=self.name, model_name=self.model)
                    with _httpx().stream("POST", url, headers=headers, json=request, timeout=self.timeout, follow_redirects=True) as response:
                        _trace_stream("connected", force=True, http_status=response.status_code, auth_attempt=auth_attempt + 1)
                        if response.status_code >= 400:
                            detail = response.read().decode("utf-8", errors="replace")[:1000]
                            if (
                                auth_attempt == 0
                                and self._is_auth_rejection(response.status_code, detail)
                                and self._recover_rejected_access_token(request_token)
                            ):
                                continue
                            raise ProviderError(f"OpenAI Codex Responses stream failed ({response.status_code}): {detail}")
                        emitted_event = False
                        try:
                            for event in self._iter_sse_response(
                                response,
                                event_from_sse=_event_from_sse,
                                cancelled=_cancelled,
                                cancel_event=cancel_event,
                            ):
                                emitted_event = True
                                last_event_at = time.monotonic()
                                counts["events_received"] += 1
                                etype = str(getattr(event, "type", "") or "")
                                if etype in {"response.created", "response.in_progress"}:
                                    phase = "accepted"
                                elif etype == "response.output_item.added":
                                    item_type = self._response_item_type(getattr(event, "item", None))
                                    if item_type == "reasoning":
                                        counts["reasoning_items"] += 1
                                        phase = "reasoning"
                                    elif item_type == "function_call":
                                        phase = "tool_arguments"
                                elif etype == "response.output_text.delta":
                                    counts["output_text_chars"] += len(str(getattr(event, "delta", "") or ""))
                                    phase = "output"
                                elif etype == "response.completed":
                                    completed = True
                                    phase = "completed"
                                _trace_stream(phase, force=completed)
                                event.stream_id = stream_id
                                yield event
                            if not completed and not _cancelled():
                                raise _httpx().RemoteProtocolError("Responses stream ended before response.completed")
                        except ProviderError as exc:
                            if (
                                auth_attempt == 0
                                and not emitted_event
                                and self._is_auth_rejection(0, str(exc))
                                and self._recover_rejected_access_token(request_token)
                            ):
                                continue
                            raise
                        return
            except Exception as exc:
                failed = True
                _trace_stream("error", force=True, error_type=type(exc).__name__)
                raise
            finally:
                if not completed and not failed:
                    _trace_stream("cancelled" if _cancelled() else "closed", force=True)

        return _events()

    def _iter_sse_response(
        self,
        response: Any,
        *,
        event_from_sse: Any,
        cancelled: Any,
        cancel_event: object = None,
    ):
        """Yield one successful Responses SSE body with bounded cancellation."""
        event_type = ""
        data_lines: list[str] = []
        try:
            from .provider_capacity import get_capacity
            capacity = get_capacity()
            capacity.record_success(self.name, self.model)
            capacity.record_headers(self.name, response.headers, self.model)
        except Exception:
            pass
        try:
            from . import codex_usage
            codex_usage.record_from_headers(response.headers)
        except Exception:
            pass
        watcher_stop = threading.Event()
        watcher = None
        close_response = getattr(response, "close", None)
        if cancel_event is not None and callable(close_response):
            def _interrupt_cancelled_response() -> None:
                while not watcher_stop.wait(0.05):
                    if cancelled():
                        try:
                            close_response()
                        except Exception:
                            pass
                        return

            watcher = threading.Thread(
                target=_interrupt_cancelled_response,
                name="mo-provider-cancel",
                daemon=True,
            )
            watcher.start()
        try:
            for line in response.iter_lines():
                if cancelled():
                    return
                if isinstance(line, bytes):
                    line = line.decode("utf-8", errors="replace")
                line = str(line)
                if not line:
                    event = event_from_sse(event_type, data_lines)
                    event_type = ""
                    data_lines = []
                    if event is not None:
                        yield event
                        if getattr(event, "type", "") == "response.completed":
                            return
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
            if cancelled():
                return
            event = event_from_sse(event_type, data_lines)
            if event is not None:
                yield event
        except Exception:
            if not cancelled():
                raise
        finally:
            watcher_stop.set()
            if watcher is not None and watcher is not threading.current_thread():
                watcher.join(timeout=0.25)

    def complete(
        self,
        *,
        messages: list[dict],
        tools: list[dict],
        temperature: float,
        max_tokens: int,
        on_token: object = None,
        cancel_event: object = None,
    ):
        collected_text: list[str] = []
        tool_calls: list[Any] = []
        response_items: list[dict[str, Any]] = []
        response_item_indexes: dict[str, int] = {}
        output_item_phases: dict[str, str] = {}
        tool_call_keys: set[tuple[str, str, str]] = set()
        usage = None
        response_id = ""
        prompt_cache_key_tag = ""
        reasoning_context = ""
        transport_resumes = 0
        can_resume = bool(_request_option("resume_reasoning_on_disconnect")) and not tools and on_token is None
        last_stream_id = ""

        def _events():
            nonlocal transport_resumes
            from ..session.session import RESPONSES_MODEL_KEY, RESPONSES_OUTPUT_ITEMS_KEY

            replayed_count = 0
            call_messages = messages
            started = time.monotonic()
            while True:
                try:
                    yield from self.stream(
                        messages=call_messages, tools=tools, temperature=temperature,
                        max_tokens=max_tokens, cancel_event=cancel_event,
                    )
                    return
                except (_httpx().RemoteProtocolError, _httpx().ReadError, _httpx().ReadTimeout) as exc:
                    # PRT has not exposed this buffered draft. Reuse the native
                    # completed-item accumulator, discard unfinished JSON, and
                    # resume only when reasoning advanced since the last replay.
                    # A second failure at the same checkpoint is terminal.
                    if (
                        not can_resume
                        or getattr(cancel_event, "is_set", lambda: False)()
                        or len(response_items) <= replayed_count
                        or any(item.get("type") != "reasoning" or not item.get("encrypted_content") for item in response_items)
                    ):
                        raise
                    replayed_count = len(response_items)
                    transport_resumes += 1
                    call_messages = messages + [{
                        "role": "assistant", "content": "",
                        RESPONSES_MODEL_KEY: self.model,
                        RESPONSES_OUTPUT_ITEMS_KEY: list(response_items),
                    }]
                    collected_text.clear()
                    output_item_phases.clear()
                    from ..runtime.backend_monitor import get_monitor
                    monitor = _request_option("monitor") or get_monitor()
                    if monitor is not None:
                        monitor.emit("provider_stream", {
                            "stream_id": last_stream_id, "phase": "resuming",
                            "provider": self.name, "model": self.model,
                            "reasoning_effort": _request_option("reasoning_effort", self.reasoning_effort) or "default",
                            "elapsed_seconds": round(time.monotonic() - started, 3),
                            "completed": False, "error_type": type(exc).__name__,
                            "replayed_reasoning_items": replayed_count,
                            "transport_resumes": transport_resumes,
                        })

        def _record_output_item(raw_item: Any) -> None:
            item = self._jsonable_response_item(raw_item)
            if item is None:
                return
            item_type = self._response_item_type(item)
            item_id = str(item.get("id") or item.get("call_id") or "")
            item_key = (
                f"{item_type}:{item_id}"
                if item_type and item_id
                else json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
            )
            if item_key in response_item_indexes:
                response_items[response_item_indexes[item_key]] = item
            else:
                response_item_indexes[item_key] = len(response_items)
                response_items.append(item)
            tc = self._tool_call_from_responses_item(item)
            if tc:
                fn = getattr(tc, "function", None)
                tc_key = (
                    str(getattr(tc, "id", "") or ""),
                    str(getattr(fn, "name", "") or ""),
                    str(getattr(fn, "arguments", "") or ""),
                )
                if tc_key not in tool_call_keys:
                    tool_call_keys.add(tc_key)
                    tool_calls.append(tc)

        for event in _events():
            etype = str(getattr(event, "type", None) or "")
            last_stream_id = str(getattr(event, "stream_id", "") or last_stream_id)
            prompt_cache_key_tag = str(getattr(event, "prompt_cache_key_tag", "") or prompt_cache_key_tag)
            reasoning_context = str(getattr(event, "reasoning_context", "") or reasoning_context)
            if etype == "response.output_text.delta":
                delta_text = str(getattr(event, "delta", "") or "")
                collected_text.append(delta_text)
                item_id = str(getattr(event, "item_id", None) or "")
                output_index = str(getattr(event, "output_index", 0))
                phase = output_item_phases.get(item_id) or output_item_phases.get(output_index, "")
                if on_token and delta_text and phase != "commentary":
                    on_token(delta_text)
            elif etype == "response.output_item.added":
                item = getattr(event, "item", None)
                item_id = str(
                    getattr(item, "id", None)
                    or (item.get("id") if isinstance(item, dict) else None)
                    or getattr(event, "item_id", None)
                    or getattr(event, "output_index", 0)
                )
                normalized_item = self._jsonable_response_item(item) or {}
                if normalized_item.get("type") == "function_call":
                    can_resume = False
                phase = str(normalized_item.get("phase") or "")
                if phase:
                    output_item_phases[item_id] = phase
                    output_item_phases[str(getattr(event, "output_index", 0))] = phase
                name = str(
                    getattr(item, "name", None)
                    or (item.get("name") if isinstance(item, dict) else None)
                    or ""
                )
                if name:
                    try:
                        from core.design.streaming import feed_tool_delta

                        feed_tool_delta(f"responses:{item_id}", name_delta=name)
                    except Exception:
                        pass
            elif etype == "response.function_call_arguments.delta":
                item_id = str(getattr(event, "item_id", None) or getattr(event, "output_index", 0))
                delta = str(getattr(event, "delta", None) or "")
                try:
                    from core.design.streaming import feed_tool_delta

                    feed_tool_delta(f"responses:{item_id}", arguments_delta=delta)
                except Exception:
                    pass
            elif etype == "response.output_item.done":
                item = getattr(event, "item", None)
                item_id = str(
                    getattr(item, "id", None)
                    or (item.get("id") if isinstance(item, dict) else None)
                    or getattr(event, "item_id", None)
                    or getattr(event, "output_index", 0)
                )
                _record_output_item(item)
                try:
                    from core.design.streaming import finish_tool_stream

                    finish_tool_stream(f"responses:{item_id}")
                except Exception:
                    pass
            elif etype == "response.completed":
                response_payload = getattr(event, "response", None)
                reasoning = (response_payload.get("reasoning") if isinstance(response_payload, dict)
                             else getattr(response_payload, "reasoning", None))
                effective_context = (reasoning.get("context") if isinstance(reasoning, dict)
                                     else getattr(reasoning, "context", None))
                if effective_context in {"current_turn", "all_turns"}:
                    reasoning_context = effective_context
                response_id = str(
                    getattr(response_payload, "id", None)
                    or (response_payload.get("id") if isinstance(response_payload, dict) else None)
                    or response_id
                )
                usage = getattr(response_payload, "usage", None)
                if usage is None and isinstance(response_payload, dict):
                    usage = response_payload.get("usage")
                    if isinstance(usage, dict):
                        usage = SimpleNamespace(**usage)
                output = getattr(response_payload, "output", None)
                if output is None and isinstance(response_payload, dict):
                    output = response_payload.get("output")
                for item in list(output or []):
                    _record_output_item(item)
            # Important: do not append arbitrary event.delta/content here.
            # Codex Responses emits function-call argument deltas as JSON text;
            # treating those as assistant text leaks raw tool payloads into chat.
        final_answer_text = "".join(
            str(part.get("text") or "")
            for item in response_items
            if item.get("type") == "message" and item.get("phase") == "final_answer"
            for part in list(item.get("content") or [])
            if isinstance(part, dict)
        )
        return SimpleResponse(
            content=final_answer_text or "".join(collected_text),
            tool_calls=tool_calls,
            usage=usage,
            response_items=response_items,
            response_id=response_id,
            reasoning_context=reasoning_context,
            prompt_cache_key_tag=prompt_cache_key_tag,
            transport_resumes=transport_resumes,
        )


# Keep exact implementations, not classes: a scripted replacement on a builtin
# provider still counts through complete_provider instead of bypassing the cap.
_TRANSPORT_COUNTED_COMPLETIONS = (
    ChatCompletionsProvider.complete, MockProvider.complete, CodexOAuthProvider.complete,
)


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
        import yaml
    except ImportError as exc:
        raise ConfigLoadError(path, "could not import yaml parser") from exc
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
    _reject_noncanonical_secret_sources(config, path)
    config["_config_path"] = path
    return config


def _reject_noncanonical_secret_sources(config: dict, path: str) -> None:
    """Fail closed on config fields that used to carry or redirect secrets."""
    for provider in config.get("providers") or []:
        if not isinstance(provider, dict):
            continue
        if str(provider.get("api_key") or provider.get("_api_key") or "").strip():
            name = str(provider.get("name") or "provider").strip()
            raise ConfigLoadError(
                path,
                f"provider {name} uses unsupported inline api_key; "
                "set api_key_env and store its value in credentials/providers.env",
            )
    image = config.get("image") if isinstance(config.get("image"), dict) else {}
    if str(image.get("api_key") or "").strip():
        raise ConfigLoadError(
            path,
            "image.api_key is unsupported; set image.api_key_env and store its "
            "value in credentials/providers.env",
        )
    credentials = (
        config.get("credentials")
        if isinstance(config.get("credentials"), dict)
        else {}
    )
    alternate_names = (
        "providers_file",
        "telegram_file",
    )
    if any(credentials.get(name) for name in alternate_names):
        raise ConfigLoadError(
            path,
            "credential file overrides are unsupported; use the canonical "
            "service file under the MO profile",
        )
    telegram = (
        config.get("telegram")
        if isinstance(config.get("telegram"), dict)
        else {}
    )
    if telegram.get("secret_files"):
        raise ConfigLoadError(
            path,
            "telegram.secret_files is unsupported; use credentials/telegram.env",
        )
    for server in ((config.get("mcp") or {}).get("servers") or []):
        if isinstance(server, dict) and server.get("secret_files"):
            name = str(server.get("name") or "MCP service").strip()
            raise ConfigLoadError(
                path,
                f"{name} secret_files is unsupported; use "
                "credentials/mcp/<service>.env with secret_env",
            )


def _resolve_api_key(provider_cfg: dict, config: dict | None = None) -> str:
    api_key_env = provider_cfg.get("api_key_env")
    return resolve_secret(
        str(api_key_env or ""),
        config=config,
        service="providers",
    ) if api_key_env else ""


def _local_provider_placeholder(provider_cfg: dict) -> str:
    """Return the non-secret SDK placeholder permitted for loopback providers."""
    try:
        host = (urllib.parse.urlsplit(str(provider_cfg.get("base_url") or "")).hostname or "").lower()
    except ValueError:
        return ""
    return "local-provider" if host in {"127.0.0.1", "localhost", "::1"} else ""


def _provider_from_config(provider_cfg: dict, model: str, config: dict | None = None) -> BaseProvider:
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
    api_key = _resolve_api_key(provider_cfg, config) or _local_provider_placeholder(provider_cfg)
    if not api_key:
        logical_name = str(api_key_env or "").strip() or "<api_key_env not configured>"
        raise ProviderError(
            f"Canonical provider credential not found for {name}. key={logical_name}"
        )
    return ChatCompletionsProvider(
        name=name,
        base_url=provider_cfg["base_url"],
        api_key=api_key,
        model=model,
        timeout=float(provider_cfg.get("timeout", 60.0) or 60.0),
        headers=provider_cfg.get("_headers") or provider_cfg.get("headers"),
        reasoning_effort=provider_cfg.get("reasoning_effort"),
        supports_vision=bool(provider_cfg["vision"]) if "vision" in provider_cfg else None,
        thinking_disabled=bool(provider_cfg.get("thinking_disabled", False)),
        capabilities=provider_cfg.get("capabilities"),
    )


def first_vision_provider_index(
    providers,
    *,
    can_accept=None,
    route_can_accept=None,
) -> int | None:
    """Index of the first image-input provider that has capacity, or None.

    Used to route a screenshot from ``computer_observe kind=screen`` to a provider that can actually
    SEE it (e.g. openai-codex) when the active provider is text-only.
    """
    for index, provider in enumerate(providers or []):
        if not provider_accepts_image_input(provider):
            continue
        if route_can_accept is not None and not route_can_accept(
            getattr(provider, "name", ""),
            getattr(provider, "model", ""),
        ):
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
    agent_cfg = config.get("agent", {})
    model_cfg = config.get("model", {})

    providers_cfg = list(config.get("providers") or [])
    providers: list[BaseProvider] = []
    setup_errors: list[str] = []

    for pcfg in providers_cfg:
        try:
            model = pcfg.get("model") or model_cfg.get("default")
            provider = _provider_from_config(pcfg, model, config)
            provider.mo_desktop_model = str(pcfg.get("mo_desktop_model") or "").strip()
            providers.append(provider)
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
        "temperature": agent_cfg.get("temperature", DEFAULT_PREFERENCES["agent.temperature"]),
        "max_tokens": agent_cfg.get("max_tokens", DEFAULT_PREFERENCES["agent.max_tokens"]),
    }


# ── Error utilities ────────────────────────────────────────────────

def is_rate_limit_error(error_msg: str) -> bool:
    e = str(error_msg or "").lower()
    markers = ("429", "too many requests", "rate limit", "rate_limit", "concurrency", "pace your requests", "usage limit", "quota exceeded", "billing quota")
    return any(marker in e for marker in markers)


def provider_error_kind(error_msg: str) -> str | None:
    """Classify one provider failure without inventing an unobserved cause.

    The distinction matters operationally: temporary capacity/service failures
    may receive one bounded same-route retry, while quota, billing, auth, and
    permission or policy failures require another route or operator action.  An
    explicit rejection wins over less-specific metadata such as a generic HTTP
    status, ``server_error``, or ``concurrency`` field so diagnostics describe
    the evidence the operator actually received.
    """
    e = str(error_msg or "").lower()
    if not e:
        return None
    if _is_provider_policy_rejection(e):
        return "policy"
    if is_context_overflow_error(e):
        return "context_overflow"
    if "402" in e or "insufficient balance" in e or "insufficient_balance" in e:
        return "balance"
    if any(marker in e for marker in (
        "billing quota", "quota exceeded", "insufficient_quota",
        "usage limit", "credit balance", "credit limit",
    )):
        return "quota"
    if re.search(
        r"(?:\b401\b|unauthori[sz]ed"
        r"|authentication(?:[_\s-]+(?:failed|error|required|token))"
        r"|invalid[_\s-]*(?:access[_\s-]*)?token"
        r"|expired[_\s-]*(?:access[_\s-]*)?token"
        r"|(?:access[_\s-]*)?token[_\s-]*(?:is[_\s-]*)?(?:expired|invalid|revoked)"
        # A sign-in whose scopes or auth context the backend rejects (Codex sends this as a
        # server_error event: "native turn auth context mismatch: scopes", 2026-10-06).
        r"|auth(?:entication|orization)?[_\s-]+context[_\s-]+mismatch"
        r"|(?:missing|insufficient)[_\s-]+scopes?|scopes?[_\s-]+mismatch)",
        e,
    ):
        return "auth"
    if "model is disabled" in e or "modelerror" in e or "not supported" in e:
        return "model"
    if any(marker in e for marker in (
        "permissiondenied", "permission_denied", "permission denied",
    )):
        return "permission"
    if "overloaded" in e or "overload" in e:
        return "overloaded"
    if "403" in e:
        return "permission"
    if "concurrency" in e:
        return "concurrency"
    if any(marker in e for marker in (
        "429", "too many requests", "rate limit", "rate_limit",
        "pace your requests",
    )):
        return "rate_limit"
    if re.search(r"(?:^|\D)(?:500|502|503|504|529)(?:\D|$)", e) or "server_error" in e:
        return "server"
    if any(marker in e for marker in (
        "timeout", "timed out", "connection",
        "server disconnected without sending a response",
        "responses stream ended before response.completed",
    )):
        return "transport"
    return None


def is_retryable_provider_error(error_msg: str) -> bool:
    """Return whether a pre-output failure may be retried on the same route."""
    return provider_error_kind(error_msg) in {
        "overloaded", "concurrency", "rate_limit", "server", "transport",
    }


def provider_retry_after_seconds(error_msg: str) -> float | None:
    """Extract a non-negative Retry-After delay from a provider error, if any."""
    text = str(error_msg or "")
    match = re.search(
        r"retry[_\s-]*after\s*[:=]?\s*(\d+(?:\.\d+)?)",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    try:
        value = float(match.group(1))
    except (TypeError, ValueError):
        return None
    if value >= 1_000_000_000:
        value -= time.time()
    return max(0.0, value)


def _is_provider_policy_rejection(error_msg: str) -> bool:
    e = str(error_msg or "").lower()
    return "usage policy" in e and ("prompt" in e or "content" in e)


def is_context_overflow_error(error_msg: str) -> bool:
    """Return True when a provider error means the request exceeded context/input size.

    This is intentionally separate from provider fallback classification. Context
    overflow should first trigger MO's deterministic compact/handoff recovery and
    exactly one retry of the same request shape; falling through to another
    provider can hide the real context-pressure evidence.
    """
    e = (error_msg or "").lower()
    if not e or _is_provider_policy_rejection(e):
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
    return _re.search(
        r"\b(too\s+(?:large|long)|exceed(?:ed|s)?)\b.{0,100}\b(context|prompt|input|messages|tokens)\b",
        e,
    ) is not None


def fallback_reason(error_msg: str) -> str | None:
    kind = provider_error_kind(error_msg)
    return {
        "policy": "provider prompt/content policy rejection",
        "balance": "provider balance/route blocked",
        "quota": "provider quota/billing limit",
        "auth": "provider authentication failed",
        "model": "provider model route failed",
        "permission": "provider permission denied",
        "overloaded": "provider temporarily overloaded",
        "concurrency": "provider temporary concurrency limit",
        "rate_limit": "provider temporary rate limit",
        "server": "provider temporary server error",
        "transport": "provider temporary timeout/connection error",
    }.get(kind)


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
