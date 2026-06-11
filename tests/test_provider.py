"""Tests for core/provider.py — LLM provider adapters."""
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from core.provider.provider import (
    ProviderError,
    SimpleResponse,
    make_tool_call,
    BaseProvider,
    ChatCompletionsProvider,
    MockProvider,
    CodexOAuthProvider,
    is_context_overflow_error,
    fallback_reason,
)


class TestProviderError:
    """Tests for ProviderError exception."""

    def test_provider_error_is_runtime_error(self):
        """Test that ProviderError is a RuntimeError."""
        error = ProviderError("Test error")
        assert isinstance(error, RuntimeError)

    def test_provider_error_message(self):
        """Test that error message is preserved."""
        error = ProviderError("Test error message")
        assert str(error) == "Test error message"


def test_context_overflow_error_classification_is_not_generic_fallback():
    samples = [
        "context_length_exceeded: maximum context length exceeded",
        "HTTP 413 Payload Too Large",
        "too many tokens in messages",
        "prompt too long for model context window",
    ]
    for sample in samples:
        assert is_context_overflow_error(sample)
        assert fallback_reason(sample) is None

    assert not is_context_overflow_error("max_tokens must be less than or equal to 4096")


class TestSimpleResponse:
    """Tests for SimpleResponse class."""

    def test_simple_response_defaults(self):
        """Test default values."""
        response = SimpleResponse()
        
        assert response.content == ""
        assert response.tool_calls == []
        assert response.usage is None
        assert response.finish_reason == ""
        assert response.reasoning_content is None

    def test_simple_response_with_values(self):
        """Test with provided values."""
        response = SimpleResponse(
            content="Test content",
            tool_calls=[{"id": "1"}],
            usage={"tokens": 100},
            finish_reason="stop",
            reasoning_content="Reasoning"
        )
        
        assert response.content == "Test content"
        assert response.tool_calls == [{"id": "1"}]
        assert response.usage == {"tokens": 100}
        assert response.finish_reason == "stop"
        assert response.reasoning_content == "Reasoning"


class TestMakeToolCall:
    """Tests for make_tool_call function."""

    def test_make_tool_call_with_id(self):
        """Test tool call with provided ID."""
        call = make_tool_call(
            call_id="call-123",
            name="test_tool",
            arguments='{"arg": "value"}'
        )
        
        assert call.id == "call-123"
        assert call.type == "function"
        assert call.function.name == "test_tool"
        assert call.function.arguments == '{"arg": "value"}'

    def test_make_tool_call_generates_id(self):
        """Test tool call generates ID if not provided."""
        call = make_tool_call(
            call_id="",
            name="test_tool",
            arguments="{}"
        )
        
        assert call.id.startswith("call_")

    def test_make_tool_call_defaults(self):
        """Test tool call with empty values."""
        call = make_tool_call(call_id="", name="", arguments="")
        
        assert call.function.name == ""
        assert call.function.arguments == "{}"


class TestBaseProvider:
    """Tests for BaseProvider class."""

    def test_base_provider_defaults(self):
        """Test default values."""
        provider = BaseProvider(model="test-model")
        
        assert provider.name == "base"
        assert provider.api_mode == "unknown"
        assert provider.model == "test-model"
        assert provider.base_url == ""

    def test_base_provider_stream_not_implemented(self):
        """Test that stream raises NotImplementedError."""
        provider = BaseProvider(model="test")
        
        with pytest.raises(NotImplementedError):
            provider.stream(messages=[], tools=[], temperature=0.7, max_tokens=100)

    def test_base_provider_complete_not_implemented(self):
        """Test that complete raises NotImplementedError."""
        provider = BaseProvider(model="test")
        
        with pytest.raises(NotImplementedError):
            provider.complete(messages=[], tools=[], temperature=0.7, max_tokens=100)


