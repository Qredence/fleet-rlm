"""Unit contracts for Fleet's bounded MLflow setup."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

import fleet_rlm.observability.tracing as tracing
from fleet_rlm.config.settings import FleetConfigurationError, Settings


@pytest.fixture(autouse=True)
def _reset_trace_content_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the shared readable content bound for a test."""
    tracing.set_tracing_active_for_tests(False)
    monkeypatch.setattr(tracing, "_TRACE_CONTENT_MAX_CHARS", 10_000)
    monkeypatch.setattr(tracing, "_TRACE_CONTENT_ENABLED", True)


def _install_fake_mlflow(
    monkeypatch: pytest.MonkeyPatch,
    *,
    set_tracking_uri: Any | None = None,
    set_experiment: Any | None = None,
    set_experiment_tag: Any | None = None,
    autolog: Any | None = None,
    raise_on_import: BaseException | None = None,
    experiment: Any | None = None,
) -> SimpleNamespace:
    """
    Install fake MLflow modules for tracing tests and record their interactions.

    Parameters:
        set_tracking_uri: Optional replacement for the fake tracking URI setter.
        set_experiment: Optional replacement for the fake experiment setter.
        autolog: Optional replacement for the fake DSPy autologging function.
        raise_on_import: Exception raised when attributes are accessed on the fake MLflow modules.
        experiment: Optional experiment returned by the fake ``get_experiment_by_name``;
            ``None`` (the default) keeps experiment lookups returning nothing.

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
        experiment_tag_args=[],
        experiment_lookups=[],
        autolog_calls=0,
        autolog_kwargs=[],
        async_logging_args=[],
        processor_args=[],
        flush_args=[],
    )
    current_tracking_uri = {"value": "initial"}

    def _set_uri(uri: str) -> None:
        calls.tracking_uri_args.append(uri)
        current_tracking_uri["value"] = uri

    def _set_exp(*args: Any, **kwargs: Any) -> None:
        calls.experiment_args.append(args)
        calls.experiment_kwargs.append(kwargs)

    def _get_experiment_by_name(name: str) -> Any:
        calls.experiment_lookups.append(name)
        return experiment

    def _set_experiment_tag(key: str, value: str) -> None:
        calls.experiment_tag_args.append((key, value))

    if set_experiment_tag is not None:
        _set_experiment_tag = set_experiment_tag

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
    mlflow.get_tracking_uri = lambda: current_tracking_uri["value"]  # type: ignore[attr-defined]
    mlflow.set_experiment = calls.set_experiment  # type: ignore[attr-defined]
    mlflow.get_experiment_by_name = _get_experiment_by_name  # type: ignore[attr-defined]
    mlflow.set_experiment_tag = _set_experiment_tag  # type: ignore[attr-defined]
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


def _install_fake_mlflow_exceptions(monkeypatch: pytest.MonkeyPatch) -> type[Exception]:
    """Make ``from mlflow.exceptions import MlflowException`` work against the fake."""

    class MlflowException(Exception):  # noqa: N818 - mirrors the real SDK name
        ...

    exceptions_mod = ModuleType("mlflow.exceptions")
    exceptions_mod.MlflowException = MlflowException  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlflow.exceptions", exceptions_mod)
    return MlflowException


def test_configure_tracing_records_experiment_purpose_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    experiment = SimpleNamespace(experiment_id="42", tags={})
    calls = _install_fake_mlflow(monkeypatch, experiment=experiment)
    _install_fake_mlflow_exceptions(monkeypatch)
    assert tracing.configure_tracing(_enabled_settings(mlflow_experiment_purpose="runtime")) is True
    assert calls.experiment_tag_args == [("fleet.experiment.purpose", "runtime")]


def test_configure_tracing_purpose_conflict_propagates_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    experiment = SimpleNamespace(experiment_id="42", tags={tracing._EXPERIMENT_PURPOSE_TAG: "evaluation"})
    _install_fake_mlflow(monkeypatch, experiment=experiment)
    _install_fake_mlflow_exceptions(monkeypatch)

    with pytest.raises(FleetConfigurationError, match="already marked with purpose") as exc_info:
        tracing.configure_tracing(_enabled_settings(mlflow_experiment_purpose="runtime"))
    assert "mlflow.experiment_purpose" in str(exc_info.value)
    assert "FLEET_MLFLOW_EXPERIMENT_PURPOSE" not in str(exc_info.value)
    assert tracing.is_tracing_active() is False


def test_configure_tracing_matching_purpose_does_not_rewrite_the_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    experiment = SimpleNamespace(experiment_id="42", tags={tracing._EXPERIMENT_PURPOSE_TAG: "runtime"})
    calls = _install_fake_mlflow(monkeypatch, experiment=experiment)
    _install_fake_mlflow_exceptions(monkeypatch)
    assert tracing.configure_tracing(_enabled_settings(mlflow_experiment_purpose="runtime")) is True
    assert calls.experiment_tag_args == []


def test_configure_tracing_purpose_tag_failure_is_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    experiment = SimpleNamespace(experiment_id="42", tags={})

    def _failing_tag(*_a: Any, **_k: Any) -> None:
        raise AttributeError("simulated MLflow tag-write failure")

    _install_fake_mlflow(monkeypatch, experiment=experiment, set_experiment_tag=_failing_tag)
    _install_fake_mlflow_exceptions(monkeypatch)
    assert tracing.configure_tracing(_enabled_settings(mlflow_experiment_purpose="runtime")) is True
    assert tracing.is_tracing_active() is True


def test_configure_tracing_disabled_skips_mlflow(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("mlflow must not be imported when disabled")

    _install_fake_mlflow(monkeypatch, set_tracking_uri=_boom, set_experiment=_boom, autolog=_boom)
    assert tracing.configure_tracing(Settings(mlflow_tracing_enabled=False)) is False


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
    assert tracing.configure_tracing(Settings(mlflow_tracing_enabled=True)) is False
    assert tracing.is_tracing_active() is False
    assert calls.tracking_uri_args == []
    assert calls.autolog_calls == 0


def test_unavailable_local_tracking_uri_does_not_activate_turn_spans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dead HTTP tracking must not leave Turn spans armed for MLflow retries."""
    fake = ModuleType("mlflow")
    fake.__file__ = str(Path(__file__).resolve())
    monkeypatch.setitem(sys.modules, "mlflow", fake)
    monkeypatch.setitem(sys.modules, "mlflow.dspy", ModuleType("mlflow.dspy"))
    monkeypatch.setattr(tracing, "_local_tracking_server_available", lambda _uri: False)

    assert (
        tracing.configure_tracing(
            Settings(
                mlflow_tracing_enabled=True,
                mlflow_experiment_name="fleet-rlm-eval",
                mlflow_tracking_uri="http://127.0.0.1:5001",
            )
        )
        is False
    )
    assert tracing.is_tracing_active() is False


