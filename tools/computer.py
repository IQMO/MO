"""Canonical target/observe/act facade for provider-facing computer use.

This module owns the compact action vocabulary and the exact target discovery
surface. Backends stay lazy so importing MO does not load UIA, Pillow,
pyautogui, or CDP.
"""
from __future__ import annotations

from typing import Any

from core.desktop.tool_actions import normalize_computer_call


def execute_computer_targets(arguments: dict[str, Any]) -> str:
    if arguments.get("_cancel_event") is not None and arguments["_cancel_event"].is_set():
        return "Error: computer operation cancelled."
    try:
        call = normalize_computer_call("computer_targets", arguments)
    except ValueError as exc:
        return f"Error: {exc}."
    assert call is not None
    args = call.arguments
    query = str(args.get("query") or "")
    try:
        requested = int(args.get("max_results", 40) or 40)
    except (TypeError, ValueError):
        requested = 40
    max_results = max(1, min(requested, 80))
    if call.kind == "windows":
        from core.desktop.uia import desktop_targets

        return desktop_targets(query=query, max_results=max_results)
    if call.kind == "applications":
        from core.desktop.apps import format_discovered_apps

        return format_discovered_apps(query=query, max_results=max_results)
    if call.kind == "owned":
        from core.desktop.runtime import current_targets

        targets = current_targets()
        if query:
            needle = query.casefold()
            targets = [target for target in targets if needle in f"{target.kind} {target.label}".casefold()]
        if not targets:
            return "[computer targets: owned]\n  (no active owned targets)"
        lines = ["[computer targets: owned]"]
        for target in targets[:max_results]:
            lines.append(
                f"  [{target.target_id}] kind={target.kind} label={target.label!r} "
                f"revision={target.revision} bounds={target.bounds}"
            )
        return "\n".join(lines)
    if call.kind == "screen":
        from .desktop import _screen_dimensions

        return _screen_dimensions(args)
    if call.kind == "browser":
        from .browser import shared_targets

        return shared_targets(query=query, max_results=max_results, cancel_event=args.get("_cancel_event"))
    return "Error: computer_targets kind must be windows, applications, owned, screen, or browser."


def execute_computer_observe(arguments: dict[str, Any]) -> str:
    if arguments.get("_cancel_event") is not None and arguments["_cancel_event"].is_set():
        return "Error: computer operation cancelled."
    try:
        call = normalize_computer_call("computer_observe", arguments)
    except ValueError as exc:
        return f"Error: {exc}."
    assert call is not None
    args = call.arguments
    if call.kind in {"desktop", "screen"}:
        from core.desktop.uia import cancel_pending_launch_verification

        cancel_pending_launch_verification()
    if call.kind == "desktop":
        from . import desktop_semantic

        executors = {
            "desktop_context": desktop_semantic._execute_desktop_context,
            "desktop_find": desktop_semantic._execute_desktop_find,
            "desktop_inspect": desktop_semantic._execute_desktop_inspect,
            "desktop_annotate": desktop_semantic._execute_desktop_annotate,
            "desktop_wait": desktop_semantic._execute_desktop_wait,
        }
        from core.desktop.runtime import native_desktop_scope

        if call.engine_tool == "desktop_wait":
            return executors[call.engine_tool](args)
        with native_desktop_scope():
            return executors[call.engine_tool](args)
    if call.kind == "screen":
        from .screen import _execute_capture_screen

        from core.desktop.runtime import native_desktop_scope

        with native_desktop_scope():
            return _execute_capture_screen(args)
    if call.kind == "browser":
        from .browser import (
            _execute_browser_read_page,
            _execute_browser_snapshot,
            _execute_browser_wait,
            _execute_browser_capture,
        )

        executors = {
            "browser_snapshot": _execute_browser_snapshot,
            "browser_read_page": _execute_browser_read_page,
            "browser_wait": _execute_browser_wait,
            "browser_capture": _execute_browser_capture,
        }
        return executors[call.engine_tool](args)
    return "Error: computer_observe kind must be desktop, screen, or browser."


