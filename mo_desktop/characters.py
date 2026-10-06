"""MO Desktop — role/MCP character mappings for the cube.

The cube already owns the mechanisms — ``set_character`` (colour), ``apply_form`` / ``set_formation``
(shape), ``react`` (emotes). A *character* is just a mapping from a subsystem's state onto those
public calls so MCP presence stays pure data while Desktop owns
the look. Nothing here touches renderer internals, so the pure helpers are headless-testable.
"""
from __future__ import annotations

from typing import Any


def mode_color(visuals: Any, token: str) -> str:
    """Semantic token → a skin hex the cube's ``set_character`` accepts, or ``"skin"`` (neutral)."""
    from interface.desktop_ui import DesktopVisualState, active_desktop_visual_state

    state = visuals if visuals is not None else active_desktop_visual_state()
    if not isinstance(state, DesktopVisualState):
        raise TypeError("Desktop character requires DesktopVisualState")
    c = state.palette
    if token == "ok":
        return c.ok
    if token == "warn":
        return c.action
    if token == "err":
        return c.error
    if token == "brand":
        return c.accent
    return "skin"


def _safe(obj: Any, method: str, *args, **kwargs) -> None:
    fn = getattr(obj, method, None)
    if callable(fn):
        try:
            fn(*args, **kwargs)
        except Exception:
            pass


def apply_mcp_character(cube: Any, state: Any, visuals: Any = None,
                        *, announce: bool = False) -> None:
    """Apply the default MCP visual contract through the cube's public API."""
    if cube is None or state is None:
        return
    _safe(cube, "set_character", color_mode=mode_color(visuals, getattr(state, "token", "neutral")))
    _safe(cube, "set_formation", str(getattr(state, "formation", "cluster") or "cluster"))
    event = str(getattr(state, "event", "") or "")
    if announce and event:
        _safe(cube, "react", event)


# ── reviewer / coach character (watching WITH the user) ─────────────────────────────────
def apply_reviewer_character(cube: Any, visuals: Any = None) -> None:
    """The reviewer/coach 'watching with you' look: the calm face formation in the skin's brand
    tone, so it reads as attentive. Public API only — literal 'glasses' would be a drawn overlay
    (renderer-level) and is a live-verified follow-up."""
    if cube is None:
        return
    _safe(cube, "set_role_motion", "")
    from mo_desktop.mcp_visuals import visual_state
    apply_mcp_character(
        cube,
        visual_state("reviewer", "active", capability="review", formation="face"),
        visuals,
    )


# ── writer character (row 28: the same four cubes, a writer's behaviour) ─────────────────────
WRITER_PARCHMENT = "#f0e6d2"


def apply_writer_character(cube: Any, visuals: Any = None) -> None:
    """Writer roles: the four cubes in a parchment tone stand in a line like letters, the last one
    blinking like a caret; while MO works the lead cube writes joined-up strokes in ink."""
    if cube is None:
        return
    _safe(cube, "set_character", color_mode=WRITER_PARCHMENT)
    _safe(cube, "set_formation", "row")
    _safe(cube, "set_role_motion", "writer")


def clear_role_character(cube: Any, *, color_mode: str = "skin") -> None:
    """Restore the configured ordinary cube character after a role is dismissed."""
    _safe(cube, "set_role_motion", "")
    _safe(cube, "set_character", color_mode=str(color_mode or "skin"))
    _safe(cube, "set_formation", "cluster")


# Role → cube character. A role/skill maps to how the cube looks while it is active. Matched by
# neutral role kind (substring), so any "…-coach" / "…-reviewer" role qualifies and future roles
# add a kind without operator- or product-specific names here.
_REVIEWER_KINDS = ("coach", "reviewer")
_WRITER_KINDS = ("writer",)


def character_for_role(role_name: str) -> Any:
    """Return the cube-character applier for a role name, or None (leave the cube as-is)."""
    key = str(role_name or "").strip().lower()
    if any(kind in key for kind in _WRITER_KINDS):
        return apply_writer_character
    if any(kind in key for kind in _REVIEWER_KINDS):
        return apply_reviewer_character
    return None
