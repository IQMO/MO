"""Provider/model menu catalog for slash UI and runtime switching.

The catalog shows configured providers for interface visibility, but runtime
switching still requires initialized providers.
"""
from __future__ import annotations

import copy
import json
import os
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelMenuItem:
    value: str
    label: str
    desc: str
    kind: str = "command"


THINKING_LEVELS: tuple[tuple[str, str], ...] = (
    ("high", "deep verification"),
    ("medium", "balanced work"),
    ("low", "fast/simple work"),
)

OPENAI_GPT56_THINKING_LEVELS: tuple[tuple[str, str], ...] = (
    ("max", "maximum exploration and verification"),
    ("xhigh", "extra-high reasoning"),
    ("high", "deep verification"),
    ("medium", "balanced work"),
    ("low", "latency-sensitive work"),
    ("none", "reasoning disabled"),
)

OPENAI_ASTRA_THINKING_LEVELS: tuple[tuple[str, str], ...] = (
    ("ultra", "maximum reasoning with automatic task delegation"),
    ("max", "maximum exploration and verification"),
    ("xhigh", "extra-high reasoning"),
    ("high", "deep verification"),
    ("medium", "balanced work"),
    ("low", "latency-sensitive work"),
)

SOURCE_LABELS: dict[str, str] = {
    "deepseek": "DeepSeek official",
    "zai": "Z.ai official",
    "opencode": "OpenCode",
    "openai-oauth": "OpenAI OAuth",
    "configured": "Local/custom",
}

SOURCE_DESCRIPTIONS: dict[str, str] = {
    "deepseek": "official DeepSeek API",
    "zai": "official Z.ai API",
    "opencode": "OpenCode model relay",
    "openai-oauth": "Codex OAuth provider",
    "configured": "other configured providers",
}

SOURCE_ORDER: tuple[str, ...] = ("deepseek", "zai", "opencode", "openai-oauth", "configured")

DEEPSEEK_OFFICIAL_FALLBACK_MODELS: tuple[str, ...] = ("deepseek-v4-pro", "deepseek-flash")

ZAI_OFFICIAL_FALLBACK_MODELS: tuple[str, ...] = (
    "glm-5.2",
    "glm-5.1",
    "glm-5-turbo",
    "glm-5",
    "glm-4.7",
    "glm-4.7-flashx",
    "glm-4.7-flash",
    "glm-4.6",
    "glm-4.5",
    "glm-4.5-air",
    "glm-4.5-x",
    "glm-4.5-airx",
    "glm-4.5-flash",
    "glm-4-32b-0414-128k",
)

OPENAI_OAUTH_FALLBACK_MODELS: tuple[str, ...] = (
    "gpt-6-astra",
    "gpt-6.1-sol",
    "gpt-6-sol",
    "gpt-6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.3-codex-spark",
    "gpt-5.3-codex",
)

OPENCODE_FALLBACK_MODELS: tuple[str, ...] = (
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "gpt-5.5",
    "gpt-5.5-pro",
    "gpt-5.4",
    "gpt-5.4-pro",
    "gpt-5.3-codex-spark",
    "gpt-5.3-codex",
    "glm-5.2",
    "glm-5.1",
    "glm-5",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "deepseek-v4.1-flash",
    "jev-1.13",
    "jev-1.13-free",
    "big-pickle",
)

OPENCODE_BROAD_MODELS_URL = "https://opencode.ai/zen/v1/models"
OPENCODE_GO_MODELS_URL = "https://opencode.ai/zen/go/v1/models"

OPENCODE_GO_FALLBACK_MODELS: tuple[str, ...] = (
    "minimax-m3",
    "minimax-m2.7",
    "minimax-m2.5",
    "kimi-k2.7-code",
    "kimi-k2.6",
    "kimi-k2.5",
    "glm-5.2",
    "glm-5.1",
    "glm-5",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "qwen3.7-max",
    "qwen3.7-plus",
    "qwen3.6-plus",
    "qwen3.5-plus",
    "mimo-v2-pro",
    "mimo-v2-omni",
    "mimo-v2.5-pro",
    "mimo-v2.5",
    "hy3-preview",
)

_FETCH_TTL_SECONDS = 15 * 60
_FETCH_CACHE: dict[str, tuple[float, tuple[str, ...]]] = {}


