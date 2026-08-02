"""Unit contracts for opt-in Databricks MLflow setup."""

from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

import fleet_rlm.observability.tracing as tracing
from fleet_rlm.config import Settings


@pytest.fixture(autouse=True)
def _reset_tracing_latch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the tracing configuration and activation state for a test."""
    monkeypatch.setattr(tracing, "_TRACING_CONFIGURED", False)
    monkeypatch.setattr(tracing, "_TRACING_ACTIVE", False)


def _install_fake_mlflow(
    monkeypatch: pytest.MonkeyPatch,
    *,
    set_tracking_uri: Any | None = None,
    set_experiment: Any | None = None,
    autolog: Any | None = None,
    raise_on_import: BaseException | None = None,
) -> SimpleNamespace:
    """
    Install fake MLflow modules for tracing tests and record their interactions.

    Parameters:
        set_tracking_uri: Optional replacement for the fake tracking URI setter.
        set_experiment: Optional replacement for the fake experiment setter.
        autolog: Optional replacement for the fake DSPy autologging function.
        raise_on_import: Exception raised when attributes are accessed on the fake MLflow modules.

    Returns:
        A namespace containing recorded MLflow calls and configurable fake functions.
    """
    if raise_on_import is not None:

        class _Boom(ModuleType):
            def __getattr__(self, name: str) -> Any:
                raise raise_on_import

        monkeypatch.setitem(sys.modules, "mlflow", _Boom("mlflow"))
        monkeypatch.setitem(sys.modules, "mlflow.dspy", _Boom("mlflow.dspy"))
        return SimpleNamespace()

    calls = SimpleNamespace(
        set_tracking_uri=set_tracking_uri or (lambda *_a, **_k: None),
        set_experiment=set_experiment or (lambda *_a, **_k: None),
        autolog=autolog or (lambda *_a, **_k: None),
        tracking_uri_args=[],
        experiment_args=[],
        experiment_kwargs=[],
        autolog_calls=0,
        autolog_kwargs=[],
        async_logging_args=[],
        processor_args=[],
        flush_args=[],
    )

    def _set_uri(uri: str) -> None:
        calls.tracking_uri_args.append(uri)

    def _set_exp(*args: Any, **kwargs: Any) -> None:
        calls.experiment_args.append(args)
        calls.experiment_kwargs.append(kwargs)

    def _autolog(**kwargs: Any) -> None:
        """
        Record autologging configuration options for test assertions.

        Parameters:
            kwargs (Any): Autologging options to record.
        """
        calls.autolog_calls += 1
        calls.autolog_kwargs.append(kwargs)

    calls.set_tracking_uri = _set_uri if set_tracking_uri is None else set_tracking_uri
    calls.set_experiment = _set_exp if set_experiment is None else set_experiment
    calls.autolog = _autolog if autolog is None else autolog

    mlflow = ModuleType("mlflow")
    mlflow.set_tracking_uri = calls.set_tracking_uri  # type: ignore[attr-defined]
    mlflow.set_experiment = calls.set_experiment  # type: ignore[attr-defined]
    dspy_mod = ModuleType("mlflow.dspy")
    dspy_mod.autolog = calls.autolog  # type: ignore[attr-defined]
    mlflow.dspy = dspy_mod  # type: ignore[attr-defined]

    config_mod = ModuleType("mlflow.config")

    def _enable_async_logging(enabled: bool) -> None:
        """Record the configured asynchronous logging state."""
        calls.async_logging_args.append(enabled)

    config_mod.enable_async_logging = _enable_async_logging  # type: ignore[attr-defined]
    tracing_mod = ModuleType("mlflow.tracing")

    def _configure(*, span_processors: list[Any]) -> None:
        """Record the span processors supplied for tracing configuration.

        Parameters:
                span_processors (list[Any]): Span processors to record.
        """
        calls.processor_args.append(span_processors)

    tracing_mod.configure = _configure  # type: ignore[attr-defined]

    def _flush(**kwargs: Any) -> None:
        calls.flush_args.append(kwargs)

    mlflow.config = config_mod  # type: ignore[attr-defined]
    mlflow.tracing = tracing_mod  # type: ignore[attr-defined]
    mlflow.flush_trace_async_logging = _flush  # type: ignore[attr-defined]
    trace_location = ModuleType("mlflow.entities.trace_location")

    class UnityCatalog:
        def __init__(self, *, catalog_name: str, schema_name: str, table_prefix: str) -> None:
            self.catalog_name = catalog_name
            self.schema_name = schema_name
            self.table_prefix = table_prefix

    trace_location.UnityCatalog = UnityCatalog  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "mlflow", mlflow)
    monkeypatch.setitem(sys.modules, "mlflow.dspy", dspy_mod)
    monkeypatch.setitem(sys.modules, "mlflow.entities.trace_location", trace_location)
    # A previously imported real ``mlflow.exceptions`` would otherwise survive
    # this fake and change _validate_experiment_trace_location's control flow;
    # ``None`` makes the submodule import raise ImportError deterministically.
    monkeypatch.setitem(sys.modules, "mlflow.exceptions", None)
    return calls


def _enabled_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "mlflow_tracing_enabled": True,
        "mlflow_experiment_name": "fleet-test-exp",
        "mlflow_tracking_uri": "databricks",
        "mlflow_trace_catalog": "analytics",
        "mlflow_trace_schema": "traces",
        "mlflow_trace_table_prefix": "fleet_app",
        "mlflow_tracing_sql_warehouse_id": "warehouse-123",
    }
    values.update(overrides)
    return Settings(**values)


def test_configure_tracing_disabled_skips_mlflow(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("mlflow must not be imported when disabled")

    _install_fake_mlflow(monkeypatch, set_tracking_uri=_boom, set_experiment=_boom, autolog=_boom)
    tracing.configure_tracing(Settings(mlflow_tracing_enabled=False))
    assert tracing._TRACING_CONFIGURED is True


def test_configure_tracing_enabled_without_workspace_settings_is_soft_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_mlflow(monkeypatch)
    for name in (
        "FLEET_MLFLOW_EXPERIMENT_NAME",
        "FLEET_MLFLOW_TRACE_CATALOG",
        "FLEET_MLFLOW_TRACE_SCHEMA",
        "FLEET_MLFLOW_TRACE_TABLE_PREFIX",
        "FLEET_MLFLOW_TRACING_SQL_WAREHOUSE_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    tracing.configure_tracing(Settings(_env_file=None, mlflow_tracing_enabled=True))
    assert calls.tracking_uri_args == []
    assert calls.autolog_calls == 0


def test_configure_tracing_enabled_sets_uri_experiment_and_autolog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_mlflow(monkeypatch)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    tracing.configure_tracing(_enabled_settings())
    assert calls.tracking_uri_args == ["databricks"]
    assert calls.experiment_args == [()]
    assert calls.experiment_kwargs[0]["experiment_name"] == "fleet-test-exp"
    location = calls.experiment_kwargs[0]["trace_location"]
    assert location.catalog_name == "analytics"
    assert location.schema_name == "traces"
    assert location.table_prefix == "fleet_app"
    assert os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] == "warehouse-123"
    assert calls.autolog_calls == 1
    assert calls.autolog_kwargs == [{"log_traces": True, "log_traces_from_eval": False, "silent": True}]
    assert calls.async_logging_args == [True]
    assert len(calls.processor_args) == 1
    assert calls.processor_args[0][0] is tracing._sanitize_mlflow_span


def test_configure_tracing_applies_sampling_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_mlflow(monkeypatch)

    tracing.configure_tracing(_enabled_settings(mlflow_trace_sampling_ratio=0.25, mlflow_async_logging=False))

    assert os.environ["MLFLOW_TRACE_SAMPLING_RATIO"] == "0.25"
    assert calls.async_logging_args == [False]


def test_mlflow_315_span_processor_bounds_and_redacts_values() -> None:
    class Span:
        def __init__(self) -> None:
            self.inputs: dict[str, object] = {"token": "real-secret", "body": "x" * 2_000}
            self.outputs: dict[str, object] = {"answer": "y" * 2_000}
            self.attributes: dict[str, object] = {"api_key": "real-secret", "kind": "tool"}

        def set_inputs(self, value: object) -> None:
            """Set the span inputs to the specified value.

            Parameters:
                value (object): The inputs associated with the span.
            """
            self.inputs = value

        def set_outputs(self, value: object) -> None:
            self.outputs = value

        def set_attributes(self, value: dict[str, object]) -> None:
            """Set the span attributes to the provided mapping.

            Parameters:
                value (dict[str, object]): Attributes to associate with the span.
            """
            self.attributes = value

    span = Span()

    tracing._sanitize_mlflow_span(span)

    assert span.inputs["token"] == "[redacted]"
    assert isinstance(span.inputs["body"], str)
    assert len(span.inputs["body"]) <= 256
    assert span.outputs["answer"].startswith("[redacted sha256=")
    assert span.attributes["api_key"] == "[redacted]"


def test_mlflow_span_processor_redacts_autolog_content_fields() -> None:
    class Span:
        def __init__(self) -> None:
            self.inputs: dict[str, object] = {
                "prompt": "candidate instruction must never be exported",
                "token_usage": 42,
            }
            self.outputs: dict[str, object] = {
                "response": "provider body must never be exported",
                "duration_ms": 15,
            }
            self.attributes: dict[str, object] = {"engine": "gepa", "tool_output": "private tool result"}

        def set_inputs(self, value: object) -> None:
            self.inputs = value

        def set_outputs(self, value: object) -> None:
            self.outputs = value

        def set_attributes(self, value: dict[str, object]) -> None:
            self.attributes = value

    span = Span()

    tracing._sanitize_mlflow_span(span)

    assert span.inputs["prompt"].startswith("[redacted sha256=")
    assert span.inputs["token_usage"] == 42
    assert span.outputs["response"].startswith("[redacted sha256=")
    assert span.outputs["duration_ms"] == 15
    assert span.attributes["engine"] == "gepa"
    assert span.attributes["tool_output"].startswith("[redacted sha256=")


def test_mlflow_span_processor_redacts_namespaced_and_unknown_text_fields() -> None:
    sanitized = tracing._sanitize_mlflow_value(
        {
            "gen_ai.prompt": "private prompt",
            "mlflow.spanInputs": "private input",
            "custom_question": "private question",
            "model": "openai/gpt-5",
        }
    )

    assert isinstance(sanitized, dict)
    assert sanitized["gen_ai.prompt"].startswith("[redacted sha256=")
    assert sanitized["mlflow.spanInputs"].startswith("[redacted sha256=")
    assert sanitized["custom_question"].startswith("[redacted sha256=")
    assert sanitized["model"] == "openai/gpt-5"


def test_configure_tracing_local_server_needs_only_experiment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_mlflow(monkeypatch)
    tracing.configure_tracing(
        Settings(
            _env_file=None,
            mlflow_tracing_enabled=True,
            mlflow_experiment_name="fleet-rlm-eval",
            mlflow_tracking_uri="http://localhost:5001",
        )
    )
    assert calls.tracking_uri_args == ["http://localhost:5001"]
    assert calls.experiment_kwargs == [{"experiment_name": "fleet-rlm-eval"}]
    assert calls.autolog_calls == 1


def test_configure_tracing_ignores_tracking_uri_environment_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_mlflow(monkeypatch)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://ignored.example:5001")
    settings = Settings(
        _env_file=None,
        mlflow_tracing_enabled=True,
        mlflow_experiment_name="fleet-rlm-eval",
        mlflow_tracking_uri="http://configured.example:5001",
    )

    tracing.configure_tracing(settings)

    assert calls.tracking_uri_args == ["http://configured.example:5001"]
    assert calls.autolog_calls == 1


def test_configure_tracing_bridges_dotenv_databricks_auth_without_overriding_exports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_mlflow(monkeypatch)
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
    settings = _enabled_settings()
    settings._dotenv_values = {
        "DATABRICKS_HOST": "https://workspace.example",
        "DATABRICKS_TOKEN": "dotenv-token",
    }

    tracing.configure_tracing(settings)

    assert os.environ["DATABRICKS_HOST"] == "https://workspace.example"
    assert os.environ["DATABRICKS_TOKEN"] == "dotenv-token"
    assert calls.autolog_calls == 1


def test_configure_tracing_preserves_exported_databricks_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_mlflow(monkeypatch)
    monkeypatch.setenv("DATABRICKS_HOST", "https://exported.example")
    monkeypatch.setenv("DATABRICKS_TOKEN", "exported-token")
    settings = _enabled_settings()
    settings._dotenv_values = {
        "DATABRICKS_HOST": "https://dotenv.example",
        "DATABRICKS_TOKEN": "dotenv-token",
    }

    tracing.configure_tracing(settings)

    assert os.environ["DATABRICKS_HOST"] == "https://exported.example"
    assert os.environ["DATABRICKS_TOKEN"] == "exported-token"
    assert calls.autolog_calls == 1


def test_configure_tracing_setup_failure_is_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_mlflow(monkeypatch, raise_on_import=RuntimeError("mlflow unavailable"))
    tracing.configure_tracing(_enabled_settings())
    assert tracing._TRACING_CONFIGURED is True


def test_configure_tracing_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_mlflow(monkeypatch)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    settings = _enabled_settings()
    tracing.configure_tracing(settings)
    tracing.configure_tracing(settings)
    assert calls.tracking_uri_args == ["databricks"]
    assert calls.autolog_calls == 1


def test_flush_tracing_terminates_async_exporter(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_mlflow(monkeypatch)
    tracing.configure_tracing(_enabled_settings())

    tracing.flush_tracing()

    assert calls.flush_args == [{"terminate": True}]
