"""Unit tests for BoundedChatLM and build_bounded_chat_lm."""

from unittest.mock import MagicMock, patch

import dspy
import openai
import pytest

from fleet_rlm.runtime.lm import BoundedChatLM, build_bounded_chat_lm


def _fake_completion(text: str = "ok", model: str = "qwen3.7-max") -> MagicMock:
    """A fake OpenAI ChatCompletion object shaped for dspy._process_completion."""
    message = MagicMock()
    message.content = text
    message.reasoning_content = None
    message.tool_calls = None
    choice = MagicMock()
    choice.message = message
    completion = MagicMock()
    completion.choices = [choice]
    completion.model = model
    completion.usage = {"prompt_tokens": 5, "completion_tokens": 3}
    completion.cache_hit = False
    completion._hidden_params = {}
    return completion


class TestBoundedChatLM:
    @patch("fleet_rlm.runtime.lm.openai.OpenAI")
    def test_construct_defaults(self, mock_openai):
        lm = BoundedChatLM(model="qwen3.7-max", api_key="k", max_tokens=512, temperature=0.0, timeout=30.0)
        assert lm.num_retries == 0
        assert lm.cache is False
        assert lm.model == "qwen3.7-max"
        assert lm._max_tokens == 512
        assert lm._disable_thinking is True  # auto-on for qwen
        # openai client built with the real timeout and no retries
        mock_openai.assert_called_once()
        assert mock_openai.call_args.kwargs["timeout"] == 30.0
        assert mock_openai.call_args.kwargs["max_retries"] == 0

    @patch("fleet_rlm.runtime.lm.openai.OpenAI")
    def test_disable_thinking_auto_off_for_non_qwen(self, mock_openai):
        lm = BoundedChatLM(model="gpt-4o", api_key="k", max_tokens=512, temperature=0.0, timeout=30.0)
        assert lm._disable_thinking is False

    @patch("fleet_rlm.runtime.lm.openai.OpenAI")
    def test_forward_sends_thinking_off_and_caps(self, mock_openai):
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create.return_value = _fake_completion()

        lm = BoundedChatLM(model="qwen3.7-max", api_key="k", max_tokens=512, temperature=0.0, timeout=30.0)
        lm.forward(messages=[{"role": "user", "content": "hi"}])

        _, kwargs = mock_client.chat.completions.create.call_args
        assert kwargs["model"] == "qwen3.7-max"
        assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
        assert kwargs["max_tokens"] == 512
        assert kwargs["temperature"] == 0.0
        assert kwargs["extra_body"] == {"enable_thinking": False}

    @patch("fleet_rlm.runtime.lm.openai.OpenAI")
    def test_forward_drops_non_openai_params(self, mock_openai):
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create.return_value = _fake_completion()

        lm = BoundedChatLM(model="qwen3.7-max", api_key="k", max_tokens=4096, temperature=0.0, timeout=90.0)
        # dspy merges LMConfig fields + self.kwargs into forward kwargs
        lm.forward(
            messages=[{"role": "user", "content": "hi"}],
            custom_llm_provider="openai",
            reasoning={"effort": "high"},
            rollout_id="abc",
            response_format={"type": "json_object"},
        )

        _, kwargs = mock_client.chat.completions.create.call_args
        # valid OpenAI params forwarded
        assert kwargs["response_format"] == {"type": "json_object"}
        # non-OpenAI / transport params dropped
        assert "custom_llm_provider" not in kwargs
        assert "reasoning" not in kwargs
        assert "rollout_id" not in kwargs
        assert "api_key" not in kwargs
        assert "api_base" not in kwargs

    @patch("fleet_rlm.runtime.lm.openai.OpenAI")
    def test_forward_timeout_raises_lmerror(self, mock_openai):
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create.side_effect = openai.APITimeoutError(request=MagicMock())

        lm = BoundedChatLM(model="qwen3.7-max", api_key="k", max_tokens=512, temperature=0.0, timeout=5.0)
        with pytest.raises(dspy.LMError):
            lm.forward(messages=[{"role": "user", "content": "hi"}])

    @patch("fleet_rlm.runtime.lm.openai.OpenAI")
    def test_forward_context_window_raises_context_window_exceeded(self, mock_openai):
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create.side_effect = openai.BadRequestError(
            "This model's maximum context length is 8192 tokens.", response=MagicMock(), body=None
        )

        lm = BoundedChatLM(model="qwen3.7-max", api_key="k", max_tokens=512, temperature=0.0, timeout=30.0)
        with pytest.raises(dspy.ContextWindowExceededError):
            lm.forward(messages=[{"role": "user", "content": "hi"}])

    @patch("fleet_rlm.runtime.lm.openai.OpenAI")
    def test_full_call_path_returns_text(self, mock_openai):
        """End-to-end through BaseLM.__call__ → _process_completion."""
        mock_client = MagicMock()
        mock_openai.return_value = mock_client
        mock_client.chat.completions.create.return_value = _fake_completion("hello world")

        lm = BoundedChatLM(model="qwen3.7-max", api_key="k", max_tokens=512, temperature=0.0, timeout=30.0)
        outputs = lm(messages=[{"role": "user", "content": "hi"}])
        # _process_completion returns [text] when every choice has only "text"
        assert outputs == ["hello world"]


class TestBuildBoundedChatLm:
    def test_returns_none_for_none_base(self):
        assert build_bounded_chat_lm(None, max_tokens=512, temperature=0.0, timeout=30.0) is None

    def test_returns_none_when_credentials_missing(self):
        from types import SimpleNamespace

        base = SimpleNamespace(model="qwen3.7-max", kwargs={})  # no api_key, no _api_key
        assert build_bounded_chat_lm(base, max_tokens=512, temperature=0.0, timeout=30.0) is None

    @patch("fleet_rlm.runtime.lm.openai.OpenAI")
    def test_builds_from_dspy_lm_kwargs(self, mock_openai):
        from types import SimpleNamespace

        base = SimpleNamespace(
            model="qwen3.7-max",
            kwargs={"api_key": "planner-key", "api_base": "http://localhost:8000", "custom_llm_provider": "openai"},
        )

        lm = build_bounded_chat_lm(base, max_tokens=4096, temperature=0.0, timeout=90.0, num_retries=0)
        assert isinstance(lm, BoundedChatLM)
        assert lm.model == "qwen3.7-max"
        assert lm._max_tokens == 4096
        assert lm._timeout == 90.0
        assert lm._api_key == "planner-key"
        assert lm._api_base == "http://localhost:8000"
        assert lm._disable_thinking is True
