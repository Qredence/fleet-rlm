"""Process-setting contracts for live runtime limits."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from fleet_rlm.config.loader import (
    load_configuration_environment_contract,
    load_runtime_settings,
)
from fleet_rlm.config.settings import FleetConfigurationError, Settings


@pytest.fixture(autouse=True)
def _clear_process_snapshot_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep dotenv-only snapshot tests isolated from credentialed live-module imports.

    Each name below is resolved from the repository ``.env`` under an explicit
    fail-closed rule: when a policy-declared value is also exported by the
    developer's shell with a different value, loading refuses instead of
    guessing. These tests supply their own hermetic temp ``.env``, so the
    ambient copies must be cleared or the fixture, not the policy, is what
    decides the result. Tests that need an override set it after this fixture.
    """
    for name in (
        "FLEET_DAYTONA_SNAPSHOT",
        "FLEET_DAYTONA_CHILD_SNAPSHOT",
        "FLEET_DAYTONA_ORG_ID",
        "DATABASE_URL",
        "FLEET_DATABASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)


def test_configuration_environment_contract_follows_toml_policy() -> None:
    contract = load_configuration_environment_contract()
    assert contract.provider == "OpenAI Chat Completion"
    assert contract.provider_environment_names == (
        "FLEET_DAYTONA_API_KEY",
        "FLEET_DAYTONA_ORG_ID",
        "DATABRICKS_TOKEN",
        "FLEET_LLM_BASE_URL",
    )
    assert contract.database_url_env == "FLEET_DATABASE_URL"
    assert contract.recursion_enabled is True
    assert contract.daytona_snapshot_environment_names == ("FLEET_DAYTONA_SNAPSHOT", "FLEET_DAYTONA_CHILD_SNAPSHOT")


def test_committed_policy_declares_databricks_model_roles() -> None:
    policy_path = Path(__file__).resolve().parents[2] / "config" / "fleet.toml"
    document = tomllib.loads(policy_path.read_text(encoding="utf-8"))

    assert document["config"] == {"schema_version": 2}
    assert "defaults" not in document and "profiles" not in document
    assert document["daytona"]["snapshot_env"] == "FLEET_DAYTONA_SNAPSHOT"
    assert document["daytona"]["child_snapshot_env"] == "FLEET_DAYTONA_CHILD_SNAPSHOT"
    assert document["daytona"]["org_id_env"] == "FLEET_DAYTONA_ORG_ID"
    assert document["runtime"]["environment"] == "daytona"
    assert document["llm"] == {
        "root": {
            "model": "uscentral.ai_gateway.deepseek-v4-1-flash-service",
            "api_key_env": "DATABRICKS_TOKEN",
            "base_url_env": "FLEET_LLM_BASE_URL",
            "max_tokens": 16384,
            "timeout_seconds": 300,
            "num_retries": 3,
            "cache": False,
        },
        "sub": {
            "model": "uscentral.ai_gateway.deepseek-v4-1-flash-service",
            "api_key_env": "DATABRICKS_TOKEN",
            "base_url_env": "FLEET_LLM_BASE_URL",
            "max_tokens": 16384,
            "timeout_seconds": 90,
            "temperature": 0,
            "num_retries": 3,
            "cache": False,
        },
    }
    assert document["runtime"]["live_enabled"] is True


def test_committed_policy_uses_bounded_root_rlm_budget_and_provider_retries() -> None:
    policy_path = Path(__file__).resolve().parents[2] / "config" / "fleet.toml"
    document = tomllib.loads(policy_path.read_text(encoding="utf-8"))

    assert {
        key: document["rlm"][key]
        for key in ("max_iters", "max_llm_calls", "max_output_chars", "max_final_output_chars")
    } == {
        "max_iters": 12,
        "max_llm_calls": 32,
        "max_output_chars": 6_000,
        "max_final_output_chars": 6_000,
    }
    # Preserve DSPy's native retry budget for both roles.
    assert document["llm"]["root"]["num_retries"] == 3
    assert document["llm"]["sub"]["num_retries"] == 3


def test_default_mlflow_policy_uses_bounded_operational_trace_delivery() -> None:
    policy_path = Path(__file__).resolve().parents[2] / "config" / "fleet.toml"
    document = tomllib.loads(policy_path.read_text(encoding="utf-8"))

    assert document["mlflow"] == {
        "tracing_enabled": True,
        "tracking_uri": "http://127.0.0.1:5001",
        "experiment_name": "fleet-rlm",
        "experiment_purpose": "runtime",
        "expose_trace_id": True,
        "async_logging": True,
        "trace_sampling_ratio": 1.0,
        "trace_content_max_chars": 10000,
        "trace_content_enabled": True,
        "trace_export_queue_size": 128,
        "trace_export_workers": 2,
        "trace_export_retry_seconds": 10,
        "http_request_timeout_seconds": 10,
        "trace_shutdown_seconds": 5.0,
    }


def test_single_configuration_ignores_ambient_selectors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.config.loader as config

    monkeypatch.setenv("DATABRICKS_TOKEN", "test-databricks-token")
    monkeypatch.setenv("FLEET_LLM_BASE_URL", "https://gateway.example.test/ai-gateway/mlflow/v1")
    monkeypatch.setenv("FLEET_CONFIG_PROFILE", "daytona-managed")

    settings = config.load_runtime_settings()

    assert settings.root_model == "uscentral.ai_gateway.deepseek-v4-1-flash-service"
    assert settings.sub_model == "uscentral.ai_gateway.deepseek-v4-1-flash-service"
    assert settings.rlm_recursion_enabled is True


def test_daytona_ignores_managed_mlflow_environment_values_when_not_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.config.loader as config

    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "test-daytona-key")
    monkeypatch.setenv("DATABRICKS_TOKEN", "test-databricks-token")
    monkeypatch.setenv("FLEET_LLM_BASE_URL", "https://gateway.example.test/ai-gateway/mlflow/v1")
    monkeypatch.setenv("FLEET_MLFLOW_EXPERIMENT_NAME", "managed-experiment")
    monkeypatch.setenv("FLEET_MLFLOW_TRACE_CATALOG", "managed_catalog")

    settings = config.load_runtime_settings()

    assert settings.mlflow_tracking_uri == "http://127.0.0.1:5001"
    assert settings.mlflow_experiment_name == "fleet-rlm"
    assert settings.mlflow_trace_catalog is None


