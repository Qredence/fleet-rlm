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


def test_build_lm_passes_provider_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    lm = MagicMock(return_value="bounded-lm")
    monkeypatch.setattr(factory.dspy, "LM", lm)

    factory.build_lm("openai/model", api_key=None, timeout_seconds=37)

    assert lm.call_args.kwargs["timeout"] == 37


@pytest.mark.parametrize("trailing_slash", [""])
def test_databricks_deepseek_declares_exact_schema_capability(trailing_slash: str) -> None:
    from dspy.clients.backend_selection import select_backend

    lm = factory.build_lm(
        "uscentral.ai_gateway.deepseek-v4-1-flash-service",
        api_key="token",
        base_url=f"https://workspace.example/ai-gateway/mlflow/v1{trailing_slash}",
        cache=False,
    )

    selected = select_backend(lm)
    assert selected.native is True
    assert selected.resolution.provider == "fleet-databricks"
    assert selected.resolution.model == "uscentral.ai_gateway.deepseek-v4-1-flash-service"
    assert selected.clients["api_base"] == f"https://workspace.example/ai-gateway/mlflow/v1{trailing_slash}"
    assert "response_format" in lm.supported_params
    assert lm.supports_response_schema is True


@pytest.mark.parametrize(
    "base_url",
    [None, "https://workspace.example/v1", "https:///ai-gateway/mlflow/v1", "https://[bad/ai-gateway/mlflow/v1"],
)
def test_databricks_deepseek_requires_ai_gateway_route(base_url: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    lm = MagicMock()
    monkeypatch.setattr(factory.dspy, "LM", lm)
    with pytest.raises(ValueError, match="AI Gateway base URL"):
        factory.build_lm("uscentral.ai_gateway.deepseek-v4-1-flash-service", api_key="token", base_url=base_url)
    lm.assert_not_called()


@pytest.mark.asyncio
async def test_databricks_action_request_carries_json_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    import dspy
    from dspy.clients.engines.lm15_engine import AsyncLM15Engine
    from dspy.lm15 import Request, Response, response_from_openai_chat

    from fleet_rlm.rlm.program import FleetJSONAdapter

    class Action(dspy.Signature):
        """One RLM action."""

        request: str = dspy.InputField()
        reasoning: str = dspy.OutputField()
        code: str = dspy.OutputField()

    seen: list[Request] = []

    async def complete(_self: AsyncLM15Engine, request: Request) -> Response:
        seen.append(request)
        return response_from_openai_chat(
            {
                "id": "offline-action",
                "object": "chat.completion",
                "created": 0,
                "model": "uscentral.ai_gateway.deepseek-v4-1-flash-service",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"reasoning":"run","code":"print(1)"}'},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            model=request.model,
        )

    monkeypatch.setattr(AsyncLM15Engine, "complete", complete)
    lm = factory.build_lm(
        "uscentral.ai_gateway.deepseek-v4-1-flash-service",
        api_key="token",
        base_url="https://workspace.example/ai-gateway/mlflow/v1",
        cache=False,
    )
    with dspy.context(lm=lm, adapter=FleetJSONAdapter()):
        result = await dspy.Predict(Action).acall(request="execute")

    assert result.code == "print(1)"
    assert len(seen) == 1
    assert seen[0].model.endswith("/uscentral.ai_gateway.deepseek-v4-1-flash-service")
    assert seen[0].config.response_format is not None
    assert seen[0].config.response_format["type"] == "json_schema"


@pytest.mark.asyncio
async def test_alibaba_action_request_uses_json_object_only(monkeypatch: pytest.MonkeyPatch) -> None:
    import dspy
    from dspy.clients.engines.lm15_engine import AsyncLM15Engine
    from dspy.lm15 import Request, Response, response_from_openai_chat

    from fleet_rlm.rlm.program import FleetJSONAdapter

    class Action(dspy.Signature):
        """One RLM action."""

        request: str = dspy.InputField()
        reasoning: str = dspy.OutputField()
        code: str = dspy.OutputField()

    seen: list[Request] = []

    async def complete(_self: AsyncLM15Engine, request: Request) -> Response:
        seen.append(request)
        return response_from_openai_chat(
            {
                "id": "offline-action",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-v4.1-flash",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": '{"reasoning":"run","code":"print(1)"}'},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            model=request.model,
        )

    monkeypatch.setattr(AsyncLM15Engine, "complete", complete)
    lm = factory.build_lm(
        "deepseek-v4.1-flash",
        api_key="token",
        base_url="https://dashscope.example/compatible-mode/v1",
        cache=False,
    )
    with dspy.context(lm=lm, adapter=FleetJSONAdapter()):
        result = await dspy.Predict(Action).acall(request="execute")

    assert result.code == "print(1)"
    assert len(seen) == 1
    assert seen[0].config.response_format == {"type": "json_object"}


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
