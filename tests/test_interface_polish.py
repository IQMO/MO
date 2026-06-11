"""Interface polish regression tests (operator UX audit 2026-06-10)."""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_status_markers_have_single_definition():
    """The D003 marker map lives only in core/tasking/task_board.py — every
    other surface imports it (was duplicated across 5 files)."""
    pattern = re.compile(r'"completed":\s*"√"')
    offenders = []
    for folder in ("core", "interface", "tools"):
        for path in (REPO / folder).rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if pattern.search(text) and path.name != "task_board.py":
                offenders.append(str(path.relative_to(REPO)))
    assert offenders == [], f"duplicate STATUS_MARKERS definitions: {offenders}"


def test_status_marker_helper():
    from core.tasking.task_board import STATUS_MARKERS, status_marker

    assert status_marker("completed") == STATUS_MARKERS["completed"]
    assert status_marker("ACTIVE") == STATUS_MARKERS["active"]
    assert status_marker("unknown-status") == STATUS_MARKERS["pending"]
    assert status_marker("") == STATUS_MARKERS["pending"]


def test_render_plain_uses_canonical_markers():
    from core.tasking.task_board import STATUS_MARKERS, TaskBoard, TaskItem
    from interface.task_board_view import render_plain

    board = TaskBoard(tasks=[
        TaskItem("1", "Done thing", "completed", kind="inspect"),
        TaskItem("2", "Active thing", "active", kind="edit"),
        TaskItem("3", "Stuck thing", "blocked", blocker="waiting"),
    ])
    text = render_plain(board)

    assert f"{STATUS_MARKERS['completed']} Done thing" in text
    assert f"{STATUS_MARKERS['active']} Active thing" in text
    assert f"{STATUS_MARKERS['blocked']} Stuck thing" in text
    assert "waiting" in text


def test_transcript_bottom_anchors_short_content():
    """Short transcripts hug the bottom panels instead of leaving a void."""
    from interface.transcript import transcript_fragments_for_viewport

    rows = [[("class:mo-response", "hello")], [("class:mo-response", "world")]]
    fragments, scroll = transcript_fragments_for_viewport(rows, visible=6, scroll_from_bottom=0)

    text = "".join(t for _s, t in fragments)
    assert scroll == 0
    assert text.count("\n") == 5  # 4 pad rows above + 1 separator between the 2 content rows
    assert text.endswith("hello\nworld")
    assert text.startswith("\n")


def test_transcript_full_viewport_unchanged():
    from interface.transcript import transcript_fragments_for_viewport

    rows = [[("", f"line{i}")] for i in range(10)]
    fragments, scroll = transcript_fragments_for_viewport(rows, visible=4, scroll_from_bottom=0)

    text = "".join(t for _s, t in fragments)
    assert text == "line6\nline7\nline8\nline9"
    assert scroll == 0


def test_moon_toggle_reuses_single_tick_thread():
    """Every /moon toggle used to leak a 10-FPS invalidation thread."""
    from types import SimpleNamespace
    from core.agent.agent import Agent

    agent = object.__new__(Agent)
    agent.tui = SimpleNamespace(_app=None)

    out_on = agent._cmd_moon("on")
    first_stop = agent._moon_tick_stop
    assert "ON" in out_on and first_stop is not None and not first_stop.is_set()

    # toggling on again must not replace/leak the running thread
    agent._cmd_moon("on")
    assert agent._moon_tick_stop is first_stop

    out_off = agent._cmd_moon("off")
    assert "OFF" in out_off
    assert first_stop.is_set()          # old thread told to stop
    assert agent._moon_tick_stop is None
