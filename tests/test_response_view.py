from interface.response import normalize_markdown_tables, response_block_fragment_lines, response_line_fragments


def _plain(fragment_lines):
    return ["".join(text for _style, text in fragments) for fragments in fragment_lines]


def test_response_line_fragments_style_headings_code_and_bullets():
    assert response_line_fragments("Summary:") == [("class:response-heading", "Summary:")]
    assert response_line_fragments("      print('x')") == [("class:response-code", "      print('x')")]
    assert response_line_fragments("  - alpha beta gamma") == [
        ("class:mo-response", "  "),
        ("class:response-bullet-marker", "- "),
        ("class:response-bullet-head", "alpha beta"),
        ("class:response-bullet-rest", " gamma"),
    ]
    assert response_line_fragments("") == [("class:mo-response", "")]


def test_response_block_fragment_lines_preserve_marker_and_rest_indent():
    lines = response_block_fragment_lines("# Done\n- alpha beta gamma\n    code")

    assert lines[0] == [("class:mo-marker", "* "), ("class:mo-response", "Done")]
    assert lines[1] == [
        ("class:mo-response", "  "),
        ("class:response-bullet-marker", "- "),
        ("class:response-bullet-head", "alpha beta"),
        ("class:response-bullet-rest", " gamma"),
    ]
    assert lines[2] == [("class:response-code", "          code")]
    assert _plain(lines) == ["* Done", "  - alpha beta gamma", "          code"]


def test_response_formats_markdown_section_labels_without_double_marker():
    lines = response_block_fragment_lines("* Unknowns: *\n* Next: Re-test at narrow width\nTokens: spent 1.2k · saved ~200")
    rendered = "\n".join(_plain(lines))

    assert "* * Unknowns" not in rendered
    assert _plain(lines)[0] == "* Unknowns:"
    assert any(("class:response-heading", "Unknowns:") in line for line in lines)
    assert any(("class:response-heading", "Next:") in line for line in lines)
    assert any(("class:response-heading", "Tokens:") in line for line in lines)


def test_multi_sentence_prose_is_not_split_onto_separate_lines():
    # IFDEV05 P1-004: prose must pass through unchanged so natural word-wrap
    # (not a sentence-boundary split) controls line breaks.
    prose = "The fix works. It routes through the resolver. Done now."
    assert normalize_markdown_tables(prose) == prose


def test_markdown_tables_render_as_bordered_aligned_rows():
    text = "Recent commits:\n\n| Commit | What |\n|---|---|\n| ea68a5c | Refine interface goal finish visuals |\n| 73a7355 | Harden sandbox + ghost route handoff |"

    normalized = normalize_markdown_tables(text)
    lines = response_block_fragment_lines(text)
    rendered = "\n".join(_plain(lines))

    assert "|---|---|" not in normalized
    assert "+" in rendered and "-" in rendered
    assert "Commit" in rendered
    assert "What" in rendered
    assert "ea68a5c" in rendered
    assert "Refine interface goal finish visuals" in rendered


def test_response_block_drops_markdown_separator_stripes():
    lines = response_block_fragment_lines("Report\n____________________\nNext sentence")
    rendered = "\n".join(_plain(lines))

    assert "________________" not in rendered
    assert "Report" in rendered
    assert "Next sentence" in rendered


def test_markdown_tables_wrap_cells_to_adaptive_width_with_borders():
    text = "| Check | Result | Evidence |\n|---|---|---|\n| Route long concrete request into objective contains angry cow and lower-case match | ✅ | objective = I want angry cow 3D running game, angry cow in lower |"

    lines = response_block_fragment_lines(text, columns=72)
    rendered_lines = _plain(lines)
    rendered = "\n".join(rendered_lines)

    assert "|---|---|---|" not in rendered
    assert "|" in rendered and "+" in rendered
    assert "Route long concrete" in rendered
    assert "objective = I want" in rendered
    assert "3D running game" in rendered
    assert any("cow in lower" in line for line in rendered_lines)


def test_markdown_table_continuation_rows_are_not_code_styled():
    text = "\n".join(
        [
            "Risk inventory",
            "",
            "| Risk | Urgency | Note |",
            "|---|---|---|",
            "| nul artifact in git | advisory | Windows NUL device, not a real file, harmless |",
            "| mo_server.py missing | track | Past sessions wrote it; should either be commented or declared dead |",
        ]
    )

    lines = response_block_fragment_lines(text, columns=100)
    rendered_lines = _plain(lines)
    continuation_index = next(index for index, line in enumerate(rendered_lines) if "commented or declared dead" in line)
    continuation = lines[continuation_index]
    continuation_plain = rendered_lines[continuation_index]
    header_plain = next(line for line in rendered_lines if "Risk" in line and "Urgency" in line and "Note" in line)

    assert all(style != "class:response-code" for style, _text in continuation)
    assert continuation == [("class:mo-response", continuation_plain)]
    assert continuation_plain.index("commented") >= header_plain.index("Note")
