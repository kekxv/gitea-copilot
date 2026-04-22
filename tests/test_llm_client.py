"""Tests for LLMClient module."""
import pytest
import os
from unittest.mock import patch
from app.skills.llm_client import LLMClient, get_llm_client, get_llm_client_from_config, reset_llm_client, close_llm_client


class TestLLMClient:
    """Tests for LLMClient class."""

    def test_init_with_defaults(self):
        """Test initialization with default values (no env vars)."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove LLM_* and OPENAI_* vars
            for key in list(os.environ.keys()):
                if key.startswith('LLM_') or key.startswith('OPENAI_'):
                    del os.environ[key]

            client = LLMClient()
            assert client.base_url == "https://api.openai.com/v1"
            assert client.model == "gpt-4o-mini"
            assert client.api_key == "sk-no-key-required"

    def test_init_with_custom_values(self):
        """Test initialization with custom values."""
        client = LLMClient(
            base_url="http://localhost:11434/v1",
            api_key="test-key",
            model="llama3"
        )
        assert client.base_url == "http://localhost:11434/v1"
        assert client.model == "llama3"
        assert client.api_key == "test-key"

    def test_init_empty_api_key(self):
        """Test that empty API key gets placeholder (no env vars)."""
        with patch.dict(os.environ, {}, clear=True):
            for key in list(os.environ.keys()):
                if key.startswith('LLM_') or key.startswith('OPENAI_'):
                    del os.environ[key]

            client = LLMClient(api_key="")
            assert client.api_key == "sk-no-key-required"

    def test_init_whitespace_api_key(self):
        """Test that whitespace-only API key gets placeholder."""
        client = LLMClient(api_key="   ")
        assert client.api_key == "sk-no-key-required"

    @pytest.mark.asyncio
    async def test_aclose(self, mocker):
        """Test aclose properly closes the AsyncOpenAI client."""
        mock_openai = mocker.Mock()
        mock_openai.close = mocker.AsyncMock()

        client = LLMClient()
        client.client = mock_openai

        await client.aclose()
        mock_openai.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_success(self, mocker):
        """Test successful generate call."""
        mock_response = mocker.Mock()
        mock_response.choices = [mocker.Mock(message=mocker.Mock(content="Test response"))]

        mock_openai = mocker.Mock()
        mock_openai.chat.completions.create = mocker.AsyncMock(return_value=mock_response)

        client = LLMClient()
        client.client = mock_openai

        result = await client.generate("Hello", max_tokens=100)
        assert result == "Test response"

    @pytest.mark.asyncio
    async def test_generate_empty_response(self, mocker):
        """Test generate with empty API response."""
        mock_response = mocker.Mock()
        mock_response.choices = []

        mock_openai = mocker.Mock()
        mock_openai.chat.completions.create = mocker.AsyncMock(return_value=mock_response)

        client = LLMClient()
        client.client = mock_openai

        result = await client.generate("Hello")
        assert "空响应" in result

    @pytest.mark.asyncio
    async def test_generate_with_system_prompt(self, mocker):
        """Test generate with system prompt."""
        mock_response = mocker.Mock()
        mock_response.choices = [mocker.Mock(message=mocker.Mock(content="Response"))]

        mock_openai = mocker.Mock()
        mock_openai.chat.completions.create = mocker.AsyncMock(return_value=mock_response)

        client = LLMClient()
        client.client = mock_openai

        result = await client.generate("Hello", system_prompt="Be helpful")
        assert result == "Response"

        # Check that system prompt was included
        call_args = mock_openai.chat.completions.create.call_args
        messages = call_args.kwargs["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "Be helpful"

    @pytest.mark.asyncio
    async def test_generate_api_error(self, mocker):
        """Test generate with API error."""
        mock_openai = mocker.Mock()
        mock_openai.chat.completions.create = mocker.AsyncMock(side_effect=Exception("API error"))

        client = LLMClient()
        client.client = mock_openai

        result = await client.generate("Hello")
        assert "API 调用出错" in result

    @pytest.mark.asyncio
    async def test_generate_with_tools_no_tool_calls(self, mocker):
        """Test generate_with_tools when AI returns final response."""
        mock_response = mocker.Mock()
        mock_response.choices = [mocker.Mock(message=mocker.Mock(content="Final answer", tool_calls=None))]

        mock_openai = mocker.Mock()
        mock_openai.chat.completions.create = mocker.AsyncMock(return_value=mock_response)

        client = LLMClient()
        client.client = mock_openai

        result, log = await client.generate_with_tools(
            "Hello", "Be helpful", [{"type": "function", "function": {"name": "test"}}]
        )
        assert result == "Final answer"
        assert log == []

    @pytest.mark.asyncio
    async def test_generate_with_tools_empty_choices(self, mocker):
        """Test generate_with_tools with empty choices."""
        mock_response = mocker.Mock()
        mock_response.choices = []

        mock_openai = mocker.Mock()
        mock_openai.chat.completions.create = mocker.AsyncMock(return_value=mock_response)

        client = LLMClient()
        client.client = mock_openai

        result, log = await client.generate_with_tools("Hello", "Help", [])
        assert "空响应" in result


class TestGlobalClientFunctions:
    """Tests for global LLM client management functions."""

    def test_get_llm_client_creates_singleton(self):
        """Test that get_llm_client creates and returns singleton."""
        reset_llm_client()  # Reset first

        client1 = get_llm_client()
        client2 = get_llm_client()

        assert client1 is client2

    def test_reset_llm_client(self):
        """Test reset_llm_client clears singleton."""
        client1 = get_llm_client()
        reset_llm_client()
        client2 = get_llm_client()

        assert client1 is not client2

    def test_get_llm_client_from_config_singleton(self, mocker):
        """Test that get_llm_client_from_config returns singleton."""
        reset_llm_client()

        # Mock database session
        mock_db = mocker.Mock()
        mock_db.query.return_value.first.return_value = None

        client1 = get_llm_client_from_config(mock_db)
        client2 = get_llm_client_from_config(mock_db)

        assert client1 is client2

    def test_get_llm_client_from_config_with_db_config(self, mocker):
        """Test get_llm_client_from_config with database config."""
        reset_llm_client()

        # Mock database config
        mock_config = mocker.Mock()
        mock_config.llm_base_url = "http://custom.api/v1"
        mock_config.llm_api_key = "custom-key"
        mock_config.llm_model = "custom-model"

        mock_db = mocker.Mock()
        mock_db.query.return_value.first.return_value = mock_config

        client = get_llm_client_from_config(mock_db)

        assert client.base_url == "http://custom.api/v1"
        assert client.model == "custom-model"
        assert client.api_key == "custom-key"

    def test_get_llm_client_from_config_empty_db_values(self, mocker):
        """Test get_llm_client_from_config with empty database values uses env."""
        reset_llm_client()

        # Mock database config with empty values
        mock_config = mocker.Mock()
        mock_config.llm_base_url = ""
        mock_config.llm_api_key = ""
        mock_config.llm_model = ""

        mock_db = mocker.Mock()
        mock_db.query.return_value.first.return_value = mock_config

        with patch.dict(os.environ, {}, clear=True):
            for key in list(os.environ.keys()):
                if key.startswith('LLM_') or key.startswith('OPENAI_'):
                    del os.environ[key]

            client = get_llm_client_from_config(mock_db)

            # Should use defaults
            assert client.base_url == "https://api.openai.com/v1"
            assert client.model == "gpt-4o-mini"

    def test_get_llm_client_from_config_no_db(self):
        """Test get_llm_client_from_config without database."""
        reset_llm_client()

        with patch.dict(os.environ, {}, clear=True):
            for key in list(os.environ.keys()):
                if key.startswith('LLM_') or key.startswith('OPENAI_'):
                    del os.environ[key]

            client = get_llm_client_from_config(None)

            assert client.base_url == "https://api.openai.com/v1"
            assert client.model == "gpt-4o-mini"

    def test_get_llm_client_from_config_db_error(self, mocker):
        """Test get_llm_client_from_config handles database error."""
        reset_llm_client()

        mock_db = mocker.Mock()
        mock_db.query.side_effect = Exception("DB error")

        with patch.dict(os.environ, {}, clear=True):
            for key in list(os.environ.keys()):
                if key.startswith('LLM_') or key.startswith('OPENAI_'):
                    del os.environ[key]

            client = get_llm_client_from_config(mock_db)

            # Should fallback to defaults
            assert client.base_url == "https://api.openai.com/v1"

    def test_get_llm_client_from_config_config_change(self, mocker):
        """Test that config change creates new client."""
        reset_llm_client()

        mock_db = mocker.Mock()
        mock_db.query.return_value.first.return_value = None

        client1 = get_llm_client_from_config(mock_db)

        # Change config
        mock_config = mocker.Mock()
        mock_config.llm_base_url = "http://new.api/v1"
        mock_config.llm_api_key = "new-key"
        mock_config.llm_model = "new-model"
        mock_db.query.return_value.first.return_value = mock_config

        client2 = get_llm_client_from_config(mock_db)

        # Should be different due to config change
        assert client1 is not client2
        assert client2.base_url == "http://new.api/v1"

    @pytest.mark.asyncio
    async def test_close_llm_client(self, mocker):
        """Test close_llm_client properly closes client."""
        reset_llm_client()

        client = get_llm_client()
        client.client = mocker.Mock()
        client.client.close = mocker.AsyncMock()

        await close_llm_client()

        client.client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_llm_client_none(self):
        """Test close_llm_client when client is None."""
        reset_llm_client()

        # Should not raise error
        await close_llm_client()

    @pytest.mark.asyncio
    async def test_close_llm_client_error(self, mocker):
        """Test close_llm_client handles errors."""
        reset_llm_client()

        client = get_llm_client()
        client.client = mocker.Mock()
        client.client.close = mocker.AsyncMock(side_effect=Exception("Close error"))

        # Should not raise error
        await close_llm_client()