class TestChatCompletionsProvider:
    """Tests for ChatCompletionsProvider class."""

    @pytest.fixture
    def mock_openai_client(self):
        """Create mock OpenAI client."""
        with patch("core.provider.provider.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            yield mock_client

    def test_init_creates_client(self, mock_openai_client):
        """Test that OpenAI client is created."""
        provider = ChatCompletionsProvider(
            name="test",
            base_url="https://api.test.com",
            api_key="test-key",
            model="test-model"
        )
        
        assert provider.name == "test"
        assert provider.base_url == "https://api.test.com"
        assert provider.model == "test-model"
        assert provider.api_mode == "chat_completions"

    def test_init_with_timeout(self, mock_openai_client):
        """Test initialization with custom timeout."""
        provider = ChatCompletionsProvider(
            name="test",
            base_url="https://api.test.com",
            api_key="test-key",
            model="test-model",
            timeout=120.0
        )
        
        assert provider.timeout == 120.0

    def test_init_with_headers(self, mock_openai_client):
        """Test initialization with custom headers."""
        headers = {"X-Custom": "value"}
        provider = ChatCompletionsProvider(
            name="test",
            base_url="https://api.test.com",
            api_key="test-key",
            model="test-model",
            headers=headers
        )
        
        # Headers should be passed to client
        assert provider is not None

    def test_stream_without_temperature(self, mock_openai_client):
        """Test streaming without temperature."""
        provider = ChatCompletionsProvider(
            name="test",
            base_url="https://api.test.com",
            api_key="test-key",
            model="test-model"
        )
        
        # Mock stream response
        mock_openai_client.chat.completions.create.return_value = iter([])
        
        provider.stream(
            messages=[{"role": "user", "content": "Hello"}],
            tools=[],
            temperature=0,
            max_tokens=100
        )
        
        # Should not include temperature in request
        call_kwargs = mock_openai_client.chat.completions.create.call_args[1]
        assert "temperature" not in call_kwargs

    def test_stream_with_tools(self, mock_openai_client):
        """Test streaming with tools."""
        provider = ChatCompletionsProvider(
            name="test",
            base_url="https://api.test.com",
            api_key="test-key",
            model="test-model"
        )
        
        tools = [{"type": "function", "function": {"name": "test"}}]
        mock_openai_client.chat.completions.create.return_value = iter([])
        
        provider.stream(
            messages=[],
            tools=tools,
            temperature=0.7,
            max_tokens=100
        )
        
        call_kwargs = mock_openai_client.chat.completions.create.call_args[1]
        assert call_kwargs["tools"] == tools
        assert call_kwargs["tool_choice"] == "auto"

    def test_stream_handles_stream_options_error(self, mock_openai_client):
        """Test handling of stream_options error."""
        provider = ChatCompletionsProvider(
            name="test",
            base_url="https://api.test.com",
            api_key="test-key",
            model="test-model"
        )
        
        # First call fails with stream_options error, second succeeds
        mock_openai_client.chat.completions.create.side_effect = [
            Exception("stream_options not supported"),
            iter([])
        ]
        
        provider.stream(
            messages=[],
            tools=[],
            temperature=0.7,
            max_tokens=100
        )
        
        # Should retry without stream_options
        assert mock_openai_client.chat.completions.create.call_count == 2

    def test_complete_without_streaming(self, mock_openai_client):
        """Test completion without streaming."""
        provider = ChatCompletionsProvider(
            name="test",
            base_url="https://api.test.com",
            api_key="test-key",
            model="test-model"
        )
        
        # Mock response
        mock_message = SimpleNamespace()
        mock_message.content = "Response"
        mock_response = SimpleNamespace()
        mock_response.choices = [SimpleNamespace(message=mock_message, finish_reason="stop")]
        mock_response.usage = {"total_tokens": 100}
        mock_openai_client.chat.completions.create.return_value = mock_response
        
        result = provider.complete(
            messages=[{"role": "user", "content": "Hello"}],
            tools=[],
            temperature=0.7,
            max_tokens=100
        )
        
        assert result.content == "Response"

    def test_complete_with_streaming(self, mock_openai_client):
        """Test completion with streaming."""
        provider = ChatCompletionsProvider(
            name="test",
            base_url="https://api.test.com",
            api_key="test-key",
            model="test-model"
        )
        
        # Mock stream chunks
        chunks = [
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="Hello"), finish_reason="")],
                usage=None
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=" world"), finish_reason="stop")],
                usage={"total_tokens": 50}
            )
        ]
        mock_openai_client.chat.completions.create.return_value = iter(chunks)
        
        tokens = []
        result = provider.complete(
            messages=[],
            tools=[],
            temperature=0.7,
            max_tokens=100,
            on_token=lambda t: tokens.append(t)
        )
        
        assert result.content == "Hello world"
        assert tokens == ["Hello", " world"]

    def test_complete_streaming_with_tool_calls(self, mock_openai_client):
        """Test streaming with tool calls."""
        provider = ChatCompletionsProvider(
            name="test",
            base_url="https://api.test.com",
            api_key="test-key",
            model="test-model"
        )
        
        # Mock stream with tool calls
        chunks = [
            SimpleNamespace(
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(
                        tool_calls=[{
                            "index": 0,
                            "id": "call-1",
                            "function": {"name": "test", "arguments": '{"arg":'}
                        }]
                    ),
                    finish_reason=""
                )],
                usage=None
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(
                        tool_calls=[{
                            "index": 0,
                            "function": {"arguments": '"value"}'}
                        }]
                    ),
                    finish_reason="tool_calls"
                )],
                usage={"total_tokens": 100}
            )
        ]
        mock_openai_client.chat.completions.create.return_value = iter(chunks)
        
        result = provider.complete(
            messages=[],
            tools=[],
            temperature=0.7,
            max_tokens=100,
            on_token=lambda t: None
        )
        
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].function.name == "test"
        assert result.tool_calls[0].function.arguments == '{"arg":"value"}'

    def test_complete_streaming_with_reasoning(self, mock_openai_client):
        """Test streaming with reasoning content."""
        provider = ChatCompletionsProvider(
            name="test",
            base_url="https://api.test.com",
            api_key="test-key",
            model="test-model"
        )
        
        chunks = [
            SimpleNamespace(
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(reasoning_content="Thinking..."),
                    finish_reason=""
                )],
                usage=None
            ),
            SimpleNamespace(
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(content="Response"),
                    finish_reason="stop"
                )],
                usage=None
            )
        ]
        mock_openai_client.chat.completions.create.return_value = iter(chunks)
        
        result = provider.complete(
            messages=[],
            tools=[],
            temperature=0.7,
            max_tokens=100,
            on_token=lambda t: None
        )
        
        assert result.content == "Response"
        assert result.reasoning_content == "Thinking..."


