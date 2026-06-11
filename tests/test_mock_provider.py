from types import SimpleNamespace

from core.provider import provider as provider_module
from core.provider.provider import CodexOAuthProvider, MockProvider, init_provider, load_config, review_providers


def test_mock_provider_streams_text_tokens():
    provider = MockProvider(model="mock-model")

    chunks = list(provider.stream(messages=[{"role": "user", "content": "hi"}], tools=[], temperature=0, max_tokens=20))

    assert chunks[0].choices[0].delta.content.startswith("Mock")
    assert chunks[-1].choices[0].finish_reason == "stop"


def test_init_provider_supports_explicit_mock_config():
    config = {
        "providers": [{"name": "mock-local", "type": "mock", "model": "mock-model"}],
        "model": {"default": "mock-model"},
        "agent": {},
    }

    result = init_provider(config)

    assert result["provider_name"] == "mock-local"
    assert result["api_mode"] == "mock"


def test_init_provider_respects_default_and_fallback_model_selectors():
    config = {
        "providers": [
            {"name": "third", "type": "mock", "model": "third-model"},
            {"name": "fallback", "type": "mock", "model": "fallback-model"},
            {"name": "primary", "type": "mock", "model": "primary-model"},
        ],
        "model": {"default": "primary-model", "fallback": "fallback-model"},
        "agent": {},
    }

    result = init_provider(config)

    assert [provider.model for provider in result["providers"]] == ["primary-model", "fallback-model", "third-model"]
    assert result["provider_name"] == "primary"
    assert result["fallback_model"] == "fallback-model"


def test_review_providers_restricts_prt_to_deepseek_and_codex():
    config = {
        "providers": [
            {"name": "flash", "type": "mock", "model": "deepseek-v4-flash"},
            {"name": "anthropic", "type": "mock", "model": "claude-opus-4-7"},
            {"name": "codex", "type": "mock", "model": "gpt-5.5"},
            {"name": "bigpickle", "type": "mock", "model": "big-pickle"},
            {"name": "pro", "type": "mock", "model": "deepseek-v4-pro"},
        ],
        "model": {"default": "deepseek-v4-pro", "fallback": "openai-codex"},
        "prt": {"default_model": "deepseek-v4-pro", "fallback_model": "codex"},
    }

    chain = review_providers(config)

    assert [(provider.name, provider.model) for provider in chain] == [
        ("pro", "deepseek-v4-pro"),
        ("codex", "gpt-5.5"),
    ]


def test_config_example_includes_switchable_agentrouter_provider():
    config = load_config("config.example.yaml")

    provider = next((p for p in config["providers"] if p.get("name") == "agentrouter"), None)

    assert provider is not None
    assert provider["type"] == "agentrouter"
    assert provider["base_url"] == "https://agentrouter.org/v1"
    assert provider["api_key_env"] == "AGENTROUTER_API_KEY"


def test_codex_provider_keeps_backend_base_url(tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"tokens":{"access_token":"test-token"}}', encoding="utf-8")

    provider = CodexOAuthProvider(model="gpt-5.5", auth_path=str(auth_path))

    assert provider.base_url == "https://chatgpt.com/backend-api/codex"


def test_codex_provider_does_not_leak_function_argument_deltas(tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"tokens":{"access_token":"test-token"}}', encoding="utf-8")
    provider = CodexOAuthProvider(model="gpt-5.5", auth_path=str(auth_path))

    events = [
        SimpleNamespace(type="response.function_call_arguments.delta", delta='{"path":"examples/retro_menu.py"}'),
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(type="function_call", name="edit_file", arguments='{"path":"examples/retro_menu.py"}', call_id="call-1"),
        ),
        SimpleNamespace(type="response.completed", response=SimpleNamespace(usage=None)),
    ]
    provider.stream = lambda **_kwargs: iter(events)

    response = provider.complete(messages=[], tools=[{"type": "function", "function": {"name": "edit_file"}}], temperature=0, max_tokens=100)

    assert response.content == ""
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].function.name == "edit_file"


def test_codex_provider_raw_sse_handles_completed_response_with_null_output(monkeypatch, tmp_path):
    auth_path = tmp_path / "auth.json"
    auth_path.write_text('{"tokens":{"access_token":"test-token"}}', encoding="utf-8")
    provider = CodexOAuthProvider(model="gpt-5.5", auth_path=str(auth_path))
    captured = {}

    class FakeStream:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_lines(self):
            return iter([
                "event: response.output_text.delta",
                'data: {"type":"response.output_text.delta","delta":"ok"}',
                "",
                "event: response.completed",
                'data: {"type":"response.completed","response":{"usage":{"total_tokens":1},"output":null}}',
                "",
            ])

    def fake_stream(method, url, **kwargs):
        captured.update({"method": method, "url": url, **kwargs})
        return FakeStream()

    monkeypatch.setattr(provider_module.httpx, "stream", fake_stream)

    response = provider.complete(messages=[{"role": "user", "content": "hi"}], tools=[], temperature=0, max_tokens=25)

    assert response.content == "ok"
    assert response.usage.total_tokens == 1
    assert captured["json"]["stream"] is True
    assert "max_output_tokens" not in captured["json"]
    assert captured["headers"]["Authorization"] == "Bearer test-token"


def test_init_provider_can_include_agentrouter_when_key_is_set(monkeypatch):
    monkeypatch.setenv("AGENTROUTER_API_KEY", "test-agentrouter-key")
    monkeypatch.setattr(provider_module, "_agentrouter_model_ids", lambda _cfg, _key: ["claude-opus-4-6"])
    config = {
        "providers": [
            {"name": "mock-local", "type": "mock", "model": "mock-model"},
            {
                "name": "agentrouter",
                "type": "agentrouter",
                "base_url": "https://agentrouter.org/v1",
                "api_key_env": "AGENTROUTER_API_KEY",
            },
        ],
        "model": {"default": "mock-model"},
        "agent": {},
    }

    result = init_provider(config)

    providers = result["providers"]
    assert [(p.name, p.api_mode, p.base_url, p.model) for p in providers] == [
        ("mock-local", "mock", "", "mock-model"),
        ("agentrouter", "chat_completions", "https://agentrouter.org/v1", "claude-opus-4-6"),
    ]
