from __future__ import annotations

from types import SimpleNamespace

import dspy
import httpx
import pytest
from pydantic import ValidationError

from fleet_rlm.config import FleetSettings
from fleet_rlm.models import build_lm, complete_text


def test_databricks_mlflow_configuration_requires_governed_trace_storage() -> None:
    with pytest.raises(ValueError, match="Unity Catalog"):
        FleetSettings(
            mlflow_mode="databricks",
            mlflow_databricks_tracking_uri="databricks",
            mlflow_databricks_trace_catalog="",
            mlflow_databricks_trace_schema="",
            mlflow_databricks_tracing_sql_warehouse_id="",
        )

    configured = FleetSettings(
        mlflow_mode="databricks",
        mlflow_databricks_tracking_uri="databricks",
        mlflow_databricks_experiment_name="/Shared/fleet-rlm",
        mlflow_databricks_trace_catalog="fleet",
        mlflow_databricks_trace_schema="traces",
        mlflow_databricks_tracing_sql_warehouse_id="warehouse-id",
    )
    assert configured.mlflow_experiment_name == "/Shared/fleet-rlm"


def test_profile_specific_mlflow_environment_ignores_sdk_destination(monkeypatch) -> None:
    monkeypatch.setenv("FLEET_MLFLOW_LOCAL_PORT", "5002")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "databricks")
    monkeypatch.setenv("FLEET_MLFLOW_LOCAL_EXPERIMENT_NAME", "fleet-test")
    settings = FleetSettings(_env_file=None)
    assert settings.mlflow_tracking_uri == "http://127.0.0.1:5002"
    assert settings.mlflow_experiment_name == "fleet-test"


def test_default_model_and_independent_role_connection(monkeypatch):
    monkeypatch.setenv("SUB_KEY", "sub-secret")
    settings = FleetSettings(
        _env_file=None,
        fleet_llm_model="openai/default",
        fleet_llm_api_base="http://localhost:8000/v1",
        fleet_llm_api_key="default-secret",
        fleet_sub_model="anthropic/custom",
        fleet_sub_api_key_env="SUB_KEY",
    )
    assert settings.resolved_model("root").model == "openai/default"
    assert settings.resolved_model("reflection").api_base == "http://localhost:8000/v1"
    assert settings.resolved_model("sub").api_base == ""
    assert settings.resolved_model("sub").api_key.get_secret_value() == "sub-secret"
    assert settings.resolved_model("judge").temperature == 0
    calls = []
    monkeypatch.setattr(
        dspy, "LM", lambda model, **kwargs: calls.append((model, kwargs)) or SimpleNamespace(model=model)
    )
    build_lm(settings, "", "root")
    assert calls[0][1]["engine"] == "lm15"
    assert calls[0][1]["api_key"] == "default-secret"
    assert not hasattr(build_lm(settings, "", "root"), "fleet_model_id")


def test_secret_snapshot_survives_session_rebuild_and_reload(tmp_path, monkeypatch):
    path = tmp_path / "credentials.env"
    path.write_text("CUSTOM_KEY=first-value\n")
    settings = FleetSettings(_env_file=path, fleet_llm_api_key_env="CUSTOM_KEY")
    path.write_text("CUSTOM_KEY=second-value\n")
    monkeypatch.setenv("CUSTOM_KEY", "environment-value")
    updated = settings.for_session({"models": {"root": "openai/new", "sub": "openai/child"}})
    assert updated.resolved_model("root").api_key.get_secret_value() == "first-value"
    assert updated.resolved_model("sub").model == "openai/child"
    assert (
        FleetSettings(_env_file=path, fleet_llm_api_key_env="CUSTOM_KEY")
        .resolved_model("root")
        .api_key.get_secret_value()
        == "environment-value"
    )
    assert "first-value" not in str(settings.configuration())
    assert "first-value" not in repr(settings)


@pytest.mark.parametrize(
    "endpoint", ["http://remote.example/v1", "https://user:pass@example.com/v1", "https://example.com/v1?key=x"]
)
def test_invalid_endpoints_fail_during_settings_load(endpoint):
    with pytest.raises(ValidationError):
        FleetSettings(_env_file=None, fleet_llm_api_base=endpoint)


@pytest.mark.parametrize("table", ["models", "connections"])
def test_obsolete_empty_registry_tables_fail(tmp_path, monkeypatch, table):
    path = tmp_path / "settings.toml"
    path.write_text(f"[config]\nschema_version=5\n[{table}]\n")
    monkeypatch.setenv("FLEET_CONFIG", str(path))
    with pytest.raises(ValueError, match="removed"):
        FleetSettings(_env_file=None)


@pytest.mark.parametrize(
    "payload", [{"data": [{"id": "foo/bar"}]}, {"data": []}, {"wrong": []}, {"data": [{"id": 123}]}]
)
def test_optional_discovery_validates_response_and_role(monkeypatch, payload):
    from contextlib import contextmanager

    calls = []

    @contextmanager
    def stream(method, url, **kwargs):
        calls.append((method, url, kwargs))
        yield httpx.Response(200, json=payload, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "stream", stream)
    settings = FleetSettings(
        _env_file=None, fleet_sub_model="openai/sub", fleet_sub_api_base="https://models.example/v1"
    )
    assert settings.list_models(role="root") == []
    if payload.get("data") in ([], [{"id": "foo/bar"}]):
        assert settings.list_models(role="sub") == (["openai/foo/bar"] if payload["data"] else [])
    else:
        with pytest.raises(ValueError):
            settings.list_models(role="sub")
    assert calls[-1][1] == "https://models.example/v1/models"