def test_committed_policy_enables_recursion() -> None:
    document = tomllib.loads(Path("config/fleet.toml").read_text(encoding="utf-8"))
    assert document["rlm"]["recursion_enabled"] is True


@pytest.mark.parametrize(
    "legacy",
    [
        "[config]\nschema_version = 1\n",
        '[config]\nschema_version = 2\ndefault_profile = "daytona"\n',
        "[config]\nschema_version = 2\n[defaults]\n",
        "[config]\nschema_version = 2\n[profiles.daytona]\n",
    ],
)
def test_legacy_configurations_require_manual_migration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, legacy: str
) -> None:
    import fleet_rlm.config.loader as config

    policy = tmp_path / "fleet.toml"
    policy.write_text(legacy, encoding="utf-8")
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)
    with pytest.raises(FleetConfigurationError, match=r"migrate to config\.schema_version = 2"):
        config.load_runtime_settings()


def _policy(path: Path) -> None:
    """
    Write a minimal runtime policy to the specified path for isolated tests.

    Parameters:
        path (Path): Destination file for the temporary TOML policy.
    """
    path.write_text(
        """
[config]
schema_version = 2
[application]
name = "fleet-test"
[runtime]
environment = "daytona"
live_enabled = true
turn_timeout_seconds = 90
max_active_daytona_leases = 2
heartbeat_seconds = 5
stale_after_seconds = 15
[llm.root]
model = "openai/root"
api_key_env = "ROOT_KEY"
cache = true
num_retries = 2
[llm.sub]
model = "openai/sub"
api_key_env = "SUB_KEY"
cache = false
num_retries = 4
temperature = 0.2
[rlm]
max_iters = 3
max_llm_calls = 4
max_provider_attempts = 32
max_tool_calls = 16
max_output_chars = 500
max_final_output_chars = 500
max_execution_output_chars = 250
max_execution_output_bytes = 10000
execution_timeout_s = 90
wrap_up_seconds = 30
finalization_attempts = 2
verbose = true
[storage]
data_root = ".fleet-test"
max_upload_bytes = 10
max_artifact_bytes = 20
[daytona]
volume_name = "fleet-volume"
volume_mount_path = "/fleet"
snapshot_env = "FLEET_DAYTONA_SNAPSHOT"
child_snapshot_env = "FLEET_DAYTONA_CHILD_SNAPSHOT"
org_id_env = "FLEET_DAYTONA_ORG_ID"
[logging]
level = "DEBUG"
        """.strip(),
        encoding="utf-8",
    )
    # Snapshot and organization identities are deliberately dotenv-only;
    # provide deterministic non-secret values for tests that load settings.
    path.with_name(".env").write_text(
        "FLEET_DAYTONA_SNAPSHOT=fleet-test-v1\nFLEET_DAYTONA_CHILD_SNAPSHOT=fleet-child-v1\nFLEET_DAYTONA_ORG_ID=fleet-test-org\n",
        encoding="utf-8",
    )


