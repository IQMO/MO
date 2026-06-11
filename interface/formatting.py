from __future__ import annotations

import time
from typing import Any

from .state import TokenStatus

MOON_PHASES: tuple[str, ...] = ("○", "◔", "◑", "◕", "●", "◕", "◑", "◔")
MOON_PHASE_SECONDS = 0.45


def moon_phase_frame(now: float | None = None) -> str:
    """Official MO activity mark: monochrome moon-phase cycle."""
    current = time.time() if now is None else float(now)
    return MOON_PHASES[int(current / MOON_PHASE_SECONDS) % len(MOON_PHASES)]


def format_k(num: int) -> str:
    if num >= 1000:
        return f"{num/1000:.1f}k"
    return str(num)


def token_status_from_agent(agent: Any) -> TokenStatus:
    token_log = getattr(getattr(agent, "session", None), "token_log", [])
    input_tokens = sum(e.get("input_tokens", 0) for e in token_log)
    output_tokens = sum(e.get("output_tokens", 0) for e in token_log)
    reasoning = getattr(agent, "reasoning", getattr(agent, "config", {}).get("agent", {}).get("reasoning", "high"))
    # Compression savings
    estimator = getattr(agent, "_compression_saved_tokens_estimate", None)
    saved_tokens = int(estimator()) if callable(estimator) else 0
    saved_chars = int(getattr(agent, "_tool_context_saved_chars", lambda: 0)() or 0) if callable(getattr(agent, "_tool_context_saved_chars", None)) else 0
    saving_ops = int(getattr(agent, "_tool_context_saving_ops", lambda: 0)() or 0) if callable(getattr(agent, "_tool_context_saving_ops", None)) else 0
    return TokenStatus(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        saved_tokens_est=saved_tokens,
        saved_chars=saved_chars,
        saving_ops=saving_ops,
        provider_name=getattr(agent, "provider_name", ""),
        model=getattr(agent, "model", ""),
        reasoning=reasoning,
    )


def format_token_status(status: TokenStatus) -> str:
    in_str = format_k(status.input_tokens)
    out_str = format_k(status.output_tokens)
    base = f"↑{in_str} ↓{out_str}"
    if status.saved_tokens_est > 0:
        total_est = status.input_tokens + status.saved_tokens_est
        pct = round(status.saved_tokens_est / max(1, total_est) * 100, 1)
        base += f" \u25ce~{format_k(status.saved_tokens_est)} ({pct}%)"
    return f"{base} \u00b7 ({status.provider_name}) {status.model} \u2022 {status.reasoning}"


def format_agent_status(agent: Any) -> str:
    return format_token_status(token_status_from_agent(agent))


def activity_label(activity: str) -> str:
    """Return the compact live-lane label for a normalized activity string."""
    text = str(activity or "").lower()
    if "preparing" in text:
        return "Preparing…"
    if "thinking" in text:
        return "Thinking…"
    if "finalizing" in text or "critiquing" in text or "critique" in text:
        return "Finalizing…"
    if "streaming" in text or "receiving" in text or "answer" in text:
        return "Answering…"
    if any(word in text for word in ("tool", "read", "write", "file", "search", "grep", "find")):
        return "Working…"
    if "goal" in text:
        return "Goal Working…"
    return "Working…"



def idle_status_text(now: float | None = None) -> str:
    """Return the compact idle heartbeat shown in the status lane."""
    current = time.time() if now is None else float(now)
    return f"{moon_phase_frame(current)} idle"



# (activity_display removed 2026-06-10: its verbose intermediate was chained
# straight into activity_label, which discarded the detail — the live activity
# lane shows only the compact label.)