def test_missing_export_distribution_is_handled_fail_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(_distribution: str) -> str:
        raise tracing.PackageNotFoundError

    monkeypatch.setattr(tracing, "package_version", missing)

    assert tracing._mlflow_export_versions_are_certified() is False


def test_missing_certified_mlflow_distribution_disables_tracing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_mlflow(monkeypatch)
    fake_mlflow = sys.modules["mlflow"]
    fake_mlflow.__file__ = str(Path(__file__).resolve())  # type: ignore[attr-defined]

    def missing_distribution(name: str) -> str:
        from importlib.metadata import PackageNotFoundError

        raise PackageNotFoundError(name)

    monkeypatch.setattr(tracing, "package_version", missing_distribution)
    assert tracing.configure_tracing(_enabled_settings()) is False
    assert tracing.is_tracing_active() is False


def test_configure_tracing_enabled_sets_uri_experiment_and_autolog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_mlflow(monkeypatch)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    assert tracing.configure_tracing(_enabled_settings()) is True
    assert tracing.is_tracing_active() is True
    assert calls.tracking_uri_args == ["databricks"]
    assert calls.experiment_args == [()]
    assert calls.experiment_kwargs[0]["experiment_name"] == "fleet-test-exp"
    location = calls.experiment_kwargs[0]["trace_location"]
    assert location.catalog_name == "analytics"
    assert location.schema_name == "traces"
    assert location.table_prefix == "fleet_app"
    assert os.environ["MLFLOW_TRACING_SQL_WAREHOUSE_ID"] == "warehouse-123"
    assert os.environ["MLFLOW_DISABLE_AGENT_HINT"] == "1"
    assert calls.autolog_calls == 1
    assert calls.autolog_kwargs == [
        {
            "log_traces": True,
            "log_traces_from_eval": False,
            "log_traces_from_compile": False,
            "log_compiles": False,
            "log_evals": False,
            "silent": True,
        }
    ]
    assert calls.async_logging_args == [True]
    assert len(calls.processor_args) == 1
    assert calls.processor_args[0][0] is tracing._sanitize_mlflow_span


