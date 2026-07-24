"""Process-setting contracts for live runtime limits."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from fleet_rlm.config import Settings


def _policy(path: Path) -> None:
    path.write_text(
        """
[config]
schema_version = 1
[defaults.application]
name = "fleet-test"
[defaults.runtime]
turn_timeout_seconds = 90
max_active_daytona_leases = 2
heartbeat_seconds = 5
stale_after_seconds = 15
[defaults.llm.root]
model = "openai/root"
api_key_env = "ROOT_KEY"
cache = true
num_retries = 2
[defaults.llm.sub]
model = "openai/sub"
api_key_env = "SUB_KEY"
cache = false
num_retries = 4
temperature = 0.2
[defaults.rlm]
max_iterations = 3
max_llm_calls = 4
max_output_chars = 500
verbose = true
[defaults.storage]
data_root = ".fleet-test"
max_upload_bytes = 10
max_artifact_bytes = 20
[defaults.daytona]
volume_name = "fleet-volume"
volume_mount_path = "/fleet"
[defaults.logging]
level = "DEBUG"
[profiles.local-deno.runtime]
environment = "deno"
[profiles.daytona.runtime]
environment = "daytona"
[profiles.daytona.daytona]
snapshot = "fleet-test-v1"
""".strip(),
        encoding="utf-8",
    )


def test_runtime_settings_deep_merge_profile_and_keep_role_policy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import fleet_rlm.config as config

    policy = tmp_path / "fleet.toml"
    _policy(policy)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)
    monkeypatch.setenv("FLEET_CONFIG_PROFILE", "local-deno")
    monkeypatch.delenv("FLEET_RUN_ENVIRONMENT", raising=False)

    settings = config.load_runtime_settings()

    assert settings.run_environment == "deno"
    assert settings.rlm_max_iterations == 3
    assert settings.llm_role("root").model == "openai/root"
    assert settings.llm_role("sub").temperature == 0.2


def test_runtime_settings_environment_overrides_toml_but_rejects_runtime_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import fleet_rlm.config as config

    policy = tmp_path / "fleet.toml"
    _policy(policy)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)
    monkeypatch.setenv("FLEET_CONFIG_PROFILE", "local-deno")
    monkeypatch.delenv("FLEET_RUN_ENVIRONMENT", raising=False)
    monkeypatch.setenv("FLEET_ROOT_MODEL", "openai/override")

    assert config.load_runtime_settings().root_model == "openai/override"

    monkeypatch.setenv("FLEET_RUN_ENVIRONMENT", "daytona")
    with pytest.raises(config.FleetConfigurationError, match="conflicts"):
        config.load_runtime_settings()


def test_runtime_settings_loads_custom_role_keys_from_repository_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import fleet_rlm.config as config
    from fleet_rlm.rlm.lm_factory import resolve_role_api_key

    policy = tmp_path / "fleet.toml"
    _policy(policy)
    (tmp_path / ".env").write_text("FLEET_CONFIG_PROFILE=local-deno\nROOT_KEY=dotenv-secret\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)
    monkeypatch.delenv("FLEET_CONFIG_PROFILE", raising=False)
    monkeypatch.delenv("FLEET_RUN_ENVIRONMENT", raising=False)
    monkeypatch.delenv("ROOT_KEY", raising=False)

    settings = config.load_runtime_settings()

    assert resolve_role_api_key(settings, settings.llm_role("root")) == "dotenv-secret"
    assert "ROOT_KEY" not in os.environ


def test_runtime_settings_reject_unknown_toml_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import fleet_rlm.config as config

    policy = tmp_path / "fleet.toml"
    _policy(policy)
    monkeypatch.chdir(tmp_path)
    policy.write_text(
        policy.read_text(encoding="utf-8").replace("verbose = true", "verbose = true\nunexpected = 1"),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)
    monkeypatch.setenv("FLEET_CONFIG_PROFILE", "local-deno")

    with pytest.raises(config.FleetConfigurationError, match="unknown configuration key"):
        config.load_runtime_settings()


def test_redacted_policy_summary_never_includes_secret_values() -> None:
    from fleet_rlm.config import redacted_policy_summary

    summary = redacted_policy_summary(
        Settings(_env_file=None, llm_api_key=SecretStr("private-llm-key")),
        profile="daytona",
    )

    assert "profile=daytona" in summary
    assert "private-llm-key" not in summary


def test_startup_rejects_retired_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.app import create_app

    monkeypatch.setenv("FLEET_LIVE_KERNEL", "true")
    with pytest.raises(ValueError, match="retired Fleet environment variable.*FLEET_LIVE_KERNEL"):
        create_app(settings=Settings(_env_file=None))


def test_startup_rejects_retired_budget_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.app import create_app

    monkeypatch.setenv("FLEET_BUDGET_MAX_ITERATIONS", "6")
    with pytest.raises(ValueError, match="retired Fleet environment variable.*FLEET_BUDGET_MAX_ITERATIONS"):
        create_app(settings=Settings(_env_file=None))


def test_turn_timeout_defaults_to_thirty_minutes() -> None:
    assert Settings(_env_file=None).turn_timeout_seconds == 1800


def test_mlflow_tracing_defaults_to_disabled() -> None:
    assert Settings(_env_file=None).mlflow_tracing_enabled is False


def test_mlflow_tracing_can_be_disabled_explicitly() -> None:
    assert Settings(_env_file=None, mlflow_tracing_enabled=False).mlflow_tracing_enabled is False


def test_daytona_admission_defaults_to_eight_leases() -> None:
    assert Settings(_env_file=None).max_active_daytona_leases == 8


def test_daytona_admission_reads_fleet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_MAX_ACTIVE_DAYTONA_LEASES", "3")
    assert Settings(_env_file=None).max_active_daytona_leases == 3


def test_mlflow_uc_settings_read_fleet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_MLFLOW_TRACE_CATALOG", "analytics")
    monkeypatch.setenv("FLEET_MLFLOW_TRACE_SCHEMA", "traces")
    monkeypatch.setenv("FLEET_MLFLOW_TRACE_TABLE_PREFIX", "fleet_app")
    monkeypatch.setenv("FLEET_MLFLOW_TRACING_SQL_WAREHOUSE_ID", "warehouse-123")

    settings = Settings(_env_file=None)

    assert settings.mlflow_trace_catalog == "analytics"
    assert settings.mlflow_trace_schema == "traces"
    assert settings.mlflow_trace_table_prefix == "fleet_app"
    assert settings.mlflow_tracing_sql_warehouse_id == "warehouse-123"


@pytest.mark.parametrize("value", [0, -1, 9])
def test_daytona_admission_must_be_between_one_and_eight(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, max_active_daytona_leases=value)


def test_turn_timeout_reads_fleet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_TURN_TIMEOUT_SECONDS", "1200")
    assert Settings(_env_file=None).turn_timeout_seconds == 1200


@pytest.mark.parametrize("value", [0, -1])
def test_turn_timeout_must_be_positive(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, turn_timeout_seconds=value)
