from interface.transcript import (
    adjusted_scroll_from_bottom,
    logical_lines_from_snapshot,
    transcript_fragments_for_viewport,
    visible_transcript_height,
    visual_rows,
)


def _plain(fragments):
    return "".join(text for _style, text in fragments)


def test_logical_lines_from_snapshot_splits_newlines_and_empty_snapshot():
    snapshot = (("class:a", "one\ntwo"), ("", "\n"), ("class:b", "three"))

    assert logical_lines_from_snapshot(snapshot) == [
        [("class:a", "one")],
        [("class:a", "two")],
        [("class:b", "three")],
    ]
    assert logical_lines_from_snapshot(()) == [[]]


def test_visual_rows_wraps_and_clamps_width():
    rows = visual_rows([[("class:a", "alpha beta gamma delta")]], 12)

    assert len(rows) > 1
    assert _plain(rows[0]).startswith("alpha beta gamma")


def test_transcript_fragments_for_viewport_clamps_scroll_from_bottom():
    rows = [[("class:a", f"line {index}")] for index in range(5)]

    fragments, scroll = transcript_fragments_for_viewport(rows, visible=2, scroll_from_bottom=99)

    assert scroll == 3
    assert _plain(fragments) == "line 0\nline 1"


def test_transcript_fragments_reorder_arabic_runs_for_terminal_display_only():
    rows = [[("class:a", "Arabic: مرحبا عالم")]]

    fragments, scroll = transcript_fragments_for_viewport(rows, visible=1, scroll_from_bottom=0)

    assert scroll == 0
    assert _plain(fragments) == "Arabic: ابحرم ملاع"
    assert rows == [[("class:a", "Arabic: مرحبا عالم")]]


def test_visible_transcript_height_matches_reserved_panels():
    assert visible_transcript_height(
        terminal_rows=24,
        busy=False,
        goal_worker_active=False,
        visible_goal_board_text="",
        board_text="",
        palette_open=False,
        palette_item_count=0,
        ghost_panel_open=False,
    ) == 19
    assert visible_transcript_height(
        terminal_rows=24,
        busy=True,
        goal_worker_active=False,
        visible_goal_board_text="2 tasks\n→ Goal",
        board_text="3 tasks\n→ Main",
        palette_open=True,
        palette_item_count=4,
        ghost_panel_open=True,
    ) == 1
    assert visible_transcript_height(
        terminal_rows=24,
        busy=True,
        goal_worker_active=False,
        visible_goal_board_text="2 tasks\n→ Goal",
        board_text="3 tasks\n→ Main",
        palette_open=True,
        palette_item_count=4,
        ghost_panel_open=True,
        ghost_expanded=True,
    ) == 1


def test_adjusted_scroll_from_bottom_clamps_delta():
    assert adjusted_scroll_from_bottom(line_count=10, visible=4, current_scroll=0, delta_from_bottom=99) == 6
    assert adjusted_scroll_from_bottom(line_count=10, visible=4, current_scroll=3, delta_from_bottom=-99) == 0