def _execute_app_launch(args: dict[str, Any]) -> str:
    from core.desktop.apps import format_launch_result, launch_resolved_app, resolve_app
    from core.desktop.uia import (
        capture_launch_window_baseline,
        continue_launched_window_verification,
        correlate_launched_window,
    )

    value = str(args.get("app") or args.get("target") or "")
    resolved = resolve_app(value)
    if resolved.app is None:
        return format_launch_result(launch_resolved_app(resolved))

    baseline = capture_launch_window_baseline()
    launched = launch_resolved_app(resolved)
    if launched.status != "dispatched" or launched.app is None:
        return format_launch_result(launched)

    outcome = correlate_launched_window(
        app_name=launched.app.name,
        query=launched.query,
        pid=launched.pid,
        baseline=baseline,
        cancel_event=args.get("_cancel_event"),
    )
    if outcome.status == "verified" and outcome.target is not None and outcome.observation is not None:
        target = outcome.target
        observation = outcome.observation
        return (
            f"Opened {launched.app.name!r} and verified desktop target "
            f"{target.target_id}: {target.label!r} via {outcome.method}.\n"
            "  [observation "
            f"id={observation.observation_id} target={observation.target_id} "
            f"revision={observation.target_revision} origin={observation.origin} "
            f"trust={observation.trust} window={observation.foreground_identity!r}]"
        )

    from core.desktop.runtime import current_owner_id, record_action

    cancel_event = args.get("_cancel_event")
    pending = not outcome.candidates and not (
        cancel_event is not None and cancel_event.is_set()
    )
    if pending:
        continue_launched_window_verification(
            app_name=launched.app.name,
            query=launched.query,
            pid=launched.pid,
            baseline=baseline,
            owner_id=current_owner_id(),
        )
    record_action(
        "computer_act",
        target=None,
        observation=None,
        status="outcome_unknown",
        state_changed=None,
        error_class="launch_window_pending" if pending else "launch_window_unverified",
        invalidate=False,
    )
    if pending:
        return (
            f"{format_launch_result(launched)}\n"
            "Verification continues locally; the user can keep chatting. Report the app as opening, not opened."
        )
    detail = outcome.detail or "the requested application window could not be verified"
    return (
        f"Could not verify a window for {launched.app.name!r}; do not claim it opened. "
        f"Reason: {detail}."
    )


