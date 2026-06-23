from __future__ import annotations

import sys
from types import SimpleNamespace

import tools


class FakeStreamResponse:
    def __init__(self, chunks, *, headers=None, status_code=200, encoding="utf-8"):
        self._chunks = list(chunks)
        self.headers = headers or {}
        self.status_code = status_code
        self.encoding = encoding

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False

    def iter_bytes(self):
        yield from self._chunks


def _install_fake_httpx(monkeypatch, response: FakeStreamResponse):
    calls = []

    def fake_stream(*args, **kwargs):
        calls.append((args, kwargs))
        return response

    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(stream=fake_stream))
    return calls


def test_web_snapshot_streams_with_http_status_and_redaction(monkeypatch):
    html = (
        b"<html><head><title>Example</title><script>ignore()</script></head>"
        b"<body>Hello api_key=supersecret <style>.x{}</style><p>World</p></body></html>"
    )
    response = FakeStreamResponse([html], headers={"content-length": str(len(html))}, status_code=203)
    calls = _install_fake_httpx(monkeypatch, response)

    result = tools.execute_web_snapshot({"url": "https://example.test"})

    assert result.startswith("[HTTP 203]\n# Example")
    assert "Hello" in result and "World" in result
    assert "api_key=[redacted]" in result
    assert "supersecret" not in result
    assert calls[0][0][:2] == ("GET", "https://example.test")
    assert calls[0][1]["follow_redirects"] is True


def test_web_snapshot_rejects_large_content_length(monkeypatch):
    response = FakeStreamResponse(
        [b"ignored"],
        headers={"content-length": str(tools.MAX_WEB_FETCH_BYTES + 1)},
        status_code=200,
    )
    _install_fake_httpx(monkeypatch, response)

    result = tools.execute_web_snapshot({"url": "https://example.test/large"})

    assert result == f"Error fetching https://example.test/large: response too large ({tools.MAX_WEB_FETCH_BYTES + 1} bytes)"


def test_web_snapshot_rejects_stream_that_exceeds_cap(monkeypatch):
    monkeypatch.setattr(tools, "MAX_WEB_FETCH_BYTES", 10)
    response = FakeStreamResponse([b"12345", b"678901"], headers={}, status_code=200)
    _install_fake_httpx(monkeypatch, response)

    result = tools.execute_web_snapshot({"url": "https://example.test/stream"})

    assert result == "Error fetching https://example.test/stream: response too large"
