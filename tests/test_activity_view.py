"""Tests for interface/activity — display rendering (view-level shape tests)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from interface.activity import (
    activity_fragments,
    board_summary_text,
    footer_fragments,
    footer_left_fragments,
    footer_notification_fragment,
    goal_elapsed_text,
    notification_items,
    status_bar_fragments,
    status_left_fragments,
)


def _plain(fragments):
    return "".join(fragment[1] for fragment in fragments)


def _plain_tuple(tup):
    """Extract text from a (style, text) tuple."""
    return tup[1]


def test_activity_fragments_include_busy_elapsed_and_board_summary():
    fragments = activity_fragments(
        busy=True,
        goal_worker_active=False,
        goal_backgrounded=False,
        activity_text="thinking",
        activity_started_at=10,
        board_text="4 tasks (3 done, 1 open)\n→ Report",
        goal_board_text="",
        goal_started_at=None,
        now=13,
    )

    rendered = _plain(fragments)
    assert rendered.startswith(" ● MO Thinking…")
    assert "3s · 4 tasks (3 done, 1 open)" in rendered
    assert "→ Report" not in rendered


def test_board_summary_text_only_uses_task_count_line():
    assert board_summary_text("2 tasks (1 done, 1 open)\n→ Report") == "2 tasks (1 done, 1 open)"
    assert board_summary_text("→ Report") == ""


def test_goal_elapsed_text_formats_seconds_minutes_and_hours():
    assert goal_elapsed_text(None, now=100) == "0s"
    assert goal_elapsed_text(10, now=42) == "32s"
    assert goal_elapsed_text(10, now=132) == "2m2s"
    assert goal_elapsed_text(10, now=7_332) == "2h2m"


def test_footer_notification_fragment_rotates_only_when_multiple_items():
    assert footer_notification_fragment([]) is None
    assert _plain_tuple(footer_notification_fragment([("class:notification-goal", "Goal done")], now=99)) == "Goal done"
    items = [("class:notification-idle", "Ghost replied: Alt+G"), ("class:notification-goal", "Goal running")]
    assert _plain_tuple(footer_notification_fragment(items, now=0)) == "Ghost replied: Alt+G"
    assert _plain_tuple(footer_notification_fragment(items, now=2)) == "Goal running"


def test_notification_items_preserve_footer_order():
    items = notification_items(ghost_unread_count=2, goal_worker_active=True, goal_done_unread=True, pending_count=3)
    assert len(items) == 3
    assert items[0] == ("class:notification-idle", "Ghost replied (2): Alt+G")
    assert items[1] == ("class:notification-goal", "Goal running")
    assert items[2] == ("class:notification-worker", "Queued (3)")

    items2 = notification_items(ghost_unread_count=0, goal_worker_active=False, goal_done_unread=True, pending_count=0)
    assert items2 == [("class:notification-goal", "Goal done")]


def test_footer_helpers_preserve_compact_footer_shape():
    agent = SimpleNamespace(
        session=SimpleNamespace(token_log=[{"input_tokens": 1500, "output_tokens": 25}]),
        provider_name="mock",
        model="mock-model",
        reasoning="high",
        config={},
    )
    from interface.formatting import TokenStatus
    with patch("interface.activity.token_status_from_agent") as mock_ts:
        mock_ts.return_value = TokenStatus(
            input_tokens=1500, output_tokens=25, model="mock-model",
            reasoning="high", provider_name="mock"
        )
        frags = footer_left_fragments(agent,
                                       notice_frag=("class:notification-goal", "Goal running"))
        rendered = _plain(footer_fragments(frags, columns=80))

    assert "↑1.5k ↓25 · mock / mock-model · high" in rendered
    assert "ctx 10.0%/8.0k" not in rendered
    assert "Goal running" in rendered
    assert not rendered.rstrip().endswith("MO")


def test_status_bar_helpers_preserve_notice_and_idle_shapes():
    frags, active = status_left_fragments(notice_text="Saved", notice_until=10, now=3)
    assert active is True
    assert "Saved" in frags[0][1]

    frags2, active2 = status_left_fragments(notice_text="Saved", notice_until=10, now=11)
    assert active2 is False
    assert frags2[0][0] == "class:notification-idle"
    assert "idle" in frags2[0][1].lower() or "◇" in frags2[0][1] or "○" in frags2[0][1] or "◆" in frags2[0][1]

    rendered = _plain(status_bar_fragments([("class:notification-idle", "○ idle")], "Active ○ idle", columns=50))
    assert rendered.startswith("○ idle")
    assert rendered.endswith("Active ○ idle")