class TestMockProvider:
    """Tests for MockProvider class."""

    def test_mock_provider_defaults(self):
        """Test default values."""
        provider = MockProvider()
        
        assert provider.name == "mock"
        assert provider.api_mode == "mock"
        assert provider.model == "mock-model"

    def test_mock_provider_custom(self):
        """Test with custom values."""
        provider = MockProvider(name="custom", model="custom-model")
        
        assert provider.name == "custom"
        assert provider.model == "custom-model"

    def test_mock_provider_stream(self):
        """Test streaming."""
        provider = MockProvider()
        
        chunks = list(provider.stream(
            messages=[{"role": "user", "content": "Hello"}],
            tools=[],
            temperature=0.7,
            max_tokens=100
        ))
        
        assert len(chunks) > 0

    def test_mock_provider_complete_without_streaming(self):
        """Test completion without streaming."""
        provider = MockProvider()
        
        result = provider.complete(
            messages=[{"role": "user", "content": "Hello"}],
            tools=[],
            temperature=0.7,
            max_tokens=100
        )
        
        assert "Mock response" in result.content
        assert result.finish_reason == "stop"

    def test_mock_provider_complete_with_streaming(self):
        """Test completion with streaming."""
        provider = MockProvider()
        
        tokens = []
        result = provider.complete(
            messages=[{"role": "user", "content": "Hello"}],
            tools=[],
            temperature=0.7,
            max_tokens=100,
            on_token=lambda t: tokens.append(t)
        )
        
        assert len(tokens) > 0
        assert "Mock response" in result.content

    def test_mock_provider_review_response(self):
        """Test review-specific response."""
        provider = MockProvider()
        
        result = provider.complete(
            messages=[{"role": "user", "content": "review this code"}],
            tools=[],
            temperature=0.7,
            max_tokens=100
        )
        
        assert "Confirmed findings" in result.content


