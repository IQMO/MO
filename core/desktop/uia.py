"""Optional Windows UI Automation adapter for semantic desktop eyes.

This wraps the third-party ``uiautomation`` package behind small, testable
functions. The package is optional and imported only on demand, so normal MO
startup and headless runtimes never pay for it.
"""
from __future__ import annotations

import importlib.util
import hashlib
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import Any


MAX_ELEMENTS = 120
MAX_DEPTH = 5
SCOPED_FIND_DEPTH = 24
TARGET_GUARD_SECONDS = 120.0
LAUNCH_WINDOW_TIMEOUT_SECONDS = 2.5
LAUNCH_WINDOW_POLL_SECONDS = 0.1

# UIA ScrollAmount, inlined so this module never imports ``uiautomation`` for an enum.
_SCROLL_LARGE_DECREMENT = 0
_SCROLL_SMALL_DECREMENT = 1
_SCROLL_NO_AMOUNT = 2
_SCROLL_LARGE_INCREMENT = 3
_SCROLL_SMALL_INCREMENT = 4

# direction -> (horizontalAmount, verticalAmount)
_SCROLL_DIRECTIONS: dict[str, tuple[int, int]] = {
    "down": (_SCROLL_NO_AMOUNT, _SCROLL_SMALL_INCREMENT),
    "up": (_SCROLL_NO_AMOUNT, _SCROLL_SMALL_DECREMENT),
    "right": (_SCROLL_SMALL_INCREMENT, _SCROLL_NO_AMOUNT),
    "left": (_SCROLL_SMALL_DECREMENT, _SCROLL_NO_AMOUNT),
    "page_down": (_SCROLL_NO_AMOUNT, _SCROLL_LARGE_INCREMENT),
    "page_up": (_SCROLL_NO_AMOUNT, _SCROLL_LARGE_DECREMENT),
    "page_right": (_SCROLL_LARGE_INCREMENT, _SCROLL_NO_AMOUNT),
    "page_left": (_SCROLL_LARGE_DECREMENT, _SCROLL_NO_AMOUNT),
}


@dataclass
class _UIAState:
    controls: dict[str, Any]
    elements: dict[str, "DesktopElement"]
    snapshot_generation: int = 0
    context_at: float = 0.0
    context_windows: tuple["DesktopElement", ...] = ()
    context_controls: tuple[Any, ...] = ()


_STATES: dict[str, _UIAState] = {}
_PENDING_LAUNCH_LOCK = threading.RLock()
_PENDING_LAUNCHES: dict[str, threading.Event] = {}


def cancel_pending_launch_verification(owner_id: str = "") -> bool:
    """Stop a delayed launch from rebinding a later native target."""
    from .runtime import current_owner_id

    owner = str(owner_id or current_owner_id())
    with _PENDING_LAUNCH_LOCK:
        pending = _PENDING_LAUNCHES.pop(owner, None)
        if pending is not None:
            pending.set()
    return pending is not None


def release_owner(owner_id: str) -> None:
    """Drop COM references and pending launch work for one closed conversation."""
    cancel_pending_launch_verification(owner_id)
    _STATES.pop(owner_id, None)


def _state() -> _UIAState:
    from .runtime import current_owner_id

    owner = current_owner_id()
    state = _STATES.get(owner)
    if state is None:
        state = _UIAState(controls={}, elements={})
        _STATES[owner] = state
    return state


