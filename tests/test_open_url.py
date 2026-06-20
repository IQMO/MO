"""open_url: opens a URL in the operator's DEFAULT browser (user-facing 'show me')."""
import tools
from tools import execute_open_url


def test_open_url_registered():
    assert "open_url" in tools.TOOL_EXECUTORS
    assert any(d["function"]["name"] == "open_url" for d in tools.TOOL_DEFINITIONS)


def test_open_url_requires_url():
    assert "requires" in execute_open_url({}).lower()


def test_open_url_uses_default_browser_handler(monkeypatch):
    calls = {}
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda u, *a, **k: calls.setdefault("url", u) or True)
    out = execute_open_url({"url": "tradingview.com"})
    # bare host gets https:// and goes to the DEFAULT browser handler
    assert calls["url"] == "https://tradingview.com"
    assert "default browser" in out.lower()


def test_open_url_preserves_scheme(monkeypatch):
    calls = {}
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda u, *a, **k: calls.setdefault("url", u) or True)
    execute_open_url({"url": "http://example.com/x"})
    assert calls["url"] == "http://example.com/x"
