"""Provider model context/output limits.

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
    # The current Codex model catalog serves the GPT-6 family with a 272k context
    # window. It does not publish an output-token cap for this route.
    if model_l in {"gpt-6-astra", "gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna"}:
        return ModelLimits(272_000, None, "codex_models_cache_272k_context")
    # The gpt-5.x-codex family is served ONLY through the Codex OAuth backend
    # (chatgpt.com/backend-api/codex), which caps the context window at 272k —
    # verified against the Codex CLI's own models_cache.json (context_window:
    # 272000 for gpt-5.4 / 5.4-mini / 5.5 / 5.6-*). The OpenAI platform-API models
    # doc quotes a larger max_context_window (up to 1M for some), but MO never
    # reaches these models except via Codex, so the served 272k is the real budget.
    if model_l == "gpt-5.3-codex-spark":
        return ModelLimits(128_000, None, "codex_models_cache_128k_context")
    if model_l in {
        "gpt-5.4", "gpt-5.4-mini", "gpt-5.5",
        "gpt-5.6", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna",
    }:
        return ModelLimits(272_000, 128_000, "codex_models_cache_272k_context")
    if "deepseek-v4" in model_l or model_l == "deepseek-flash":
        # DeepSeek API docs list deepseek-flash and deepseek-v4-pro with a 1M
        # context window and 384K maximum output. OpenCode also exposes v4
        # aliases without token limits, so use the upstream family limits.
        return ModelLimits(1_000_000, 384_000, "deepseek_api_docs_1m_context")
    if "deepseek" in model_l:
        # Older/other routed DeepSeek aliases may not expose a machine-readable
        # context window to MO. Keep MO's conservative default unless the exact
        # public family above is matched.
        return ModelLimits(DEFAULT_CONTEXT_BUDGET_TOKENS, 8_192, "deepseek_family_conservative_default")
    if "glm-5.2" in model_l:
        return ModelLimits(1_000_000, 128_000, "zai_glm_5_2_docs")
    if any(name in model_l for name in ("glm-5.1", "glm-5-turbo", "glm-5", "glm-4.7", "glm-4.6")):
        return ModelLimits(200_000, 128_000, "zai_glm_docs_200k_context")
    if any(name in model_l for name in ("glm-4.5", "glm-4-32b-0414-128k")):
        max_out = 16_384 if "glm-4-32b-0414-128k" in model_l else 98_304
        return ModelLimits(128_000, max_out, "zai_glm_docs_128k_context")
    if "qwen2.5-coder" in model_l or "qwen2.5coder" in model_l:
        # Qwen2.5-Coder model card: 32,768-token context, up to 8,192 output.
        # Served locally (e.g. Ollama, http://localhost:11434/v1). NOTE: a local
        # runtime may cap the live window below this (Ollama's num_ctx /
        # OLLAMA_CONTEXT_LENGTH); set agent.context_budget_tokens to match it.
        return ModelLimits(32_768, 8_192, "qwen2.5_coder_model_card")
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
