"""Unit tests for ResponseAPILM.

Tests the custom BaseLM subclass that uses OpenAI's Response API with the
typed_lm forward contract.
"""

from unittest.mock import MagicMock, patch

import dspy
from dspy.core.types import LMToolSpec

from fleet_rlm.runtime.lm import ResponseAPILM


class TestResponseAPILM:
    def test_forward_contract_is_typed_lm(self):
        """ResponseAPILM should declare forward_contract='typed_lm'."""
        assert ResponseAPILM.forward_contract == "typed_lm"

    @patch("fleet_rlm.runtime.lm.openai.OpenAI")
    def test_forward_calls_responses_api(self, mock_openai):
        """forward() should call openai.responses.create()."""
        # Mock the OpenAI client
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_response = MagicMock()
        mock_response.output_text = "test response"
        mock_response.model = "gpt-4o"
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 20
        mock_client.responses.create.return_value = mock_response

        # Create LM and call forward
        lm = ResponseAPILM(model="openai/gpt-4o", api_key="test-key")
        request = dspy.LMRequest(
            model="openai/gpt-4o",
            messages=[dspy.LMMessage(role="user", parts=[{"type": "text", "text": "Hello"}])],
            tools=[],
            config=dspy.LMConfig(),
            metadata={},
        )
        response = lm.forward(request)

        # Verify OpenAI client was called
        mock_client.responses.create.assert_called_once()
        assert response.text == "test response"
        assert response.usage.input_tokens == 10
        assert response.usage.output_tokens == 20

    @patch("fleet_rlm.runtime.lm.openai.OpenAI")
    def test_tool_conversion(self, mock_openai):
        """forward() should convert LMToolSpec to OpenAI tool format."""
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_response = MagicMock()
        mock_response.output_text = "test"
        mock_response.model = "gpt-4o"
        mock_response.usage.input_tokens = 5
        mock_response.usage.output_tokens = 10
        mock_client.responses.create.return_value = mock_response

        lm = ResponseAPILM(model="openai/gpt-4o", api_key="test-key")

        # Create a proper LMToolSpec
        tool = LMToolSpec(name="search", description="Search the web", parameters={"query": {"type": "string"}})

        request = dspy.LMRequest(
            model="openai/gpt-4o",
            messages=[dspy.LMMessage(role="user", parts=[{"type": "text", "text": "Search for X"}])],
            tools=[tool],
            config=dspy.LMConfig(),
            metadata={},
        )
        lm.forward(request)

        # Verify tools were converted and passed to OpenAI
        call_kwargs = mock_client.responses.create.call_args[1]
        assert "tools" in call_kwargs
        tools = call_kwargs["tools"]
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "search"
        assert tools[0]["function"]["description"] == "Search the web"

    @patch("fleet_rlm.runtime.lm.openai.OpenAI")
    def test_history_populated(self, mock_openai):
        """forward() should populate .history for backward compat."""
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_response = MagicMock()
        mock_response.output_text = "response"
        mock_response.model = "gpt-4o"
        mock_response.usage.input_tokens = 15
        mock_response.usage.output_tokens = 25
        mock_client.responses.create.return_value = mock_response

        lm = ResponseAPILM(model="openai/gpt-4o", api_key="test-key")

        # Initially history should be empty
        assert len(lm.history) == 0

        # Call forward
        request = dspy.LMRequest(
            model="openai/gpt-4o",
            messages=[dspy.LMMessage(role="user", parts=[{"type": "text", "text": "Hello"}])],
            tools=[],
            config=dspy.LMConfig(),
            metadata={},
        )
        lm.forward(request)

        # Verify history was populated
        assert len(lm.history) == 1
        assert lm.history[0]["usage"]["input_tokens"] == 15
        assert lm.history[0]["usage"]["output_tokens"] == 25
        assert lm.history[0]["model"] == "gpt-4o"

    @patch("fleet_rlm.runtime.lm.openai.OpenAI")
    def test_config_overrides(self, mock_openai):
        """forward() should apply temperature and max_tokens from config."""
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_response = MagicMock()
        mock_response.output_text = "response"
        mock_response.model = "gpt-4o"
        mock_response.usage.input_tokens = 10
        mock_response.usage.output_tokens = 20
        mock_client.responses.create.return_value = mock_response

        lm = ResponseAPILM(model="openai/gpt-4o", api_key="test-key")

        config = dspy.LMConfig(temperature=0.7, max_tokens=500)
        request = dspy.LMRequest(
            model="openai/gpt-4o",
            messages=[dspy.LMMessage(role="user", content="Hello")],
            tools=[],
            config=config,
            metadata={},
        )
        lm.forward(request)

        # Verify config was passed to OpenAI
        call_kwargs = mock_client.responses.create.call_args[1]
        assert call_kwargs["temperature"] == 0.7
        assert call_kwargs["max_output_tokens"] == 500

    @patch("fleet_rlm.runtime.lm.openai.OpenAI")
    def test_dump_load_state(self, mock_openai):
        """dump_state and load_state should preserve LM configuration."""
        mock_client = MagicMock()
        mock_openai.return_value = mock_client

        lm = ResponseAPILM(
            model="openai/gpt-4o",
            api_key="test-key",
            api_base="https://custom.api.com",
            max_tokens=1000,
            temperature=0.5,
        )

        # Dump state
        state = lm.dump_state()
        assert state["model"] == "openai/gpt-4o"
        assert state["api_base"] == "https://custom.api.com"
        assert state["max_tokens"] == 1000
        assert state["temperature"] == 0.5

        # Load state into new instance
        lm2 = ResponseAPILM(model="openai/gpt-4o-mini", api_key="other-key")
        lm2.load_state(state)
        assert lm2.model == "openai/gpt-4o"
        assert lm2._api_base == "https://custom.api.com"
        assert lm2.kwargs.get("max_tokens") == 1000
        assert lm2.kwargs.get("temperature") == 0.5
