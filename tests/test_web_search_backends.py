"""Web search: keyed backend parsing + keyless fall-through (no network)."""
import tools


def test_brave_parser():
    data = {"web": {"results": [
        {"title": "Python docs", "url": "https://docs.python.org", "description": "official"},
        {"title": "PyPI", "url": "https://pypi.org", "description": "packages"},
    ]}}
    out = tools._format_brave_results(data, 5)
    assert "Python docs" in out and "https://docs.python.org" in out and "official" in out


def test_serper_parser():
    data = {"organic": [
        {"title": "Result A", "link": "https://a.example", "snippet": "snip a"},
    ]}
    out = tools._format_serper_results(data, 5)
    assert "Result A" in out and "https://a.example" in out and "snip a" in out


def test_keyed_returns_none_without_env(monkeypatch):
    monkeypatch.delenv("MO_WEB_SEARCH_API_KEY", raising=False)
    monkeypatch.delenv("MO_WEB_SEARCH_PROVIDER", raising=False)
    assert tools._web_search_keyed("anything", 5) is None


def test_keyed_returns_none_for_unknown_provider(monkeypatch):
    monkeypatch.setenv("MO_WEB_SEARCH_API_KEY", "k")
    monkeypatch.setenv("MO_WEB_SEARCH_PROVIDER", "bogus")
    assert tools._web_search_keyed("anything", 5) is None


def test_empty_query_errors():
    assert tools.execute_web_search({"query": ""}).startswith("Error")
