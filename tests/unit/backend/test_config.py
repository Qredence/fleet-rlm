"""Process-setting contracts for live runtime limits."""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from fleet_rlm.config.loader import _deep_merge, active_profile_contract, load_profile_environment_contracts
from fleet_rlm.config.settings import FleetConfigurationError, Settings


def test_profile_environment_matrix_follows_selected_toml_policy() -> None:
    contracts = {contract.name: contract for contract in load_profile_environment_contracts()}

    assert active_profile_contract().name == "daytona-recursive"
    assert contracts["daytona-recursive"].provider == "OpenAI Chat Completion"
    assert contracts["daytona-recursive"].provider_environment_names == (
        "FLEET_DAYTONA_API_KEY",
        "DATABRICKS_TOKEN",
        "DATABRICKS_HOST",
    )


def test_committed_policy_declares_databricks_model_roles() -> None:
    policy_path = Path(__file__).resolve().parents[3] / "config" / "fleet.toml"
    document = tomllib.loads(policy_path.read_text(encoding="utf-8"))

    assert set(document["profiles"]) == {"daytona-recursive"}
    assert document["defaults"]["daytona"]["snapshot"] == "fleet-rlm-python313-v5"
    assert document["defaults"]["runtime"]["environment"] == "daytona"
    assert document["defaults"]["llm"] == {
        "root": {
            "model": "databricks-deepseek-v4-flash-0731",
            "api_key_env": "DATABRICKS_TOKEN",
        "base_url_env": "DATABRICKS_HOST",
        "max_tokens": 131072,
            "timeout_seconds": 300,
            "cache": False,
        },
        "sub": {
            "model": "databricks-deepseek-v4-flash-0731",
            "api_key_env": "DATABRICKS_TOKEN",
        "base_url_env": "DATABRICKS_HOST",
        "max_tokens": 131072,
            "timeout_seconds": 90,
            "temperature": 0,
            "cache": False,
        },
    }
    assert document["defaults"]["runtime"]["live_enabled"] is True


# Every committed profile routes Root and Sub through the Databricks endpoint.
_DATABRICKS_MODEL = "databricks-deepseek-v4-flash-0731"
_DATABRICKS_ROLE = ("DATABRICKS_TOKEN", "DATABRICKS_HOST")


@pytest.mark.parametrize(
    ("profile", "expected_model", "expected_role"),
    (("daytona-recursive", _DATABRICKS_MODEL, _DATABRICKS_ROLE),),
)
def test_daytona_profiles_use_expected_model_for_both_roles(
    profile: str,
    expected_model: str,
    expected_role: tuple[str, str],
) -> None:
    policy_path = Path(__file__).resolve().parents[3] / "config" / "fleet.toml"
    document = tomllib.loads(policy_path.read_text(encoding="utf-8"))

    llm = _deep_merge(document["defaults"]["llm"], document["profiles"][profile].get("llm", {}))
    expected_key_env, expected_base_env = expected_role
    assert llm["root"]["model"] == expected_model
    assert llm["sub"]["model"] == expected_model
    assert llm["root"]["api_key_env"] == expected_key_env
    assert llm["sub"]["api_key_env"] == expected_key_env
    assert llm["root"]["base_url_env"] == expected_base_env
    assert llm["sub"]["base_url_env"] == expected_base_env


def test_default_profile_routes_tracing_to_supervised_local_mlflow() -> None:
    policy_path = Path(__file__).resolve().parents[3] / "config" / "fleet.toml"
    document = tomllib.loads(policy_path.read_text(encoding="utf-8"))

    mlflow = _deep_merge(document["defaults"]["mlflow"], document["profiles"]["daytona-recursive"].get("mlflow", {}))
    assert mlflow["tracing_enabled"] is True
    assert mlflow["tracking_uri"] == "http://127.0.0.1:5001"
    assert mlflow["experiment_name"] == "fleet-rlm"


