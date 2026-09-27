"""Unit tests for normalized dspy.LM construction helpers."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

import fleet_rlm.rlm.program as factory
from fleet_rlm.config.settings import Settings


def test_build_lm_stays_native_in_a_fresh_process() -> None:
    script = """
import dspy
import sys
from fleet_rlm.rlm.program import build_lm

lm = build_lm("openai/test", api_key=None)
assert lm.engine == "lm15"
assert "litellm" not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_model_bundle_applies_independent_role_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROOT_KEY", "root-secret")
    monkeypatch.setenv("SUB_KEY", "sub-secret")
    build = MagicMock(side_effect=("root-lm", "sub-lm"))
    monkeypatch.setattr(factory, "build_lm", build)
    settings = Settings(
        root_model="openai/root",
        sub_model="openai/sub",
        root_llm_api_key_env="ROOT_KEY",
        sub_llm_api_key_env="SUB_KEY",
        root_llm_max_tokens=101,
        sub_llm_max_tokens=202,
        root_llm_timeout_seconds=303,
        sub_llm_timeout_seconds=404,
        root_llm_cache=True,
        sub_llm_cache=False,
        root_llm_num_retries=1,
        sub_llm_num_retries=4,
        sub_llm_temperature=0.3,
        root_llm_reasoning_effort="none",
    )

    bundle = factory.build_model_bundle(settings)

    assert bundle.root_lm == "root-lm"
    assert bundle.sub_lm == "sub-lm"
    assert build.call_args_list[0].kwargs["max_tokens"] == 101
    assert build.call_args_list[0].kwargs["timeout_seconds"] == 303
    assert build.call_args_list[0].kwargs["cache"] is True
    assert build.call_args_list[0].kwargs["reasoning_effort"] == "none"
    assert build.call_args_list[1].kwargs["max_tokens"] == 202
    assert build.call_args_list[1].kwargs["timeout_seconds"] == 404
    assert build.call_args_list[1].kwargs["cache"] is False
    assert build.call_args_list[1].kwargs["temperature"] == 0.3


def test_build_lm_passes_reasoning_effort_only_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    lm = MagicMock(side_effect=("default-lm", "bounded-lm"))
    monkeypatch.setattr(factory.dspy, "LM", lm)

    default = factory.build_lm("openai/default", api_key=None)
    bounded = factory.build_lm("openai/bounded", api_key=None, reasoning_effort="none")

    assert default == "default-lm"
    assert bounded == "bounded-lm"
    assert "reasoning_effort" not in lm.call_args_list[0].kwargs
    assert lm.call_args_list[1].kwargs["reasoning_effort"] == "none"
    assert "allowed_openai_params" not in lm.call_args_list[1].kwargs


def test_build_lm_uses_dspy_aggregated_completion_path(monkeypatch: pytest.MonkeyPatch) -> None:
    lm = MagicMock(return_value="lm")
    monkeypatch.setattr(factory.dspy, "LM", lm)

    factory.build_lm("openai/model", api_key=None)

    kwargs = lm.call_args.kwargs
    # DSPy's native RLM path consumes the provider's completed response rather
    # than a raw streaming wrapper.
    assert "stream" not in kwargs
    assert "stream_options" not in kwargs
    assert kwargs["engine"] == "lm15"


def test_build_lm_requests_usage_and_reasoning_effort_together(monkeypatch: pytest.MonkeyPatch) -> None:
    lm = MagicMock(return_value="lm")
    monkeypatch.setattr(factory.dspy, "LM", lm)

    factory.build_lm("openai/model", api_key=None, reasoning_effort="none")

    kwargs = lm.call_args.kwargs
    assert kwargs["reasoning_effort"] == "none"
    assert "stream" not in kwargs
    assert "stream_options" not in kwargs
    assert "allowed_openai_params" not in kwargs


