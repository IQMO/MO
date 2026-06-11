from prompt_toolkit.utils import get_cwidth

from interface.ghost_panel import body_row_count, content_rows, fit_cells, panel_dimensions, panel_fragments, route_line_style, wrap_cells, wrap_long_token


def test_fit_cells_pads_to_exact_display_width_with_wide_glyphs():
    text = fit_cells("ok ✅", 8)

    assert get_cwidth(text) == 8
    assert text.startswith("ok ✅")


def test_wrap_cells_preserves_words_and_wraps_long_tokens():
    lines = wrap_cells("one two superlongtoken", 8)

    assert lines[0] == "one two"
    assert "superlon" in lines
    assert "gtoken" in lines


def test_wrap_long_token_handles_wide_glyphs():
    assert wrap_long_token("✅✅x", 2) == ["✅", "✅", "x"]


def test_panel_dimensions_match_terminal_width_caps():
    assert panel_dimensions(10) == (12, 8)
    assert panel_dimensions(40) == (38, 34)
    assert panel_dimensions(120) == (100, 96)


def test_route_line_style_marks_route_receipts_and_blockers():
    assert route_line_style("class:ghost-response", "MO routed") == "class:ghost-route"
    assert route_line_style("class:ghost-response", "Worker routed") == "class:ghost-route"
    assert route_line_style("class:ghost-response", "! unavailable: limit") == "class:ghost-route-blocked"
    assert route_line_style("class:ghost-response", "↯") == "class:ghost-route"
    assert route_line_style("class:ghost-response", "→") == "class:ghost-route"
    assert route_line_style("class:ghost-response", "✓") == "class:ghost-route"
    assert route_line_style("class:ghost-response", "regular answer") == "class:ghost-response"


def test_content_rows_sanitizes_markdown_and_preserves_user_gap():
    rows = content_rows(
        [
            ("class:ghost-user", "question"),
            ("class:ghost-response", "# Answer\nMO routed"),
        ],
        20,
        now=0,
    )

    rendered = [row[0] for row in rows]
    assert rendered[0][0] == "class:ghost-user"
    assert rendered[1][0] == "class:ghost-gap"
    assert any(style == "class:ghost-route" and "MO routed" in text for row in rendered for style, text in [row])


def test_panel_fragments_clamps_scroll_and_reports_visible_line_range():
    lines = [("class:ghost-response", "\n".join(f"line {index}" for index in range(10)))]
    fragments, scroll = panel_fragments(
        panel_open=True,
        panel_lines=lines,
        total_width=70,
        inner=66,
        scroll_from_bottom=99,
        expanded=True,
        now=0,
    )
    rendered = "".join(text for _style, text in fragments)

    assert scroll == 1
    assert "1-9/10" in rendered
    assert "line 0" in rendered
    assert "line 9" not in rendered
    assert "─" * 66 in rendered
    assert rendered.rstrip().endswith("scroll C↑↓")


def test_body_row_count_expands_ghost_panel_vertically():
    assert body_row_count(expanded=False) == 5
    assert body_row_count(expanded=True) == 9


def test_panel_fragments_use_lightweight_compact_hint_row_by_default():
    lines = [
        ("class:ghost-user", "question"),
        ("class:ghost-response", "answer line one\nanswer line two\nanswer line three"),
    ]
    fragments, scroll = panel_fragments(
        panel_open=True,
        panel_lines=lines,
        total_width=70,
        inner=66,
        scroll_from_bottom=0,
        now=0,
    )
    rendered = "".join(text for _style, text in fragments)

    assert scroll == 0
    assert "╭" not in rendered
    assert "╰" not in rendered
    assert "Ghost" in rendered
    assert any(style == "class:ghost-route" and text == "Ghost" for style, text in fragments)
    assert not any(style == "class:ghost-route" and "Alt+G" in text for style, text in fragments)
    assert "Alt+G/Esc hide" in rendered
    assert "Ctrl+O expand" in rendered
    assert rendered.index("─" * 66) < rendered.index("Alt+G/Esc hide")
    assert "answer line three" in rendered
    assert rendered.rstrip().endswith("Ctrl+O expand")
