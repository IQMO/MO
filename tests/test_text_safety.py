import sys

from core.text_safety import configure_utf8_stdio, contains_secret_value, sanitize_unicode_text


def test_contains_secret_value_detects_standalone_high_confidence_tokens():
    # Labeled forms (already covered before the hardening)
    assert contains_secret_value("api_key=supersecretvalue123")
    assert contains_secret_value("token: bearer abcdef123456")
    # Standalone tokens — regression: invisible to the response/learning guard before
    assert contains_secret_value("sk-ABCD1234efgh5678ijkl9012mnop3456")
    assert contains_secret_value("ghp_16CharsOfGitHubPersonalAccessTokenABCDEFG")
    assert contains_secret_value("AKIAIOSFODNN7EXAMPLE")
    assert contains_secret_value(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----"
    )
    # Ordinary prose must not trip the detector
    assert not contains_secret_value("the memory module passed all checks")


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