def test_default_mlflow_policy_uses_async_full_fidelity_trace_delivery() -> None:
    policy_path = Path(__file__).resolve().parents[3] / "config" / "fleet.toml"
    document = tomllib.loads(policy_path.read_text(encoding="utf-8"))

    assert document["defaults"]["mlflow"] == {
        "tracing_enabled": True,
        "tracking_uri": "http://127.0.0.1:5001",
        "experiment_name": "fleet-rlm",
        "expose_trace_id": True,
        "async_logging": True,
        "trace_sampling_ratio": 1.0,
        "trace_content_max_chars": 10000,
    }


def test_selected_recursive_profile_resolves_root_and_sub_with_databricks_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.config.loader as config

    # Only the selected profile's declared names supply the Root/Sub provider.
    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "test-daytona-key")
    monkeypatch.setenv("DATABRICKS_TOKEN", "test-databricks-token")
    monkeypatch.setenv("DATABRICKS_HOST", "https://gateway.example.test")

    settings = config.load_runtime_settings()

    assert settings.root_model == "databricks-deepseek-v4-flash-0731"
    assert settings.sub_model == "databricks-deepseek-v4-flash-0731"
    # The Modal vLLM endpoint has no reasoning-effort surface; the override
    # stays unset instead of being forwarded with a default.
    assert settings.root_llm_reasoning_effort is None
    assert settings.sub_llm_reasoning_effort is None
    assert settings.sub_llm_temperature == 0
    # Live observation turns disable the LM cache: a hit would provide no fresh
    # action observation and read as a frozen stream.
    assert settings.root_llm_cache is False
    assert settings.sub_llm_cache is False
    # Both roles share the documented GLM-5.3-Flash completion ceiling: Z.AI's
    # chat completion API reference caps the model's output length at 128K
    # (131,072) tokens and recommends at least 1024.
    assert settings.root_llm_max_tokens == 131072
    assert settings.sub_llm_max_tokens == 131072
    assert settings.root_llm_timeout_seconds == 300
    assert settings.sub_llm_timeout_seconds == 90
    assert settings.mlflow_tracing_enabled is True
    assert settings.mlflow_tracking_uri == "http://127.0.0.1:5001"


def test_daytona_ignores_managed_mlflow_environment_values_when_not_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleet_rlm.config.loader as config

    monkeypatch.setenv("FLEET_DAYTONA_API_KEY", "test-daytona-key")
    monkeypatch.setenv("DATABRICKS_TOKEN", "test-databricks-token")
    monkeypatch.setenv("FLEET_DATABRICKS_AI_GATEWAY_BASE_URL", "https://gateway.example.test/v1")
    monkeypatch.setenv("FLEET_MODAL_API_KEY", "test-modal-key")
    monkeypatch.setenv("FLEET_MODAL_BASE_URL", "https://modal.example.test/v1")
    monkeypatch.setenv("FLEET_MLFLOW_EXPERIMENT_NAME", "managed-experiment")
    monkeypatch.setenv("FLEET_MLFLOW_TRACE_CATALOG", "managed_catalog")

    settings = config.load_runtime_settings()

    assert settings.mlflow_tracking_uri == "http://127.0.0.1:5001"
    assert settings.mlflow_experiment_name == "fleet-rlm"
    assert settings.mlflow_trace_catalog is None


def test_recursive_depth_is_a_recursive_execution_invariant_not_a_setting() -> None:
    import dataclasses

    from fleet_rlm.rlm.recursion import recursive_rlm_options

    settings = Settings()
    assert not hasattr(settings, "rlm_recursion_max_depth")
    options = recursive_rlm_options(settings)
    # The composed recursion surface carries no depth setting of any name
    # shape: the native depth stop is a fixed execution invariant, not a
    # policy knob (behavioral depth evidence lives in the recursion lanes).
    assert not hasattr(options, "max_depth")
    assert not any("depth" in field.name for field in dataclasses.fields(options))


