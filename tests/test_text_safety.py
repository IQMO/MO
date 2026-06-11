import sys

from core.text_safety import configure_utf8_stdio, sanitize_unicode_text


class FakeStream:
    def __init__(self):
        self.calls = []

    def reconfigure(self, **kwargs):
        self.calls.append(kwargs)


def test_sanitize_unicode_text_strips_unsafe_controls_but_preserves_whitespace():
    text = sanitize_unicode_text("\ufeffok\x00\x1b\ttwo\nthree\r\ud800")

    assert "\ufeff" not in text
    assert "\x00" not in text
    assert "\x1b" not in text
    assert "\t" in text
    assert "\n" in text
    assert "\r" in text
    assert "�" in text


def test_configure_utf8_stdio_reconfigures_available_streams(monkeypatch):
    out = FakeStream()
    err = FakeStream()
    inp = FakeStream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    monkeypatch.setattr(sys, "stdin", inp)

    configure_utf8_stdio()

    assert out.calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert err.calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert inp.calls == [{"encoding": "utf-8", "errors": "replace"}]
