from core.text_utils import DEFAULT_CONTEXT_STOPWORDS, cap_by_tokens, chars_to_tokens, token_aware_truncation_enabled


def test_cap_by_tokens_uses_estimated_token_budget():
    text = "alpha " * 200

    capped = cap_by_tokens(text, 20, "[cut]")

    assert "[cut]" in capped
    assert chars_to_tokens(capped) <= 25


def test_token_aware_truncation_flag(monkeypatch):
    monkeypatch.delenv("MO_TOKEN_AWARE_TRUNCATION", raising=False)
    assert token_aware_truncation_enabled() is False

    monkeypatch.setenv("MO_TOKEN_AWARE_TRUNCATION", "1")
    assert token_aware_truncation_enabled() is True


def test_default_context_stopwords_cover_graph_query_noise():
    assert {"the", "please", "review", "analyze", "work"} <= DEFAULT_CONTEXT_STOPWORDS
    assert "taskboard" not in DEFAULT_CONTEXT_STOPWORDS
