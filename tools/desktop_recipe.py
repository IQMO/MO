"""Transparent desktop recipe replay over MO's existing desktop tools."""
from __future__ import annotations

import json
from contextlib import nullcontext
from typing import Any

from core.tooling.sandbox import redact_sensitive_text

from .desktop import execute_point_on_screen
from .desktop_semantic import _execute_desktop_wait

MAX_RECIPE_STEPS = 40

_ACTIONS = frozenset({
    "wait", "point",
    "invoke", "click_element", "focus_element", "set_value", "toggle", "semantic_scroll",
    "focus_window", "minimize_window", "hide_window", "close_window",
    "click", "move", "drag", "scroll", "type", "key",
})

_REQUIRED: dict[str, tuple[str, ...]] = {
    "wait": ("query",),
    "point": ("x", "y"),
    "invoke": ("target",),
    "click_element": ("target",),
    "focus_element": ("target",),
    "set_value": ("target", "value"),
    "toggle": ("target",),
    "semantic_scroll": ("target", "value"),
    "focus_window": ("target",),
    "minimize_window": ("target",),
    "hide_window": ("target",),
    "close_window": ("target",),
    "click": (),
    "move": ("x", "y"),
    "drag": ("start_x", "start_y", "end_x", "end_y"),
    "scroll": ("delta",),
    "type": ("text",),
    "key": ("keys",),
}


def _load_recipe(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except Exception as exc:
            raise ValueError(f"recipe is not valid JSON: {exc}") from exc
        if isinstance(loaded, dict):
            return loaded
    raise ValueError("recipe must be an object or JSON object string")


def _step_action(step: dict[str, Any]) -> str:
    raw = str(step.get("tool") or step.get("action") or "").strip()
    if raw not in _ACTIONS:
        allowed = ", ".join(sorted(_ACTIONS))
        raise ValueError(f"unsupported recipe action {raw!r}; allowed: {allowed}")
    return raw


def _step_args(step: dict[str, Any], action: str) -> dict[str, Any]:
    args = step.get("args")
    if isinstance(args, dict):
        out = dict(args)
    else:
        out = {k: v for k, v in step.items() if k not in {"tool", "action", "label", "args", "continue_on_error"}}
    # ``label`` is generic recipe metadata for most actions, but it is visible
    # walkthrough content for point_on_screen. Accept the natural top-level form
    # as well as ``args.label`` so a batched guide never degrades to "here".
    if action == "point" and step.get("label") not in (None, ""):
        out.setdefault("label", step.get("label"))
    points = out.get("points") if action == "drag" else None
    if points is not None:
        if not isinstance(points, list) or not 2 <= len(points) <= 64:
            raise ValueError("drag points must contain 2 to 64 coordinate objects")
        for index, point in enumerate(points, 1):
            if not isinstance(point, dict):
                raise ValueError(f"drag point {index} must be an object")
            try:
                int(point["x"])
                int(point["y"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"drag point {index} requires integer x and y") from exc
    required = () if points is not None else _REQUIRED[action]
    missing = [name for name in required if out.get(name) in (None, "")]
    if missing:
        raise ValueError(f"{action} step missing required arg(s): {', '.join(missing)}")
    return out


def validate_recipe(recipe: Any) -> tuple[dict[str, Any], list[tuple[str, dict[str, Any], bool]]]:
    data = _load_recipe(recipe)
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("recipe.steps must be a non-empty list")
    if len(steps) > MAX_RECIPE_STEPS:
        raise ValueError(f"recipe has {len(steps)} steps; max is {MAX_RECIPE_STEPS}")
    normalized: list[tuple[str, dict[str, Any], bool]] = []
    for index, raw_step in enumerate(steps, 1):
        if not isinstance(raw_step, dict):
            raise ValueError(f"step {index} must be an object")
        action = _step_action(raw_step)
        args = _step_args(raw_step, action)
        normalized.append((action, args, bool(raw_step.get("continue_on_error", False))))
    return data, normalized


def _execute_step(action: str, arguments: dict[str, Any]) -> str:
    if action == "wait":
        return _execute_desktop_wait(arguments)
    if action == "point":
        return execute_point_on_screen(arguments)
    from .computer import execute_computer_act

    return execute_computer_act({**arguments, "kind": "desktop", "action": action})


def _execute_desktop_recipe_run(arguments: dict[str, Any]) -> str:
    recipe_value = arguments.get("recipe")
    if recipe_value is None and "steps" in arguments:
        recipe_value = arguments
    dry_run = bool(arguments.get("dry_run", False))
    try:
        recipe, steps = validate_recipe(recipe_value)
    except ValueError as exc:
        return f"Error: invalid desktop recipe: {exc}"

    name = str(recipe.get("name") or "desktop-recipe").strip()[:80]
    lines = [f"[desktop recipe] {name}: {len(steps)} step(s){' (dry run)' if dry_run else ''}"]
    if dry_run:
        for index, (action, _args, _continue) in enumerate(steps, 1):
            lines.append(f"{index}. {action}: ok")
        return "\n".join(lines)

    from core.desktop.runtime import native_desktop_scope

    failed_steps: list[int] = []
    cancelled_at: int | None = None
    for index, (action, args, continue_on_error) in enumerate(steps, 1):
        cancel_event = arguments.get("_cancel_event")
        if cancel_event is not None and cancel_event.is_set():
            cancelled_at = index
            lines.append(f"Cancelled before step {index}.")
            break
        args = {**args, "_cancel_event": cancel_event,
                "_sandbox_config": arguments.get("_sandbox_config", {})}
        # Pointer labels and waits must not hold the physical input resource.
        scope = nullcontext() if action in {"point", "wait"} else native_desktop_scope()
        with scope:
            if cancel_event is not None and cancel_event.is_set():
                cancelled_at = index
                lines.append(f"Cancelled before step {index}.")
                break
            result = redact_sensitive_text(str(_execute_step(action, args) or ""))
        lines.append(f"{index}. {action}: {result}")
        if result.startswith("Error:"):
            failed_steps.append(index)
            if not continue_on_error:
                lines.append(f"Stopped at step {index}.")
                break
    if cancelled_at is not None:
        lines.insert(0, f"Error: desktop recipe cancelled before step {cancelled_at}; prior steps may have executed.")
    elif failed_steps:
        lines.insert(0, f"Error: desktop recipe failed at step(s) {', '.join(map(str, failed_steps))}; prior steps may have executed.")
    return "\n".join(lines)
