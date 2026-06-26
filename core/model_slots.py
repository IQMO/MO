"""Runtime model-slot resolution.

Provider initialization builds the available provider objects.  This module
turns runtime surfaces (main, Ghost, review) into ordered provider slots so the
agent consumes one explicit routing contract instead of scattered string checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ModelSlotResolution:
    slot: str
    surface: str
    providers: tuple[Any, ...]
    selectors: tuple[str, ...] = ()
    source: str = ""


def provider_name_model(provider: Any | None) -> tuple[str, str, str]:
    if provider is None:
        return "", "", ""
    return (
        str(getattr(provider, "name", "") or "").strip().lower(),
        str(getattr(provider, "model", "") or "").strip().lower(),
        str(getattr(provider, "api_mode", "") or "").strip().lower(),
    )


def provider_matches_selector(provider: Any | None, selector: str) -> bool:
    value = str(selector or "").strip().lower()
    if not value:
        return False
    name, model, _api_mode = provider_name_model(provider)
    return value in {name, model, f"{name}/{model}"}


def main_model_selectors(config: dict[str, Any] | None) -> tuple[str, ...]:
    cfg = config if isinstance(config, dict) else {}
    model_cfg = cfg.get("model", {}) if isinstance(cfg.get("model", {}), dict) else {}
    return tuple(
        str(selector).strip()
        for selector in (model_cfg.get("default"), model_cfg.get("fallback"))
        if str(selector or "").strip()
    )


def resolve_model_slot(
    surface: str,
    providers: list[Any] | tuple[Any, ...],
    *,
    active_provider: Any | None = None,
    config: dict[str, Any] | None = None,
    review_chain_builder: Callable[..., list[Any]] | None = None,
) -> ModelSlotResolution:
    surface_name = str(surface or "main")
    pool = list(providers or [])
    active = active_provider or (pool[0] if pool else None)
    slot = _slot_for_surface(surface_name)
    if slot == "ghost":
        return _resolve_ghost_slot(surface_name, pool, active, config)
    if slot == "review":
        return _resolve_review_slot(surface_name, pool, active, config, review_chain_builder)
    return ModelSlotResolution(
        slot="main",
        surface=surface_name,
        providers=tuple([active] if active is not None else []),
        selectors=main_model_selectors(config),
        source="active_provider",
    )


def _slot_for_surface(surface: str) -> str:
    value = str(surface or "").strip().lower()
    if value.startswith("ghost"):
        return "ghost"
    if value.startswith("review"):
        return "review"
    return "main"


def _resolve_ghost_slot(
    surface: str,
    providers: list[Any],
    active_provider: Any | None,
    config: dict[str, Any] | None,
) -> ModelSlotResolution:
    cfg = config if isinstance(config, dict) else {}
    agent_cfg = cfg.get("agent", {}) if isinstance(cfg.get("agent", {}), dict) else {}
    want_provider = str(agent_cfg.get("ghost_provider") or "").strip().lower()
    want_model = str(agent_cfg.get("ghost_model") or "").strip().lower()
    chain: list[Any] = []

    def add(provider: Any | None) -> None:
        if provider is not None and not any(existing is provider for existing in chain):
            chain.append(provider)

    if want_provider or want_model:
        for provider in providers:
            name, model, _api_mode = provider_name_model(provider)
            if want_provider and name != want_provider:
                continue
            if want_model and model != want_model:
                continue
            add(provider)
            break

    for predicate in (_is_non_free_flash_provider, _is_deepseek_pro_provider, _is_codex_provider):
        for provider in providers:
            if predicate(provider):
                add(provider)

    if not chain:
        add(active_provider)

    source = "ghost_config" if want_provider or want_model else "ghost_default_chain"
    selectors = tuple(value for value in (want_provider, want_model) if value)
    return ModelSlotResolution("ghost", surface, tuple(chain), selectors=selectors, source=source)


def _resolve_review_slot(
    surface: str,
    providers: list[Any],
    active_provider: Any | None,
    config: dict[str, Any] | None,
    review_chain_builder: Callable[..., list[Any]] | None,
) -> ModelSlotResolution:
    cfg = config if isinstance(config, dict) else {}
    prt_cfg = cfg.get("prt", {}) if isinstance(cfg.get("prt", {}), dict) else {}
    default_model = str(prt_cfg.get("default_model") or "deepseek-v4-pro")
    fallback_model = str(prt_cfg.get("fallback_model") or "codex")
    if review_chain_builder is not None:
        chain = review_chain_builder(
            providers,
            active_provider=active_provider,
            default_model=default_model,
            fallback_model=fallback_model,
        )
    else:
        chain = _review_chain(providers, active_provider, default_model, fallback_model)
    return ModelSlotResolution(
        "review",
        surface,
        tuple(chain),
        selectors=tuple(value for value in (default_model, fallback_model) if value),
        source="review_config",
    )


def _review_chain(
    providers: list[Any],
    active_provider: Any | None,
    default_model: str,
    fallback_model: str,
) -> list[Any]:
    chain: list[Any] = []

    def add(provider: Any | None) -> None:
        if provider is not None and not any(existing is provider for existing in chain):
            chain.append(provider)

    for target in (default_model, fallback_model):
        selector = str(target or "").strip().lower()
        if not selector:
            continue
        for provider in providers:
            name, model, api_mode = provider_name_model(provider)
            if selector == "codex":
                if "codex" in name or "codex" in api_mode:
                    add(provider)
            elif model == selector or model.endswith(f"/{selector}") or selector in model:
                add(provider)
    if not chain:
        add(active_provider)
    return chain


def _is_non_free_flash_provider(provider: Any | None) -> bool:
    name, model, _api_mode = provider_name_model(provider)
    return "flash" in model and "free" not in model and "free" not in name


def _is_deepseek_pro_provider(provider: Any | None) -> bool:
    _name, model, _api_mode = provider_name_model(provider)
    return "deepseek" in model and "pro" in model


def _is_codex_provider(provider: Any | None) -> bool:
    name, _model, api_mode = provider_name_model(provider)
    return "codex" in name or "codex" in api_mode
