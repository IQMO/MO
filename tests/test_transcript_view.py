from interface.transcript_view import continuation_prefix, split_cells, wrap_fragment_line


def _plain_rows(rows):
    return ["".join(text for _style, text in row) for row in rows]


def test_wrap_fragment_line_preserves_words_and_indents_continuations():
    rows = wrap_fragment_line([("class:mo-response", "- one two three four five")], width=12)

    plain = _plain_rows(rows)
    assert plain[0] == "- one two "
    assert plain[1].startswith("  ")
    assert "three" in plain[1]
    assert all("thr\nee" not in row for row in plain)


def test_preformatted_rows_can_split_long_tokens():
    rows = wrap_fragment_line([("class:response-code", "abcdefghij")], width=8)

    assert _plain_rows(rows) == ["abcdefgh", "ij"]


def test_split_cells_keeps_wide_glyph_chunks_within_width():
    chunks = split_cells("✅✅x", width=2)

    assert chunks == ["✅", "✅", "x"]


def test_continuation_prefix_for_bullets_matches_bullet_width():
    assert continuation_prefix([("class:mo-response", "  - item")]) == "    "
