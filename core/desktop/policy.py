"""Action-time confirmation policy for high-impact computer use."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from ..runtime.capability_ids import CAP_COMPUTER_CONTROL, CAP_WEB
from .runtime import active_target, current_owner_id
from .tool_actions import normalize_computer_call


CONFIRMATION_TTL_SECONDS = 180.0

_HIGH_IMPACT = re.compile(
    r"\b(delete|remove|erase|discard|don'?t\s+save|do\s+not\s+save|send|upload|share|publish|post|"
    r"pay|buy|purchase|transfer|place\s+(?:an?\s+)?order|confirm\s+(?:the\s+)?order|"
    r"submit\s+(?:(?:an?|the)\s+)?(?:order|payment|purchase|transfer|application)|"
    r"unsubscribe|close\s+account|reset|factory\s+reset|password|passcode|credential|"
    r"api\s*key|secret|grant|allow\s+permission)\b",
    re.I,
)
_APPROVE = re.compile(r"\b(approve|approved|yes|confirm|confirmed|go ahead|proceed|do it)\b", re.I)
_DENY = re.compile(r"\b(deny|denied|cancel|stop|do not|don't|dont|no)\b", re.I)
_CONFIRMATION_ID = re.compile(r"\bid=([0-9a-f]{10})\b", re.I)
_PHONE_CONFIRMATION_ID = re.compile(r"\bphone\s+control\s+request\s+([0-9a-f]{10})\b", re.I)
_COMPUTER_ACTION_CAPABILITIES = frozenset({CAP_COMPUTER_CONTROL, CAP_WEB})


@dataclass(frozen=True)
class PendingConfirmation:
    confirmation_id: str
    fingerprint: str
    owner_id: str
    tool: str
    target_id: str
    target_revision: int
    observation_id: str
    action: str
    target: str
    destination: str
    data_summary: str
    created_turn_id: str
    created_at: float


_LOCK = threading.RLock()
_PENDING: dict[str, PendingConfirmation] = {}
_PENDING_PIXELS: dict[str, PendingConfirmation] = {}


def computer_action_request_authorized(admission: object, turn_intent: object) -> bool:
    """Return whether routing should prime the bounded computer-action catalog.

    The request-local admission classifier recognizes natural action language.
    Turn intent supplies the competing-work check: source review, diagnosis,
    build, and audit turns may mention UI verbs while describing evidence. This
    helper optimizes initial tool exposure only; it never authorizes or blocks
    execution. Normal task scope, sandbox/target ownership, fresh observation,
    and high-impact confirmation remain the action-time boundaries.
    """
    if not bool(getattr(admission, "permits_action", False)):
        return False
    capability = str(getattr(admission, "capability", "") or "").strip().lower()
    action = str(getattr(admission, "action", "") or "").strip().lower()
    target = str(getattr(admission, "target", "") or "").strip().lower()
    if capability not in _COMPUTER_ACTION_CAPABILITIES:
        return False
    if capability == CAP_WEB and action == "search":
        # Web research uses the web capability, not the operator's input device.
        # An explicitly UI-bound search is classified as computer_control.
        return False
    if action in {"", "none"} or target in {"", "none", "unknown"}:
        return False
    work_pattern = str(getattr(turn_intent, "work_pattern", "") or "").strip().lower()
    board_policy = str(getattr(turn_intent, "board_policy", "") or "").strip().lower()
    admission_source = str(getattr(admission, "source", "") or "").strip().lower()
    trusted_resume = bool(
        board_policy == "resume"
        and admission_source in {"continued_receipt", "pending_task"}
    )
    trusted_target_continuation = admission_source in {"continued_receipt", "active_target"}
    if board_policy not in {"", "none"} and work_pattern != "desktop_task" and not trusted_resume:
        # A taskboard-owning work turn has an explicit non-desktop owner. UI
        # verbs inside prose cannot replace that scope with live actuation. Only
        # bounded receipt or typed pending-task state may resume that catalog.
        return False
    if work_pattern and work_pattern != "desktop_task" and not trusted_target_continuation:
        return False
    return True


def action_confirmation_block_reason(user_input: str, tool: str, arguments: dict[str, Any]) -> str | None:
    """Return an approval block for risky computer actions on every surface."""
    risk = _risk_summary(tool, arguments)
    if risk is None:
        return None
    action, target_summary, destination, data_summary = risk
    owner = current_owner_id()
    if str(tool) == "computer_act":
        try:
            normalized = normalize_computer_call(tool, arguments)
        except ValueError:
            return None
        kind = normalized.kind if normalized is not None else "desktop"
    else:
        kind = (
            "browser" if str(tool).startswith("browser_")
            else "phone" if str(tool).startswith("phone_")
            else "desktop"
        )
    target = active_target(kind)
    if target is None and kind == "desktop":
        kind = "screen"
        target = active_target(kind)
    from .runtime import latest_observation

    observation = latest_observation(kind) if target is not None else None
    observation_id = observation.observation_id if observation is not None else ""
    target_id = target.target_id if target is not None else ""
    target_revision = target.revision if target is not None else 0
    fingerprint = _fingerprint(tool, arguments, target_id, target_revision, observation_id)
    now = time.time()
    text = str(user_input or "")
    supplied_confirmation_id = _supplied_confirmation_id(text)
    turn_id = _current_turn_id()
    with _LOCK:
        pending = _PENDING.get(owner)
        if pending and now - pending.created_at > CONFIRMATION_TTL_SECONDS:
            _PENDING.pop(owner, None)
            pending = None
        if (
            pending
            and supplied_confirmation_id
            and supplied_confirmation_id != pending.confirmation_id
        ):
            return (
                "[CONFIRMATION DENIED] The supplied confirmation identifier is no longer "
                "current. Review the latest action before approving or denying it."
            )
        if pending and _DENY.search(text):
            _PENDING.pop(owner, None)
            if re.search(r"\bcancel\s+(?:the\s+)?task\b", text, re.I):
                try:
                    from .runtime import invalidate_current_targets

                    invalidate_current_targets(reason="operator_cancelled")
                except Exception:
                    pass
            return "[CONFIRMATION DENIED] The pending computer action was cancelled."
        if (
            pending
            and pending.fingerprint == fingerprint
            and pending.target_id == target_id
            and pending.target_revision == target_revision
            and pending.observation_id == observation_id
            and bool(turn_id)
            and pending.created_turn_id != turn_id
            and (
                not supplied_confirmation_id
                or supplied_confirmation_id == pending.confirmation_id
            )
            and _APPROVE.search(text)
            and not _DENY.search(text)
        ):
            _PENDING.pop(owner, None)
            return None
        confirmation_id = hashlib.sha256(f"{fingerprint}:{now}".encode()).hexdigest()[:10]
        pending = PendingConfirmation(
            confirmation_id=confirmation_id,
            fingerprint=fingerprint,
            owner_id=owner,
            tool=str(tool),
            target_id=target_id,
            target_revision=target_revision,
            observation_id=observation_id,
            action=action,
            target=target_summary,
            destination=destination,
            data_summary=data_summary,
            created_turn_id=turn_id,
            created_at=now,
        )
        _PENDING[owner] = pending
    paused_target = "phone" if kind == "phone" else "computer"
    approval_guidance = (
        "Approval expires in 3 minutes and is single-use. MO will re-check the target "
        "and parameters after approval; any drift requires a new review."
        if kind == "phone"
        else (
            "Ask the operator to Approve once, Deny, or Cancel task. Approval is single-use and "
            "will be rejected if the target revision or action parameters change."
        )
    )
    return (
        f"[APPROVAL REQUIRED id={confirmation_id}] High-impact {paused_target} action is paused. "
        f"Action: {action}. Target: {target_summary or 'current owned target'}. "
        f"Destination: {destination or 'not separately identified'}. "
        f"Data: {data_summary or 'no typed/uploaded data declared'}. "
        f"{approval_guidance}"
    )


def _supplied_confirmation_id(text: str) -> str:
    for pattern in (_CONFIRMATION_ID, _PHONE_CONFIRMATION_ID):
        match = pattern.search(str(text or ""))
        if match:
            return match.group(1).lower()
    return ""


def pending_phone_confirmation_retry_block_reason(user_input: str, tool: str) -> str | None:
    """Keep an approved phone retry on its exact pending tool.

    A provider may otherwise re-observe the phone before retrying a paused
    action. That replaces the observation fingerprint and turns one valid
    approval into an endless confirmation loop. The exact pending action still
    passes through :func:`action_confirmation_block_reason`, which owns target,
    parameter, expiry, later-turn, and single-use validation.
    """
    name = str(tool or "")
    if not name.startswith("phone_"):
        return None
    owner = current_owner_id()
    text = str(user_input or "")
    supplied_confirmation_id = _supplied_confirmation_id(text)
    turn_id = _current_turn_id()
    now = time.time()
    with _LOCK:
        pending = _PENDING.get(owner)
        if pending and now - pending.created_at > CONFIRMATION_TTL_SECONDS:
            _PENDING.pop(owner, None)
            pending = None
        if (
            pending is None
            or not pending.tool.startswith("phone_")
            or pending.created_turn_id == turn_id
            or not _APPROVE.search(text)
            or _DENY.search(text)
        ):
            return None
        if supplied_confirmation_id and supplied_confirmation_id != pending.confirmation_id:
            return (
                "[CONFIRMATION DENIED] The supplied confirmation identifier is no longer "
                "current. Review the latest action before approving or denying it."
            )
        if name == pending.tool:
            return None
        return (
            "[CONFIRMATION RETRY REQUIRED] Retry the exact pending "
            f"{pending.tool} action before any other phone tool. A new phone observation "
            "would invalidate the reviewed target and require another confirmation."
        )


def clear_pending_confirmation() -> None:
    with _LOCK:
        owner = current_owner_id()
        _PENDING.pop(owner, None)
        _PENDING_PIXELS.pop(owner, None)


def cloud_pixel_approval_block_reason(
    user_input: str,
    *,
    provider: str,
    data_summary: str,
    target_kind: str = "",
    target_id: str = "",
) -> str | None:
    """Require a later-turn, single-use approval for target pixels sent to cloud."""
    owner = current_owner_id()
    requested_kind = str(target_kind or "").strip().lower()
    target = active_target(requested_kind) if requested_kind else active_target("desktop") or active_target("screen")
    if target_id and (target is None or target.target_id != str(target_id)):
        return "[PIXEL TRANSMISSION BLOCKED] The visual target changed before cloud observation. Capture it again."
    target_id = target.target_id if target is not None else ""
    target_revision = target.revision if target is not None else 0
    arguments = {"provider": str(provider or ""), "data": str(data_summary or "")[:240]}
    fingerprint = _fingerprint(
        "cloud_pixel_observation", arguments, target_id, target_revision
    )
    now = time.time()
    text = str(user_input or "")
    turn_id = _current_turn_id()
    with _LOCK:
        pending = _PENDING_PIXELS.get(owner)
        if pending and now - pending.created_at > CONFIRMATION_TTL_SECONDS:
            _PENDING_PIXELS.pop(owner, None)
            pending = None
        if pending and _DENY.search(text):
            _PENDING_PIXELS.pop(owner, None)
            return "[PIXEL TRANSMISSION DENIED] The pending cloud visual observation was cancelled."
        if (
            pending
            and pending.fingerprint == fingerprint
            and pending.target_id == target_id
            and pending.target_revision == target_revision
            and bool(turn_id)
            and pending.created_turn_id != turn_id
            and _APPROVE.search(text)
            and not _DENY.search(text)
        ):
            _PENDING_PIXELS.pop(owner, None)
            return None
        confirmation_id = hashlib.sha256(f"pixels:{fingerprint}:{now}".encode()).hexdigest()[:10]
        _PENDING_PIXELS[owner] = PendingConfirmation(
            confirmation_id=confirmation_id,
            fingerprint=fingerprint,
            owner_id=owner,
            tool="cloud_pixel_observation",
            target_id=target_id,
            target_revision=target_revision,
            observation_id="",
            action="send bounded target pixels for visual observation",
            target=target.label if target is not None else "current screen target",
            destination=str(provider or "cloud vision provider"),
            data_summary=str(data_summary or "bounded screenshot")[:240],
            created_turn_id=turn_id,
            created_at=now,
        )
    return (
        f"[PIXEL APPROVAL REQUIRED id={confirmation_id}] Bounded target pixels are paused. "
        f"Target: {target.label if target is not None else 'current screen target'}. "
        f"Destination: {provider}. Data: {str(data_summary or 'bounded screenshot')[:240]}. "
        "Approval is single-use and invalid if the target revision or provider changes.\n"
        '__MO_OPTIONS__:{"mode":"single","options":['
        '{"label":"Approve once","detail":"Send only this bounded target image to the named provider"},'
        '{"label":"Deny","detail":"Keep the pixels local and do not send them"},'
        '{"label":"Cancel task","detail":"Stop this desktop task"}'
        "]}"
    )


def _current_turn_id() -> str:
    try:
        from core.runtime.backend_monitor import current_monitor_context

        return str(current_monitor_context().get("turn_id") or "")
    except Exception:
        return ""


def _risk_summary(tool: str, arguments: dict[str, Any]) -> tuple[str, str, str, str] | None:
    name = str(tool or "")
    args = dict(arguments or {})
    if name == "computer_act":
        try:
            normalized = normalize_computer_call(name, args)
        except ValueError:
            return None
        if normalized is None or normalized.engine_tool == "computer_act":
            return None
        return _risk_summary(normalized.engine_tool, normalized.arguments)
    if name == "desktop_recipe_run":
        # The recipe inspector is the complete authority for a composite action.
        # Falling through to the generic keyword scan would treat explanatory
        # point labels such as "Create — upload a video" as an upload action even
        # though every recipe step is presentation-only.
        return _recipe_risk_summary(args)
    # Target labels describe what is under inspection, not what navigation will
    # do.  A composer titled "Send message" must not turn focus/scroll/move into
    # a send request; consequential activation still falls through below.
    action = str(args.get("action") or "").strip().lower()
    if name == "desktop_window" and action in {"focus", "minimize", "hide"}:
        return None
    if name == "desktop_invoke" and action in {"focus", "scroll"}:
        return None
    if name in {"move_pointer", "scroll_pointer"}:
        return None
    target_summary = ""
    destination = ""
    data_summary = ""
    if name.startswith("desktop_"):
        try:
            from .uia import action_target_summary

            target_summary = action_target_summary(str(args.get("target") or args.get("ref") or ""))
        except Exception:
            target_summary = str(args.get("target") or args.get("ref") or "")[:160]
    elif name in {"mouse_click", "move_pointer", "drag_pointer", "scroll_pointer"}:
        target_summary = _visual_point_summary(args)
    elif name.startswith("browser_"):
        try:
            from tools.browser import current_action_summary

            target_summary = current_action_summary(str(args.get("ref") or ""))
        except Exception:
            target_summary = str(args.get("ref") or "")[:160]
        target = active_target("browser")
        destination = str(getattr(target, "metadata", {}).get("url") or "")[:240] if target else ""
    elif name.startswith("phone_"):
        target_summary = _phone_target_summary(args)

    if name == "phone_file_delete":
        path = str(args.get("path") or "")[:512]
        return ("delete phone file", target_summary, destination, path)
    if name == "phone_cache_trim":
        amount = str(args.get("bytes_to_free") or "")
        return ("trim Android app caches", target_summary, destination, f"{amount} requested byte(s)")
    if name == "phone_package_action":
        action = str(args.get("action") or "")[:40]
        package = str(args.get("package") or "")[:220]
        permission = str(args.get("permission") or "")[:220]
        detail = package if not permission else f"{package} · {permission}"
        return (f"Android package action: {action}", target_summary, destination, detail)
    if name == "phone_shell":
        command = str(args.get("command") or "")
        return (
            "run arbitrary Android shell command",
            target_summary,
            destination,
            f"{len(command)} command character(s)",
        )
    if name == "systemcare_apply":
        return (
            "apply reviewed Windows maintenance plan",
            str(args.get("plan_id") or "")[:80],
            "this Windows device",
            "exact plan digest; may include permanent file removal",
        )
    if name == "systemcare_rollback":
        return (
            "restore reviewed Windows maintenance receipt",
            str(args.get("receipt_id") or "")[:80],
            "this Windows device",
            "exact reversible receipt",
        )
    if name == "browser_eval":
        return ("run arbitrary page script", target_summary, destination, "script-controlled page data/state")
    if name in {"press_key", "browser_key"}:
        keys = args.get("keys") or args.get("key") or ""
        normalized = "+".join(str(item).strip().lower() for item in (
            keys if isinstance(keys, list) else [keys]
        ))
        # Closing a window is ordinary, like its own close button; the app's
        # unsaved-changes prompt is where discarding work is confirmed.
        if "shift+delete" in normalized:
            return (f"press {normalized}", target_summary, destination, "possible destructive shortcut")
    serialized = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    match = _HIGH_IMPACT.search(" ".join((target_summary, serialized)))
    if not match:
        return None
    if "text" in args:
        data_summary = f"{len(str(args.get('text') or ''))} typed character(s)"
    return (match.group(0).lower(), target_summary, destination, data_summary)


def _phone_target_summary(arguments: dict[str, Any]) -> str:
    try:
        from .runtime import latest_observation

        observation = latest_observation("phone")
        facts = observation.facts if observation is not None else {}
        by_ref = facts.get("elements_by_ref") if isinstance(facts, dict) else {}
        target = str(arguments.get("target") or "")
        element = by_ref.get(target) if isinstance(by_ref, dict) else None
        if isinstance(element, dict):
            return " ".join(
                str(element.get(key) or "") for key in ("role", "label", "actions")
            )[:200]
        live = active_target("phone")
        return str(getattr(live, "label", "") or "")[:160]
    except Exception:
        return str(arguments.get("target") or "")[:160]


def _recipe_risk_summary(arguments: dict[str, Any]) -> tuple[str, str, str, str] | None:
    recipe = arguments.get("recipe")
    if recipe is None and "steps" in arguments:
        recipe = arguments
    if isinstance(recipe, str):
        try:
            recipe = json.loads(recipe)
        except Exception:
            return ("run uninspectable desktop recipe", "current owned target", "", "recipe text")
    steps = recipe.get("steps") if isinstance(recipe, dict) else None
    for raw in steps if isinstance(steps, list) else []:
        if not isinstance(raw, dict):
            continue
        step_action = str(raw.get("tool") or raw.get("action") or "").strip()
        nested = raw.get("args") if isinstance(raw.get("args"), dict) else {
            key: value for key, value in raw.items()
            if key not in {"tool", "action", "label", "continue_on_error"}
        }
        if step_action in {"point", "wait"}:
            continue
        try:
            normalized = normalize_computer_call(
                "computer_act",
                {**nested, "kind": "desktop", "action": step_action},
            )
        except ValueError:
            continue
        if normalized is None or normalized.engine_tool in {"computer_act", "desktop_recipe_run"}:
            continue
        risk = _risk_summary(normalized.engine_tool, normalized.arguments)
        if risk is not None:
            return (f"recipe step: {risk[0]}", risk[1], risk[2], risk[3])
    return None


def _visual_point_summary(arguments: dict[str, Any]) -> str:
    try:
        from .runtime import active_target, latest_observation

        target = active_target("desktop") or active_target("screen")
        if target is None:
            return ""
        observation = latest_observation(target.kind)
        facts = observation.facts if observation is not None else {}
        elements = facts.get("elements") if isinstance(facts, dict) else None
        size = facts.get("image_size") if isinstance(facts, dict) else None
        points = arguments.get("points")
        last_point = (
            points[-1]
            if isinstance(points, list) and points and isinstance(points[-1], dict)
            else {}
        )
        x = float(arguments.get(
            "x", arguments.get("end_x", arguments.get("start_x", last_point.get("x")))
        ))
        y = float(arguments.get(
            "y", arguments.get("end_y", arguments.get("start_y", last_point.get("y")))
        ))
        from_capture = str(arguments.get("from_capture") or "").strip().lower() not in {
            "", "0", "false", "no", "off",
        }
        if not from_capture and target.bounds and isinstance(size, (list, tuple)) and len(size) == 2:
            left, top, right, bottom = target.bounds
            if right > left and bottom > top:
                x = (x - left) * float(size[0]) / float(right - left)
                y = (y - top) * float(size[1]) / float(bottom - top)
        for element in elements if isinstance(elements, list) else []:
            if not isinstance(element, dict):
                continue
            bounds = element.get("bounds")
            if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
                continue
            ex, ey, ew, eh = (float(value) for value in bounds)
            if ex <= x <= ex + ew and ey <= y <= ey + eh:
                return " ".join(
                    str(element.get(key) or "") for key in ("role", "label", "state")
                ).strip()[:200]
        return target.label[:160]
    except Exception:
        return ""


def _fingerprint(
    tool: str,
    arguments: dict[str, Any],
    target_id: str,
    target_revision: int,
    observation_id: str = "",
) -> str:
    payload = json.dumps(
        {
            "tool": str(tool or ""),
            "arguments": arguments or {},
            "target_id": target_id,
            "target_revision": int(target_revision or 0),
            "observation_id": str(observation_id or ""),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()
