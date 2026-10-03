"""Canonical computer-tool normalization shared by dispatch and safety gates."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


CANONICAL_COMPUTER_TOOLS = frozenset(
    {"computer_targets", "computer_observe", "computer_act", "point_on_screen"}
)

_TARGET_KINDS = {
    "windows": "computer_targets",
    "applications": "computer_targets",
    "owned": "computer_targets",
    "screen": "computer_targets",
    "browser": "computer_targets",
}
_DESKTOP_OBSERVATIONS = {
    "context": "desktop_context",
    "find": "desktop_find",
    "inspect": "desktop_inspect",
    "annotate": "desktop_annotate",
    "wait": "desktop_wait",
}
_BROWSER_OBSERVATIONS = {
    "snapshot": "browser_snapshot",
    "read": "browser_read_page",
    "wait": "browser_wait",
    "capture": "browser_capture",
}
_DESKTOP_ACTIONS = {
    "launch": "computer_act",
    "open": "computer_act",
    "invoke": "desktop_invoke",
    "click_element": "desktop_invoke",
    "focus_element": "desktop_invoke",
    "set_value": "desktop_invoke",
    "toggle": "desktop_invoke",
    "semantic_scroll": "desktop_invoke",
    "focus_window": "desktop_window",
    "minimize_window": "desktop_window",
    "hide_window": "desktop_window",
    "close_window": "desktop_window",
    "click": "mouse_click",
    "move": "move_pointer",
    "drag": "drag_pointer",
    "scroll": "scroll_pointer",
    "type": "type_text",
    "key": "press_key",
    "recipe": "desktop_recipe_run",
}
_BROWSER_ACTIONS = {
    "open": "browser_open",
    "click": "browser_click",
    "type": "browser_type",
    "key": "browser_key",
    "eval": "browser_eval",
}


@dataclass(frozen=True)
class NormalizedComputerCall:
    """One validated provider-facing computer request and its internal engine."""

    tool: str
    kind: str
    operation: str
    engine_tool: str
    arguments: dict[str, Any]


def normalize_computer_call(
    name: str,
    arguments: dict[str, Any] | None,
) -> NormalizedComputerCall | None:
    """Normalize one canonical computer call; return ``None`` for other tools."""
    tool = str(name or "").strip()
    if tool not in CANONICAL_COMPUTER_TOOLS:
        return None
    args = dict(arguments or {})

    if tool == "point_on_screen":
        return NormalizedComputerCall(tool, "desktop", "point", tool, args)

    if tool == "computer_targets":
        kind = _token(args.get("kind") or "windows")
        if kind not in _TARGET_KINDS:
            raise ValueError("computer_targets kind must be windows, applications, owned, screen, or browser")
        args["kind"] = kind
        return NormalizedComputerCall(tool, kind, "targets", _TARGET_KINDS[kind], args)

    kind = _token(args.get("kind") or "desktop")
    if tool == "computer_observe":
        default_operation = {"desktop": "context", "screen": "capture", "browser": "snapshot"}.get(kind, "")
        operation = _token(args.get("operation") or default_operation)
        if kind == "desktop":
            engine = _DESKTOP_OBSERVATIONS.get(operation)
            expected = "context, find, inspect, annotate, or wait"
        elif kind == "screen":
            if operation != "capture":
                raise ValueError("screen computer_observe operation must be capture")
            region = args.get("region")
            if (
                isinstance(region, dict)
                and {"width", "height"} <= set(region) <= {"x", "y", "width", "height"}
                and all(region.get(key, 0) == 0 for key in ("x", "y", "width", "height"))
            ):
                # Some provider payloads materialize an omitted optional object
                # as its scalar zero defaults. No valid region has zero dimensions.
                args.pop("region", None)
            engine = "capture_screen"
            expected = "capture"
        elif kind == "browser":
            engine = _BROWSER_OBSERVATIONS.get(operation)
            expected = "snapshot, read, wait, or capture"
        else:
            raise ValueError("computer_observe kind must be desktop, screen, or browser")
        if engine is None:
            raise ValueError(f"{kind} computer_observe operation must be {expected}")
        args["kind"] = kind
        args["operation"] = operation
        if engine == "desktop_wait" and "timeout_seconds" in args:
            args["timeout"] = args["timeout_seconds"]
        return NormalizedComputerCall(tool, kind, operation, engine, args)

    action = _token(args.get("action"))
    if kind == "desktop":
        engine = _DESKTOP_ACTIONS.get(action)
        expected = ", ".join(_DESKTOP_ACTIONS)
    elif kind == "browser":
        engine = _BROWSER_ACTIONS.get(action)
        expected = ", ".join(_BROWSER_ACTIONS)
    else:
        raise ValueError("computer_act kind must be desktop or browser")
    if engine is None:
        raise ValueError(f"{kind} computer_act action must be {expected}")
    if args.get("submit") and engine != "browser_type":
        raise ValueError(
            "submit=true is supported only for kind=browser action=type; "
            "native fields require a separate observed action=key with keys=enter"
        )
    args["kind"] = kind
    args["action"] = _engine_action(action, engine)
    return NormalizedComputerCall(tool, kind, action, engine, args)


def computer_engine_tool_name(name: str, arguments: dict[str, Any] | None) -> str:
    """Return the internal safety/evidence name for a canonical computer call."""
    normalized = normalize_computer_call(name, arguments)
    return normalized.engine_tool if normalized is not None else str(name or "").strip()


def _engine_action(action: str, engine: str) -> str:
    if engine == "desktop_invoke":
        return {
            "click_element": "click",
            "focus_element": "focus",
            "semantic_scroll": "scroll",
        }.get(action, action)
    if engine == "desktop_window":
        return {
            "minimize_window": "minimize",
            "hide_window": "hide",
            "close_window": "close",
        }.get(action, "focus")
    return action


def _token(value: Any) -> str:
    return str(value or "").strip().lower()