def normalize_model_source(value: str) -> str:
    key = str(value or "").strip().lower().replace("_", "-").replace(" ", "-")
    aliases = {
        "deepseek-official": "deepseek",
        "official-deepseek": "deepseek",
        "official": "deepseek",
        "z.ai": "zai",
        "z-ai": "zai",
        "zai": "zai",
        "zai-official": "zai",
        "z-ai-official": "zai",
        "open-code": "opencode",
        "openai": "openai-oauth",
        "open-ai": "openai-oauth",
        "oauth": "openai-oauth",
        "openai-codex": "openai-oauth",
        "openai-oauth": "openai-oauth",
        "open-ai-oauth": "openai-oauth",
        "codex": "openai-oauth",
        "custom": "configured",
        "local": "configured",
        "local-custom": "configured",
    }
    return aliases.get(key, key if key in SOURCE_LABELS else "")


def source_label(source_key: str) -> str:
    return SOURCE_LABELS.get(normalize_model_source(source_key), str(source_key or "").strip())


def _provider_text(provider: Any, attr: str) -> str:
    if isinstance(provider, dict):
        return str(provider.get(attr, "") or "").strip()
    return str(getattr(provider, attr, "") or "").strip()


def provider_source_key(provider: Any) -> str:
    name = _provider_text(provider, "name").lower()
    base_url = _provider_text(provider, "base_url").lower()
    api_mode = (_provider_text(provider, "api_mode") or _provider_text(provider, "type")).lower()
    if "codex" in api_mode or "codex" in name or "chatgpt.com/backend-api/codex" in base_url:
        return "openai-oauth"
    if "opencode.ai" in base_url or name.startswith("opencode"):
        return "opencode"
    if "api.z.ai" in base_url or name in {"zai", "z.ai"} or name.startswith(("zai-", "z.ai-")):
        return "zai"
    if "api.deepseek.com" in base_url or name == "deepseek" or name.startswith("deepseek-"):
        return "deepseek"
    return "configured"


def _configured_provider_rows(agent: Any) -> list[dict[str, Any]]:
    config = getattr(agent, "config", {}) if agent is not None else {}
    rows = (config.get("providers") if isinstance(config, dict) else None) or []
    return [row for row in rows if isinstance(row, dict)]


def _provider_identity(provider: Any) -> tuple[str, str, str, str, str]:
    return (
        provider_source_key(provider),
        _provider_text(provider, "name").lower(),
        _provider_text(provider, "model").lower(),
        _provider_text(provider, "base_url").lower(),
        (_provider_text(provider, "api_mode") or _provider_text(provider, "type")).lower(),
    )


def _providers(agent: Any, *, include_config: bool = False) -> list[Any]:
    providers = list(getattr(agent, "providers", []) or [])
    if not include_config:
        return providers
    seen = {_provider_identity(provider) for provider in providers}
    for row in _configured_provider_rows(agent):
        ident = _provider_identity(row)
        if ident not in seen:
            providers.append(row)
            seen.add(ident)
    return providers


def _providers_by_source(agent: Any, *, include_config: bool = False) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = {key: [] for key in SOURCE_ORDER}
    for provider in _providers(agent, include_config=include_config):
        grouped.setdefault(provider_source_key(provider), []).append(provider)
    return grouped


def _active_source(agent: Any) -> str:
    providers = _providers(agent)
    try:
        index = int(getattr(agent, "provider_index", 0) or 0)
        provider = providers[index]
    except Exception:
        provider = None
    return provider_source_key(provider) if provider is not None else ""


def source_menu_items(agent: Any) -> list[ModelMenuItem]:
    grouped = _providers_by_source(agent, include_config=True)
    initialized = _providers_by_source(agent)
    active = _active_source(agent)
    rows: list[ModelMenuItem] = []
    for key in SOURCE_ORDER:
        if not grouped.get(key):
            continue
        desc = SOURCE_DESCRIPTIONS[key]
        if key == active:
            desc = f"current source - {desc}"
        elif not initialized.get(key):
            desc = f"not initialized - {desc}"
        rows.append(ModelMenuItem(key, SOURCE_LABELS[key], desc, "submenu"))
    return rows


