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
    monkeypatch.setattr(tracing, "_TRACING_CONFIGURED", False)


def _install_fake_mlflow(
    monkeypatch: pytest.MonkeyPatch,
    *,
    set_tracking_uri: Any | None = None,
    set_experiment: Any | None = None,
    autolog: Any | None = None,
    raise_on_import: BaseException | None = None,
) -> SimpleNamespace:
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
    )

    def _set_uri(uri: str) -> None:
        calls.tracking_uri_args.append(uri)

    def _set_exp(*args: Any, **kwargs: Any) -> None:
        calls.experiment_args.append(args)
        calls.experiment_kwargs.append(kwargs)

    def _autolog() -> None:
        calls.autolog_calls += 1

    calls.set_tracking_uri = _set_uri if set_tracking_uri is None else set_tracking_uri
    calls.set_experiment = _set_exp if set_experiment is None else set_experiment
    calls.autolog = _autolog if autolog is None else autolog

    mlflow = ModuleType("mlflow")
    mlflow.set_tracking_uri = calls.set_tracking_uri  # type: ignore[attr-defined]
    mlflow.set_experiment = calls.set_experiment  # type: ignore[attr-defined]
    dspy_mod = ModuleType("mlflow.dspy")
    dspy_mod.autolog = calls.autolog  # type: ignore[attr-defined]
    mlflow.dspy = dspy_mod  # type: ignore[attr-defined]
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
