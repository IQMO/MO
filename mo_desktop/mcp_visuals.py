"""Desktop-owned visual contract for MCP presence.

MCP servers provide capability and runtime state; they never own MO's palette,
cube renderer, or panel styling. This small immutable contract gives every
future MCP a useful default without turning its domain language into the
global default.
"""
from __future__ import annotations

from dataclasses import dataclass


MCP_PHASES = frozenset({
    "disconnected", "connecting", "active", "acting", "success", "warning", "error",
})


@dataclass(frozen=True)
class McpVisualState:
    name: str
    phase: str = "disconnected"
    capability: str = "read"
    label: str = ""
    detail: str = ""
    token: str = "neutral"
    formation: str = "cluster"
    event: str = ""
    updated_at: float = 0.0


_DEFAULTS = {
    "disconnected": ("neutral", "cluster", "", "disconnected"),
    "connecting": ("brand", "cluster", "mcp_connecting", "connecting"),
    "active": ("brand", "cluster", "mcp_active", "active"),
    "acting": ("warn", "row", "mcp_acting", "working"),
    "success": ("ok", "cluster", "mcp_success", "done"),
    "warning": ("warn", "cluster", "mcp_warning", "needs attention"),
    "error": ("err", "cluster", "mcp_error", "unavailable"),
}


def visual_state(name: str, phase: str, *, capability: str = "read", label: str = "",
                 detail: str = "", token: str = "", formation: str = "",
                 event: str = "", updated_at: float = 0.0) -> McpVisualState:
    """Build a normalized default visual state for any MCP integration."""
    clean_phase = str(phase or "disconnected").strip().lower()
    if clean_phase not in MCP_PHASES:
        clean_phase = "warning"
    default_token, default_form, default_event, default_label = _DEFAULTS[clean_phase]
    return McpVisualState(
        name=str(name or "MCP").strip() or "MCP",
        phase=clean_phase,
        capability=str(capability or "read").strip().lower() or "read",
        label=str(label or default_label).strip(),
        detail=str(detail or "").strip(),
        token=str(token or default_token).strip().lower(),
        formation=str(formation or default_form).strip().lower(),
        event=str(event or default_event).strip().lower(),
        updated_at=float(updated_at or 0.0),
    )