def _dedupe(values: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item or item.lower() in seen:
            continue
        seen.add(item.lower())
        out.append(item)
    return out


def _fetch_json_model_ids(url: str, *, timeout: float = 0.75) -> tuple[str, ...]:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - fixed model catalog endpoints
        body = response.read(512_000)
    data = json.loads(body.decode("utf-8", errors="replace"))
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return ()
    return tuple(
        str(row.get("id") or "").strip()
        for row in rows
        if isinstance(row, dict) and str(row.get("id") or "").strip()
    )


def _cached_model_ids(url: str) -> tuple[str, ...]:
    now = time.time()
    cached = _FETCH_CACHE.get(url)
    if cached and now - cached[0] < _FETCH_TTL_SECONDS:
        return cached[1]
    try:
        ids = _fetch_json_model_ids(url)
    except Exception:
        ids = ()
    _FETCH_CACHE[url] = (now, ids)
    return ids


def _opencode_live_models_enabled() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return os.environ.get("MO_MODEL_CATALOG_OFFLINE", "").strip().lower() not in {"1", "true", "yes", "on"}


def _has_opencode_endpoint(providers: list[Any]) -> bool:
    return any("opencode.ai" in _provider_text(provider, "base_url").lower() for provider in providers)


def _has_opencode_broad_endpoint(providers: list[Any]) -> bool:
    return any(
        "opencode.ai/zen/v1" in _provider_text(provider, "base_url").lower()
        and "/go/" not in _provider_text(provider, "base_url").lower()
        for provider in providers
    )


def _has_opencode_go_endpoint(providers: list[Any]) -> bool:
    return any("opencode.ai/zen/go/v1" in _provider_text(provider, "base_url").lower() for provider in providers)


def _opencode_model_endpoints(providers: list[Any]) -> list[str]:
    endpoints: list[str] = []
    if _has_opencode_broad_endpoint(providers):
        endpoints.append(OPENCODE_BROAD_MODELS_URL)
    if _has_opencode_go_endpoint(providers):
        endpoints.append(OPENCODE_GO_MODELS_URL)
    return endpoints


def _opencode_live_models(providers: list[Any]) -> list[str]:
    if not _has_opencode_endpoint(providers) or not _opencode_live_models_enabled():
        return []
    models: list[str] = []
    for endpoint in _opencode_model_endpoints(providers):
        models.extend(_cached_model_ids(endpoint))
    return _dedupe(models)


def _opencode_endpoint_models(providers: list[Any], endpoint: str, fallback: tuple[str, ...]) -> list[str]:
    if endpoint not in _opencode_model_endpoints(providers):
        return []
    models = list(_cached_model_ids(endpoint)) if _opencode_live_models_enabled() else []
    return _dedupe(models or list(fallback))


def _opencode_fallback_models(providers: list[Any]) -> list[str]:
    models: list[str] = []
    if _has_opencode_broad_endpoint(providers):
        models.extend(OPENCODE_FALLBACK_MODELS)
    if _has_opencode_go_endpoint(providers):
        models.extend(OPENCODE_GO_FALLBACK_MODELS)
    return _dedupe(models)


def _configured_provider_models(providers: list[Any]) -> list[str]:
    return _dedupe([
        _provider_text(provider, field)
        for provider in providers
        for field in ("model", "mo_desktop_model")
    ])


def model_menu_items(
    agent: Any,
    source_key: str,
    *,
    allow_live: bool = True,
) -> list[ModelMenuItem]:
    key = normalize_model_source(source_key)
    grouped = _providers_by_source(agent, include_config=True)
    providers = grouped.get(key) or []
    if not providers:
        return []

    configured = _configured_provider_models(providers)
    if key == "opencode":
        extras = _opencode_live_models(providers) if allow_live else []
        if not extras and _has_opencode_endpoint(providers):
            extras = _opencode_fallback_models(providers)
        models = _dedupe(configured + extras)
    elif key == "openai-oauth":
        models = _dedupe(configured + list(OPENAI_OAUTH_FALLBACK_MODELS))
    elif key == "zai":
        models = _dedupe(configured + list(ZAI_OFFICIAL_FALLBACK_MODELS))
    elif key == "deepseek":
        models = _dedupe(configured + list(DEEPSEEK_OFFICIAL_FALLBACK_MODELS))
    else:
        models = configured

    active_model = str(getattr(agent, "model", "") or "").strip().lower()
    active_source = _active_source(agent)
    rows: list[ModelMenuItem] = []
    for model in models:
        desc = f"{SOURCE_LABELS.get(key, key)} model"
        if key == active_source and model.lower() == active_model:
            desc = f"current model - {desc}"
        rows.append(ModelMenuItem(f"{key} {model}", model, desc, "submenu"))
    return rows


def thinking_levels_for(source_key: str, model: str) -> tuple[tuple[str, str], ...]:
    key = normalize_model_source(source_key)
    model_id = str(model or "").strip().lower()
    if key == "openai-oauth" and model_id in {"gpt-6-astra", "gpt-6.1-sol", "gpt-6-sol"}:
        return OPENAI_ASTRA_THINKING_LEVELS
    if key == "openai-oauth" and model_id == "gpt-6-luna":
        return OPENAI_ASTRA_THINKING_LEVELS[1:]
    if key == "openai-oauth" and model_id in {
        "gpt-5.6",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    }:
        return OPENAI_GPT56_THINKING_LEVELS
    return THINKING_LEVELS


def thinking_menu_items(agent: Any, source_key: str, model: str) -> list[ModelMenuItem]:
    key = normalize_model_source(source_key)
    if not key or not str(model or "").strip():
        return []
    model_id = str(model).strip()
    current_level = str(getattr(agent, "reasoning", "") or "").strip().lower()
    rows: list[ModelMenuItem] = []
    for level, desc in thinking_levels_for(key, model_id):
        detail = desc
        if level == current_level:
            detail = f"current thinking - {desc}"
        rows.append(ModelMenuItem(f"{key} {model_id} {level}", level, detail, "command"))
    return rows


def model_catalog_projection(agent: Any, *, allow_live: bool = True) -> list[dict[str, Any]]:
    """Return the credential-free provider/model choices for remote surfaces."""
    rows: list[dict[str, Any]] = []
    for source in source_menu_items(agent):
        models = []
        for item in model_menu_items(agent, source.value, allow_live=allow_live):
            models.append({
                "id": item.label,
                "thinking": [
                    {"value": level, "label": description}
                    for level, description in thinking_levels_for(source.value, item.label)
                ],
            })
        if models:
            rows.append({
                "source": source.value,
                "label": source.label,
                "description": source.desc,
                "models": models,
            })
    return rows


def validate_model_selection(
    catalog: list[dict[str, Any]],
    value: Any,
) -> dict[str, str]:
    """Return one catalog-backed, credential-free model selection."""
    if not isinstance(value, dict):
        raise ValueError("model selection must be an object")
    requested_source = str(value.get("source") or "").strip()
    requested_model = str(value.get("model") or "").strip()
    requested_thinking = str(value.get("thinking") or "").strip().lower()
    for source in catalog if isinstance(catalog, list) else []:
        if not isinstance(source, dict) or str(source.get("source") or "") != requested_source:
            continue
        for model in source.get("models", []):
            if not isinstance(model, dict) or str(model.get("id") or "") != requested_model:
                continue
            levels = {
                str(row.get("value") or "").strip().lower()
                for row in model.get("thinking", [])
                if isinstance(row, dict)
            }
            if requested_thinking not in levels:
                raise ValueError("model thinking level is no longer available")
            return {
                "source": requested_source,
                "model": requested_model,
                "thinking": requested_thinking,
            }
    raise ValueError("model selection is no longer available")


def runtime_model_selection(agent: Any, *, surface: str = "terminal") -> dict[str, str] | None:
    """Read /model's choice, adapting Desktop through that provider's config.

    Desktop may have its own explicit request selection saved by Settings;
    otherwise it follows the Terminal provider's Desktop variant.
    """
    from ..state.preferences import RuntimePreferenceError, load_runtime_preferences

    try:
        preferences = load_runtime_preferences(getattr(agent, "config", {}) or {})
    except (RuntimePreferenceError, OSError):
        preferences = {}
    if surface == "mo_desktop":
        desktop = preferences.get("desktop", {}).get("model")
        if desktop is not None:
            return validate_model_selection(model_catalog_projection(agent, allow_live=False), desktop)
    terminal = preferences.get("terminal") if isinstance(preferences, dict) else None
    saved = terminal.get("model") if isinstance(terminal, dict) else None
    selection = (
        {key: str(saved.get(key) or "").strip() for key in ("source", "model", "thinking")}
        if isinstance(saved, dict) else None
    )
    if selection is not None and not all(selection.values()):
        selection = None
    if surface != "mo_desktop":
        return selection

    if selection is not None:
        validate_model_selection(model_catalog_projection(agent, allow_live=False), selection)
        provider = _template_for_choice(agent, selection["source"], selection["model"])
    else:
        providers = _providers(agent)
        index = int(getattr(agent, "provider_index", 0) or 0)
        provider = providers[index] if 0 <= index < len(providers) else None
    if provider is None:
        raise ValueError("model source is configured but not initialized")
    source = provider_source_key(provider)
    model = _provider_text(provider, "mo_desktop_model") or (
        selection["model"] if selection is not None else _provider_text(provider, "model")
    )
    levels = dict(thinking_levels_for(source, model))
    return {"source": source, "model": model, "thinking": "none" if "none" in levels else "low"}


def active_model_selection(agent: Any) -> dict[str, str]:
    """Observe this Agent, independently of defaults saved for a later load."""
    providers = _providers(agent)
    index = int(getattr(agent, "provider_index", 0) or 0)
    provider = providers[index] if 0 <= index < len(providers) else None
    return {"source": provider_source_key(provider) if provider is not None else "",
            "model": str(getattr(agent, "model", "") or ""),
            "thinking": str(getattr(agent, "reasoning", "") or "none")}


def activate_model_selection(
    agent: Any,
    value: Any,
    *,
    surface: str,
    reason: str,
    record_change: bool = True,
) -> dict[str, str]:
    """Activate one validated selection through the shared agent model owner."""
    selection = validate_model_selection(model_catalog_projection(agent), value)
    selected = ensure_model_choice_provider(agent, selection["source"], selection["model"])
    if selected is None:
        raise ValueError("model source is configured but not initialized")
    target_index, provider = selected
    old_provider = str(getattr(agent, "provider_name", "") or "")
    old_model = str(getattr(agent, "model", "") or "")
    old_reasoning = str(getattr(agent, "reasoning", "") or "")
    agent.provider_index = target_index
    agent.provider_name = str(getattr(provider, "name", selection["source"]) or selection["source"])
    agent.model = str(getattr(provider, "model", selection["model"]) or selection["model"])
    agent.api_mode = getattr(provider, "api_mode", getattr(agent, "api_mode", ""))
    agent.config.setdefault("agent", {})["reasoning"] = selection["thinking"]
    agent.reasoning = selection["thinking"]
    api_reasoning = apply_model_reasoning_choice(
        provider,
        selection["source"],
        selection["model"],
        selection["thinking"],
    )
    refresh = getattr(agent, "_refresh_context_budget", None)
    if callable(refresh):
        refresh()
    if record_change and (old_provider, old_model, old_reasoning) != (
        agent.provider_name,
        agent.model,
        agent.reasoning,
    ):
        route_reason = (
            f"{str(reason or 'model selection')}; thinking={agent.reasoning}"
            + (f"; api_reasoning_effort={api_reasoning}" if api_reasoning else "")
        )
        agent.last_model_change_kind = "model_switch"
        agent.last_provider_route = {
            "event": "model_switch",
            "from_provider": old_provider,
            "from_model": old_model,
            "to_provider": agent.provider_name,
            "to_model": agent.model,
            "reason": route_reason[:180],
        }
        from .provider_audit import append_provider_audit

        append_provider_audit(
            "model_switch",
            surface=str(surface or "model_selection"),
            session_id=str(getattr(getattr(agent, "session", None), "session_id", "") or ""),
            from_provider=old_provider,
            from_model=old_model,
            to_provider=agent.provider_name,
            to_model=agent.model,
            provider=agent.provider_name,
            model=agent.model,
            reason=route_reason,
        )
    return selection


def apply_model_reasoning_choice(
    provider: Any,
    source_key: str,
    model: str,
    thinking: str,
) -> str:
    """Apply the provider-specific request effort for one validated choice."""
    overrides = model_reasoning_request_overrides(
        provider,
        source_key=source_key,
        model=model,
        thinking=thinking,
    )
    if "reasoning_effort" not in overrides:
        return ""
    effort = overrides["reasoning_effort"]
    provider.reasoning_effort = effort
    return str(effort or "")


def model_reasoning_request_overrides(
    provider: Any,
    *,
    source_key: str,
    model: str,
    thinking: str,
) -> dict[str, object]:
    """Map one model choice to request-local provider reasoning controls."""
    if not hasattr(provider, "reasoning_effort"):
        return {}
    source = normalize_model_source(source_key) or provider_source_key(provider)
    level = str(thinking or "").strip().lower()
    if source == "openai-oauth":
        return {"reasoning_effort": level}
    if source != "zai":
        return {}
    if str(model or "").strip().lower() != "glm-5.2":
        return {"reasoning_effort": None}
    effort = {"high": "max", "medium": "high", "low": "none"}.get(level, "")
    return {"reasoning_effort": effort} if effort else {}


def split_model_command_rest(rest: str) -> tuple[str, str, str]:
    tokens = str(rest or "").strip().split()
    if not tokens:
        return "", "", ""
    for source_len in (2, 1):
        if len(tokens) < source_len:
            continue
        source = normalize_model_source(" ".join(tokens[:source_len]))
        if not source:
            continue
        remaining = tokens[source_len:]
        model = remaining[0] if remaining else ""
        thinking = remaining[1].lower() if len(remaining) > 1 else ""
        return source, model, thinking
    return "", "", ""


def model_command_menu_items(command: str, agent: Any) -> list[ModelMenuItem]:
    parts = str(command or "").strip().split(maxsplit=1)
    rest = parts[1] if len(parts) > 1 else ""
    source, model, thinking = split_model_command_rest(rest)
    if not rest or not source:
        return source_menu_items(agent)
    if source and not model:
        return model_menu_items(agent, source)
    if source and model and not thinking:
        available = {item.label.lower() for item in model_menu_items(agent, source)}
        if available and model.lower() not in available:
            return []
        return thinking_menu_items(agent, source, model)
    return []


def _template_for_choice(agent: Any, source_key: str, model: str) -> Any | None:
    key = normalize_model_source(source_key)
    providers = _providers_by_source(agent).get(key) or []
    if not providers:
        return None
    model_l = str(model or "").strip().lower()
    for provider in providers:
        if _provider_text(provider, "model").lower() == model_l:
            return provider
    if key == "opencode":
        broad = _opencode_broad_templates(providers)
        go = _opencode_go_templates(providers)
        broad_models = {item.lower() for item in _opencode_endpoint_models(providers, OPENCODE_BROAD_MODELS_URL, OPENCODE_FALLBACK_MODELS)}
        go_models = {item.lower() for item in _opencode_endpoint_models(providers, OPENCODE_GO_MODELS_URL, OPENCODE_GO_FALLBACK_MODELS)}
        if model_l in go_models and model_l not in broad_models and go:
            return go[0]
        if model_l in broad_models and broad:
            return broad[0]
        if model_l in go_models and go:
            return go[0]
        if broad:
            return broad[0]
    return providers[0]


def _opencode_broad_templates(providers: list[Any]) -> list[Any]:
    return [
        provider for provider in providers
        if "opencode.ai/zen/v1" in _provider_text(provider, "base_url").lower()
        and "/go/" not in _provider_text(provider, "base_url").lower()
    ]


def _opencode_go_templates(providers: list[Any]) -> list[Any]:
    return [
        provider for provider in providers
        if "opencode.ai/zen/go/v1" in _provider_text(provider, "base_url").lower()
    ]


def _runtime_provider_name(source_key: str, template: Any) -> str:
    key = normalize_model_source(source_key)
    if key in {"deepseek", "zai", "opencode"}:
        return key
    return _provider_text(template, "name") or key


def ensure_model_choice_provider(agent: Any, source_key: str, model: str) -> tuple[int, Any] | None:
    lock = getattr(agent, "_provider_catalog_lock", None)
    if lock is None:
        # Real Agent construction installs this before providers are exposed.
        # Keep small test doubles compatible when they call the catalog directly.
        lock = threading.RLock()
        agent._provider_catalog_lock = lock
    with lock:
        key = normalize_model_source(source_key)
        model_id = str(model or "").strip()
        if not key or not model_id:
            return None
        template = _template_for_choice(agent, key, model_id)
        if template is None:
            return None

        provider_name = _runtime_provider_name(key, template)
        base_url = _provider_text(template, "base_url")
        api_mode = _provider_text(template, "api_mode")
        providers = _providers(agent)
        for index, provider in enumerate(providers):
            if (
                _provider_text(provider, "name") == provider_name
                and _provider_text(provider, "model") == model_id
                and _provider_text(provider, "base_url") == base_url
                and _provider_text(provider, "api_mode") == api_mode
            ):
                return index, provider

        clone = copy.copy(template)
        try:
            clone.name = provider_name
        except Exception:
            pass
        try:
            clone.model = model_id
        except Exception:
            pass
        agent.providers.append(clone)
        return len(agent.providers) - 1, clone