@dataclass(frozen=True)
class DesktopElement:
    ref: str
    name: str
    role: str
    class_name: str
    automation_id: str
    bounds: tuple[int, int, int, int]
    enabled: bool
    focusable: bool
    depth: int
    patterns: tuple[str, ...] = ()
    process_id: int = 0
    native_handle: int = 0
    window_identity: str = ""
    state_signature: str = ""
    observed_at: float = 0.0

    @property
    def label(self) -> str:
        return self.name or self.automation_id or self.class_name or self.role or "element"

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bounds
        return (left + max(0, right - left) // 2, top + max(0, bottom - top) // 2)


@dataclass(frozen=True)
class LaunchWindowBaseline:
    captured: bool
    identities: frozenset[str] = frozenset()
    detail: str = ""


@dataclass(frozen=True)
class LaunchWindowOutcome:
    status: str
    method: str = ""
    target: Any | None = None
    observation: Any | None = None
    element: DesktopElement | None = None
    candidates: tuple[DesktopElement, ...] = ()
    detail: str = ""


def available() -> bool:
    """Return True when the optional Windows UIA package is importable."""
    return importlib.util.find_spec("uiautomation") is not None


def availability_message() -> str:
    if available():
        return "Windows UI Automation available."
    return (
        "Windows UI Automation unavailable: install optional computer-use extras "
        "(`pip install -r requirements-computer-use.txt`)."
    )


def desktop_context(
    *,
    query: str = "",
    target: str = "",
    max_elements: int = 80,
    root: Any | None = None,
) -> str:
    """Return a compact UI tree snapshot with refs scoped to this snapshot."""
    if str(target or "").strip():
        resolved = _resolve(str(target), root=root)
        if isinstance(resolved, str):
            return resolved
        ref, ctrl, element = resolved
        fresh = _describe_control(ctrl, ref, element.depth)
        if not _is_window_element(fresh):
            return f"Error: {ref} is not a top-level desktop window."
        elements = snapshot(root=ctrl, include_root=True, max_depth=SCOPED_FIND_DEPTH, max_elements=max_elements)
        fresh = elements[0]
        target_state = _remember_selected_window(fresh.ref, ctrl, fresh)
        observation = _record_observation("computer_observe", target_state, elements)
        lines = ["[desktop context via Windows UI Automation]"]
        if observation is not None:
            lines.append(_format_observation(observation))
        lines.extend(_format_element(element) for element in elements)
        return "\n".join(lines)
    try:
        elements = snapshot(query=query, max_elements=max_elements, root=root)
    except Exception as exc:  # noqa: BLE001
        return f"Error: computer_observe operation=context failed: {type(exc).__name__}: {exc}"
    target = _remember_context_targets(query, elements)
    if not elements:
        return "[desktop context]\n  (no matching UI Automation elements found)"
    lines = ["[desktop context via Windows UI Automation]"]
    observation = _record_observation("computer_observe", target, elements)
    if observation is not None:
        lines.append(_format_observation(observation))
    lines.extend(_format_element(e) for e in elements)
    return "\n".join(lines)


def desktop_targets(*, query: str = "", max_results: int = 40, root: Any | None = None) -> str:
    """List current top-level windows and their owned dialogs without an action lease."""
    try:
        elements = snapshot(
            query="",
            max_elements=max(1, min(int(max_results or 40) * 3, MAX_ELEMENTS)),
            # Native modal dialogs may be exposed as an owned window one level
            # beneath their disabled top-level owner rather than as a root
            # child. Depth two covers that standard UIA topology without
            # turning target discovery into a general control-tree walk.
            max_depth=2,
            root=root,
        )
    except Exception as exc:  # noqa: BLE001
        return f"Error: computer target discovery failed: {type(exc).__name__}: {exc}"
    windows = [element for element in elements if _is_window_element(element)]
    if query.strip():
        # Chromium window classes are shared by Chrome, Brave and Edge. Match
        # window titles here; a framework class does not identify the browser.
        windows = [element for element in windows if query.strip().casefold() in element.label.casefold()]
    windows = windows[:max(1, int(max_results or 40))]
    if not windows:
        return "[computer targets: desktop windows]\n  (no matching top-level windows or owned dialogs)"
    lines = ["[computer targets: desktop windows]"]
    lines.extend(_format_element(element) for element in windows)
    return "\n".join(lines)


def capture_launch_window_baseline(*, root: Any | None = None) -> LaunchWindowBaseline:
    """Capture identities only; discovery must not grant an action lease."""
    if root is None:
        try:
            from .win32 import top_level_windows

            native_windows = top_level_windows()
            if native_windows:
                return LaunchWindowBaseline(
                    captured=True,
                    identities=frozenset(f"hwnd:{window.handle}" for window in native_windows),
                )
        except Exception:
            pass
    try:
        windows = _top_level_windows(root=root)
    except Exception as exc:  # noqa: BLE001
        return LaunchWindowBaseline(
            captured=False,
            detail=f"{type(exc).__name__}: {exc}",
        )
    return LaunchWindowBaseline(
        captured=True,
        identities=frozenset(_window_identity(element) for element in windows),
    )


def _select_native_launch_windows(
    windows: tuple[Any, ...],
    *,
    app_name: str,
    query: str,
    pid: int,
    baseline: LaunchWindowBaseline,
) -> tuple[str, tuple[Any, ...]]:
    pid_matches = tuple(window for window in windows if pid > 0 and window.process_id == pid)
    if len(pid_matches) == 1:
        return "unique", pid_matches
    if len(pid_matches) > 1:
        return "ambiguous", pid_matches
    if not baseline.captured:
        return "none", ()
    new_windows = tuple(
        window for window in windows
        if f"hwnd:{window.handle}" not in baseline.identities
    )
    named = tuple(
        window
        for window in new_windows
        if _matches_launched_app(
            _native_window_element(window), app_name=app_name, query=query,
        )
    )
    if len(named) == 1:
        return "unique", named
    if len(named) > 1:
        return "ambiguous", named
    return "none", ()


def correlate_launched_window(
    *,
    app_name: str,
    query: str = "",
    pid: int = 0,
    baseline: LaunchWindowBaseline | None = None,
    timeout: float = LAUNCH_WINDOW_TIMEOUT_SECONDS,
    poll_interval: float = LAUNCH_WINDOW_POLL_SECONDS,
    cancel_event: Any | None = None,
    root: Any | None = None,
) -> LaunchWindowOutcome:
    """Bind one causally attributable launched window or return explicit uncertainty."""
    baseline = baseline or LaunchWindowBaseline(captured=False, detail="baseline not captured")
    deadline = time.monotonic() + max(0.0, float(timeout))
    ambiguous: tuple[DesktopElement, ...] = ()
    last_error = ""
    saw_snapshot = False
    while True:
        if cancel_event is not None and cancel_event.is_set():
            return LaunchWindowOutcome(
                "outcome_unknown",
                detail="cancelled after launch dispatch and before window verification",
            )
        native_windows: tuple[Any, ...] = ()
        if root is None:
            try:
                from .win32 import top_level_windows

                native_windows = top_level_windows()
            except Exception as exc:  # noqa: BLE001
                last_error = f"{type(exc).__name__}: {exc}"
        if native_windows:
            saw_snapshot = True
            last_error = ""
            native_status, native_matches = _select_native_launch_windows(
                native_windows,
                app_name=app_name,
                query=query,
                pid=pid,
                baseline=baseline,
            )
            if native_status == "unique":
                method = "process_id" if pid > 0 and native_matches[0].process_id == pid else "new_named_window"
                return _bind_native_launched_window(native_matches[0], method=method)
            if native_status == "ambiguous":
                ambiguous = tuple(_native_window_element(window) for window in native_matches)
        else:
            try:
                windows = _top_level_windows(root=root)
                saw_snapshot = True
                last_error = ""
            except Exception as exc:  # noqa: BLE001
                windows = ()
                last_error = f"{type(exc).__name__}: {exc}"

            pid_matches = tuple(element for element in windows if pid > 0 and element.process_id == pid)
            if len(pid_matches) == 1:
                return _bind_launched_window(pid_matches[0], method="process_id")
            if len(pid_matches) > 1:
                ambiguous = pid_matches
            elif baseline.captured:
                new_windows = tuple(
                    element
                    for element in windows
                    if _window_identity(element) not in baseline.identities
                )
                named = tuple(
                    element
                    for element in new_windows
                    if _matches_launched_app(element, app_name=app_name, query=query)
                )
                if len(named) == 1:
                    return _bind_launched_window(named[0], method="new_named_window")
                if len(named) > 1:
                    ambiguous = named

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        delay = min(max(0.01, float(poll_interval)), remaining)
        if cancel_event is not None:
            if cancel_event.wait(delay):
                return LaunchWindowOutcome(
                    "outcome_unknown",
                    detail="cancelled after launch dispatch and before window verification",
                )
        else:
            time.sleep(delay)

    if ambiguous:
        return LaunchWindowOutcome(
            "outcome_unknown",
            candidates=ambiguous[:8],
            detail="multiple possible application windows appeared; MO did not guess",
        )
    if not saw_snapshot:
        detail = f"Windows UI Automation did not provide a launch observation ({last_error})"
    elif not baseline.captured and pid <= 0:
        detail = "no process id or pre-launch window baseline was available for safe correlation"
    else:
        detail = "no unique matching top-level window appeared before the verification deadline"
    return LaunchWindowOutcome("outcome_unknown", detail=detail)


def continue_launched_window_verification(
    *,
    app_name: str,
    query: str,
    pid: int,
    baseline: LaunchWindowBaseline,
    timeout: float = 12.0,
    poll_interval: float = LAUNCH_WINDOW_POLL_SECONDS,
    owner_id: str = "",
) -> None:
    """Bind a slow-launching native window without blocking the companion turn."""
    from .runtime import current_owner_id

    owner = str(owner_id or current_owner_id())
    cancelled = threading.Event()
    with _PENDING_LAUNCH_LOCK:
        previous = _PENDING_LAUNCHES.get(owner)
        if previous is not None:
            previous.set()
        _PENDING_LAUNCHES[owner] = cancelled

    def verify() -> None:
        deadline = time.monotonic() + max(0.1, float(timeout))
        try:
            from .win32 import top_level_windows

            while not cancelled.is_set():
                windows = top_level_windows()
                status, matches = _select_native_launch_windows(
                    windows,
                    app_name=app_name,
                    query=query,
                    pid=pid,
                    baseline=baseline,
                )
                if status == "unique":
                    # A later native request cancels this verifier before it
                    # chooses its own target. Keep that cancellation and this
                    # final bind atomic so the old launch cannot win the race.
                    with _PENDING_LAUNCH_LOCK:
                        if cancelled.is_set() or _PENDING_LAUNCHES.get(owner) is not cancelled:
                            return
                        _bind_native_runtime_target(matches[0], owner_id=owner)
                    return
                if status == "ambiguous" or time.monotonic() >= deadline:
                    return
                cancelled.wait(max(0.01, float(poll_interval)))
        finally:
            with _PENDING_LAUNCH_LOCK:
                if _PENDING_LAUNCHES.get(owner) is cancelled:
                    _PENDING_LAUNCHES.pop(owner, None)

    threading.Thread(
        target=verify,
        name="mo-desktop-launch-verifier",
        daemon=True,
    ).start()


def _top_level_windows(*, root: Any | None = None) -> tuple[DesktopElement, ...]:
    elements = snapshot(max_elements=MAX_ELEMENTS, max_depth=2, root=root)
    return tuple(element for element in elements if _is_window_element(element))


def _native_window_element(window: Any) -> DesktopElement:
    return DesktopElement(
        ref="",
        name=str(window.title or ""),
        role="WindowControl",
        class_name=str(window.class_name or ""),
        automation_id="",
        bounds=tuple(window.bounds),
        enabled=True,
        focusable=True,
        depth=0,
        process_id=int(window.process_id or 0),
        native_handle=int(window.handle or 0),
        window_identity=f"hwnd:{int(window.handle or 0)}",
        observed_at=time.monotonic(),
    )


def _cache_native_window(window: Any) -> DesktopElement | None:
    if not available():
        return None
    try:
        import uiautomation as auto

        ctrl = auto.ControlFromHandle(int(window.handle))
        candidate = _describe_control(ctrl, "d0", 0, with_patterns=False)
    except Exception:
        return None
    candidate = replace(
        candidate,
        name=candidate.name or str(window.title or ""),
        class_name=candidate.class_name or str(window.class_name or ""),
        bounds=tuple(window.bounds),
        process_id=int(window.process_id or 0),
        native_handle=int(window.handle or 0),
        window_identity=f"hwnd:{int(window.handle or 0)}",
    )
    if not _is_window_element(candidate):
        return None
    state = _state()
    state.snapshot_generation += 1
    ref = f"d{state.snapshot_generation}-1"
    element = replace(candidate, ref=ref, observed_at=time.monotonic())
    state.controls[ref] = ctrl
    state.elements[ref] = element
    return element


def _bind_native_launched_window(window: Any, *, method: str) -> LaunchWindowOutcome:
    element = _cache_native_window(window)
    if element is None:
        return LaunchWindowOutcome(
            "outcome_unknown",
            detail="the unique native application window could not be bound for follow-up control",
        )
    return _bind_launched_window(element, method=method)


def _bind_native_runtime_target(window: Any, *, owner_id: str) -> LaunchWindowOutcome:
    from .runtime import bind_target, record_action, record_observation

    element = _native_window_element(window)
    target = bind_target(
        kind="desktop",
        identity=_window_identity(element),
        label=element.label,
        bounds=element.bounds,
        metadata={
            "process_id": element.process_id,
            "native_handle": element.native_handle,
            "class_name": element.class_name,
        },
        owner_id=owner_id,
    )
    record_action(
        "computer_act",
        target=target,
        observation=None,
        status="executed",
        state_changed=True,
        invalidate=False,
    )
    observation = record_observation(
        "computer_act",
        target=target,
        origin="win32",
        trust="external_untrusted",
        foreground_identity=target.label,
        signature=f"{element.native_handle}|{element.label}|{element.bounds}",
    )
    return LaunchWindowOutcome(
        "verified",
        method="background_native_window",
        target=target,
        observation=observation,
        element=element,
    )


def _matches_launched_app(element: DesktopElement, *, app_name: str, query: str) -> bool:
    label_tokens = _name_tokens(element.label)
    label_set = set(label_tokens)
    if not label_tokens:
        return False
    for value in (app_name, query):
        value = str(value or "").strip()
        if not value or value.startswith("app-"):
            continue
        tokens = _name_tokens(value)
        if tokens and set(tokens) <= label_set:
            return True
    return False


def _name_tokens(value: str) -> tuple[str, ...]:
    return tuple("".join(char if char.isalnum() else " " for char in value.casefold()).split())


def _bind_launched_window(element: DesktopElement, *, method: str) -> LaunchWindowOutcome:
    state = _state()
    ctrl = state.controls.get(element.ref)
    if ctrl is None:
        return LaunchWindowOutcome(
            "outcome_unknown",
            detail="the correlated application window expired before it could be bound",
        )
    target = _remember_selected_window(element.ref, ctrl, element)
    from .runtime import record_action

    record_action(
        "computer_act",
        target=target,
        observation=None,
        status="executed",
        state_changed=True,
        invalidate=False,
    )
    observation = _record_observation("computer_act", target, [element])
    return LaunchWindowOutcome(
        "verified",
        method=method,
        target=target,
        observation=observation,
        element=element,
    )


def desktop_find(query: str, *, target: str = "", max_results: int = 20, root: Any | None = None) -> str:
    """Find UI elements by accessible name, role, class, or automation id."""
    query = str(query or "").strip()
    if not query:
        return "Error: computer_observe operation=find needs a non-empty 'query'."
    try:
        search_root = root
        search_depth = MAX_DEPTH
        if target:
            resolved = _resolve(target, root=root)
            if isinstance(resolved, str):
                return resolved
            ref, search_root, element = resolved
            fresh = _describe_control(search_root, ref, element.depth)
            if not _is_window_element(fresh):
                return f"Error: {ref} is not a desktop window."
            _remember_selected_window(ref, search_root, fresh)
            search_depth = SCOPED_FIND_DEPTH
        if search_root is None:
            recent_roots = _active_context_controls(max_age=TARGET_GUARD_SECONDS)
            if len(recent_roots) == 1:
                search_root = recent_roots[0]
                # Modern application content can sit substantially deeper than its
                # top-level window (Explorer's navigation items are depth eight).
                # Search deeper only inside the already selected window so a normal
                # whole-desktop scan stays bounded and inexpensive.
                search_depth = SCOPED_FIND_DEPTH
        elements = snapshot(
            query=query,
            max_elements=max_results,
            max_depth=search_depth,
            root=search_root,
        )
    except Exception as exc:  # noqa: BLE001
        return f"Error: computer_observe operation=find failed: {type(exc).__name__}: {exc}"
    if not elements:
        return f"[desktop find: {query!r}]\n  (no matches)"
    lines = [f"[desktop find: {query!r}]"]
    observation = _record_active_observation("computer_observe", elements)
    if observation is not None:
        lines.append(_format_observation(observation))
    lines.extend(_format_element(e) for e in elements)
    return "\n".join(lines)


def desktop_annotation_elements(
    *,
    query: str = "",
    target: str = "",
    max_results: int = 8,
    root: Any | None = None,
) -> list[DesktopElement]:
    """Collect annotation elements inside the requested target only."""
    search_root = root
    if str(target or "").strip():
        resolved = _resolve(str(target), root=root)
        if isinstance(resolved, str):
            raise ValueError(resolved.removeprefix("Error: "))
        ref, search_root, element = resolved
        fresh = _describe_control(search_root, ref, element.depth)
        if _is_window_element(fresh):
            _remember_selected_window(ref, search_root, fresh)
    elif search_root is None:
        recent_roots = _active_context_controls(max_age=TARGET_GUARD_SECONDS)
        if len(recent_roots) == 1:
            search_root = recent_roots[0]
    elements = snapshot(
        query=str(query or ""),
        max_elements=max_results,
        max_depth=SCOPED_FIND_DEPTH if search_root is not None else MAX_DEPTH,
        root=search_root,
        include_root=search_root is not None,
    )
    _record_active_observation("computer_observe", elements)
    return elements


def desktop_inspect(target: str, *, root: Any | None = None) -> str:
    """Inspect a current snapshot ref or the first element matching a query."""
    resolved = _resolve(target, root=root)
    if isinstance(resolved, str):
        return resolved
    ref, ctrl, element = resolved
    fresh = replace(_describe_control(ctrl, ref, element.depth), window_identity=element.window_identity)
    lines = ["[desktop inspect]", _format_element(fresh, verbose=True)]
    observation = _record_active_observation("computer_observe", [fresh])
    if observation is not None:
        lines.insert(1, _format_observation(observation))
    if fresh.patterns:
        lines.append("  patterns: " + ", ".join(fresh.patterns))
    return "\n".join(lines)


def desktop_invoke(target: str, *, action: str = "invoke", value: str = "", root: Any | None = None) -> str:
    """Act on a UIA element using semantic patterns before pixel fallback."""
    resolved = _resolve(target, root=root)
    if isinstance(resolved, str):
        return resolved
    ref, ctrl, element = resolved
    runtime_target, observation, guard = _validate_action_target(ctrl, element)
    if guard:
        return guard
    action = str(action or "invoke").strip().lower()
    try:
        if action in {"focus", "set_focus"}:
            _call_first(ctrl, ("SetFocus",))
            result = f"Focused {ref}: {element.label}"
            _record_action("computer_act", runtime_target, observation)
            return result
        if action in {"invoke", "press", "click"}:
            if _invoke_pattern(ctrl):
                result = f"Invoked {ref}: {element.label}"
            else:
                _call_first(ctrl, ("Click",))
                result = f"Clicked {ref}: {element.label}"
            _record_action("computer_act", runtime_target, observation)
            return result
        if action in {"set_value", "value", "type"}:
            if not str(value):
                return "Error: computer_act action=set_value needs 'value'."
            if _set_value(ctrl, str(value)):
                result = f"Set value on {ref}: {element.label}"
                _record_action("computer_act", runtime_target, observation)
                return result
            return f"Error: {ref} does not expose a value/text pattern."
        if action == "toggle":
            pat = _pattern(ctrl, "GetTogglePattern")
            if pat is None:
                return f"Error: {ref} does not expose a toggle pattern."
            _call_first(pat, ("Toggle",))
            result = f"Toggled {ref}: {element.label}"
            _record_action("computer_act", runtime_target, observation)
            return result
        if action == "scroll":
            result = _scroll(ctrl, ref, element, value)
            if not result.startswith("Error:"):
                _record_action("computer_act", runtime_target, observation)
            return result
        return "Error: computer_act semantic action must be invoke, focus_element, click_element, set_value, toggle, or semantic_scroll."
    except Exception as exc:  # noqa: BLE001
        return f"Error: computer_act semantic action failed for {ref}: {type(exc).__name__}: {exc}"


def desktop_wait(
    query: str,
    *,
    state: str = "exists",
    timeout: float = 10.0,
    target: str = "",
    root: Any | None = None,
    cancel_event: Any = None,
) -> str:
    """Wait for a UI element to appear, disappear, or become enabled."""
    query = str(query or "").strip()
    if not query:
        return "Error: computer_observe operation=wait needs a non-empty 'query'."
    search_root = root
    if str(target or "").strip():
        resolved = _resolve(str(target), root=root)
        if isinstance(resolved, str):
            return resolved
        ref, search_root, element = resolved
        fresh = _describe_control(search_root, ref, element.depth)
        if not _is_window_element(fresh):
            return f"Error: {ref} is not a top-level desktop window."
        _remember_selected_window(ref, search_root, fresh)
    elif search_root is None:
        recent_roots = _active_context_controls(max_age=TARGET_GUARD_SECONDS)
        if len(recent_roots) == 1:
            search_root = recent_roots[0]
    state = str(state or "exists").strip().lower()
    deadline = time.time() + max(0.1, float(timeout or 10.0))
    last: list[DesktopElement] = []
    from .runtime import native_desktop_scope

    # An empty result proves absence only inside the explicitly searched window.
    search_identity = ""
    if search_root is not None:
        search_element = _describe_control(search_root, "d0", 0, with_patterns=False)
        if _is_window_element(search_element):
            search_identity = _window_identity(search_element)

    while time.time() <= deadline:
        if cancel_event is not None and cancel_event.is_set():
            return "Error: computer observation cancelled."
        with native_desktop_scope():
            try:
                last = snapshot(query=query, max_elements=5, max_depth=SCOPED_FIND_DEPTH, root=search_root)
            except Exception as exc:  # noqa: BLE001
                return f"Error: computer_observe operation=wait failed: {type(exc).__name__}: {exc}"
            if state in {"exists", "visible", "present"} and last:
                _record_active_observation("computer_observe", last)
                return "computer_observe wait matched:\n" + "\n".join(_format_element(e) for e in last[:3])
            if state in {"gone", "missing", "absent"} and not last:
                _record_active_observation("computer_observe", last, signature=f"gone:{query}", empty_window=search_identity)
                return f"computer_observe wait: {query!r} is gone."
            if state == "enabled" and any(e.enabled for e in last):
                _record_active_observation("computer_observe", last)
                return "computer_observe wait enabled:\n" + "\n".join(_format_element(e) for e in last if e.enabled)
        if cancel_event is not None:
            cancel_event.wait(0.25)
        else:
            time.sleep(0.25)
    if last:
        return f"computer_observe wait timed out waiting for {query!r} state={state}; last matches:\n" + "\n".join(
            _format_element(e) for e in last[:3]
        )
    return f"computer_observe wait timed out waiting for {query!r} state={state}; no matches."


def desktop_window(target: str, *, action: str = "focus", root: Any | None = None) -> str:
    """Focus, minimize, non-destructively hide, or close a top-level window by ref/query."""
    resolved = _resolve(target, root=root)
    if isinstance(resolved, str):
        return resolved
    ref, ctrl, element = resolved
    runtime_target, observation, guard = _validate_action_target(ctrl, element, allow_window_query=True)
    if guard:
        return guard
    action = str(action or "focus").strip().lower()
    try:
        if action == "focus":
            _restore_window(ctrl)
            _call_first(ctrl, ("SetFocus",))
            from .win32 import foreground_window_handle

            hwnd = int(element.native_handle or getattr(ctrl, "NativeWindowHandle", 0) or 0)
            foreground = foreground_window_handle()
            if not hwnd or not foreground:
                return f"Error: focus requested for {ref}, but native foreground confirmation is unavailable."
            if foreground != hwnd:
                return f"Error: focus requested for {ref}, but the target is not the foreground window. Observe the current target before acting."
            _remember_selected_window(ref, ctrl, element)
            result = f"Focused window {ref}: {element.label}"
            _record_action("computer_act", runtime_target, observation)
            return result
        if action == "minimize":
            hwnd = int(element.native_handle or getattr(ctrl, "NativeWindowHandle", 0) or 0)
            if not hwnd:
                return f"Error: {ref} does not expose a native window handle."
            from .runtime import native_desktop_scope
            from .win32 import minimize_window

            with native_desktop_scope(invalidate=True):
                minimized = minimize_window(hwnd)
            if not minimized:
                return f"Error: {ref} was not confirmed minimized."
            result = f"Minimized window {ref}: {element.label}"
            _record_action("computer_act", runtime_target, observation)
            from .runtime import active_target, record_observation

            minimized_target = active_target("desktop")
            if minimized_target is not None:
                record_observation(
                    "computer_observe",
                    target=minimized_target,
                    origin="native_window_state",
                    trust="local_trusted",
                    signature="minimized:1",
                )
            return result
        if action == "hide":
            import ctypes
            from ctypes import wintypes

            hwnd = int(element.native_handle or getattr(ctrl, "NativeWindowHandle", 0) or 0)
            if not hwnd:
                return f"Error: {ref} does not expose a native window handle."
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
            user32.ShowWindow.restype = wintypes.BOOL
            user32.IsWindowVisible.argtypes = [wintypes.HWND]
            user32.IsWindowVisible.restype = wintypes.BOOL
            from .runtime import native_desktop_scope

            with native_desktop_scope(invalidate=True):
                user32.ShowWindow(hwnd, 0)  # SW_HIDE is non-destructive; state remains in the app.
                still_visible = bool(user32.IsWindowVisible(hwnd))
            if still_visible:
                return f"Error: {ref} remained visible after the hide request."
            result = f"Hid window {ref}: {element.label}"
            _record_action("computer_act", runtime_target, observation)
            from .runtime import active_target, record_observation

            hidden_target = active_target("desktop")
            if hidden_target is not None:
                record_observation(
                    "computer_observe",
                    target=hidden_target,
                    origin="native_window_state",
                    trust="local_trusted",
                    signature="visible:0",
                )
            return result
        if action == "close":
            pat = _pattern(ctrl, "GetWindowPattern")
            if pat is None:
                return f"Error: {ref} does not expose a window close pattern."
            _call_first(pat, ("Close",))
            result = f"Closed window {ref}: {element.label}"
            _record_action("computer_act", runtime_target, observation)
            return result
        return "Error: computer_act window action must be focus_window, minimize_window, hide_window, or close_window."
    except Exception as exc:  # noqa: BLE001
        return f"Error: computer_act window action failed for {ref}: {type(exc).__name__}: {exc}"


def snapshot(
    *,
    query: str = "",
    max_elements: int = MAX_ELEMENTS,
    max_depth: int = MAX_DEPTH,
    root: Any | None = None,
    include_root: bool = False,
) -> list[DesktopElement]:
    """Collect visible-ish desktop elements and cache their refs."""
    state = _state()
    if root is None:
        if not available():
            raise RuntimeError(availability_message())
        import uiautomation as auto

        root = auto.GetRootControl()
    max_elements = max(1, min(int(max_elements or MAX_ELEMENTS), MAX_ELEMENTS))
    max_depth = max(1, min(int(max_depth or MAX_DEPTH), max(MAX_DEPTH, SCOPED_FIND_DEPTH)))
    query_text = str(query or "").strip().lower()

    controls: list[tuple[Any, DesktopElement]] = []
    queue = deque([(root, 0, "")])
    while queue and len(controls) < max_elements:
        ctrl, depth, window_identity = queue.popleft()
        candidate = _describe_control(ctrl, "d0", depth, with_patterns=False)
        if _is_window_element(candidate):
            window_identity = _window_identity(candidate)
        if depth > 0 or include_root:
            # Describe once, without probing patterns: deciding whether to LIST a control needs
            # only its bounds and identity, and every pattern probe is a cross-process COM call.
            if _worth_listing(candidate) and (not query_text or _matches(ctrl, query_text)):
                controls.append((ctrl, replace(candidate, window_identity=window_identity)))
        if depth >= max_depth:
            continue
        for child in _children(ctrl):
            queue.append((child, depth + 1, window_identity))

    # Read-only discovery must not erase another still-fresh observation.
    # Keep bounded observation refs; input still revalidates the actual control
    # and the owner's current target revision before it can act.
    now = time.monotonic()
    expired = [ref for ref, element in state.elements.items()
               if now - element.observed_at > TARGET_GUARD_SECONDS]
    for ref in expired:
        state.elements.pop(ref, None)
        state.controls.pop(ref, None)
    state.snapshot_generation += 1
    elements: list[DesktopElement] = []
    for index, (ctrl, candidate) in enumerate(controls, start=1):
        ref = f"d{state.snapshot_generation}-{index}"
        patterns, state_signature = _pattern_state(ctrl)
        element = replace(candidate, ref=ref, patterns=patterns, state_signature=state_signature, observed_at=now)
        state.controls[ref] = ctrl
        state.elements[ref] = element
        elements.append(element)
    for ref in list(state.elements)[:-4 * MAX_ELEMENTS]:
        state.elements.pop(ref, None)
        state.controls.pop(ref, None)
    return elements


def cached_elements() -> list[DesktopElement]:
    return list(_state().elements.values())


def action_target_summary(target: str) -> str:
    """Safe current-cache label for action-time policy classification."""
    value = str(target or "").strip()
    element = _state().elements.get(value)
    if element is None:
        return value[:160]
    return f"{element.role or 'element'} {element.label}"[:160]


def refresh_active_target(target: str = "") -> Any | None:
    """Re-read the bound top-level window before a target crop."""
    from .runtime import active_target

    if target:
        resolved = _resolve(target)
        if isinstance(resolved, str):
            raise ValueError(resolved)
        ref, ctrl, element = resolved
        fresh = _describe_control(ctrl, ref, element.depth, with_patterns=False)
        if not _is_window_element(fresh):
            raise ValueError("capture target must be an exact desktop window")
        return _remember_selected_window(ref, ctrl, fresh)
    target = active_target("desktop")
    if target is None:
        return None
    state = _state()
    for ctrl, remembered in zip(state.context_controls, state.context_windows):
        try:
            fresh = _describe_control(ctrl, remembered.ref, remembered.depth, with_patterns=False)
        except Exception:
            continue
        if _window_identity(fresh) == target.identity and _is_window_element(fresh):
            state.context_windows = (fresh,)
            return _bind_window_target(fresh)
    return target


def _rehydrate_runtime_window(target: Any) -> tuple[str, Any, DesktopElement] | None:
    """Lazily attach UIA to a Win32-bound runtime target on the acting thread."""
    metadata = dict(getattr(target, "metadata", {}) or {})
    handle = int(metadata.get("native_handle") or 0)
    if handle <= 0:
        identity = str(getattr(target, "identity", "") or "")
        if identity.startswith("hwnd:"):
            try:
                handle = int(identity.partition(":")[2])
            except ValueError:
                return None
    if handle <= 0:
        return None
    from .win32 import NativeWindow

    element = _cache_native_window(NativeWindow(
        handle=handle,
        process_id=int(metadata.get("process_id") or 0),
        title=str(getattr(target, "label", "") or ""),
        class_name=str(metadata.get("class_name") or ""),
        bounds=tuple(getattr(target, "bounds", None) or (0, 0, 0, 0)),
    ))
    if element is None or _window_identity(element) != str(getattr(target, "identity", "") or ""):
        return None
    state = _state()
    ctrl = state.controls[element.ref]
    state.context_at = time.time()
    state.context_windows = (element,)
    state.context_controls = (ctrl,)
    return element.ref, ctrl, element


def _resolve(target: str, *, root: Any | None = None) -> tuple[str, Any, DesktopElement] | str:
    target = str(target or "").strip()
    if not target:
        return "Error: target is required (a current snapshot ref like d3-1, or a query)."
    state = _state()
    from .runtime import active_target

    current = active_target("desktop")
    if current is not None and target == current.target_id:
        for ctrl, element in zip(state.context_controls, state.context_windows):
            if _window_identity(element) == current.identity:
                return element.ref, ctrl, element
        rehydrated = _rehydrate_runtime_window(current)
        if rehydrated is not None:
            return rehydrated
    if target in state.controls and target in state.elements:
        return target, state.controls[target], state.elements[target]
    generation, separator, index = target[1:].partition("-") if target.startswith("d") else ("", "", "")
    if separator and generation.isdigit() and index.isdigit():
        return (
            f"Error: cached desktop ref {target!r} expired. Use the refs in the latest "
            "computer_observe context/find result. "
            "If the intended target is absent, observe it again and use the newly returned ref."
        )
    elements = snapshot(query=target, max_elements=1, root=root)
    if not elements:
        return f"Error: no desktop element found for {target!r}."
    ref = elements[0].ref
    state = _state()
    return ref, state.controls[ref], state.elements[ref]


def _remember_context_targets(query: str, elements: list[DesktopElement]) -> Any | None:
    state = _state()
    query_text = str(query or "").strip()
    # An unfiltered whole-desktop read is context, not target selection. Even if
    # only one window happened to be returned by a bounded scan, the operator/model
    # did not name it; require an explicit query before granting an actuation lease.
    candidates = tuple(element for element in elements if _is_window_element(element)) if query_text else ()
    windows = candidates if len(candidates) == 1 else ()
    if len(windows) != 1:
        return None
    element = windows[0]
    return _remember_selected_window(element.ref, state.controls[element.ref], element)


def _remember_selected_window(ref: str, ctrl: Any, element: DesktopElement) -> Any:
    state = _state()
    element = replace(element, window_identity=_window_identity(element), observed_at=time.monotonic())
    state.controls[ref] = ctrl
    state.elements[ref] = element
    state.context_at = time.time()
    state.context_windows = (element,)
    state.context_controls = (ctrl,)
    return _bind_window_target(element)


def _active_context_windows(*, max_age: float) -> tuple[DesktopElement, ...]:
    state = _state()
    if not state.context_windows:
        return ()
    if time.time() - state.context_at > max(0.1, float(max_age or TARGET_GUARD_SECONDS)):
        return ()
    return state.context_windows


def _active_context_controls(*, max_age: float) -> tuple[Any, ...]:
    if not _active_context_windows(max_age=max_age):
        return ()
    return _state().context_controls


def _is_window_element(element: DesktopElement) -> bool:
    role = str(element.role or "").lower()
    left, top, right, bottom = element.bounds
    window = "window" in role or ("pane" in role and element.native_handle and element.depth <= 1)
    return bool(window and right > left and bottom > top)


def _point_in_bounds(x: int, y: int, bounds: tuple[int, int, int, int]) -> bool:
    left, top, right, bottom = bounds
    return left <= x <= right and top <= y <= bottom


def _window_identity(element: DesktopElement) -> str:
    if element.native_handle:
        return f"hwnd:{element.native_handle}"
    if element.process_id:
        return f"pid:{element.process_id}:{element.class_name}:{element.label}"
    return f"uia:{element.class_name}:{element.role}:{element.label}"


def _bind_window_target(element: DesktopElement) -> Any:
    from .runtime import bind_target

    return bind_target(
        kind="desktop",
        identity=_window_identity(element),
        label=element.label,
        bounds=element.bounds,
        metadata={
            "process_id": element.process_id,
            "native_handle": element.native_handle,
            "class_name": element.class_name,
        },
    )


def _observation_signature(elements: list[DesktopElement]) -> str:
    normalized = "\n".join(
        f"{e.label}|{e.role}|{e.automation_id}|{e.bounds}|{e.enabled}|{e.focusable}|{e.state_signature}"
        for e in elements
    )
    return hashlib.sha256(normalized.encode("utf-8", errors="replace")).hexdigest()[:20]


def _record_observation(tool: str, target: Any | None, elements: list[DesktopElement], *, signature: str = "") -> Any | None:
    if target is None:
        return None
    from .runtime import record_observation

    return record_observation(
        tool,
        target=target,
        origin="uia",
        trust="external_untrusted",
        foreground_identity=target.label,
        signature=signature or _observation_signature(elements),
    )


def _record_active_observation(tool: str, elements: list[DesktopElement], *, signature: str = "", empty_window: str = "") -> Any | None:
    from .runtime import active_target

    target = active_target("desktop")
    identities = {element.window_identity for element in elements} if elements else {empty_window}
    if target is None or identities != {target.identity}:
        return None
    return _record_observation(tool, target, elements, signature=signature)


def _format_observation(observation: Any) -> str:
    return (
        "  [observation "
        f"id={observation.observation_id} target={observation.target_id} "
        f"revision={observation.target_revision} origin={observation.origin} "
        f"trust={observation.trust} window={observation.foreground_identity!r}]"
    )


def _validate_action_target(ctrl: Any, element: DesktopElement, *, allow_window_query: bool = False) -> tuple[Any | None, Any | None, str | None]:
    """Re-read identity/bounds and validate the active owner-scoped observation."""
    from .runtime import active_target, latest_observation, validate_action

    try:
        from core.runtime.backend_monitor import current_monitor_context

        runtime_enforced = bool(current_monitor_context())
    except Exception:
        runtime_enforced = False

    runtime_target = active_target("desktop")
    observation = latest_observation("desktop")
    if not runtime_enforced:
        return runtime_target, observation, None
    if runtime_target is None and allow_window_query and _is_window_element(element):
        runtime_target = _bind_window_target(element)
        observation = _record_observation("computer_observe", runtime_target, [element])
    if runtime_target is None:
        return None, None, "Error: no owned desktop target is active; run computer_observe kind=desktop operation=context for one window before acting."
    fresh = _describe_control(ctrl, element.ref, element.depth, with_patterns=False)
    window_identity = element.window_identity
    top_level = _attr(ctrl, "GetTopLevelControl", default=None)
    if top_level is not None:
        window_identity = _window_identity(_describe_control(top_level, "d0", 0, with_patterns=False))
        if window_identity != runtime_target.identity:
            # Owned popups can report the application root as their top level.
            # Re-read the actual parent chain instead of trusting cached ancestry.
            parent = ctrl
            for _ in range(SCOPED_FIND_DEPTH):
                described = _describe_control(parent, "d0", 0, with_patterns=False)
                if _window_identity(described) == runtime_target.identity:
                    window_identity = runtime_target.identity
                    break
                parent = _attr(parent, "GetParentControl", default=None)
                if parent is None:
                    break
    if window_identity != runtime_target.identity:
        return runtime_target, observation, "Error: the UI element belongs to a different desktop window; observe and select that window before acting."
    if (
        (not _is_window_element(element) and fresh.label != element.label)
        or fresh.role != element.role
        or fresh.native_handle != element.native_handle
        or fresh.process_id != element.process_id
        or fresh.automation_id != element.automation_id
        or fresh.bounds != element.bounds
    ):
        return runtime_target, observation, "Error: the UI element changed after observation; run computer_observe operation=context or operation=find again."
    if runtime_target.bounds and not _point_in_bounds(*fresh.center, runtime_target.bounds):
        return runtime_target, observation, "Error: the UI element is outside the active owned desktop target."
    _target, observation, error = validate_action("desktop")
    return runtime_target, observation, error


def _record_action(tool: str, target: Any | None, observation: Any | None) -> None:
    from .runtime import record_action

    try:
        from core.runtime.backend_monitor import current_monitor_context

        runtime_enforced = bool(current_monitor_context())
    except Exception:
        runtime_enforced = False
    if runtime_enforced and target is not None:
        record_action(
            tool,
            target=target,
            observation=observation,
            status="executed",
            state_changed=True,
        )


def _describe_control(ctrl: Any, ref: str, depth: int, *, with_patterns: bool = True) -> DesktopElement:
    """Snapshot a control's properties. ``with_patterns=False`` skips the six pattern probes."""
    patterns, state_signature = _pattern_state(ctrl) if with_patterns else ((), "")
    return DesktopElement(
        ref=ref,
        name=_text_attr(ctrl, "Name"),
        role=_text_attr(ctrl, "ControlTypeName", "LocalizedControlType", "ControlType"),
        class_name=_text_attr(ctrl, "ClassName"),
        automation_id=_text_attr(ctrl, "AutomationId", "AutomationID"),
        bounds=_rect_tuple(_attr(ctrl, "BoundingRectangle")),
        enabled=bool(_attr(ctrl, "IsEnabled", default=True)),
        focusable=bool(_attr(ctrl, "IsKeyboardFocusable", default=False)),
        depth=int(depth),
        patterns=patterns,
        state_signature=state_signature,
        process_id=int(_attr(ctrl, "ProcessId", default=0) or 0),
        native_handle=int(_attr(ctrl, "NativeWindowHandle", default=0) or 0),
    )


def _format_element(e: DesktopElement, *, verbose: bool = False) -> str:
    left, top, right, bottom = e.bounds
    width = max(0, right - left)
    height = max(0, bottom - top)
    parts = [f"[{e.ref}]", e.role or "element", repr(e.label)]
    if e.automation_id:
        parts.append(f"id={e.automation_id}")
    if e.class_name:
        parts.append(f"class={e.class_name}")
    parts.append(f"bounds=({left},{top},{width}x{height})")
    if not e.enabled:
        parts.append("disabled")
    elif verbose:
        parts.append("enabled")
    if e.focusable:
        parts.append("focusable")
    return "  " + " ".join(parts)


def _attr(obj: Any, name: str, default: Any = "") -> Any:
    try:
        value = getattr(obj, name)
        return value() if callable(value) and name.startswith("Get") else value
    except Exception:
        return default


def _text_attr(obj: Any, *names: str) -> str:
    for name in names:
        value = _attr(obj, name)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    return ""


def _rect_tuple(rect: Any) -> tuple[int, int, int, int]:
    if rect is None:
        return (0, 0, 0, 0)
    for names in (("left", "top", "right", "bottom"), ("Left", "Top", "Right", "Bottom")):
        try:
            return tuple(int(getattr(rect, n)) for n in names)  # type: ignore[return-value]
        except Exception:
            pass
    try:
        vals = list(rect)
        if len(vals) >= 4:
            return tuple(int(v) for v in vals[:4])  # type: ignore[return-value]
    except Exception:
        pass
    return (0, 0, 0, 0)


def _children(ctrl: Any) -> list[Any]:
    for name in ("GetChildren", "GetChildrenControl", "Children"):
        try:
            value = getattr(ctrl, name)
            children = value() if callable(value) else value
            if children:
                return list(children)
        except Exception:
            continue
    return []


def _worth_listing(element: DesktopElement) -> bool:
    left, top, right, bottom = element.bounds
    has_size = right > left and bottom > top
    has_identity = bool(element.name or element.role or element.class_name or element.automation_id)
    if _role_is_window(element.role):
        return has_identity
    return has_size and has_identity


def _matches(ctrl: Any, query: str) -> bool:
    """Match against the control's raw text properties, not a described element.

    ``DesktopElement.role`` keeps only the FIRST non-empty of ControlTypeName /
    LocalizedControlType, so matching off the element would stop finding controls whose query
    hits the other one. The extra fetch buys exact search semantics.
    """
    hay = " ".join(
        _text_attr(ctrl, name)
        for name in ("Name", "ControlTypeName", "LocalizedControlType", "ClassName", "AutomationId", "AutomationID")
    ).lower()
    return query in hay


def _role_is_window(role: str) -> bool:
    return "window" in str(role or "").lower()


def _pattern_state(ctrl: Any) -> tuple[tuple[str, ...], str]:
    names: list[str] = []
    state = [f"focused={bool(_attr(ctrl, 'HasKeyboardFocus', default=False))}"]
    password = bool(_attr(ctrl, "IsPassword", default=False))
    for getter, label in (
        ("GetInvokePattern", "invoke"),
        ("GetValuePattern", "value"),
        ("GetTextPattern", "text"),
        ("GetWindowPattern", "window"),
        ("GetTogglePattern", "toggle"),
        ("GetScrollPattern", "scroll"),
    ):
        pattern = _pattern(ctrl, getter)
        if pattern is not None:
            names.append(label)
            if label == "value" and not password:
                state.append(str(_attr(pattern, "Value")))
            elif label == "toggle":
                state.append(str(_attr(pattern, "ToggleState")))
            elif label == "scroll":
                state.extend(str(_attr(pattern, attr)) for attr in ("HorizontalScrollPercent", "VerticalScrollPercent"))
    return tuple(names), hashlib.sha256("\n".join(state).encode("utf-8", errors="replace")).hexdigest()[:20]


def _pattern(ctrl: Any, getter: str) -> Any | None:
    try:
        fn = getattr(ctrl, getter)
    except Exception:
        return None
    try:
        return fn()
    except Exception:
        return None


def _invoke_pattern(ctrl: Any) -> bool:
    pat = _pattern(ctrl, "GetInvokePattern")
    if pat is None:
        return False
    _call_first(pat, ("Invoke",))
    return True


def _set_value(ctrl: Any, value: str) -> bool:
    pat = _pattern(ctrl, "GetValuePattern")
    if pat is not None:
        try:
            _call_first(pat, ("SetValue",), value)
            return True
        except Exception:
            pass
    for name in ("SetValue", "SendKeys"):
        try:
            _call_first(ctrl, (name,), value)
            return True
        except Exception:
            continue
    return False


def _scroll(ctrl: Any, ref: str, element: DesktopElement, value: str) -> str:
    """Scroll an element through its UIA ScrollPattern.

    ``snapshot`` already reports ``scroll`` in an element's patterns, so an element the model
    was told is scrollable must actually scroll. Direction comes in as ``value``.
    """
    key = str(value or "down").strip().lower().replace(" ", "_").replace("-", "_")
    amounts = _SCROLL_DIRECTIONS.get(key)
    if amounts is None:
        return (
            "Error: computer_act action=semantic_scroll needs value=up, down, left, right, "
            "page_up, page_down, page_left, or page_right."
        )
    pat = _pattern(ctrl, "GetScrollPattern")
    if pat is None:
        return f"Error: {ref} does not expose a scroll pattern."
    horizontal, vertical = amounts
    if vertical != _SCROLL_NO_AMOUNT and not _attr(pat, "VerticallyScrollable", default=True):
        return f"Error: {ref} is not vertically scrollable."
    if horizontal != _SCROLL_NO_AMOUNT and not _attr(pat, "HorizontallyScrollable", default=True):
        return f"Error: {ref} is not horizontally scrollable."
    # ScrollPattern.Scroll returns a bool. Discarding it let MO report "Scrolled ..." for a scroll
    # that never moved — an element already at the end returns False. Report what happened.
    scrolled = _call_first(pat, ("Scroll",), horizontal, vertical)
    if scrolled is False:
        return f"Error: {ref} did not scroll {key.replace('_', ' ')} (already at the end?)."
    return f"Scrolled {ref} {key.replace('_', ' ')}: {element.label}"


def _call_first(obj: Any, names: tuple[str, ...], *args: Any) -> Any:
    from .runtime import native_desktop_scope

    last_exc: Exception | None = None
    for name in names:
        try:
            fn = getattr(obj, name)
            with native_desktop_scope(invalidate=True):
                return fn(*args)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    if last_exc:
        raise last_exc
    raise AttributeError(", ".join(names))


def _restore_window(ctrl: Any) -> None:
    pat = _pattern(ctrl, "GetWindowPattern")
    if pat is None:
        return
    for state in (0, "Normal"):
        try:
            _call_first(pat, ("SetWindowVisualState",), state)
            return
        except Exception:
            continue