@pytest.mark.parametrize("reasoning_effort", [None, "none"])
@pytest.mark.asyncio
async def test_build_lm_async_call_processes_an_aggregated_completion(
    monkeypatch: pytest.MonkeyPatch,
    reasoning_effort: str | None,
) -> None:
    """The native async RLM path receives an aggregated completion response."""

    import dspy.clients.lm as dspy_lm
    from dspy.clients.engines.lm15_engine import AsyncLM15Engine
    from dspy.lm15 import Request, Response, response_from_openai_chat

    async def complete(self: AsyncLM15Engine, request: Request) -> Response:
        assert self.resolve(request.model).provider == "openai-chat"
        return response_from_openai_chat(
            {
                "id": "offline-test",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "OK"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            model=request.model,
        )

    monkeypatch.setattr(AsyncLM15Engine, "complete", complete)

    lm = factory.build_lm("openai/model", api_key=None, cache=False, reasoning_effort=reasoning_effort)

    process_send_stream = getattr(dspy_lm.dspy.settings, "send_stream", None)
    with dspy_lm.dspy.context(send_stream=None):
        result = await lm.acall(prompt="Reply with exactly OK.")

    assert result == ["OK"]
    assert getattr(dspy_lm.dspy.settings, "send_stream", None) is process_send_stream


def test_build_lm_uses_chat_completion_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    lm = MagicMock(return_value="deepseek-lm")
    monkeypatch.setattr(factory.dspy, "LM", lm)

    result = factory.build_lm(
        "deepseek-v4-flash",
        api_key="token",
        base_url="https://gateway.example/v1",
    )

    assert result == "deepseek-lm"
    assert lm.call_args.args == ("openai/deepseek-v4-flash",)
    assert lm.call_args.kwargs["model_type"] == "chat"
    assert lm.call_args.kwargs["api_base"] == "https://gateway.example/v1"
    assert "headers" not in lm.call_args.kwargs


def test_build_lm_passes_provider_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    lm = MagicMock(return_value="bounded-lm")
    monkeypatch.setattr(factory.dspy, "LM", lm)

    factory.build_lm("openai/model", api_key=None, timeout_seconds=37)

    assert lm.call_args.kwargs["timeout"] == 37


def test_native_route_resolves_unqualified_deepseek_model() -> None:
    from dspy.clients.backend_selection import select_backend

    lm = factory.build_lm(
        "deepseek-v4-flash",
        api_key="token",
        base_url="https://gateway.example/v1",
        cache=False,
    )

    selected = select_backend(lm)
    assert lm.engine == "lm15"
    assert selected.native is True
    assert selected.resolution.provider == "openai-chat"
    assert selected.resolution.model == "deepseek-v4-flash"
    assert selected.clients["api_base"] == "https://gateway.example/v1"


@pytest.mark.asyncio
async def test_unsupported_native_request_does_not_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    import dspy.clients.lm as dspy_lm
    from dspy.utils.exceptions import LMUnsupportedFeatureError

    fallback = MagicMock(side_effect=AssertionError("LiteLLM fallback was called"))
    monkeypatch.setattr(dspy_lm, "_get_litellm", fallback)
    lm = factory.build_lm("openai/test", api_key=None, cache=False)

    with pytest.raises(LMUnsupportedFeatureError, match="allowed_openai_params"):
        await lm.acall(prompt="ping", allowed_openai_params=["unsupported"])

    fallback.assert_not_called()


def test_sanitize_base_url_accepts_https_and_strips_comments() -> None:
    assert factory.sanitize_base_url("https://opencode.ai/zen/v1") == "https://opencode.ai/zen/v1"
    assert factory.sanitize_base_url("https://opencode.ai/zen/v1/") == "https://opencode.ai/zen/v1"
    assert factory.sanitize_base_url("https://opencode.ai/zen/v1'   # real gateway") == "https://opencode.ai/zen/v1"
    assert factory.sanitize_base_url("'https://example.com/v1'") == "https://example.com/v1"


def test_sanitize_base_url_rejects_keys_and_empty() -> None:
    assert factory.sanitize_base_url(None) is None
    assert factory.sanitize_base_url("") is None
    assert factory.sanitize_base_url("sk-ws-H.not-a-url") is None
    assert factory.sanitize_base_url("openai.com/v1") is None  # missing scheme


def test_normalize_model_id_adds_openai_prefix_to_bare_names() -> None:
    assert factory.normalize_model_id("deepseek-v4-flash-free") == "openai/deepseek-v4-flash-free"
    assert factory.normalize_model_id("openai/gpt-4o-mini") == "openai/gpt-4o-mini"
    assert factory.normalize_model_id("anthropic/claude-sonnet-4") == "anthropic/claude-sonnet-4"


def test_runtime_does_not_accept_provider_environment_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "FLEET_OPENAI_API_KEY",
        "FLEET_LLM_BASE_URL",
        "FLEET_ROOT_MODEL",
        "FLEET_SUB_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "provider-alias-must-not-be-used")
    monkeypatch.setenv("DSPY_LM_MODEL", "provider/alias-model")

    settings = Settings()

    assert settings.llm_api_key is None
    assert settings.root_model == "openai/gpt-4o-mini"
    with pytest.raises(RuntimeError, match="FLEET_OPENAI_API_KEY"):
        factory.build_model_bundle(settings)


def test_whitespace_legacy_key_is_not_a_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLEET_OPENAI_API_KEY", raising=False)
    settings = Settings(llm_api_key=SecretStr("   "))

    assert factory.has_llm_credentials(settings) is False


def test_legacy_generic_key_does_not_cross_provider_role_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
    settings = Settings(
        llm_api_key=SecretStr("legacy-key"),
        root_llm_api_key_env="DATABRICKS_TOKEN",
        sub_llm_api_key_env="DATABRICKS_TOKEN",
    )

    assert factory.resolve_role_api_key(settings, settings.llm_role("root")) is None


def test_explicit_role_environment_credentials_are_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABRICKS_TOKEN", "provider-key")
    settings = Settings(
        root_llm_api_key_env="DATABRICKS_TOKEN",
        sub_llm_api_key_env="DATABRICKS_TOKEN",
    )

    assert settings.llm_api_key is None
    assert factory.has_llm_credentials(settings) is True
