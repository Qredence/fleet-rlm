"""Keep every test away from the developer's .env, live MLflow and Postgres servers, and shell overrides."""

from __future__ import annotations

from pathlib import Path

import dspy
import mlflow
import pytest
from pydantic import AliasChoices

from fleet_rlm.config import RETIRED_MLFLOW_ENV, FleetSettings, get_settings


def _setting_env_names() -> set[str]:
    names = {"FLEET_CONFIG"}
    for name, field in FleetSettings.model_fields.items():
        names.add(name.upper())
        if isinstance(field.validation_alias, str):
            names.add(field.validation_alias)
        elif isinstance(field.validation_alias, AliasChoices):
            names.update(choice for choice in field.validation_alias.choices if isinstance(choice, str))
    return names


@pytest.fixture(scope="session")
def mlflow_test_uri(tmp_path_factory: pytest.TempPathFactory) -> str:
    return f"sqlite:///{tmp_path_factory.mktemp('mlflow') / 'mlflow.db'}"


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch, mlflow_test_uri: str):
    for name in (
        _setting_env_names()
        | RETIRED_MLFLOW_ENV
        | {
            "MLFLOW_EXPERIMENT_ID",
            "DATABRICKS_CONFIG_PROFILE",
            "MLFLOW_CRYPTO_KEK_PASSPHRASE",
            "MLFLOW_CRYPTO_KEK_VERSION",
        }
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(FleetSettings.model_config, "env_file", None)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", mlflow_test_uri)
    monkeypatch.setenv("MLFLOW_DISABLE_AGENT_HINT", "1")
    # Persist real local traces inline: SQLite has one writer, and these tests
    # read traces immediately rather than testing background exporter throughput.
    monkeypatch.setenv("MLFLOW_ENABLE_ASYNC_TRACE_LOGGING", "false")
    monkeypatch.setenv("MLFLOW_USE_BATCH_SPAN_PROCESSOR", "false")
    callbacks = list(dspy.settings.callbacks)
    # A test that forgets its database fake fails fast instead of reaching a real local Postgres.
    monkeypatch.setenv("FLEET_DATABASE_URL", "postgresql://fleet-test@db.invalid:5432/fleet-test")
    monkeypatch.setenv("FLEET_CONFIG", str(Path(__file__).resolve().parents[1] / "config/fleet.toml.example"))
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_TRACING_SQL_WAREHOUSE_ID", "example-warehouse")
    monkeypatch.setenv("FLEET_MLFLOW_DATABRICKS_TRACE_CATALOG", "example_catalog")
    get_settings.cache_clear()
    mlflow.set_tracking_uri(mlflow_test_uri)
    yield
    # Native autologging configures process-wide DSPy callbacks. A tracing test must
    # not make every subsequent unit test persist prompts and spans to SQLite.
    dspy.configure(callbacks=callbacks)
    get_settings.cache_clear()
    mlflow.set_tracking_uri(mlflow_test_uri)
