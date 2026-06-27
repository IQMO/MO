"""Duck-typed runtime adapters for the isolated UX surface.

This module intentionally avoids importing ``core`` or ``interface`` at module
load.  It converts already-created runtime objects into immutable display
snapshots without taking ownership of runtime state.
"""
from __future__ import annotations

import os
from typing import Any

from UX.state.models import BoardRow, LaneSnapshot, SessionSnapshot, TranscriptItem, normalize_status


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _redact_display_text(value: str) -> str:
    try:
        from core.tooling.sandbox import redact_sensitive_text
    except Exception:
        return value
    try:
        return redact_sensitive_text(value)
    except Exception:
        return value


def rows_from_gateway_board(board: Any) -> tuple[BoardRow, ...]:
    if board is None:
        return ()
    try:
        summary = board.summary()
    except Exception:
        summary = {}
    tasks = []
    if isinstance(summary, dict):
        tasks = list(summary.get("tasks", []) or [])
    if not tasks:
        tasks = list(getattr(board, "tasks", []) or [])

    rows: list[BoardRow] = []
    for index, task in enumerate(tasks, start=1):
        if isinstance(task, dict):
            rows.append(BoardRow.from_mapping(task))
            continue
        rows.append(
            BoardRow(
                id=_safe_str(getattr(task, "id", "")) or str(index),
                title=_safe_str(getattr(task, "title", "")),
                status=normalize_status(getattr(task, "status", "")),
                blocker=_safe_str(getattr(task, "blocker", "")),
                kind=_safe_str(getattr(task, "kind", "")),
            )
        )
    return tuple(rows)


def lanes_from_runtime(agent: Any, gateway: Any) -> tuple[LaneSnapshot, ...]:
    provider = _safe_str(getattr(agent, "provider_name", ""))
    model = _safe_str(getattr(agent, "model", ""))
    reported = getattr(gateway, "lanes", None) or getattr(agent, "lanes", None) or getattr(agent, "runtime_lanes", None)
    if reported and not isinstance(reported, (str, bytes)):
        lanes: list[LaneSnapshot] = []
        for item in list(reported)[:6]:
            if isinstance(item, dict):
                lanes.append(
                    LaneSnapshot(
                        _safe_str(item.get("name")) or _safe_str(item.get("lane")) or "lane",
                        _safe_str(item.get("status")) or "idle",
                        _safe_str(item.get("detail")),
                        _safe_str(item.get("model")),
                    )
                )
                continue
            lanes.append(
                LaneSnapshot(
                    _safe_str(getattr(item, "name", "")) or _safe_str(getattr(item, "lane", "")) or "lane",
                    _safe_str(getattr(item, "status", "")) or "idle",
                    _safe_str(getattr(item, "detail", "")),
                    _safe_str(getattr(item, "model", "")),
                )
            )
        if lanes:
            return tuple(lanes)

    board = getattr(gateway, "last_task_board", None)
    open_count = 0
    if board is not None:
        try:
            open_count = int(board.open_count())
        except Exception:
            open_count = 0
    active_lane = _safe_str(getattr(agent, "active_lane", "")) or _safe_str(getattr(agent, "_active_lane", ""))
    if active_lane:
        return (LaneSnapshot(active_lane, "running" if open_count else "ready", f"{open_count} open runtime task(s)", model or provider),)
    if open_count:
        return (LaneSnapshot("taskboard", "running", f"{open_count} open runtime task(s)", model or provider),)
    return (LaneSnapshot("runtime", "ready", "no active lanes reported", model or provider),)


def snapshot_from_runtime(agent: Any, gateway: Any) -> SessionSnapshot:
    transcript: list[TranscriptItem] = []
    for item in list(getattr(agent, "messages", []) or [])[-8:]:
        if not isinstance(item, dict):
            continue
        role = _safe_str(item.get("role")) or "system"
        if role not in {"user", "assistant"}:
            continue
        content = _redact_display_text(_safe_str(item.get("content")))
        if content:
            transcript.append(TranscriptItem(role, content))

    project = _safe_str(getattr(agent, "project_cwd", "")) or os.environ.get("MO_PROJECT_CWD", "") or os.getcwd()
    runtime_home = _safe_str(getattr(agent, "runtime_home", ""))
    runtime = "private runtime state" if runtime_home else ""
    provider = _safe_str(getattr(agent, "provider_name", ""))
    model = _safe_str(getattr(agent, "model", ""))
    return SessionSnapshot(
        project=project,
        runtime=runtime,
        surface="UX runtime bridge",
        provider=provider,
        model=model,
        busy=False,
        lanes=lanes_from_runtime(agent, gateway),
        board=rows_from_gateway_board(getattr(gateway, "last_task_board", None)),
        transcript=tuple(transcript),
        composer_hint="runtime adapter preview; not promoted",
    )
