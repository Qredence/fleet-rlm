from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import dspy
import pytest

from fleet_rlm.config import FleetSettings


def _unity_gateway_test_settings() -> FleetSettings:
    return FleetSettings(
        _env_file=None,
        fleet_root_model="openai/system.ai.deepseek-v4-1-flash",
        fleet_sub_model="openai/system.ai.claude-haiku-5-5",
        fleet_reflection_model="openai/system.ai.claude-sonnet-5-5",
        fleet_llm_base_url="https://workspace.example.com/ai-gateway/mlflow/v1",
        databricks_token="test-token",
    )


def test_databricks_mlflow_configuration_requires_governed_trace_storage() -> None:
    with pytest.raises(ValueError, match="Unity Catalog"):
        FleetSettings(
            mlflow_mode="databricks",
            mlflow_tracking_uri="databricks",
            mlflow_trace_catalog="",
            mlflow_trace_schema="",
            mlflow_tracing_sql_warehouse_id="",
        )

    configured = FleetSettings(
        mlflow_mode="databricks",
        mlflow_tracking_uri="databricks",
        mlflow_experiment_name="/Shared/fleet-rlm",
        mlflow_trace_catalog="fleet",
        mlflow_trace_schema="traces",
        mlflow_tracing_sql_warehouse_id="warehouse-id",
    )
    assert configured.mlflow_experiment_name == "/Shared/fleet-rlm"


def test_unity_gateway_models_use_native_dspy_engine_and_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_lm(model: str, **kwargs: Any) -> SimpleNamespace:
        calls.append((model, kwargs))
        return SimpleNamespace(model=model, kwargs=kwargs)

    monkeypatch.setattr(dspy, "LM", fake_lm)
    settings = FleetSettings(
        _env_file=None,
        fleet_root_model="openai/uscentral.ai_gateway.deepseek-v4-1-flash-service",
        fleet_sub_model="openai/system.ai.claude-haiku-5-5",
        fleet_reflection_model="openai/system.ai.claude-sonnet-5-5",
        databricks_host="workspace.example.com",
        databricks_token="test-token",
        fleet_llm_base_url="",
    )

    root_lm, sub_lm, reflection_lm, _ = settings.build_lms()

    gateway_options = {
        "api_base": "https://workspace.example.com/ai-gateway/mlflow/v1",
        "api_key": "test-token",
        "engine": "lm15",
        "cache": False,
        "num_retries": 3,
    }
    assert root_lm.kwargs == gateway_options
    assert sub_lm.kwargs == gateway_options
    # config/fleet.toml gives GEPA reflection the DSPy-recommended temperature and output budget.
    reflection_options = {**gateway_options, "temperature": 1.0, "max_tokens": 32000}
    assert reflection_lm.kwargs == reflection_options
    assert calls == [
        ("openai/uscentral.ai_gateway.deepseek-v4-1-flash-service", gateway_options),
        ("openai/system.ai.claude-haiku-5-5", gateway_options),
        ("openai/system.ai.claude-sonnet-5-5", reflection_options),
    ]
    assert "test-token" not in repr(settings)


def test_typesafe_route_uses_the_configured_model_not_the_client_default(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, str]]] = []

    def fake_lm(model: str, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(model=model)

    def fake_typesafe(model: str, *, api_key: str) -> SimpleNamespace:
        calls.append((model, {"api_key": api_key}))
        return SimpleNamespace(model=model)

    monkeypatch.setattr(dspy, "LM", fake_lm)
    monkeypatch.setattr(dspy.experimental, "TypeSafe", fake_typesafe)
    settings = FleetSettings(
        _env_file=None,
        fleet_database_url="postgresql://unused:unused@localhost/fleet",
        fleet_root_model="openai/test-root",
        fleet_sub_model="openai/test-sub",
        fleet_reflection_model="openai/test-reflection",
        fleet_llm_base_url="https://workspace.example.com/ai-gateway/mlflow/v1",
        databricks_token="test-token",
        typesafe_model="jev-1.14.0",
        typesafe_api_key="typesafe-test-token",
    )

    *_, jev_lm = settings.build_lms()

    assert settings.typesafe_model == "jev-1.14.0"
    assert jev_lm.model == "jev-1.14.0"
    assert calls == [("jev-1.14.0", {"api_key": "typesafe-test-token"})]


def test_non_unity_gateway_models_are_rejected_without_backend_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_lm(model: str, **_kwargs: Any) -> SimpleNamespace:
        calls.append(model)
        return SimpleNamespace(model=model)

    monkeypatch.setattr(dspy, "LM", fake_lm)
    with pytest.raises(ValueError, match="OpenAI-compatible"):
        FleetSettings(
            _env_file=None,
            fleet_root_model="databricks/databricks-claude-sonnet-5.5",
            fleet_sub_model="openai/system.ai.claude-haiku-5-5",
            fleet_reflection_model="openai/system.ai.claude-sonnet-5-5",
            databricks_host="workspace.example.com",
            databricks_token="test-token",
        )

    assert calls == []


def test_unity_gateway_models_require_token_and_https_base_url() -> None:
    settings = FleetSettings(
        _env_file=None,
        fleet_root_model="openai/system.ai.claude-haiku-5-5",
        databricks_host="",
        databricks_token="",
        fleet_llm_base_url="",
    )
    with pytest.raises(ValueError, match="HTTPS workspace URL"):
        settings.build_lms()

    with pytest.raises(ValueError, match="HTTPS workspace URL"):
        FleetSettings(
            _env_file=None,
            fleet_root_model="openai/system.ai.claude-haiku-5-5",
            fleet_llm_base_url="http://gateway.example.com/ai-gateway/mlflow/v1",
            databricks_token="test-token",
        )


def test_fleet_prefixed_mlflow_environment_takes_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_MLFLOW_TRACKING_URI", "sqlite:///fleet-test.db")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks")
    monkeypatch.setenv("FLEET_MLFLOW_EXPERIMENT_NAME", "fleet-test")
    settings = FleetSettings(_env_file=None)

    assert settings.mlflow_tracking_uri == "sqlite:///fleet-test.db"
    assert settings.mlflow_experiment_name == "fleet-test"