def test_complete_text_preserves_generation_defaults():
    calls = []
    lm = SimpleNamespace(model="openai/test", kwargs={"temperature": 0.2, "max_tokens": 300})

    class Fake:
        model = lm.model
        kwargs = lm.kwargs

        def __call__(self, request):
            calls.append(request)
            return SimpleNamespace(text="OK")

    assert complete_text(Fake(), "prompt", max_tokens=16) == "OK"
    assert calls[0].config.max_tokens == 16
    assert calls[0].config.temperature == 0.2


def test_native_auth_is_deferred_to_dspy(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "native-secret")
    settings = FleetSettings(_env_file=None, fleet_llm_model="openai/test")
    calls = []
    monkeypatch.setattr(dspy, "LM", lambda model, **kwargs: calls.append(kwargs) or SimpleNamespace(model=model))
    build_lm(settings, "", "root")
    assert "api_key" not in calls[0]


def test_global_generation_defaults_and_explicit_role_override(tmp_path, monkeypatch):
    path = tmp_path / "fleet.toml"
    path.write_text(
        '[config]\nschema_version=5\n[llm]\nmodel="openai/test"\nmax_tokens=100\n'
        "temperature=0.4\ncache=true\n[llm.sub]\nmax_tokens=200\n"
    )
    monkeypatch.setenv("FLEET_CONFIG", str(path))
    settings = FleetSettings(_env_file=None)
    assert settings.resolved_model("root").max_tokens == 100
    assert settings.resolved_model("sub").max_tokens == 200
    assert settings.resolved_model("reflection").temperature == 0.4
    assert settings.resolved_model("judge").cache


@pytest.mark.parametrize("failure", ["auth", "timeout", "oversized"])
def test_discovery_transport_and_size_failures(monkeypatch, failure):
    from contextlib import contextmanager

    @contextmanager
    def stream(method, url, **_kwargs):
        if failure == "timeout":
            raise httpx.ReadTimeout("private detail")
        response = httpx.Response(
            401 if failure == "auth" else 200,
            content=b"x" * 1_000_001 if failure == "oversized" else b"private detail",
            request=httpx.Request(method, url),
        )
        yield response

    monkeypatch.setattr(httpx, "stream", stream)
    settings = FleetSettings(_env_file=None, fleet_llm_api_base="http://127.0.0.1:8000/v1")
    with pytest.raises((httpx.HTTPError, ValueError)):
        settings.list_models()


@pytest.mark.parametrize(
    ("model", "wire_format"),
    [("openai/deepseek/deepseek-v4.1-flash", "json_object"), ("openai/gpt-4o-mini", "json_schema")],
)
def test_explicit_json_mode_reaches_native_request(model, wire_format):
    from dspy._vendor.lm15.providers.openai_chat import _response_format_to_chat
    from dspy.clients.execution import _canonical, prepare

    settings = FleetSettings(
        _env_file=None,
        fleet_root_model=model,
        fleet_root_adapter="json",
        fleet_root_response_format="json_object",
    )
    lm = build_lm(settings, "", "root")
    assert lm.kwargs["response_format"] == {"type": "json_object"}
    kwargs = {}
    settings.resolved_model("root").build_adapter()._prepare_response_format(
        lm, kwargs, dspy.Signature("task -> reasoning, code")
    )
    call = prepare(lm, prompt="Return JSON", messages=None, kwargs=kwargs)
    request = _canonical(call)
    wire = _response_format_to_chat(request.config.response_format)
    assert wire["type"] == wire_format
    if wire_format == "json_object":
        assert wire == {"type": "json_object"}
    else:
        assert wire["json_schema"]["strict"] is True
        assert set(wire["json_schema"]["schema"]["required"]) == {"reasoning", "code"}


def test_response_format_inheritance_validation_and_digest():
    settings = FleetSettings(
        _env_file=None,
        fleet_llm_adapter="json",
        fleet_llm_response_format="json_object",
        fleet_sub_response_format="auto",
    )
    assert settings.resolved_model("root").response_format == "json_object"
    assert settings.resolved_model("sub").response_format == "auto"
    baseline = FleetSettings(_env_file=None)
    assert settings.configuration()["digest"] != baseline.configuration()["digest"]
    with pytest.raises(ValueError, match="JSON adapter"):
        FleetSettings(_env_file=None, fleet_root_response_format="json_object")


def test_response_format_sources_and_session_snapshot(tmp_path, monkeypatch):
    config = tmp_path / "fleet.toml"
    config.write_text('[config]\nschema_version=5\n[llm]\nadapter="json"\nresponse_format="json_object"\n')
    env = tmp_path / ".env"
    env.write_text("FLEET_SUB_RESPONSE_FORMAT=auto\n")
    monkeypatch.setenv("FLEET_CONFIG", str(config))
    monkeypatch.setenv("FLEET_ROOT_RESPONSE_FORMAT", "auto")
    settings = FleetSettings(_env_file=env, fleet_judge_response_format="auto")
    report = settings.configuration()
    assert report["values"]["fleet_llm_response_format"] == "json_object"
    assert report["sources"]["fleet_llm_response_format"] == "toml"
    assert report["sources"]["fleet_sub_response_format"] == "dotenv"
    assert report["sources"]["fleet_root_response_format"] == "environment"
    assert report["sources"]["fleet_judge_response_format"] == "explicit"
    updated = settings.for_session({"models": {"root": "openai/other", "sub": "openai/child"}})
    assert updated.resolved_model("root").response_format == "auto"
    assert updated.resolved_model("reflection").response_format == "json_object"