def test_omitted_role_cache_and_retry_defaults_resolve_to_settings_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import fleet_rlm.config.loader as config

    policy = tmp_path / "fleet.toml"
    _policy(policy)
    policy.write_text(
        policy.read_text(encoding="utf-8").replace("cache = false\n", "", 1).replace("num_retries = 4\n", "", 1),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)

    settings = config.load_runtime_settings()
    assert settings.sub_lm.cache is True
    assert settings.sub_lm.num_retries == 3


def test_require_live_execution_honors_the_toml_switch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import fleet_rlm.config.loader as config

    policy = tmp_path / "fleet.toml"
    _policy(policy)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)

    assert config.require_live_execution().live_enabled is True

    policy.write_text(
        policy.read_text(encoding="utf-8").replace("live_enabled = true", "live_enabled = false"), encoding="utf-8"
    )
    with pytest.raises(FleetConfigurationError, match=r"runtime\.live_enabled=false"):
        config.require_live_execution()


def test_runtime_settings_ignores_stale_environment_policy_overrides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import fleet_rlm.config.loader as config

    policy = tmp_path / "fleet.toml"
    _policy(policy)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)
    monkeypatch.delenv("FLEET_RUN_ENVIRONMENT", raising=False)
    monkeypatch.setenv("FLEET_ROOT_MODEL", "openai/override")
    monkeypatch.setenv("FLEET_RLM_MAX_ITERATIONS", "99")

    settings = config.load_runtime_settings()

    assert settings.root_model == "openai/root"
    assert settings.rlm_max_iters == 3


def test_snapshot_resolution_falls_back_to_process_environment_and_rejects_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.config.loader as config

    monkeypatch.setenv("SNAPSHOT_NAME", "fleet-process-v1")
    assert config._resolve_environment_value("SNAPSHOT_NAME", {}, dotenv_only=True) == "fleet-process-v1"

    monkeypatch.setenv("SNAPSHOT_NAME", "fleet-dotenv-v2")
    with pytest.raises(FleetConfigurationError, match="SNAPSHOT_NAME"):
        config._resolve_environment_value(
            "SNAPSHOT_NAME",
            {"SNAPSHOT_NAME": "fleet-dotenv-v1"},
            dotenv_only=True,
        )


def test_daytona_org_id_resolution_is_dotenv_only_and_rejects_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.config.loader as config

    monkeypatch.setenv("FLEET_DAYTONA_ORG_ID", "fleet-process-org")
    assert config._resolve_environment_value("FLEET_DAYTONA_ORG_ID", {}, dotenv_only=True) == "fleet-process-org"

    with pytest.raises(FleetConfigurationError, match="FLEET_DAYTONA_ORG_ID"):
        config._resolve_environment_value(
            "FLEET_DAYTONA_ORG_ID",
            {"FLEET_DAYTONA_ORG_ID": "fleet-dotenv-org"},
            dotenv_only=True,
        )