def test_configure_tracing_applies_sampling_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_mlflow(monkeypatch)

    assert (
        tracing.configure_tracing(_enabled_settings(mlflow_trace_sampling_ratio=0.25, mlflow_async_logging=False))
        is True
    )

    assert os.environ["MLFLOW_TRACE_SAMPLING_RATIO"] == "0.25"
    assert os.environ["MLFLOW_ENABLE_ASYNC_TRACE_LOGGING"] == "false"
    assert calls.async_logging_args == [False]


def test_mlflow_316_span_processor_bounds_and_protects_secrets() -> None:
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
    assert span.inputs["body"] == "x" * 2_000
    assert span.outputs["answer"] == "y" * 2_000
    assert span.attributes["api_key"] == "[redacted]"


def test_trace_sanitizer_preserves_authorized_content_and_input_key_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "_TRACE_CONTENT_ENABLED", True)

    sanitized = tracing._sanitize_mlflow_value(
        {
            "system_prompt": "BEGIN SYSTEM\nUse the workspace tools.",
            "reasoning_content": "I will inspect the selected trace first.",
            "input_keys": ["messages", "prompt", "kwargs"],
            "prompt": "Explain the captured execution.",
        }
    )

    assert sanitized == {
        "system_prompt": "BEGIN SYSTEM\nUse the workspace tools.",
        "reasoning_content": "I will inspect the selected trace first.",
        "input_keys": ["messages", "prompt", "kwargs"],
        "prompt": "Explain the captured execution.",
    }


def test_trace_sanitizer_keeps_structural_keys_in_operational_only_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "_TRACE_CONTENT_ENABLED", False)

    sanitized = tracing._sanitize_mlflow_value(
        {
            "system_prompt": "private system instructions",
            "prompt": "private user request",
            "input_keys": ["messages", "prompt", "kwargs"],
        }
    )

    assert sanitized == {
        "system_prompt": "[content suppressed]",
        "prompt": "[content suppressed]",
        "input_keys": ["messages", "prompt", "kwargs"],
    }


def test_trace_text_redacts_structured_secrets_urls_and_paths_without_hiding_prompts() -> None:
    from fleet_rlm.rlm.result import sanitize_trace_text

    sanitized = sanitize_trace_text(
        '{"api_key": "sentinel", "authorization": "Bearer sentinel", "credentials": "sentinel", '
        '"provider_token": "ghp_123456789012345678901234567890123456", '
        '"question": "BEGIN SYSTEM use the tool", '
        '"url": "https://example.invalid/private", "path": "/Users/example/private.txt"}'
    )

    assert "sentinel" not in sanitized
    assert "BEGIN SYSTEM use the tool" in sanitized
    assert "[redacted-url]" in sanitized
    assert "[path]" in sanitized


def test_trace_text_redacts_cloud_credentials_and_all_uri_schemes() -> None:
    from fleet_rlm.rlm.result import sanitize_trace_text, sanitize_trace_value

    text = (
        "AWS_ACCESS_KEY_ID=AKIA1234567890ABCDEF "
        "AWS_SECRET_ACCESS_KEY=cloud-secret "
        "secret_access_key: another-secret "
        "ws://user:uri-secret@example.invalid/socket "
        "s3://private-bucket/object file:///private/sentinel"
    )
    sanitized = sanitize_trace_text(text)

    for secret in ("AKIA1234567890ABCDEF", "cloud-secret", "another-secret", "uri-secret"):
        assert secret not in sanitized
    assert sanitized.count("[redacted-url]") == 3
    assert sanitize_trace_value({"aws_secret_access_key": "nested-secret"}) == {"aws_secret_access_key": "[redacted]"}