class TestCodexOAuthProvider:
    """Tests for CodexOAuthProvider class."""

    @pytest.fixture
    def temp_auth_file(self):
        """Create temporary auth file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            auth_path = Path(tmpdir) / "auth.json"
            auth_data = {
                "tokens": {
                    "access_token": "test-access-token"
                }
            }
            auth_path.write_text(json.dumps(auth_data))
            yield auth_path

    @pytest.fixture
    def mock_openai_client(self):
        """Create mock OpenAI client."""
        with patch("core.provider.provider.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_openai.return_value = mock_client
            yield mock_client

    def test_init_reads_auth_file(self, temp_auth_file, mock_openai_client):
        """Test that auth file is read."""
        provider = CodexOAuthProvider(
            model="gpt-5.5",
            auth_path=str(temp_auth_file)
        )
        
        assert provider.access_token == "test-access-token"
        assert provider.model == "gpt-5.5"
        assert provider.api_mode == "codex_responses"

    def test_init_missing_auth_file(self, mock_openai_client):
        """Test error when auth file is missing."""
        with pytest.raises(ProviderError, match="auth file not found"):
            CodexOAuthProvider(
                model="gpt-5.5",
                auth_path="/nonexistent/auth.json"
            )

    def test_init_missing_access_token(self, mock_openai_client):
        """Test error when access token is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            auth_path = Path(tmpdir) / "auth.json"
            auth_path.write_text(json.dumps({"tokens": {}}))
            
            with pytest.raises(ProviderError, match="access token missing"):
                CodexOAuthProvider(
                    model="gpt-5.5",
                    auth_path=str(auth_path)
                )

    def test_init_with_timeout(self, temp_auth_file, mock_openai_client):
        """Test initialization with custom timeout."""
        provider = CodexOAuthProvider(
            model="gpt-5.5",
            auth_path=str(temp_auth_file),
            timeout=120.0
        )
        
        assert provider.timeout_seconds == 120.0

    def test_codex_headers_include_user_agent(self, temp_auth_file, mock_openai_client):
        """Test that headers include User-Agent."""
        provider = CodexOAuthProvider(
            model="gpt-5.5",
            auth_path=str(temp_auth_file)
        )
        
        assert "User-Agent" in provider.default_headers
        assert "codex_cli_rs" in provider.default_headers["User-Agent"]

    def test_codex_input_conversion_does_not_emit_raw_tool_marker(self):
        """Tool-call history must not train Codex to print raw tool-call text."""
        messages = [
            {"role": "system", "content": "system"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "read_file", "arguments": "{\"path\":\"README.md\"}"}}]},
            {"role": "tool", "content": "ok"},
        ]

        _instructions, input_items = CodexOAuthProvider._to_instructions_and_input(messages)
        rendered = json.dumps(input_items)

        assert "tool calls requested" not in rendered
        assert "read_file" not in rendered
        assert "README.md" not in rendered

    def test_codex_headers_extract_account_id(self, temp_auth_file, mock_openai_client):
        """Test that ChatGPT-Account-ID is extracted from JWT."""
        # Create auth file with JWT containing account ID
        import base64
        payload = {"https://api.openai.com/auth": {"chatgpt_account_id": "account-123"}}
        payload_b64 = base64.b64encode(json.dumps(payload).encode()).decode()
        jwt_token = f"header.{payload_b64}.signature"
        
        auth_data = {"tokens": {"access_token": jwt_token}}
        temp_auth_file.write_text(json.dumps(auth_data))
        
        provider = CodexOAuthProvider(
            model="gpt-5.5",
            auth_path=str(temp_auth_file)
        )
        
        assert "ChatGPT-Account-ID" in provider.default_headers
        assert provider.default_headers["ChatGPT-Account-ID"] == "account-123"
