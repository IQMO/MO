"""Semantic desktop tools backed by Windows UI Automation.

The UIA package is optional and lazy. These executors provide model-facing text
and keep any overlay display on the safe guided side; actual invoke/window
actions are sandboxed as actuation tools.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from core.runtime.subprocess_flags import apply_windows_hidden_process_flags, gui_python_executable

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _execute_desktop_context(arguments: dict[str, Any]) -> str:
    from core.desktop import uia

    return uia.desktop_context(
        query=str(arguments.get("query") or ""),
        target=str(arguments.get("target") or arguments.get("ref") or ""),
        max_elements=int(arguments.get("max_elements", 80) or 80),
    )


def _execute_desktop_find(arguments: dict[str, Any]) -> str:
    from core.desktop import uia

    return uia.desktop_find(
        str(arguments.get("query") or ""),
        target=str(arguments.get("target") or ""),
        max_results=int(arguments.get("max_results", 20) or 20),
    )


def _execute_desktop_inspect(arguments: dict[str, Any]) -> str:
    from core.desktop import uia

    return uia.desktop_inspect(str(arguments.get("target") or arguments.get("ref") or arguments.get("element_id") or ""))


def _execute_desktop_invoke(arguments: dict[str, Any]) -> str:
    from core.desktop import uia

    return uia.desktop_invoke(
        str(arguments.get("target") or arguments.get("ref") or ""),
        action=str(arguments.get("action") or "invoke"),
        value=str(arguments.get("value") or ""),
    )


def _execute_desktop_wait(arguments: dict[str, Any]) -> str:
    from core.desktop import uia

    return uia.desktop_wait(
        str(arguments.get("query") or ""),
        state=str(arguments.get("state") or "exists"),
        timeout=float(arguments.get("timeout", 10.0) or 10.0),
        target=str(arguments.get("target") or arguments.get("ref") or ""),
        cancel_event=arguments.get("_cancel_event"),
    )


def _execute_desktop_window(arguments: dict[str, Any]) -> str:
    from core.desktop import uia

    return uia.desktop_window(
        str(arguments.get("target") or arguments.get("ref") or ""),
        action=str(arguments.get("action") or "focus"),
    )


def _execute_desktop_annotate(arguments: dict[str, Any]) -> str:
    from core.desktop import uia

    query = str(arguments.get("query") or "")
    max_results = int(arguments.get("max_results", 8) or 8)
    seconds = float(arguments.get("seconds", 5.0) or 5.0)
    try:
        elements = uia.desktop_annotation_elements(
            query=query,
            target=str(arguments.get("target") or arguments.get("ref") or ""),
            max_results=max_results,
        )
    except Exception as exc:  # noqa: BLE001
        return f"Error: computer_observe operation=annotate failed: {type(exc).__name__}: {exc}"
    if not elements:
        return "[desktop annotate]\n  (no matching elements)"
    payload = [
        {
            "ref": e.ref,
            "label": e.label,
            "role": e.role,
            "bounds": list(e.bounds),
        }
        for e in elements[:max(1, min(max_results, 12))]
    ]
    try:
        popen_kwargs = {
            "cwd": _REPO_ROOT,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        apply_windows_hidden_process_flags(popen_kwargs)
        subprocess.Popen(
            [gui_python_executable(), "-m", "interface.desktop_annotation", json.dumps(payload), str(seconds)],
            **popen_kwargs,
        )
    except Exception as exc:  # noqa: BLE001
        return f"Error: annotation overlay failed: {type(exc).__name__}: {exc}"
    lines = [f"[desktop annotate: showing {len(payload)} label(s)]"]
    lines.extend(f"  [{item['ref']}] {item['role'] or 'element'} {item['label']!r}" for item in payload)
    return "\n".join(lines)
