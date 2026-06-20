"""Model context/output limits used for provider-facing budget decisions.

When a provider does not expose token limits, this module only applies public
upstream model-family limits for known model names; otherwise callers keep their
configured/default budget.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


DEFAULT_CONTEXT_BUDGET_TOKENS = 128_000
DEFAULT_CONTEXT_RESERVE_TOKENS = 16_384


@dataclass(frozen=True)
class ModelLimits:
    context_window: int | None = None
    max_output_tokens: int | None = None
    source: str = "unknown"


def _claude_limits(model: str) -> ModelLimits | None:
    lower = str(model or "").lower()
    # Anthropic model docs: Claude Opus 4.6/4.7/4.8 = 1M context, 128k sync max output.
    if re.search(r"\bclaude-opus-4-[678]\b", lower):
        return ModelLimits(1_000_000, 128_000, "anthropic_models_doc")
    # Anthropic model docs: Claude Sonnet 4.6 = 1M context, 64k sync max output.
    if re.search(r"\bclaude-sonnet-4-6\b", lower):
        return ModelLimits(1_000_000, 64_000, "anthropic_models_doc")
    # Older Claude 4.x family entries remain 200k context in Anthropic's model table.
    if re.search(r"\bclaude-(opus|sonnet|haiku)-4", lower):
        max_out = 32_000 if "opus-4-1" in lower or "opus-4-20250514" in lower else 64_000
        return ModelLimits(200_000, max_out, "anthropic_models_doc")
    return None


def infer_model_limits(provider: str, model: str) -> ModelLimits:
    model_s = str(model or "")
    model_l = model_s.lower()
    claude = _claude_limits(model_s)
    if claude:
        return claude
    if "deepseek-v4" in model_l:
        # DeepSeek API docs (Models & Pricing) list deepseek-v4-flash and
        # deepseek-v4-pro with CONTEXT LENGTH 1M and MAX OUTPUT 384K. OpenCode's
        # /models endpoint exposes the deepseek-v4-pro id but not token limits,
        # so use the upstream DeepSeek model-family limit for this exact alias.
        return ModelLimits(1_000_000, 384_000, "deepseek_api_docs_1m_context")
    if "deepseek" in model_l:
        # Older/other routed DeepSeek aliases may not expose a machine-readable
        # context window to MO. Keep MO's conservative default unless the exact
        # public family above is matched.
        return ModelLimits(DEFAULT_CONTEXT_BUDGET_TOKENS, 8_192, "deepseek_family_conservative_default")
    return ModelLimits(None, None, "unknown")


def resolve_context_budget_tokens(
    configured: Any,
    *,
    provider: str,
    model: str,
    reserve_tokens: int = DEFAULT_CONTEXT_RESERVE_TOKENS,
    default_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
) -> int:
    """Return provider input budget tokens.

    Numeric config values are respected as explicit operator overrides.  Values
    of "auto", "model", "dynamic", empty, or non-positive ask MO to infer from
    selected model limits and reserve headroom for the provider response.
    """
    raw = configured
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value and value not in {"auto", "model", "dynamic"}:
            try:
                explicit = int(float(value))
                if explicit > 0:
                    return explicit
            except ValueError:
                pass
    elif raw is not None:
        try:
            explicit = int(raw)
            if explicit > 0:
                return explicit
        except (TypeError, ValueError):
            pass

    limits = infer_model_limits(provider, model)
    if limits.context_window:
        return max(1, int(limits.context_window) - max(0, int(reserve_tokens or 0)))
    return int(default_tokens)


def context_budget_source(configured: Any, *, provider: str, model: str) -> str:
    if isinstance(configured, str) and configured.strip().lower() in {"auto", "model", "dynamic", ""}:
        return infer_model_limits(provider, model).source
    try:
        if configured is not None and int(configured) > 0:
            return "config"
    except (TypeError, ValueError):
        pass
    return infer_model_limits(provider, model).source
