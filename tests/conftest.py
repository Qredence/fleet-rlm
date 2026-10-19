"""Keep every test away from the developer's .env, live MLflow and Postgres servers, and shell overrides."""

from __future__ import annotations

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
    for name in _setting_env_names() | RETIRED_MLFLOW_ENV | {"MLFLOW_EXPERIMENT_ID"}:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setitem(FleetSettings.model_config, "env_file", None)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", mlflow_test_uri)
    monkeypatch.setenv("MLFLOW_DISABLE_AGENT_HINT", "1")
    # A test that forgets its database fake fails fast instead of reaching a real local Postgres.
    monkeypatch.setenv("FLEET_DATABASE_URL", "postgresql://fleet-test@db.invalid:5432/fleet-test")
    get_settings.cache_clear()
    mlflow.set_tracking_uri(mlflow_test_uri)
    yield
    get_settings.cache_clear()
    mlflow.set_tracking_uri(mlflow_test_uri)