def execute_computer_act(arguments: dict[str, Any]) -> str:
    if arguments.get("_cancel_event") is not None and arguments["_cancel_event"].is_set():
        return "Error: computer operation cancelled."
    try:
        call = normalize_computer_call("computer_act", arguments)
    except ValueError as exc:
        return f"Error: {exc}."
    assert call is not None
    args = call.arguments
    if call.kind == "desktop":
        from core.desktop.uia import cancel_pending_launch_verification

        cancel_pending_launch_verification()
        if call.operation == "open":
            import webbrowser

            url = str(args.get("url") or "").strip()
            if not url:
                return "Error: computer_act action=open requires a 'url'."
            if not url.startswith(("http://", "https://", "file:", "mailto:", "about:")):
                url = "https://" + url
            try:
                opened = webbrowser.open(url)
            except Exception as exc:
                return f"Error: could not open the default browser: {type(exc).__name__}: {exc}"
            if opened:
                return f"Opened {url} in your default browser."
            return f"Requested to open {url}, but no default browser handler confirmed success."
        from core.desktop.runtime import native_desktop_scope

        if call.engine_tool == "desktop_recipe_run":
            from .desktop_recipe import _execute_desktop_recipe_run

            return _execute_desktop_recipe_run(args)
        with native_desktop_scope():
            if args.get("_cancel_event") is not None and args["_cancel_event"].is_set():
                return "Error: computer operation cancelled."
            if call.operation == "launch":
                return _execute_app_launch(args)
            from . import desktop, desktop_semantic

            executors = {
                "desktop_invoke": desktop_semantic._execute_desktop_invoke,
                "desktop_window": desktop_semantic._execute_desktop_window,
                "mouse_click": desktop._execute_mouse_click,
                "move_pointer": desktop._execute_move_pointer,
                "drag_pointer": desktop._execute_drag_pointer,
                "scroll_pointer": desktop._execute_scroll_pointer,
                "type_text": desktop._execute_type_text,
                "press_key": desktop._execute_press_key,
            }
            executor = executors.get(call.engine_tool)
            if executor is not None:
                from core.desktop.runtime import active_target, latest_observation

                target_before = active_target("desktop") or active_target("screen")
                before = latest_observation("desktop") or latest_observation("screen")
                rejection = ""
                if call.engine_tool not in {"desktop_invoke", "desktop_window"} and args.get("target"):
                    target = target_before
                    expected = str(args["target"])
                    if target is None:
                        rejection = "Error: no observed native target; observe the requested target first."
                    elif expected != target.target_id:
                        from core.desktop.uia import _resolve, _window_identity

                        try:
                            resolved = _resolve(expected)
                            matches = not isinstance(resolved, str) and _window_identity(resolved[2]) == target.identity
                        except Exception:
                            matches = False
                        if not matches:
                            rejection = "Error: action target differs from the observed native target; observe the requested target first."
                from core.desktop.runtime import native_input_revision

                native_revision_before = native_input_revision()
                result = rejection or executor(args)
                if result.startswith("Error:"):
                    from core.desktop.runtime import record_action

                    if (
                        target_before is not None
                        and before is not None
                        and native_input_revision() != native_revision_before
                    ):
                        record_action(
                            "computer_act",
                            target=target_before,
                            observation=before,
                            status="outcome_unknown",
                            state_changed=None,
                            error_class="native_input_error",
                            invalidate=False,
                        )
                        result += (
                            "\nNative input may have partially executed before the error; "
                            "observe the same target before deciding the outcome."
                        )
                    else:
                        record_action(
                            "computer_act", target=target_before, observation=before,
                            status="rejected", state_changed=False,
                            error_class="native_validation", invalidate=False,
                        )
                    return result
                if call.operation == "move":
                    return result
                cancel_event = args.get("_cancel_event")
                if cancel_event is not None and cancel_event.is_set():
                    return result + "\nCancelled before result observation; outcome unverified."
                if call.operation in {"minimize_window", "hide_window"}:
                    # The UIA owner records a post-action native state observation;
                    # hidden/minimized windows need no full semantic resnapshot.
                    return result
                target = active_target("desktop") or active_target("screen")
                if target is None:
                    return result + "\nResult observation unavailable: no active desktop target."
                pixels = desktop._as_bool(args.get("from_capture")) or getattr(before, "origin", "") == "pixels"
                if pixels:
                    from core.tooling.sandbox import guard_tool_call

                    blocked = guard_tool_call("computer_observe", {"kind": "screen"},
                                              sandbox_config=args.get("_sandbox_config", {}))
                    if blocked:
                        return result + "\nResult observation unavailable: " + blocked
                refreshed = execute_computer_observe({
                    "kind": "screen" if pixels else "desktop",
                    "target": target.target_id if target.kind == "desktop" else "",
                    "_cancel_event": cancel_event,
                })
                if refreshed.startswith("Error:"):
                    return result + "\nResult observation failed; do not repeat the action.\n" + refreshed
                return result + "\n" + refreshed
    if call.kind == "browser":
        from . import browser

        executors = {
            "browser_open": browser._execute_browser_open,
            "browser_click": browser._execute_browser_click,
            "browser_type": browser._execute_browser_type,
            "browser_key": browser._execute_browser_key,
            "browser_eval": browser._execute_browser_eval,
        }
        return executors[call.engine_tool](args)
    return "Error: computer_act kind must be desktop or browser."