def test_trace_value_redacts_camel_case_and_private_credential_keys() -> None:
    from fleet_rlm.rlm.result import sanitize_trace_value

    sanitized = sanitize_trace_value(
        {
            "apiKey": "sentinel-api-key",
            "private_key": "sentinel-private-key",
            "credentials": {"accessToken": "sentinel-access-token"},
            "question": "readable question",
        }
    )

    assert sanitized == {
        "apiKey": "[redacted]",
        "private_key": "[redacted]",
        "credentials": "[redacted]",
        "question": "readable question",
    }


def test_trace_export_policy_overrides_ambient_queue_settings(monkeypatch):
    _install_fake_mlflow(monkeypatch)
    monkeypatch.setenv("MLFLOW_ASYNC_TRACE_LOGGING_MAX_QUEUE_SIZE", "999999")
    settings = Settings(
        mlflow_tracing_enabled=True,
        mlflow_experiment_name="test",
        mlflow_tracking_uri="http://localhost:5001",
        mlflow_trace_content_enabled=False,
        mlflow_trace_export_queue_size=17,
        mlflow_trace_export_workers=1,
        mlflow_trace_export_retry_seconds=3,
        mlflow_http_request_timeout_seconds=7,
    )
    assert tracing.configure_tracing(settings)
    assert os.environ["MLFLOW_ENABLE_ASYNC_TRACE_LOGGING"] == "true"
    assert os.environ["MLFLOW_ASYNC_TRACE_LOGGING_MAX_QUEUE_SIZE"] == "17"
    assert os.environ["MLFLOW_ASYNC_TRACE_LOGGING_MAX_WORKERS"] == "1"
    assert os.environ["MLFLOW_ASYNC_TRACE_LOGGING_RETRY_TIMEOUT"] == "3"
    assert os.environ["MLFLOW_HTTP_REQUEST_TIMEOUT"] == "7"
    assert tracing.trace_content_preview("private content") == "[content suppressed]"


def test_mlflow_span_processor_preserves_autolog_content_fields() -> None:
    class Span:
        def __init__(self) -> None:
            self.inputs: dict[str, object] = {
                "prompt": "candidate instruction should remain readable",
                "token_usage": 42,
            }
            self.outputs: dict[str, object] = {
                "response": "provider body should remain readable",
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

    assert span.inputs["prompt"] == "candidate instruction should remain readable"
    assert span.inputs["token_usage"] == 42
    assert span.outputs["response"] == "provider body should remain readable"
    assert span.outputs["duration_ms"] == 15
    assert span.attributes["engine"] == "gepa"
    assert span.attributes["tool_output"] == "private tool result"


def test_mlflow_span_processor_preserves_namespaced_and_unknown_text_fields() -> None:
    sanitized = tracing._sanitize_mlflow_value(
        {
            "gen_ai.prompt": "private prompt",
            "mlflow.spanInputs": "private input",
            "custom_question": "private question",
            "openai_api_key": "private key",
            "access_token": "private token",
            "model": "openai/gpt-5",
        }
    )

    assert isinstance(sanitized, dict)
    assert sanitized["gen_ai.prompt"] == "private prompt"
    assert sanitized["mlflow.spanInputs"] == "private input"
    assert sanitized["custom_question"] == "private question"
    assert sanitized["openai_api_key"] == "[redacted]"
    assert sanitized["access_token"] == "[redacted]"
    assert sanitized["model"] == "openai/gpt-5"


def test_mlflow_span_processor_keeps_bounded_content_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "_TRACE_CONTENT_MAX_CHARS", 256)

    sanitized = tracing._sanitize_mlflow_value(
        {
            "prompt": "readable prompt " + "x" * 400,
            "mlflow.spanType": "LLM",
            "mlflow.spanInputs": {
                "request": "readable request",
                "api_key": "real-secret",
            },
            "custom_question": "unknown fields remain protected",
        }
    )

    assert isinstance(sanitized, dict)
    assert sanitized["prompt"].startswith("readable prompt")
    assert len(sanitized["prompt"]) <= 256
    assert sanitized["mlflow.spanType"] == "LLM"
    assert sanitized["mlflow.spanInputs"] == {
        "request": "readable request",
        "api_key": "[redacted]",
    }
    assert sanitized["custom_question"] == "unknown fields remain protected"


