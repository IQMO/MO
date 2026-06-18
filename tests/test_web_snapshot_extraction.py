"""Tests for web_snapshot's zero-dep main-content extraction (_extract_readable)."""
from __future__ import annotations

from tools import _extract_readable

SAMPLE = """<html><head><title>My &amp; Page</title></head>
<body>
<nav>Home About Contact</nav>
<header>SiteName Login</header>
<main>
<h1>Real Heading</h1>
<p>This is the real content paragraph.</p>
<ul><li>Item one</li><li>Item two</li></ul>
</main>
<footer>Copyright 2026 boilerplate</footer>
<script>var x = 1;</script>
</body></html>"""


def test_title_prepended_and_entities_decoded():
    out = _extract_readable(SAMPLE)
    assert out.startswith("# My & Page")  # title prepended (distinct from h1) + decoded


def test_keeps_main_content_and_structure():
    out = _extract_readable(SAMPLE)
    assert "real content paragraph" in out
    assert "# Real Heading" in out   # heading preserved as markdown
    assert "- Item one" in out and "- Item two" in out


def test_drops_boilerplate_and_scripts():
    out = _extract_readable(SAMPLE)
    assert "Home About Contact" not in out  # nav dropped
    assert "Login" not in out               # header dropped
    assert "boilerplate" not in out         # footer dropped
    assert "var x" not in out               # script dropped


def test_title_not_duplicated_when_body_leads_with_it():
    html = "<title>Same Title</title><body><main><h1>Same Title</h1><p>content x</p></main></body>"
    out = _extract_readable(html)
    assert out.count("Same Title") == 1     # not duplicated when title == h1


def test_no_main_falls_back_to_body_minus_boilerplate():
    out = _extract_readable("<html><body><nav>NAVLINKS</nav><p>Body text here</p></body></html>")
    assert "Body text here" in out
    assert "NAVLINKS" not in out


def test_empty_and_plain_inputs_are_safe():
    assert _extract_readable("") == ""
    assert "hello world" in _extract_readable("<title>T</title><p>hello world</p>")