def test_runtime_settings_resolves_only_toml_declared_environment_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import fleet_rlm.config.loader as config
    from fleet_rlm.rlm.program import resolve_role_api_key

    policy = tmp_path / "fleet.toml"
    _policy(policy)
    policy.write_text(
        policy.read_text(encoding="utf-8")
        .replace(
            "max_artifact_bytes = 20",
            'max_artifact_bytes = 20\ndatabase_url_env = "DATABASE_URL"',
        )
        .replace(
            'volume_mount_path = "/fleet"',
            'volume_mount_path = "/fleet"\napi_key_env = "DAYTONA_KEY"',
        )
        .replace('api_key_env = "ROOT_KEY"', 'api_key_env = "ROOT_KEY"\nbase_url_env = "AI_GATEWAY_URL"')
        .replace('api_key_env = "SUB_KEY"', 'api_key_env = "SUB_KEY"\nbase_url_env = "AI_GATEWAY_URL"')
        + """
[mlflow]
tracing_enabled = true
tracking_uri = "databricks"
experiment_name_env = "EXPERIMENT_NAME"
trace_catalog_env = "TRACE_CATALOG"
trace_schema_env = "TRACE_SCHEMA"
trace_table_prefix_env = "TRACE_TABLE_PREFIX"
tracing_sql_warehouse_id_env = "TRACE_WAREHOUSE"
""",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(
        "ROOT_KEY=dotenv-root\nDATABASE_URL=sqlite+aiosqlite:///dotenv.sqlite3\nDAYTONA_KEY=dotenv-daytona\nFLEET_DAYTONA_ORG_ID=dotenv-daytona-org\nAI_GATEWAY_URL=https://dotenv.example/ai-gateway/openai/v1\nEXPERIMENT_NAME=/Users/example/fleet\nTRACE_CATALOG=dotenv_catalog\nTRACE_SCHEMA=dotenv_schema\nTRACE_TABLE_PREFIX=dotenv_prefix\nTRACE_WAREHOUSE=dotenv-warehouse\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///process.sqlite3")
    monkeypatch.setenv("DAYTONA_KEY", "process-daytona")
    monkeypatch.setenv("AI_GATEWAY_URL", "https://process.example/ai-gateway/openai/v1")
    monkeypatch.setenv("TRACE_CATALOG", "process_catalog")

    settings = config.load_runtime_settings()

    assert resolve_role_api_key(settings, settings.llm_role("root")) == "dotenv-root"
    assert settings.database_url == "sqlite+aiosqlite:///process.sqlite3"
    assert settings.daytona_api_key is not None
    assert settings.daytona_api_key.get_secret_value() == "process-daytona"
    assert settings.llm_role("root").base_url == "https://process.example/ai-gateway/openai/v1"
    assert settings.llm_role("sub").base_url == "https://process.example/ai-gateway/openai/v1"
    assert settings.mlflow_experiment_name == "/Users/example/fleet"
    assert settings.mlflow_trace_catalog == "process_catalog"
    assert settings.mlflow_trace_schema == "dotenv_schema"
    assert settings.mlflow_trace_table_prefix == "dotenv_prefix"
    assert settings.mlflow_tracing_sql_warehouse_id == "dotenv-warehouse"


def test_redacted_policy_summary_never_includes_secret_values() -> None:
    from fleet_rlm.config.loader import redacted_policy_summary

    summary = redacted_policy_summary(
        Settings(llm_api_key=SecretStr("private-llm-key")),
    )

    assert "environment=daytona" in summary
    assert "private-llm-key" not in summary


def test_startup_rejects_retired_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.app import create_app

    monkeypatch.setenv("FLEET_LIVE_KERNEL", "true")
    with pytest.raises(ValueError, match=r"retired Fleet environment variable.*FLEET_LIVE_KERNEL"):
        create_app(settings=Settings())


def test_startup_rejects_retired_budget_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleet_rlm.app import create_app

    monkeypatch.setenv("FLEET_BUDGET_MAX_ITERATIONS", "6")
    with pytest.raises(ValueError, match=r"retired Fleet environment variable.*FLEET_BUDGET_MAX_ITERATIONS"):
        create_app(settings=Settings())


def test_turn_timeout_defaults_to_thirty_minutes() -> None:
    assert Settings().turn_timeout_seconds == 1800


def test_deadline_reserve_and_role_timeout_defaults_are_public_policy_values() -> None:
    settings = Settings()

    assert settings.rlm_wrap_up_seconds == 300
    assert settings.root_lm.timeout_seconds == 300
    assert settings.sub_lm.timeout_seconds == 90


def test_deadline_reserve_must_leave_time_inside_the_turn() -> None:
    with pytest.raises(ValidationError, match="rlm_wrap_up_seconds"):
        Settings(turn_timeout_seconds=300, rlm_wrap_up_seconds=300)

    valid = Settings(turn_timeout_seconds=301, rlm_wrap_up_seconds=300)
    assert valid.rlm_wrap_up_seconds < valid.turn_timeout_seconds


def test_child_execution_timeout_defaults_to_derive_sentinel() -> None:
    assert Settings().rlm_child_execution_timeout_s == 0


def test_child_execution_timeout_must_not_exceed_the_parent_deadline() -> None:
    with pytest.raises(ValidationError, match="rlm_child_execution_timeout_s"):
        Settings(rlm_execution_timeout_s=300, rlm_child_execution_timeout_s=301)

    valid = Settings(rlm_execution_timeout_s=300, rlm_child_execution_timeout_s=300)
    assert valid.rlm_child_execution_timeout_s == 300


def test_child_execution_timeout_resolves_from_the_committed_toml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    policy = tmp_path / "fleet.toml"
    _policy(policy)
    policy.write_text(
        policy.read_text(encoding="utf-8").replace(
            "execution_timeout_s = 90",
            "execution_timeout_s = 90\nchild_execution_timeout_s = 45",
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("fleet_rlm.config.loader._CONFIG_PATH", policy)

    settings = load_runtime_settings()
    assert settings.rlm_child_execution_timeout_s == 45


def test_live_execution_is_enabled_by_default_and_can_be_disabled() -> None:
    assert Settings().live_enabled is True
    assert Settings(live_enabled=False).live_enabled is False


def test_daytona_admission_defaults_to_eight_leases() -> None:
    assert Settings().max_active_daytona_leases == 8


def test_settings_does_not_read_fleet_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_MAX_ACTIVE_DAYTONA_LEASES", "3")
    assert Settings().max_active_daytona_leases == 8


def test_settings_ignore_mlflow_environment_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_MLFLOW_TRACE_CATALOG", "analytics")
    monkeypatch.setenv("FLEET_MLFLOW_TRACE_SCHEMA", "traces")
    monkeypatch.setenv("FLEET_MLFLOW_TRACE_TABLE_PREFIX", "fleet_app")
    monkeypatch.setenv("FLEET_MLFLOW_TRACING_SQL_WAREHOUSE_ID", "warehouse-123")

    settings = Settings()

    assert settings.mlflow_trace_catalog is None
    assert settings.mlflow_trace_schema is None
    assert settings.mlflow_trace_table_prefix is None
    assert settings.mlflow_tracing_sql_warehouse_id is None


def test_settings_does_not_read_runtime_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FLEET_TURN_TIMEOUT_SECONDS", "1200")
    assert Settings().turn_timeout_seconds == 1800


def test_settings_does_not_scan_unprefixed_environment_or_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("LOG_LEVEL=DEBUG\nDATABASE_URL=postgresql://dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DATABASE_URL", "postgresql://leaked")
    monkeypatch.setenv("DAYTONA_API_KEY", "leaked-key")

    constructed = Settings()
    validated = Settings.model_validate({})

    for settings in (constructed, validated):
        assert settings.log_level == "INFO"
        assert settings.database_url is None
        assert settings.daytona_api_key is None


@pytest.mark.parametrize("value", [0])
def test_turn_timeout_must_be_positive(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(turn_timeout_seconds=value)


def test_autonomous_memory_candidate_category_policy_is_bounded() -> None:
    with pytest.raises(ValidationError, match="rlm_autonomous_memory_categories"):
        Settings(rlm_autonomous_memory_categories=tuple(f"Category {index}" for index in range(17)))


@pytest.mark.parametrize("version", ["2.0", '"2"', "3"])
def test_configuration_rejects_unsupported_schema_versions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, version: str
) -> None:
    import fleet_rlm.config.loader as config

    policy = tmp_path / "fleet.toml"
    policy.write_text(f"[config]\nschema_version = {version}\n", encoding="utf-8")
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)
    with pytest.raises(FleetConfigurationError, match=r"config\.schema_version must be 2"):
        config.load_runtime_settings()
