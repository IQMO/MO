"""Runtime provider model-slot resolution.

Provider initialization builds the available provider objects. This module
turns runtime surfaces (main, MO Desktop, prompt enhancement, mapper, review)
into ordered provider slots so the agent consumes one explicit routing contract
instead of scattered string checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
) -> ModelSlotResolution:
    surface_name = str(surface or "main")
    pool = list(providers or [])
    active = active_provider or (pool[0] if pool else None)
    slot = _slot_for_surface(surface_name)
    if slot == "mapper":
        return _resolve_mapper_slot(surface_name, pool, active, config)
    if slot in {"mo_desktop", "prompt_enhance", "review"}:
        return _resolve_selected_model_slot(surface_name, active, slot=slot)
    return ModelSlotResolution(
        slot="main",
        surface=surface_name,
        providers=tuple([active] if active is not None else []),
        selectors=main_model_selectors(config),
        source="active_provider",
    )


def _slot_for_surface(surface: str) -> str:
    value = str(surface or "").strip().lower()
    if value.startswith("review"):
        return "review"
    if value.startswith("mapper"):
        return "mapper"
    if value.startswith(("mo_desktop", "companion")):
        return "mo_desktop"
    if value.startswith("prompt_enhance"):
        return "prompt_enhance"
    return "main"


def _resolve_selected_model_slot(
    surface: str,
    active_provider: Any | None,
    *,
    slot: str,
) -> ModelSlotResolution:
    """Keep interface-coupled work on the model the operator selected.

    Ctrl+E and PRT may bound tools, tokens, or review depth, but must not silently
    substitute a different provider or model behind the current interface selection.
    """
    return ModelSlotResolution(
        slot,
        surface,
        tuple([active_provider] if active_provider is not None else []),
        source="active_provider",
    )


def _resolve_mapper_slot(
    surface: str,
    providers: list[Any],
    active_provider: Any | None,
    config: dict[str, Any] | None,
) -> ModelSlotResolution:
    """Keep mapthis workers on the model selected for the current interface.

    Mapping may adapt token and reasoning depth per slice, but it must not silently
    swap to legacy ``agent.mapper_*`` provider/model overrides. This mirrors prompt
    enhancement and PRT: one selected-model authority, with request-local adaptation.
    """
    del providers, config
    return _resolve_selected_model_slot(surface, active_provider, slot="mapper")