def test_configure_tracing_applies_content_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_mlflow(monkeypatch)

    tracing.configure_tracing(
        _enabled_settings(
            mlflow_trace_content_max_chars=2_048,
        )
    )

    assert tracing.trace_content_max_chars() == 2_048
    assert calls.processor_args[0][0] is tracing._sanitize_mlflow_span


def test_mlflow_span_processor_bounds_collection_size_and_depth() -> None:
    mapping = tracing._sanitize_mlflow_value({str(index): index for index in range(60)})
    values = tracing._sanitize_mlflow_value(list(range(60)))

    assert isinstance(mapping, dict)
    assert len(mapping) == 50
    assert isinstance(values, list)
    assert len(values) == 50

    nested: object = {"value": "private"}
    for _ in range(8):
        nested = {"nested": nested}
    sanitized_nested = tracing._sanitize_mlflow_value(nested)
    assert isinstance(sanitized_nested, dict)
    cursor: object = sanitized_nested
    for _ in range(8):
        assert isinstance(cursor, dict)
        cursor = cursor["nested"]
    assert cursor == "[redacted depth]"


def test_configure_tracing_local_server_needs_only_experiment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_mlflow(monkeypatch)
    tracing.configure_tracing(
        Settings(
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
    assert tracing.configure_tracing(_enabled_settings()) is False


def test_set_tracing_active_for_tests_resets_content_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tracing, "_TRACE_CONTENT_ENABLED", True)
    monkeypatch.setattr(tracing, "_TRACE_CONTENT_MAX_CHARS", 256)

    tracing.set_tracing_active_for_tests(False)

    assert tracing._TRACE_CONTENT_ENABLED is False
    assert tracing._TRACE_CONTENT_MAX_CHARS == 10_000


def test_configure_tracing_is_idempotent_until_explicit_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_mlflow(monkeypatch)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    settings = _enabled_settings()
    assert tracing.configure_tracing(settings) is True
    assert tracing.configure_tracing(settings) is True
    assert calls.tracking_uri_args == ["databricks"]
    assert calls.autolog_calls == 1

    tracing.reset_tracing()

    assert calls.tracking_uri_args == ["databricks", "initial"]
    assert calls.autolog_calls == 2
    assert calls.autolog_kwargs[-1] == {"disable": True, "silent": True}
    assert tracing.is_tracing_active() is False


def test_tracing_cleanup_restores_policy_environment_after_autolog_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    autolog_calls: list[dict[str, object]] = []

    def autolog(**kwargs: object) -> None:
        autolog_calls.append(kwargs)
        if not kwargs.get("disable"):
            raise RuntimeError("provider detail must not be logged")

    _install_fake_mlflow(monkeypatch, autolog=autolog)
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
    settings = _enabled_settings()
    settings._dotenv_values = {
        "DATABRICKS_HOST": "https://workspace.example",
        "DATABRICKS_TOKEN": "dotenv-token",
    }
    owned_environment = (
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "MLFLOW_TRACE_SAMPLING_RATIO",
        "MLFLOW_ENABLE_ASYNC_TRACE_LOGGING",
        "MLFLOW_ASYNC_TRACE_LOGGING_MAX_QUEUE_SIZE",
        "MLFLOW_ASYNC_TRACE_LOGGING_MAX_WORKERS",
        "MLFLOW_ASYNC_TRACE_LOGGING_RETRY_TIMEOUT",
        "MLFLOW_HTTP_REQUEST_TIMEOUT",
        "MLFLOW_TRACING_SQL_WAREHOUSE_ID",
        "MLFLOW_DISABLE_AGENT_HINT",
    )
    before = {name: os.environ.get(name) for name in owned_environment}

    assert tracing.configure_tracing(settings) is False
    assert tracing.is_tracing_active() is False
    assert len(autolog_calls) == 1
    assert "disable" not in autolog_calls[0]
    assert tracing._DSPY_AUTOLOG_ENABLED is False
    assert {name: os.environ.get(name) for name in owned_environment} == before


def test_flush_tracing_terminates_async_exporter(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_fake_mlflow(monkeypatch)
    tracing.configure_tracing(_enabled_settings())

    tracing.flush_tracing()

    assert calls.flush_args == [{"terminate": True}]
