"""Tests for interface/activity.py — notification, footer, and status bar rendering."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from interface.activity import (
    notification_items,
    footer_notification_fragment,
    compact_path_for_footer,
    footer_left_fragments,
    footer_fragments,
    status_bar_fragments,
    status_left_fragments,
    activity_fragments,
    context_status_text,
    elapsed_seconds_text,
    goal_elapsed_text,
    goal_progress_text,
)


class TestNotificationItems:
    """Tests for notification_items() which maps system state to styled fragments."""

    def test_ghost_unread(self):
        """Ghost unread count → idle notification with reveal hint."""
        items = notification_items(ghost_unread_count=1, goal_worker_active=False,
                                    goal_done_unread=False, pending_count=0)
        assert ("class:notification-idle", "Ghost replied: Alt+G") in items

    def test_ghost_unread_multiple(self):
        """Ghost unread > 1 includes count."""
        items = notification_items(ghost_unread_count=3, goal_worker_active=False,
                                    goal_done_unread=False, pending_count=0)
        assert ("class:notification-idle", "Ghost replied (3): Alt+G") in items

    def test_prt_done_unread(self):
        """PRT completion has its own notification, not Ghost unread."""
        items = notification_items(ghost_unread_count=0, goal_worker_active=False,
                                    goal_done_unread=False, pending_count=0, prt_done_unread=True)
        assert items == [("class:notification-prt", "PRT ready: Alt+G")]

    def test_goal_worker_active(self):
        """Goal worker active → notification-goal style."""
        items = notification_items(ghost_unread_count=0, goal_worker_active=True,
                                    goal_done_unread=False, pending_count=0)
        assert ("class:notification-goal", "Goal running") in items

    def test_goal_worker_active_with_progress(self):
        """Goal worker active can include conservative completion percentage."""
        items = notification_items(ghost_unread_count=0, goal_worker_active=True,
                                    goal_done_unread=False, pending_count=0, goal_progress="50%")
        assert ("class:notification-goal", "Goal running 50%") in items

    def test_goal_done_unread(self):
        """Goal done without active worker → Goal done."""
        items = notification_items(ghost_unread_count=0, goal_worker_active=False,
                                    goal_done_unread=True, pending_count=0)
        assert ("class:notification-goal", "Goal done") in items

    def test_goal_done_trumped_by_active(self):
        """Active goal takes precedence over done."""
        items = notification_items(ghost_unread_count=0, goal_worker_active=True,
                                    goal_done_unread=True, pending_count=0)
        assert ("class:notification-goal", "Goal running") in items
        assert ("class:notification-goal", "Goal done") not in items

    def test_pending_count(self):
        """Queued items → notification-worker style."""
        items = notification_items(ghost_unread_count=0, goal_worker_active=False,
                                    goal_done_unread=False, pending_count=2)
        assert ("class:notification-worker", "Queued (2)") in items

    def test_empty(self):
        """No notifications → empty list."""
        items = notification_items(ghost_unread_count=0, goal_worker_active=False,
                                    goal_done_unread=False, pending_count=0)
        assert items == []

    def test_all_active_together(self):
        """All notifications active simultaneously."""
        items = notification_items(ghost_unread_count=1, goal_worker_active=True,
                                    goal_done_unread=False, pending_count=2)
        assert len(items) == 3
        styles = {s for s, _ in items}
        assert "class:notification-idle" in styles
        assert "class:notification-goal" in styles
        assert "class:notification-worker" in styles


class TestFooterNotificationFragment:
    """Tests for footer_notification_fragment() cycling."""

    def test_empty_none(self):
        """Empty items → None."""
        assert footer_notification_fragment([]) is None

    def test_single_item(self):
        """Single item returned directly."""
        items = [("class:notification-idle", "Ghost replied: Alt+G")]
        assert footer_notification_fragment(items) == ("class:notification-idle", "Ghost replied: Alt+G")

    def test_cycles_over_time(self):
        """Multiple items cycle every 2 seconds."""
        items = [
            ("class:notification-idle", "Ghost replied: Alt+G"),
            ("class:notification-goal", "Goal running"),
        ]
        t0 = 0.0
        assert footer_notification_fragment(items, now=t0) == items[0]
        assert footer_notification_fragment(items, now=0.5) == items[0]
        assert footer_notification_fragment(items, now=2.0) == items[1]
        assert footer_notification_fragment(items, now=3.5) == items[1]
        assert footer_notification_fragment(items, now=4.0) == items[0]


class TestFooterLeftFragments:
    """Tests for footer_left_fragments() — token status + notice."""

    def test_base_only(self):
        """No context or notice → just the status line."""
        agent = MagicMock()
        # Need to mock token_status_from_agent output
        from interface.formatting import TokenStatus
        agent.input_tokens = 1234
        agent.output_tokens = 567
        agent.model = "gpt-4"
        agent.reasoning = ""
        agent.provider_name = "test"

        with patch("interface.activity.token_status_from_agent") as mock_ts:
            mock_ts.return_value = TokenStatus(
                input_tokens=1234, output_tokens=567, model="gpt-4",
                reasoning="", provider_name="test"
            )
            frags = footer_left_fragments(agent)
        assert len(frags) == 1
        style, text = frags[0]
        assert style == "class:footer"
        assert "test / gpt-4" in text

    def test_base_includes_compact_project_path_first(self):
        agent = MagicMock()
        from interface.formatting import TokenStatus
        agent.project_cwd = r"E:\voice"
        with patch("interface.activity.token_status_from_agent") as mock_ts:
            mock_ts.return_value = TokenStatus(
                input_tokens=25800, output_tokens=1500, model="deepseek-v4-pro",
                reasoning="high", provider_name="opencode"
            )
            frags = footer_left_fragments(agent)
        assert frags[0][1].startswith(r"E:\voice · ↑25.8k ↓1.5k · opencode / deepseek-v4-pro · high")

    def test_compact_path_for_footer_keeps_first_and_tail(self):
        rendered = compact_path_for_footer(r"E:\very\long\workspace\with\many\folders\voice", max_chars=24)
        assert rendered.startswith(r"E:\…")
        assert rendered.endswith(r"\voice")
        assert len(rendered) <= 24

    def test_footer_left_fragments_no_context_text_parameter(self):
        """footer_left_fragments no longer exposes context_text (removed as vestigial)."""
        agent = MagicMock()
        from interface.formatting import TokenStatus
        with patch("interface.activity.token_status_from_agent") as mock_ts:
            mock_ts.return_value = TokenStatus(
                input_tokens=0, output_tokens=0, model="m", reasoning="", provider_name="p"
            )
            frags = footer_left_fragments(agent)
        texts = [t for _, t in frags]
        assert " • " not in texts

    def test_with_notice_frag(self):
        """Notice fragment is appended with separator."""
        agent = MagicMock()
        from interface.formatting import TokenStatus
        with patch("interface.activity.token_status_from_agent") as mock_ts:
            mock_ts.return_value = TokenStatus(
                input_tokens=0, output_tokens=0, model="m", reasoning="", provider_name="p"
            )
            frags = footer_left_fragments(agent, notice_frag=("class:notification-prt", "PRT ready"))
        texts = [t for _, t in frags]
        assert "p / m" in texts[0]
        assert "PRT ready" in texts


class TestFooterFragments:
    """Tests for footer_fragments() — truncation and padding."""

    def test_basic_layout(self):
        """Basic footer layout with no decorative right logo."""
        frags = [("class:footer", "hello world")]
        result = footer_fragments(frags, columns=30)
        assert result[-1] == ("class:footer", " " * 19)

    def test_truncation_long_text(self):
        """Long text is truncated with ellipsis."""
        frags = [("class:footer", "x" * 100)]
        result = footer_fragments(frags, columns=20)
        text = "".join(t for _, t in result)
        assert "…" in text
        assert len(text.rstrip()) <= 20

    def test_right_status_when_supplied(self):
        """Right side shows worker status when supplied."""
        frags = [("class:footer", "short")]
        result = footer_fragments(frags, columns=24, right="Active ◔ MO")
        assert result[-1] == ("class:palette-hint", "Active ◔ MO")

    def test_minimum_columns(self):
        """Minimum column width is 1."""
        frags = [("class:footer", "text")]
        result = footer_fragments(frags, columns=3)
        assert len(result) >= 2  # at least left + logo


class TestStatusBarFragments:
    """Tests for status_bar_fragments() — padding and alignment."""

    def test_basic_alignment(self):
        """Left frags and right text are padded between."""
        left_frags = [("class:notification-idle", "◇ idle")]
        result = status_bar_fragments(left_frags, "Active MO", columns=30)
        texts = [t for _, t in result]
        assert "◇ idle" in texts
        assert "Active MO" in texts

    def test_padding_fills_gap(self):
        """Padding fills remaining space between left and right."""
        left_frags = [("class:notification-idle", "◆ idle…")]
        result = status_bar_fragments(left_frags, "PRT", columns=20)
        total = sum(len(t) for _, t in result)
        assert total == 20  # exactly fills columns


class TestStatusLeftFragments:
    """Tests for status_left_fragments() — idle line dynamic colors."""

    def test_idle_default_style(self):
        """Normal idle state → notification-idle style."""
        frags, active = status_left_fragments(notice_text="", notice_until=0.0, now=1000.0)
        assert not active
        assert frags[0][0] == "class:notification-idle"
        assert "idle" in frags[0][1].lower() or "◇" in frags[0][1] or "◆" in frags[0][1]

    def test_idle_custom_style_prt(self):
        """Custom idle_style for PRT purple."""
        frags, active = status_left_fragments(
            notice_text="", notice_until=0.0, idle_style="class:notification-prt", now=1000.0
        )
        assert frags[0][0] == "class:notification-prt"

    def test_idle_custom_style_goal(self):
        """Custom idle_style for Goal gold."""
        frags, active = status_left_fragments(
            notice_text="", notice_until=0.0, idle_style="class:notification-goal", now=1000.0
        )
        assert frags[0][0] == "class:notification-goal"

    def test_notice_normal_style(self):
        """Active notice without error keywords → class:activity style."""
        frags, active = status_left_fragments(
            notice_text="PRT review queued", notice_until=1005.0, now=1000.0
        )
        assert active
        assert frags[0][0] == "class:activity"
        assert "PRT review queued" in frags[0][1]

    def test_notice_critical_style(self):
        """Notice with 'error' → notification-critical red."""
        frags, active = status_left_fragments(
            notice_text="Error: provider failed", notice_until=1005.0, now=1000.0
        )
        assert active
        assert frags[0][0] == "class:notification-critical"

    def test_notice_aborted_style(self):
        """Notice with 'aborted' → notification-critical red."""
        frags, active = status_left_fragments(
            notice_text="Aborted", notice_until=1005.0, now=1000.0
        )
        assert active
        assert frags[0][0] == "class:notification-critical"

    def test_expired_notice_falls_back_to_idle(self):
        """Expired notice → idle state fallback."""
        frags, active = status_left_fragments(
            notice_text="Old notice", notice_until=5.0, now=1005.0
        )
        assert not active
        assert frags[0][0] == "class:notification-idle"

    def test_notice_future_active(self):
        """Future notice is active."""
        frags, active = status_left_fragments(
            notice_text="PRT running", notice_until=1010.0, now=1000.0
        )
        assert active


class TestActivityFragments:
    """Tests for activity_fragments() — busy/goal working header."""

    def test_idle_returns_empty(self):
        """Not busy and no goal → empty fragments."""
        result = activity_fragments(
            busy=False, goal_worker_active=False, goal_backgrounded=False,
            activity_text="", activity_started_at=None, board_text="",
            goal_board_text="", goal_started_at=None, now=1000.0
        )
        assert result == [("", "")]

    def test_busy_returns_mo_label(self):
        """Busy → spinner + activity text."""
        result = activity_fragments(
            busy=True, goal_worker_active=False, goal_backgrounded=False,
            activity_text="code review", activity_started_at=1000.0,
            board_text="", goal_board_text="", goal_started_at=None, now=1002.0
        )
        assert len(result) == 3
        assert result[1][1] == "MO"
        assert result[2][0] == "class:activity"
        assert "MO" in result[1][1]

    def test_goal_foreground_shows_goal(self):
        """Goal worker active and not backgrounded → Goal Working label."""
        result = activity_fragments(
            busy=False, goal_worker_active=True, goal_backgrounded=False,
            activity_text="", activity_started_at=None, board_text="",
            goal_board_text="", goal_started_at=1000.0, now=1005.0
        )
        assert len(result) == 3
        assert result[1][1] == "MO"
        assert "Goal Working" in result[2][1]

    def test_goal_backgrounded_shows_empty(self):
        """Backgrounded goal → empty fragments (status bar handles it)."""
        result = activity_fragments(
            busy=False, goal_worker_active=True, goal_backgrounded=True,
            activity_text="", activity_started_at=None, board_text="",
            goal_board_text="", goal_started_at=1000.0, now=1005.0
        )
        assert result == [("", "")]


class TestElapsedHelpers:
    """Tests for elapsed_seconds_text, goal_elapsed_text, and goal_progress_text."""

    def test_elapsed_seconds_text(self):
        """Elapsed seconds returns diff."""
        assert elapsed_seconds_text(1000.0, now=1005.0) == "5s"

    def test_elapsed_seconds_none(self):
        """None started_at → empty string."""
        assert elapsed_seconds_text(None) == ""

    def test_goal_elapsed_text_seconds(self):
        """Under 60s → Xs."""
        assert goal_elapsed_text(1000.0, now=1010.0) == "10s"

    def test_goal_elapsed_text_minutes(self):
        """60s+ → XmYs."""
        assert goal_elapsed_text(1000.0, now=1120.0) == "2m0s"

    def test_goal_elapsed_text_hours(self):
        """3600s+ → XhYm."""
        assert goal_elapsed_text(1000.0, now=4600.0) == "1h0m"

    def test_goal_elapsed_text_none(self):
        """None started_at → 0s."""
        assert goal_elapsed_text(None) == "0s"

    def test_goal_progress_text_from_plan(self):
        """Goal progress uses completed steps over total steps."""
        plan = MagicMock()
        plan.steps = [object(), object(), object(), object()]
        plan.completed_count.return_value = 3
        assert goal_progress_text(MagicMock(_goal_plan=plan)) == "75%"


class TestContextStatusText:
    """Tests for context_status_text()."""

    def test_context_status_is_not_user_visible(self):
        """Internal context/handoff budget metrics are not footer text."""
        agent = MagicMock()
        agent.context_budget_tokens = 32000
        assert context_status_text(agent) == ""