def test_stale_recursive_depth_policy_key_fails_validation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import fleet_rlm.config.loader as config

    policy = tmp_path / "fleet.toml"
    _policy(policy)
    policy.write_text(
        policy.read_text(encoding="utf-8").replace(
            "verbose = true",
            "recursion_max_depth = 2\nverbose = true",
            1,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)

    with pytest.raises(FleetConfigurationError, match="recursion_max_depth"):
        config.load_runtime_settings()


def test_committed_policy_enables_recursive_child_execution() -> None:
    policy_path = Path(__file__).resolve().parents[3] / "config" / "fleet.toml"
    document = tomllib.loads(policy_path.read_text(encoding="utf-8"))

    assert document["defaults"]["rlm"]["recursion_enabled"] is True
    # The committed default profile is the [defaults] policy itself: the table
    # stays empty because the schema requires at least one profile.
    assert document["profiles"]["daytona-recursive"] == {}


def _select_profile(tmp_path: Path, *, profile: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write a minimal TOML policy with the requested default_profile."""
    source = Path("config/fleet.toml")
    policy = tmp_path / "fleet.toml"
    content = source.read_text(encoding="utf-8")
    updated = re.sub(
        r'^default_profile = "[^"]*"$',
        f'default_profile = "{profile}"',
        content,
        count=1,
        flags=re.MULTILINE,
    )
    assert updated != content or f'default_profile = "{profile}"' in content
    policy.write_text(updated, encoding="utf-8")
    monkeypatch.setattr("fleet_rlm.config.loader._CONFIG_PATH", policy)
    return policy


def test_removed_databricks_daytona_profile_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import fleet_rlm.config.loader as config

    # unknown default_profile in the committed policy is rejected even when it
    # names a previously existing profile
    _select_profile(tmp_path, profile="databricks-daytona", monkeypatch=monkeypatch)

    with pytest.raises(FleetConfigurationError, match="configured profile does not exist"):
        config.load_runtime_settings()


def _policy(path: Path) -> None:
    path.write_text(
        """
[config]
schema_version = 1
default_profile = "daytona"
[defaults.application]
name = "fleet-test"
[defaults.runtime]
live_enabled = true
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
max_iters = 3
max_llm_calls = 4
max_output_chars = 500
max_execution_output_chars = 250
execution_timeout_s = 90
wrap_up_seconds = 30
verbose = true
[defaults.storage]
data_root = ".fleet-test"
max_upload_bytes = 10
max_url_bytes = 30
max_artifact_bytes = 20
[defaults.daytona]
volume_name = "fleet-volume"
volume_mount_path = "/fleet"
[defaults.logging]
level = "DEBUG"
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
    """Verify that profile settings are deeply merged while preserving separate root and sub-model policies."""
    import fleet_rlm.config.loader as config

    policy = tmp_path / "fleet.toml"
    _policy(policy)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)
    monkeypatch.delenv("FLEET_RUN_ENVIRONMENT", raising=False)

    settings = config.load_runtime_settings()

    assert settings.run_environment == "daytona"
    assert settings.live_enabled is True
    assert settings.rlm_max_iters == 3
    assert settings.max_url_bytes == 30
    assert settings.root_lm.model == "openai/root"
    assert settings.sub_lm.temperature == 0.2
    assert settings.lm_roles.root.model == "openai/root"
    assert settings.lm_roles.sub.api_key_env == "SUB_KEY"


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
        + """
[profiles.daytona.llm.root]
api_key_env = "ROOT_KEY"
base_url_env = "AI_GATEWAY_URL"
[profiles.daytona.llm.sub]
api_key_env = "SUB_KEY"
base_url_env = "AI_GATEWAY_URL"
[profiles.daytona.mlflow]
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
        "ROOT_KEY=dotenv-root\nDATABASE_URL=sqlite+aiosqlite:///dotenv.sqlite3\nDAYTONA_KEY=dotenv-daytona\nAI_GATEWAY_URL=https://dotenv.example/ai-gateway/openai/v1\nEXPERIMENT_NAME=/Users/example/fleet\nTRACE_CATALOG=dotenv_catalog\nTRACE_SCHEMA=dotenv_schema\nTRACE_TABLE_PREFIX=dotenv_prefix\nTRACE_WAREHOUSE=dotenv-warehouse\n",
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


def test_runtime_settings_loads_custom_role_keys_from_repository_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import fleet_rlm.config.loader as config
    from fleet_rlm.rlm.program import resolve_role_api_key

    policy = tmp_path / "fleet.toml"
    _policy(policy)
    (tmp_path / ".env").write_text("ROOT_KEY=dotenv-secret\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)
    monkeypatch.delenv("FLEET_RUN_ENVIRONMENT", raising=False)
    monkeypatch.delenv("ROOT_KEY", raising=False)

    settings = config.load_runtime_settings()

    assert resolve_role_api_key(settings, settings.llm_role("root")) == "dotenv-secret"
    assert "ROOT_KEY" not in os.environ


def test_runtime_settings_reject_unknown_toml_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import fleet_rlm.config.loader as config

    policy = tmp_path / "fleet.toml"
    _policy(policy)
    monkeypatch.chdir(tmp_path)
    policy.write_text(
        policy.read_text(encoding="utf-8").replace("verbose = true", "verbose = true\nunexpected = 1"),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)

    with pytest.raises(FleetConfigurationError, match="unknown configuration key"):
        config.load_runtime_settings()


def test_redacted_policy_summary_never_includes_secret_values() -> None:
    from fleet_rlm.config.loader import redacted_policy_summary

    summary = redacted_policy_summary(
        Settings(llm_api_key=SecretStr("private-llm-key")),
        profile="daytona",
    )

    assert "profile=daytona" in summary
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


def test_live_execution_is_enabled_by_default_and_can_be_disabled() -> None:
    assert Settings().live_enabled is True
    assert Settings(live_enabled=False).live_enabled is False


def test_url_source_limit_defaults_to_ten_mebibytes() -> None:
    assert Settings().max_url_bytes == 10 * 1024 * 1024


def test_mlflow_tracing_field_defaults_to_disabled() -> None:
    # The Settings field default is off; the committed [defaults.mlflow] policy
    # enables it. Test the field default here, policy default in test_config.py.
    assert Settings().mlflow_tracing_enabled is False
    assert Settings().mlflow_trace_content_max_chars == 10_000


def test_mlflow_tracing_can_be_disabled_explicitly() -> None:
    assert Settings(mlflow_tracing_enabled=False).mlflow_tracing_enabled is False


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


@pytest.mark.parametrize("value", [0, -1, 9])
def test_daytona_admission_must_be_between_one_and_eight(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(max_active_daytona_leases=value)


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


@pytest.mark.parametrize("value", [0, -1])
def test_turn_timeout_must_be_positive(value: int) -> None:
    with pytest.raises(ValidationError):
        Settings(turn_timeout_seconds=value)


def test_autonomous_memory_candidate_categories_default_off() -> None:
    policy_path = Path(__file__).resolve().parents[3] / "config" / "fleet.toml"
    document = tomllib.loads(policy_path.read_text(encoding="utf-8"))

    assert Settings().rlm_autonomous_memory_categories == ()
    assert document["defaults"]["rlm"]["autonomous_memory_categories"] == []


def test_autonomous_memory_candidate_categories_resolve_from_toml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import fleet_rlm.config.loader as config

    policy = tmp_path / "fleet.toml"
    _policy(policy)
    policy.write_text(
        policy.read_text(encoding="utf-8").replace(
            "verbose = true",
            "autonomous_memory_categories = [' Project ', 'Project', 'Workflow']".replace("'", '"')
            + chr(10)
            + "verbose = true",
            1,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "_CONFIG_PATH", policy)

    settings = config.load_runtime_settings()

    assert settings.rlm_autonomous_memory_categories == ("Project", "Workflow")


def test_autonomous_memory_candidate_categories_fail_closed() -> None:
    with pytest.raises(ValidationError, match="rlm_autonomous_memory_categories"):
        Settings(rlm_autonomous_memory_categories=("Bad Category!",))


def test_autonomous_memory_candidate_category_policy_is_bounded() -> None:
    with pytest.raises(ValidationError, match="rlm_autonomous_memory_categories"):
        Settings(rlm_autonomous_memory_categories=tuple(f"Category {index}" for index in range(17)))
